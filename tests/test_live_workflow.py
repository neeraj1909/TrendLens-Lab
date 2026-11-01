import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app
from app import (
    INSTAGRAM_KEYWORD_PROMPT,
    PUBLIC_SEARCH_GENERATION_KEY,
    WELCOME_MESSAGE,
    _analyze_instagram_text,
    _capture_latest_search,
    _instagram_records,
    _latest_search_matches_terms,
    _manual_instagram_records,
    on_prompt_instagram_extraction,
    _render_instagram_extraction,
    _render_instagram_search,
    _render_snapshot_comparison,
    _snapshot_candidate_metadata,
    _snapshot_topic_signal_metadata,
    _render_keyword_insights,
)
from socialrum.discovery import build_instagram_search, normalize_instagram_phrase


def test_instagram_records_keep_source_and_do_not_invent_dates_or_metrics():
    records = _instagram_records([
        {"source_url": "https://www.instagram.com/p/example/", "text": "A finance tip about saving."},
    ])

    assert records == [{
        "creator": None,
        "content": "A finance tip about saving.",
        "platform": "Instagram",
        "post_url": "https://www.instagram.com/p/example/",
        "posted_at": None,
    }]


def test_verified_creator_flows_from_individual_visible_record_to_match():
    records = _instagram_records([{
        "source_url": "https://www.instagram.com/p/example/",
        "text": "A finance tip about saving.",
        "creator": "financialtimes",
    }])

    assert records[0]["creator"] == "financialtimes"
    assert _analyze_instagram_text(records, "finance")["keywords"][0]["matches"][0]["creator"] == "financialtimes"


@pytest.mark.parametrize("creator", ["about", "bad/handle", "@financialtimes", "x" * 31, 123])
def test_invalid_or_ambiguous_creator_metadata_is_not_propagated(creator):
    records = _instagram_records([{
        "source_url": "https://www.instagram.com/p/example/",
        "text": "A finance tip about saving.",
        "creator": creator,
    }])

    assert records[0]["creator"] is None


def test_manual_paste_does_not_infer_creator_from_post_url():
    records = _manual_instagram_records(
        "https://www.instagram.com/financialtimes/p/example/ | A finance tip about saving."
    )

    assert records[0]["creator"] == "creator not identified"


def test_observed_post_date_flows_to_match_report_without_inference():
    records = _instagram_records([{
        "source_url": "https://www.instagram.com/p/example/",
        "text": "A finance tip about saving.",
        "posted_at": "2026-10-30",
    }])
    result = _analyze_instagram_text(records, "finance")
    rendered = _render_keyword_insights(
        result,
        datetime(2026, 11, 1, 1, 2, tzinfo=timezone.utc),
        source_description="Visible text from a supplied page.",
    )

    assert records[0]["posted_at"] == "2026-10-30"
    assert r"Date: 2026\-10\-30" in rendered
    assert r"2026\-10\-29" not in rendered


def test_manual_instagram_records_parse_only_user_pasted_https_sources():
    records = _manual_instagram_records(
        "https://www.instagram.com/p/example/ | A finance tip about saving."
    )

    assert records[0]["post_url"] == "https://www.instagram.com/p/example/"
    assert records[0]["content"] == "A finance tip about saving."
    assert records[0]["posted_at"] is None


@pytest.mark.parametrize("line", [
    "https://example.com/p/example/ | some text",
    "http://instagram.com/p/example/ | some text",
    "https://user:pass@instagram.com/p/example/ | some text",
    "https://instagram.com:444/p/example/ | some text",
])
def test_manual_instagram_records_reject_non_instagram_or_unsafe_urls(line):
    with pytest.raises(ValueError):
        _manual_instagram_records(line)


def test_keyword_report_is_readable_and_bounded_as_snapshot():
    observed_at = datetime(2026, 11, 1, 1, 2, tzinfo=timezone.utc)
    result = _analyze_instagram_text(_manual_instagram_records(
        "https://www.instagram.com/p/example/ | A finance tip about saving."
    ), "finance")
    rendered = _render_keyword_insights(
        result, observed_at,
        source_description="This reflects only excerpts you pasted.",
    )

    assert "Snapshot report generated at: 2026-11-01 01:02 UTC" in rendered
    assert "not a platform-wide search, trend, or popularity ranking" in rendered
    assert "creator not identified" in rendered
    assert "Date: not provided" in rendered
    assert "[Instagram source](<https://www.instagram.com/p/example/>)" in rendered
    assert "finance" in rendered and "1 matching observation" in rendered


