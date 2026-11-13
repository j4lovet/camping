"""Offline checks: foreground browser only, without opening native windows."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import camping_browser
import camping_watch as watch


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.page = Mock()
        settings = json.loads((watch.ROOT / 'camping_settings.json').read_text(encoding='utf-8'))
        args = (self.page, settings, Path(self.tmp.name) / 'state.json', {})
        if hasattr(watch, 'CampingWatcher'):
            self.w = watch.CampingWatcher(*args)
        else:
            self.w = watch.Watcher(*args, {})
        self.w.state.update(active={'site': 'A135'}, room='오토캠핑B 07')

    @patch('camping_watch.core.notify')
    @patch('camping_watch.focus_browser')
    def test_foreground_precedes_telegram(self, focus, notify):
        self.w.foreground_enabled = True
        notify.side_effect = lambda *_: self.assertEqual(focus.call_count, 1)
        self.w.announce('CAPTCHA_REQUIRED', '2026-12-25')
        focus.assert_called_once_with(self.page)
        if hasattr(watch, 'CampingWatcher'):
            self.w.announce('HELD', '2026-12-25')
            focus.assert_called_once()

    @patch('camping_watch.core.notify')
    @patch('camping_watch.focus_browser')
    def test_headless_does_not_request_foreground(self, focus, notify):
        self.w.announce('CAPTCHA_REQUIRED', '2026-12-25')
        focus.assert_not_called()
        notify.assert_called_once()

    @patch('camping_watch.core.notify')
    def test_focus_failure_preserves_handoff_and_notification(self, notify):
        self.w.foreground_enabled = True
        self.page.bring_to_front.side_effect = RuntimeError('closed')
        with self.assertLogs(level='ERROR'):
            self.w.announce('CAPTCHA_REQUIRED', '2026-12-25')
        self.assertEqual(self.w.state['phase'], 'CAPTCHA_REQUIRED')
        notify.assert_called_once()

    @patch('camping_browser._raise_window')
    def test_native_raise_restores_original_browser_title(self, native_raise):
        with patch.object(camping_browser.os, 'name', 'nt'):
            camping_browser.focus_browser(self.page)
        self.page.bring_to_front.assert_called_once()
        native_raise.assert_called_once()
        self.assertEqual(self.page.evaluate.call_count, 2)
        self.assertIn('document.title.slice', self.page.evaluate.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
