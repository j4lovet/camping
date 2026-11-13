"""Windows: install/remove the always-on phone remote (camping_remote.py). Starts when you log in."""
import sys
from pathlib import Path

from camping_common import load_telegram
from install_reminder import install_remote

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    remove = "--remove" in sys.argv
    if not remove and not (load_telegram(ROOT / ".env").get("TELEGRAM_BOT_TOKEN")):
        raise SystemExit("텔레그램 봇이 아직 설정되지 않았습니다. 먼저 telegram_setup.py를 실행하세요.")
    print("폰 리모컨:", install_remote(remove))
    print("삭제하려면: .camping-venv\\Scripts\\python.exe install_remote.py --remove")
