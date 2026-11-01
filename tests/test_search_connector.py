from __future__ import annotations

import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from socialrum.search_connector import (
    MAX_CANDIDATES,
    MAX_SEARCH_CANDIDATES,
    _candidate_rows,
    _canonical_post_url,
    _run_json,
    _valid_creator,
    search_and_collect_public_posts,
)
from socialrum.instagram import InstagramAccessBoundaryError, InstagramStopStateCheckError


def _response(payload, returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload))


def _clear_stop_state(target_id="owned-target"):
    return _response({
        "ok": True,
        "classification_ok": True,
        "status": "ok",
        "target": {"id": target_id},
    })


def test_cdp_debug_logs_only_phase_duration_and_outcome(caplog):
    private_values = [
        "sensitive query phrase",
        "https://www.instagram.com/p/private-post-id/",
        "private-target-id",
        "PRIVATE PAGE CONTENT",
        "PRIVATE STDERR",
    ]
    commands = [
        ["cdp", "open", private_values[1], "--target", private_values[2]],
        ["cdp", "wait", "load-state", "domcontentloaded", "--target", private_values[2]],
        ["cdp", "stop-state", "classify", "--target", private_values[2]],
        ["cdp", "eval", "--expression", private_values[0], "--target", private_values[2]],
        ["cdp", "page", "close", "--target", private_values[2]],
    ]

    def successful_runner(_command, **_kwargs):
        return _response({"ok": True, "value": private_values[3]})

    with caplog.at_level(logging.DEBUG, logger="socialrum.search_connector"):
        for command in commands:
            _run_json(command, successful_runner)

        def timeout_runner(command, **_kwargs):
            raise subprocess.TimeoutExpired(
                command, 20, output=private_values[3], stderr=private_values[4],
            )

        with pytest.raises(RuntimeError, match="timed out"):
            _run_json(commands[2], timeout_runner)

        def search_runner(command, **_kwargs):
            if "open" in command:
                return _response({"ok": True, "page": {"id": private_values[2]}})
            if "stop-state" in command:
                return _clear_stop_state(private_values[2])
            if "eval" in command:
                return _response({"ok": True, "result": {"value": []}})
            if "close" in command:
                return _response({"ok": True, "target_gone": True})
            return _response({"ok": True})

        search_and_collect_public_posts(private_values[0], runner=search_runner)

    assert "phase=run outcome=requested" in caplog.text
    assert "phase=run outcome=started" in caplog.text
    assert "phase=open" in caplog.text
    assert "phase=wait" in caplog.text
    assert "phase=stop-state" in caplog.text
    assert "phase=eval" in caplog.text
    assert "phase=close" in caplog.text
    assert "outcome=success" in caplog.text
    assert "outcome=timeout" in caplog.text
    assert "elapsed_ms=" in caplog.text
    for phase in ("open", "wait", "stop-state", "eval", "close"):
        assert f"phase={phase} outcome=started" in caplog.text
    for private_value in private_values:
        assert private_value not in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.instagram.com/p/Abc123/?igsh=ignored", "https://www.instagram.com/p/Abc123/"),
        ("https://instagram.com/reel/Xyz/", "https://www.instagram.com/reel/Xyz/"),
        ("https://www.google.com/url?q=https%3A%2F%2Fwww.instagram.com%2Fp%2FAbc%2F", "https://www.instagram.com/p/Abc/"),
        ("http://instagram.com/p/Abc/", None),
        ("https://instagram.com/accounts/login/", None),
        ("https://evilinstagram.com/p/Abc/", None),
        ("https://instagram.com:444/p/Abc/", None),
        ("javascript:alert(1)", None),
    ],
)
def test_canonical_post_url_is_strict(value, expected):
    assert _canonical_post_url(value) == expected


def test_candidates_are_deduplicated_and_bounded():
    rows = [
        {"href": f"https://www.instagram.com/p/{i}/", "result_text": "  visible  snippet\n"}
        for i in range(MAX_CANDIDATES + 2)
    ]
    rows.insert(1, rows[0])
    result = _candidate_rows({"result": {"value": rows}}, MAX_CANDIDATES)
    assert len(result) == MAX_CANDIDATES
    assert result[0] == {
        "source_url": "https://www.instagram.com/p/0/",
        "search_result_text": "visible snippet",
    }


