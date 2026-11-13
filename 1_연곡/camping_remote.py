"""Phone remote: commands from your own private Telegram chat start/stop/check the watcher on this computer."""
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import camping_common as core

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "camping_state.json"
COMMANDS = {"/start": "start", "시작": "start", "/open": "open", "오픈": "open", "/stop": "stop", "중지": "stop",
            "종료": "stop", "/status": "status", "상태": "status", "/help": "help", "도움말": "help"}
HELP = ("🏕️ 캠핑 리모컨\n시작 (또는 /start): 감시 시작 · 컴퓨터에 브라우저가 열립니다\n"
        "오픈 (또는 /open): 오늘 열리는 날짜만 감시\n중지 (또는 /stop): 감시 끄기(예약 진행 중이면 거절)\n상태 (또는 /status): 지금 상태")


def parse_command(text):
    words = (text or "").strip().split()
    return COMMANDS.get(words[0].split("@")[0].lower()) if words else None


def watcher_pids():
    """Only watchers started from THIS folder (another camping project may run the same script name)."""
    import psutil
    script, mine = os.path.normcase(str(ROOT / "camping_watch.py")), []
    for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            cmd = proc.info["cmdline"] or []
            if not any(os.path.basename(part) == "camping_watch.py" for part in cmd):
                continue
            by_path = any(os.path.normcase(os.path.abspath(part)) == script for part in cmd if part.endswith("camping_watch.py"))
            by_cwd = os.path.normcase(proc.info["cwd"] or "") == os.path.normcase(str(ROOT))
            if by_path or by_cwd:
                mine.append(proc.info["pid"])
        except (psutil.Error, OSError):
            continue
    return mine


def launch(open_only=False):
    command = [str(core.venv_python(ROOT)), str(ROOT / "camping_watch.py"), "--auto-start"]
    if open_only:
        command.append("--open-only")
    (ROOT / "data").mkdir(exist_ok=True)
    log = open(ROOT / "data" / "watcher.log", "a", encoding="utf-8")
    flags = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
             if os.name == "nt" else {"start_new_session": True})
    subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **flags)


def interrupt(pids):
    import psutil
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            if os.name != "nt":
                os.kill(pid, signal.SIGINT)  # same as Ctrl+C: the watcher shuts down cleanly
                continue
            family = proc.children(recursive=True)  # Windows has no clean Ctrl+C for detached jobs: stop the tree
            proc.terminate()
            for child in family:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
        except psutil.Error:
            continue


def read_state(path=STATE):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status_text(pids, state):
    head = "🟢 감시 실행 중" if pids else "⚪ 감시 꺼짐"
    if state.get("active"):
        label = "현재 실행의 예약 진행 기록" if pids else "이전 실행의 진행 기록(다음 실행 시 보관 후 초기화)"
        head += f"\n⚠️ {label}: {state['active'].get('checkin')} {state['active'].get('site')}"
        head += "\n실제 예약 접수 여부는 사이트에서 확인하세요."
    dates = ", ".join(f"{d[5:]} {r}" for d, r in list(state.get("dates", {}).items())[:8])
    return "\n".join(x for x in (head, f"단계: {state.get('phase', '-')} · {state.get('updated_at', '')[:19]}", dates) if x)


def handle(action, pids=watcher_pids, start=launch, stop=interrupt, state=read_state):
    """Return the reply for one action. Never stops or restarts anything while a reservation is in progress."""
    running, current = pids(), state()
    if action == "help":
        return HELP
    if action == "status":
        return status_text(running, current)
    if action in ("start", "open"):
        if running:
            return "이미 감시가 실행 중입니다. '상태'로 확인하세요."
        start(open_only=action == "open")
        return "🏕️ 감시를 시작합니다. 컴퓨터에서 브라우저가 열립니다. 곧 '감시 시작' 알림이 옵니다." + (
            "\n(오늘 열리는 날짜만 감시)" if action == "open" else "")
    if action == "stop":
        if not running:
            return "감시가 꺼져 있습니다."
        if current.get("active"):
            return "예약 진행 중이라 끄지 않습니다. 컴퓨터에서 직접 마무리하세요."
        stop(running)
        return "감시를 끕니다."
    return None


def from_owner(update, chat_id):
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    return chat.get("type") == "private" and str(chat.get("id")) == str(chat_id) and not sender.get("is_bot")


def main():
    if sys.stderr is None:  # pythonw has no console: keep the log in a file
        (ROOT / "data").mkdir(exist_ok=True)
        sys.stderr = sys.stdout = open(ROOT / "data" / "remote.log", "a", encoding="utf-8", buffering=1)
    credentials = core.load_telegram(ROOT / ".env")
    token, chat_id = credentials.get("TELEGRAM_BOT_TOKEN"), credentials.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("텔레그램 설정(.env)이 없습니다. telegram_setup.py를 먼저 실행하세요.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    backlog = core.telegram_call(token, "getUpdates", {"offset": -1, "timeout": 0})
    offset = backlog[-1]["update_id"] + 1 if backlog else None  # never run commands sent while this was off
    core.notify(credentials, "📱 캠핑 리모컨이 켜졌습니다. '도움말'을 보내 보세요.")
    while not core.STOP:
        try:
            params = {"timeout": 8, **({"offset": offset} if offset else {})}
            updates = core.telegram_call(token, "getUpdates", params)
        except RuntimeError as exc:
            logging.warning("메시지 조회 실패: %s", exc)
            time.sleep(30 if "409" in str(exc) else 5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            if not from_owner(update, chat_id):
                logging.info("본인 개인 대화가 아닌 메시지는 무시합니다.")
                continue
            action = parse_command(update["message"].get("text"))
            if action:
                logging.info("명령: %s", action)
                core.notify(credentials, handle(action))


if __name__ == "__main__":
    signal.signal(signal.SIGINT, core.stop)
    signal.signal(signal.SIGTERM, core.stop)
    main()
