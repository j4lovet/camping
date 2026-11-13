"""조건 보기/수정 + 처음 설정(알림을 쓸지 묻기). 갯골/연곡 공용: camping_settings.json 내용으로 프로그램을 알아낸다.

사용: python configure.py            (메뉴: 언제든 조건/알림 확인·수정)
      python configure.py --first-run (Start Camping.bat가 처음 한 번 자동 실행)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import camping_common as core
import camping_watch as watch
from camping_priority import parse_ranges

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "camping_settings.json"
DONE = ROOT / "data" / "first_run_done"
DAYS = "월화수목금토일"
GAETGOL = "rooms" in json.loads(SETTINGS.read_text(encoding="utf-8"))
NAME = "갯골자연휴양림 오토캠핑장" if GAETGOL else "연곡해변캠핑장 A구역"
ALARM_WHEN = ("매달 4일 아침 8시에" if GAETGOL else "예약 날짜가 열리는 날 아침 9시 30분에")
ALARM_TASK = "GaetgolCampingReminder" if GAETGOL else "YeongokCampingReminder"
STARTUP_BAT = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / (
    "gaetgol-camping-remote.bat" if GAETGOL else "yeongok-camping-remote.bat")


def ask(text, default=""):
    try:
        return input(text).strip() or default
    except EOFError:
        return default


def load():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def save(settings):
    watch.check_settings(settings)  # refuse anything the program would reject
    backup = SETTINGS.with_suffix(".json.bak")
    backup.write_text(SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT, delete=False) as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(f.name, SETTINGS)


def day_text(days):
    return "·".join(DAYS[d] for d in sorted(days)) + "요일" if len(set(days)) < 7 else "모든 요일"


def room_text(rooms):
    out = []
    for room in rooms:
        m = re.fullmatch(r"오토캠핑([AB]) (\d{2})", room)
        out.append(f"{m[1]}{m[2]}" if m else f"{room[-1]}구역 아무 자리")
    return " → ".join(out)


def telegram_ready():
    values = core.load_telegram(ROOT / ".env")
    return bool(values.get("TELEGRAM_BOT_TOKEN") and values.get("TELEGRAM_CHAT_ID"))


def task_installed():
    try:
        return subprocess.run(["schtasks", "/Query", "/TN", ALARM_TASK], capture_output=True).returncode == 0
    except OSError:
        return None


def show():
    s = load()
    alarm, remote = task_installed(), STARTUP_BAT.exists()
    print(f"\n==== 지금 설정 ({NAME}) ====")
    print(f"  일정      : {day_text(s['weekdays']) if GAETGOL else watch.schedule_text(s)}  ({s['nights']}박)")
    if not GAETGOL and s.get("checkin_dates"):
        print("              지정 날짜가 요일 조건보다 우선합니다. 지난 날짜는 자동 제외됩니다.")
        if not watch.target_dates(s):
            print("              현재 조회 기간 내 대상 날짜가 없습니다. 지정일과 볼 기간을 확인하세요.")
    print(f"  인원      : {s['headcount']}명")
    if GAETGOL:
        print(f"  자리      : {room_text(s['rooms'])}  (앞에 있는 자리부터 찾음)")
        print(f"  오픈      : 매달 {s['open_day']}일 {s['open_time']} (사이트 규칙이라 바꾸지 않음)")
    else:
        print(f"  자리      : {watch.site_text(s)}")
        print(f"  볼 기간   : 오늘부터 {s['max_lookahead_days']}일 앞까지")
        print(f"  오픈      : 날짜마다 한 달 전 같은 날짜 {s['open_time']}")
    print(f"  확인 주기 : 평소 {s['poll_seconds']}초 · 오픈 직전·직후 {s['fast_poll_seconds']}초 / {s['burst_poll_seconds']}초 (바꾸지 않는 걸 권장)")
    print(f"  텔레그램  : {'연결됨' if telegram_ready() else '연결 안 됨 (알림은 컴퓨터 화면에만 표시)'}")
    print(f"  아침 알림 : {'켜짐' if alarm else '꺼짐' if alarm is False else '확인 불가'}  ({ALARM_WHEN} 텔레그램으로 알림)")
    print(f"  폰 리모컨 : {'켜짐' if remote else '꺼짐'}")
    print()


def edit_days(s):
    print("원하는 요일을 쓰세요. 예: 토   /  금 토   /  토 일   /  전체")
    text = ask("요일: ")
    if not text:
        return
    days = list(range(7)) if "전체" in text else sorted({DAYS.index(c) for c in text if c in DAYS})
    if not days:
        print("요일을 못 알아봤어요. 월 화 수 목 금 토 일 중에서 쓰세요.")
        return
    s["weekdays"] = days
    if not GAETGOL:
        s["checkin_dates"] = []
    if "reminder_weekdays" in s:
        s["reminder_weekdays"] = days
    save(s)
    print(f"저장했습니다 -> {day_text(days)}")


def edit_people(s):
    text = ask(f"인원 (1~6, 지금 {s['headcount']}): ")
    if not text:
        return
    if not text.isdigit() or not 1 <= int(text) <= 6:
        print("1~6 사이 숫자로 쓰세요.")
        return
    s["headcount"] = int(text)
    save(s)
    print(f"저장했습니다 -> {text}명")


def edit_dates(s):
    print("원하는 입실 날짜를 입력하세요. 예: 2026-11-20 2026-11-27 (공백 또는 쉼표로 구분)")
    print("여러 날짜는 후보입니다. 그중 한 날짜에 한 자리만 진행합니다.")
    print("요일 조건으로 돌아가려면 '해제'를 입력하세요. Enter는 변경 없이 돌아갑니다.")
    text = ask("입실 날짜: ")
    if not text:
        return
    if text == "해제":
        s["checkin_dates"] = []
    else:
        tokens = text.replace(",", " ").split()
        try:
            today = datetime.now(core.KST).date()
            for token in tokens:
                if date.fromisoformat(token).isoformat() != token or date.fromisoformat(token) < today:
                    raise ValueError
        except ValueError:
            print("오늘 이후의 실제 날짜를 YYYY-MM-DD 형식으로 입력하세요. 저장하지 않았습니다.")
            return
        s["checkin_dates"] = sorted(set(tokens))
    save(s)
    print(f"저장했습니다 -> {watch.schedule_text(s)}")


def edit_nights(s):
    text = ask(f"숙박 일수 (1 또는 2, 지금 {s['nights']}박): ")
    if not text:
        return
    if text not in ("1", "2"):
        print("1 또는 2를 입력하세요. 저장하지 않았습니다.")
        return
    s["nights"] = int(text)
    save(s)
    print(f"저장했습니다 -> {text}박 (퇴실일은 입실일 + {text}일)")


def edit_sites(s):
    print("예약할 A/B사이트를 고르세요.")
    print("  1) A사이트 전체 중 빈자리 한 자리")
    print("  2) 특정 번호 지정 (한 개 또는 여러 개, 입력 순서 우선)")
    print("  3) 지정 자리 우선 → 없으면 지정한 구역의 나머지 전체")
    print("  4) 지정 자리 → 지정 범위 → 해당 구역의 나머지 전체")
    print("  5) B사이트 전체 중 빈자리 한 자리")
    print("  6) A·B사이트 전체 중 빈자리 한 자리")
    choice = ask("번호 (Enter = 변경 없음): ")
    if choice == "1":
        selected = "A*"
    elif choice == '5':
        selected = 'B*'
    elif choice == '6':
        selected = 'AB*'
    elif choice in ("2", "3", "4"):
        print("예: A135 / B220 / B220 B221 A135 / 135,220 (A101~A153, B201~B246)")
        tokens = ask("사이트 번호: ").upper().replace(",", " ").split()
        if not tokens:
            return
        sites = []
        for token in tokens:
            match = re.fullmatch(r"([AB]?)(\d{3})", token)
            if not match:
                print(f"'{token}'는 지원하는 번호가 아닙니다. A101~A153, B201~B246으로 입력하세요. 저장하지 않았습니다.")
                return
            zone = match[1] or ('A' if int(match[2]) in watch.SITE_NUMBERS else 'B')
            site = zone + match[2]
            if not watch.valid_site(site):
                print(f"'{token}'는 지원하는 번호가 아닙니다. A101~A153, B201~B246으로 입력하세요. 저장하지 않았습니다.")
                return
            if site not in sites:
                sites.append(site)
        selected = sites[0] if len(sites) == 1 else sites
    else:
        return
    ranges = None
    if choice == '4':
        try:
            ranges = parse_ranges(ask('2순위 범위 (예: B210~B230 A130~A145): '), watch.RANGE_LIMITS)
        except ValueError as exc:
            print(f'{exc} · 저장하지 않았습니다.')
            return
    s["site"] = selected
    if ranges is not None:
        s['site_ranges'] = ranges
    else:
        s.pop('site_ranges', None)
    if choice in ('3', '4') or 'fallback_all_sites' in s:
        s['fallback_all_sites'] = choice in ('3', '4')
    save(s)
    print(f"저장했습니다 -> {watch.site_text(s)}")


def edit_rooms(s):
    print("자리를 고르세요.")
    print("  1) A구역(4인실) 아무 자리  - A01~A09 중 먼저 되는 곳")
    print("  2) A06 → A07 → A08 → A09 만 (기본)")
    print("  3) A·B구역 아무 자리  - B는 6인실 (요금이 더 비쌈)")
    print("  4) 직접 입력  - 예: A06 A07 B05  (A는 01~09, B는 01~16, 앞에 쓴 순서대로 우선)")
    choice = ask("번호: ")
    if choice == "1":
        s["rooms"] = ["오토캠핑A"]
    elif choice == "2":
        s["rooms"] = [f"오토캠핑A {n:02d}" for n in (6, 7, 8, 9)]
    elif choice == "3":
        s["rooms"] = ["오토캠핑A", "오토캠핑B"]
    elif choice == "4":
        rooms = []
        for token in ask("자리들: ").upper().replace(",", " ").split():
            m = re.fullmatch(r"([AB])(\d{1,2})", token)
            low, high = (1, 9) if m and m[1] == "A" else (1, 16)
            if not m or not low <= int(m[2]) <= high:
                print(f"'{token}' 는 없는 자리예요. (A01~A09, B01~B16)")
                return
            rooms.append(f"오토캠핑{m[1]} {int(m[2]):02d}")
        if not rooms:
            return
        s["rooms"] = rooms
    else:
        return
    save(s)
    print(f"저장했습니다 -> {room_text(s['rooms'])}")


def edit_lookahead(s):
    text = ask(f"오늘부터 며칠 앞까지 볼까요? (1~62, 지금 {s['max_lookahead_days']}일): ")
    if not text:
        return
    if not text.isdigit() or not 1 <= int(text) <= watch.MAX_SPAN:
        print("1~62 사이 숫자로 쓰세요.")
        return
    s["max_lookahead_days"] = int(text)
    save(s)
    print(f"저장했습니다 -> {text}일")


def toggle_alarm():
    if not telegram_ready():
        print("텔레그램이 아직 연결되지 않아서 알림을 켤 수 없어요. (00_처음시작.txt 맨 아래 '텔레그램 봇 만들기' 참고)")
        return
    import install_reminder
    if task_installed():
        if ask("아침 알림이 켜져 있습니다. 끌까요? (y/N): ").lower() == "y":
            print(install_reminder.install_reminder(remove=True))
        return
    print(f"아침 알림: {ALARM_WHEN} 텔레그램으로 '오늘 예약이 열려요' 알려줍니다.")
    print("※ 이 알림은 그 시각에 컴퓨터가 켜져 있어야만 옵니다. (꺼져 있거나 절전이면 안 옵니다)")
    if ask("알림을 켤까요? (y/N): ").lower() == "y":
        print(install_reminder.install_reminder())


def toggle_remote():
    if not telegram_ready():
        print("텔레그램이 아직 연결되지 않아서 폰 리모컨을 켤 수 없어요.")
        return
    import install_reminder
    if STARTUP_BAT.exists():
        if ask("폰 리모컨이 켜져 있습니다. 끌까요? (y/N): ").lower() == "y":
            print(install_reminder.install_remote(remove=True))
        return
    print("폰 리모컨: 폰에서 내 봇에 '시작' / '중지' / '상태'를 보내면 컴퓨터의 감시를 켜고 끕니다.")
    print("※ 컴퓨터가 켜져 있어야 동작합니다.")
    if ask("켤까요? (y/N): ").lower() == "y":
        print(install_reminder.install_remote())


def menu():
    while True:
        show()
        print("  1) 요일 바꾸기   2) 인원 바꾸기   3) " + ("자리 바꾸기" if GAETGOL else "볼 기간 바꾸기"))
        print("  4) 아침 알림 켜기/끄기   5) 폰 리모컨 켜기/끄기   0) 끝내기")
        if not GAETGOL:
            print("  6) 입실 날짜 지정/해제   7) 숙박 일수 바꾸기 (1박/2박)")
            print("  8) A/B사이트 전체·특정 번호·범위 선택")
        choice = ask("번호: ", "0")
        s = load()
        if choice == "1":
            edit_days(s)
        elif choice == "2":
            edit_people(s)
        elif choice == "3":
            edit_rooms(s) if GAETGOL else edit_lookahead(s)
        elif choice == "4":
            toggle_alarm()
        elif choice == "5":
            toggle_remote()
        elif choice == "6" and not GAETGOL:
            edit_dates(s)
        elif choice == "7" and not GAETGOL:
            edit_nights(s)
        elif choice == "8" and not GAETGOL:
            edit_sites(s)
        elif choice == "0":
            print("끝. 바꾼 조건은 감시를 껐다가 다시 켜면 적용됩니다.")
            return


def first_run():
    if DONE.exists():
        return
    print(f"\n처음 설정입니다. 아래가 지금 정해져 있는 조건이에요. ({NAME})")
    show()
    if ask("이 조건 그대로 쓸까요? (Enter = 그대로 / e = 바꾸기): ").lower() == "e":
        menu()
    if telegram_ready():
        if not task_installed():
            print("\n[아침 알림]")
            toggle_alarm()
        if not STARTUP_BAT.exists():
            print("\n[폰 리모컨]")
            toggle_remote()
    else:
        print("\n텔레그램이 연결되지 않아 알림/리모컨은 건너뜁니다. 나중에 '설정_보기_수정.bat'에서 켤 수 있어요.")
    DONE.parent.mkdir(exist_ok=True)
    DONE.write_text("ok", encoding="utf-8")
    print("\n설정은 언제든 '설정_보기_수정.bat'로 다시 보고 바꿀 수 있습니다.\n")


if __name__ == "__main__":
    try:
        first_run() if "--first-run" in sys.argv else menu()
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