def test_search_collects_candidates_and_always_closes_owned_tab():
    commands = []
    candidate_urls = ["https://www.instagram.com/p/one/", "https://www.instagram.com/reel/two/"]

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": candidate_urls[0], "result_text": "snippet one"},
                {"href": candidate_urls[1], "result_text": "snippet two"},
                {"href": "https://instagram.com/accounts/login/", "result_text": "not a post"},
            ]}})
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        if "stop-state" in command:
            return _clear_stop_state()
        return _response({"ok": True})

    def collector(url, *, runner):
        if url.endswith("one/"):
            return [{"source_url": url, "text": "caption one", "posted_at": "2026-10-10"}]
        raise RuntimeError("No visible post caption was available")

    result = search_and_collect_public_posts("coffee", runner=runner, collector=collector)

    assert [item["source_url"] for item in result["candidates"]] == candidate_urls
    assert result["candidates"][0]["post_text"] == "caption one"
    assert result["candidates"][0]["posted_at"] == "2026-10-10"
    assert "creator" not in result["candidates"][0]
    assert result["candidates"][1]["status"] == "unavailable"
    assert len(result["errors"]) == 1
    close_command = next(cmd for cmd in commands if "page" in cmd and "close" in cmd)
    assert close_command[close_command.index("--target") + 1] == "owned-target"
    search_url = next(cmd[cmd.index("open") + 1] for cmd in commands if "open" in cmd and "--new-tab=false" not in cmd)
    assert urlsplit(search_url).hostname == "www.google.com"
    query = parse_qs(urlsplit(search_url).query)["q"][0]
    assert '"coffee"' in query


def test_propagates_valid_collector_creator():
    candidate_url = "https://www.instagram.com/p/one/"

    def runner(command, **_kwargs):
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": candidate_url, "result_text": "visible snippet"},
            ]}})
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        if "stop-state" in command:
            return _clear_stop_state()
        return _response({"ok": True})

    def collector(url, *, runner):
        return [{"source_url": url, "text": "visible caption", "creator": "fin_creator.1"}]

    result = search_and_collect_public_posts("finance", runner=runner, collector=collector)

    assert result["candidates"][0]["creator"] == "fin_creator.1"


@pytest.mark.parametrize("collected", [
    [],
    [{"source_url": "https://www.instagram.com/p/one/", "text": "  \n"}],
    [
        {"source_url": "https://www.instagram.com/p/one/", "text": "first caption"},
        {"source_url": "https://www.instagram.com/p/one/", "text": "second caption"},
    ],
    [{"source_url": "https://www.instagram.com/p/one/", "text": None}],
])
def test_empty_or_ambiguous_collector_output_is_not_marked_read(collected):
    candidate_url = "https://www.instagram.com/p/one/"

    def runner(command, **_kwargs):
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": candidate_url, "result_text": "visible snippet"},
            ]}})
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        if "stop-state" in command:
            return _clear_stop_state()
        return _response({"ok": True})

    result = search_and_collect_public_posts(
        "finance", runner=runner, collector=lambda _url, **_kwargs: collected,
    )

    candidate = result["candidates"][0]
    assert candidate["status"] == "unavailable"
    assert "exactly one nonempty visible post text" in candidate["error"]
    assert "post_text" not in candidate


@pytest.mark.parametrize("value", [None, 123, "@creator", "has space", "x" * 31, "accounts"])
def test_omits_absent_or_malformed_collector_creator(value):
    assert _valid_creator(value) is None


def test_closes_owned_tab_when_result_probe_fails():
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "wait" in command:
            return _response({"ok": True})
        if "eval" in command:
            return _response({"ok": False})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    with pytest.raises(RuntimeError, match="successful result"):
        search_and_collect_public_posts("coffee", runner=runner)
    assert any("close" in cmd and "owned-target" in cmd for cmd in commands)


def test_stops_on_search_access_boundary_and_closes_owned_tab():
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "stop-state" in command:
            return _response({"ok": True, "classification_ok": True, "status": "blocked",
                              "agent_should_stop": True, "stop_state": "unusual_traffic",
                              "target": {"id": "owned-target"}})
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    with pytest.raises(InstagramAccessBoundaryError) as exc:
        search_and_collect_public_posts("coffee", runner=runner)
    assert exc.value.stop_state == "unusual_traffic"
    assert not any("eval" in cmd for cmd in commands)
    assert any("close" in cmd and "owned-target" in cmd for cmd in commands)


