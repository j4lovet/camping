"""First-run setup: offers the Telegram bot setup (optional; alerts otherwise appear on screen only)."""
import subprocess
import sys
from pathlib import Path

from camping_common import load_telegram

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"


def main():
    if not (load_telegram(ENV).get("TELEGRAM_BOT_TOKEN") and load_telegram(ENV).get("TELEGRAM_CHAT_ID")):
        if input("\n텔레그램 알림/폰 리모컨을 설정할까요? (y/N): ").strip().lower() == "y":
            subprocess.run([sys.executable, str(ROOT / "telegram_setup.py")])
        else:
            print("건너뜁니다. 빈자리 알림은 화면에만 표시됩니다. 나중에 telegram_setup.py로 설정할 수 있습니다.")


if __name__ == "__main__":
    main()
