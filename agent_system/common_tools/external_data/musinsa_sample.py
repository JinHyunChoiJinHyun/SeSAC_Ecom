"""무신사 랭킹·리뷰 CSV 읽기 (로컬 모드 전용, 읽기만 함).

위치: database/musinsa/  (환경변수 CART_SIGNAL_MUSINSA_DIR로 변경)
    수집기(external_data/musinsa_crawler.py)는 주마다 database/musinsa/YYYYMMDD/ 에 저장합니다.
    날짜 폴더가 있으면 가장 최근 폴더를 읽고, 없으면 database/musinsa/ 바로 아래 파일을 읽습니다.
    수집기 파일 구성: ranking.csv · product_info.csv · review.csv (맨 앞 collected_date, 그다음 category 열)
        product_info.product_gender = 제품 성별, review.reviewer_gender = 리뷰 작성자 성별
        → 읽을 때 예전 이름(add_info.csv, gender, collected_at)으로 맞춰 아래 코드는 그대로 씁니다.
아래 예전 파일 구성도 계속 읽습니다.
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

from common.external_data_store import fetch, LABELS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LABEL = "무신사 (랭킹·리뷰)"
KINDS = ("ranking", "add_info", "review")
# 수집기의 새 파일 이름 → 이 모듈이 쓰는 이름
KIND_ALIASES = {"product_info": "add_info"}
# 수집기의 새 컬럼 이름 → 이 모듈이 쓰는 이름 (예전 이름이 이미 있으면 바꾸지 않음)
COLUMN_ALIASES = {"product_gender": "gender", "reviewer_gender": "gender", "collected_date": "collected_at"}

_cache: dict = {}
_lock = threading.Lock()


def data_dir() -> Path:
    return Path(os.getenv("CART_SIGNAL_MUSINSA_DIR") or PROJECT_ROOT / "database" / "musinsa")


def latest_folder(folder: Path | None = None) -> Path:
    """database/musinsa/YYYYMMDD/ 날짜 폴더가 있으면 가장 최근 폴더, 없으면 기본 폴더."""
    folder = folder or data_dir()
    dated = sorted(d for d in folder.glob("*") if d.is_dir() and d.name.isdigit() and len(d.name) == 8
                   and any(d.glob("*.csv"))) if folder.is_dir() else []
    return dated[-1] if dated else folder


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """수집기의 새 컬럼 이름을 이 모듈이 쓰는 이름으로 바꾸고,
    category 의 영문 코드(knit_sweater 등)를 한글 대분류 이름(니트·스웨터 등)으로 바꿉니다."""
    renames = {new: old for new, old in COLUMN_ALIASES.items() if new in frame and old not in frame}
    frame = frame.rename(columns=renames) if renames else frame
    if "category" in frame and frame["category"].isin(list(LABELS)).any():
        frame = frame.assign(category=frame["category"].map(lambda c: LABELS.get(c, c)))
    return frame


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
    folder = latest_folder(folder)
    sets: dict[str, dict[str, Path]] = {}
    if not folder.is_dir():
        return {}
    for path in sorted(folder.glob("*.csv")):
        stem = _nfc(path.stem)
        for alias, kind in KIND_ALIASES.items():
            if stem == alias or stem.endswith("_" + alias):
                stem = stem[: -len(alias)] + kind
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
    # R8_DUCKDB_LOAD
    from common.external_data_store import enabled as _r8_enabled
    if _r8_enabled():
        return musinsa_snapshot()
    """{"products", "reviews", "collected_at", "categories"} — 파일이 바뀌면 다시 읽습니다."""
    global _cache
    folder = latest_folder()
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
            products = normalize_columns(_read(files.get("ranking"), dtype={"item_id": str}))
            info = normalize_columns(_read(files.get("add_info"), dtype={"item_id": str}))
            reviews = normalize_columns(_read(files.get("review"), dtype={"item_id": str, "review_id": str}))
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
        # 카테고리별 상품·리뷰 복합 키를 사용해 다른 상품의 동일 review_id를 보존합니다.
        products = products.drop_duplicates(["category", "item_id"], keep="first").reset_index(drop=True)
        dedupe = ["category", "item_id", "review_id"] if "review_id" in reviews and reviews["review_id"].notna().all() else ["category", "item_id", "review"]
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


# 로컬 DuckDB 읽기 어댑터 (기존 CSV 반환 계약 유지)
def _musinsa_tables():
    ranking = fetch('musinsa_ranking')
    info = fetch('musinsa_add_info')
    reviews = fetch('musinsa_review')
    for kind, frame in (('ranking', ranking), ('info', info), ('reviews', reviews)):
        needed = {'category', 'item_id'}
        if not needed.issubset(frame.columns):
            raise ValueError(f'musinsa_{kind}: 필수 컬럼 누락: {needed - set(frame.columns)}')
        unknown = set(frame['category'].dropna().unique()) - set(LABELS)
        if unknown:
            raise ValueError(f'musinsa_{kind}: 미등록 카테고리: {unknown}')
        frame['category'] = frame['category'].map(LABELS)
        frame['item_id'] = frame['item_id'].astype('string')
    if 'review_id' in reviews:
        reviews['review_id'] = reviews['review_id'].astype('string')
    extra = [c for c in ('gender', 'rating', 'review_count') if c in info and c not in ranking]
    if extra:
        ranking = ranking.merge(info[['category','item_id', *extra]], on=['category','item_id'],
                                how='left', validate='one_to_one')
    return ranking, reviews


def musinsa_snapshot() -> dict:
    products, reviews = _musinsa_tables()
    # 통합 CSV는 카테고리와 상품을 함께 식별한다. review_id 단독은 전역 고유키가 아니다.
    products = products.drop_duplicates(['category', 'item_id'], keep='first').reset_index(drop=True)
    if 'review_id' in reviews and reviews['review_id'].notna().all():
        reviews = reviews.drop_duplicates(['category', 'item_id', 'review_id'], keep='first').reset_index(drop=True)
    elif {'category', 'item_id', 'review'}.issubset(reviews):
        reviews = reviews.drop_duplicates(['category','item_id','review'], keep='first').reset_index(drop=True)
    for column in ('item_id', 'satisfaction', 'review'):
        if column not in reviews:
            reviews[column] = None
    return {'products': products, 'reviews': reviews,
            'collected_at': '미상 (원본 CSV 수집일 없음)',
            'categories': list(LABELS.values())}


def category_frames(category: str):
    """카테고리별 원본 표본을 반환: 서로 다른 카테고리의 동일 ID를 제거하지 않는다."""
    if category not in LABELS.values():
        raise ValueError(f'지원하지 않는 무신사 카테고리: {category}')
    products, reviews = _musinsa_tables()
    products = products.loc[products['category'].eq(category)].copy()
    reviews = reviews.loc[reviews['category'].eq(category)].copy()
    return products, reviews, '미상 (원본 CSV 수집일 없음)'
