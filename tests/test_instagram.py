import json
import subprocess
import unittest
from types import SimpleNamespace

from socialrum.instagram import (
    InstagramAccessBoundaryError,
    InstagramStopStateCheckError,
    collect_visible_posts,
)


class FakeRunner:
    def __init__(
        self, *payloads, stop_state_payload=None, association_payload=None,
        reel_caption_payload=None, wait_payload=None, wait_eval_payload=None,
        wait_eval_error=False,
    ):
        self.payloads = list(payloads)
        self.stop_state_payload = stop_state_payload
        self.association_payload = association_payload or {
            "ok": True, "result": {"value": {"status": "unverified"}},
        }
        self.reel_caption_payload = reel_caption_payload or {
            "ok": True, "result": {"value": {"status": "unverified"}},
        }
        self.wait_payload = wait_payload or {"ok": True, "wait": {"matched": True}}
        self.wait_eval_payload = wait_eval_payload or {"ok": True, "wait": {"matched": True}}
        self.wait_eval_error = wait_eval_error
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if "stop-state" in command:
            target_id = command[command.index("--target") + 1]
            payload = self.stop_state_payload
            if isinstance(payload, list):
                payload = payload.pop(0)
            if payload is None:
                payload = {
                "ok": True, "classification_ok": True, "status": "ok",
                "agent_should_stop": False, "human_required": False,
                "target": {"id": target_id},
                }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if "wait" in command and "selector" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.wait_payload), stderr="")
        if "wait" in command and "eval" in command:
            if self.wait_eval_error:
                if isinstance(self.wait_eval_error, BaseException):
                    raise self.wait_eval_error
                raise RuntimeError("synthetic wait-eval timeout")
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.wait_eval_payload), stderr="")
        if any("SOCIALRUM_SOURCE_ASSOCIATION" in arg for arg in command):
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(self.association_payload), stderr="",
            )
        if any("SOCIALRUM_REEL_CARD" in arg for arg in command):
            payload = self.reel_caption_payload
            if isinstance(payload, list):
                payload = payload.pop(0)
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(payload), stderr="",
            )
        payload = self.payloads.pop(0)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