def test_keyword_only_matches_text_and_keeps_url_as_source():
    records = _manual_instagram_records(
        "https://www.instagram.com/p/finance-tips/ | A useful budget idea."
    )

    result = _analyze_instagram_text(records, "finance")

    assert result["keywords"][0]["match_count"] == 0
    assert result["keywords"][0]["matches"] == []


@pytest.mark.parametrize("phrase", ["#finance", "personal finance"])
def test_instagram_search_link_is_clickable_but_not_presented_as_fetched_data(phrase):
    rendered = _render_instagram_search(build_instagram_search(phrase))

    assert "[Open Google search results](<https://www.google.com/search?q=" in rendered
    assert "The app has not run this search, read results, or verified any post." in rendered
    assert "index may be incomplete or stale" in rendered
    assert "Review results yourself and choose a public post or reel" in rendered
    assert "not an Instagram feed, trend report, or popularity ranking" in rendered


def test_extract_sends_bounded_progress_notice_before_search_starts(monkeypatch):
    messages = []

    class Session:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def fake_search(keywords):
        assert keywords == "#finance"
        assert len(messages) == 1
        assert "up to five public post/reel pages" in messages[0]
        assert "may take a few minutes" in messages[0]
        assert "when the search finishes" in messages[0]
        return {
            "query": "finance query", "search_engine": "Google",
            "searched_at": "2026-11-01T00:00:00+00:00", "candidates": [],
            "errors": [], "provenance_note": "Sample only.",
        }

    monkeypatch.setattr(app.cl, "user_session", Session())
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", fake_search)

    asyncio.run(app._send_instagram_extraction("#finance"))

    assert "Searching visible Google results" in messages[0]
    assert "Instagram post candidates" in messages[1]


def test_public_search_runs_when_default_executor_is_saturated(monkeypatch):
    default_pool = ThreadPoolExecutor(max_workers=1)
    default_started = threading.Event()
    release_default = threading.Event()
    search_called = threading.Event()
    messages = []

    class Session:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def occupy_default_executor():
        default_started.set()
        release_default.wait(timeout=5)

    def fake_search(_keywords):
        search_called.set()
        return {
            "query": "finance query", "search_engine": "Google",
            "searched_at": "2026-11-01T00:00:00+00:00", "candidates": [],
            "errors": [], "provenance_note": "Sample only.",
        }

    monkeypatch.setattr(app.cl, "user_session", Session())
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", fake_search)

    async def run_with_saturated_default():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(default_pool)
        blocker = loop.run_in_executor(None, occupy_default_executor)
        try:
            for _ in range(500):
                if default_started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert default_started.is_set()
            await asyncio.wait_for(app._send_instagram_extraction("finance"), timeout=2)
            assert search_called.is_set()
        finally:
            release_default.set()
            await blocker

    asyncio.run(run_with_saturated_default())
    assert any("Instagram post candidates" in message for message in messages)


def test_extraction_keeps_google_snippets_separate_from_collected_post_text():
    result = {
        "query": '(site:instagram.com/p/ OR site:instagram.com/reel/) "#finance"',
        "searched_at": "2026-11-01T00:00:00+00:00",
        "candidates": [{
            "source_url": "https://www.instagram.com/p/example/",
            "status": "read",
            "search_result_text": "#finance appeared in this Google snippet.",
            "post_text": "A caption with only retirement tips.",
        }],
        "errors": [],
        "provenance_note": "Visible-page sample only.",
    }

    rendered = _render_instagram_extraction(result, "#finance")

    assert "Google search snippet (unverified)" in rendered
    assert "Collected visible post/caption text (first 1,000 characters)" in rendered
    assert "A caption with only retirement tips\\." in rendered
    assert "No match for \\#finance in the visible post text." in rendered
    assert "Instagram post/reel" in rendered
    assert "not a live Instagram search, popularity ranking, or trend report" in rendered
    assert r"Visible-text matches for \#finance:** 0 of 1 readable posts" in rendered
    assert "Readable post pages:** 1 of 1 candidates" in rendered
    assert "Observed dates:** 0 of 1 readable posts" in rendered


