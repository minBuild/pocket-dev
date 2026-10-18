"""로그인 정보 설정 / 잠금 해제 (집 PC 에서 직접 실행).

  setup_login.bat  → 아이디·비밀번호 설정 + 새 액세스 토큰 발급 (.env 에 저장)
  unlock.bat       → 로그인 실패로 잠긴 공개 주소 잠금 해제
"""
import getpass
import secrets
import sys

from .auth import hash_password, load_state, unlock
from .config import ROOT

ENV_FILE = ROOT / ".env"


def write_env(updates: dict) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    done, out = set(), []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def setup() -> None:
    print("=== Pocket-Dev 로그인 설정 ===\n")
    login_id = input("아이디 [minbuild]: ").strip() or "minbuild"
    while True:
        password = getpass.getpass("비밀번호 (입력해도 화면에 안 보여요, 8자 이상): ")
        if len(password) < 8:
            print("  8자 이상으로 정해 주세요.\n")
            continue
        if password != getpass.getpass("비밀번호 한 번 더: "):
            print("  두 번 입력한 비밀번호가 달라요.\n")
            continue
        break
    token = secrets.token_urlsafe(12)
    write_env({"LOGIN_ID": login_id, "LOGIN_PASSWORD_HASH": hash_password(password), "ACCESS_TOKEN": token})
    print("\n저장했어요. 로그인할 때 필요한 정보:")
    print(f"  아이디      : {login_id}")
    print("  비밀번호    : (방금 정한 것)")
    print(f"  액세스 토큰 : {token}")
    print("\n액세스 토큰은 폰 메모 등 안전한 곳에 적어 두세요. 서버를 다시 켜야 적용됩니다.")


def do_unlock() -> None:
    state = load_state()
    if not state.get("locked"):
        print("잠겨 있지 않아요.")
        return
    print("최근 로그인 실패 기록:")
    for item in state.get("log", [])[-5:]:
        import time
        print(f"  {time.strftime('%m-%d %H:%M:%S', time.localtime(item['at']))}  {item['client']} ({item['via']})")
    unlock()
    print("\n잠금을 해제했어요. 공개 주소로 다시 로그인할 수 있습니다.")


if __name__ == "__main__":
    {"setup": setup, "unlock": do_unlock}.get(sys.argv[1] if len(sys.argv) > 1 else "setup", setup)()
