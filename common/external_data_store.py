"""팀원 통합 CSV → DuckDB 적재, 검사, 읽기 전용 조회.

실행: python -m common.external_data_store import --source ~/Downloads
       python -m common.external_data_store verify
       python -m common.external_data_store inspect

데이터별 별도 DB: database/musinsa.duckdb, naver.duckdb, youtube.duckdb

기본적으로 Agent는 기존 CSV를 계속 사용합니다.
DuckDB 모드는 적재·검증 후 CART_SIGNAL_EXTERNAL_SOURCE=duckdb 지정 시에만 사용합니다.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
import tempfile
from pathlib import Path

import pandas as pd

from common.data_sources import (
    PROJECT_ROOT as ROOT, LOCAL_EXTERNAL_DB_FILES as DB_FILES,
    get_local_external_db_path,
)
CATEGORIES = ('long_sleeve_tee', 'knit_sweater', 'short_sleeve_tee', 'shirt_blouse', 'outer')
LABELS = {
    'long_sleeve_tee': '긴소매 티셔츠', 'knit_sweater': '니트·스웨터',
    'short_sleeve_tee': '반소매 티셔츠', 'shirt_blouse': '셔츠·블라우스', 'outer': '아우터',
}
TABLES = ('musinsa_ranking', 'musinsa_add_info', 'musinsa_review', 'naver_trend', 'youtube_candidate')
REQUIRED = {
    'ranking': {'category', 'item_id', 'ranking', 'name'},
    'add_info': {'category', 'item_id', 'gender', 'rating', 'review_count'},
    'review': {'category', 'item_id', 'review_id', 'review'},
    'naver': {'날짜'},
    'youtube': {'상품 카테고리', '키워드', '등장 채널 수', '근거 링크', '기준'},
}


TABLE_DATASET = {
    'musinsa_ranking': 'musinsa',
    'musinsa_add_info': 'musinsa',
    'musinsa_review': 'musinsa',
    'naver_trend': 'naver',
    'youtube_candidate': 'youtube',
}


def db_path(dataset: str, db_dir: Path | None = None) -> Path:
    """데이터 종류별 DB 경로는 공통 출처 설정에서 관리합니다."""
    return get_local_external_db_path(dataset, db_dir)


def _open_readonly(path: Path):
    # CSV 모드와 CLI 도움말은 DuckDB 설치 없이도 사용 가능하도록 지연 import.
    _duckdb()
    from common.db_client import open_local_external_connection
    return open_local_external_connection(path)


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError('DuckDB가 없습니다. python -m pip install duckdb 를 실행하세요.') from exc
    return duckdb


def _load_csv(path: Path, kind: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f'{kind} 파일이 없습니다: {path}')
    df = pd.read_csv(path, dtype={'item_id': 'string', 'review_id': 'string'})
    df = df.rename(columns={new: old for new, old in COLUMN_ALIASES.items() if new in df and old not in df})
    if not REQUIRED[kind].issubset(df.columns):
        raise ValueError(f'{path.name}: 누락 컬럼 {sorted(REQUIRED[kind] - set(df.columns))}')
    if not len(df):
        raise ValueError(f'{path.name}: 데이터가 비어 있습니다.')
    return df


def _first(*paths: Path) -> Path:
    """있는 파일 중 첫 번째(모두 없으면 첫 후보를 돌려 '파일 없음' 오류를 냅니다)."""
    return next((p for p in paths if p.is_file()), paths[0])


def _sources(folder: Path, project: Path) -> dict[str, Path]:
    """수집기(external_data/)가 database/ 에 저장한 새 이름을 먼저 찾고, 없으면 예전 이름을 찾습니다.

    무신사: --source 폴더(예: database/musinsa/20261123)의 product_info.csv → add_info.csv
    네이버: database/naver/keyword_trend.csv → shopping_daily.csv
    유튜브: database/youtube/keyword_candidates/ 의 가장 최근 파일 → 예전 keyword_candidates_rule_20261123.csv
    """
    weekly = sorted((project / 'database/youtube/keyword_candidates').glob('keyword_candidates_*.csv'))
    return {
        'ranking': folder / 'ranking.csv',
        'add_info': _first(folder / 'product_info.csv', folder / 'add_info.csv'),
        'review': folder / 'review.csv',
        'naver': _first(project / 'database/naver/keyword_trend.csv', project / 'database/naver/shopping_daily.csv',
                        folder / 'keyword_trend.csv', folder / 'shopping_daily.csv'),
        'youtube': _first(*weekly[-1:], project / 'database/youtube/keyword_candidates_rule_20261123.csv',
                          folder / 'keyword_candidates_rule_20261123.csv'),
    }


# 수집기의 새 컬럼 이름 → 적재 테이블이 쓰는 이름
COLUMN_ALIASES = {'product_gender': 'gender', 'reviewer_gender': 'gender'}


def prepare_frames(source: Path, project: Path = ROOT) -> dict[str, pd.DataFrame]:
    paths = _sources(source, project)
    ranking = _load_csv(paths['ranking'], 'ranking')
    info = _load_csv(paths['add_info'], 'add_info')
    review = _load_csv(paths['review'], 'review')
    for kind, frame in (('ranking', ranking), ('add_info', info), ('review', review)):
        frame['category'] = frame['category'].astype('string').str.strip()
        frame['item_id'] = frame['item_id'].astype('string').str.strip()
        if frame['category'].isna().any() or not frame['category'].isin(CATEGORIES).all():
            raise ValueError(f'{kind}: 알 수 없는 카테고리 포함')
    for kind, frame, keys in (('ranking', ranking, ['category', 'item_id']),
                               ('add_info', info, ['category', 'item_id']),
                               ('review', review, ['category', 'item_id', 'review_id'])):
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            raise ValueError(f'{kind}: 키 결측/중복 {keys}')
    product_keys = set(zip(ranking.category, ranking.item_id))
    if set(zip(info.category, info.item_id)) != product_keys:
        raise ValueError('무신사 add_info 상품 키가 ranking과 일치하지 않습니다.')
    if not set(zip(review.category, review.item_id)).issubset(product_keys):
        raise ValueError('무신사 리뷰에 ranking에 없는 상품 키가 있습니다.')
    naver_raw = _load_csv(paths['naver'], 'naver')
    naver_raw['날짜'] = pd.to_datetime(naver_raw['날짜'], errors='raise')
    if naver_raw['날짜'].duplicated().any():
        raise ValueError('네이버 날짜 중복')
    if not naver_raw['날짜'].is_monotonic_increasing:
        raise ValueError('네이버 날짜가 오름차순이 아닙니다.')
    keywords = [c for c in naver_raw if c != '날짜']
    if not keywords or len(set(keywords)) != len(keywords):
        raise ValueError('네이버 키워드가 없거나 중복입니다.')
    # 키워드별 독립 0~100 상대 지수. 서로 다른 키워드끼리 합산/비교 금지.
    for kw in keywords:
        values = pd.to_numeric(naver_raw[kw], errors='raise')
        if values.isna().any() or not values.between(0, 100).all():
            raise ValueError(f'네이버 관심도 값 오류: {kw}')
    naver = naver_raw.melt(id_vars=['날짜'], var_name='keyword', value_name='ratio')
    naver = naver.rename(columns={'날짜': 'period'})
    naver['period'] = naver['period'].dt.date
    naver['request_id'] = 'shopping_daily'
    # 수집일이 CSV에 없으므로 임의로 생성하지 않음.
    naver['collected_at'] = pd.NaT
    naver['normalization'] = 'per_keyword_independent'
    youtube_raw = _load_csv(paths['youtube'], 'youtube')
    youtube = youtube_raw.rename(columns={'상품 카테고리': 'category', '키워드': 'keyword',
                                           '등장 채널 수': 'channel_count', '근거 링크': 'links', '기준': 'basis'})
    youtube['channel_count'] = pd.to_numeric(youtube['channel_count'], errors='raise')
    if youtube['channel_count'].isna().any() or (youtube['channel_count'] < 0).any():
        raise ValueError('유튜브 채널 수 오류')
    stamp = __import__('re').search(r'(20\d{2})(\d{2})(\d{2})', paths['youtube'].stem)
    youtube['collected_at'] = '-'.join(stamp.groups()) if stamp else None
    youtube['source_file'] = paths['youtube'].name
    youtube['links'] = youtube['links'].fillna('')
    return {'musinsa_ranking': ranking, 'musinsa_add_info': info,
            'musinsa_review': review, 'naver_trend': naver, 'youtube_candidate': youtube}



def _quote_ident(name: str) -> str:
    """컬럼명에 한글·공백·따옴표가 포함되어도 안전하게 식별자를 인용한다."""
    return '"' + str(name).replace('"', '""') + '"'


def _sql_type(series: pd.Series) -> str:
    """CSV COPY 시 Pandas의 주요 타입과 식별자 문자열을 보존한다."""
    from pandas.api.types import (
        is_bool_dtype, is_datetime64_any_dtype,
        is_integer_dtype, is_float_dtype,
    )
    if is_bool_dtype(series.dtype):
        return 'BOOLEAN'
    if is_integer_dtype(series.dtype):
        return 'BIGINT'
    if is_float_dtype(series.dtype):
        return 'DOUBLE'
    if is_datetime64_any_dtype(series.dtype):
        return 'TIMESTAMP'
    return 'VARCHAR'


def import_data(source: Path, db_dir: Path | None = None) -> dict[str, int]:
    """입력 CSV 전체를 검증한 후 데이터별 DB 파일에 분리 적재.

    각 DB는 임시 파일을 완전히 작성한 후 교체해 부분 적재 DB를 노출하지 않는다.
    서로 다른 세 DB 사이의 원자적 동시 교체는 지원하지 않는다.
    """
    frames = prepare_frames(source)
    engine = _duckdb()
    folder = db_dir or (ROOT / 'database')
    folder.mkdir(parents=True, exist_ok=True)
    staged = {}
    counts = {}
    try:
        # Python DataFrame 등록(register/from_df)은 DuckDB 2.0에서
        # ResourceInUseException을 유발할 수 있어 SQL COPY로 적재한다.
        # CSV는 이 호출 동안만 존재하고 적재 완료 시 자동 정리된다.
        with tempfile.TemporaryDirectory(prefix='cart_external_import_') as temp_dir:
            csv_folder = Path(temp_dir)
            for dataset in DB_FILES:
                target = db_path(dataset, db_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.stem + f'.staging_{uuid.uuid4().hex}.duckdb')
                staged[dataset] = (temporary, target)
                con = engine.connect(str(temporary))
                try:
                    for table, df in frames.items():
                        if TABLE_DATASET[table] != dataset:
                            continue
                        csv_file = csv_folder / f'{table}.csv'
                        df.to_csv(csv_file, index=False, encoding='utf-8')
                        columns = ', '.join(
                            f'{_quote_ident(column)} {_sql_type(df[column])}'
                            for column in df.columns
                        )
                        con.execute(f'CREATE TABLE {_quote_ident(table)} ({columns})').fetchall()
                        escaped_path = str(csv_file).replace("'", "''")
                        con.execute(
                            f"COPY {_quote_ident(table)} FROM '{escaped_path}' "
                            "(FORMAT CSV, HEADER TRUE, NULL '')"
                        ).fetchall()
                        actual = con.execute(
                            f'SELECT COUNT(*) FROM {_quote_ident(table)}'
                        ).fetchone()[0]
                        if actual != len(df):
                            raise ValueError(f'{table}: 적재 건수 불일치 {actual} != {len(df)}')
                        counts[table] = actual
                finally:
                    con.close()
        # 세 DB를 모두 완성한 뒤 교체. 교체 자체는 DB별로 원자적이다.
        for temporary, target in staged.values():
            os.replace(temporary, target)
        return counts
    finally:
        for temporary, _ in staged.values():
            if temporary.exists():
                temporary.unlink()


def verify(db_dir: Path | None = None) -> dict:
    """세 개 DB 모두 존재하고 실제 테이블 및 핵심 조건이 정상인지 검증."""
    counts = {}
    paths = {}
    # DB 설치 여부보다 파일 유무를 먼저 확인해 명확한 오류를 출력
    for dataset in DB_FILES:
        path = db_path(dataset, db_dir)
        if not path.is_file():
            raise FileNotFoundError(f'{dataset} DuckDB 파일이 없습니다: {path}')
        paths[dataset] = str(path)
    for dataset in DB_FILES:
        path = db_path(dataset, db_dir)
        with _open_readonly(path) as con:
            for table in TABLES:
                if TABLE_DATASET[table] == dataset:
                    counts[table] = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if dataset == 'musinsa':
                categories = dict(con.execute('SELECT category, COUNT(*) FROM musinsa_ranking GROUP BY category').fetchall())
                if set(categories) != set(CATEGORIES) or any(v <= 0 for v in categories.values()):
                    raise ValueError('무신사 5개 카테고리 데이터가 완전하지 않습니다.')
            elif dataset == 'naver':
                naver_keys = con.execute('SELECT COUNT(DISTINCT keyword) FROM naver_trend').fetchone()[0]
                naver_days = con.execute('SELECT COUNT(DISTINCT period) FROM naver_trend').fetchone()[0]
                if naver_keys * naver_days != counts['naver_trend']:
                    raise ValueError('네이버 날짜×키워드 건수가 일치하지 않습니다.')
    if any(not count for count in counts.values()):
        raise ValueError('비어 있는 테이블이 있습니다.')
    return {'tables': counts, 'categories': categories,
            'naver_keywords': naver_keys, 'naver_days': naver_days,
            'db_files': paths}


def fetch(table: str, category: str | None = None) -> pd.DataFrame:
    if table not in TABLES:
        raise ValueError('허용되지 않은 테이블')
    db = db_path(TABLE_DATASET[table])
    if not db.is_file():
        raise FileNotFoundError(f'{table} 테이블 DB 파일이 없습니다: {db}')
    with _open_readonly(db) as con:
        if category is None:
            return con.execute(f'SELECT * FROM {table}').df()
        if TABLE_DATASET[table] != 'musinsa':
            raise ValueError('카테고리 필터를 지원하지 않는 테이블')
        if category not in CATEGORIES:
            raise ValueError('알 수 없는 카테고리')
        return con.execute(f'SELECT * FROM {table} WHERE category = ?', [category]).df()


def enabled() -> bool:
    return os.getenv('CART_SIGNAL_EXTERNAL_SOURCE', 'csv').lower() == 'duckdb'


def main():
    parser = argparse.ArgumentParser(description='CART_SIGNAL 외부 시장 데이터 3개 DuckDB 적재/검증')
    parser.add_argument('action', choices=['import', 'verify', 'inspect'])
    parser.add_argument('--source', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--db-dir', type=Path, help='세 DB가 생성될 디렉터리 (기본 database/)')
    args = parser.parse_args()
    result = (import_data(args.source.expanduser(), args.db_dir)
              if args.action == 'import' else verify(args.db_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