def test_extraction_evidence_summary_uses_visible_text_and_observed_date_denominators():
    rendered = _render_instagram_extraction({
        "query": "finance query",
        "searched_at": "2026-11-01T00:00:00+00:00",
        "candidates": [
            {
                "source_url": "https://www.instagram.com/p/finance-url-only/",
                "status": "read",
                "search_result_text": "finance in Google snippet only",
                "post_text": "A simple budgeting habit.",
                "posted_at": "2026-10-30",
            },
            {
                "source_url": "https://www.instagram.com/p/another/",
                "status": "read",
                "search_result_text": "unrelated snippet",
                "post_text": "Finance can be easier with a written plan.",
            },
            {
                "source_url": "https://www.instagram.com/p/unavailable/",
                "status": "unavailable",
                "search_result_text": "finance visible in snippet",
                "post_text": "finance but not readable",
                "posted_at": "2026-10-29",
            },
        ],
    }, "finance")

    summary = rendered.split("### Candidate 1", maxsplit=1)[0]
    assert "### Evidence summary · bounded Google-indexed sample" in summary
    assert "Search query:** finance query" in summary
    assert r"Search run:** 2026\-11\-01T00\:00\:00\+00\:00" in summary
    assert "Visible-text matches for finance:** 1 of 2 readable posts" in summary
    assert "Readable post pages:** 2 of 3 candidates" in summary
    assert "Observed dates:** 1 of 2 readable posts" in summary


def test_extraction_matches_canonical_phrase_and_preserves_capture_terms():
    raw_input = '  "personal\\   finance"  '
    effective_phrase = normalize_instagram_phrase(raw_input)
    rendered = _render_instagram_extraction({
        "query": 'site:instagram.com/p/ "personal finance"',
        "searched_at": "2026-11-01T00:00:00+00:00",
        "candidates": [{
            "source_url": "https://www.instagram.com/p/example/",
            "status": "read",
            "search_result_text": "unverified search context",
            "post_text": "A personal finance plan starts with a budget.",
        }, {
            "source_url": "https://www.instagram.com/p/personal-finance/",
            "status": "read",
            "search_result_text": "personal finance appears in this snippet only",
            "post_text": "A simple weekly budgeting routine.",
        }],
    }, raw_input)

    assert effective_phrase == "personal finance"
    assert "Visible-text matches for personal finance:** 1 of 2 readable posts" in rendered
    assert "Visible post text match:** personal finance" in rendered
    assert "No match for personal finance in the visible post text" in rendered
    assert "Input as entered:**" in rendered
    assert "Effective phrase used for matching:** personal finance" in rendered
    assert "Capture command for this result:** capture" in rendered
    assert "Google search snippet (unverified)" in rendered
    assert 'capture "personal\\\\ finance"' in rendered
    raw_normalized_terms = " ".join(raw_input.split()).casefold()
    latest = {"terms": raw_normalized_terms}
    assert _latest_search_matches_terms(latest, raw_input)
    assert not _latest_search_matches_terms(latest, effective_phrase)


def test_hashtag_remains_part_of_canonical_match_phrase():
    assert normalize_instagram_phrase("  #Finance  ") == "#Finance"
    rendered = _render_instagram_extraction({
        "query": 'site:instagram.com/p/ "#finance"',
        "candidates": [{
            "source_url": "https://www.instagram.com/p/example/",
            "status": "read",
            "search_result_text": "#finance in snippet only",
            "post_text": "A #finance tip.",
        }],
    }, "  #Finance  ")
    assert r"Visible-text matches for \#Finance:** 1 of 1 readable posts" in rendered
    assert "Input as entered" not in rendered


def test_extract_keeps_raw_normalized_snapshot_terms_for_existing_capture_matching(monkeypatch):
    raw_input = '  "personal\\   finance"  '

    class Session:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            pass

    session = Session()
    monkeypatch.setattr(app.cl, "user_session", session)
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", lambda _terms: {
        "query": build_instagram_search(raw_input)["query"],
        "search_engine": "Google", "searched_at": "2026-11-01T00:00:00+00:00",
        "candidates": [], "errors": [], "provenance_note": "Sample only.",
    })

    asyncio.run(app._send_instagram_extraction(raw_input))

    latest = session.get("socialrum_latest_public_search")
    assert latest["terms"] == '"personal\\ finance"'
    assert _latest_search_matches_terms(latest, raw_input)
    assert not _latest_search_matches_terms(latest, normalize_instagram_phrase(raw_input))


