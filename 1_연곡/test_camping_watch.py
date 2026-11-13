import json
import tempfile
import unittest
import unittest.mock
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from camping_common import KST
from camping_remote import from_owner, handle, parse_command, status_text
from camping_reminder import reminder_text
from camping_watch import (CampingWatcher, entry_url, fast_dates, open_date, open_rule_verified,
                           target_dates, wait_seconds, watch_days, window_closed, rush_dates)

FILE_SETTINGS = json.loads((Path(__file__).parent / "camping_settings.json").read_text())
SETTINGS = dict(FILE_SETTINGS, site="A135", weekdays=[5], nights=1, checkin_dates=[], max_lookahead_days=45,
                fallback_all_sites=False)
SETTINGS.pop('site_ranges', None)
ALL_DAYS = dict(SETTINGS, weekdays=list(range(7)), max_lookahead_days=35)
DAY = "2026-11-28"


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


class RulesTests(unittest.TestCase):
    def test_saturdays_rolling_window_and_expiration(self):
        self.assertEqual(target_dates(SETTINGS, date(2026, 10, 30)),
                         ["2026-10-31", "2026-11-07", "2026-11-14", "2026-11-21", "2026-11-28", "2026-12-05", "2026-12-12"])
        self.assertEqual(target_dates(SETTINGS, date(2026, 11, 28))[0], "2026-11-28")
        self.assertEqual(target_dates(SETTINGS, date(2027, 6, 1))[0], "2027-06-05")  # no fixed months

    def test_saturday_weekday_filter(self):
        self.assertTrue(all(date.fromisoformat(d).weekday() == 5 for d in target_dates(SETTINGS, date(2026, 11, 2))))

    def test_all_weekdays_setting_and_reminder_stays_saturday(self):
        days = target_dates(ALL_DAYS, date(2026, 11, 2))
        self.assertEqual(len(days), ALL_DAYS["max_lookahead_days"] + 1)
        self.assertIn("2026-11-03", days)  # a Tuesday
        self.assertIsNone(reminder_text(ALL_DAYS, at("2026-11-02 09:30")))  # Dec 2 (Wed) opens: no reminder
        self.assertIn("12/5(토)", reminder_text(ALL_DAYS, at("2026-11-05 09:30")))

    def test_unsafe_settings(self):
        for key, value in (("nights", 3), ("nights", True), ("nights", 1.0), ("site", "B134"), ("weekdays", []), ("weekdays", [7]), ("weekdays", "5"), ("reminder_weekdays", [5.5]), ("poll_seconds", 1),
                           ("stop_at_customer_form", False), ("max_lookahead_days", 100),
                           ("fast_poll_seconds", 0.2), ("burst_poll_seconds", 0.01),
                           ("fast_poll_seconds", 15), ("burst_before_seconds", 11),
                           ("burst_after_seconds", 61), ("burst_after_seconds", 60.1),
                           ("fast_before_seconds", 1), ("open_time", "아침")):
            with self.assertRaises(ValueError, msg=key):
                target_dates(dict(SETTINGS, **{key: value}))

    def test_open_date_is_same_day_previous_month(self):
        self.assertEqual(open_date("2026-12-05"), date(2026, 11, 5))
        self.assertEqual(open_date("2027-01-02"), date(2026, 12, 2))
        self.assertTrue(open_rule_verified("2026-12-05"))
        self.assertEqual(open_date("2026-12-31"), date(2026, 12, 1))  # Nov has no 31st: seen on the site
        self.assertTrue(open_rule_verified("2026-12-31"))
        self.assertEqual(open_date("2026-10-31"), date(2026, 10, 1))
        self.assertEqual(open_date("2025-03-29"), date(2025, 3, 1))  # short February: a guess
        self.assertFalse(open_rule_verified("2025-03-29"))

    def test_fast_window_only_for_dates_opening_now(self):
        self.assertEqual(fast_dates(SETTINGS, at("2026-11-05 09:59:55")), ["2026-12-05"])
        self.assertEqual(fast_dates(SETTINGS, at("2026-11-05 10:01:00")), ["2026-12-05"])
        for when in ("2026-11-05 09:59:49", "2026-11-05 10:01:01", "2026-11-06 10:00:00", "2026-11-04 10:00:00"):
            self.assertEqual(fast_dates(SETTINGS, at(when)), [], when)

    def test_burst_is_short_and_inside_fast_window(self):
        days = ["2026-12-05"]
        interval = lambda when: wait_seconds(SETTINGS, days, at(when))
        self.assertEqual(interval("2026-11-05 09:59:57"), 1)
        self.assertEqual(interval("2026-11-05 09:59:58"), 0.1)
        self.assertEqual(interval("2026-11-05 10:00:20"), 0.1)
        self.assertEqual(interval("2026-11-05 10:00:20.001"), 1)

    def test_burst_keeps_sold_out_dates_until_requested_end(self):
        day = "2026-12-05"
        for result in ("NOT_OPEN", "ZONE_SOLD_OUT", "SITE_SOLD_OUT", "STAY_UNAVAILABLE"):
            for when in ("2026-11-05 09:59:58", "2026-11-05 10:00:03", "2026-11-05 10:00:20"):
                now = at(when)
                rush = rush_dates(SETTINGS, now, {day: result})
                self.assertEqual(rush, [day], (when, result))
                self.assertEqual(wait_seconds(SETTINGS, rush, now), 0.1)
        self.assertEqual(rush_dates(SETTINGS, at("2026-11-05 10:00:20.001"), {day: "ZONE_SOLD_OUT"}), [])
        self.assertEqual(rush_dates(SETTINGS, at("2026-11-05 09:59:57.999"), {day: "ZONE_SOLD_OUT"}), [])
        self.assertEqual(rush_dates(SETTINGS, at("2026-11-06 10:00:00"), {}), [])

    def test_first_cycle_inside_burst_uses_short_wait(self):
        now = at("2026-11-05 10:00:10")
        rush = rush_dates(SETTINGS, now, {})
        self.assertEqual(rush, ["2026-12-05"])
        self.assertEqual(wait_seconds(SETTINGS, rush, now), 0.1)

    def test_normal_wait_never_sleeps_past_fast_start(self):
        self.assertEqual(wait_seconds(SETTINGS, [], at("2026-11-05 09:00:00")), 15)
        self.assertEqual(wait_seconds(SETTINGS, [], at("2026-11-05 09:59:45")), 5)
        self.assertEqual(wait_seconds(SETTINGS, [], at("2026-11-05 09:59:59.5")), 15)  # window already started
        self.assertEqual(wait_seconds(SETTINGS, [], at("2026-11-08 12:00:00")), 15)

    def test_entry_url_follows_current_month(self):
        self.assertTrue(entry_url(date(2027, 3, 9)).endswith("tmonth=202703"))


