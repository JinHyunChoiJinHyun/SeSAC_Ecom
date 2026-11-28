from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from .errors import ArticleNotFound

TABLES = ("transactions", "articles", "customers")
REQUIRED_TABLES = ("transactions", "articles")
ATTRIBUTE_COLUMNS = {
    "product_type": "product_type_name",
    "colour": "colour_group_name",
    "pattern": "graphical_appearance_name",
}
UNKNOWN_VALUES = frozenset({"Unknown"})


@dataclass
class HMData:
    con: duckdb.DuckDBPyConnection
    source: str
    data_start: date
    data_end: date

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_range(con) -> tuple[date, date]:
    start, end = con.execute("SELECT MIN(t_dat)::DATE, MAX(t_dat)::DATE FROM hm.transactions").fetchone()
    return start, end


def _wrap(con, source: str) -> HMData:
    try:
        start, end = _date_range(con)
    except Exception:
        con.close()
        raise
    return HMData(con, source, start, end)


def connect_team(repo_root) -> HMData:
    """팀 저장소의 common.db_client 연결(읽기 전용)을 사용. repo_root = 팀 저장소 루트 폴더"""
    root = str(Path(repo_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from common.db_client import get_analysis_connection

    return _wrap(get_analysis_connection(), f"team:{Path(root).as_posix()}")


def from_connection(con, source: str = "external") -> HMData:
    return _wrap(con, source)


def connect_parquet(parquet_dir) -> HMData:
    folder = Path(parquet_dir).resolve()
    missing = [t for t in REQUIRED_TABLES if not (folder / f"{t}.parquet").is_file()]
    if missing:
        raise FileNotFoundError(f"Parquet 파일이 없습니다: {[str(folder / f'{t}.parquet') for t in missing]}")
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE SCHEMA hm")
        for table in TABLES:
            path = folder / f"{table}.parquet"
            if path.is_file():
                escaped = path.as_posix().replace("'", "''")
                con.execute(f"CREATE VIEW hm.{table} AS SELECT * FROM read_parquet('{escaped}')")
    except Exception:
        con.close()
        raise
    return _wrap(con, f"parquet:{folder.as_posix()}")


def article_attributes(data: HMData, article_ids) -> pd.DataFrame:
    ids = list(dict.fromkeys(article_ids))
    bad = [a for a in ids if not isinstance(a, str)]
    if bad:
        raise TypeError(f"article_id는 문자열이어야 합니다 (앞자리 0 포함): {bad[:5]}")
    if not ids:
        return pd.DataFrame(columns=["article_id", *ATTRIBUTE_COLUMNS.values()])
    found = data.con.execute(
        f"SELECT article_id, {', '.join(ATTRIBUTE_COLUMNS.values())} FROM hm.articles "
        "WHERE article_id IN (SELECT UNNEST(?::VARCHAR[]))",
        [ids],
    ).df()
    missing = [a for a in ids if a not in set(found["article_id"])]
    if missing:
        raise ArticleNotFound(f"존재하지 않는 article_id: {missing}")
    return found.set_index("article_id").loc[ids].reset_index()


def combo_article_count(data: HMData, product_type: str, colour: str | None = None) -> int:
    if colour is None:
        return data.con.execute(
            "SELECT COUNT(*) FROM hm.articles WHERE product_type_name = ?", [product_type]
        ).fetchone()[0]
    return data.con.execute(
        "SELECT COUNT(*) FROM hm.articles WHERE product_type_name = ? AND colour_group_name = ?",
        [product_type, colour],
    ).fetchone()[0]