class InstagramCollectorTests(unittest.TestCase):
    def test_collects_only_article_text_from_exact_existing_user_target(self):
        runner = FakeRunner(
            {
                "ok": True,
                "pages": [
                    {"id": "public-tab", "type": "page", "url": "https://www.instagram.com/explore/tags/finance/"},
                    {"id": "other-tab", "type": "page", "url": "https://www.instagram.com/other/"},
                    {"id": "worker", "type": "worker", "url": "https://www.instagram.com/explore/tags/finance/"},
                ],
            },
            {"ok": True, "items": [{"text": "  A public finance post\n#finance  "}, {"text": "Another post"}]},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/explore/tags/finance/?next=%2Fprivate#section",
            runner=runner,
        )

        self.assertEqual(records, [
            {"source_url": "https://www.instagram.com/explore/tags/finance/", "text": "A public finance post #finance"},
            {"source_url": "https://www.instagram.com/explore/tags/finance/", "text": "Another post"},
        ])
        self.assertEqual(len(runner.commands), 3)
        self.assertEqual(runner.commands[0][0], ["cdp", "--browser-mode", "headed", "pages", "--json"])
        self.assertEqual(runner.commands[1][0][3:5], ["stop-state", "classify"])
        self.assertEqual(runner.commands[2][0], [
            "cdp", "--browser-mode", "headed", "snapshot", "--target", "public-tab",
            "--selector", "article", "--limit", "50", "--json",
        ])
        self.assertTrue(all(call[1]["timeout"] == 15 for call in runner.commands))

    def test_rejects_non_instagram_or_non_https_urls_without_running_cdp(self):
        runner = FakeRunner()
        for url in (
            "http://www.instagram.com/explore/tags/finance/",
            "https://instagram.com.evil.example/explore/tags/finance/",
            "https://user:secret@instagram.com/explore/tags/finance/",
            "https://instagram.com:444/explore/tags/finance/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                collect_visible_posts(url, runner=runner)
        self.assertEqual(runner.commands, [])

    def test_requires_one_matching_open_page_and_does_not_navigate(self):
        runner = FakeRunner({"ok": True, "pages": [
            {"id": "different", "type": "page", "url": "https://www.instagram.com/explore/tags/markets/"},
        ]})

        with self.assertRaisesRegex(RuntimeError, "Open the exact public Instagram URL"):
            collect_visible_posts("https://www.instagram.com/explore/tags/finance/", runner=runner)

        self.assertEqual(len(runner.commands), 1)
        self.assertEqual(runner.commands[0][0][-2:], ["pages", "--json"])

    def test_blocked_stop_state_prevents_any_caption_or_metadata_evaluation(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            stop_state_payload={
                "ok": True, "classification_ok": True, "status": "blocked",
                "stop_state": "login_required", "stop_state_class": "auth",
                "agent_should_stop": True, "human_required": True,
                "target": {"id": "post"},
            },
        )

        with self.assertRaises(InstagramAccessBoundaryError) as caught:
            collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertEqual(caught.exception.stop_state, "login_required")
        self.assertEqual(len(runner.commands), 2)
        self.assertIn("stop-state", runner.commands[1][0])
        self.assertFalse(any(
            arg in {"text", "snapshot", "eval"}
            for args, _ in runner.commands[2:]
            for arg in args
        ))

    def test_unavailable_or_malformed_stop_state_fails_closed(self):
        for classifier_result in (
            {"ok": False, "code": "classifier_unavailable"},
            {"ok": True, "classification_ok": False, "status": "ok", "target": {"id": "post"}},
            {"ok": True, "classification_ok": True, "status": "ok"},
        ):
            with self.subTest(result=classifier_result):
                runner = FakeRunner(
                    {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
                    stop_state_payload=classifier_result,
                )

                with self.assertRaises(InstagramStopStateCheckError) as caught:
                    collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

                self.assertNotIsInstance(caught.exception, InstagramAccessBoundaryError)
                self.assertEqual(
                    str(caught.exception),
                    "The browser page could not be verified as accessible. "
                    "Review it manually; no post content was collected.",
                )
                self.assertNotIn("classifier_unavailable", str(caught.exception))
                self.assertEqual(len(runner.commands), 2)
                self.assertIn("stop-state", runner.commands[1][0])

    def test_optional_stop_state_fields_may_be_absent_or_empty(self):
        for optional_fields in ({}, {"stop_state": "", "agent_should_stop": False, "human_required": False}):
            with self.subTest(optional_fields=optional_fields):
                classifier = {
                    "ok": True, "classification_ok": True, "status": "ok",
                    "target": {"id": "tag"}, **optional_fields,
                }
                runner = FakeRunner(
                    {"ok": True, "pages": [{"id": "tag", "type": "page", "url": "https://www.instagram.com/explore/tags/finance/"}]},
                    {"ok": True, "items": [{"text": "Public finance item"}]},
                    stop_state_payload=classifier,
                )

                records = collect_visible_posts("https://www.instagram.com/explore/tags/finance/", runner=runner)

                self.assertEqual(records[0]["text"], "Public finance item")

    def test_malformed_optional_stop_state_fields_fail_closed(self):
        malformed = (
            {"agent_should_stop": "true"},
            {"agent_should_stop": 1},
            {"human_required": None},
            {"stop_state": []},
            {"stop_state": None},
            {"stop_state": 7},
        )
        for fields in malformed:
            with self.subTest(fields=fields):
                runner = FakeRunner(
                    {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
                    stop_state_payload={
                        "ok": True, "classification_ok": True, "status": "ok",
                        "target": {"id": "post"}, **fields,
                    },
                )

                with self.assertRaises(InstagramStopStateCheckError) as caught:
                    collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

                self.assertNotIsInstance(caught.exception, InstagramAccessBoundaryError)
                self.assertEqual(len(runner.commands), 2)

    def test_refuses_ambiguous_matching_tabs(self):
        runner = FakeRunner({"ok": True, "pages": [
            {"id": "one", "type": "page", "url": "https://www.instagram.com/p/abc/"},
            {"id": "two", "type": "page", "url": "https://www.instagram.com/p/abc/?__a=1"},
        ]})

        with self.assertRaisesRegex(RuntimeError, "one headed tab"):
            collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

    def test_owned_target_selection_works_with_duplicate_matching_tabs(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "other-tab", "type": "page", "url": "https://www.instagram.com/explore/tags/finance/"},
                {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/explore/tags/finance/"},
            ]},
            {"ok": True, "items": [{"text": "Visible finance caption"}]},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/explore/tags/finance/",
            runner=runner,
            target_id="owned-target",
        )

        self.assertEqual(records[0]["text"], "Visible finance caption")
        snapshot = next(command for command, _kwargs in runner.commands if "snapshot" in command)
        self.assertEqual(snapshot[snapshot.index("--target") + 1], "owned-target")

    def test_owned_target_with_mismatched_path_is_rejected_before_stop_check(self):
        runner = FakeRunner({"ok": True, "pages": [
            {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/reels/different-id/"},
        ]})

        with self.assertRaisesRegex(RuntimeError, "not showing the requested Instagram URL"):
            collect_visible_posts(
                "https://www.instagram.com/p/abc/", runner=runner, target_id="owned-target",
            )

        self.assertEqual(len(runner.commands), 1)
        self.assertIn("pages", runner.commands[0][0])

    def test_same_shortcode_reels_feed_keeps_unique_caption_unverified(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/reels/De1nZZLBtx5/"},
            ]},
            {"ok": True, "items": [{"text": "Visible reel caption"}]},
            {"ok": True, "result": {"value": {"status": "no_scoped_time", "caption_count": 1}}},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/reel/De1nZZLBtx5/",
            runner=runner,
            target_id="owned-target",
        )

        self.assertEqual(records[0]["text"], "Visible reel caption")
        self.assertEqual(records[0]["source_url"], "https://www.instagram.com/reels/De1nZZLBtx5/")
        # The matching page shortcode is not enough: /reels/ may show
        # recommendations, so the unique global caption could belong elsewhere.
        self.assertEqual(records[0]["source_association"], "unverified")
        text_command = next(command for command, _kwargs in runner.commands if "text" in command)
        self.assertEqual(text_command[text_command.index("--target") + 1], "owned-target")

    def test_owned_reels_page_does_not_attribute_unverified_first_video_caption(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/reels/De1nZZLBtx5/"},
            ]},
            {"ok": True, "items": []},
        )

        with self.assertRaisesRegex(RuntimeError, "No visible post caption"):
            collect_visible_posts(
                "https://www.instagram.com/reel/De1nZZLBtx5/",
                runner=runner,
                target_id="owned-target",
            )

        self.assertTrue(any(
            any("SOCIALRUM_REEL_CARD" in arg for arg in command)
            for command, _kwargs in runner.commands
        ))

    def test_reel_div_caption_from_single_video_card_is_kept_unverified_without_link(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={
                "ok": True, "result": {"value": {
                    "status": "unverified", "text": "Visible card caption", "shortcode": None,
                    "candidate_count": 1,
                }},
            },
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/reel/xyz/",
            "text": "Visible card caption",
            "source_association": "unverified",
        }])
        probe = next(
            command for command, _kwargs in runner.commands
            if any("SOCIALRUM_REEL_CARD" in arg for arg in command)
        )
        expression = probe[probe.index("eval") + 1]
        self.assertIn('div[dir="auto"].xvs91rp.xd4r4e8', expression)
        self.assertIn('"reel", "reels"', expression)
        self.assertIn('node.hidden', expression)
        self.assertIn('aria-hidden', expression)
        self.assertIn('style.display === "none"', expression)
        self.assertIn('style.visibility === "hidden"', expression)
        self.assertIn('style.opacity', expression)

    def test_reel_div_caption_with_same_card_permalink_is_verified(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={
                "ok": True, "result": {"value": {
                    "status": "unique", "shortcode": "xyz", "text": "Bound reel caption",
                    "candidate_count": 1,
                }},
            },
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/reel/xyz/",
            "text": "Bound reel caption",
            "source_association": "verified",
        }])

    def test_reel_fallback_rejects_ambiguous_cross_card_caption_candidates(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={"ok": True, "result": {"value": {
                "status": "ambiguous", "candidate_count": 2,
            }}},
        )

        with self.assertRaisesRegex(RuntimeError, "No visible post caption"):
            collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)
        self.assertFalse(any("wait" in command for command, _kwargs in runner.commands))

    def test_reel_caption_waits_once_only_when_initial_probe_has_no_candidates(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload=[
                {"ok": True, "result": {"value": {
                    "status": "unverified", "candidate_count": 0,
                }}},
                {"ok": True, "result": {"value": {
                    "status": "unique", "shortcode": "xyz", "text": "Ready caption",
                    "candidate_count": 1,
                }}},
            ],
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records[0]["source_association"], "verified")
        self.assertEqual(records[0]["text"], "Ready caption")
        commands = [command for command, _kwargs in runner.commands]
        first_probe = next(i for i, command in enumerate(commands) if any(
            "SOCIALRUM_REEL_CARD" in arg for arg in command
        ))
        wait_index = next(i for i, command in enumerate(commands) if "wait" in command)
        state_indexes = [i for i, command in enumerate(commands) if "stop-state" in command]
        probe_indexes = [i for i, command in enumerate(commands) if any(
            "SOCIALRUM_REEL_CARD" in arg for arg in command
        ) and "wait" not in command]
        self.assertEqual(len(probe_indexes), 2)
        self.assertEqual(len(state_indexes), 2)
        self.assertEqual(first_probe, probe_indexes[0])
        self.assertLess(probe_indexes[0], wait_index)
        self.assertLess(wait_index, state_indexes[1])
        self.assertLess(state_indexes[1], probe_indexes[1])
        self.assertEqual(commands[wait_index][commands[wait_index].index("--timeout") + 1], "4s")

    def test_reel_caption_wait_reclassifies_stop_state_before_retry(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={"ok": True, "result": {"value": {
                "status": "unverified", "candidate_count": 0,
            }}},
            stop_state_payload=[
                {
                    "ok": True, "classification_ok": True, "status": "ok",
                    "agent_should_stop": False, "human_required": False,
                    "target": {"id": "reel"},
                },
                {
                    "ok": True, "classification_ok": True, "status": "blocked",
                    "stop_state": "login_required", "agent_should_stop": True,
                    "human_required": True, "target": {"id": "reel"},
                },
            ],
        )

        with self.assertRaises(InstagramAccessBoundaryError):
            collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        commands = [command for command, _kwargs in runner.commands]
        self.assertEqual(sum("wait" not in command and any("SOCIALRUM_REEL_CARD" in arg for arg in command) for command in commands), 1)
        wait_index = next(i for i, command in enumerate(commands) if "wait" in command)
        state_indexes = [i for i, command in enumerate(commands) if "stop-state" in command]
        self.assertEqual(len(state_indexes), 2)
        self.assertLess(wait_index, state_indexes[1])

    def test_reel_probe_rejects_missing_zero_or_invalid_candidate_count(self):
        for count in (None, 0, True, "1", -1):
            with self.subTest(candidate_count=count):
                runner = FakeRunner(
                    {"ok": True, "pages": [
                        {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
                    ]},
                    {"ok": True, "items": []},
                    reel_caption_payload={"ok": True, "result": {"value": {
                        "status": "unique", "shortcode": "xyz", "text": "Caption",
                        "candidate_count": count,
                    }}},
                )

                with self.assertRaisesRegex(RuntimeError, "No visible post caption"):
                    collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

                expected_probe_count = 2 if count == 0 else 1
                self.assertEqual(
                    sum("wait" not in command and any("SOCIALRUM_REEL_CARD" in arg for arg in command)
                        for command, _kwargs in runner.commands),
                    expected_probe_count,
                )

    def test_reel_probe_does_not_retry_when_wait_did_not_match(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={"ok": True, "result": {"value": {
                "status": "unverified", "candidate_count": 0,
            }}},
            wait_payload={"ok": True, "wait": {"matched": False}},
        )

        with self.assertRaisesRegex(RuntimeError, "No visible post caption"):
            collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(sum("wait" not in command and any("SOCIALRUM_REEL_CARD" in arg for arg in command)
                             for command, _kwargs in runner.commands), 1)

    def test_unverified_reel_card_can_become_verified_during_one_second_eval_wait(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload=[
                {"ok": True, "result": {"value": {
                    "status": "unverified", "candidate_count": 1,
                    "text": "Caption awaiting permalink", "shortcode": None,
                }}},
                {"ok": True, "result": {"value": {
                    "status": "unique", "candidate_count": 1,
                    "text": "Caption awaiting permalink", "shortcode": "xyz",
                }}},
            ],
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records[0]["source_association"], "verified")
        commands = [command for command, _kwargs in runner.commands]
        probe_indexes = [i for i, command in enumerate(commands) if any(
            "SOCIALRUM_REEL_CARD" in arg for arg in command
        ) and "wait" not in command]
        wait_index = next(i for i, command in enumerate(commands) if "wait" in command and "eval" in command)
        state_indexes = [i for i, command in enumerate(commands) if "stop-state" in command]
        self.assertEqual(len(probe_indexes), 2)
        self.assertEqual(len(state_indexes), 2)
        self.assertLess(probe_indexes[0], wait_index)
        self.assertLess(wait_index, state_indexes[1])
        self.assertLess(state_indexes[1], probe_indexes[1])
        wait_command = commands[wait_index]
        self.assertEqual(wait_command[wait_command.index("--timeout") + 1], "1s")
        self.assertIn("value.status === 'unique' && value.shortcode === \"xyz\"",
                      wait_command[wait_command.index("--ready-expr") + 1])
        self.assertIn("value.text.trim().length > 0",
                      wait_command[wait_command.index("--ready-expr") + 1])

    def test_unverified_reel_card_wait_timeout_keeps_candidate_unverified(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={"ok": True, "result": {"value": {
                "status": "unverified", "candidate_count": 1,
                "text": "Caption without reel link", "shortcode": None,
            }}},
            wait_eval_error=True,
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records[0]["source_association"], "unverified")
        self.assertEqual(records[0]["text"], "Caption without reel link")
        self.assertEqual(sum("stop-state" in command for command in [c for c, _ in runner.commands]), 2)

    def test_wait_readiness_rejects_verified_probe_with_empty_caption_text(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload=[
                {"ok": True, "result": {"value": {
                    "status": "unverified", "candidate_count": 1,
                    "text": "Caption pending link", "shortcode": None,
                }}},
                {"ok": True, "result": {"value": {
                    "status": "unique", "candidate_count": 1,
                    "text": "  \n ", "shortcode": "xyz",
                }}},
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "No visible post caption"):
            collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertIn("value.text.trim().length > 0", next(
            arg for command, _kwargs in runner.commands if "wait" in command and "eval" in command
            for arg in command[command.index("--ready-expr") + 1:command.index("--ready-expr") + 2]
        ))

    def test_wait_eval_stop_error_is_rechecked_then_preserved(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={"ok": True, "result": {"value": {
                "status": "unverified", "candidate_count": 1,
                "text": "Caption pending link", "shortcode": None,
            }}},
            wait_eval_error=InstagramAccessBoundaryError("login_required"),
        )

        with self.assertRaises(InstagramAccessBoundaryError) as caught:
            collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(caught.exception.stop_state, "login_required")
        self.assertEqual(sum("stop-state" in command for command, _ in runner.commands), 2)
        self.assertEqual(sum("wait" in command and "eval" in command
                             for command, _ in runner.commands), 1)
        self.assertEqual(sum("wait" not in command and any("SOCIALRUM_REEL_CARD" in arg
                                                           for arg in command)
                             for command, _ in runner.commands), 1)

    def test_unverified_reel_eval_wait_rechecks_and_stops_at_access_boundary(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"},
            ]},
            {"ok": True, "items": []},
            reel_caption_payload={"ok": True, "result": {"value": {
                "status": "unverified", "candidate_count": 1,
                "text": "Caption without reel link", "shortcode": None,
            }}},
            wait_eval_error=True,
            stop_state_payload=[
                {
                    "ok": True, "classification_ok": True, "status": "ok",
                    "agent_should_stop": False, "human_required": False,
                    "target": {"id": "reel"},
                },
                {
                    "ok": True, "classification_ok": True, "status": "blocked",
                    "stop_state": "login_required", "agent_should_stop": True,
                    "human_required": True, "target": {"id": "reel"},
                },
            ],
        )

        with self.assertRaises(InstagramAccessBoundaryError):
            collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(sum("wait" not in command and any("SOCIALRUM_REEL_CARD" in arg for arg in command)
                             for command, _kwargs in runner.commands), 1)

    def test_owned_reels_page_rejects_ambiguous_caption_matches(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/reels/De1nZZLBtx5/"},
            ]},
            {"ok": True, "items": [{"text": "Caption for another reel"}, {"text": "Requested reel caption"}]},
        )

        with self.assertRaisesRegex(RuntimeError, "caption is ambiguous"):
            collect_visible_posts(
                "https://www.instagram.com/reel/De1nZZLBtx5/",
                runner=runner,
                target_id="owned-target",
            )

        self.assertTrue(any(
            any("SOCIALRUM_REEL_CARD" in arg for arg in command)
            for command, _kwargs in runner.commands
        ))

    def test_unowned_reels_path_does_not_use_owned_feed_fallback(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "reels-tab", "type": "page", "url": "https://www.instagram.com/reels/De1nZZLBtx5/"},
            ]},
            {"ok": True, "items": [{"text": "Article text"}]},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/reels/De1nZZLBtx5/", runner=runner,
        )

        self.assertEqual(records[0]["text"], "Article text")
        self.assertFalse(any("eval" in command for command, _kwargs in runner.commands))

    def test_does_not_return_data_when_page_has_no_visible_articles(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "tab", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": []},
        )

        with self.assertRaisesRegex(RuntimeError, "may require login"):
            collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

    def test_single_post_uses_only_one_narrow_caption_span(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Visible post caption #finance"}]},
            {"ok": True, "result": {"value": {
                "status": "unique", "caption_count": 1, "time_count": 1,
                "block_text_chars": 725,
                "creator_count": 1, "creator": "financialtimes",
                "datetime": "2026-10-30T17:06:17.000Z",
            }}},
        )

        records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/p/abc/",
            "text": "Visible post caption #finance",
            "source_association": "unverified",
            "posted_at": "2026-10-30",
            "creator": "financialtimes",
        }])
        self.assertEqual(runner.commands[1][0][3:5], ["stop-state", "classify"])
        self.assertEqual(runner.commands[2][0][3], "text")
        self.assertTrue(any(arg.startswith("span.x193iq5w") for arg in runner.commands[2][0]))
        self.assertIn("--limit", runner.commands[2][0])
        self.assertEqual(runner.commands[2][0][runner.commands[2][0].index("--limit") + 1], "2")
        self.assertEqual(runner.commands[3][0][3], "eval")

    def test_exact_reel_route_uses_same_narrow_caption_fallback(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "reel", "type": "page", "url": "https://www.instagram.com/reel/xyz/"}]},
            {"ok": True, "items": [{"text": "Visible reel caption"}]},
            {"ok": True, "result": {"value": {"status": "no_scoped_time", "caption_count": 1}}},
            reel_caption_payload={
                "ok": True, "result": {"value": {
                    "status": "unique", "shortcode": "xyz", "text": "Visible reel caption",
                    "candidate_count": 1,
                }},
            },
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records[0]["text"], "Visible reel caption")
        self.assertEqual(records[0]["source_association"], "verified")
        self.assertEqual(runner.commands[2][0][3], "text")
        self.assertNotIn("posted_at", records[0])
        reel_probe = next(
            command for command, _kwargs in runner.commands
            if any("SOCIALRUM_REEL_CARD" in arg for arg in command)
        )
        expression = reel_probe[reel_probe.index("eval") + 1]
        self.assertIn('const requested = "xyz"', expression)
        self.assertIn('card.querySelectorAll("video")', expression)
        self.assertIn('videos.length === 1', expression)
        self.assertIn('card.querySelectorAll("a[href]")', expression)

    def test_permalink_with_different_shortcode_does_not_verify_unique_caption(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/requested/"}]},
            {"ok": True, "items": [{"text": "Unique but unrelated recommendation caption"}]},
            {"ok": True, "result": {"value": {"status": "no_scoped_time", "caption_count": 1}}},
            association_payload={
                "ok": True, "result": {"value": {
                    "status": "unique", "shortcode": "different",
                    "text": "Unique but unrelated recommendation caption",
                }},
            },
        )

        records = collect_visible_posts("https://www.instagram.com/p/requested/", runner=runner)

        self.assertEqual(records[0]["text"], "Unique but unrelated recommendation caption")
        self.assertEqual(records[0]["source_association"], "unverified")

    def test_permalink_outside_caption_article_does_not_verify_association(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/requested/"}]},
            {"ok": True, "items": [{"text": "Unique caption in a separate card"}]},
            {"ok": True, "result": {"value": {"status": "no_scoped_time", "caption_count": 1}}},
            association_payload={
                "ok": True, "result": {"value": {
                    "status": "unverified", "shortcode": None,
                    "text": "Unique caption in a separate card",
                }},
            },
        )

        records = collect_visible_posts("https://www.instagram.com/p/requested/", runner=runner)

        self.assertEqual(records[0]["source_association"], "unverified")
        probe = next(
            command for command, _kwargs in runner.commands
            if any("SOCIALRUM_SOURCE_ASSOCIATION" in arg for arg in command)
        )
        expression = probe[probe.index("eval") + 1]
        self.assertIn('caption.closest("article")', expression)
        self.assertIn('card.querySelectorAll("a[href]")', expression)

    def test_same_card_probe_text_must_match_text_command_result(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/requested/"}]},
            {"ok": True, "items": [{"text": "Caption before DOM changed"}]},
            {"ok": True, "result": {"value": {"status": "no_scoped_time", "caption_count": 1}}},
            association_payload={
                "ok": True, "result": {"value": {
                    "status": "unique", "shortcode": "requested",
                    "text": "A different caption after DOM changed",
                }},
            },
        )

        records = collect_visible_posts("https://www.instagram.com/p/requested/", runner=runner)

        self.assertEqual(records[0]["source_association"], "unverified")

    def test_single_post_empty_caption_fails_without_broadening_to_article_or_main(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": []},
        )

        with self.assertRaisesRegex(RuntimeError, "No visible post caption"):
            collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertEqual(len(runner.commands), 3)
        self.assertNotIn("snapshot", runner.commands[2][0])

    def test_ambiguous_single_post_spans_are_rejected(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Caption candidate"}, {"text": "Other matched text"}]},
        )

        with self.assertRaisesRegex(RuntimeError, "caption is ambiguous"):
            collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertEqual(runner.commands[2][0][runner.commands[2][0].index("--limit") + 1], "2")

    def test_multiple_scoped_times_leave_post_date_unknown(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Visible post caption"}]},
            {"ok": True, "result": {"value": {
                "status": "ambiguous", "caption_count": 1, "time_count": 2, "datetime": None,
                "creator_count": 2, "creator": None,
            }}},
        )

        records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/p/abc/",
            "text": "Visible post caption",
            "source_association": "unverified",
        }])

    def test_target_collection_omits_unbound_date_and_creator_metadata(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Visible post caption"}]},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/p/abc/", runner=runner, include_metadata=False,
        )

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/p/abc/",
            "text": "Visible post caption",
            "source_association": "unverified",
        }])
        eval_commands = [command for command, _kwargs in runner.commands if "eval" in command]
        self.assertEqual(len(eval_commands), 1)
        self.assertTrue(any("SOCIALRUM_SOURCE_ASSOCIATION" in arg for arg in eval_commands[0]))

    def test_large_caption_ancestor_does_not_supply_a_post_date(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Visible post caption"}]},
            {"ok": True, "result": {"value": {
                "status": "unique", "caption_count": 1, "time_count": 1,
                "block_text_chars": 2245, "datetime": "2026-10-30T17:06:17.000Z",
                "creator_count": 1, "creator": "financialtimes",
            }}},
        )

        records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertNotIn("posted_at", records[0])
        self.assertNotIn("creator", records[0])

    def test_multiple_profile_links_leave_creator_unknown_but_keep_unique_date(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Visible post caption"}]},
            {"ok": True, "result": {"value": {
                "status": "unique", "caption_count": 1, "time_count": 1,
                "block_text_chars": 725, "creator_count": 2, "creator": None,
                "datetime": "2026-10-30T17:06:17.000Z",
            }}},
        )

        records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertEqual(records[0].get("posted_at"), "2026-10-30")
        self.assertNotIn("creator", records[0])

    def test_invalid_or_reserved_profile_path_is_not_used_as_creator(self):
        for creator in ("bad handle", "explore"):
            with self.subTest(creator=creator):
                runner = FakeRunner(
                    {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
                    {"ok": True, "items": [{"text": "Visible post caption"}]},
                    {"ok": True, "result": {"value": {
                        "status": "unique", "caption_count": 1, "time_count": 1,
                        "block_text_chars": 725, "creator_count": 1, "creator": creator,
                        "datetime": "2026-10-30T17:06:17.000Z",
                    }}},
                )

                records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

                self.assertEqual(records[0].get("posted_at"), "2026-10-30")
                self.assertNotIn("creator", records[0])

    def test_absent_profile_anchor_leaves_creator_unknown(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
            {"ok": True, "items": [{"text": "Visible post caption"}]},
            {"ok": True, "result": {"value": {
                "status": "unique", "caption_count": 1, "time_count": 1,
                "block_text_chars": 725, "creator_count": 0, "creator": None,
                "datetime": "2026-10-30T17:06:17.000Z",
            }}},
        )

        records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

        self.assertNotIn("creator", records[0])

    def test_malformed_or_timezone_free_timestamp_leaves_date_unknown(self):
        for timestamp in ("not-a-date", "2026-10-30T17:06:17"):
            with self.subTest(timestamp=timestamp):
                runner = FakeRunner(
                    {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
                    {"ok": True, "items": [{"text": "Visible post caption"}]},
                    {"ok": True, "result": {"value": {
                        "status": "unique", "caption_count": 1, "time_count": 1,
                        "block_text_chars": 725,
                        "creator_count": 1, "creator": "financialtimes",
                        "datetime": timestamp,
                    }}},
                )

                records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

                self.assertNotIn("posted_at", records[0])

    def test_malformed_eval_result_keeps_caption_and_leaves_date_unknown(self):
        for malformed_result in (None, "unexpected", []):
            with self.subTest(result=malformed_result):
                runner = FakeRunner(
                    {"ok": True, "pages": [{"id": "post", "type": "page", "url": "https://www.instagram.com/p/abc/"}]},
                    {"ok": True, "items": [{"text": "Visible post caption"}]},
                    {"ok": True, "result": malformed_result},
                )

                records = collect_visible_posts("https://www.instagram.com/p/abc/", runner=runner)

                self.assertEqual(records, [{
                    "source_url": "https://www.instagram.com/p/abc/",
                    "text": "Visible post caption",
                    "source_association": "unverified",
                }])

    def test_accepts_expected_instagram_tag_redirect_and_reports_observed_source(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{
                "id": "tab", "type": "page",
                "url": "https://www.instagram.com/popular/finance/?utm_source=explore_tag",
            }]},
            {"ok": True, "items": [{"text": "Public finance post #finance"}]},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/explore/tags/finance/", runner=runner,
        )

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/popular/finance/",
            "text": "Public finance post #finance",
        }])

    def test_expected_tag_redirect_with_no_visible_content_is_not_bypassed(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{
                "id": "tab", "type": "page",
                "url": "https://www.instagram.com/popular/finance/?utm_source=explore_tag",
            }]},
            {"ok": True, "items": []},
        )

        with self.assertRaisesRegex(RuntimeError, "may require login"):
            collect_visible_posts("https://www.instagram.com/explore/tags/finance/", runner=runner)

    def test_caps_post_count_and_text_length(self):
        runner = FakeRunner(
            {"ok": True, "pages": [{"id": "tab", "type": "page", "url": "https://instagram.com/explore/tags/finance/"}]},
            {"ok": True, "items": [{"text": "x" * 5000}] * 55},
        )

        records = collect_visible_posts("https://instagram.com/explore/tags/finance/", runner=runner)

        self.assertEqual(len(records), 50)
        self.assertEqual(len(records[0]["text"]), 4000)

    def test_cdp_error_does_not_echo_potentially_sensitive_stderr(self):
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="cookie=secret")

        with self.assertRaises(RuntimeError) as caught:
            collect_visible_posts("https://instagram.com/p/abc/", runner=runner)

        self.assertNotIn("secret", str(caught.exception))

    def test_reports_missing_cdp_cli(self):
        def runner(command, **kwargs):
            raise FileNotFoundError

        with self.assertRaisesRegex(RuntimeError, "cdp CLI is required"):
            collect_visible_posts("https://instagram.com/p/abc/", runner=runner)

    def test_reports_cdp_timeout(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 15)

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            collect_visible_posts("https://instagram.com/p/abc/", runner=runner)


if __name__ == "__main__":
    unittest.main()
