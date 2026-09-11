import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import send_dm  # noqa: E402


class SendDmTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"INTERVIEWER_BOT_TOKEN": "test-token"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    @patch("send_dm.requests.post")
    def test_plain_text_message(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        send_dm.send(telegram_id="111111111", text="hello there")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.telegram.org/bottest-token/sendMessage")
        self.assertEqual(kwargs["json"]["chat_id"], "111111111")
        self.assertEqual(kwargs["json"]["text"], "hello there")
        self.assertNotIn("reply_markup", kwargs["json"])

    @patch("send_dm.requests.post")
    def test_message_with_rating_buttons(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        send_dm.send(telegram_id="222222222", text="how'd today go?", buttons="1,2,3,4,5")

        args, kwargs = mock_post.call_args
        markup = kwargs["json"]["reply_markup"]
        row = markup["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in row], ["1", "2", "3", "4", "5"])
        self.assertEqual([b["callback_data"] for b in row], ["1", "2", "3", "4", "5"])

    @patch("send_dm.requests.post")
    def test_raises_on_non_200(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=400, json=lambda: {"ok": False, "description": "chat not found"}
        )

        with self.assertRaises(send_dm.SendDmError) as ctx:
            send_dm.send(telegram_id="0", text="x")
        self.assertIn("chat not found", str(ctx.exception))

    def test_missing_token_raises(self):
        del os.environ["INTERVIEWER_BOT_TOKEN"]
        with self.assertRaises(send_dm.SendDmError):
            send_dm.send(telegram_id="111111111", text="x")

    def test_cli_missing_args_exits_nonzero(self):
        result = os.system(
            f"{sys.executable} {os.path.join(os.path.dirname(__file__), '..', 'scripts', 'send_dm.py')} "
            "> /dev/null 2>&1"
        )
        self.assertNotEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
