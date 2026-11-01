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
    def __init__(self, *payloads, stop_state_payload=None):
        self.payloads = list(payloads)
        self.stop_state_payload = stop_state_payload
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if "stop-state" in command:
            target_id = command[command.index("--target") + 1]
            payload = self.stop_state_payload if self.stop_state_payload is not None else {
                "ok": True, "classification_ok": True, "status": "ok",
                "agent_should_stop": False, "human_required": False,
                "target": {"id": target_id},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
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

    def test_owned_target_accepts_singular_to_plural_reel_path_for_same_shortcode(self):
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
        text_command = next(command for command, _kwargs in runner.commands if "text" in command)
        self.assertEqual(text_command[text_command.index("--target") + 1], "owned-target")

    def test_owned_reels_feed_uses_unique_first_video_card_caption_fallback(self):
        caption = "Day 23: The basics of the 3 financial statements #finance"
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/reels/De1nZZLBtx5/"},
            ]},
            {"ok": True, "items": []},
            {"ok": True, "result": {"value": {"status": "unique", "text": caption}}},
        )

        records = collect_visible_posts(
            "https://www.instagram.com/reel/De1nZZLBtx5/",
            runner=runner,
            target_id="owned-target",
        )

        self.assertEqual(records, [{
            "source_url": "https://www.instagram.com/reels/De1nZZLBtx5/",
            "text": caption,
        }])
        probe = next(command for command, _kwargs in runner.commands if "eval" in command)
        expression = probe[probe.index("eval") + 1]
        self.assertIn('div[dir="auto"].xvs91rp.xd4r4e8', expression)
        self.assertIn("const firstVideo = videos[0]", expression)
        self.assertIn("cardVideos.length === 1 && cardCaptions.length === 1", expression)
        self.assertEqual(probe[probe.index("--target") + 1], "owned-target")

    def test_owned_reels_fallback_rejects_ambiguous_first_card_caption(self):
        runner = FakeRunner(
            {"ok": True, "pages": [
                {"id": "owned-target", "type": "page", "url": "https://www.instagram.com/reels/De1nZZLBtx5/"},
            ]},
            {"ok": True, "items": []},
            {"ok": True, "result": {"value": {"status": "ambiguous"}}},
        )

        with self.assertRaisesRegex(RuntimeError, "ambiguous visible caption"):
            collect_visible_posts(
                "https://www.instagram.com/reel/De1nZZLBtx5/",
                runner=runner,
                target_id="owned-target",
            )

        self.assertFalse(any("snapshot" in command for command, _kwargs in runner.commands))

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
        )

        records = collect_visible_posts("https://www.instagram.com/reel/xyz/", runner=runner)

        self.assertEqual(records[0]["text"], "Visible reel caption")
        self.assertEqual(runner.commands[2][0][3], "text")
        self.assertNotIn("posted_at", records[0])

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
        }])

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
