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

# 호출 예시: 어제 담은 장바구니 고객 구매 확률 예측해 줘
