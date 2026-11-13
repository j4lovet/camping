"""Offline date filters, menu persistence and public date-picker behavior."""
import json
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

import camping_common as core
import camping_watch as watch
import configure
from camping_reminder import reminder_text

SETTINGS = dict(json.loads((Path(__file__).parent / 'camping_settings.json').read_text(encoding='utf-8')),
                nights=1, checkin_dates=[])


class DateRulesTests(unittest.TestCase):
    def test_explicit_dates_override_weekdays_deduplicate_and_expire(self):
        settings = dict(SETTINGS, checkin_dates=['2026-11-20', '2026-11-06', '2026-11-20', '2026-10-30'])
        self.assertEqual(watch.target_dates(settings, date(2026, 11, 1)), ['2026-11-06', '2026-11-20'])
        self.assertEqual(watch.target_dates(settings, date(2026, 11, 21)), [])
        self.assertEqual(watch.target_dates(dict(settings, max_lookahead_days=3), date(2026, 11, 1)), [])

    def test_bad_date_inputs_are_rejected(self):
        for dates in ('2026-11-20', [None], ['2026-02-30'], ['20261120'], ['2026-11-20T00:00']):
            with self.subTest(dates=dates), self.assertRaises(ValueError):
                watch.check_settings(dict(SETTINGS, checkin_dates=dates))

    def test_explicit_date_opening_and_two_night_reminder(self):
        settings = dict(SETTINGS, checkin_dates=['2026-12-04'], nights=2)
        self.assertEqual(watch.watch_days(settings, date(2026, 11, 4), True), ['2026-12-04'])
        now = datetime(2026, 11, 4, 9, 59, 55, tzinfo=core.KST)
        self.assertEqual(watch.fast_dates(settings, now), ['2026-12-04'])
        text = reminder_text(settings, now)
        self.assertIn('12/4(금)', text)
        self.assertIn('2박', text)
        self.assertIsNone(reminder_text(settings, now + timedelta(days=1)))

    def test_menu_changes_dates_and_nights_preserving_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'camping_settings.json'
            path.write_text(json.dumps(SETTINGS), encoding='utf-8')
            settings = dict(SETTINGS)
            with patch.object(configure, 'SETTINGS', path), patch.object(configure, 'ROOT', path.parent), \
                 patch.object(configure, 'ask', return_value='2027-01-08, 2027-01-15'):
                configure.edit_dates(settings)
            with patch.object(configure, 'SETTINGS', path), patch.object(configure, 'ROOT', path.parent), \
                 patch.object(configure, 'ask', return_value='2'):
                configure.edit_nights(settings)
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['checkin_dates'], ['2027-01-08', '2027-01-15'])
            self.assertEqual(saved['nights'], 2)
            for key in SETTINGS.keys() - {'checkin_dates', 'nights'}:
                self.assertEqual(saved[key], SETTINGS[key])
            with patch.object(configure, 'save'), patch.object(configure, 'ask', return_value='토'):
                configure.edit_days(settings)
            self.assertEqual(settings['checkin_dates'], [])


