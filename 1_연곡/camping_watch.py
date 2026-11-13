"""Yeongok A/B sites: use public UI and stop before manual customer entry."""
from __future__ import annotations

import argparse
import calendar as cal
import json
import logging
import re
import signal
from time import perf_counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

import camping_common as core
from camping_browser import focus_browser
from camping_priority import expand_ranges

ROOT = Path(__file__).resolve().parent
MAX_SPAN = 62  # never act on a date further ahead than the site could possibly have opened
SITE_NUMBERS = range(101, 154)  # A101~A153, verified in the public A-zone UI
B_SITE_NUMBERS = range(201, 247)
SITE_NUMBERS_BY_ZONE = {'A': SITE_NUMBERS, 'B': B_SITE_NUMBERS}
RANGE_LIMITS = {'A': (101, 153, 3), 'B': (201, 246, 3)}
ZONE_CALENDAR_IDS = {'A': '1', 'B': '2'}


def valid_site(site):
    return (isinstance(site, str) and re.fullmatch(r'[AB]\d{3}', site) is not None
            and int(site[1:]) in SITE_NUMBERS_BY_ZONE[site[0]])


def target_zones(settings):
    site = settings['site']
    if isinstance(site, str) and site in ('A*', 'B*', 'AB*'):
        return list(site[:-1])
    priority = [site] if isinstance(site, str) else list(site)
    priority.extend(expand_ranges(settings.get('site_ranges', []), RANGE_LIMITS))
    return list(dict.fromkeys(name[0] for name in priority))


def entry_url(today):
    return f"https://camping.gtdc.or.kr/pub/reserv.do?tmonth={today:%Y%m}"


def _number(settings, key, low, high):
    value = settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"{key} 값 확인 필요 ({low}~{high})")
    return value


