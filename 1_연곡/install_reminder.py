"""Windows: install/remove the daily reminder (09:30, Task Scheduler) and the always-on phone remote (Startup folder).
No secrets are written to them; both read .env at run time."""
import os
import subprocess
import sys
from pathlib import Path

import camping_common as core

ROOT = Path(__file__).resolve().parent
REMINDER_TASK = "YeongokCampingReminder"
STARTUP = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def need_venv():
    python = core.venv_python(ROOT)
    if not python.exists():
        raise SystemExit("먼저 Start Camping.bat로 설치를 마치세요 (.camping-venv 없음).")
    return core.venv_python(ROOT, windowless=True)


def install_reminder(remove=False):
    if remove:
        result = subprocess.run(["schtasks", "/Delete", "/TN", REMINDER_TASK, "/F"], capture_output=True, text=True)
        return "삭제했습니다." if not result.returncode else "등록된 알림이 없습니다."
    pythonw = need_venv()
    command = f'"{pythonw}" "{ROOT / "camping_reminder.py"}"'
    result = subprocess.run(["schtasks", "/Create", "/TN", REMINDER_TASK, "/TR", command,
                             "/SC", "DAILY", "/ST", "09:30", "/F"], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"등록 실패: {result.stderr.strip() or result.stdout.strip()}")
    return "등록했습니다. (매일 09:30에 확인, 알릴 오픈이 있는 날에만 알림 · 그 시각에 컴퓨터가 켜져 있어야 합니다)"


def install_remote(remove=False):
    launcher = STARTUP / "yeongok-camping-remote.bat"
    if remove:
        launcher.unlink(missing_ok=True)
        return "삭제했습니다. (이미 켜진 리모컨은 다시 시작할 때 꺼집니다)"
    pythonw = need_venv()
    STARTUP.mkdir(parents=True, exist_ok=True)
    launcher.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\nstart "" "{pythonw}" "{ROOT / "camping_remote.py"}"\r\n', encoding="utf-8")
    subprocess.Popen([str(pythonw), str(ROOT / "camping_remote.py")], cwd=ROOT, stdin=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    return "등록했습니다. 컴퓨터를 켤 때마다 자동으로 켜지고, 지금도 바로 시작했습니다."


def main():
    remove = "--remove" in sys.argv
    print("알림(오픈 당일 9:30):", install_reminder(remove))
    print("삭제하려면: .camping-venv\\Scripts\\python.exe install_reminder.py --remove")


if __name__ == "__main__":
    main()
