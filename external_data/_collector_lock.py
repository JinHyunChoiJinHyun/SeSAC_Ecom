"""수집기 중복 실행 방지 잠금.

대시보드 수동 실행과 작업 스케줄러 예약 실행이 같은 CSV를 동시에 쓰지 않도록,
수집기마다 data/.locks/<이름>.lock 파일에 배타 잠금을 겁니다.
잠금은 프로세스가 끝나면(정상·오류·강제 종료 모두) 운영체제가 자동으로 풉니다.

사용: 수집기 스크립트의 `if __name__ == "__main__":` 첫 줄에서 hold("naver") 호출
이미 다른 곳에서 실행 중이면 안내를 출력하고 종료 코드 SKIP_EXIT_CODE(75)로 끝납니다.
"""

from __future__ import annotations

import os
from pathlib import Path

LOCK_DIR = Path(__file__).resolve().parent / "data" / ".locks"
SKIP_EXIT_CODE = 75
_held = []  # 프로세스가 끝날 때까지 잠금 파일을 열어 둡니다.


def _try_lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass


def hold(name: str) -> None:
    """이 수집기의 잠금을 잡습니다. 이미 실행 중이면 안내 후 종료합니다."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_DIR / f"{name}.lock", "a+")
    if not _try_lock(handle):
        handle.close()
        print(f"[건너뜀] {name} 수집이 다른 곳(예약 실행 또는 대시보드)에서 이미 실행 중입니다. 이번 실행은 건너뜁니다.", flush=True)
        raise SystemExit(SKIP_EXIT_CODE)
    _held.append(handle)


def is_running(name: str) -> bool:
    """다른 프로세스가 이 수집기 잠금을 잡고 있으면 True."""
    path = LOCK_DIR / f"{name}.lock"
    if not path.exists():
        return False
    try:
        with open(path, "a+") as handle:
            if _try_lock(handle):
                _unlock(handle)
                return False
            return True
    except OSError:
        return False
