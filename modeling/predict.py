"""예측 결과 저장 배치
- 라벨이 없는 이벤트의 구매 확률 예측 및 저장
"""

import sys
import logging
from datetime import datetime

import joblib
import pandas as pd
from config import(
    PROJECT_ROOT,
    MODEL_DIR,
    SQL_DIR,
    PREDICTION_HOURS,
    PURCHASE_DEADLINE_DAYS,
    FEATURE_COLUMNS,
    ID_COLUMN,
    PREDICTION_HOUR_COLUMN,
    TIME_COLUMN,
    PREDICTION_MODEL_NAME,
    PREDICTIONS_TABLE,
    PROMOTION_TARGET_PERCENTILES,
    get_model_name
)

# 모듈 경로에 현재 루트 추가
sys.path.insert(0, str(PROJECT_ROOT))

from modeling.data_loader import load_prediction_data
from database.db_client import get_duckdb_connection

logger = logging.getLogger(__name__)

# 예측 결과 테이블 생성 쿼리
CREATE_PREDICTIONS_TABLE_SQL = SQL_DIR / "create_predictions_table.sql"

# 저장할 컬럼
PREDICTION_RESULT_COLUMNS = [
    "predicted_at",
    ID_COLUMN,
    "client_id",
    "sku",
    PREDICTION_HOUR_COLUMN,
    TIME_COLUMN,
    "purchase_probability",
    "promotion_segment",
    "model_name"
]

# 프로모션 구분 값
SEGMENT_LOW = "low" # 쿠폰을 줘도 사지 않을 가능성 높음 -> 제외
SEGMENT_TARGET = "target" # 구매 확률이 애매한 고객 -> 프로모션 대상
SEGMENT_HIGH = "high" # 쿠폰 없이도 살 가능성이 높음 -> 제외

def assign_promotion_segment(purchase_probabilities: pd.Series) -> pd.Series:
    """구매 확률 순위로 프로모션 구분 표시
    low: 하위 경계 미만
    target: [하위 경계, 상위 경계) 사이
    high: 상위 경계 이상
    """
    lower, upper = PROMOTION_TARGET_PERCENTILES
    lower_value = purchase_probabilities.quantile(lower) # 하위 경계 지점의 구매 확률
    upper_value = purchase_probabilities.quantile(upper) # 상위 경계 지점의 구매 확률

    # 구분 값 계산
    segment = pd.Series(SEGMENT_LOW, index=purchase_probabilities.index) # 모든 값이 "low"인 열 생성
    segment[purchase_probabilities >= lower_value] = SEGMENT_TARGET # 조건이 일치하면 "target"으로 변환
    segment[purchase_probabilities >= upper_value] = SEGMENT_HIGH # 조건이 일치하면 "high"로 덮어쓰기

    return segment

def predict_purchase_probability(prediction_hour: int, predicted_at: datetime) -> pd.DataFrame:
    """예측 기준 시점에 대해 구매 확률 계산"""
    df = load_prediction_data(prediction_hour)

    # 예측 대상이 없는 경우 빈 df 반환 (모델을 불러오기 전에 확인하여 불필요한 로드 및 빈 입력 예측 방지)
    if df.empty: 
        # 예측 대상이 없는 경우 빈 df 반환 (장바구니 이벤트가 없거나 이미 구매한 정상 상황)
        logger.info("%s시간 예측 대상 없음", prediction_hour)
        return pd.DataFrame(columns=PREDICTION_RESULT_COLUMNS)

    # 저장된 모델 불러와 구매 확률 계산
    model_name = get_model_name(PREDICTION_MODEL_NAME, prediction_hour)
    model = joblib.load(MODEL_DIR / f"{model_name}.joblib")

    purchase_probability = model.predict_proba(df[FEATURE_COLUMNS])[:, 1] # 구매 확률(두번째 행)만 반환

    # 저장할 결과
    result_df = df[[ID_COLUMN, "client_id", "sku", PREDICTION_HOUR_COLUMN, TIME_COLUMN]].copy() # 필요한 컬럼만 복사
    result_df["predicted_at"] = predicted_at
    result_df["purchase_probability"] = purchase_probability
    result_df["promotion_segment"] = assign_promotion_segment(result_df["purchase_probability"])
    result_df["model_name"] = model_name

    logger.info("%s시간 예측 완료 (%s건)", prediction_hour, len(result_df))
    return result_df[PREDICTION_RESULT_COLUMNS]

