"""Small standalone helpers; no account identifiers or unrelated monitors."""
import json
import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
STOP = False


def venv_python(root, windowless=False):
    if os.name == "nt":
        return root / ".camping-venv" / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return root / ".camping-venv" / "bin" / "python"


def acquire_lock(path):
    """Exclusive non-blocking lock so two watchers never run at once. Raises OSError if already held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle


def recover_previous_state(path, lock):
    """Archive orphaned local progress only while the caller owns camping.lock.

    The OS releases that lock even after a crash. An `active` JSON entry alone
    describes a past UI attempt, not a live process or a confirmed reservation.
    This function does not inspect, cancel, or modify anything on the server.
    """
    if lock.closed:
        raise ValueError("실행 잠금이 있어야 이전 상태를 복구할 수 있습니다")
    if not path.exists():
        return None
    previous = json.loads(path.read_text(encoding="utf-8"))
    if not previous.get("active"):
        return None
    now = datetime.now(KST)
    archive_dir = path.parent / "state_history"
    archive_dir.mkdir(exist_ok=True)
    archive = archive_dir / f"{path.stem}_{now:%Y%m%d_%H%M%S_%f}.json"
    shutil.copy2(path, archive)  # preserve the original before clearing only local progress
    recovered = dict(previous)
    recovered.pop("active")
    recovered.update(phase="RECOVERED", previous_phase=previous.get("phase"),
                     updated_at=now.isoformat(), recovery_archive=archive.name)
    save(path, recovered)
    return archive


def keep_awake():
    """Windows: ask the system not to go to idle sleep while this process runs (the screen may still turn off)."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def stop(*_):
    global STOP
    STOP = True


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(f.name, path)


def load_telegram(env_file):
    values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
                values[key.strip()] = value.strip().strip("\"'")
    return values


def telegram_call(token, method, params=None):
    payload = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{token}/{method}", data=payload), timeout=12) as r:
            result = json.load(r)
    except urllib.error.HTTPError as exc:
        # Never include exception URL: it contains the token.
        raise RuntimeError(f"Telegram HTTP {exc.code}") from None
    except Exception:
        raise RuntimeError("Telegram 연결 실패 (네트워크/인증 확인)") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram 응답 실패")
    return result.get("result")


def notify(credentials, text):
    token, chat = credentials.get("TELEGRAM_BOT_TOKEN"), credentials.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logging.warning("텔레그램 미설정 · 화면 알림만 사용: %s", text)
        return
    try:
        telegram_call(token, "sendMessage", {"chat_id": chat, "text": text})
    except RuntimeError as exc:
        logging.error("알림 실패: %s", exc)


class ConsoleCommands:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False

    def start(self):
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            for line in sys.stdin:
                self.lines.put((time.monotonic(), line.strip().upper()))
        finally:
            self.closed = True

    def poll(self, after):
        while True:
            try:
                received, command = self.lines.get_nowait()
            except queue.Empty:
                return None
            if received >= after:
                return command