def test_extraction_renders_only_collector_provided_creator_metadata():
    base_candidate = {
        "source_url": "https://www.instagram.com/p/example/",
        "status": "read",
        "post_text": "A finance tip about saving.",
    }
    rendered_known = _render_instagram_extraction({
        "query": "finance", "candidates": [{**base_candidate, "creator": "financialtimes"}],
    }, "finance")
    rendered_unknown = _render_instagram_extraction({
        "query": "finance", "candidates": [base_candidate],
    }, "finance")

    assert "financialtimes" in rendered_known
    assert "creator not identified" in rendered_unknown


def test_extraction_displays_quoted_source_hook_before_editable_template():
    rendered = _render_instagram_extraction({
        "query": "finance",
        "candidates": [{
            "source_url": "https://www.instagram.com/p/example/",
            "status": "read",
            "post_text": "One finance habit: track every monthly expense.",
        }],
    }, "finance")

    punchline_at = rendered.index("**Punchline:**")
    suggestion_at = rendered.index("**Editable draft suggestion:**")
    assert r"Source hook \(quoted; see linked source\)" in rendered
    assert punchline_at < suggestion_at


def test_topic_signals_count_only_cooccurring_tags_in_readable_posts():
    result = {
        "query": '"#finance"',
        "candidates": [
            {
                "source_url": "https://www.instagram.com/p/one/",
                "status": "read",
                "search_result_text": "#finance #viral",
                "post_text": "#finance #tax #education #tax",
            },
            {
                "source_url": "https://www.instagram.com/p/two/",
                "status": "read",
                "search_result_text": "#market",
                "post_text": "#Finance #tax #investing",
            },
            {
                "source_url": "https://www.instagram.com/p/blocked/",
                "status": "unavailable",
                "search_result_text": "#global",
                "post_text": "#market #stocks",
            },
        ],
    }

    rendered = _render_instagram_extraction(result, "#finance")

    assert "Counted distinct readable posts: 2 of 3 candidates." in rendered
    assert "**\\#tax** — 2 of 2 readable posts." in rendered
    assert "**\\#education** — 1 of 2 readable posts." in rendered
    assert "**\\#investing** — 1 of 2 readable posts." in rendered
    signals = rendered.split("## Co-occurring hashtags in readable posts", maxsplit=1)[1]
    assert "\\#viral" not in signals and "\\#market** —" not in signals
    assert "This small convenience sample may be incomplete" in rendered
    assert "does not estimate platform\\-wide frequency, popularity, engagement, or trends" in rendered


def test_topic_signals_report_zero_readable_or_zero_cooccurrence_clearly():
    unavailable = _render_instagram_extraction({
        "query": "#finance",
        "candidates": [{"status": "unavailable", "search_result_text": "#investing"}],
    }, "#finance")
    readable_without_tags = _render_instagram_extraction({
        "query": "personal finance",
        "candidates": [{
            "status": "read",
            "source_url": "https://www.instagram.com/p/plain/",
            "post_text": "A plain caption without hashtags.",
        }],
    }, "personal finance")

    assert "No signals computed because no post text was readable." in unavailable
    assert "No co-occurring hashtags were present in the readable post text" in readable_without_tags


def test_topic_signal_report_discloses_omitted_tags_after_top_ten():
    text = " ".join(f"#tag{i}" for i in range(11))
    rendered = _render_instagram_extraction({
        "query": "topic",
        "candidates": [{
            "status": "read",
            "source_url": "https://www.instagram.com/p/many-tags/",
            "post_text": text,
        }],
    }, "topic")

    assert "Top 10 shown; additional co-occurring hashtags were omitted." in rendered


def test_generic_extract_prompt_accepts_hashtag_or_phrase():
    assert "hashtag" in INSTAGRAM_KEYWORD_PROMPT
    assert "#finance" in INSTAGRAM_KEYWORD_PROMPT
    assert "personal finance" in INSTAGRAM_KEYWORD_PROMPT
    assert "up to five" in INSTAGRAM_KEYWORD_PROMPT


