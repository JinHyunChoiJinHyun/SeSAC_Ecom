"""Backend API 호출 (아키텍처 [0]: Agent는 DB에 직접 붙지 않고 backend API만 호출).

백엔드는 팀원이 만드는 중입니다. 이 파일은 '연결 자리'로, 아래 환경변수가 있을 때만 HTTP를 씁니다.
    CART_SIGNAL_BACKEND_URL       예: http://127.0.0.1:8000  (prefix가 있으면 포함: http://…/api/v1)
    CART_SIGNAL_BACKEND_API_KEY   있으면 X-API-Key 헤더로 전달   (지금 backend/api/security.py 방식)
    CART_SIGNAL_BACKEND_TOKEN     있으면 Authorization: Bearer 로 전달 (아키텍처 [4] Agent 토큰 방식)
    CART_SIGNAL_BACKEND_TIMEOUT   초, 기본 30
값은 루트 .env에서 읽습니다. 설정이 없으면 enabled()=False → 각 Tool이 로컬 모드로 동작합니다.

응답은 ToolResult로 맞춥니다: {"available", "status", "source", "data", "evidence"}
    - 200 응답에 "data"가 있으면 그대로, 없으면 응답 전체를 data로 감쌉니다.
    - 503(분석 DB 갱신 중 등)·4xx·네트워크 오류는 예외 대신 available=False로 돌려 Agent가 안내하게 합니다.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def base_url() -> str:
    return os.getenv("CART_SIGNAL_BACKEND_URL", "").strip().rstrip("/")


def enabled() -> bool:
    return bool(base_url())


def _headers(extra: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.getenv("CART_SIGNAL_BACKEND_API_KEY", "").strip()
    token = os.getenv("CART_SIGNAL_BACKEND_TOKEN", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update({k: v for k, v in (extra or {}).items() if v})
    return headers


def _origin(url: str) -> tuple:
    parts = urllib.parse.urlsplit(url)
    port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """같은 오리진(scheme·host·port)으로의 리디렉션만 따라갑니다 (CodeRabbit 리뷰).

    기본 처리기는 다른 서버로 리디렉션돼도 X-API-Key·Authorization 헤더를 그대로 복사하므로
    오리진이 바뀌면 따라가지 않고 HTTP 오류로 돌려줍니다.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if _origin(target) != _origin(req.full_url):
            raise urllib.error.HTTPError(req.full_url, code, "다른 오리진으로의 리디렉션 거부", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameOriginRedirect)


def as_tool_result(body, source: str = "") -> dict:
    if isinstance(body, dict) and "data" in body:
        result = dict(body)
    else:
        result = {"data": body}
    result.setdefault("available", True)
    result.setdefault("status", "연결됨 (백엔드)")
    result.setdefault("source", source)
    result.setdefault("evidence", [])
    return result


def call(method: str, path: str, *, params: dict | None = None, body: dict | None = None,
         source: str = "", headers: dict | None = None) -> dict:
    """백엔드 API 1회 호출 → ToolResult. 실패해도 예외를 내지 않습니다."""
    if not enabled():
        return {"available": False, "status": "백엔드 미연결 (CART_SIGNAL_BACKEND_URL 없음)", "source": source,
                "data": {}, "evidence": []}
    url = base_url() + path
    if params:
        clean = {k: (",".join(map(str, v)) if isinstance(v, (list, tuple)) else v)
                 for k, v in params.items() if v not in (None, "", [])}
        url += "?" + urllib.parse.urlencode(clean)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method.upper(), headers=_headers(headers))
    timeout_raw = os.getenv("CART_SIGNAL_BACKEND_TIMEOUT", "30").strip() or "30"
    try:
        timeout = float(timeout_raw)
        if not 0 < timeout < float("inf"):
            raise ValueError("유효하지 않은 timeout")
    except (ValueError, OverflowError):
        return {
            "available": False,
            "status": "백엔드 타임아웃 설정 오류",
            "source": source,
            "data": {},
            "evidence": [],
        }
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return as_tool_result(json.loads(response.read().decode("utf-8") or "{}"), source)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        reason = {503: "분석 데이터 갱신 중이거나 백엔드 준비 안 됨", 401: "인증 실패", 403: "권한 없음",
                  404: "API 없음 (백엔드에 아직 없는 기능)", 409: "지금 단계에서 허용되지 않음"}.get(exc.code, "백엔드 오류")
        if 300 <= exc.code < 400:
            reason = "다른 주소로의 리디렉션 거부 (인증 정보 보호)"
        return {"available": False, "status": f"{reason} (HTTP {exc.code}){': ' + str(detail) if detail else ''}",
                "source": source, "data": {}, "evidence": []}
    except Exception as exc:  # 네트워크·시간 초과·JSON 오류
        return {"available": False, "status": f"백엔드 호출 실패 ({type(exc).__name__})", "source": source,
                "data": {}, "evidence": []}