class OpenLogTests(unittest.TestCase):
    def test_logs_first_sight_changes_and_rush_checks_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            watcher = CampingWatcher(unittest.mock.MagicMock(), SETTINGS, Path(tmp) / "state.json", {})
            for result, rush in (("NOT_OPEN", False), ("NOT_OPEN", False), ("NOT_OPEN", True), ("AVAILABLE", True), ("SITE_SOLD_OUT", True)):
                watcher.record("2026-12-05", result, 123.4, rush)
                watcher.state["dates"]["2026-12-05"] = result
            lines = [json.loads(x) for x in (Path(tmp) / "open_log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["result"] for x in lines], ["NOT_OPEN", "NOT_OPEN", "AVAILABLE", "SITE_SOLD_OUT"])
        self.assertEqual(lines[0]["previous"], None)
        self.assertEqual(lines[2]["previous"], "NOT_OPEN")
        self.assertTrue(lines[0]["t"].endswith("+09:00"))
        self.assertEqual(lines[0]["inspect_ms"], 123)


class WindowClosedTests(unittest.TestCase):
    def test_closed_window_is_a_quiet_exit_but_site_errors_are_not(self):
        class TargetClosedError(Exception):
            pass
        open_page, closed_page = unittest.mock.Mock(), unittest.mock.Mock()
        open_page.is_closed.return_value, closed_page.is_closed.return_value = False, True
        self.assertTrue(window_closed(open_page, TargetClosedError()))
        self.assertTrue(window_closed(closed_page, RuntimeError("x")))
        self.assertFalse(window_closed(open_page, PermissionError("접근 제한")))
        self.assertFalse(window_closed(open_page, TimeoutError()))


class RemoteTests(unittest.TestCase):
    def run_action(self, action, pids=(), state=None):
        calls = []
        reply = handle(action, pids=lambda: list(pids), state=lambda: state or {},
                       start=lambda open_only: calls.append(("start", open_only)),
                       stop=lambda p: calls.append(("stop", list(p))))
        return reply, calls

    def test_parse_command(self):
        for text, action in (("/start", "start"), ("시작", "start"), ("/start@my_bot", "start"), ("오픈", "open"),
                             ("/STOP", "stop"), ("상태", "status"), ("hello", None), ("", None), (None, None)):
            self.assertEqual(parse_command(text), action, text)

    def test_start_open_and_stop(self):
        self.assertEqual(self.run_action("start")[1], [("start", False)])
        self.assertEqual(self.run_action("open")[1], [("start", True)])
        self.assertEqual(self.run_action("stop", pids=[11, 12])[1], [("stop", [11, 12])])

    def test_no_double_start_and_nothing_while_reservation_active(self):
        self.assertEqual(self.run_action("start", pids=[5])[1], [])
        active = {"active": {"site": "A135", "checkin": "2026-12-05"}}
        reply, calls = self.run_action("stop", pids=[5], state=active)
        self.assertEqual(calls, []); self.assertIn("예약 진행 중", reply)
        reply, calls = self.run_action("start", state=active)
        self.assertEqual(calls, [("start", False)])  # stale JSON cannot block a new process

    def test_status_text(self):
        text = status_text([7], {"phase": "MONITORING", "updated_at": "2026-11-05T09:51:00+09:00",
                                 "dates": {"2026-12-05": "NOT_OPEN"}})
        self.assertIn("실행 중", text); self.assertIn("12-05 NOT_OPEN", text)
        self.assertIn("꺼짐", status_text([], {}))
        stale = status_text([], {"active": {"site": "A107", "checkin": DAY}})
        self.assertIn("이전 실행", stale)
        self.assertIn("실제 예약 접수 여부", stale)

    def test_only_own_private_chat_is_obeyed(self):
        own = {"message": {"chat": {"id": 42, "type": "private"}, "from": {"is_bot": False}, "text": "시작"}}
        self.assertTrue(from_owner(own, "42"))
        for change in ({"chat": {"id": 43, "type": "private"}}, {"chat": {"id": 42, "type": "group"}},
                       {"from": {"is_bot": True}}):
            self.assertFalse(from_owner({"message": {**own["message"], **change}}, "42"))
        self.assertFalse(from_owner({}, "42"))


class OpenOnlyTests(unittest.TestCase):
    def test_open_only_watches_just_todays_opening_dates(self):
        all_days = ALL_DAYS
        self.assertEqual(watch_days(all_days, date(2026, 11, 2), open_only=True), ["2026-12-02"])
        self.assertIn("2026-11-11", watch_days(all_days, date(2026, 11, 2)))
        self.assertEqual(watch_days(SETTINGS, date(2026, 11, 2), open_only=True), [])  # Saturdays only: none opens Monday


class ReminderTests(unittest.TestCase):
    def test_reminds_only_on_saturday_opening_day_before_open(self):
        text = reminder_text(SETTINGS, at("2026-11-05 09:30"))
        self.assertIn("12/5(토)", text)
        self.assertIn("10:00", text)
        self.assertIn("09:50", text)
        self.assertNotIn("말일", text)
        for when in ("2026-11-04 09:30", "2026-11-06 09:30", "2026-11-05 10:00", "2026-11-05 11:00"):
            self.assertIsNone(reminder_text(SETTINGS, at(when)), when)

    def test_every_remaining_opening_day(self):
        for day, saturday in (("11-12", "12/12"), ("11-19", "12/19"), ("11-26", "12/26")):
            self.assertIn(saturday, reminder_text(SETTINGS, at(f"2026-{day} 09:30")))

    def test_month_end_rule_is_flagged_as_unverified(self):
        self.assertIn("말일", reminder_text(SETTINGS, at("2025-03-01 09:30")))
        self.assertNotIn("말일", reminder_text(SETTINGS, at("2026-10-01 09:30")))  # 10/31 opens 10/1, seen pattern


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.page=self.browser.new_page()
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.page.close)
        self.path=Path(self.tmp.name)/"state.json"

    def fixture(self, outcome="held", disabled=False, checkout="2026/11/29", room="A135", extra=False, minus=True):
        html='''<meta charset="utf-8"><div class="reserv_area">
<div class="chkin_txt"><strong>2026/11/28</strong></div><div class="chkout_txt"><strong>CHECKOUT</strong></div>
<div class="period_txt">1박</div><input type="checkbox" id="roomcode35" DISABLED>
<label for="roomcode35" onclick="document.querySelector('#rKey_35').checked=true">A135</label>
<ul><li><div class="intro_tit"><big>ROOM</big></div><div><input type="checkbox" id="rKey_35" name="rkey[]" DISABLED></div>
<div class="mens">MINUS<input type="text" id="men" value="6"><span>6명</span></div></li></ul>
EXTRA<button id="NextEntry" onclick="next()">다음단계</button></div>
<script>window.nextClicks=0;window.finalClicks=0;
function next(){window.nextClicks++;if('OUTCOME'==='captcha'){
 document.body.insertAdjacentHTML('beforeend','<input id="CAPTCHA_TEXT"><button id="captcha-confirm" onclick="window.captchaSubmit=true">확인</button>');
}else if('OUTCOME'==='held'||'OUTCOME'==='form'){
 document.body.innerHTML='<div class="reserv_reserv"><ul class="ResvPurList"><li><div class="app_title"><h4>A135</h4></div><div class="app_date">2026년 11월 28일 ~ 2026년 11월 29일 (1박)</div></li></ul><input id="appName"><input name="appMobile[]"><button id="NextEntry" onclick="window.finalClicks++">다음단계</button></div>';
 if('OUTCOME'==='held')document.body.insertAdjacentHTML('beforeend','<div class="PriorTimer"><span id="DzReserv_Counter">09:58</span></div>');
}}</script>'''.replace("CHECKOUT",checkout).replace("ROOM",room).replace("OUTCOME",outcome).replace("DISABLED","disabled" if disabled else "").replace("MINUS",'<button class="btn-minus" onclick="var i=document.querySelector(\'#men\'),n=Math.max(1,+i.value-1);i.value=n;document.querySelector(\'.mens span\').textContent=n+\'명\'"></button>' if minus else '').replace("EXTRA",'<input type="checkbox" name="rkey[]" id="rKey_34" checked>' if extra else '')
        self.page.set_content(html)
        watcher = CampingWatcher(self.page, SETTINGS, self.path, {})
        watcher.today = lambda: date(2026, 10, 30)
        return watcher

    @patch("camping_watch.core.notify")
    def test_hold_stops_before_personal_info_or_payment(self, notify):
        w=self.fixture()
        self.assertTrue(w.attempt(DAY));self.assertEqual(w.state["phase"],"HELD")
        self.assertEqual(self.page.locator('#appName').input_value(), '')
        self.assertEqual(self.page.locator('input[name="appMobile[]"]').input_value(), '')
        self.assertEqual(self.page.evaluate('window.finalClicks'),0)
        self.assertEqual(json.loads(self.path.read_text())["active"]["site"],"A135")
        notify.assert_called_once()

    @patch("camping_watch.core.notify")
    def test_captcha_requires_human_not_claimed_as_hold(self, notify):
        w=self.fixture(outcome="captcha")
        self.assertTrue(w.attempt(DAY));self.assertEqual(w.state["phase"],"CAPTCHA_REQUIRED")
        self.assertEqual(self.page.locator('#CAPTCHA_TEXT').input_value(),'')
        self.assertFalse(self.page.evaluate('Boolean(window.captchaSubmit)'))
        self.assertIn('아직 임시점유 확인 전',notify.call_args.args[1])

    @patch("camping_watch.core.notify")
    def test_form_without_timer_not_claimed_as_hold(self, notify):
        w=self.fixture(outcome="form");self.assertTrue(w.attempt(DAY))
        self.assertEqual(w.state["phase"],"FORM_READY")

    def test_disabled_site_not_clicked(self):
        w=self.fixture(disabled=True);self.assertFalse(w.attempt(DAY))
        self.assertEqual(self.page.evaluate('window.nextClicks'),0)

    @patch("camping_watch.core.notify")
    def test_all_a_sites_skip_sold_out_and_other_zones(self, notify):
        w = self.fixture(outcome="captcha", disabled=True)
        w.settings = dict(SETTINGS, site="A*")
        self.page.locator('.reserv_area').evaluate('''el => el.insertAdjacentHTML('beforeend', `
            <input type="checkbox" id="roomcode81"><label for="roomcode81" onclick="document.querySelector('#rKey_81').checked=true">B101</label>
            <ul><li><div class="intro_tit"><big>B101</big></div><div><input id="rKey_81" name="rkey[]" type="checkbox"></div></li></ul>
            <input type="checkbox" id="roomcode92"><label for="roomcode92" onclick="document.querySelector('#rKey_92').checked=true">A142</label>
            <ul><li><div class="intro_tit"><big>A142</big></div><div><input id="rKey_92" name="rkey[]" type="checkbox"></div>
            <div class="mens"><input type="text" value="2"><span>2명</span></div></li></ul>` )''')
        self.assertEqual(w.available_site()[:3], ("A142", "rKey_92", "roomcode92"))
        self.assertTrue(w.attempt(DAY))
        self.assertEqual(w.state["active"]["site"], "A142")
        self.assertEqual(self.page.locator('input[name="rkey[]"]:checked').count(), 1)
        self.assertIn("A142", notify.call_args.args[1])
        self.assertNotIn("A135", notify.call_args.args[1])

    def test_all_a_sites_sold_out_or_wrong_zone(self):
        w = self.fixture(disabled=True)
        w.settings = dict(SETTINGS, site="A*")
        self.page.locator('.reserv_area').evaluate('el => el.insertAdjacentHTML("beforeend", \'<div class="_foot"><div class="charge"><input type="checkbox" name="rkey[]" disabled></div></div>\')')
        self.assertIsNone(w.available_site())
        self.assertFalse(w.attempt(DAY))
        self.page.locator('.intro_tit big').evaluate("el => el.textContent = 'B135'")
        with self.assertRaises(RuntimeError):
            w.attempt(DAY)
        self.assertEqual(self.page.evaluate('window.nextClicks'), 0)

    def test_handoff_requires_actual_selected_site(self):
        w = self.fixture()
        w.state["active"] = {"site": "A142"}
        self.page.evaluate("next()")
        self.assertEqual(w.handoff_phase(DAY), "REVIEW_REQUIRED")
        self.page.locator('.app_title h4').evaluate("el => el.textContent = 'A142'")
        self.assertEqual(w.handoff_phase(DAY), "HELD")

    def test_wrong_stay_room_and_multiple_sites_block(self):
        for kwargs in ({"checkout":"2026/11/30"},{"room":"A134"},{"extra":True}):
            w=self.fixture(**kwargs)
            with self.assertRaises(RuntimeError):w.attempt(DAY)
            self.assertEqual(self.page.evaluate('window.nextClicks'),0)

    @patch("camping_watch.core.notify")
    def test_headcount_set_to_two_before_next_step(self, notify):
        w=self.fixture(outcome="unknown")
        with patch.object(self.page,'wait_for_timeout'):
            w.attempt(DAY)
        self.assertEqual(self.page.locator('#men').input_value(),"2")
        self.assertEqual(self.page.locator('.mens span').inner_text(),"2명")
        self.assertEqual(self.page.evaluate('window.nextClicks'),1)

    def test_headcount_not_confirmed_blocks_next_step(self):
        w=self.fixture(minus=False)
        with patch.object(self.page,'wait_for_timeout'):
            with self.assertRaises(Exception):w.attempt(DAY)
        self.assertEqual(self.page.evaluate('window.nextClicks'),0)
        self.assertFalse(self.path.exists())

    def test_timeout_is_retried_but_restriction_is_not(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        w=self.fixture()
        with patch.object(w,"inspect",side_effect=[PlaywrightTimeout("slow"),"NOT_OPEN"]) as inspect, \
             patch.object(w,"reload_entry") as reload:
            self.assertEqual(w.inspect_with_retry(DAY),"NOT_OPEN")
        self.assertEqual((inspect.call_count,reload.call_count),(2,1))
        with patch.object(w,"inspect",side_effect=PlaywrightTimeout("slow")), patch.object(w,"reload_entry"):
            with self.assertRaises(PlaywrightTimeout):w.inspect_with_retry(DAY)  # gives up after 3 tries
        with patch.object(w,"inspect",side_effect=PermissionError("restricted")) as inspect:
            with self.assertRaises(PermissionError):w.inspect_with_retry(DAY)
        self.assertEqual(inspect.call_count,1)
        w.state["active"]={"site":"A135"}
        with patch.object(w,"inspect",side_effect=PlaywrightTimeout("slow")) as inspect:
            with self.assertRaises(PlaywrightTimeout):w.inspect_with_retry(DAY)
        self.assertEqual(inspect.call_count,1)  # never retry once an attempt is pending

    def test_weekday_blocked(self):
        w=self.fixture()
        with self.assertRaises(RuntimeError):w.attempt("2026-11-27")
        self.assertEqual(self.page.evaluate('window.nextClicks'),0)

    @patch("camping_watch.core.notify")
    def test_unknown_result_pauses_with_pending_state(self, notify):
        w=self.fixture(outcome="unknown")
        with patch.object(self.page,'wait_for_timeout'):
            self.assertTrue(w.attempt(DAY))
        self.assertEqual(w.state["phase"],"REVIEW_REQUIRED")
        self.assertIsNotNone(w.state["active"])

    def test_calendar_refresh_uses_month_button(self):
        w=self.fixture()
        self.page.set_content('<div class="reserv_calendar"><button class="prevMonth" onclick="window.switched=true">이전달</button></div>')
        w.refresh_calendar_ui()
        self.assertTrue(self.page.evaluate('window.switched'))


if __name__ == "__main__":
    unittest.main()