class StayBrowserTests(unittest.TestCase):
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
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.page.close)
        self.watcher = watch.CampingWatcher(self.page, dict(SETTINGS, nights=2), Path(self.tmp.name) / 'state.json', {})

    def fixture(self, day='2026-11-28', checkout_enabled=True, update_dates=True):
        start = date.fromisoformat(day)
        checkout = start + timedelta(days=2)
        stamp = lambda d: int(datetime.combine(d, time(), tzinfo=core.KST).timestamp() * 1000)
        html = '''<meta charset="utf-8"><div class="reserv_area">
          <div class="period_calendar"><button onclick="document.querySelector('#datepicker-checkInOutTerm').style.display='block'">날짜변경</button></div>
          <div class="chkin_txt"><strong>START_DATE</strong></div>
          <div class="chkout_txt"><strong>ONE_NIGHT_END</strong></div><div class="period_txt">1박</div>
          <input type="checkbox" name="rkey[]" id="rKey_35"><label for="rKey_35">선택</label>
          <button id="NextEntry" onclick="window.nextClicks++">다음단계</button></div>
          <div id="datepicker-checkInOutTerm" style="display:none">
          <table><tr><td class="datepicker__month-day--visibleMonth datepicker__month-day--valid" time="START_STAMP" onclick="window.rangeClicks.push('start')">입실</td>
          <td class="datepicker__month-day--visibleMonth END_CLASS" time="END_STAMP" onclick="finishRange()">퇴실</td></tr></table>
          <button id="close-checkInOutTerm" onclick="this.parentElement.style.display='none'">닫기</button></div>
          <script>window.nextClicks=0;window.rangeClicks=[];
          function finishRange(){window.rangeClicks.push('end');
            document.querySelector('#datepicker-checkInOutTerm').style.display='none';
            document.querySelector('.period_txt').textContent='2박';
            UPDATE_DATES
            document.querySelector('#rKey_35').disabled=true;
          }</script>'''
        html = (html.replace('START_DATE', start.strftime('%Y/%m/%d'))
                .replace('ONE_NIGHT_END', (start + timedelta(days=1)).strftime('%Y/%m/%d'))
                .replace('START_STAMP', str(stamp(start))).replace('END_STAMP', str(stamp(checkout)))
                .replace('END_CLASS', 'datepicker__month-day--valid' if checkout_enabled else 'datepicker__month-day--invalid')
                .replace('UPDATE_DATES', "document.querySelector('.chkout_txt strong').textContent='" + checkout.strftime('%Y/%m/%d') + "';" if update_dates else ''))
        self.page.set_content(html)
        return day

    def test_two_nights_clicks_range_and_reloads_full_stay_inventory(self):
        for day in ('2026-11-28', '2026-11-30', '2026-12-31'):
            with self.subTest(day=day):
                self.fixture(day)
                self.assertTrue(self.watcher.set_stay(day))
                self.watcher.verify_dates(day)
                self.assertEqual(self.page.evaluate('window.rangeClicks'), ['start', 'end'])
                self.assertTrue(self.page.locator('#rKey_35').is_disabled())
                self.assertEqual(self.page.locator('input[name="rkey[]"]:checked').count(), 0)
                self.assertEqual(self.page.evaluate('window.nextClicks'), 0)

    def test_unavailable_checkout_is_never_forced(self):
        day = self.fixture(checkout_enabled=False)
        self.assertFalse(self.watcher.set_stay(day))
        self.assertEqual(self.page.evaluate('window.rangeClicks'), ['start'])
        self.assertEqual(self.page.evaluate('window.nextClicks'), 0)

    def test_two_night_label_with_one_night_checkout_blocks(self):
        day = self.fixture(update_dates=False)
        with self.assertRaises(RuntimeError):
            self.watcher.set_stay(day)
        self.assertEqual(self.page.evaluate('window.nextClicks'), 0)

    @patch('camping_watch.core.notify')
    def test_two_night_attempt_records_and_validates_actual_stay(self, notify):
        import test_camping_watch as legacy
        # Reuse the full selection fixture, changing its confirmed stay to two nights.
        self.path = Path(self.tmp.name) / 'attempt.json'
        watcher = legacy.BrowserTests.fixture(self, checkout='2026/11/30')
        watcher.settings = dict(SETTINGS, nights=2, checkin_dates=['2026-11-28'])
        self.page.locator('.period_txt').evaluate("el => el.textContent='2박'")
        self.page.evaluate("() => { const oldNext=window.next; window.next=()=>{oldNext(); document.querySelector('.app_date').textContent='2026년 11월 28일 ~ 2026년 11월 30일 (2박)';}; }")
        self.assertTrue(watcher.attempt('2026-11-28'))
        self.assertEqual(watcher.state['active']['nights'], 2)
        self.assertEqual(watcher.state['active']['checkout'], '2026-11-30')
        self.assertEqual(watcher.state['phase'], 'HELD')
        self.assertIn('2박', notify.call_args.args[1])
        self.page.locator('.app_date').evaluate("el => el.textContent='2026년 11월 28일 ~ 2026년 11월 29일 (1박)'")
        self.assertEqual(watcher.handoff_phase('2026-11-28'), 'REVIEW_REQUIRED')
        self.assertEqual(self.page.evaluate('window.finalClicks'), 0)


if __name__ == '__main__':
    unittest.main()
