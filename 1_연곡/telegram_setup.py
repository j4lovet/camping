"""Interactive private-chat setup. Tokens are entered locally, not in AI chat."""
import getpass
import os
import re
import sys
import tempfile
from pathlib import Path
from camping_common import telegram_call

ROOT = Path(__file__).resolve().parent


def private_chats(updates):
    chats = {}
    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        sender = message.get("from", {})
        if chat.get("type") == "private" and not sender.get("is_bot") and chat.get("id"):
            chats[str(chat["id"])] = chat
    return chats


def write_env(path, token, chat_id):
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"\d+", str(chat_id)):
        raise ValueError("봇 토큰 또는 개인 채팅 ID 형식 확인 필요")
    keep = [line for line in (path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else [])
            if not line.startswith(("TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=")) and line.strip()]
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        f.write("\n".join([f'TELEGRAM_BOT_TOKEN="{token}"', f'TELEGRAM_CHAT_ID="{chat_id}"', *keep]) + "\n")
    os.replace(f.name, path)  # mkstemp permissions: owner read/write only.


def auto_setup(token, destination, chat_id=None):
    """비대화형 연결(코덱스/AI가 대신 실행): 토큰 확인 -> 개인 대화 확인 -> 테스트 전송 -> .env 저장. 성공 시 True."""
    token = token.strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        print("토큰 형식이 올바르지 않습니다. 숫자:영문 형태의 전체 토큰을 다시 확인하세요.")
        return False
    bot = telegram_call(token, "getMe")
    print(f"확인된 봇: @{bot.get('username', '')}")
    chats = private_chats(telegram_call(token, "getUpdates", {"timeout": 0}))
    if chat_id:
        chats = {k: v for k, v in chats.items() if k == str(chat_id)}
    if not chats:
        print("이 봇에 보낸 개인 메시지가 없습니다. 폰 텔레그램에서 내 봇을 열고 /start 를 보낸 뒤 다시 실행하세요.")
        return False
    if len(chats) > 1:
        for cid, chat in chats.items():
            print(f"  - {chat.get('first_name', '')} @{chat.get('username', '')} / ID {cid}")
        print("대화가 여러 개입니다. 본인 ID를 --chat-id 로 지정해 다시 실행하세요.")
        return False
    cid, chat = next(iter(chats.items()))
    telegram_call(token, "sendMessage", {"chat_id": cid, "text": "🏕️ 캠핑 알림 연결 테스트입니다. 이 메시지가 보이면 성공!"})
    write_env(destination, token, cid)
    print(f"저장했습니다. {chat.get('first_name', '')}님 대화로 테스트 메시지를 보냈습니다. 폰에서 도착했는지 확인하세요.")
    return True


def main():
    if "--token" in sys.argv:
        args = sys.argv
        token = args[args.index("--token") + 1] if args.index("--token") + 1 < len(args) else ""
        chat = args[args.index("--chat-id") + 1] if "--chat-id" in args and args.index("--chat-id") + 1 < len(args) else None
        try:
            ok = auto_setup(token, ROOT / ".env", chat)
        except RuntimeError as exc:
            print(f"연결 실패: {exc}. 토큰이 틀렸거나(BotFather가 준 전체 토큰인지 확인) 인터넷 연결 문제입니다.")
            raise SystemExit(1)
        if not ok:
            raise SystemExit(1)
        return
    print("본인의 @BotFather에서 /newbot으로 만든 새 봇을 사용하세요. 토큰을 AI 채팅에 붙이지 마세요.")
    destination = ROOT / ".env"
    if destination.exists() and input("기존 .env 설정을 교체할까요? 교체하려면 YES: ").strip() != "YES":
        return
    token = getpass.getpass("본인 봇 토큰 (입력 내용은 화면에 표시되지 않음): ").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise ValueError("토큰 형식을 확인하세요")
    bot = telegram_call(token, "getMe")
    print(f"확인된 봇: @{bot.get('username', '')}")
    input("텔레그램에서 이 봇의 개인 대화창에 /start를 보낸 다음 여기서 Enter: ")
    chats = list(private_chats(telegram_call(token, "getUpdates", {"timeout": 0})).values())
    if not chats:
        print("개인 메시지가 없습니다. /start를 다시 보내고 재실행하세요. 다른 getUpdates 프로그램은 중지하세요.")
        return
    for index, chat in enumerate(chats, 1):
        print(f"{index}. {chat.get('first_name', '')} @{chat.get('username', '')} / ID {chat['id']}")
    choice = input("본인 대화의 번호를 선택하세요: ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(chats):
        raise ValueError("선택 번호를 확인하세요")
    chat = chats[int(choice)-1]
    if input(f"이 대화(ID {chat['id']})에 테스트 알림을 보낼까요? YES: ").strip() != "YES":
        return
    telegram_call(token, "sendMessage", {"chat_id": chat["id"], "text": "🏕️ 캠핑 알림 연결 테스트입니다. 본인 계정으로 도착했는지 확인하세요."})
    if input("본인 텔레그램에 도착했나요? 저장하려면 YES: ").strip() != "YES":
        return
    write_env(destination, token, str(chat["id"]))
    print("이 폴더의 .env에 저장했습니다. 이제 Start Camping.bat를 실행하세요.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(f"설정 실패: {exc}")
        print("409 오류: 다른 메시지 조회 프로그램/webhook과 충돌할 수 있습니다. 기존 연결은 자동 삭제하지 않습니다.")
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print("\n설정을 취소했습니다.")
