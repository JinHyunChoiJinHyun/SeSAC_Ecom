"""구매 예측 모델 패키지 — 기준일별 하나의 계층형 로지스틱 회귀로 학습 가능한 모든 품목·색상의 고객 점수 (프로젝트 기본 방식)"""
from .data import HMData, article_attributes, connect_parquet, connect_team, from_connection
from .errors import (
    ArticleNotFound,
    ComboNotFound,
    DataIncomplete,
    FutureModel,
    InsufficientHistory,
    InvalidCombo,
    ModelMismatch,
    ModelNotReady,
    NotConverged,
    SaveFailed,
    SingleClass,
    TargetingError,
    UnsupportedCombo,
)
from .features import FeatureSet, build_features, training_mondays
from .model import (
    MODELS_DIR,
    ComboScores,
    PurchaseModel,
    list_models,
    load_model,
    valid_score_dates,
    save_model,
    score_article,
    score_combo,
    score_model,
    select_model,
    supported_combos,
    train_model,
)
from .taste import target_from_article, taste_scores

__version__ = "0.1.0"
