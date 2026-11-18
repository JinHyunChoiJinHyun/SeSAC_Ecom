"""공통 Tool — 결과 표(DataFrame)를 CSV 파일로 저장.

Slack(interfaces/slack/delivery.py)과 FastAPI(backend/services/chat_service.py)가 같은 규칙으로
결과 CSV를 만들도록 한곳에 모았습니다. 특화 Agent도 결과 파일이 필요하면 이 Tool을 씁니다.

규칙
    - 표 선택 우선순위: processed_dataframe(전처리 결과) → dataframe(조회 결과)
    - 0행이면 만들지 않음, MAX_AUTO_CSV_ROWS 초과면 만들지 않음 (사유를 돌려줌)
    - utf-8-sig로 저장 (Excel에서 한글이 깨지지 않게)
    - 저장 위치: delivery_output/  (환경변수 CART_SIGNAL_CSV_OUTPUT_DIR로 변경 가능)
    - 한 결과에 대해 한 번만 만듦: 만든 경로를 result["result_csv_path"]에 기록하고 다시 호출하면 재사용
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]

CSV_OUTPUT_DIR = Path(os.getenv("CART_SIGNAL_CSV_OUTPUT_DIR") or PROJECT_ROOT / "delivery_output")

# 이 행 수를 넘는 표는 CSV 자동 생성을 생략합니다 (파일 생성·업로드가 오래 걸림)
MAX_AUTO_CSV_ROWS = 100_000

LOGGER = logging.getLogger("cart_signal.output_tools")


def ensure_output_dir() -> Path:
    CSV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return CSV_OUTPUT_DIR


def select_result_dataframe(result: dict):
    """결과 dict에서 전달할 표를 고릅니다. 반환: (DataFrame 또는 None, "processed"|"data"|None)"""

    processed = result.get("processed_dataframe")
    if isinstance(processed, pd.DataFrame):
        return processed, "processed"
    dataframe = result.get("dataframe")
    if isinstance(dataframe, pd.DataFrame):
        return dataframe, "data"
    return None, None


def save_dataframe_to_csv(df: pd.DataFrame, result_type: str) -> Path:
    """DataFrame을 CSV로 저장하고 경로를 돌려줍니다."""

    folder = ensure_output_dir()
    file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}_{result_type}_result.csv"
    path = folder / file_name
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def prepare_result_csv(result: dict) -> dict:
    """결과 표 CSV를 (필요하면) 만들고 상태를 돌려줍니다.

    반환: {"path": Path|None, "skipped_reason": str, "error": str, "result_type": str|None, "row_count": int}
    결과 dict에도 result_csv_path / csv_skipped_reason 를 기록해 두 번 만들지 않습니다.
    """

    df, result_type = select_result_dataframe(result)
    status = {"path": None, "skipped_reason": "", "error": "", "result_type": result_type,
              "row_count": int(len(df)) if df is not None else 0}

    existing = result.get("result_csv_path")
    if existing and Path(str(existing)).is_file():
        status["path"] = Path(str(existing))
        return status
    if df is None:
        return status
    if len(df) == 0:
        status["skipped_reason"] = "결과가 0행이라 CSV를 첨부하지 않았습니다."
    elif len(df) > MAX_AUTO_CSV_ROWS:
        status["skipped_reason"] = (
            f"결과가 {len(df):,}행으로 커서 전체 CSV 자동 첨부를 생략했습니다. "
            f"(자동 첨부 기준: {MAX_AUTO_CSV_ROWS:,}행 이하)"
        )
    else:
        try:
            status["path"] = save_dataframe_to_csv(df, result_type)
            result["result_csv_path"] = str(status["path"])
        except Exception as exc:
            status["error"] = repr(exc)
            LOGGER.exception("CSV 생성 실패 (%s)", type(exc).__name__)
    if status["skipped_reason"]:
        result["csv_skipped_reason"] = status["skipped_reason"]
    return status
