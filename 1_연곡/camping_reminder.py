"""Telegram heads-up on days a reminder_weekdays date (default Saturday) opens for booking. launchd runs this daily at 09:30."""
import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import camping_common as core
from camping_watch import MAX_SPAN, WEEKDAY_NAMES, check_settings, open_at, open_date, open_rule_verified, target_dates

ROOT = Path(__file__).resolve().parent


def reminder_text(settings, now):
    """Message for reminder-weekday dates opening today, or None. Skips once the opening time has passed."""
    check_settings(settings)
    days = [d for d in target_dates(dict(settings, weekdays=settings["reminder_weekdays"]), now.date(), MAX_SPAN)
            if open_date(d) == now.date() and now < open_at(settings, d)]
    if not days:
        return None
    opening = open_at(settings, days[0])
    names = ", ".join(f"{date.fromisoformat(d).month}/{date.fromisoformat(d).day}({WEEKDAY_NAMES[date.fromisoformat(d).weekday()]})"
                      for d in days)
    start = (opening - timedelta(minutes=10)).strftime("%H:%M")
    lines = [f"🏕️ 오늘 {opening:%H:%M}에 {names} 예약이 열립니다.",
             f"{settings['nights']}박 · {start}쯤 Start Camping.bat를 실행하고 Enter까지 눌러 두세요.",
             "오픈 직후에는 브라우저에서 직접 시도하는 것도 병행하세요."]
    if not all(open_rule_verified(d) for d in days):
        lines.append("※ 말일 오픈 규칙은 확인되지 않았습니다. 사이트의 '오픈예정' 표시를 확인하세요.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="텔레그램으로 보내지 않고 화면에만 표시")
    parser.add_argument("--now", help='시험용 현재 시각 "YYYY-MM-DD HH:MM" (KST)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    now = datetime.fromisoformat(args.now).replace(tzinfo=core.KST) if args.now else datetime.now(core.KST)
    text = reminder_text(json.loads((ROOT / "camping_settings.json").read_text()), now)
    if text is None:
        logging.info("오늘은 알릴 오픈이 없습니다.")
        return
    if args.dry_run:
        print(text)
        return
    core.notify(core.load_telegram(ROOT / ".env"), text)
    logging.info("알림 전송 시도 완료")


if __name__ == "__main__":
    main()