def test_welcome_prioritizes_real_topic_extraction_over_synthetic_demo():
    assert WELCOME_MESSAGE.index("Search a topic") < WELCOME_MESSAGE.index("synthetic demo")
    assert "extract #finance" in WELCOME_MESSAGE
    assert "extract personal finance" in WELCOME_MESSAGE
    assert "not a trend or popularity report" in WELCOME_MESSAGE


def test_snapshot_metadata_omits_captions_and_search_snippets():
    metadata = _snapshot_candidate_metadata([{
        "source_url": "https://www.instagram.com/p/example/",
        "status": "read",
        "posted_at": "2026-10-30",
        "post_text": "private caption text must not be saved",
        "search_result_text": "unverified snippet must not be saved",
    }, {
        "source_url": "https://www.instagram.com/reel/example/",
        "status": "unavailable",
        "error": "login wall",
    }])

    assert metadata == [
        {"url": "https://www.instagram.com/p/example/", "status": "read", "observed_date": "2026-10-30"},
        {"url": "https://www.instagram.com/reel/example/", "status": "unreadable"},
    ]


def test_capture_is_scoped_to_current_search_terms_without_researching():
    latest = {"terms": "#finance planning"}

    assert _latest_search_matches_terms(latest, "  #Finance   planning ")
    assert not _latest_search_matches_terms(latest, "personal finance")
    assert not _latest_search_matches_terms(None, "#finance planning")


def test_search_topic_action_closes_chainlit_task_after_ask(monkeypatch):
    ended = []

    async def prompt():
        return None

    async def task_end():
        ended.append(True)

    monkeypatch.setattr(app, "_prompt_instagram_extraction", prompt)
    monkeypatch.setattr(app.cl, "context", SimpleNamespace(emitter=SimpleNamespace(task_end=task_end)))

    asyncio.run(on_prompt_instagram_extraction(SimpleNamespace()))

    assert ended == [True]


