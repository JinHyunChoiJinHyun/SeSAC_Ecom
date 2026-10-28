import pandas as pd

from config import (
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    TIME_COLUMN,
    ID_COLUMN
)

from env.db_client import get_duckdb_connection

def load_training_data() -> pd.DataFrame:
    query = """
            SELECT
                cart_event_id,
                add_to_cart
            """