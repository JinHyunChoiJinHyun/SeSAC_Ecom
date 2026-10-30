from __future__ import annotations

import base64
import json
import mimetypes
import secrets
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .service import Dashboard, Settings, export_csv, select_runs, summary

STATIC = Path(__file__).parent / "static"


def build_server(settings: Settings, host="127.0.0.1", port=8000, username="", password=""):
    if bool(username) != bool(password):
        raise ValueError("관리자 아이디와 비밀번호를 모두 설정해 주세요.")
    if host not in {"127.0.0.1", "localhost", "::1"} and not username:
        raise ValueError("외부 접속에는 관리자 인증 설정이 필요합니다.")
    dashboard = Dashboard(settings)

    class Handler(BaseHTTPRequestHandler):
        server_version = "EcomAdmin/1.0"

        def log_message(self, format, *args):
            pass  # 질문·검색어가 웹 서버 콘솔에 노출되지 않습니다.

        def send_data(self, data, content_type="application/json; charset=utf-8", status=200, disposition=None):
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
            self.send_data(json.dumps({"detail": message}, ensure_ascii=False), status=status)

        def authorized(self):
            if not username:
                return self.client_address[0] in {"127.0.0.1", "::1"}
            raw = self.headers.get("Authorization", "")
            try:
                scheme, token = raw.split(" ", 1)
                user, key = base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)
                return scheme.lower() == "basic" and secrets.compare_digest(user.encode(), username.encode()) and secrets.compare_digest(key.encode(), password.encode())
            except (ValueError, UnicodeError):
                return False

        def do_GET(self):
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

        def route(self):
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            get = lambda key, default="": query.get(key, [default])[0]
            if path == "/":
                return self.send_data((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/assets/"):
                name = path.removeprefix("/assets/")
                if name not in {"app.js", "styles.css", "favicon.svg"}:
                    return self.error(404, "파일을 찾지 못했습니다.")
                mime = {"app.js": "application/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}[name]
                return self.send_data((STATIC / name).read_bytes(), mime)
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
                refs = [ref for raw in run.get("files", []) if (ref := dashboard.file_ref(raw))] if run else []
                ref = next((ref for ref in refs if ref["id"] == parts[1]), None)
                if not ref:
                    return self.error(404, "파일을 찾지 못했습니다.")
                file = (settings.project.resolve() / ref["relative"]).resolve()
                if not dashboard.file_ref(str(file)):
                    return self.error(404, "파일을 찾지 못했습니다.")
                from urllib.parse import quote
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
                    while chunk := source.read(65536):
                        self.wfile.write(chunk)
                return
            self.error(404, "페이지를 찾지 못했습니다.")

    return ThreadingHTTPServer((host, port), Handler)
