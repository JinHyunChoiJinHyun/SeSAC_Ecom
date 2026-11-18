"""모델링 결과 조회 Tool (읽기 전용) — 이후 Targeting Agent 등에서 사용.

modeling/predict.py가 저장한 mart.purchase_predictions(장바구니 구매 확률·프로모션 구분)를 읽습니다.
모델 학습·예측 실행은 하지 않습니다(배치: python run_modeling.py ...).

- 연결: common.db_client.get_duckdb_connection(read_only=True) → 모델링 DB를 절대 수정하지 않음
- 모델링 배치가 같은 DB에 쓰는 중이면 열지 못하므로, 예외 대신 error 메시지를 담아 반환합니다.
- 아직 Agent에 연결하지 않았습니다(Targeting Agent enabled=False).
"""

from pathlib import Path

import pandas as pd

from common.db_client import get_duckdb_connection, get_modeling_db_path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = PROJECT_ROOT / "modeling" / "models"
PREDICTIONS_TABLE = "mart.purchase_predictions"
SEGMENTS = ("low", "target", "high")
MAX_ROWS = 100_000


def get_modeling_status() -> dict:
    """모델링 DB·예측 테이블·모델 파일 상태를 반환합니다."""

    db_path = get_modeling_db_path()
    status = {
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "model_files": sorted(p.name for p in MODEL_DIR.glob("*.joblib")) if MODEL_DIR.exists() else [],
        "has_predictions": False,
        "latest_predicted_at": None,
        "segment_counts": {},
        "error": "",
    }
    if not status["db_exists"]:
        return status

    try:
        con = get_duckdb_connection(read_only=True)
    except Exception as exc:  # 배치가 쓰는 중(잠김) 등
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status

    try:
        exists = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'mart' AND table_name = 'purchase_predictions'"
        ).fetchone()[0]
        if not exists:
            return status
        latest = con.execute(f"SELECT MAX(predicted_at) FROM {PREDICTIONS_TABLE}").fetchone()[0]
        status["has_predictions"] = latest is not None
        status["latest_predicted_at"] = str(latest) if latest is not None else None
        if latest is not None:
            rows = con.execute(
                f"SELECT promotion_segment, COUNT(*) FROM {PREDICTIONS_TABLE} "
                f"WHERE predicted_at = ? GROUP BY 1",
                [latest],
            ).fetchall()
            status["segment_counts"] = {segment: int(count) for segment, count in rows}
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        con.close()
    return status


def load_purchase_predictions(segment: str | None = "target", limit: int = 1000,
                              latest_only: bool = True) -> dict:
    """구매 예측 결과를 DataFrame으로 반환합니다.

    segment: "target"(프로모션 대상) / "low" / "high" / None(전체)
    반환: {"dataframe": DataFrame, "predicted_at": str|None, "error": str}
    """

    if segment is not None and segment not in SEGMENTS:
        return {"dataframe": pd.DataFrame(), "predicted_at": None,
                "error": f"segment는 {SEGMENTS} 중 하나여야 합니다: {segment}"}
    limit = max(1, min(int(limit), MAX_ROWS))

    status = get_modeling_status()
    if status["error"] or not status["has_predictions"]:
        return {"dataframe": pd.DataFrame(), "predicted_at": None,
                "error": status["error"] or "예측 결과가 없습니다. python run_modeling.py predict 를 먼저 실행하세요."}

    where, params = [], []
    if latest_only:
        where.append("predicted_at = ?")
        params.append(status["latest_predicted_at"])
    if segment is not None:
        where.append("promotion_segment = ?")
        params.append(segment)
    query = (
        f"SELECT * FROM {PREDICTIONS_TABLE} "
        + (f"WHERE {' AND '.join(where)} " if where else "")
        + f"ORDER BY purchase_probability DESC LIMIT {limit}"
    )

    try:
        con = get_duckdb_connection(read_only=True)
    except Exception as exc:
        return {"dataframe": pd.DataFrame(), "predicted_at": None, "error": f"{type(exc).__name__}: {exc}"}
    try:
        df = con.execute(query, params).df()
    finally:
        con.close()
    return {"dataframe": df, "predicted_at": status["latest_predicted_at"], "error": ""}
