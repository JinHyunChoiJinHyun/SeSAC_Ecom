"""CART SIGNAL: H&M / Amazon / Synerise Parquet -> DuckDB 생성.

기본 정책: H&M·Amazon은 VIEW(Parquet 참조), Synerise는 TABLE(실제 적재).
데이터를 전체 복사하고 싶으면 --materialize-all 옵션을 사용합니다.
기존 DuckDB가 있다면 기본적으로 건너뛰며 --overwrite 사용 시 재생성합니다.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent
DB_DIR = PROJECT_ROOT / "database"

DATASETS = {
    "hm": {
        "source_dir": "h-and-m-parquet",
        "database": "hm_data.duckdb",
        "schema": "hm",
        "files": {
            "articles.parquet": "articles",
            "customers.parquet": "customers",
            "transactions.parquet": "transactions",
        },
        "default_mode": "view",
    },
    "amazon": {
        "source_dir": "Amazon_parquet",
        "database": "amazon_data.duckdb",
        "schema": "amazon",
        "files": {
            "meta_Clothing_Shoes_and_Jewelry.parquet": "meta",
            "Clothing_Shoes_and_Jewelry.parquet": "review",
        },
        "default_mode": "view",
    },
    "synerise": {
        "source_dir": "synerise_dataset",
        "database": "synerise.duckdb",
        "schema": "raw",
        "files": {
            "product_buy.parquet": "product_buy",
            "add_to_cart.parquet": "add_to_cart",
            "remove_from_cart.parquet": "remove_from_cart",
            "page_visit.parquet": "page_visit",
            "search_query.parquet": "search_query",
            "product_properties.parquet": "product_properties",
        },
        "default_mode": "table",
    },
}


def quoted_string(value: Path) -> str:
    """SQL 문자열에서 단일 따옴표를 이스케이프."""
    return "'" + value.resolve().as_posix().replace("'", "''") + "'"


def create_database(dataset: str, mode: str | None, overwrite: bool) -> bool:
    """데이터셋별 별도 DB 생성; 실패 시 기존 DB 파일은 건드리지 않음."""
    spec = DATASETS[dataset]
    source_dir = PROJECT_ROOT / spec["source_dir"]
    DB_DIR.mkdir(parents=True, exist_ok=True)
    target = DB_DIR / spec["database"]
    final_mode = mode or spec["default_mode"]

    missing = [name for name in spec["files"] if not (source_dir / name).is_file()]
    if missing:
        print(f"[실패] {dataset}: 필수 Parquet 파일을 찾지 못했습니다.")
        for name in missing:
            print(f"  - {source_dir / name}")
        return False

    if target.exists() and not overwrite:
        print(f"[건너뜀] {target} (재생성: --overwrite)")
        return True

    # 기존 DB를 직접 덮어쓰지 않고 임시 DB를 만든 후 성공 시 교체
    temp = DB_DIR / (target.name + ".building.duckdb")
    if temp.exists():
        print(f"[실패] 이전 임시 DB가 존재합니다: {temp}")
        print("  다른 생성 프로세스가 없는지 확인하고 수동으로 처리해 주세요.")
        return False

    con = None
    try:
        print(f"\n[{dataset.upper()}] 생성 시작 ({final_mode.upper()}): {target}", flush=True)
        con = duckdb.connect(str(temp), read_only=False)
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{spec["schema"]}"')
        for filename, table in spec["files"].items():
            path = source_dir / filename
            sql_path = quoted_string(path)
            qualified = f'"{spec["schema"]}"."{table}"'
            if final_mode == "table":
                print(f"  [적재] {filename} -> {qualified}", flush=True)
                con.execute(f"CREATE TABLE {qualified} AS SELECT * FROM read_parquet({sql_path})")
                count = con.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()[0]
                print(f"  [완료] {qualified}: {count:,}행", flush=True)
            else:
                # VIEW는 원본 Parquet를 참조하므로 DB 파일만 복사하면 조회되지 않음
                con.execute(f"CREATE VIEW {qualified} AS SELECT * FROM read_parquet({sql_path})")
                con.execute(f"SELECT * FROM {qualified} LIMIT 0")
                print(f"  [연결] {filename} -> {qualified} (Parquet VIEW)", flush=True)
        con.close()
        con = None
        # DuckDB 연결이 완전히 닫힌 후 교체
        os.replace(temp, target)
        print(f"[성공] {target}", flush=True)
        return True
    except Exception as exc:
        print(f"[실패] {dataset}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return False
    finally:
        if con is not None:
            con.close()
        # 실패한 임시 DB만 정리. 기존 target은 보존.
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="H&M, Amazon, Synerise DuckDB 생성")
    parser.add_argument("--dataset", choices=["all", *DATASETS], default="all")
    parser.add_argument("--materialize-all", action="store_true", help="세 DB 모두 TABLE로 실데이터 복사 (디스크 사용량 증가)")
    parser.add_argument("--overwrite", action="store_true", help="기존 DuckDB 재생성")
    args = parser.parse_args()
    datasets = list(DATASETS) if args.dataset == "all" else [args.dataset]
    result = [create_database(name, "table" if args.materialize_all else None, args.overwrite) for name in datasets]
    return 0 if all(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