@pytest.mark.parametrize("classification", [
    {"classification_ok": False, "status": "ok", "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "unknown", "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "human_required": True,
     "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "stop_state": "custom_boundary",
     "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "target": {"id": "other-target"}},
    {"classification_ok": True, "status": "ok"},
    {"classification_ok": True, "status": "ok", "agent_should_stop": "true",
     "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "human_required": 1,
     "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "stop_state": None,
     "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "stop_state": ["login_required"],
     "target": {"id": "owned-target"}},
    {"classification_ok": True, "status": "ok", "stop_state": {"reason": "login_required"},
     "target": {"id": "owned-target"}},
])
def test_unclear_or_blocked_classifier_state_fails_closed_before_probe(classification):
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "stop-state" in command:
            return _response({"ok": True, **classification})
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    with pytest.raises(InstagramStopStateCheckError):
        search_and_collect_public_posts("coffee", runner=runner)

    assert not any("eval" in cmd for cmd in commands)
    assert any("close" in cmd and "owned-target" in cmd for cmd in commands)


def test_stops_after_candidate_challenge_without_collecting_or_opening_more_posts():
    commands = []
    collector_calls = []
    candidate_urls = [
        "https://www.instagram.com/p/one/",
        "https://www.instagram.com/p/two/",
    ]
    instagram_navigation_count = 0

    def runner(command, **_kwargs):
        nonlocal instagram_navigation_count
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "open" in command and "--new-tab=false" in command:
            instagram_navigation_count += 1
            return _response({"ok": True})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": url, "result_text": "visible result"} for url in candidate_urls
            ]}})
        if "stop-state" in command:
            if instagram_navigation_count:
                return _response({"ok": True, "classification_ok": True, "status": "blocked",
                                  "agent_should_stop": True, "stop_state": "login_required",
                                  "target": {"id": "owned-target"}})
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    def collector(url, *, runner):
        collector_calls.append(url)
        return [{"source_url": url, "text": "caption"}]

    with pytest.raises(InstagramAccessBoundaryError) as exc:
        search_and_collect_public_posts("coffee", runner=runner, collector=collector)
    assert exc.value.stop_state == "login_required"

    navigations = [cmd for cmd in commands if "open" in cmd and "--new-tab=false" in cmd]
    assert len(navigations) == 1
    navigation_index = commands.index(navigations[0])
    readiness = commands[navigation_index + 1]
    assert "wait" in readiness
    assert readiness[readiness.index("load-state") + 1] == "domcontentloaded"
    assert readiness[readiness.index("--target") + 1] == "owned-target"
    stop_check_index = next(
        index for index, command in enumerate(commands)
        if index > navigation_index and "stop-state" in command
    )
    assert commands[navigation_index + 1] == readiness
    assert navigation_index < navigation_index + 1 < stop_check_index
    assert collector_calls == []
    assert any("close" in cmd and "owned-target" in cmd for cmd in commands)


def test_preserves_prior_success_and_marks_remaining_candidates_on_later_challenge():
    commands = []
    candidate_urls = [
        "https://www.instagram.com/p/one/",
        "https://www.instagram.com/p/two/",
        "https://www.instagram.com/p/three/",
    ]
    instagram_navigation_count = 0
    stop_checks_after_navigation = 0

    def runner(command, **_kwargs):
        nonlocal instagram_navigation_count, stop_checks_after_navigation
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "open" in command and "--new-tab=false" in command:
            instagram_navigation_count += 1
            return _response({"ok": True})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": url, "result_text": "visible result"} for url in candidate_urls
            ]}})
        if "stop-state" in command:
            if instagram_navigation_count:
                stop_checks_after_navigation += 1
                if stop_checks_after_navigation == 2:
                    return _response({"ok": True, "classification_ok": True, "status": "blocked",
                                      "agent_should_stop": True, "stop_state": "login_required",
                                      "target": {"id": "owned-target"}, "evidence": "RAW PAGE TEXT"})
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    collected = []

    def collector(url, *, runner):
        collected.append(url)
        return [{"source_url": url, "text": "caption from first post"}]

    result = search_and_collect_public_posts("coffee", runner=runner, collector=collector)

    assert collected == [candidate_urls[0]]
    assert result["candidates"][0]["status"] == "read"
    assert result["candidates"][0]["post_text"] == "caption from first post"
    assert result["candidates"][1]["status"] == "unavailable"
    assert result["candidates"][1]["error"] == "Stopped at access boundary (login_required)"
    assert result["candidates"][2]["status"] == "not_attempted"
    assert result["stop_reason"] == "login_required"
    assert any("remaining candidates were not attempted" in error for error in result["errors"])
    assert "RAW PAGE TEXT" not in repr(result)
    navigations = [cmd for cmd in commands if "open" in cmd and "--new-tab=false" in cmd]
    assert len(navigations) == 2
    assert any("close" in cmd and "owned-target" in cmd for cmd in commands)


