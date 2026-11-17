"""API 키 인증 및 사용자별 리소스 접근 검증."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _keys():
    raw = os.getenv("CART_SIGNAL_API_KEYS", "")
    try:
        keys = json.loads(raw)
    except (ValueError, TypeError):
        keys = None
    if not isinstance(keys, dict) or not keys or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or len(v) < 32
        for k, v in keys.items()
    ) or len(set(keys.values())) != len(keys):
        raise HTTPException(503, "API 인증 설정이 유효하지 않습니다.")
    return keys


def authenticated_user(api_key: str | None = Security(_header)) -> str:
    keys = _keys()
    if not api_key:
        raise HTTPException(401, "API 키가 필요합니다.", headers={"WWW-Authenticate": "ApiKey"})
    given = hashlib.sha256(api_key.encode()).digest()
    found = None
    for user, key in keys.items():
        if hmac.compare_digest(given, hashlib.sha256(key.encode()).digest()):
            found = user
    if found is None:
        raise HTTPException(401, "API 키가 유효하지 않습니다.", headers={"WWW-Authenticate": "ApiKey"})
    return found


def owned_thread(user: str, thread_id: str | None = None) -> str:
    # 사용자 ID 자체가 thread_id로 노출되지 않게 해시값 사용
    expected = "api:" + hashlib.sha256(user.encode()).hexdigest()
    if thread_id is not None and not hmac.compare_digest(thread_id, expected):
        raise HTTPException(404, "대화를 찾지 못했습니다.")
    return expected


def _file_secret() -> bytes:
    secret = os.getenv("CART_SIGNAL_FILE_SIGNING_KEY", "")
    if len(secret) < 32:
        raise HTTPException(503, "파일 서명 키 설정이 유효하지 않습니다.")
    return secret.encode()


def signed_file_url(user: str, kind: str, name: str) -> str:
    # 인증된 사용자에게만 제공하는 최대 1시간 유효 다운로드 URL
    expires = int(time.time()) + 3600
    key = _file_secret()
    payload = f"{user}\n{kind}\n{name}\n{expires}".encode()
    signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return f"/files/{quote(kind, safe='')}/{quote(name, safe='')}?expires={expires}&signature={signature}"


def verify_file(user: str, kind: str, name: str, expires: int, signature: str) -> None:
    if not 0 <= expires-int(time.time()) <= 3600:
        raise HTTPException(404, "파일을 찾지 못했습니다.")
    payload = f"{user}\n{kind}\n{name}\n{expires}".encode()
    expected = hmac.new(_file_secret(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(404, "파일을 찾지 못했습니다.")