def test_public_search_rejects_concurrent_request_without_queue_and_releases_slot(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    messages = []
    calls = []

    class Session:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def fake_search(terms):
        calls.append(terms)
        if terms == "first":
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test search release timed out")
        return {
            "query": terms,
            "search_engine": "Google",
            "searched_at": "2026-11-01T00:00:00+00:00",
            "candidates": [{
                "source_url": f"https://www.instagram.com/p/{terms}/",
                "status": "read",
                "post_text": f"Visible text for {terms}.",
            }],
            "errors": [],
            "provenance_note": "Sample only.",
        }

    session = Session()
    monkeypatch.setattr(app.cl, "user_session", session)
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", fake_search)

    async def run_overlapping_searches():
        first = asyncio.create_task(app._send_instagram_extraction("first"))
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        await app._send_instagram_extraction("queued")
        assert calls == ["first"]
        release.set()
        await first
        await app._send_instagram_extraction("after")

    try:
        asyncio.run(run_overlapping_searches())
    finally:
        release.set()

    latest = session.get("socialrum_latest_public_search")
    assert calls == ["first", "after"]
    assert latest["terms"] == "after"
    assert latest["candidates"][0]["url"].endswith("/after/")
    assert any("new request was not queued" in message for message in messages)


def test_public_search_failure_releases_admission_slot(monkeypatch):
    calls = []
    messages = []

    class Session:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def fake_search(terms):
        calls.append(terms)
        if terms == "fails":
            raise RuntimeError("simulated bounded search failure")
        return {
            "query": terms, "search_engine": "Google",
            "searched_at": "2026-11-01T00:00:00+00:00", "candidates": [],
            "errors": [], "provenance_note": "Sample only.",
        }

    monkeypatch.setattr(app.cl, "user_session", Session())
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", fake_search)

    async def run_searches():
        await app._send_instagram_extraction("fails")
        await app._send_instagram_extraction("after-failure")

    asyncio.run(run_searches())

    assert calls == ["fails", "after-failure"]
    assert any("simulated bounded search failure" in message for message in messages)
    assert any("Instagram post candidates" in message for message in messages)


def test_public_search_session_setup_failure_releases_admission_slot(monkeypatch):
    messages = []
    search_calls = []

    class Session:
        def __init__(self, fail_latest=False):
            self.values = {}
            self.fail_latest = fail_latest

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            if self.fail_latest and key == "socialrum_latest_public_search":
                raise RuntimeError("simulated session setup failure")
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def fake_search(_terms):
        search_calls.append(True)
        return {
            "query": "finance query", "search_engine": "Google",
            "searched_at": "2026-11-01T00:00:00+00:00", "candidates": [],
            "errors": [], "provenance_note": "Sample only.",
        }

    monkeypatch.setattr(app.cl, "user_session", Session(fail_latest=True))
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", fake_search)

    with pytest.raises(RuntimeError, match="simulated session setup failure"):
        asyncio.run(app._send_instagram_extraction("first"))

    assert not app._PUBLIC_SEARCH_ADMISSION.locked()
    monkeypatch.setattr(app.cl, "user_session", Session())
    asyncio.run(app._send_instagram_extraction("after-setup-failure"))

    assert search_calls == [True]


def test_cancelled_search_keeps_admission_until_worker_finishes(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []
    messages = []

    class Session:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def fake_search(terms):
        calls.append(terms)
        if terms == "running":
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test worker release timed out")
        return {
            "query": terms, "search_engine": "Google",
            "searched_at": "2026-11-01T00:00:00+00:00", "candidates": [],
            "errors": [], "provenance_note": "Sample only.",
        }

    monkeypatch.setattr(app.cl, "user_session", Session())
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "search_and_collect_public_posts", fake_search)

    async def exercise_cancelled_search():
        task = asyncio.create_task(app._send_instagram_extraction("running"))
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("the extraction coroutine should propagate cancellation")

        await app._send_instagram_extraction("during-cancelled-worker")
        assert calls == ["running"]
        assert app._PUBLIC_SEARCH_ADMISSION.locked()

        release.set()
        for _ in range(500):
            if not app._PUBLIC_SEARCH_ADMISSION.locked():
                break
            await asyncio.sleep(0.01)
        assert not app._PUBLIC_SEARCH_ADMISSION.locked()
        await app._send_instagram_extraction("after-worker")

    try:
        asyncio.run(exercise_cancelled_search())
    finally:
        release.set()

    assert calls == ["running", "after-worker"]
    assert any("new request was not queued" in message for message in messages)


def test_snapshot_comparison_is_explicitly_sample_only():
    report = _render_snapshot_comparison({
        "status": "compared_local_observations",
        "previous": {"captured_at": "2026-10-30T10:00:00+00:00", "candidate_count": 2, "successfully_read_count": 1},
        "latest": {"captured_at": "2026-10-31T10:00:00+00:00", "candidate_count": 2, "successfully_read_count": 2},
        "recurring_urls": ["https://www.instagram.com/p/a/"],
        "newly_observed_urls": ["https://www.instagram.com/p/b/"],
        "no_longer_observed_urls": [],
    }, "#finance")

    assert "Repeated Google-indexed sample comparison" in report
    assert "1 readable of 2 candidates" in report
    assert "2 readable of 2 candidates" in report
    assert "does not measure posting growth, freshness, popularity, or Instagram-wide trends" in report
    assert "URL first observed later may still point to an older post" in report
    assert "Aggregate counts are unavailable" in report
    assert "Missing aggregates are unavailable, not zero" in report


def test_snapshot_topic_signal_metadata_keeps_only_aggregate_counts():
    candidates = [
        {"source_url": "https://www.instagram.com/p/a/", "status": "read", "post_text": "Budget tips #saving #tax #saving"},
        {"source_url": "https://www.instagram.com/p/b/", "status": "read", "post_text": "#saving again"},
        {"source_url": "https://www.instagram.com/p/c/", "status": "unavailable", "post_text": "#tax"},
    ]

    summary = _snapshot_topic_signal_metadata(candidates, "#finance")

    assert summary == {
        "read_denominator": 2,
        "tags": [{"tag": "#saving", "count": 2}, {"tag": "#tax", "count": 1}],
        "truncated": False,
    }
    assert "post_text" not in str(summary)
    assert _snapshot_topic_signal_metadata(candidates[2:], "#finance") is None


def test_snapshot_comparison_marks_omitted_truncated_tag_count_unknown():
    report = _render_snapshot_comparison({
        "status": "compared_local_observations",
        "previous": {"captured_at": "earlier", "candidate_count": 2, "successfully_read_count": 2},
        "latest": {"captured_at": "later", "candidate_count": 2, "successfully_read_count": 2},
        "recurring_urls": [], "newly_observed_urls": [], "no_longer_observed_urls": [],
        "topic_signals": {
            "status": "available",
            "previous": {"read_denominator": 2, "truncated": True, "tags": [{"tag": "#saving", "count": 2, "raw": "2/2"}]},
            "latest": {"read_denominator": 2, "truncated": False, "tags": [{"tag": "#tax", "count": 1, "raw": "1/2"}]},
        },
    }, "finance")

    assert r"\#saving** — earlier 2/2; later 0/2" in report
    assert r"\#tax** — earlier unknown; later 1/2" in report
    assert "omitted from a truncated sample are unknown, not zero" in report
    assert "do not indicate rising or falling trends" in report


def test_concurrent_capture_consumes_one_latest_result(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    save_calls = []
    messages = []

    class Session:
        def __init__(self):
            self.values = {
                PUBLIC_SEARCH_GENERATION_KEY: 7,
                "socialrum_latest_public_search": {
                    "terms": "#finance",
                    "generation": 7,
                    "query": "finance query",
                    "searched_at": "2026-11-01T00:00:00+00:00",
                    "candidates": [{"url": "https://www.instagram.com/p/a/", "status": "read"}],
                    "topic_signals": {
                        "read_denominator": 1,
                        "tags": [{"tag": "#saving", "count": 1}],
                        "truncated": False,
                    },
                },
            }

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            messages.append(self.content)

    def fake_save(*_args, **_kwargs):
        save_calls.append(True)
        assert _kwargs["topic_signals"] == {
            "read_denominator": 1,
            "tags": [{"tag": "#saving", "count": 1}],
            "truncated": False,
        }
        started.set()
        assert release.wait(timeout=5)
        return {"id": "a" * 32, "captured_at": "2026-11-01T00:00:00+00:00"}

    session = Session()
    monkeypatch.setattr(app.cl, "user_session", session)
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "save_snapshot", fake_save)

    async def capture_twice():
        first = asyncio.create_task(_capture_latest_search("#finance"))
        await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=6)
        await _capture_latest_search("#finance")
        assert session.get("socialrum_latest_public_search") is None
        release.set()
        await first

    try:
        asyncio.run(capture_twice())
    finally:
        release.set()

    assert len(save_calls) == 1
    assert session.get("socialrum_latest_public_search") is None
    assert any("No matching search is ready to capture" in message for message in messages)


