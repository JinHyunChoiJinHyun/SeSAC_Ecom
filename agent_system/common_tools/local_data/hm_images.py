"""H&M 상품 이미지 조회 (로컬 모드 전용, 읽기 전용).

이미지 파일은 DB에 넣지 않습니다. DB에는 "상품 ID → 파일 경로" 인덱스만 있습니다.

경로 인덱스  hm.article_image_paths  (db_setup.py가 이미지 폴더를 훑어 만듦)
    article_id   VARCHAR  상품 ID (= 이미지 파일명) → hm.articles.article_id, 상품당 최대 1개
    image_path   VARCHAR  이미지 폴더 기준 상대 경로 (예: 010/0108775015.jpg)
    file_bytes   BIGINT   인덱스를 만들 때 확인한 파일 크기

이미지 폴더 위치 (앞쪽 우선)
    1. 환경변수 CART_SIGNAL_HM_IMAGE_DIR
    2. 기본: 프로젝트/h-and-m-parquet/images (폴더가 있을 때)
    3. 인덱스를 만들 때 쓴 폴더 (db_setup.py가 테이블 주석 image_root=...에 기록, 프로젝트 밖 폴더를 쓴 경우 대비)

이미지 상태 (image_status)
    있음                     파일을 찾음
    원본에 이미지 없음        H&M 원본에 이미지가 없는 상품 (약 442개) → 이미지 기반 기능에서 제외
    이미지 파일을 찾지 못함   인덱스에는 있는데 폴더에 파일이 없음 (폴더 위치 확인)
    경로 테이블 없음          db_setup.py로 인덱스를 아직 만들지 않음 → Kaggle 파일명 규칙으로만 확인

LLM·Slack에 보내는 값은 상대 경로·상태뿐입니다. 실제 파일 위치(image_file)는 보고서 등 코드에서만 씁니다.
백엔드가 생기면 같은 일을 backend /products/{article_id} (articles + article_image_paths)가 합니다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from agent_system.common_tools.local_data import hm_products as HM

SCHEMA = "hm"
TABLE = "article_image_paths"
QUALIFIED = f"{SCHEMA}.{TABLE}"
ENV_DIR = "CART_SIGNAL_HM_IMAGE_DIR"
DEFAULT_DIR = HM.PROJECT_ROOT / "h-and-m-parquet" / "images"
_ID = re.compile(r"^\d{10}$")

STATUS_OK = "있음"
STATUS_NO_IMAGE = "원본에 이미지 없음 (이미지 기반 기능에서 제외)"
STATUS_FILE_MISSING = "이미지 파일을 찾지 못함 (이미지 폴더 위치 확인: CART_SIGNAL_HM_IMAGE_DIR)"
STATUS_NO_TABLE = "경로 테이블 없음 (db_setup.py --dataset hm --overwrite로 생성)"


def _normalize(article_id) -> str:
    value = str(article_id or "").strip()
    return value.zfill(10) if value.isdigit() and len(value) < 10 else value


def rule_path(article_id: str) -> str:
    """Kaggle 원본 폴더 규칙: {article_id 앞 3자리}/{article_id}.jpg"""
    return f"{article_id[:3]}/{article_id}.jpg"


# ---------------------------------------------------------------- 경로 인덱스
def _load_index() -> dict:
    """{"table": bool, "paths": {article_id: image_path}, "root": 기록된 폴더}. DB 파일이 바뀌면 다시 읽습니다."""
    def load():
        found = HM._query("SELECT comment FROM duckdb_tables() WHERE schema_name = ? AND table_name = ?",
                          [SCHEMA, TABLE])
        if found.empty:
            return {"table": False, "paths": {}, "root": ""}
        comment = str(found["comment"].iloc[0] or "")
        frame = HM._query(f"SELECT article_id, image_path FROM {QUALIFIED}")
        return {"table": True,
                "paths": dict(zip(frame["article_id"].astype(str), frame["image_path"].astype(str))),
                "root": comment.split("image_root=", 1)[1].strip() if "image_root=" in comment else ""}

    if not HM.status()["available"]:
        return {"table": False, "paths": {}, "root": ""}
    return HM._cached("image_index", load)


def image_root() -> Path:
    """이미지 폴더: 환경변수 → 프로젝트 기본 폴더(있을 때) → 인덱스 생성 때 쓴 폴더(있을 때) → 기본 폴더."""
    if os.getenv(ENV_DIR):
        return Path(os.environ[ENV_DIR]).expanduser()
    if DEFAULT_DIR.is_dir():
        return DEFAULT_DIR
    recorded = _load_index().get("root") or ""
    if recorded and Path(recorded).is_dir():
        return Path(recorded)
    return DEFAULT_DIR


def _inside(root: Path, relative: str) -> Path | None:
    """root 밖을 가리키는 경로(../ 등)는 쓰지 않습니다."""
    try:
        base = root.resolve()
        path = (base / relative).resolve()
    except OSError:
        return None
    return path if base == path or base in path.parents else None


# ---------------------------------------------------------------- 조회
def lookup(article_ids) -> dict[str, dict]:
    """여러 상품의 이미지 상태 (LLM·표에 넣어도 되는 값만).

    반환 {article_id: {"image_available", "image_path", "image_status"}}
    """
    index = _load_index()
    root = image_root()
    out = {}
    for raw in article_ids or []:
        article_id = _normalize(raw)
        if not _ID.match(article_id):
            out[article_id] = {"image_available": False, "image_path": None, "image_status": "상품 ID 형식 아님"}
            continue
        if index["table"]:
            relative = index["paths"].get(article_id)
            if relative is None:
                out[article_id] = {"image_available": False, "image_path": None, "image_status": STATUS_NO_IMAGE}
                continue
            status_missing = STATUS_FILE_MISSING
        else:
            relative, status_missing = rule_path(article_id), STATUS_NO_TABLE
        path = _inside(root, relative)
        ok = bool(path and path.is_file())
        out[article_id] = {"image_available": ok, "image_path": relative,
                           "image_status": STATUS_OK if ok else status_missing}
    return out


def image_info(article_id) -> dict:
    article_id = _normalize(article_id)
    return lookup([article_id])[article_id]


def image_file(article_id) -> Path | None:
    """실제 이미지 파일 위치 (보고서·첨부용). 없으면 None. LLM에 넘기지 마십시오."""
    info = image_info(article_id)
    if not info["image_available"]:
        return None
    return _inside(image_root(), info["image_path"])


def image_files(article_ids, limit: int = 6) -> list[dict]:
    """보고서용: [{"article_id", "path"}] (파일이 있는 상품만, 순서 유지, 최대 limit개)."""
    out = []
    for raw in article_ids or []:
        path = image_file(raw)
        if path is not None and all(item["article_id"] != _normalize(raw) for item in out):
            out.append({"article_id": _normalize(raw), "path": str(path)})
        if len(out) >= limit:
            break
    return out


def status() -> dict:
    """연결 상태 한 줄 (data_status·안내용)."""
    if not HM.status()["available"]:
        return {"available": False, "status": "미연결 (H&M DB 없음)"}
    index = _load_index()
    root = image_root()
    if not index["table"]:
        state = f"경로 테이블 없음 — 파일명 규칙으로만 확인 (폴더 {'있음' if root.is_dir() else '없음'})"
        return {"available": root.is_dir(), "status": state}
    if not root.is_dir():
        return {"available": False, "status": f"{QUALIFIED} {len(index['paths']):,}개, 이미지 폴더 없음 ({ENV_DIR} 확인)"}
    return {"available": True, "status": f"{QUALIFIED} {len(index['paths']):,}개, 이미지 폴더 연결됨"}
