from pathlib import Path

# 경로
PROJECT_ROOT = Path(__file__).resolve().parents[1] # 프로젝트 기준 경로: Project_Ecom
DB_PATH = PROJECT_ROOT / "database" / "synerise.duckdb"
MODEL_DIR = PROJECT_ROOT / "modeling" / "models"
REPORT_DIR = PROJECT_ROOT / "modeling" / "reports"

# MART 데이터
TRAINING_TABLE = "mart.purchase_training_data"

ID_COLUMN = "cart_event_id"
LABEL_COLUMN = "label"
TIME_COLUMN = "cart_timestamp"
PREDICTION_HOUR_COLUMN = "hours_after_cart"

PREDICTION_HOURS = [24, 48, 72] # 예측 기준 시점 (쿼리의 값과 같아야 함)

# 특징 컬럼
CATEGORICAL_FEATURE_COLUMNS = [
    "product_category",
]

NUMERIC_FEATURE_COLUMNS = [
    # 담기 전
    "page_visit_count_7d_before",
    "past_sku_buy_count",
    # 담은 후
    "page_visit_count_after_cart",
    "search_count_after_cart",
    "same_sku_remove_count_after_cart",
    "cart_add_count_after_cart",
    # 상품
    "product_price",
]

FEATURE_COLUMNS = NUMERIC_FEATURE_COLUMNS + CATEGORICAL_FEATURE_COLUMNS

# 학습
TEST_RATIO = 0.2 # 가장 최근 20% 기간을 테스트용으로 사용
RANDOM_SEED = 42
PURCHASE_DEADLINE_DAYS = 7 # 구매 데드라인 (변경 시 SQL 쿼리 변경 필요)

# 예측
PREDICTION_MODEL_NAME = "lightgbm"                   # 예측에 사용할 모델
PREDICTIONS_TABLE = "purchase_predictions"

def get_model_file_name(model_name: str, prediction_hour: int) -> str:
    """모델 파일 이름 규칙
    예) lightgbm_24h_deadline_7d
    """

    return f"{model_name}_{prediction_hour}h_deadline_{PURCHASE_DEADLINE_DAYS}d"