@pytest.mark.parametrize("newer_started", [False, True])
def test_failed_capture_restores_only_if_search_generation_is_unchanged(monkeypatch, newer_started):
    started = threading.Event()
    release = threading.Event()
    latest = {
        "terms": "#finance",
        "generation": 7,
        "query": "finance query",
        "searched_at": "2026-11-01T00:00:00+00:00",
        "candidates": [{"url": "https://www.instagram.com/p/a/", "status": "read"}],
    }

    class Session:
        def __init__(self):
            self.values = {
                PUBLIC_SEARCH_GENERATION_KEY: 7,
                "socialrum_latest_public_search": latest,
            }

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    class Message:
        def __init__(self, content):
            self.content = content

        async def send(self):
            pass

    def failing_save(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=5)
        raise OSError("disk full")

    session = Session()
    monkeypatch.setattr(app.cl, "user_session", session)
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app, "save_snapshot", failing_save)

    async def fail_capture():
        capture = asyncio.create_task(_capture_latest_search("#finance"))
        await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=6)
        if newer_started:
            session.set(PUBLIC_SEARCH_GENERATION_KEY, 8)
            session.set("socialrum_latest_public_search", {"terms": "personal finance", "generation": 8})
        release.set()
        await capture

    try:
        asyncio.run(fail_capture())
    finally:
        release.set()

    actual = session.get("socialrum_latest_public_search")
    if newer_started:
        assert actual == {"terms": "personal finance", "generation": 8}
    else:
        assert actual == latest

