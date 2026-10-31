import pandas as pd
from typing import Optional

from config import (
    TEST_RATIO,
    PURCHASE_DEADLINE_DAYS,
    TRAINING_TABLE,
    ID_COLUMN,
    PREDICTION_HOUR_COLUMN,
    TIME_COLUMN,
    LABEL_COLUMN,
    FEATURE_COLUMNS,
    CATEGORICAL_FEATURE_COLUMNS
)

from database.db_client import get_duckdb_connection

# 학습 데이터에서 조회할 컬럼 조합
TRAINING_COLUMNS = (
    [ID_COLUMN, PREDICTION_HOUR_COLUMN, TIME_COLUMN]
    + FEATURE_COLUMNS
    + [LABEL_COLUMN]
)

# 예측 데이터에서 조회할 컬럼 조합 (특징은 모델 입력, 나머지는 추후 예측 결과 테이블에 함꼐 저장해 대상 식별에 사용)
PREDICTION_COLUMNS = (
    [ID_COLUMN, PREDICTION_HOUR_COLUMN, TIME_COLUMN, "client_id", "sku"]
    + FEATURE_COLUMNS
)

KEY_COLUMN = [ID_COLUMN, PREDICTION_HOUR_COLUMN]

def _load_mart_data(
        columns: list[str], 
        is_labeled: bool,
        prediction_hour: Optional[int]
    ) -> pd.DataFrame:
    """MART 테이블에서 데이터를 시간순으로 조회

    Args:
        columns: 조회할 컬럼 목록
        is_labeled: True인 경우 라벨이 확정된 데이터 / False인 경우 라벨이 없는 데이터
        prediction_hour: 특정 예측 기준 시점 지정
    """
    # label 조건 설정
    label_filter = "IS NOT NULL" if is_labeled else "IS NULL"

    # 조회
    select_columns = ", ".join(f'"{column}"' for column in columns)

    query = f"""
        SELECT
            {select_columns}
        FROM {TRAINING_TABLE}
        WHERE "{LABEL_COLUMN}" {label_filter}
        AND (? IS NULL OR "{PREDICTION_HOUR_COLUMN}" = ?)
        ORDER BY "{TIME_COLUMN}", "{ID_COLUMN}"
    """
    # DB 조회
    conn = get_duckdb_connection(read_only=True)

    try:
        return conn.execute(query, [prediction_hour, prediction_hour]).df() # 쿼리의 ?자리에 순서대로 입력
    finally:
        conn.close()

def _validate_keys(df: pd.DataFrame, data_name: str) -> None:
    """빈 데이터와 키 중복 검증"""
    # 빈 값 검증
    if df.empty:
        raise ValueError(f"{data_name} 데이터가 없습니다.")

    # 중복 값 검증
    duplicate_count = df.duplicated(subset=KEY_COLUMN).sum()
    if duplicate_count > 0:
        raise ValueError(f"{data_name} 데이터에 {KEY_COLUMN} 중복 {duplicate_count}건이 있습니다.")

def _convert_types(df: pd.DataFrame) -> pd.DataFrame:
    """데이터 타입 변환 적용"""
    # 타입 변환
    df[TIME_COLUMN] = pd.to_datetime(df[TIME_COLUMN])
    for column in CATEGORICAL_FEATURE_COLUMNS:
        # 범주형 특징을 category 타입으로 변환 (숫자를 크기가 아닌 종류로 이해)
        df[column] = df[column].astype("category") 

    return df

def load_training_data(prediction_hour:Optional[int] = None) -> pd.DataFrame:
    """학습 데이터 조회: 데드라인이 지나 구매 여부(라벨)가 확정된 행만 조회하여 학습
    
    Args:
        prediction_hour: 특정 예측 기준 시점 불러올 시 지정 (예: 24)
    """
    # 쿼리 조회
    df = _load_mart_data(TRAINING_COLUMNS,is_labeled=True,prediction_hour=prediction_hour)

    # df 검증
    _validate_keys(df, "학습")

    # 라벨 값 검증 및 타입 변환
    label_values = set(df[LABEL_COLUMN].unique())
    if not label_values <= {0,1}:
        raise ValueError(f"label에 0/1 이외의 값이 존재합니다: {label_values}")

    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype("int8")

    # df 타입 변환 후 반환
    return _convert_types(df)

def load_prediction_data(prediction_hour: int) -> pd.DataFrame:
    """예측 데이터 조회: 데드라인이 지나지 않은 행만 조회하여 예측
    
    Args:
        prediction_hour: 특정 예측 기준 시점 불러올 시 지정 (예: 24)
    """
    # 예측 데이터 조회
    df = _load_mart_data(PREDICTION_COLUMNS, is_labeled=False, prediction_hour=prediction_hour)

    # 예측 대상이 없는 경우 에러가 아닌 빈 값 반환 (장바구니 이벤트가 없거나 이미 구매한 정상 상황)
    if df.empty:
        return df

    # df 검증
    _validate_keys(df, "예측")

    # df 타입 변환 후 반환
    return _convert_types(df)

def temporal_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """시간 순서로 학습/평가를 구분하고 라벨 기간만큼 간격 생성"""

    # 학습/테스트 구분 시점
    # 예) TEST_RATIO = 0.2 -> 앞 80%와 뒤 20% 구분하는 시점
    cutoff = df[TIME_COLUMN].quantile(1 - TEST_RATIO)

    # 학습/테스트 사이 간격 지정
    gap = pd.Timedelta(days=PURCHASE_DEADLINE_DAYS)

    # 학습/테스트 데이터 생성
    train_df = df[df[TIME_COLUMN] < cutoff - gap] # cutoff 이전 데드라인 기간의 이벤트는 라벨이 테스트 데이터와 겹치므로 제외 (예: cutoff가 22일인 경우 16일의 이벤트의 라벨 기간(16~23일)은 테스트 데이터에 포함됨)
    test_df = df[df[TIME_COLUMN] >= cutoff]

    if train_df.empty or test_df.empty:
        raise ValueError("학습 또는 테스트 데이터가 비어 있습니다.")

    return train_df, test_df