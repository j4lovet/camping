"""Offline tests for editable A-site choices and ordered single-site selection."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

import camping_watch as watch
import configure
import test_camping_watch as fixtures


class SiteSettingsTests(unittest.TestCase):
    def test_three_stage_menu_and_priority_expansion(self):
        settings = copy.deepcopy(fixtures.SETTINGS)
        with patch.object(configure, 'save'), patch.object(configure, 'ask', side_effect=['4', 'A142 A135', 'A130~A145']):
            configure.edit_sites(settings)
        self.assertEqual(settings['site'], ['A142', 'A135'])
        self.assertEqual(settings['site_ranges'], ['A130~A145'])
        self.assertTrue(settings['fallback_all_sites'])
        priority = watch.target_sites(settings)
        self.assertEqual(priority[:3], ['A142', 'A135', 'A130'])
        self.assertLess(priority.index('A145'), priority.index('A101'))
        original = copy.deepcopy(settings)
        with patch.object(configure, 'save') as save, patch.object(configure, 'ask', side_effect=['4', 'A135', 'A153~A154']):
            configure.edit_sites(settings)
        save.assert_not_called()
        self.assertEqual(settings, original)

    def test_accepts_all_single_or_multiple_a_sites_only(self):
        for site in ('A*', 'B*', 'AB*', 'B201', 'B246', 'A101', 'A134', 'A153', ['B220', 'A135'], ['A142', 'A135']):
            watch.check_settings(dict(fixtures.SETTINGS, site=site))
        for site in ('B135', 'B200', 'B247', 'C301', 'A100', 'A154', '135', [], ['A*'], ['A135', 'B136'], [135], None):
            with self.subTest(site=site), self.assertRaises(ValueError):
                watch.check_settings(dict(fixtures.SETTINGS, site=site))

    def test_menu_saves_order_without_changing_schedule_or_people(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'camping_settings.json'
            original = dict(fixtures.SETTINGS, checkin_dates=['2026-12-04'], nights=2)
            settings = copy.deepcopy(original)
            path.write_text(json.dumps(settings), encoding='utf-8')
            with patch.object(configure, 'ROOT', root), patch.object(configure, 'SETTINGS', path):
                with patch.object(configure, 'ask', side_effect=['2', 'a142,135 A142 136']):
                    configure.edit_sites(settings)
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved['site'], ['A142', 'A135', 'A136'])
                self.assertEqual({k:v for k,v in saved.items() if k != 'site'}, {k:v for k,v in original.items() if k != 'site'})
                with patch.object(configure, 'ask', side_effect=['1']):
                    configure.edit_sites(settings)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['site'], 'A*')

    def test_single_site_and_invalid_menu_input(self):
        settings = copy.deepcopy(fixtures.SETTINGS)
        with patch.object(configure, 'save'), patch.object(configure, 'ask', side_effect=['2', '134']):
            configure.edit_sites(settings)
        self.assertEqual(settings['site'], 'A134')
        for value in ('135 B136', '154', '1', '', '135 bad'):
            original = copy.deepcopy(settings)
            with patch.object(configure, 'save') as save, patch.object(configure, 'ask', side_effect=['2', value]):
                configure.edit_sites(settings)
            self.assertEqual(settings, original)
            save.assert_not_called()

    def test_fallback_menu_preserves_priority_and_can_be_disabled(self):
        settings = copy.deepcopy(fixtures.SETTINGS)
        with patch.object(configure, 'save'), patch.object(configure, 'ask', side_effect=['3', '142 135']):
            configure.edit_sites(settings)
        self.assertEqual(settings['site'], ['A142', 'A135'])
        self.assertTrue(settings['fallback_all_sites'])
        self.assertEqual(watch.target_sites(settings)[:2], ['A142', 'A135'])
        self.assertEqual(len(watch.target_sites(settings)), len(watch.SITE_NUMBERS))
        with patch.object(configure, 'save'), patch.object(configure, 'ask', side_effect=['2', '142']):
            configure.edit_sites(settings)
        self.assertFalse(settings['fallback_all_sites'])
        self.assertEqual(watch.target_sites(settings), ['A142'])


class SiteBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.page = self.browser.new_page()
        self.path = Path(self.tmp.name) / 'state.json'
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.page.close)
        self.watcher = fixtures.BrowserTests.fixture(self, outcome='captcha')
        self.page.locator('.reserv_area').evaluate('''el => el.insertAdjacentHTML('beforeend', `
          <input type="checkbox" id="roomcode92"><label for="roomcode92" onclick="document.querySelector('#rKey_92').checked=true">A142</label>
          <ul><li><div class="intro_tit"><big>A142</big></div><div><input type="checkbox" id="rKey_92" name="rkey[]"></div>
          <div class="mens"><input type="text" value="2"><span>2명</span></div></li></ul>`)''')

    def test_user_priority_overrides_screen_order(self):
        self.watcher.settings = dict(fixtures.SETTINGS, site=['A142', 'A135'])
        self.assertEqual(self.watcher.available_site()[:3], ('A142', 'rKey_92', 'roomcode92'))

    def test_sold_out_preferred_site_falls_back_only_within_list(self):
        self.watcher.settings = dict(fixtures.SETTINGS, site=['A142', 'A135'])
        self.page.locator('#roomcode92').evaluate('el => el.disabled=true')
        self.assertEqual(self.watcher.available_site()[0], 'A135')
        self.watcher.settings['site'] = 'A142'
        self.assertIsNone(self.watcher.available_site())

    def test_other_site_is_used_only_when_fallback_enabled_and_preferred_is_sold_out(self):
        self.watcher.settings = dict(fixtures.SETTINGS, site='A142', fallback_all_sites=True)
        self.assertEqual(self.watcher.available_site()[0], 'A142')
        self.page.locator('#roomcode92').evaluate('el => el.disabled=true')
        self.assertEqual(self.watcher.available_site()[0], 'A135')

    def test_range_site_beats_remaining_zone_when_specific_preference_is_sold_out(self):
        self.watcher.settings = dict(fixtures.SETTINGS, site='A135', site_ranges=['A140~A145'], fallback_all_sites=True)
        self.assertEqual(self.watcher.available_site()[0], 'A135')
        preferred = self.watcher.available_site()
        self.page.locator(f'#{preferred[2]}').evaluate('el => el.disabled=true')
        self.assertEqual(self.watcher.available_site()[0], 'A142')

    @patch('camping_watch.core.notify')
    def test_priority_choice_records_and_notifies_only_one_actual_site(self, notify):
        self.watcher.settings = dict(fixtures.SETTINGS, site=['A142', 'A135'])
        self.assertTrue(self.watcher.attempt(fixtures.DAY))
        self.assertEqual(self.page.locator('input[name="rkey[]"]:checked').count(), 1)
        self.assertEqual(self.watcher.state['active']['site'], 'A142')
        self.assertIn('A142', notify.call_args.args[1])
        self.assertNotIn('A135', notify.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
