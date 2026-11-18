"""무신사 샘플 CSV 읽기 (로컬 모드 전용, 읽기만 함).

위치: database/musinsa/  (환경변수 CART_SIGNAL_MUSINSA_DIR로 변경)
    ranking.csv   item_id, ranking, brand, brand_id, name, price, original_price, discount_rate (+ collected_at 선택)
    add_info.csv  item_id, gender, rating, img                    (선택)
    review.csv    item_id, review_id, gender, height, weight, option, size, satisfaction, review
컬럼은 백엔드 external 스키마(musinsa_product · musinsa_ranking_snapshot · musinsa_review)와 같은 이름입니다.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LABEL = "무신사 (랭킹·리뷰 샘플)"

_cache: dict = {}
_lock = threading.Lock()


def data_dir() -> Path:
    return Path(os.getenv("CART_SIGNAL_MUSINSA_DIR") or PROJECT_ROOT / "database" / "musinsa")


def _short(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name


def status() -> dict:
    path = data_dir() / "ranking.csv"
    if not path.is_file():
        return {"available": False, "status": f"미연결 (샘플 없음: {_short(path)})"}
    return {"available": True, "status": "연결됨 (로컬 샘플 CSV)"}


def load() -> dict:
    """{"products", "reviews", "collected_at"} — 파일이 바뀌면 다시 읽습니다."""
    global _cache
    folder = data_dir()
    ranking_path = folder / "ranking.csv"
    if not ranking_path.is_file():
        raise FileNotFoundError(status()["status"])
    # 세 CSV 파일의 추가·삭제·수정을 모두 감지
    filenames = ("ranking.csv", "add_info.csv", "review.csv")
    files = tuple(folder / name for name in filenames)
    key = (
        str(folder),
        tuple(
            (name, path.stat().st_mtime_ns, path.stat().st_size)
            if path.is_file() else (name, None, None)
            for name, path in zip(filenames, files)
        ),
    )
    with _lock:
        if _cache.get("key") == key:
            return _cache
        read = lambda name, **kw: pd.read_csv(folder / name, **kw) if (folder / name).is_file() else pd.DataFrame()
        products = read("ranking.csv", dtype={"item_id": str})
        info = read("add_info.csv", dtype={"item_id": str})
        reviews = read("review.csv", dtype={"item_id": str, "review_id": str})
        if not info.empty and "item_id" in info:
            extra = [c for c in ("gender", "rating") if c in info and c not in products]
            products = products.merge(info[["item_id", *extra]].drop_duplicates("item_id"), on="item_id", how="left")
        for column in ("item_id", "satisfaction", "review"):
            if column not in reviews:
                reviews[column] = None
        # 수집일: collected_at 열이 있으면 그 값, 없으면 '미상'(파일 수정일은 참고로만 표시)
        if "collected_at" in products and pd.to_datetime(products["collected_at"], errors="coerce").notna().any():
            collected = str(pd.to_datetime(products["collected_at"], errors="coerce").max().date())
        else:
            stamp = datetime.fromtimestamp(ranking_path.stat().st_mtime, tz=timezone.utc).date().isoformat()
            collected = f"미상 (파일 수정일 {stamp})"
        # 이전 요청이 참조하던 캐시는 변경하지 않음
        snapshot = {
            "key": key,
            "products": products,
            "reviews": reviews,
            "collected_at": collected,
        }
        _cache = snapshot
        return snapshot
