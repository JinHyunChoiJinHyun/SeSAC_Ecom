"""표준 라이브러리 HTTP 서버로 관리자 인증, 정적 화면, 기록 조회와 모델 설정 API를 제공합니다."""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .service import Dashboard, Settings, export_csv, select_runs, summary
from env.model_settings import get_store, ModelSettingsError

# 브라우저에 제공할 정적 파일의 기준 폴더입니다.
STATIC = Path(__file__).parent / "static"


def date_context():
    """로그의 로컬 접수 시각과 같은 서버 달력 날짜를 화면에 제공합니다."""
    now = datetime.now().astimezone()
    # naive datetime.timestamp()가 서버의 로컬 시간대와 다음 날의 DST도 적용합니다.
    midnight = datetime.combine(now.date() + timedelta(days=1), time.min)
    offset = now.strftime("%z")
    return {"today": now.date().isoformat(), "timezone": f"UTC{offset[:3]}:{offset[3:]}",
            "rollover_ms": max(1000, int((midnight.timestamp() - now.timestamp()) * 1000) + 250)}


# =========================================================
# 인증 설정 및 HTTP 서버 생성
# =========================================================

def build_server(settings: Settings, host="127.0.0.1", port=8000, username="", password=""):
    """인증 설정을 검증하고 요청별 핸들러를 사용하는 HTTP 서버를 생성합니다."""
    # 계정 일부만 설정하거나 외부 접속에 인증이 없으면 시작하지 않습니다.
    if bool(username) != bool(password):
        raise ValueError("관리자 아이디와 비밀번호를 모두 설정해 주세요.")
    if host not in {"127.0.0.1", "localhost", "::1"} and not username:
        raise ValueError("외부 접속에는 관리자 인증 설정이 필요합니다.")
    dashboard = Dashboard(settings)
    model_store = get_store(settings.project)

    class Handler(BaseHTTPRequestHandler):
        """GET으로 화면·기록·결과 파일·모델 설정을 조회하고 POST로 모델 설정을 변경합니다."""
        server_version = "EcomAdmin/1.0"

        def log_message(self, format, *args):
            """URL에 포함될 수 있는 질문·검색어의 기본 접근 로그 출력을 생략합니다."""
            pass  # 질문·검색어가 웹 서버 콘솔에 노출되지 않습니다.

        def send_data(self, data, content_type="application/json; charset=utf-8", status=200, disposition=None):
            """본문을 전송하고 캐시 방지, 콘텐츠 형식과 보안 헤더를 설정합니다."""
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            if status == 401:
                self.send_header("WWW-Authenticate", 'Basic realm="Ecom Admin", charset="UTF-8"')
            self.end_headers()
            self.wfile.write(data)

        def error(self, status, message):
            """오류 응답을 detail 필드가 있는 JSON 형식으로 통일합니다."""
            self.send_data(json.dumps({"detail": message}, ensure_ascii=False), status=status)

        def authorized(self):
            """관리자 계정을 확인하거나 계정 미설정 시 루프백 접속만 허용합니다."""
            # 계정을 생략한 구성에서는 루프백 주소의 요청만 허용합니다.
            if not username:
                return self.client_address[0] in {"127.0.0.1", "::1"}
            raw = self.headers.get("Authorization", "")
            try:
                scheme, token = raw.split(" ", 1)
                user, key = base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)
                # 인증 문자열 비교에는 compare_digest를 사용합니다.
                return scheme.lower() == "basic" and secrets.compare_digest(user.encode(), username.encode()) and secrets.compare_digest(key.encode(), password.encode())
            except (ValueError, UnicodeError):
                return False

        def do_GET(self):
            """인증 후 라우팅하고 입력·파일·서버 오류를 HTTP 응답으로 변환합니다."""
            if not self.authorized():
                self.error(401, "관리자 인증이 필요합니다.")
                return
            try:
                self.route()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, TypeError):
                self.error(422, "날짜·페이지·상태 필터 값을 확인해 주세요.")
            except OSError:
                self.error(503, "기록 또는 파일을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.")
            except Exception:
                self.error(500, "기록 조회 중 오류가 발생했습니다.")

        def do_POST(self):
            """관리자 접근 권한과 출처 헤더를 확인한 JSON 요청으로 모델 설정을 적용합니다."""
            if not self.authorized():
                return self.error(401, "관리자 인증이 필요합니다.")
            if urlsplit(self.path).path != "/api/model-settings":
                return self.error(404, "페이지를 찾지 못했습니다.")
            # Sec-Fetch-Site가 cross-site이거나 Origin의 스킴·호스트가 허용되지 않으면 차단합니다.
            # 두 출처 헤더가 없는 요청도 관리자 접근 권한 검사 후 허용합니다.
            origin = self.headers.get("Origin")
            if self.headers.get("Sec-Fetch-Site") == "cross-site" or (
                origin and (urlsplit(origin).scheme not in {"http", "https"}
                            or urlsplit(origin).netloc != self.headers.get("Host"))
            ):
                return self.error(403, "다른 출처에서는 모델 설정을 변경할 수 없습니다.")
            if self.headers.get_content_type() != "application/json":
                return self.error(415, "JSON 형식으로 모델 설정을 보내 주세요.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError()
                selection = json.loads(self.rfile.read(length).decode("utf-8"))
                model_store.apply(selection)
                # 공개 응답에는 API 키를 포함하지 않습니다.
                return self.send_data(json.dumps(model_store.public_status(), ensure_ascii=False))
            except ModelSettingsError as exc:
                return self.error(422, str(exc))
            except (ValueError, UnicodeError):
                return self.error(400, "모델 설정 요청을 확인해 주세요.")
            except OSError:
                return self.error(503, "모델 설정을 저장하지 못했습니다.")

        def route(self):
            """GET URL을 해석해 날짜 기준, 화면, 기록·모델 설정, CSV와 파일 조회로 분기합니다."""
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            get = lambda key, default="": query.get(key, [default])[0]
            if path == "/api/date-context":
                return self.send_data(json.dumps(date_context(), ensure_ascii=False))
            if path == "/api/model-settings":
                try:
                    return self.send_data(json.dumps(model_store.public_status(), ensure_ascii=False))
                except ModelSettingsError as exc:
                    return self.error(503, str(exc))
            if path == "/":
                return self.send_data((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/assets/"):
                name = path.removeprefix("/assets/")
                if name not in {"app.js", "styles.css", "favicon.svg"}:
                    return self.error(404, "파일을 찾지 못했습니다.")
                mime = {"app.js": "application/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}[name]
                return self.send_data((STATIC / name).read_bytes(), mime)
            # 목록 조회와 CSV 내보내기는 같은 검색·상태·날짜 조건을 사용합니다.
            if path in {"/api/requests", "/api/export.csv"}:
                q, status, start, end = get("q"), get("status", "all"), get("start"), get("end")
                if len(q) > 300 or status not in {"all", "completed", "failed", "running", "unverified", "unfinished", "unknown"}:
                    raise ValueError()
                for value in (start, end):
                    if value and date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                if start and end and start > end:
                    raise ValueError()
                snap = dashboard.snapshot()
                runs = select_runs(snap["runs"], q, status, start, end)
                if path.endswith(".csv"):
                    return self.send_data(export_csv(runs), "text/csv; charset=utf-8", disposition='attachment; filename="ecom-requests.csv"')
                # 요약 통계는 전체 필터 결과로 계산하고 목록에만 페이지 범위를 적용합니다.
                page, size = int(get("page", "1")), int(get("page_size", "12"))
                if page < 1 or not 1 <= size <= 100:
                    raise ValueError()
                return self.send_data(json.dumps({"mode": snap["mode"], "notices": snap["notices"], "log_files": snap["log_files"], "total": len(runs), "page": page, "page_size": size, "summary": summary(runs), "items": [dashboard.public_run(r) for r in runs[(page-1)*size:page*size]]}, ensure_ascii=False))
            if path.startswith("/api/requests/"):
                run_id = path.removeprefix("/api/requests/")
                run = next((r for r in dashboard.snapshot()["runs"] if r["id"] == run_id), None)
                if not run:
                    return self.error(404, "요청을 찾지 못했습니다.")
                return self.send_data(json.dumps(dashboard.public_run(run, detail=True), ensure_ascii=False))
            if path.startswith("/api/files/"):
                parts = path.removeprefix("/api/files/").split("/")
                if len(parts) != 2:
                    return self.error(404, "파일을 찾지 못했습니다.")
                run = next((r for r in dashboard.snapshot()["runs"] if r["id"] == parts[0]), None)
                # 해당 요청에 기록되고 경로 검증을 통과한 파일만 선택합니다.
                refs = [ref for raw in run.get("files", []) if (ref := dashboard.file_ref(raw))] if run else []
                ref = next((ref for ref in refs if ref["id"] == parts[1]), None)
                if not ref:
                    return self.error(404, "파일을 찾지 못했습니다.")
                file = (settings.project.resolve() / ref["relative"]).resolve()
                # 파일을 열기 직전에도 허용된 위치와 확장자를 확인합니다.
                if not dashboard.file_ref(str(file)):
                    return self.error(404, "파일을 찾지 못했습니다.")
                from urllib.parse import quote
                # 이미지는 기본 미리보기로 제공하고 다운로드 요청은 첨부 응답으로 보냅니다.
                inline = ref["kind"] in {"png", "jpg", "jpeg"} and get("download") != "true"
                disposition = ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(ref["name"])
                mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(file.stat().st_size))
                self.send_header("Content-Disposition", disposition)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with file.open("rb") as source:
                    # 파일 전체를 메모리에 올리지 않고 64KB씩 전송합니다.
                    while chunk := source.read(65536):
                        self.wfile.write(chunk)
                return
            self.error(404, "페이지를 찾지 못했습니다.")

    return ThreadingHTTPServer((host, port), Handler)
