from pathlib import Path

# 기준 경로
BASE_DIR = Path(__file__).resolve().parent.parent

# 학습 데이터 설정
LABEL_COLUMN = "label"
TIME_COLUMN = "add_to_cart_timestamp"
ID_COLUMN = "cart_event_id"

FEATURE_COLUMNS = [
    "page_visits_7d",
    "search_count_7d",
    "past_sku_buy_count",
    "product_category",
    "price_group",
]

CATEGORICAL_COLUMNS = [
    "product_category",
    "price_group",
]

NUMERIC_COLUMNS = [
    "page_visits_7d",
    "search_count_7d",
    "past_sku_buy_count",
]

PREDICTION_DAYS = 7