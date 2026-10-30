"""예측 결과 저장 배치
- 라벨이 없는 이벤트의 구매 확률 예측 및 저장
"""

import logging
from datetime import datetime

import joblib
import pandas as pd
from config import(
    MODEL_DIR,
    PREDICTION_HOURS,
    PURCHASE_DEADLINE_DAYS,
    FEATURE_COLUMNS,
    ID_COLUMN,
    PREDICTION_HOUR_COLUMN,
    TIME_COLUMN,
    PREDICTION_MODEL_NAME,
    PREDICTIONS_TABLE,
    get_model_file_name
)

from data import load_prediction_data
from database.db_client import get_duckdb_connection

def predict_purchase_probability(prediction_hour: int, predicted_at: datetime) -> pd.DataFrame:
    """예측 기준 시점에 대해 구매 확률 계산"""
    try:
        df = load_prediction_data(prediction_hour)
    except ValueError:
        # 예측 대상이 없는 경우 빈 값 반환 (장바구니 이벤트가 없거나 이미 구매한 정상 상황)
        logging.info("%s시간 예측 대상 없음", prediction_hour)
        return pd.DataFrame()

    # 저장된 모델 불러와 구매 확률 계산
    model_file_name = get_model_file_name(PREDICTION_MODEL_NAME, prediction_hour)
    model = joblib.load(MODEL_DIR / f"{model_file_name}.joblib")

    purchase_probability = model.predict_proba(df[FEATURE_COLUMNS])[:, 1] # 구매 확률(두번째 행)만 반환

    # 저장할 결과
    return pd.DataFrame({
        "predicted_at": predicted_at,
        ID_COLUMN: df[ID_COLUMN].to_numpy(),
        "client_id": df["client_id"].to_numpy(),
        "sku": df["sku"].to_numpy(),
        PREDICTION_HOUR_COLUMN: df[PREDICTION_HOUR_COLUMN].to_numpy(),
        TIME_COLUMN: df[TIME_COLUMN].to_numpy(),
        "purchase_probability": df["purchase_probability"].to_numpy(),
        "model_name": df["model_name"].to_numpy()
    })

# 호출 예시: 어제 담은 장바구니 고객 구매 확률 예측해 줘
def main():
    # 예측 시점 설정
    predicted_at = datetime.now()

    # 예측 시점별 예측 후 빈 df 제외
    predictions = [predict_purchase_probability(hour, predicted_at) for hour in PREDICTION_HOURS]
    non_empty_predictions = [df for df in predictions if not df.empty]

    # 값이 있는 df가 없을 시 종료
    if not non_empty_predictions:
        logging.info("저장할 예측 결과가 없습니다.")
        return

    # df 결합
    predictions_df = pd.concat(non_empty_predictions, ignore_index=True)

    # df 저장
    




