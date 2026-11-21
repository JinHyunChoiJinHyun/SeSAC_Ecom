"""무신사 랭킹·리뷰 CSV 읽기 (로컬 모드 전용, 읽기만 함).

위치: database/musinsa/  (환경변수 CART_SIGNAL_MUSINSA_DIR로 변경)
두 가지 파일 구성을 모두 읽습니다.
    ① 카테고리별 배치 파일 (11/20 배치 데이터)
        {카테고리}_ranking.csv   item_id, ranking, brand, brand_id, name, price, best_price, original_price, discount_rate
        {카테고리}_add_info.csv  item_id, gender, rating, review_count, img          (선택)
        {카테고리}_review.csv    item_id, review_id, gender, height, weight, option, size, satisfaction, review
        예: 니트_스웨터_ranking.csv → category = "니트/스웨터"
    ② 예전 샘플 한 벌: ranking.csv · add_info.csv · review.csv  (category 없음)
여러 카테고리에 같은 상품이 있으면 상품은 처음 것 하나만, 리뷰는 review_id(없으면 상품+본문) 기준으로 한 번만 셉니다.
랭킹은 카테고리 안 순위입니다 (카테고리가 다르면 순위끼리 비교하지 않음).
컬럼은 백엔드 external 스키마(musinsa_product · musinsa_ranking_snapshot · musinsa_review)와 같은 이름입니다.
배치 DB(PostgreSQL external)가 완성되면 백엔드 API가 이 파일 대신 같은 형식의 데이터를 줍니다.
"""

from __future__ import annotations

import os
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LABEL = "무신사 (랭킹·리뷰)"
KINDS = ("ranking", "add_info", "review")

_cache: dict = {}
_lock = threading.Lock()


def data_dir() -> Path:
    return Path(os.getenv("CART_SIGNAL_MUSINSA_DIR") or PROJECT_ROOT / "database" / "musinsa")


def _short(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name


def _nfc(text: str) -> str:
    # macOS 파일 이름은 한글 자모가 분리(NFD)되어 있을 수 있어 NFC로 맞춥니다
    return unicodedata.normalize("NFC", text)


def file_sets(folder: Path | None = None) -> dict[str, dict[str, Path]]:
    """{카테고리: {"ranking": 경로, "add_info": 경로, "review": 경로}} — ranking이 있는 세트만.

    예전 샘플(ranking.csv)은 카테고리 "" 로 둡니다.
    """
    folder = folder or data_dir()
    sets: dict[str, dict[str, Path]] = {}
    if not folder.is_dir():
        return {}
    for path in sorted(folder.glob("*.csv")):
        stem = _nfc(path.stem)
        for kind in KINDS:
            if stem == kind:
                sets.setdefault("", {})[kind] = path
                break
            if stem.endswith("_" + kind):
                category = stem[: -len(kind) - 1].replace("_", "/")
                sets.setdefault(category, {})[kind] = path
                break
    return {k: v for k, v in sets.items() if "ranking" in v}


def status() -> dict:
    sets = file_sets()
    if not sets:
        return {"available": False, "status": f"미연결 (무신사 CSV 없음: {_short(data_dir())})"}
    names = [c for c in sets if c] or ["샘플 1벌"]
    return {"available": True, "status": f"연결됨 (로컬 CSV, 카테고리 {len(names)}개: {', '.join(names)})",
            "categories": [c for c in sets if c]}


def _read(path: Path | None, **kw) -> pd.DataFrame:
    return pd.read_csv(path, **kw) if path is not None and path.is_file() else pd.DataFrame()


def load() -> dict:
    """{"products", "reviews", "collected_at", "categories"} — 파일이 바뀌면 다시 읽습니다."""
    global _cache
    folder = data_dir()
    sets = file_sets(folder)
    if not sets:
        raise FileNotFoundError(status()["status"])
    # 파일 추가·삭제·수정을 모두 감지
    key = (str(folder), tuple(sorted((c, kind, str(p), p.stat().st_mtime_ns, p.stat().st_size)
                                     for c, files in sets.items() for kind, p in files.items())))
    with _lock:
        if _cache.get("key") == key:
            return _cache
        product_parts, review_parts, stamps = [], [], []
        for category, files in sets.items():
            products = _read(files.get("ranking"), dtype={"item_id": str})
            info = _read(files.get("add_info"), dtype={"item_id": str})
            reviews = _read(files.get("review"), dtype={"item_id": str, "review_id": str})
            if not info.empty and "item_id" in info:
                extra = [c for c in ("gender", "rating", "review_count") if c in info and c not in products]
                products = products.merge(info[["item_id", *extra]].drop_duplicates("item_id"), on="item_id",
                                          how="left")
            if category:
                products["category"] = category
                reviews["category"] = category
            product_parts.append(products)
            review_parts.append(reviews)
            stamps.append(files["ranking"].stat().st_mtime)
        products = pd.concat(product_parts, ignore_index=True)
        reviews = pd.concat(review_parts, ignore_index=True) if review_parts else pd.DataFrame()
        for column in ("item_id", "satisfaction", "review"):
            if column not in reviews:
                reviews[column] = None
        # 같은 상품이 여러 카테고리에 있으면 한 번만 (리뷰 중복 집계 방지)
        products = products.drop_duplicates("item_id", keep="first").reset_index(drop=True)
        dedupe = ["review_id"] if "review_id" in reviews and reviews["review_id"].notna().all() else ["item_id", "review"]
        reviews = reviews.drop_duplicates(dedupe, keep="first").reset_index(drop=True)
        # 수집일: collected_at 열이 있으면 그 값, 없으면 '미상'(파일 수정일은 참고로만 표시)
        if "collected_at" in products and pd.to_datetime(products["collected_at"], errors="coerce").notna().any():
            collected = str(pd.to_datetime(products["collected_at"], errors="coerce").max().date())
        else:
            stamp = datetime.fromtimestamp(max(stamps), tz=timezone.utc).date().isoformat()
            collected = f"미상 (파일 수정일 {stamp})"
        # 이전 요청이 참조하던 캐시는 변경하지 않음
        snapshot = {"key": key, "products": products, "reviews": reviews, "collected_at": collected,
                    "categories": [c for c in sets if c]}
        _cache = snapshot
        return snapshot
