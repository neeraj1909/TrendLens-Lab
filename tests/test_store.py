import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from socialrum.store import DraftConflictError, export_markdown, list_drafts, load_draft, save_draft


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store_dir = Path(self.temp.name) / "private drafts"
        self.record = {
            "title": "Local first AI",
            "source_ids": ["post-17", "note-2"],
            "provenance": {"kind": "user_import", "file": "sample.csv"},
            "content": "Edited and reviewed copy.",
            "review_state": "approved",
            "template_version": "template-v1",
            "model": None,
            "metadata": {"topic": "local models"},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_save_reopen_edit_and_export_preserve_review_context(self):
        saved = save_draft(self.record, self.store_dir)
        self.assertEqual(os.stat(self.store_dir).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.store_dir / f"{saved['id']}.json").st_mode & 0o777, 0o600)
        reopened = load_draft(saved["id"], self.store_dir)

        self.assertEqual(reopened["content"], self.record["content"])
        self.assertEqual(reopened["source_ids"], self.record["source_ids"])
        self.assertEqual(reopened["provenance"], self.record["provenance"])
        self.assertEqual(reopened["review_state"], "approved")
        self.assertEqual(reopened["template_version"], "template-v1")
        self.assertIsNone(reopened["model"])
        self.assertTrue(reopened["created_at"])
        self.assertLessEqual(reopened["created_at"], reopened["updated_at"])

        edited = {**reopened, "content": "Revised copy.", "review_state": "needs_review"}
        updated = save_draft(
            edited, self.store_dir, overwrite=True, expected_updated_at=reopened["updated_at"]
        )
        self.assertEqual(updated["id"], saved["id"])
        self.assertEqual(updated["created_at"], saved["created_at"])
        self.assertGreaterEqual(updated["updated_at"], saved["updated_at"])
        self.assertEqual(load_draft(saved["id"], self.store_dir)["content"], "Revised copy.")

        markdown = export_markdown(updated)
        for expected in (
            "# Local first AI", "Revised copy.", "post-17", "note-2", "user_import",
            "needs_review", "template-v1", "Not used", updated["created_at"], updated["updated_at"],
        ):
            self.assertIn(expected, markdown)

    def test_lists_newest_first_and_refuses_overwrite_or_traversal(self):
        first = save_draft(self.record, self.store_dir)
        second = save_draft({**self.record, "title": "Second"}, self.store_dir)
        self.assertEqual([item["id"] for item in list_drafts(self.store_dir)], [second["id"], first["id"]])

        with self.assertRaises(FileExistsError):
            save_draft({**first, "content": "clobbered"}, self.store_dir)
        with self.assertRaises(ValueError):
            load_draft("../outside", self.store_dir)
        with self.assertRaises(ValueError):
            save_draft({**self.record, "id": "../../outside"}, self.store_dir)

    def test_corrupt_json_is_reported_instead_of_silently_ignored(self):
        self.store_dir.mkdir(parents=True)
        (self.store_dir / ("a" * 32 + ".json")).write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            list_drafts(self.store_dir)

    def test_failed_new_write_leaves_no_corrupt_draft(self):
        identifier = "f" * 32
        real_fdopen = os.fdopen

        class PartialFailingStream:
            def __init__(self, fd, *args, **kwargs):
                self.stream = real_fdopen(fd, *args, **kwargs)

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, payload):
                self.stream.write(payload[:8])
                raise OSError("simulated disk failure after partial write")

            def flush(self):
                return self.stream.flush()

            def fileno(self):
                return self.stream.fileno()

        with patch("socialrum.store.os.fdopen", side_effect=PartialFailingStream):
            with self.assertRaises(OSError):
                save_draft({**self.record, "id": identifier}, self.store_dir)
        self.assertFalse((self.store_dir / f"{identifier}.json").exists())
        self.assertEqual(list_drafts(self.store_dir), [])
        self.assertFalse(any(path.suffix == ".tmp" for path in self.store_dir.iterdir()))

    def test_optimistic_revision_rejects_stale_and_concurrent_edits(self):
        saved = save_draft(self.record, self.store_dir)
        original_revision = saved["updated_at"]
        first = {**saved, "content": "first writer"}
        updated = save_draft(first, self.store_dir, overwrite=True, expected_updated_at=original_revision)

        with self.assertRaises(DraftConflictError):
            save_draft({**saved, "content": "stale writer"}, self.store_dir,
                       overwrite=True, expected_updated_at=original_revision)
        self.assertEqual(load_draft(saved["id"], self.store_dir)["content"], "first writer")

        revision = updated["updated_at"]
        candidates = [
            {**updated, "content": "concurrent A"},
            {**updated, "content": "concurrent B"},
        ]
        def attempt(candidate):
            try:
                return save_draft(candidate, self.store_dir, overwrite=True, expected_updated_at=revision)
            except DraftConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, candidates))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertIn(load_draft(saved["id"], self.store_dir)["content"], {"concurrent A", "concurrent B"})


if __name__ == "__main__":
    unittest.main()
