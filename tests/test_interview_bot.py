import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import interview_bot  # noqa: E402


def _config(project_path, interview_ids=None):
    cfg = {"project_path": str(project_path), "vars": {"product_dir": "product"}}
    if interview_ids is not None:
        cfg["interview_ids"] = interview_ids
    return cfg


def _state_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    return db


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"INTERVIEWER_BOT_TOKEN": "test-token"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_no_token_is_a_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(interview_bot.sync(tmp), 0)

    @patch("interview_bot.requests.get")
    def test_fetches_and_appends_to_shared_inbox(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            "ok": True,
            "result": [
                {"update_id": 5, "message": {"chat": {"id": 111}, "text": "hello", "date": 1000}},
                {"update_id": 6, "message": {"chat": {"id": 222}, "text": "hi there", "date": 1001}},
            ],
        })
        with tempfile.TemporaryDirectory() as tmp:
            n = interview_bot.sync(tmp)
            self.assertEqual(n, 2)
            inbox = (Path(tmp) / ".interview" / interview_bot.INBOX_NAME).read_text().splitlines()
            self.assertEqual(len(inbox), 2)
            offset = (Path(tmp) / ".interview" / interview_bot.OFFSET_NAME).read_text().strip()
            self.assertEqual(offset, "7")  # max update_id + 1

            # A second call with no new updates advances nothing further.
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": []})
            n2 = interview_bot.sync(tmp)
            self.assertEqual(n2, 0)
            args, kwargs = mock_get.call_args
            self.assertEqual(kwargs["params"]["offset"], 7)

    @patch("interview_bot.requests.get")
    def test_skips_updates_with_no_text(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            "ok": True,
            "result": [{"update_id": 1, "message": {"chat": {"id": 111}}}],  # no "text"
        })
        with tempfile.TemporaryDirectory() as tmp:
            interview_bot.sync(tmp)
            inbox_path = Path(tmp) / ".interview" / interview_bot.INBOX_NAME
            self.assertFalse(inbox_path.exists() and inbox_path.read_text().strip())


class DeliverTests(unittest.TestCase):
    def test_no_interview_ids_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _state_db()
            n = interview_bot.deliver(tmp, _config(tmp), db)
            self.assertEqual(n, 0)

    def test_delivers_only_matching_chat_ids_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            product.mkdir()
            (product / "FEEDBACK.md").write_text(
                "# Feedback\n\n## Inbox\n\n## Interview\n\n## Questions\n\n## Processed\n"
            )
            shared = root / ".interview"
            shared.mkdir()
            (shared / interview_bot.INBOX_NAME).write_text(
                json.dumps({"chat_id": "111", "text": "yes daily is fine", "date": 1700000000, "update_id": 1}) + "\n"
                + json.dumps({"chat_id": "999", "text": "not one of ours", "date": 1700000001, "update_id": 2}) + "\n"
            )

            db = _state_db()
            config = _config(root, interview_ids={"Kate": "111", "Alex": "222"})
            n = interview_bot.deliver(root, config, db)
            self.assertEqual(n, 1)

            feedback = (product / "FEEDBACK.md").read_text()
            self.assertIn("Kate replied", feedback)
            self.assertIn("yes daily is fine", feedback)
            self.assertNotIn("not one of ours", feedback)

            # Re-running with no new lines delivers nothing more (cursor advanced).
            n2 = interview_bot.deliver(root, config, db)
            self.assertEqual(n2, 0)

    def test_missing_feedback_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / ".interview"
            shared.mkdir()
            (shared / interview_bot.INBOX_NAME).write_text(
                json.dumps({"chat_id": "111", "text": "x", "date": 1700000000, "update_id": 1}) + "\n"
            )
            db = _state_db()
            config = _config(root, interview_ids={"Kate": "111"})
            n = interview_bot.deliver(root, config, db)
            self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
