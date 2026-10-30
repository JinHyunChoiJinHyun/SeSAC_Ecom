import logging
import sys

import joblib
import numpy as np
import pandas as pd

from sklearn.metrics import(
    average_precision_score,
    brier_score_loss,
    roc_auc_score
)

from config import(
    PROJECT_ROOT,
    MODEL_DIR,
    REPORT_DIR,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    PREDICTION_HOURS,
    PURCHASE_DEADLINE_DAYS,
    get_model_file_name
)

# 모듈 경로에 현재 루트 추가
sys.path.insert(0, str(PROJECT_ROOT))

from data import temporal_split, load_training_data
from models import build_models

logger = logging.getLogger(__name__)

# 결과 해석 예시 (24시간, baseline_category)
#   positive_rate 0.0268 : 무작위로 뽑았는데 실제 구매자일 확률 (테스트 전체 중 실제 구매 약 2만 9천 건)
#   pr_auc        0.0325 : 모델이 기준을 두고 뽑았는데 실제 구매자일 확률 3.2% (positive_rate보다 약 1.2배 나음)
#   roc_auc       0.5501 : 0.5(무작위)에 가까움 → 카테고리만으로는 구분 능력이 거의 없음
#   brier_score   0.02609: 각 이벤트마다 예측한 구매 확률이 크게 부풀려지진 않았으나 기준 없이 positive_rate로 예측한 MSE(0.02605)와 비슷하므로 잘 예측하지 못함
def evaluate_predictions(y_true, probabilities) -> dict:
    """
    구매 확률 예측 성능 평가

    Args:
        y_true: 테스트할 데이터의 실제 정답 (구매 1 / 미구매 0)
        probabilities: 모델이 예측한 구매 확률 (0 ~ 1)
    """
    # 테스트할 데이터의 실제 정답에 구매(1)와 미구매(0)가 모두 있는지 검증
    has_both_labels = pd.Series(y_true).nunique() == 2
    return {
        # 무작위로 뽑았는데 구매자일 확률 (PR-AUC의 기준점)
        "positive_rate": float(np.mean(y_true)), 

        # 모델이 기준(예측 확률)을 두고 높은 순서로 뽑았을 때 실제 구매자의 비율 (positive_rate보다 높고 1에 가까울수록 잘 예측)
        "pr_auc": average_precision_score(y_true, probabilities) if has_both_labels else np.nan, 

        # 구매 확률이 구매자와 미구매자를 잘 구분하는지 (1에 가까울수록 잘 구분)
        "roc_auc": roc_auc_score(y_true, probabilities) if has_both_labels else np.nan, 

        # 예측 확률과 실제 라벨의 평균제곱오차(MSE)로 구매 확률의 정확도 평가 (0에 가까울수록 정확) * MSE = (예측 확률 - 라벨)²의 평균
        # 실제 구매(1)일 때  0.9 예측 → 0.01 (확실히 맞힘)  /  0.1 예측 → 0.81 (확실히 틀림)
        # 실제 미구매(0)일 때 0.1 예측 → 0.01 (확실히 맞힘)  /  0.9 예측 → 0.81 (확실히 틀림)
        "brier_score": brier_score_loss(y_true, probabilities) 
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
            **evaluate_predictions(y_test, probabilities) # dict를 하나씩 꺼내서 반환
        })

        # 학습이 끝난 모델 저장        
        model_file_name = get_model_file_name(model_name, prediction_hour)
        model_path = MODEL_DIR / f"{model_file_name}.joblib"
        joblib.dump(model, model_path)
        logger.info("%s시간 %s 완료", prediction_hour, model_name)

    return results

def main():
    # 폴더 생성
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 모델 학습 시작
    all_result = []
    for prediction_hour in PREDICTION_HOURS:
        df = load_training_data(prediction_hour=prediction_hour)
        all_result.extend(train_models_for_prediction_hour(df, prediction_hour))

    # 레포트 생성
    report = pd.DataFrame(all_result)
    report_path = REPORT_DIR / f"model_comparison_deadline_{PURCHASE_DEADLINE_DAYS}d.csv"
    report.to_csv(report_path, index=False)
    print(report.to_string(index=False))

if __name__ == "__main__":
    # 로그 레벨 설정
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()