def save_predictions(prediction_results_df: pd.DataFrame, predicted_at: datetime) -> None:
    """예측 결과 DB 저장"""

    # 예측에 사용된 모델들의 이름 저장
    model_names = [get_model_name(PREDICTION_MODEL_NAME, hour) for hour in PREDICTION_HOURS]
    model_name_placeholders = ", ".join("?" for _ in model_names) # 실행된 모델의 수만큼 ? 생성
    
    conn = get_duckdb_connection(read_only=False)
    
    try:
        conn.begin()

        # 예측 결과 테이블 생성 (없는 경우만)
        conn.execute(CREATE_PREDICTIONS_TABLE_SQL.read_text(encoding="utf-8"))

        # 실행 날짜 + 실행된 모델 전체의 기존 결과 삭제 (df 값 존재 유무와 관계없이 삭제)
        conn.execute(f"""
            DELETE FROM {PREDICTIONS_TABLE}
            WHERE CAST(predicted_at AS DATE) = CAST(? AS DATE)
            AND model_name IN ({model_name_placeholders})
        """,[predicted_at, *model_names]) # 첫번쨰 ?에 실행 시각, 나머지 ?에 모델 이름 입력

        # 새 df 결과 값이 존재할 떄만 값 추가
        if not prediction_results_df.empty:

            # df를 테이블처럼 사용할 수 있게 등록
            conn.register("new_predictions", prediction_results_df)

            # 등록한 df 테이블(new_predictions)을 조회해 예측 결과 테이블에 추가
            column_list = ", ".join(f'"{column}"' for column in PREDICTION_RESULT_COLUMNS)
            conn.execute(f"""
                INSERT INTO {PREDICTIONS_TABLE} ({column_list})
                SELECT {column_list} FROM new_predictions
            """)

        conn.commit() # 트랜잭션 내 삭제와 삽입 함께 반영 (모두 성공 시 반영 하나라도 실패 시 롤백)

    except Exception:
        conn.rollback()
        logger.exception("예측 결과 저장 실패 - 변경 사항 되돌림")
        raise

    finally:
        conn.close()

def summarize_predictions(prediction_results_df: pd.DataFrame) -> pd.DataFrame:
    """시점별 예측 확률 분포의 프로모션 대상 구간 확률 범위(중하위 구간) 요약"""

    # 프로모션 대상 구간의 하위/상위 경계
    lower, upper = PROMOTION_TARGET_PERCENTILES

    # 시점별 예측 확률 요약
    summary = (
        prediction_results_df
        .groupby(PREDICTION_HOUR_COLUMN)["purchase_probability"]
        .describe(percentiles=[0.5,lower,upper]) # 중앙값과 프로모션 대상 구간 하위/상위 경계 지점 구매 확률 추가
    )

    # summary에서 필요한 컬럼만 반환
    lower_col, upper_col = f"{lower:.0%}", f"{upper:.0%}"
    summary = summary[["count", "mean", "50%", lower_col, upper_col, "max"]]

    # 시점별 프로모션 구분 건수
    segment_counts = (
        prediction_results_df
        .groupby([PREDICTION_HOUR_COLUMN, "promotion_segment"]) # 시점, 구분 조합으로 구분
        .size() # 조합별 장바구니 건수
        .unstack(fill_value=0) # 구분 값(low/target/high)을 컬럼으로 펼침 (없으면 0으로 채움)
        .reindex(columns=[SEGMENT_LOW, SEGMENT_TARGET, SEGMENT_HIGH], fill_value=0) # 컬럼 순서 지정
    )

    return summary.join(segment_counts)

def main():
    # 예측 시점 설정
    predicted_at = datetime.now()

    # 예측 시점별 예측 후 빈 df 제외
    predictions = [predict_purchase_probability(hour, predicted_at) for hour in PREDICTION_HOURS]
    non_empty_predictions = [df for df in predictions if not df.empty]

    # 시점별 예측 결과 결합
    prediction_results_df = (
        pd.concat(non_empty_predictions, ignore_index=True) # if 조건 만족 시 예측 결과 결합
        if non_empty_predictions
        else pd.DataFrame(columns=PREDICTION_RESULT_COLUMNS) # 예측 결과가 모두 비었을 경우 컬럼만 있는 빈 df 생성
    ) # 엑셀 IF와 같은 문법

    # df 저장 (결과가 비어도 호출 필요)
    save_predictions(prediction_results_df, predicted_at)

    if prediction_results_df.empty:
        logger.info("저장할 예측 결과가 없어 오늘 날짜의 기존 결과만 삭제했습니다.")
        return

    # 실행 결과 요약
    logger.info(
        "예측 결과 저장 완료 (%s건)\n%s",
        len(prediction_results_df),
        summarize_predictions(prediction_results_df)
    )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
    
    