def test_collector_access_boundary_race_preserves_success_and_stops_navigation():
    commands = []
    candidate_urls = [
        "https://www.instagram.com/p/one/",
        "https://www.instagram.com/p/two/",
        "https://www.instagram.com/p/three/",
    ]

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": url, "result_text": "visible result"} for url in candidate_urls
            ]}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    def collector(url, *, runner):
        if url == candidate_urls[0]:
            return [{"source_url": url, "text": "safe first caption"}]
        if url == candidate_urls[1]:
            raise InstagramAccessBoundaryError("login_required")
        pytest.fail("collector must not be called for candidates after the access boundary")

    result = search_and_collect_public_posts("coffee", runner=runner, collector=collector)

    assert result["candidates"][0]["post_text"] == "safe first caption"
    assert result["candidates"][1]["status"] == "unavailable"
    assert result["candidates"][2]["status"] == "not_attempted"
    assert result["stop_reason"] == "login_required"
    navigations = [cmd for cmd in commands if "open" in cmd and "--new-tab=false" in cmd]
    assert len(navigations) == 2


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "runner-error"])
def test_candidate_navigation_failure_stops_and_preserves_prior_success(failure):
    commands = []
    candidate_urls = [
        "https://www.instagram.com/p/one/",
        "https://www.instagram.com/p/two/",
        "https://www.instagram.com/p/three/",
    ]
    navigation_count = 0

    def runner(command, **_kwargs):
        nonlocal navigation_count
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "open" in command and "--new-tab=false" in command:
            navigation_count += 1
            if navigation_count == 2:
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 20, stderr="PRIVATE PAGE DATA")
                if failure == "nonzero":
                    return _response({"ok": False}, returncode=1)
                raise RuntimeError("PRIVATE PAGE DATA from unstable browser")
            return _response({"ok": True})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": url, "result_text": "visible result"} for url in candidate_urls
            ]}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    def collector(url, *, runner):
        assert url == candidate_urls[0]
        return [{"source_url": url, "text": "caption from first post"}]

    result = search_and_collect_public_posts("coffee", runner=runner, collector=collector)

    assert result["candidates"][0]["post_text"] == "caption from first post"
    assert result["candidates"][1]["status"] == "unavailable"
    assert result["candidates"][2]["status"] == "not_attempted"
    if failure == "timeout":
        assert result["stop_reason"] == "candidate_navigation_timeout"
        assert "timed out" in result["candidates"][1]["error"]
    else:
        assert result["stop_reason"] == "candidate_navigation_failed"
        assert "failed" in result["candidates"][1]["error"]
    assert "PRIVATE PAGE DATA" not in repr(result)
    navigations = [cmd for cmd in commands if "open" in cmd and "--new-tab=false" in cmd]
    assert len(navigations) == 2


def test_first_candidate_navigation_failure_returns_sanitized_stopped_result():
    candidate_url = "https://www.instagram.com/p/one/"
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "open" in command and "--new-tab=false" in command:
            raise RuntimeError("PRIVATE PAGE DATA")
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": candidate_url, "result_text": "visible result"},
                {"href": "https://www.instagram.com/p/two/", "result_text": "another result"},
            ]}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    result = search_and_collect_public_posts("coffee", runner=runner)

    assert result["stop_reason"] == "candidate_navigation_failed"
    assert result["candidates"][0]["status"] == "unavailable"
    assert result["candidates"][1]["status"] == "not_attempted"
    assert "PRIVATE PAGE DATA" not in repr(result)
    navigations = [cmd for cmd in commands if "open" in cmd and "--new-tab=false" in cmd]
    assert len(navigations) == 1


