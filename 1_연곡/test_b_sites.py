"""B-zone settings, public zone navigation and manual handoff without reservations."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
import camping_watch as watch
import configure
import test_camping_watch as fixtures


class BSettingsTests(unittest.TestCase):
    def test_b_ranges_and_fallback_stay_in_b_zone(self):
        settings = dict(fixtures.SETTINGS, site=['B220', 'B222'], site_ranges=['B210~B225'], fallback_all_sites=True)
        watch.check_settings(settings)
        priority = watch.target_sites(settings)
        self.assertEqual(priority[:3], ['B220', 'B222', 'B210'])
        self.assertEqual(len(priority), 46)
        self.assertTrue(all(name.startswith('B') for name in priority))
        self.assertEqual(watch.target_zones(settings), ['B'])

    def test_b_and_mixed_menu_choices(self):
        cases = [(['5'], 'B*'), (['6'], 'AB*'), (['2', '220 B221'], ['B220', 'B221']),
                 (['4', 'B220 A135', 'B210~B230 A130~A145'], ['B220', 'A135'])]
        for answers, expected in cases:
            with self.subTest(answers=answers):
                settings = copy.deepcopy(fixtures.SETTINGS)
                with patch.object(configure, 'save'), patch.object(configure, 'ask', side_effect=answers):
                    configure.edit_sites(settings)
                self.assertEqual(settings['site'], expected)
                watch.check_settings(settings)
                for key in ('checkin_dates', 'weekdays', 'nights', 'headcount'):
                    self.assertEqual(settings[key], fixtures.SETTINGS[key])

    def test_invalid_b_number_does_not_save(self):
        settings = copy.deepcopy(fixtures.SETTINGS)
        with patch.object(configure, 'save') as save, patch.object(configure, 'ask', side_effect=['2', 'B247']):
            configure.edit_sites(settings)
        save.assert_not_called()
        self.assertEqual(settings, fixtures.SETTINGS)


class BBrowserTests(unittest.TestCase):
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
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.w = fixtures.BrowserTests.fixture(self, outcome='captcha', room='B220')
        self.w.settings = dict(fixtures.SETTINGS, site='B220')

    @patch('camping_watch.core.notify')
    def test_b_handoff_uses_actual_control_id_and_never_finalizes(self, notify):
        self.assertTrue(self.w.attempt(fixtures.DAY))
        self.assertEqual(self.w.state['active']['site'], 'B220')
        self.assertEqual(self.w.state['phase'], 'CAPTCHA_REQUIRED')
        self.assertEqual(self.page.evaluate('window.nextClicks'), 1)
        self.assertEqual(self.page.evaluate('window.finalClicks'), 0)
        self.assertIn('B220', notify.call_args.args[1])

    def calendar_fixture(self):
        self.page.locator('.reserv_area').evaluate("el => el.style.display='none'")
        self.page.evaluate('''() => {
            window.zoneVisits = [];
            const back = document.createElement('button'); back.id = 'PrevStep';
            back.onclick = () => {
                document.querySelector('.reserv_area').style.display = 'none';
                document.querySelector('.reserv_calendar').style.display = 'block';
            };
            document.querySelector('.reserv_area').append(back);
            document.body.insertAdjacentHTML('afterbegin', `<div class="reserv_calendar">
                <div class="_title"><h2>2026년 11월</h2></div>
                <button value="1-26-11-28-1" onclick="openZone('A')">A</button>
                <button value="2-26-11-28-1" onclick="openZone('B')">B</button></div>`);
            window.openZone = zone => {
                window.zoneVisits.push(zone);
                document.querySelector('.reserv_calendar').style.display = 'none';
                document.querySelector('.reserv_area').style.display = 'block';
                document.querySelector('.intro_tit big').textContent = zone === 'A' ? 'A135' : 'B220';
            };
        }''')

    def test_inspect_clicks_b_calendar_button(self):
        self.calendar_fixture()
        self.assertEqual(self.w.inspect(fixtures.DAY), 'AVAILABLE')
        self.assertEqual(self.page.evaluate('window.zoneVisits'), ['B'])
        self.assertEqual(self.w.searched_site, 'B220')

    def test_mixed_priority_revisits_winning_zone_before_attempt(self):
        self.calendar_fixture()
        # A142 is unavailable. B220 must beat the available, lower-priority A135.
        self.w.settings = dict(fixtures.SETTINGS, site=['A142', 'B220', 'A135'])
        self.assertEqual(self.w.inspect(fixtures.DAY), 'AVAILABLE')
        self.assertEqual(self.page.evaluate('window.zoneVisits'), ['A', 'B'])
        self.assertEqual(self.w.searched_site, 'B220')
        self.assertEqual(self.w.available_site()[0], 'B220')

    def test_mixed_scan_returns_to_earlier_zone_if_its_candidate_wins(self):
        self.calendar_fixture()
        self.w.settings = dict(fixtures.SETTINGS, site=['A142', 'A135', 'B220'])
        self.assertEqual(self.w.inspect(fixtures.DAY), 'AVAILABLE')
        self.assertEqual(self.page.evaluate('window.zoneVisits'), ['A', 'B', 'A'])
        self.assertEqual(self.w.searched_site, 'A135')

    def test_a_only_configuration_does_not_select_b(self):
        self.w.settings = dict(fixtures.SETTINGS, site='A*')
        with self.assertRaises(RuntimeError):
            self.w.available_site()


if __name__ == '__main__':
    unittest.main()
