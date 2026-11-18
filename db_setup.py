"""CART SIGNAL: H&M / Amazon / Synerise Parquet -> DuckDB 생성.

기본 정책: H&M·Amazon은 VIEW(Parquet 참조), Synerise는 TABLE(실제 적재).
데이터를 전체 복사하고 싶으면 --materialize-all 옵션을 사용합니다.
기존 DuckDB가 있다면 기본적으로 건너뛰며 --overwrite 사용 시 재생성합니다.

H&M 상품 이미지 경로 인덱스 (hm.article_image_paths, TABLE)
    이미지 파일 자체는 DB에 넣지 않고, 이미지 폴더를 훑어 "상품 ID → 파일 경로"만 저장합니다.
    이미지 폴더 위치 (앞쪽 우선)
        1. --hm-image-dir 옵션
        2. 환경변수 CART_SIGNAL_HM_IMAGE_DIR
        3. 기본: h-and-m-parquet/images  (Parquet 파일과 같은 폴더 아래 images)
    폴더 규칙(Kaggle 원본): images/{article_id 앞 3자리}/{article_id}.jpg
    폴더가 없으면 경고만 하고 나머지 H&M DB는 그대로 만듭니다 (이미지 조회는 '경로 테이블 없음'으로 표시).
    예) python db_setup.py --dataset hm --overwrite        (기본 폴더 h-and-m-parquet/images 사용)
        python db_setup.py --dataset hm --overwrite --hm-image-dir /다른/위치/images
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import uuid

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


# H&M 상품 이미지 경로 인덱스
HM_IMAGE_TABLE = "article_image_paths"
HM_IMAGE_DEFAULT_DIR = f"{DATASETS['hm']['source_dir']}/images"   # h-and-m-parquet/images
HM_IMAGE_ENV = "CART_SIGNAL_HM_IMAGE_DIR"
_IMAGE_NAME = re.compile(r"^(\d{10})\.jpg$", re.IGNORECASE)


def hm_image_dir(option: str | None = None) -> Path:
    """이미지 폴더 위치: 옵션 → 환경변수 → 기본 폴더."""
    raw = option or os.getenv(HM_IMAGE_ENV) or str(PROJECT_ROOT / HM_IMAGE_DEFAULT_DIR)
    return Path(raw).expanduser()


def scan_hm_images(image_dir: Path) -> tuple[list[tuple[str, str, int]], dict]:
    """이미지 폴더 → [(article_id, 상대 경로, 파일 크기)], 점검 숫자.

    상대 경로는 이미지 폴더 기준 (예: 010/0108775015.jpg). 파일명이 10자리 숫자 .jpg가 아니면 제외합니다.
    같은 article_id가 여러 번 나오면 처음 1개만 씁니다 (현재 원본은 중복 0건).
    """
    rows, seen = [], set()
    stats = {"skipped_name": 0, "duplicate": 0, "empty_file": 0}
    for folder in sorted(p for p in image_dir.iterdir() if p.is_dir()):
        for entry in sorted(os.scandir(folder), key=lambda e: e.name):
            if not entry.is_file():
                continue
            match = _IMAGE_NAME.match(entry.name)
            if not match:
                stats["skipped_name"] += 1
                continue
            article_id = match.group(1)
            if article_id in seen:
                stats["duplicate"] += 1
                continue
            size = entry.stat().st_size
            if size == 0:
                stats["empty_file"] += 1
            seen.add(article_id)
            rows.append((article_id, f"{folder.name}/{entry.name}", size))
    return rows, stats


def create_hm_image_table(con, schema: str, image_dir: Path) -> bool:
    """hm.article_image_paths 생성. 폴더가 없으면 False (DB 생성은 계속)."""
    if not image_dir.is_dir():
        print(f"  [건너뜀] 이미지 폴더 없음: {image_dir}\n"
              f"          → --hm-image-dir 또는 환경변수 {HM_IMAGE_ENV}로 이미지 폴더를 지정하세요.", flush=True)
        return False
    import pandas as pd

    print(f"  [색인] 이미지 폴더 {image_dir}", flush=True)
    rows, stats = scan_hm_images(image_dir)
    frame = pd.DataFrame(rows, columns=["article_id", "image_path", "file_bytes"])
    qualified = f'"{schema}"."{HM_IMAGE_TABLE}"'
    con.register("_hm_image_rows", frame)
    try:
        con.execute(f"""CREATE TABLE {qualified} (
                            article_id VARCHAR PRIMARY KEY,
                            image_path VARCHAR NOT NULL,
                            file_bytes BIGINT NOT NULL)""")
        con.execute(f"INSERT INTO {qualified} SELECT article_id, image_path, file_bytes FROM _hm_image_rows")
    finally:
        con.unregister("_hm_image_rows")
    root = image_dir.resolve().as_posix().replace("'", "''")
    # 조회 코드가 이미지 폴더를 찾을 수 있도록 만든 위치를 테이블 주석에 남김 (환경변수가 있으면 환경변수 우선)
    con.execute(f"COMMENT ON TABLE {qualified} IS 'image_root={root}'")
    unmatched = con.execute(
        f'SELECT COUNT(*) FROM {qualified} i LEFT JOIN "{schema}"."articles" a USING (article_id) '
        "WHERE a.article_id IS NULL").fetchone()[0]
    no_image = con.execute(
        f'SELECT COUNT(*) FROM "{schema}"."articles" a LEFT JOIN {qualified} i USING (article_id) '
        "WHERE i.article_id IS NULL").fetchone()[0]
    print(f"  [완료] {qualified}: {len(rows):,}행 · 이미지 없는 상품 {no_image:,}개 · "
          f"상품표에 없는 이미지 {unmatched:,}개 · 이름 규칙 밖 파일 {stats['skipped_name']:,}개 · "
          f"중복 {stats['duplicate']:,}개 · 0바이트 {stats['empty_file']:,}개", flush=True)
    return True


def wal_path(db: Path) -> Path:
    return db.with_name(db.name + ".wal")


def set_aside_old_wal(target: Path) -> Path | None:
    """교체 전에 기존 DB의 WAL 파일을 옆으로 옮겨 둡니다 (CodeRabbit 리뷰).

    --overwrite는 DB 전체를 새로 만드는 것이므로 이전 DB의 WAL을 새 DB에 재생하면 안 됩니다.
    데이터 보존을 위해 지우지 않고 '<이름>.wal.<시각>.old'로 이름만 바꿉니다.
    옮기지 못하면(다른 프로세스가 DB를 쓰는 중 등) 예외를 내서 교체하지 않습니다.
    반환: 옮긴 파일 경로 (WAL이 없었으면 None)
    """
    wal = wal_path(target)
    if not wal.exists():
        return None
    from datetime import datetime
    kept = wal.with_name(f"{wal.name}.{datetime.now():%Y%m%d_%H%M%S}.old")
    os.replace(wal, kept)
    if wal.exists():
        raise RuntimeError(f"기존 WAL 파일을 옮기지 못했습니다: {wal}")
    return kept


def quoted_string(value: Path) -> str:
    """SQL 문자열에서 단일 따옴표를 이스케이프."""
    return "'" + value.resolve().as_posix().replace("'", "''") + "'"


def create_database(dataset: str, mode: str | None, overwrite: bool, image_dir: str | None = None) -> bool:
    """데이터셋별 별도 DB 생성; 실패 시 기존 DB 파일은 건드리지 않음."""
    spec = DATASETS[dataset]
    source_dir = PROJECT_ROOT / spec["source_dir"]
    DB_DIR.mkdir(parents=True, exist_ok=True)
    target = DB_DIR / spec["database"]
    final_mode = mode or spec["default_mode"]

    if target.exists() and not overwrite:
        print(f"[건너뜀] {target} (재생성: --overwrite)")
        return True

    missing = [name for name in spec["files"] if not (source_dir / name).is_file()]
    if missing:
        print(f"[실패] {dataset}: 필수 Parquet 파일을 찾지 못했습니다.")
        for name in missing:
            print(f"  - {source_dir / name}")
        return False

    # 기존 DB를 직접 덮어쓰지 않고 임시 DB를 만든 후 성공 시 교체
    # 실행마다 별도 파일 사용
    temp = DB_DIR / f"{target.name}.{uuid.uuid4().hex}.building.duckdb"
    temp_owned = False

    con = None
    try:
        print(f"\n[{dataset.upper()}] 생성 시작 ({final_mode.upper()}): {target}", flush=True)
        con = duckdb.connect(str(temp), read_only=False)
        temp_owned = True
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
        if dataset == "hm":
            create_hm_image_table(con, spec["schema"], hm_image_dir(image_dir))
        con.execute("CHECKPOINT")          # 새 DB 내용을 본 파일에 모두 기록 (임시 WAL이 남지 않게)
        con.close()
        con = None
        if wal_path(temp).exists():
            raise RuntimeError(f"임시 DB의 WAL이 남아 있어 교체하지 않습니다: {wal_path(temp)}")
        # 이전 DB의 WAL을 먼저 치운 뒤 교체 (새 DB에 이전 변경이 재생되지 않도록)
        kept = set_aside_old_wal(target)
        if kept:
            print(f"  [WAL] 이전 DB의 WAL을 옮겨 둠: {kept.name} (새 DB에는 적용하지 않음)", flush=True)
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
        if temp_owned:
            for leftover in (temp, wal_path(temp)):
                if leftover.exists():
                    try:
                        leftover.unlink()
                    except OSError:
                        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="H&M, Amazon, Synerise DuckDB 생성")
    parser.add_argument("--dataset", choices=["all", *DATASETS], default="all")
    parser.add_argument("--materialize-all", action="store_true", help="세 DB 모두 TABLE로 실데이터 복사 (디스크 사용량 증가)")
    parser.add_argument("--overwrite", action="store_true", help="기존 DuckDB 재생성")
    parser.add_argument("--hm-image-dir", default=None,
                        help=f"H&M 이미지 폴더 (기본: 환경변수 {HM_IMAGE_ENV} → {HM_IMAGE_DEFAULT_DIR})")
    args = parser.parse_args()
    datasets = list(DATASETS) if args.dataset == "all" else [args.dataset]
    result = [create_database(name, "table" if args.materialize_all else None, args.overwrite, args.hm_image_dir)
              for name in datasets]
    return 0 if all(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
