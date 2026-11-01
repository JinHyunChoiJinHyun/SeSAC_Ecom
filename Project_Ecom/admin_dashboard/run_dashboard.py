from __future__ import annotations

import argparse
import getpass
import os
import re
import socket
import sys
import threading
import webbrowser
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from admin_dashboard.service import Settings
from admin_dashboard.standalone import build_server


def project_path(explicit=None):
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        parent = Path(__file__).resolve().parent.parent
        path = parent / "Project_Ecom" if (parent / "Project_Ecom").is_dir() else parent
    if not path.is_dir():
        raise ValueError("프로젝트 폴더가 없습니다.")
    if not (path / "slack_bot.py").is_file() and not (path / "logs").is_dir():
        raise ValueError("admin_app을 slack_bot.py가 있는 프로젝트 폴더 안에 넣어 주세요. 다른 위치라면 --project로 경로를 지정하세요.")
    return path


def local_addresses():
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET) if not info[4][0].startswith("127.")})
    except OSError:
        return []


def load_admin_env(project):
    """프로젝트 .env에서 관리자 설정 두 항목만 읽습니다."""
    path = project / "env" / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?(ECOM_ADMIN_USER|ECOM_ADMIN_PASSWORD)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                raise ValueError(".env 관리자 설정의 따옴표를 확인해 주세요.")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        os.environ.setdefault(key, value)


def main(argv=None):
    parser = argparse.ArgumentParser(description="SeSAC Ecom 관리자 대시보드 실행")
    parser.add_argument("--project", help="프로젝트 경로. 생략하면 자동 탐색")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--local-only", action="store_true", help="내 PC에서만 접속")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="서버 실행 없이 파일·경로 확인")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535 범위여야 합니다.")
    try:
        project = project_path(args.project)
    except ValueError as exc:
        parser.error(str(exc))
    for name in ("index.html", "styles.css", "app.js", "favicon.svg"):
        if not (Path(__file__).parent / "static" / name).is_file():
            parser.error(f"화면 파일이 없습니다: static/{name}. admin_app 폴더 전체를 복사해 주세요.")
    print(f"프로젝트: {project}", flush=True)
    print(f"로그 위치: {project / 'logs' / 'app.log'}", flush=True)
    if args.check:
        print("파일 및 프로젝트 경로 확인 완료. 서버는 실행하지 않았습니다.")
        return 0

    try:
        load_admin_env(project)
    except (OSError, UnicodeError, ValueError):
        parser.error("프로젝트 .env 관리자 설정을 읽지 못했습니다. 파일 인코딩과 따옴표를 확인해 주세요.")
    username = os.getenv("ECOM_ADMIN_USER", "").strip()
    password = os.getenv("ECOM_ADMIN_PASSWORD", "")
    if not args.local_only:
        if not username:
            username = input("관리자 아이디 [admin]: ").strip() or "admin"
        while not password:
            password = getpass.getpass("관리자 비밀번호 (입력 내용 숨김): ")
            if not password:
                print("빈 비밀번호는 사용할 수 없습니다.")
    elif not (username and password):
        username = password = ""

    try:
        server = build_server(Settings(project), host="127.0.0.1" if args.local_only else "0.0.0.0", port=args.port, username=username, password=password)
    except (OSError, ValueError) as exc:
        parser.error(f"서버 실행 실패: {exc}. 포트 사용 중이면 --port 8001로 실행하세요.")
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n내 PC 접속: {url}", flush=True)
    if not args.local_only:
        for ip in local_addresses():
            print(f"같은 네트워크 접속 후보: http://{ip}:{args.port}", flush=True)
        print("다른 PC는 같은 네트워크 연결 및 방화벽 허용이 필요합니다. 인터넷 공개용 설정은 아닙니다.", flush=True)
    print("브라우저 로그인에는 위에서 설정한 관리자 계정을 사용하세요. 종료: Ctrl+C", flush=True)
    timer = None
    if not args.no_browser:
        timer = threading.Timer(0.8, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n관리자 대시보드를 종료합니다.")
    finally:
        if timer:
            timer.cancel()
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n실행을 취소했습니다.")