def test_candidate_readiness_failure_stops_before_stop_check_or_next_navigation():
    commands = []
    candidate_urls = [
        "https://www.instagram.com/p/one/",
        "https://www.instagram.com/p/two/",
    ]
    navigation_count = 0

    def runner(command, **_kwargs):
        nonlocal navigation_count
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "open" in command and "--new-tab=false" in command:
            navigation_count += 1
            return _response({"ok": True})
        if "wait" in command and "load-state" in command and navigation_count == 2:
            raise subprocess.TimeoutExpired(command, 20, stderr="PRIVATE PAGE DATA")
        if "eval" in command:
            return _response({"ok": True, "result": {"value": [
                {"href": url, "result_text": "visible result"} for url in candidate_urls
            ]}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    def collector(url, *, runner):
        assert url == candidate_urls[0]
        return [{"source_url": url, "text": "first caption"}]

    result = search_and_collect_public_posts("coffee", runner=runner, collector=collector)

    assert result["candidates"][0]["status"] == "read"
    assert result["candidates"][1]["status"] == "unavailable"
    assert result["candidates"][2:] == []
    assert result["stop_reason"] == "candidate_navigation_timeout"
    assert "timed out" in result["candidates"][1]["error"]
    assert "PRIVATE PAGE DATA" not in repr(result)
    navigations = [cmd for cmd in commands if "open" in cmd and "--new-tab=false" in cmd]
    assert len(navigations) == 2
    second_nav_index = commands.index(navigations[1])
    assert "wait" in commands[second_nav_index + 1]
    assert not any("stop-state" in command for command in commands[second_nav_index + 2:])
    assert any("close" in cmd and "owned-target" in cmd for cmd in commands)


def test_concurrent_connector_call_is_rejected_without_queueing():
    first_open_started = threading.Event()
    release_first = threading.Event()
    first_commands = []
    second_commands = []

    def first_runner(command, **_kwargs):
        first_commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            first_open_started.set()
            if not release_first.wait(timeout=5):
                raise AssertionError("test did not release the first search")
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": []}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(search_and_collect_public_posts, "coffee", runner=first_runner)
        assert first_open_started.wait(timeout=5)
        try:
            with pytest.raises(RuntimeError, match="Another public search is already running"):
                search_and_collect_public_posts(
                    "finance", runner=lambda command, **_kwargs: second_commands.append(command),
                )
            assert second_commands == []
        finally:
            release_first.set()
        result = first.result(timeout=5)

    assert result["candidates"] == []
    assert any("close" in command for command in first_commands)


def test_connector_lock_is_released_after_run_failure():
    commands = []

    def failing_runner(command, **_kwargs):
        commands.append(command)
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "stop-state" in command:
            raise RuntimeError("simulated classifier transport failure")
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    with pytest.raises(InstagramStopStateCheckError):
        search_and_collect_public_posts("coffee", runner=failing_runner)

    def succeeding_runner(command, **_kwargs):
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "eval" in command:
            return _response({"ok": True, "result": {"value": []}})
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    result = search_and_collect_public_posts("finance", runner=succeeding_runner)
    assert result["candidates"] == []
    assert any("close" in command for command in commands)


@pytest.mark.parametrize("limit", [0, MAX_SEARCH_CANDIDATES + 1, True])
def test_candidate_limit_is_strict(limit):
    with pytest.raises(ValueError, match="max_candidates"):
        search_and_collect_public_posts("coffee", max_candidates=limit)


@pytest.mark.parametrize("days", [0, 366, True, "30"])
def test_recent_days_filter_is_strict(days):
    with pytest.raises(ValueError, match="recent_days"):
        search_and_collect_public_posts("coffee", recent_days=days)


def test_twenty_candidate_cap_and_reels_only_filter_and_query():
    commands = []
    rows = [
        {"href": f"https://www.instagram.com/{'reel' if i % 2 else 'p'}/{i}/", "result_text": "visible"}
        for i in range(22)
    ]

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command and "--new-tab=false" not in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": rows}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    result = search_and_collect_public_posts(
        "#coffee", runner=runner, collector=lambda *_a, **_k: [],
        max_candidates=MAX_SEARCH_CANDIDATES, reels_only=True, recent_days=30,
    )
    assert len(result["candidates"]) == 11
    assert all("/reel/" in item["source_url"] for item in result["candidates"])
    search_url = next(cmd[cmd.index("open") + 1] for cmd in commands if "open" in cmd and "--new-tab=false" not in cmd)
    query = parse_qs(urlsplit(search_url).query)["q"][0]
    assert "site:instagram.com/reel/" in query
    assert "site:instagram.com/p/" not in query
    params = parse_qs(urlsplit(search_url).query)
    assert params["tbs"] == ["qdr:d30"]
    assert params["num"] == ["20"]
    assert result["index_time_filter"] == "qdr:d30"


def test_default_connector_search_has_no_time_filter_or_google_result_count_override():
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        if "open" in command:
            return _response({"ok": True, "page": {"id": "owned-target"}})
        if "eval" in command:
            return _response({"ok": True, "result": {"value": []}})
        if "stop-state" in command:
            return _clear_stop_state()
        if "close" in command:
            return _response({"ok": True, "target_gone": True})
        return _response({"ok": True})

    result = search_and_collect_public_posts("coffee", runner=runner)
    url = next(cmd[cmd.index("open") + 1] for cmd in commands if "open" in cmd)
    params = parse_qs(urlsplit(url).query)
    assert "tbs" not in params
    assert "num" not in params
    assert result["index_time_filter"] is None
