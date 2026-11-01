import json
import os
import tempfile
import unittest
from pathlib import Path

from socialrum.snapshots import (
    compare_latest_snapshots,
    delete_snapshot,
    delete_snapshots,
    list_snapshots,
    normalize_terms,
    save_snapshot,
)


class SnapshotHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "private history"
        self.terms = "  #Summer   Looks  "
        self.query = 'site:instagram.com/p/ "#Summer Looks"'
        self.source = "google"
        self.version = "query-builder-v2"

    def tearDown(self):
        self.temp.cleanup()

    def save(self, candidates, timestamp, *, topic_signals=None):
        return save_snapshot(
            self.terms,
            self.query,
            self.source,
            self.version,
            candidates,
            self.directory,
            observed_at=timestamp,
            topic_signals=topic_signals,
        )

    def test_saves_private_bounded_metadata_and_compares_url_sets(self):
        first = self.save(
            [
                {
                    "url": "https://instagram.com/p/a",
                    "status": "read",
                    "observed_date": "2026-10-29",
                },
                {"url": "https://instagram.com/p/b", "status": "unreadable"},
            ],
            "2026-10-30T10:00:00+00:00",
        )
        latest = self.save(
            [
                {"url": "https://instagram.com/p/a", "status": "read"},
                {
                    "url": "https://instagram.com/p/c",
                    "status": "read",
                    "observed_date": "2026-10-30",
                },
            ],
            "2026-10-31T10:00:00+00:00",
        )

        self.assertEqual(normalize_terms(self.terms), "#summer looks")
        self.assertEqual(first["query"], self.query)
        self.assertEqual(first["source_version"], self.version)
        self.assertNotIn("caption", json.dumps(first))
        self.assertEqual(os.stat(self.directory).st_mode & 0o777, 0o700)
        self.assertEqual(
            os.stat(self.directory / f"{latest['id']}.json").st_mode & 0o777, 0o600
        )
        comparison = compare_latest_snapshots(
            self.terms, self.query, self.source, self.version, self.directory
        )
        self.assertEqual(comparison["status"], "compared_local_observations")
        self.assertEqual(comparison["recurring_urls"], ["https://instagram.com/p/a"])
        self.assertEqual(
            comparison["newly_observed_urls"], ["https://instagram.com/p/c"]
        )
        self.assertEqual(
            comparison["no_longer_observed_urls"], ["https://instagram.com/p/b"]
        )
        self.assertEqual(comparison["previous"]["candidate_count"], 2)
        self.assertIn("not Instagram-wide trends", comparison["interpretation"])
        self.assertEqual(
            [
                item["id"]
                for item in list_snapshots(
                    "#SUMMER looks",
                    self.directory,
                    query=self.query,
                    source=self.source,
                    source_version=self.version,
                )
            ],
            [latest["id"], first["id"]],
        )

    def test_insufficient_data_and_exact_query_scoping(self):
        self.save([], "2026-10-30T10:00:00+00:00")
        self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-31T10:00:00+00:00",
        )
        result = compare_latest_snapshots(
            self.terms, self.query, self.source, self.version, self.directory
        )
        self.assertEqual(
            result, {"status": "insufficient", "reason": "snapshot_has_no_candidates"}
        )
        other = compare_latest_snapshots(
            self.terms, self.query + " extra", self.source, self.version, self.directory
        )
        self.assertEqual(other["reason"], "fewer_than_two_matching_snapshots")

    def test_query_history_matches_case_and_whitespace_but_keeps_other_queries_separate(
        self,
    ):
        save_snapshot(
            self.terms, "Site:instagram.com   #Finance", self.source, self.version,
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            self.directory, observed_at="2026-10-30T10:00:00+00:00",
        )
        save_snapshot(
            self.terms, "site:instagram.com #finance", self.source, self.version,
            [{"url": "https://instagram.com/p/b", "status": "read"}],
            self.directory, observed_at="2026-10-31T10:00:00+00:00",
        )

        matched = compare_latest_snapshots(
            self.terms, " SITE:INSTAGRAM.COM  #FINANCE ", self.source,
            self.version, self.directory,
        )
        separate = compare_latest_snapshots(
            self.terms, "site:instagram.com #finance extra", self.source,
            self.version, self.directory,
        )

        self.assertEqual(matched["status"], "compared_local_observations")
        self.assertEqual(matched["query"], " SITE:INSTAGRAM.COM  #FINANCE ")
        self.assertEqual(separate["reason"], "fewer_than_two_matching_snapshots")

    def test_each_snapshot_requires_a_successfully_read_post(self):
        self.save(
            [{"url": "https://instagram.com/p/a", "status": "unreadable"}],
            "2026-10-30T10:00:00+00:00",
        )
        self.save(
            [{"url": "https://instagram.com/p/b", "status": "read"}],
            "2026-10-31T10:00:00+00:00",
        )
        result = compare_latest_snapshots(
            self.terms, self.query, self.source, self.version, self.directory
        )
        self.assertEqual(result["reason"], "snapshot_has_no_successfully_read_post")

    def test_validation_rejects_unbounded_or_malformed_candidate_input(self):
        with self.assertRaises(ValueError):
            self.save(
                [{"url": "https://instagram.com/p/a", "status": "read"}] * 6,
                "2026-10-30T10:00:00+00:00",
            )
        with self.assertRaises(ValueError):
            self.save(
                [{"url": "file:///etc/passwd", "status": "read"}],
                "2026-10-30T10:00:00+00:00",
            )
        with self.assertRaises(ValueError):
            self.save(
                [{"url": "https://instagram.com/p/a", "status": "success"}],
                "2026-10-30T10:00:00+00:00",
            )
        with self.assertRaises(ValueError):
            self.save([], "2026-10-30T10:00:00")
        self.assertEqual(list(self.directory.glob("*.json")), [])

    def test_malformed_record_fails_closed_and_delete_is_explicit(self):
        record = self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-30T10:00:00+00:00",
        )
        self.assertTrue(delete_snapshot(record["id"], self.directory))
        self.assertFalse(delete_snapshot(record["id"], self.directory))
        self.assertFalse(list_snapshots(self.terms, self.directory))

        bad_id = "a" * 32
        (self.directory / f"{bad_id}.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            list_snapshots(self.terms, self.directory)
        with self.assertRaises(ValueError):
            delete_snapshots(self.terms, self.directory)

    def test_delete_by_normalized_term_is_scoped(self):
        one = self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-30T10:00:00+00:00",
        )
        save_snapshot(
            "summer looks",
            self.query,
            self.source,
            self.version,
            [{"url": "https://instagram.com/p/b", "status": "read"}],
            self.directory,
            observed_at="2026-10-30T10:00:00+00:00",
        )
        self.assertEqual(delete_snapshots("#summer looks", self.directory), 1)
        remaining = list_snapshots("summer looks", self.directory)
        self.assertEqual(len(remaining), 1)
        self.assertNotEqual(remaining[0]["id"], one["id"])

    def test_legacy_record_round_trips_without_rewrite_or_backfill(self):
        record = self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-30T10:00:00+00:00",
        )
        path = self.directory / f"{record['id']}.json"
        original = path.read_bytes()
        loaded = list_snapshots(self.terms, self.directory)[0]
        self.assertEqual(loaded, record)
        self.assertNotIn("topic_signals", loaded)
        self.assertEqual(path.read_bytes(), original)

    def test_mixed_legacy_and_aggregate_compares_urls_but_marks_tags_unavailable(self):
        self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-30T10:00:00+00:00",
        )
        self.save(
            [
                {"url": "https://instagram.com/p/a", "status": "read"},
                {"url": "https://instagram.com/p/b", "status": "read"},
            ],
            "2026-10-31T10:00:00+00:00",
            topic_signals={
                "read_denominator": 2,
                "tags": [{"tag": "#outfit", "count": 2}],
                "truncated": False,
            },
        )
        comparison = compare_latest_snapshots(
            self.terms, self.query, self.source, self.version, self.directory
        )
        self.assertEqual(comparison["status"], "compared_local_observations")
        self.assertEqual(comparison["recurring_urls"], ["https://instagram.com/p/a"])
        self.assertEqual(
            comparison["topic_signals"],
            {
                "status": "unavailable",
                "reason": "aggregate_missing_from_one_or_more_snapshots",
            },
        )

    def test_aggregate_comparison_uses_raw_count_denominators_and_discloses_truncation(
        self,
    ):
        aggregate = {
            "read_denominator": 2,
            "tags": [{"tag": "#style", "count": 1}],
            "truncated": True,
        }
        self.save(
            [
                {"url": "https://instagram.com/p/a", "status": "read"},
                {"url": "https://instagram.com/p/b", "status": "read"},
            ],
            "2026-10-30T10:00:00+00:00",
            topic_signals=aggregate,
        )
        latest_signals = {
            "read_denominator": 1,
            "tags": [{"tag": "#style", "count": 1}],
            "truncated": False,
        }
        self.save(
            [{"url": "https://instagram.com/p/c", "status": "read"}],
            "2026-10-31T10:00:00+00:00",
            topic_signals=latest_signals,
        )
        comparison = compare_latest_snapshots(
            self.terms, self.query, self.source, self.version, self.directory
        )
        topics = comparison["topic_signals"]
        self.assertEqual(topics["status"], "available")
        self.assertEqual(
            topics["previous"]["tags"], [{"tag": "#style", "count": 1, "raw": "1/2"}]
        )
        self.assertTrue(topics["previous"]["truncated"])
        self.assertIn("unknown, not zero", topics["previous"]["omission_note"])
        self.assertEqual(topics["latest"]["tags"][0]["raw"], "1/1")
        self.assertFalse(topics["latest"]["truncated"])
        self.assertIsNone(topics["latest"]["omission_note"])
        self.assertIn("not trend or popularity", topics["interpretation"])

    def test_invalid_aggregate_and_text_or_per_post_fields_are_rejected(self):
        read_candidate = {"url": "https://instagram.com/p/a", "status": "read"}
        invalid_aggregates = [
            {"read_denominator": 0, "tags": [], "truncated": False},
            {"read_denominator": 2, "tags": [], "truncated": False},
            {
                "read_denominator": 1,
                "tags": [{"tag": "#x", "count": 2}],
                "truncated": False,
            },
            {
                "read_denominator": 1,
                "tags": [{"tag": "not a tag", "count": 1}],
                "truncated": False,
            },
            {
                "read_denominator": 1,
                "tags": [{"tag": "#x", "count": 1, "post_url": read_candidate["url"]}],
                "truncated": False,
            },
            {"read_denominator": 1, "tags": [], "truncated": "yes"},
        ]
        for aggregate in invalid_aggregates:
            with self.subTest(aggregate=aggregate), self.assertRaises(ValueError):
                self.save(
                    [read_candidate],
                    "2026-10-30T10:00:00+00:00",
                    topic_signals=aggregate,
                )
        with self.assertRaises(ValueError):
            self.save(
                [{**read_candidate, "caption": "private text"}],
                "2026-10-30T10:00:00+00:00",
            )
        with self.assertRaises(ValueError):
            self.save(
                [read_candidate],
                "2026-10-30T10:00:00+00:00",
                topic_signals={
                    "read_denominator": 1,
                    "tags": [{"tag": "#x", "count": 1, "caption": "private text"}],
                    "truncated": False,
                },
            )
        self.assertEqual(list(self.directory.glob("*.json")), [])

    def test_saved_aggregates_contain_no_per_post_tag_mapping(self):
        record = self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-30T10:00:00+00:00",
            topic_signals={
                "read_denominator": 1,
                "tags": [{"tag": "#style", "count": 1}],
                "truncated": False,
            },
        )
        stored = (self.directory / f"{record['id']}.json").read_text(encoding="utf-8")
        self.assertNotIn("caption", stored)
        self.assertNotIn("post_tags", stored)
        self.assertEqual(
            set(record["topic_signals"]), {"read_denominator", "tags", "truncated"}
        )

    def test_aggregate_accepts_unicode_combining_marks(self):
        record = self.save(
            [{"url": "https://instagram.com/p/a", "status": "read"}],
            "2026-10-30T10:00:00+00:00",
            topic_signals={
                "read_denominator": 1,
                "tags": [{"tag": "#cafe\u0301", "count": 1}],
                "truncated": False,
            },
        )

        self.assertEqual(list_snapshots(self.terms, self.directory)[0], record)


if __name__ == "__main__":
    unittest.main()
