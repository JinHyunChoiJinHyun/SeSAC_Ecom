import pandas as pd
import numpy as np

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

def build_models() -> dict:
    return {
        # 상품 종류만 사용
        "baseline_category": PurchaseRateBaseline("product_category"),
        # 행동 규칙 사용
        "baseline_remove": PurchaseRateBaseline("same_sku_remove_count_after_cart")
    }
