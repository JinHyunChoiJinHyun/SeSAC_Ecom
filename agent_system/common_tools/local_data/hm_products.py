"""H&M 상품·구매 기록 읽기 (로컬 모드 전용, 읽기 전용).

백엔드가 생기면 같은 일을 backend/repositories/analytics/hm_product_repository(articles, product_features,
article_image_paths)와 daily_purchases 조회가 합니다. 그때 이 파일은 쓰이지 않습니다.

- 연결: common/db_client.get_analysis_connection() (READ_ONLY ATTACH + 외부 접근 차단)
- 기준일(as_of): H&M 마지막 거래일 (아키텍처 ANALYSIS_AS_OF = 2020-09-22). 실제 오늘 날짜를 쓰지 않습니다.
  환경변수 CART_SIGNAL_ANALYSIS_AS_OF(YYYY-MM-DD)로 바꿀 수 있습니다.
- 스타일 태그: shared_analysis/product/keyword_matching.extract_style_tags (규칙 기반)
  → 아키텍처의 product_features(스타일 태그)가 생기기 전 임시 계산. 상품 전체를 한 번 태그해 캐시합니다.
- 이미지: hm_images.py (경로 인덱스 hm.article_image_paths + 이미지 폴더). image_info()는 그 함수를 그대로 부릅니다.
- 다음에 상품·판매 출처가 바뀌면(무신사·네이버 쇼핑 배치 등) 이 파일과 같은 함수 이름으로 새 파일을 만들고
  agent_system/tools/product_tools.py의 LOCAL 모듈만 바꾸면 됩니다.
"""

from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path

import pandas as pd

from shared_analysis.product.keyword_matching import add_style_tags

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LABEL = "H&M (자사 상품·구매 기록)"
CHANNELS = {"online": [2], "offline": [1], "all": [1, 2]}
CHANNEL_LABEL = {"online": "온라인", "offline": "매장", "all": "온라인+매장"}
ARTICLE_COLUMNS = [
    "article_id", "product_code", "prod_name", "product_type_name", "product_group_name",
    "graphical_appearance_name", "colour_group_name", "perceived_colour_value_name",
    "perceived_colour_master_name", "department_name", "index_name", "index_group_name",
    "section_name", "garment_group_name", "detail_desc",
]

_cache: dict = {}
_lock = threading.Lock()


def _db_path() -> Path:
    from common.data_sources import get_source
    source = get_source("hm")
    return Path(source.path) if source else PROJECT_ROOT / "database" / "hm_data.duckdb"


def status() -> dict:
    path = _db_path()
    if not path.is_file():
        return {"source": LABEL, "available": False, "status": f"미연결 (DB 파일 없음: {path.name})"}
    return {"source": LABEL, "available": True, "status": "연결됨 (로컬 DuckDB 읽기 전용)"}


def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    from common.db_client import get_analysis_connection
    con = get_analysis_connection()
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def _cache_key() -> tuple:
    """DB 파일이 바뀌면(경로·수정 시각·크기) 다시 읽습니다."""
    path = _db_path()
    if not path.exists():
        return (str(path), None, None)
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _cached(kind: str, load):
    """종류별 최신 값 1개만 보관. 새로 읽은 값은 통째로 바꿔 넣어
    이전 요청이 쓰고 있던 값은 건드리지 않습니다 (CodeRabbit 리뷰: musinsa_sample 캐시와 같은 방식)."""
    key = _cache_key()
    with _lock:
        entry = _cache.get(kind)
        if entry and entry[0] == key:
            return entry[1]
    value = load()                      # DB 조회는 잠금 밖에서 (다른 요청을 막지 않음)
    with _lock:
        _cache[kind] = (key, value)
    return value


def data_as_of() -> date:
    """분석 기준일 = H&M 마지막 거래일 (환경변수로 고정 가능)."""
    fixed = os.getenv("CART_SIGNAL_ANALYSIS_AS_OF", "").strip()
    if fixed:
        return pd.Timestamp(fixed).date()
    return _cached("as_of", lambda: pd.Timestamp(
        _query("SELECT max(t_dat) AS d FROM hm.transactions")["d"].iloc[0]).date())


def articles() -> pd.DataFrame:
    """H&M 상품 전체 + style_tags 열 (캐시). 첫 호출은 태그 계산 때문에 몇 초 걸립니다.
    돌려받은 DataFrame은 여러 요청이 함께 쓰므로 바꾸지 말고 복사해서 쓰십시오."""
    def load():
        frame = _query(f"SELECT {', '.join(ARTICLE_COLUMNS)} FROM hm.articles")
        frame["article_id"] = frame["article_id"].astype(str)
        frame["product_code"] = frame["product_code"].astype(str)
        return add_style_tags(frame)

    return _cached("articles", load)


def sales(keys: list[str], key: str, period: dict, channel: str = "online") -> tuple[pd.DataFrame, pd.DataFrame]:
    """같은 기간·조건의 구매 실적 집계.

    key     "article_id" 또는 "product_code"
    period  shared_analysis.product.product_analysis.make_period 결과
    반환    totals(key, period, purchases, customers, price_index), weekly(key, week_start, purchases)
    """
    if not keys:
        empty = pd.DataFrame(columns=[key, "period", "purchases", "customers", "price_index"])
        return empty, pd.DataFrame(columns=[key, "week_start", "purchases"])
    channels = CHANNELS.get(channel, CHANNELS["online"])
    key_expr = "t.article_id" if key == "article_id" else "a.product_code"
    join = "" if key == "article_id" else "JOIN hm.articles a ON a.article_id = t.article_id"
    marks = ", ".join("?" for _ in keys)
    ch_marks = ", ".join("?" for _ in channels)
    base = f"""
        FROM hm.transactions t {join}
        WHERE {key_expr} IN ({marks}) AND t.sales_channel_id IN ({ch_marks})
          AND t.t_dat BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
    """
    totals_sql = f"""
        SELECT CAST({key_expr} AS VARCHAR) AS {key},
               CASE WHEN t.t_dat >= CAST(? AS DATE) THEN 'current' ELSE 'previous' END AS period,
               COUNT(*) AS purchases, COUNT(DISTINCT t.customer_id) AS customers, AVG(t.price) AS price_index
        {base}
        GROUP BY 1, 2
    """
    params = [period["start"], *keys, *channels, period["previous_start"], period["end"]]
    totals = _query(totals_sql, params)
    weekly_sql = f"""
        SELECT CAST({key_expr} AS VARCHAR) AS {key},
               CAST(? AS DATE) + 7 * CAST(floor((t.t_dat - CAST(? AS DATE)) / 7) AS INTEGER) AS week_start,
               COUNT(*) AS purchases
        {base}
        GROUP BY 1, 2
    """
    weekly = _query(weekly_sql, [period["start"], period["start"], *keys, *channels, period["start"], period["end"]])
    return totals, weekly


def image_info(article_id: str) -> dict:
    """상품 이미지 상태 {"image_available", "image_path", "image_status"} (자세한 규칙: hm_images.py)."""
    from agent_system.common_tools.local_data import hm_images
    return hm_images.image_info(article_id)
