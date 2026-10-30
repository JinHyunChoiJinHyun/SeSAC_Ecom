import pandas as pd
import numpy as np

from lightgbm import LGBMClassifier
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import (
    StandardScaler, 
    FunctionTransformer, 
    OneHotEncoder
)

from config import (
    NUMERIC_FEATURE_COLUMNS,
    CATEGORICAL_FEATURE_COLUMNS,
    RANDOM_SEED
)

# 컬럼 값별 구매율 베이스라인 생성 (예: 카테고리별, 장바구니 제거 횟수별)
# 타 모델은 라이브러리가 이미 클래스로 제공
class PurchaseRateBaseline:
    """한 컬럼의 값별 과거 구매율로 구매 확률 예측하는 베이스라인 모델"""

    MISSING_VALUE = "MISSING"

    def __init__(self, column:str, smoothing:int = 20):
        """
        Args:
            column: 구매율을 계산할 기준 컬럼 (예: product_category)
            smoothing: 
            - 모든 값에 전체 평균 구매율의 가상 이벤트를 섞어, 이벤트가 적은 값일수록 평균에 가깝게 보정
            - 표본이 적은 값은 우연에 의해 크게 흔들리므로, 표본이 많아 안정적인 전체 평균에 가깝게 보정
        """

        self.column = column
        self.smoothing = smoothing

    def _prepare_values(self, X:pd.DataFrame) -> pd.Series:
        """기준 컬럼 값을 문자열로 통일하고 결측치의 경우 'MISSING'으로 표시"""
        return X[self.column].astype("string").fillna(self.MISSING_VALUE)

    def fit(self, X:pd.DataFrame, y:pd.Series):
        """학습 단계: 학습 데이터로 전체 구매율과 컬럼 값별 스무딩 반영 구매율을 계산해 저장"""
        # 전체 구매율 계산
        self.overall_purchase_rate_ = y.mean() # 라벨의 평균 = 구매율

        # 기준 컬럼 값별 구매 수와 이벤트 수 집계 (스무딩 구매율 계산용)
        purchase_and_event_counts = (
            pd.DataFrame({"value": self._prepare_values(X), "label": y})
            .groupby("value", observed=True)["label"] # 값별 라벨을 묶어서 반환
            .agg(["sum", "count"]) # 라벨을 통해 구매 수(라벨의 합) / 이벤트 수(라벨의 수) 각각 계산
        )

        # 스무딩을 반영하여 값별 구매율 계산
        self.value_purchase_rates_ = (
            (purchase_and_event_counts["sum"] + self.smoothing * self.overall_purchase_rate_)
            / (purchase_and_event_counts["count"] + self.smoothing)
        )

        return self

    def predict_proba(self, X:pd.DataFrame) -> np.ndarray:
        positive = (
            self._prepare_values(X) # 학습 때와 같은 데이터 준비
            .map(self.value_purchase_rates_) # 컬럼 값을 학습한 구매율 표에서 찾아 구매율로 변환 (예: product_category가 3인 경우 학습 때 계산한 구매율인 0.12 반환)
            .astype(float) # 계산이 가능한 실수 타입으로 변환
            .fillna(self.overall_purchase_rate_) # 학습 떄 없던 값(신제품 등)의 경우 전체 구매율로 대체
            .to_numpy()
        )

        return np.column_stack([1 - positive, positive])

def to_string(X):
    """원-핫 인코딩을 위해 범주형 값을 문자열로 변환 (숫자와 빈 값이 섞여 있으면 에러 발생)"""
    return X.astype(str)

def make_logistic_model() -> Pipeline:
    """로지스틱 회귀 모델 생성 (숫자/범주형 컬럼 각각 전처리 후 학습)"""

    # 전처리
    preprocessor = ColumnTransformer([
        # 숫자 컬럼 전처리
        ("numeric", Pipeline([
            ("imputer", SimpleImputer(strategy="median")), # 숫자 컬럼의 빈 값을 중앙값으로 대체
            ("scaler", StandardScaler()) # 표준화 (큰 숫자 컬럼에 영향을 받지 않게 평균 0, 표준편차 1이 되도록 설정)
        ]), NUMERIC_FEATURE_COLUMNS),

        # 범주형 컬럼 전처리
        ("categorical", Pipeline([
            ("to_string", FunctionTransformer(to_string)), # 원-핫 인코딩을 위해 문자열로 통일 (NaN의 경우 "nan" 카테고리로 변환)
            ("encoder", OneHotEncoder( # 원-핫 인코딩 (로지스틱 회귀는 숫자만 받으므로 카테고리 값을 컬럼으로 변환 후 0/1로 값 변환)
                handle_unknown="infrequent_if_exist", # 학습에 없던 카테고리의 경우 에러 없이 드문 카테고리 묶음(infrequent)으로 처리
                min_frequency=100 # 학습 데이터 중 100건 미만인 카테고리의 경우 하나로 묶어서 처리
            ))
        ]), CATEGORICAL_FEATURE_COLUMNS)
    ])
    # 전처리와 모델을 하나로 묶음 (학습 데이터로 정한 전처리 기준값(예: 중앙값 = 3)을 테스트 데이터에도 동일하게 사용)
    return Pipeline([
        ("preprocessor", preprocessor),
        ("model", LogisticRegression(
            max_iter=1000, # 학습 반복 횟수 상한
            random_state=RANDOM_SEED # 결과 재현용 시드
        ))
    ])
def make_lightgbm_model() -> LGBMClassifier:
    """lightgbm 모델 생성"""
    # 카테고리 타입과 결측치를 직접 처리하므로 전처리 없이 사용 가능
    return LGBMClassifier(
        n_estimators=300, # 트리 개수 생성
        learning_rate=0.05, # 학습률 (트리 하나가 결과에 반영되는 정도)
        num_leaves=31, # 트리 하나의 최대 잎 수 (클수록 복잡한 규칙을 배우나 과적합 위험 증가)
        random_state=RANDOM_SEED,
        verbosity=-1 # 학습 중 출력 메세지 off
    )

def build_models() -> dict:
    return {
        # 상품 종류만 사용
        "baseline_category": PurchaseRateBaseline("product_category"),
        # 행동 규칙 사용
        "baseline_remove": PurchaseRateBaseline("same_sku_remove_count_after_cart"),
        # 로지스틱 회귀
        "logistic_regression": make_logistic_model(),
        # lightgbm
        "lightgbm": make_lightgbm_model()
    }
