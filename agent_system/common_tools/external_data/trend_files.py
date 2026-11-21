"""네이버 쇼핑인사이트·유튜브 배치 CSV 읽기 (로컬 모드 전용, 읽기만 함).

배치 DB(PostgreSQL external: naver_*_trend · youtube_video_metric)가 완성되기 전까지
database/ 폴더에 둔 수집 파일을 읽어 shared_analysis가 받는 형식으로 바꿉니다.
DB가 완성되면 백엔드 API가 같은 계산 결과를 주므로 이 파일은 쓰지 않습니다.

네이버  database/naver/*.csv           (환경변수 CART_SIGNAL_NAVER_DIR)
    넓은 형식  날짜, <키워드1>, <키워드2>, …      예: shopping_daily.csv (날짜, 경량패딩)
    긴 형식    keyword, period, ratio (+ request_id, collected_at)
    → keyword, period, ratio, request_id(파일 이름), collected_at(열이 없으면 파일 수정 시각)
    파일 하나 = 네이버 요청 하나로 봅니다 (ratio는 같은 요청 안의 상대값이라 파일끼리 크기를 비교하지 않음).

유튜브  database/youtube/*.csv          (환경변수 CART_SIGNAL_YOUTUBE_DIR)
    키워드 후보  상품 카테고리, 키워드, 등장 채널 수, 근거 링크, 기준   예: keyword_candidates_20261115.csv
                 → category, keyword, channel_count, links, basis, collected_at(파일 이름의 날짜)
    영상 지표    keyword, video_id, title, channel_title, published_at, collected_at, view_count (있으면)
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATE_COLUMNS = ("날짜", "period", "date", "기간")
VIDEO_COLUMNS = {"keyword", "video_id", "published_at", "collected_at", "view_count"}
CANDIDATE_COLUMNS = {"상품 카테고리": "category", "키워드": "keyword", "등장 채널 수": "channel_count",
                     "근거 링크": "links", "기준": "basis"}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", str(text))


def _short(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name


def _mtime(path: Path) -> pd.Timestamp:
    return pd.Timestamp(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).tz_convert(None)


def naver_dir() -> Path:
    return Path(os.getenv("CART_SIGNAL_NAVER_DIR") or PROJECT_ROOT / "database" / "naver")


def youtube_dir() -> Path:
    return Path(os.getenv("CART_SIGNAL_YOUTUBE_DIR") or PROJECT_ROOT / "database" / "youtube")


def _csvs(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.csv")) if folder.is_dir() else []


# ---------------------------------------------------------------- 네이버
def _naver_frame(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    raw.columns = [_nfc(c).strip() for c in raw.columns]
    request_id, collected = _nfc(path.stem), _mtime(path)
    if {"keyword", "period", "ratio"} <= set(raw.columns):
        df = raw.copy()
    else:
        date_col = next((c for c in raw.columns if c in DATE_COLUMNS), raw.columns[0])
        df = raw.melt(id_vars=[date_col], var_name="keyword", value_name="ratio").rename(columns={date_col: "period"})
    df["keyword"] = df["keyword"].map(lambda k: _nfc(k).strip())
    if "request_id" not in df:
        df["request_id"] = request_id
    if "collected_at" not in df:
        df["collected_at"] = collected
    return df[["keyword", "period", "ratio", "request_id", "collected_at"]]


def naver_status() -> dict:
    files = _csvs(naver_dir())
    if not files:
        return {"available": False, "status": f"미연결 (네이버 CSV 없음: {_short(naver_dir())})"}
    keywords = naver_keywords()
    return {"available": True, "status": f"연결됨 (로컬 배치 CSV {len(files)}개, 키워드 {len(keywords)}개: "
                                         f"{', '.join(keywords[:10])})", "keywords": keywords}


def load_naver() -> pd.DataFrame:
    """모든 네이버 CSV → shared_analysis.market.trend_analysis 입력 형식."""
    parts = []
    for path in _csvs(naver_dir()):
        try:
            parts.append(_naver_frame(path))
        except Exception as exc:          # 형식이 다른 파일 하나 때문에 전체가 멈추지 않게
            print(f"[네이버 CSV] 읽기 실패 {path.name}: {type(exc).__name__}: {exc}")
    if not parts:
        return pd.DataFrame(columns=["keyword", "period", "ratio", "request_id", "collected_at"])
    return pd.concat(parts, ignore_index=True)


def naver_keywords() -> list[str]:
    df = load_naver()
    return list(dict.fromkeys(df["keyword"].astype(str))) if not df.empty else []


# ---------------------------------------------------------------- 유튜브
def _file_date(path: Path) -> str:
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", path.stem)
    if match:
        return "-".join(match.groups())
    return _mtime(path).date().isoformat()


def load_youtube() -> dict:
    """{"candidates": DataFrame, "videos": DataFrame, "files": [...]}"""
    candidates, videos, files = [], [], []
    for path in _csvs(youtube_dir()):
        try:
            raw = pd.read_csv(path)
        except Exception as exc:
            print(f"[유튜브 CSV] 읽기 실패 {path.name}: {type(exc).__name__}: {exc}")
            continue
        raw.columns = [_nfc(c).strip() for c in raw.columns]
        files.append(_nfc(path.name))
        if VIDEO_COLUMNS <= set(raw.columns):
            videos.append(raw)
        elif {"키워드", "등장 채널 수"} <= set(raw.columns) or {"keyword", "channel_count"} <= set(raw.columns):
            df = raw.rename(columns=CANDIDATE_COLUMNS)
            df["keyword"] = df["keyword"].map(lambda k: _nfc(k).strip())
            df["links"] = df["links"].fillna("").map(lambda s: [u.strip() for u in str(s).split("|") if u.strip()]) \
                if "links" in df else [[] for _ in range(len(df))]
            df["collected_at"] = _file_date(path)
            df["source_file"] = _nfc(path.name)
            candidates.append(df)
    return {"candidates": pd.concat(candidates, ignore_index=True) if candidates else pd.DataFrame(),
            "videos": pd.concat(videos, ignore_index=True) if videos else pd.DataFrame(),
            "files": files}


def youtube_status() -> dict:
    data = load_youtube()
    if data["candidates"].empty and data["videos"].empty:
        return {"available": False, "status": f"미연결 (유튜브 CSV 없음: {_short(youtube_dir())})"}
    kinds = []
    if not data["videos"].empty:
        kinds.append(f"영상 지표 {len(data['videos'])}행")
    if not data["candidates"].empty:
        kinds.append(f"키워드 후보 {len(data['candidates'])}개 (조회수 상위 영상 반복 등장, 영상별 조회수 없음)")
    return {"available": True, "status": "연결됨 (로컬 배치 CSV: " + ", ".join(kinds) + ")"}
