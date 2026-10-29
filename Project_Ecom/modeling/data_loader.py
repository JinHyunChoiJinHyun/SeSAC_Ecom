import sys
from typing import Optional

import pandas as pd

from config import (
    PROJECT_ROOT,
    DB_PATH,
    TRAING_TABLE,
    ID_COLUMN,
    PREDICTION_HOUR_COLUMN,
    TIME_COLUMN,
    LABEL_COLUMN,
    FEATURE_COLUMNS,
    CATEGORICAL_FEATURES
)

# 모듈 경로에 현재 루트 추가
sys.path.insert(0, str(PROJECT_ROOT))

from database.db_client import get_duckdb_connection

# 학습 데이터에서 조회할 컬럼 조합
REQUIRED_COLUMNS = (
    [ID_COLUMN, PREDICTION_HOUR_COLUMN, TIME_COLUMN]
    + FEATURE_COLUMNS
    + [LABEL_COLUMN]
)

KEY_COLUMN = [ID_COLUMN, PREDICTION_HOUR_COLUMN]

def _validate(df:pd.DataFrame) -> None:
    """학습 데이터 검증"""

    # 빈 값 검증
    if df.empty:
        raise ValueError(f"{TRAING_TABLE} 테이블에 데이터가 없습니다.")

    # 중복 값 검증
    duplicate_count = df.duplicated(subset=KEY_COLUMN).sum()
    if duplicate_count > 0:
        raise ValueError(f"{KEY_COLUMN} 중복 {duplicate_count}건이 있습니다.")

    # 라벨 값 검증
    label_values = set(df[LABEL_COLUMN].unique())
    if not label_values <= {0,1}:
        raise ValueError(f"label에 0/1 이외의 값이 존재합니다: {label_values}")

def load_training_data(prediction_hour:Optional[int] = None) -> pd.DataFrame:
    """DB에서 모델 학습 데이터를 시간순으로 조회
    
    Args:
        prediction_hour: 특정 예측 기준 시점 불러올 시 지정 (예: 24)
    """

    # DB 경로 확인
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DuckDB 파일이 없습니다: {DB_PATH}")

    # 조회 쿼리 작성
    select_columns = ",\n          ".join(f'"{column}"' for column in REQUIRED_COLUMNS) # 들여쓰기 위해서 공백 생성
    query = f"""
        SELECT
            {select_columns}
        FROM {TRAING_TABLE}
        WHERE (? IS NULL OR "{PREDICTION_HOUR_COLUMN}" = ?)
        ORDER BY "{TIME_COLUMN}", "{ID_COLUMN}"
    """

    # DB 조회
    conn = get_duckdb_connection(read_only=True)
    df = conn.execute(query, [prediction_hour, prediction_hour]).df() # 쿼리의 ?자리에 순서대로 입력

    # df 검증
    _validate(df)

    # 타입 변환
    df[TIME_COLUMN] = pd.to_datetime(df[TIME_COLUMN])
    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype("int8")
    for column in CATEGORICAL_FEATURES:
        df[column] = df[column].astype("category") # 숫자를 카테고리 타입으로 변환 (숫자를 크기가 아닌 종류로 이해)

    return df

# if __name__ == "__main__":
#     df = load_training_data(24)
#     print(df)
    



    