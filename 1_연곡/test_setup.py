import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import camping_common as core
from telegram_setup import private_chats, write_env


class SetupTests(unittest.TestCase):
    def test_missing_env_never_inherits_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_env(root / ".env", "123:demo", "456")
            self.assertEqual(core.load_telegram(root / "child" / ".env"), {})
            source = (Path(__file__).parent / "camping_watch.py").read_text()
            self.assertNotIn('ROOT.parent / ".env"', source)

    def test_setup_writes_private_local_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            write_env(path, "123:demo", "456")
            self.assertEqual(core.load_telegram(path), {"TELEGRAM_BOT_TOKEN": "123:demo", "TELEGRAM_CHAT_ID": "456"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                write_env(path, "123:demo\nINJECT=true", "456")

    def test_only_private_human_chats(self):
        updates = [
            {"message": {"chat": {"type": "private", "id": 456}, "from": {"is_bot": False}}},
            {"message": {"chat": {"type": "group", "id": -789}, "from": {"is_bot": False}}},
            {"message": {"chat": {"type": "private", "id": 123}, "from": {"is_bot": True}}},
            {"channel_post": {"chat": {"type": "channel", "id": -111}}},
        ]
        self.assertEqual(list(private_chats(updates)), ["456"])

    def test_request_error_does_not_leak_token_url(self):
        error = urllib.error.HTTPError("https://example.invalid/private-token", 409, "Conflict", {}, None)
        with patch("camping_common.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                core.telegram_call("123:demo", "getUpdates")
        self.assertEqual(str(caught.exception), "Telegram HTTP 409")
        self.assertNotIn("private-token", str(caught.exception))

    def test_no_credentials_no_network(self):
        with patch("camping_common.telegram_call") as request:
            core.notify({}, "local status")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