def check_settings(settings):
    expand_ranges(settings.get('site_ranges', []), RANGE_LIMITS)
    if type(settings.get('fallback_all_sites', False)) is not bool:
        raise ValueError('fallback_all_sites는 true 또는 false여야 합니다')
    site = settings.get("site")
    sites = [site] if isinstance(site, str) else site
    whole_zone = isinstance(site, str) and site in ('A*', 'B*', 'AB*')
    if not isinstance(sites, list) or not sites or (not whole_zone and any(not valid_site(s) for s in sites)):
        raise ValueError("site는 A*/B*/AB*(전체), A101~A153·B201~B246 번호 또는 번호 목록이어야 합니다")
    if (type(settings.get("nights")) is not int
            or settings["nights"] not in (1, 2) or settings.get("stop_at_customer_form") is not True):
        raise ValueError("1박/2박 · 정보입력 화면에서 중단 조건 확인 필요")
    dates = settings.get("checkin_dates", [])
    if not isinstance(dates, list):
        raise ValueError("checkin_dates는 YYYY-MM-DD 형식의 입실 날짜 목록이어야 합니다")
    for day in dates:
        try:
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("입실 날짜는 YYYY-MM-DD 형식의 실제 날짜여야 합니다") from None
    for key in ("weekdays", "reminder_weekdays"):
        days = settings.get(key)
        if (not isinstance(days, list) or not days
                or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days)):
            raise ValueError(f"{key}는 0(월)~6(일) 숫자 목록이어야 합니다")
    poll = _number(settings, "poll_seconds", 15, 300)
    fast = _number(settings, "fast_poll_seconds", 0.5, 10)
    burst = _number(settings, "burst_poll_seconds", 0.1, 1)
    burst_before = _number(settings, "burst_before_seconds", 0, 10)
    burst_after = _number(settings, "burst_after_seconds", 0, 60)
    _number(settings, "max_lookahead_days", 1, MAX_SPAN)
    if _number(settings, "headcount", 1, 6) != int(settings["headcount"]):
        raise ValueError("headcount는 정수여야 합니다")
    _number(settings, "fast_before_seconds", 0, 120)
    _number(settings, "fast_after_seconds", 30, 600)
    if not burst < fast < poll:
        raise ValueError("burst_poll_seconds < fast_poll_seconds < poll_seconds 이어야 합니다")
    if (burst_before > settings["fast_before_seconds"] or burst_after > settings["fast_after_seconds"]):
        raise ValueError("burst 구간은 fast 구간 안에 있어야 합니다")
    try:
        time.fromisoformat(settings["open_time"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("open_time은 HH:MM 형식이어야 합니다") from None


def target_dates(settings, today=None, span=None):
    """Explicit check-ins override weekdays; expired/out-of-window dates are ignored."""
    check_settings(settings)
    today = today or datetime.now(core.KST).date()
    span = settings["max_lookahead_days"] if span is None else span
    if settings.get("checkin_dates"):
        end = today + timedelta(days=span)
        return sorted({d for d in settings["checkin_dates"] if today <= date.fromisoformat(d) <= end})
    days = (today + timedelta(days=i) for i in range(span + 1))
    return [d.isoformat() for d in days if d.weekday() in settings["weekdays"]]


WEEKDAY_NAMES = "월화수목금토일"


def weekday_text(settings):
    return "·".join(WEEKDAY_NAMES[d] for d in sorted(settings["weekdays"])) + "요일"


def site_text(settings):
    if isinstance(settings['site'], str) and settings['site'] in ('A*', 'B*', 'AB*'):
        return '·'.join(target_zones(settings)) + '구역 전체 중 빈자리 한 자리'
    site = settings['site']
    sites = list(dict.fromkeys([site] if isinstance(site, str) else site))
    text = sites[0] if len(sites) == 1 else " → ".join(sites) + " 중 한 자리 (입력 순서 우선)"
    if settings.get('site_ranges'):
        text += ' → 범위 ' + ', '.join(settings['site_ranges'])
    return text + (" → 없으면 나머지 " + '·'.join(target_zones(settings)) + "구역 전체"
                   if settings.get('fallback_all_sites', False) else '')


def target_sites(settings):
    """None means the whole A zone; other lists include specific/range/fallback priorities."""
    site = settings["site"]
    if site == "A*":
        return None
    if isinstance(site, str) and site in ('B*', 'AB*'):
        return [f'{zone}{number}' for zone in target_zones(settings) for number in SITE_NUMBERS_BY_ZONE[zone]]
    priority = [site] if isinstance(site, str) else list(site)
    priority.extend(expand_ranges(settings.get('site_ranges', []), RANGE_LIMITS))
    if settings.get('fallback_all_sites', False):
        priority.extend(f'{zone}{number}' for zone in target_zones(settings) for number in SITE_NUMBERS_BY_ZONE[zone])
    return list(dict.fromkeys(priority))


def schedule_text(settings):
    dates = settings.get("checkin_dates")
    return "입실 지정일 " + ", ".join(sorted(set(dates))) if dates else weekday_text(settings)


def open_date(day):
    """Day a reservation date opens: same day-of-month one month earlier.
    A day the previous month lacks rolls into the next month (site shows 12/31 opening on 12/1)."""
    d = date.fromisoformat(day)
    year, month = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(year, month, 1) + timedelta(days=d.day - 1)


def open_rule_verified(day):
    """True for dates seen on the site: the day exists last month, or the 31st after a 30-day month."""
    d = date.fromisoformat(day)
    year, month = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    last = cal.monthrange(year, month)[1]
    return d.day <= last or (d.day == 31 and last == 30)


def open_at(settings, day):
    return datetime.combine(open_date(day), time.fromisoformat(settings["open_time"]), tzinfo=core.KST)


def fast_dates(settings, now):
    """Dates opening right now: only these get the short polling interval."""
    before = timedelta(seconds=settings["fast_before_seconds"])
    after = timedelta(seconds=settings["fast_after_seconds"])
    return [d for d in target_dates(settings, now.date(), MAX_SPAN)
            if open_at(settings, d) - before <= now <= open_at(settings, d) + after]


def next_fast_start(settings, now):
    before = timedelta(seconds=settings["fast_before_seconds"])
    starts = [open_at(settings, d) - before for d in target_dates(settings, now.date(), MAX_SPAN)]
    return min((s for s in starts if s > now), default=None)


def window_closed(page, exc):
    """True when the user closed the browser window (not a site error)."""
    return type(exc).__name__ == "TargetClosedError" or page.is_closed()


def watch_days(settings, today, open_only=False):
    """Normal-mode targets; with open_only, just the dates that open today."""
    if not open_only:
        return target_dates(settings, today)
    return [d for d in target_dates(settings, today, MAX_SPAN) if open_date(d) == today]


def in_burst(settings, days, now):
    before = timedelta(seconds=settings["burst_before_seconds"])
    after = timedelta(seconds=settings["burst_after_seconds"])
    return any(open_at(settings, d) - before <= now <= open_at(settings, d) + after for d in days)


def rush_dates(settings, now, results):
    """Keep opening dates in the burst window even when the first result is sold out."""
    return [d for d in fast_dates(settings, now)
            if in_burst(settings, [d], now) or results.get(d, "NOT_OPEN") == "NOT_OPEN"]


def wait_seconds(settings, rush_days, now):
    if rush_days:
        return settings["burst_poll_seconds"] if in_burst(settings, rush_days, now) else settings["fast_poll_seconds"]
    wait = settings["poll_seconds"]
    start = next_fast_start(settings, now)
    return max(1, min(wait, (start - now).total_seconds())) if start else wait


class CampingWatcher:
    def __init__(self, page, settings, state_path, credentials):
        self.page, self.settings, self.state_path, self.credentials = page, settings, state_path, credentials
        self.state = {"phase": "STARTING", "dates": {}, "cycles": 0}
        self.dialogs = []
        self.foreground_enabled = False
        self.foreground_shown = False
        self.searched_site = None
        self.today = lambda: datetime.now(core.KST).date()
        page.set_default_timeout(7000)
        page.on("dialog", self.on_dialog)

    def on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        # Never accept confirmations that might release an existing reservation.
        dialog.accept() if dialog.type == "alert" else dialog.dismiss()

    def log_event(self, event, **fields):
        """Append one JSON line to data/open_log.jsonl so a missed opening can be analysed afterwards."""
        entry = {"t": datetime.now(core.KST).isoformat(timespec="milliseconds"), "event": event, **fields}
        with (self.state_path.parent / "open_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record(self, day, result, inspect_ms, rush):
        """Log first sight of a date, every result change, and every check inside the opening window."""
        previous = self.state["dates"].get(day)
        if rush or result != previous:
            self.log_event("check", day=day, result=result, previous=previous,
                           inspect_ms=round(inspect_ms), rush=rush)

    def save(self, phase, **fields):
        self.state.update(phase=phase, updated_at=datetime.now(core.KST).isoformat(), **fields)
        core.save(self.state_path, self.state)

    def visible(self, selector):
        return any(obj.is_visible() for obj in self.page.locator(selector).all())

    def refresh_calendar_ui(self):
        # Unopened dates have no button yet; the site's inventory timer alone cannot
        # create those buttons. Normal month navigation rebuilds them at opening time.
        self.guard()
        if self.visible(".reserv_calendar"):
            for selector in (".prevMonth", ".nextMonth"):
                button = self.page.locator(selector)
                if button.count() == 1 and not button.is_disabled():
                    button.click()
                    self.page.wait_for_timeout(250)
                    return

    def guard(self):
        if self.visible("#CAPTCHA_TEXT"):
            raise RuntimeError("자동입력방지 인증 확인 필요")
        if self.visible(".PageErr_Frame, .dzAlert, .POPUP_BOX, .DZ_ALERTBOX"):
            raise PermissionError("사이트 안내/접근 제한 화면 확인 필요")

    def calendar(self, month):
        self.guard()
        if self.visible(".reserv_area"):
            self.page.locator("#PrevStep").click()
        self.page.locator(".reserv_calendar").wait_for(state="visible")
        for _ in range(6):
            title = self.page.locator(".reserv_calendar > ._title h2").inner_text()
            match = re.search(r"(\d{4})년\s*(\d{1,2})월", title)
            if not match:
                raise RuntimeError("달력 연월 확인 실패")
            current = f"{match[1]}{int(match[2]):02d}"
            if current == month:
                return True
            button = self.page.locator(".nextMonth" if current < month else ".prevMonth")
            if button.is_disabled():
                return False
            button.click()
            self.page.wait_for_timeout(250)
        raise RuntimeError("달력 이동 확인 실패")

    def reload_entry(self):
        self.page.goto(entry_url(self.today()), wait_until="domcontentloaded", timeout=30000)
        self.page.locator(".reserv_calendar").wait_for(state="visible", timeout=15000)

    def inspect_with_retry(self, day, tries=3):
        """Retry slow-page timeouts by reloading. Restriction/CAPTCHA screens (guard) are never retried."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        for attempt in range(1, tries + 1):
            try:
                return self.inspect(day)
            except PlaywrightTimeout:
                if self.state.get("active") or attempt == tries:
                    raise
                logging.warning("화면 응답 지연 · 다시 불러와 재시도 (%d/%d)", attempt, tries - 1)
                self.guard()
                self.reload_entry()

    def inspect(self, day):
        self.searched_site = None
        zones = target_zones(self.settings)
        if len(zones) == 1:
            result = self.inspect_zone(day, zones[0])
            if result == 'AVAILABLE':
                candidate = self.available_site()
                if candidate is None:
                    return 'SITE_SOLD_OUT'
                self.searched_site = candidate[0]
            return result
        priority = target_sites(self.settings)
        best, results, last_zone = None, [], None
        for zone in zones:
            result = self.inspect_zone(day, zone)
            last_zone = zone
            results.append(result)
            if result == 'AVAILABLE':
                candidate = self.available_site()
                if candidate is None:
                    continue
                name = candidate[0]
                rank = priority.index(name)
                if best is None or rank < best[0]:
                    best = (rank, zone, name)
                if rank == 0:
                    break
        if best is None:
            return 'SITE_SOLD_OUT' if 'SITE_SOLD_OUT' in results else results[0]
        _, zone, name = best
        if last_zone != zone and self.inspect_zone(day, zone) != 'AVAILABLE':
            return 'SITE_SOLD_OUT'
        candidate = self.available_site()
        if candidate is None or candidate[0] != name:
            return 'SITE_SOLD_OUT'
        self.searched_site = name
        return 'AVAILABLE'

    def inspect_zone(self, day, zone):
        if day not in target_dates(self.settings, self.today(), MAX_SPAN):
            raise RuntimeError("대상 날짜 조건이 아닙니다")
        if not self.calendar(day[:7].replace("-", "")):
            return "NOT_OPEN"
        value = ZONE_CALENDAR_IDS[zone] + "-" + day[2:] + "-1"
        button = self.page.locator(f'.reserv_calendar button[value="{value}"]')
        if button.count() == 0:
            return "NOT_OPEN"
        if button.is_disabled():
            return "ZONE_SOLD_OUT"
        button.click()
        self.page.locator(".reserv_area").wait_for(state="visible")
        self.guard()
        if not self.set_stay(day):
            return "STAY_UNAVAILABLE"
        self.verify_dates(day)
        return "AVAILABLE" if self.available_site() else "SITE_SOLD_OUT"

    def available_site(self):
        # Read displayed names and actual control IDs; never derive IDs from site numbers.
        found = False
        wanted = target_sites(self.settings)
        candidates = {}
        for key in self.page.locator('.reserv_area input[name="rkey[]"][id^="rKey_"]').all():
            row = key.locator("../..")
            title = row.locator(".intro_tit big")
            if title.count() != 1:
                raise RuntimeError("시설 표시명 확인 실패")
            name = title.inner_text().strip()
            if not valid_site(name) or name[0] not in target_zones(self.settings):
                continue
            if wanted is not None and name not in wanted:
                continue
            found = True
            key_id = key.get_attribute("id") or ""
            match = re.fullmatch(r"rKey_([0-9]+)", key_id)
            if not match:
                raise RuntimeError("시설 선택 ID 확인 실패")
            room_id = "roomcode" + match[1]
            room = self.page.locator(f'input[id="{room_id}"]')
            label = self.page.locator(f'label[for="{room_id}"]')
            if room.count() != 1 or label.count() != 1:
                raise RuntimeError("시설 선택 버튼 연결 확인 실패")
            if key.is_disabled() or room.is_disabled() or not label.is_visible():
                continue
            candidate = name, key_id, room_id, row
            if wanted is None:
                return candidate
            candidates[name] = candidate
        if not found:
            raise RuntimeError("대상 A/B구역 시설을 화면에서 확인하지 못했습니다")
        if wanted is None:
            return None
        return next((candidates[name] for name in wanted if name in candidates), None)

    def set_stay(self, day):
        """Change the range through the public date picker before choosing any site."""
        if self.settings["nights"] == 1:
            return True  # the calendar's normal date button opens a one-night stay
        self.page.locator('.reserv_area .period_calendar button').click()
        picker = self.page.locator('#datepicker-checkInOutTerm')
        picker.wait_for(state="visible")
        for chosen in (date.fromisoformat(day), date.fromisoformat(day) + timedelta(days=2)):
            stamp = int(datetime.combine(chosen, time(), tzinfo=core.KST).timestamp() * 1000)
            cell = picker.locator(f'.datepicker__month-day--visibleMonth[time="{stamp}"]')
            if (cell.count() != 1 or not cell.is_visible()
                    or "datepicker__month-day--valid" not in (cell.get_attribute("class") or "").split()):
                picker.locator('#close-checkInOutTerm').click()
                return False
            cell.click()
        picker.wait_for(state="hidden")
        from playwright.sync_api import expect
        expect(self.page.locator('.reserv_area .period_txt')).to_have_text('2박')
        self.guard()
        self.verify_dates(day)
        return True

    def verify_dates(self, day):
        nights = self.settings["nights"]
        checkout = (date.fromisoformat(day) + timedelta(days=nights)).strftime("%Y/%m/%d")
        if (self.page.locator(".chkin_txt strong").inner_text().strip() != day.replace("-", "/")
                or self.page.locator(".chkout_txt strong").inner_text().strip() != checkout
                or self.page.locator(".period_txt").inner_text().strip() != f"{nights}박"):
            raise RuntimeError(f"입실/퇴실/{nights}박 검증 실패")

    def set_headcount(self, row):
        # The minus button only appears after the site is selected; reduce through the public UI only.
        want, shown = self.settings["headcount"], row.locator(".mens span").first
        for _ in range(6):
            if shown.inner_text().strip() == f"{want}명":
                break
            row.locator(".btn-minus").click()
            self.page.wait_for_timeout(50)
        if (shown.inner_text().strip() != f"{want}명"
                or row.locator(".mens input[type=text]").first.input_value() != str(want)):
            raise RuntimeError("예약인원 설정 확인 실패")

    def handoff_phase(self, day):
        if self.visible("#CAPTCHA_TEXT"):
            return "CAPTCHA_REQUIRED"
        if self.visible(".reserv_reserv #appName"):
            rows = self.page.locator(".ResvPurList > li:visible")
            d = date.fromisoformat(day)
            checkout = d + timedelta(days=self.settings["nights"])
            stay_text = rows.first.locator(".app_date").inner_text() if rows.count() == 1 else ""
            date_parts = re.findall(r"(\d{4})\s*(?:년|[./-])\s*(\d{1,2})\s*(?:월|[./-])\s*(\d{1,2})(?:\s*일)?", stay_text)
            expected = [(str(x.year), str(x.month), str(x.day)) for x in (d, checkout)]
            actual = [tuple(str(int(n)) for n in parts) for parts in date_parts]
            if (rows.count() == 1 and rows.first.locator(".app_title h4").inner_text().strip() == self.state.get("active", {}).get("site")
                    and actual == expected and re.search(rf"(?<!\d){self.settings['nights']}\s*박", stay_text)):
                timer = self.page.locator("#DzReserv_Counter")
                timer_running = self.visible("#DzReserv_Counter") and any(
                    int(n) > 0 for n in re.findall(r"\d+", timer.inner_text()))
                return "HELD" if self.visible(".PriorTimer") and timer_running else "FORM_READY"
        return "REVIEW_REQUIRED"

    def attempt(self, day):
        if day not in target_dates(self.settings, self.today(), MAX_SPAN):
            raise RuntimeError("허용되지 않은 날짜")
        began = perf_counter()
        self.guard()
        self.verify_dates(day)
        candidate = self.available_site()
        if candidate is None:
            return False
        site, key_id, room_id, row = candidate
        if self.searched_site is not None and site != self.searched_site:
            return False  # Recheck all zones on the next cycle if inventory changed.
        self.page.locator(f'label[for="{room_id}"]').click()
        checked = self.page.locator('input[name="rkey[]"]:checked')
        if checked.count() != 1 or checked.first.get_attribute("id") != key_id:
            raise RuntimeError("A/B구역 한 자리 단일 선택 확인 실패")
        if row.locator(".intro_tit big").inner_text().strip() != site:
            raise RuntimeError("선택한 시설 표시명 불일치")
        self.set_headcount(row)
        self.verify_dates(day)
        # Persist before the action; unknown results never trigger duplicate attempts.
        self.save("ENTRY_ATTEMPT", active={"site": site, "checkin": day,
                  "checkout": (date.fromisoformat(day) + timedelta(days=self.settings["nights"])).isoformat(),
                  "nights": self.settings["nights"]})
        prepared = perf_counter()
        self.page.locator(".reserv_area #NextEntry").click()
        for _ in range(30):
            self.page.wait_for_timeout(200)
            phase = self.handoff_phase(day)
            if phase != "REVIEW_REQUIRED" or self.visible(".POPUP_BOX, .dzAlert, .PageErr_Frame"):
                break
        self.log_event("attempt", day=day, phase=phase, prepare_ms=round((prepared - began) * 1000),
                       next_to_phase_ms=round((perf_counter() - prepared) * 1000))
        self.announce(phase, day)
        return True

    def announce(self, phase, day):
        site = self.state["active"]["site"]
        labels = {"HELD": f"🏕️ {site} 임시점유 안내 확인", "FORM_READY": f"🏕️ {site} 정보입력 화면 확인",
                  "CAPTCHA_REQUIRED": f"⚠️ {site} 자동입력방지 인증 필요", "REVIEW_REQUIRED": f"⚠️ {site} 다음단계 결과 확인 필요"}
        self.save(phase)
        if self.foreground_enabled and not self.foreground_shown:
            focus_browser(self.page)
            self.foreground_shown = True
        message = ("아직 임시점유 확인 전입니다. 브라우저에서 인증을 직접 입력해주세요." if phase == "CAPTCHA_REQUIRED" else
                   "화면의 남은 시간을 확인하고 이름·전화번호·최종 예약은 직접 진행해주세요." if phase in ("HELD", "FORM_READY") else
                   "확보됐다고 확인할 수 없습니다. 브라우저의 안내를 직접 확인해주세요.")
        core.notify(self.credentials, f"{labels[phase]}\n{day}({WEEKDAY_NAMES[date.fromisoformat(day).weekday()]}) 입실 · {self.settings['nights']}박 · {site}\n{message}\n자동 감시는 멈추고 창을 유지합니다.")
        print(f"{labels[phase]} · {day}\n{message}\n결제 중에는 터미널/브라우저를 닫지 마세요.", flush=True)

    def hold_window(self):
        # Observe human CAPTCHA completion only; never read/solve/submit the challenge.
        while not core.STOP and not self.page.is_closed():
            if self.state.get("phase") == "CAPTCHA_REQUIRED":
                day = self.state["active"]["checkin"]
                phase = self.handoff_phase(day)
                if phase in ("HELD", "FORM_READY"):
                    self.announce(phase, day)
            self.page.wait_for_timeout(500)


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-only", action="store_true", help="오늘 열리는 날짜만 감시(다른 날짜의 빈자리는 무시)")
    parser.add_argument("--auto-start", action="store_true", help="Enter 대기 없이 바로 감시 시작(예약 실행용)")
    parser.add_argument("--check", action="store_true", help="한 번 조회만. 선택/다음단계 클릭 안 함")
    parser.add_argument("--test", action="store_true", help="체험: 날짜·요일 조건 없이 설정한 A/B구역 자리의 다음단계까지 진행 후 멈춤")
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    settings = json.loads((ROOT / "camping_settings.json").read_text(encoding="utf-8"))
    if args.test:  # ignore date/weekday filters; keep the configured number of nights
        settings["weekdays"] = [0, 1, 2, 3, 4, 5, 6]
        settings["checkin_dates"] = []
    target_dates(settings)
    state_path = ROOT / "data" / ("camping_probe.json" if args.check else "camping_test.json" if args.test else "camping_state.json")
    state_path.parent.mkdir(exist_ok=True)
    try:
        lock = core.acquire_lock(ROOT / "data" / "camping.lock")
    except OSError:
        print("이미 다른 감시가 실행 중입니다. 열려 있는 감시 창을 확인하세요.")
        return
    if not args.check and not args.test:
        core.keep_awake()
    archive = core.recover_previous_state(state_path, lock)
    if archive:
        print(f"실행 잠금 확인 완료: 이전 실행의 진행 기록을 보관하고 초기화했습니다. ({archive.name})\n"
              "이 기록은 실제 예약 접수 여부를 뜻하지 않습니다. 사이트의 예약 내역은 별도로 확인해주세요.", flush=True)
    env = ROOT / ".env"
    credentials = core.load_telegram(env)  # missing .env/Telegram is fine: alerts then appear on screen only
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    with sync_playwright() as p:
        if args.check or args.headless:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1280, "height": 900})
        else:
            context = p.chromium.launch_persistent_context(str(ROOT / ".camping-profile"), headless=False,
                      locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        watcher = CampingWatcher(page, settings, state_path, credentials)
        watcher.foreground_enabled = not (args.check or args.headless)
        try:
            page.goto(entry_url(datetime.now(core.KST).date()), wait_until="domcontentloaded", timeout=30000)
            page.locator(".reserv_calendar").wait_for(state="visible", timeout=15000)
            if args.test:
                print(f"\n🧪 체험(테스트) 모드: 날짜·요일 조건 없이, {settings['nights']}박 · 지금 {site_text(settings)}가 열려 있는 아무 날짜로 해봅니다.\n"
                      "   인증(캡차) 화면에서 멈춥니다. 이름/전화/결제까지 진행하지만 않으면 예약되지 않습니다.\n"
                      "   혹시 '정보입력' 화면까지 가면 몇 분간 임시점유 상태가 되니, 창을 닫고 기다리면 풀립니다.\n", flush=True)
            if not args.check and not args.test:
                print(f"앞으로 {settings['max_lookahead_days']}일 이내 {schedule_text(settings)} · {settings['nights']}박 · {site_text(settings)}. 오픈 시각({settings['open_time']})에는 빠른 확인. Enter로 감시 시작. 인증이 나오면 직접 입력해야 합니다.", flush=True)
                if not args.auto_start:
                    commands = core.ConsoleCommands();commands.start()
                    while not core.STOP and commands.poll(0) is None:
                        page.wait_for_timeout(200)
                        if commands.closed:
                            return
                if core.STOP:
                    return
                core.notify(credentials, f"🏕️ 연곡해변 {site_text(settings)} 감시 시작\n앞으로 {settings['max_lookahead_days']}일 이내 {schedule_text(settings)} · {settings['nights']}박\n인원 {settings['headcount']}명 · 다음단계까지 자동, 인증/정보입력/최종 예약은 직접 진행합니다.")
            while not core.STOP and not page.is_closed():
                if watcher.state["cycles"]:
                    watcher.refresh_calendar_ui()
                now = datetime.now(core.KST)
                # During the burst, keep checking today's opening even after a sold-out result.
                rush = [] if args.check or args.test else rush_dates(settings, now, watcher.state["dates"])
                for day in rush or watch_days(settings, now.date(), args.open_only):
                    if core.STOP:
                        return
                    started = perf_counter()
                    result = watcher.inspect_with_retry(day)
                    if not args.check and not args.test:
                        watcher.record(day, result, (perf_counter() - started) * 1000, bool(rush))
                    watcher.state["dates"][day] = result
                    if result == "AVAILABLE" and not args.check and watcher.attempt(day):
                        if args.test:
                            print(f"\n✅ 체험 성공! {day} 로 [다음 단계]까지 눌렀습니다. 화면 상태: {watcher.state.get('phase')}\n"
                                  "   실제로도 이렇게 되고, 인증 숫자/이름/전화/최종 예약은 직접 하게 됩니다.\n"
                                  "   이제 브라우저 창을 닫으면 체험이 끝납니다. (결제 전에 멈췄다면 아무것도 예약되지 않았습니다)", flush=True)
                            if args.headless:
                                return
                        watcher.hold_window()
                        return
                watcher.state["cycles"] += 1
                watcher.save("MONITORING" if not (args.check or args.test) else "CHECK_COMPLETE")
                logging.info("%s %s확인 완료 · %s", site_text(settings), "오픈 직후 빠른 " if rush else "", watcher.state["dates"])
                if args.test:
                    print("\n체험 결과: 사이트 접속과 달력 조회는 정상입니다. 다만 지금은 지정한 구역에 빈자리가 있는 날짜가 없어서 "
                          "[다음 단계]까지는 못 갔습니다. (자리가 열리면 자동으로 이렇게 진행됩니다) 나중에 다시 해보세요.", flush=True)
                if args.check or args.test:
                    return
                page.wait_for_timeout(int(wait_seconds(settings, rush, datetime.now(core.KST)) * 1000))
        except Exception as exc:
            if window_closed(page, exc):
                # User closed the window on purpose: exit quietly. A pending attempt record is kept as-is.
                pending = bool(watcher.state.get("active"))
                watcher.save("REVIEW_REQUIRED" if pending else "STOPPED")
                print("브라우저 창이 닫혀 종료합니다." + (" 예약 진행 기록은 남겨 둡니다." if pending else ""), flush=True)
                return
            watcher.save("REVIEW_REQUIRED" if watcher.state.get("active") else "PAUSED_ERROR", error=type(exc).__name__)
            logging.warning("감시 중단: %s · 사이트 화면 확인 필요", type(exc).__name__)
            if args.check or args.test:
                raise
            core.notify(credentials, "⚠️ 연곡해변 감시 중단 · 사이트 안내/오류 화면을 확인하세요. 자동 재시도하지 않고 창을 유지합니다.")
            watcher.hold_window()
        finally:
            try:
                context.close()
            except Exception:
                pass  # already closed by the user


if __name__ == "__main__":
    signal.signal(signal.SIGINT, core.stop)
    signal.signal(signal.SIGTERM, core.stop)
    main()
