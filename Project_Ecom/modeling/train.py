import logging

import numpy as np
import pandas as pd

from sklearn.metrics import(
    average_precision_score,
    brier_score_loss,
    roc_auc_score
)

from config import(
    MODEL_DIR,
    REPORT_DIR,
    FEATURE_COLUMNS,
    LABEL_COLUMN
)

from data import temporal_split, load_training_data
from models import build_models

def evaluate_predictions(y_true, probabilities) -> dict:
    """구매 확률 예측 성능 평가"""
    # y_test에 대한 정답에 구매(1)와 미구매(0)가 모두 있는지 검증
    has_both_labels = pd.Series(y_true).nunique() == 2
    return {
        "positive_rate": float(np.mean(y_true)), # PR-AUC 비교 기준점
        "pr-auc": average_precision_score(y_true, probabilities) if 
    }

def train_models_for_prediction_hour(df:pd.DataFrame, prediction_hour:int) -> list[dict]:
    """한 예측 기준 시점에 대해 모든 모델을 학습시키고 평가 및 저장한다"""
    train_df, test_df = temporal_split(df)

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df[LABEL_COLUMN]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df[LABEL_COLUMN]

    # train 데이터 라벨에 구매(1)와 미구매(0)가 모두 있는지 검증
    if y_train.nunique() < 2:
        raise ValueError(f"{prediction_hour}시간 학습 데이터에 구매/미구매가 모두 있지 않습니다.")
    
    results = []
    for model_name, model in build_models().items():
        # 모델 학습
        model.fit(X_train, y_train)

        # 모델 예측
        probabilities = model.predict_proba(X_test)[:, 1]

        results.append({
            "prediction_hour": prediction_hour,
            "model": model_name,
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            **evaluate_predictions
        })





def main():
    # 폴더 생성
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    #
