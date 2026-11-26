"""입력 변수·기간 규칙 (구매 예측 모델)

변수: 같은 품목 90일 구매 수(log1p), 마지막 구매 후 일수(log), 90일 구매일 수(log). 입력은 점수 기준일 직전 90일 거래만 사용한다.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import joblib
import numpy as np
import pandas as pd
import scipy

from .data import UNKNOWN_VALUES, HMData, combo_article_count, to_date
from .errors import ComboNotFound, DataIncomplete, InsufficientHistory, InvalidCombo

PACKAGE_VERSION = "0.1.0"
FEATURES = ["type_cnt_90d", "days_since_last", "purchase_days_90d"]
TRANSFORMS = {"type_cnt_90d": "log1p", "days_since_last": "log", "purchase_days_90d": "log"}
HISTORY_DAYS = 90
LABEL_DAYS = 30
N_MONDAYS = 12
_FUNCS = {"log1p": np.log1p, "log": np.log}


@dataclass
class FeatureSet:
    frame: pd.DataFrame
    as_of: date
    product_type: str
    excluded: pd.DataFrame = field(default_factory=lambda: pd.DataFrame({"customer_id": [], "reason": []}))



def _resolve_complete(data: HMData, complete_through) -> tuple[date, str]:
    if complete_through is None:
        return data.data_end, "default: 원본 거래의 마지막 날짜(max t_dat)"
    value = to_date(complete_through)
    if value > data.data_end:
        raise DataIncomplete(f"complete_through {value}가 데이터 마지막 거래일 {data.data_end}보다 늦습니다.")
    return value, "specified"


def _check_window(data: HMData, as_of: date, complete: date, days: int) -> None:
    if as_of - timedelta(days=days) < data.data_start:
        raise InsufficientHistory(
            f"기준일 {as_of}의 이전 {days}일({as_of - timedelta(days=days)}~)이 데이터 시작일 {data.data_start}보다 앞섭니다."
        )
    if as_of - timedelta(days=1) > complete:
        raise DataIncomplete(
            f"기준일 {as_of} 직전까지의 구매 기록이 필요하지만 데이터는 {complete}까지만 확보돼 있습니다."
        )


def _check_combo(data: HMData, product_type: str, colour: str | None) -> None:
    values = [product_type] if colour is None else [product_type, colour]
    if any(v in UNKNOWN_VALUES for v in values):
        raise InvalidCombo(f"Unknown 속성으로는 모델을 만들거나 적용할 수 없습니다: {values}")
    if combo_article_count(data, product_type, colour) == 0:
        raise ComboNotFound(f"articles에 없는 조합입니다: {values}")


def training_mondays(as_of, data_start, complete_through, n: int = N_MONDAYS) -> list[date]:
    as_of, data_start, complete_through = to_date(as_of), to_date(data_start), to_date(complete_through)
    limit = min(as_of, complete_through + timedelta(days=1)) - timedelta(days=LABEL_DAYS)
    last = limit - timedelta(days=limit.weekday())
    earliest = data_start + timedelta(days=HISTORY_DAYS)
    mondays = [last - timedelta(weeks=k) for k in range(n)]
    available = sum(m >= earliest for m in mondays)
    if available < n:
        raise InsufficientHistory(
            f"조건을 만족하는 학습 월요일이 {available}개입니다 (필요 {n}개, 마지막 가능 월요일 {last}, "
            f"이전 {HISTORY_DAYS}일 기록이 필요한 최초 월요일 {earliest} 이후)."
        )
    return sorted(mondays)


def _snapshot(con, as_of: date, product_type: str, label_colour: str | None = None) -> pd.DataFrame:
    start = as_of - timedelta(days=HISTORY_DAYS)
    end = as_of + timedelta(days=LABEL_DAYS) if label_colour is not None else as_of
    params = {"start": start, "end": end, "u": as_of, "pt": product_type}
    sql = """
    WITH tx AS (
        SELECT t.customer_id, t.t_dat::DATE AS d, a.product_type_name AS pt, a.colour_group_name AS cg
        FROM hm.transactions t JOIN hm.articles a ON a.article_id = t.article_id
        WHERE t.t_dat >= $start AND t.t_dat < $end
    ),
    f AS (
        SELECT customer_id,
               COUNT(*) FILTER (WHERE pt = $pt) AS type_cnt_90d,
               ($u - MAX(d)) AS days_since_last,
               COUNT(DISTINCT d) AS purchase_days_90d
        FROM tx WHERE d < $u GROUP BY customer_id
    )
    """
    if label_colour is None:
        sql += "SELECT * FROM f ORDER BY customer_id"
    else:
        params["cg"] = label_colour
        sql += """,
    l AS (SELECT DISTINCT customer_id FROM tx WHERE d >= $u AND pt = $pt AND cg = $cg)
    SELECT f.*, (l.customer_id IS NOT NULL)::INTEGER AS y
    FROM f LEFT JOIN l USING (customer_id) ORDER BY customer_id
    """
    df = con.execute(sql, params).df()
    for c in FEATURES + (["y"] if label_colour is not None else []):
        df[c] = df[c].astype(np.int64)
    df["customer_id"] = df["customer_id"].astype(str)
    return df


def _transform(frame: pd.DataFrame) -> np.ndarray:
    return np.column_stack([_FUNCS[TRANSFORMS[c]](frame[c].to_numpy(np.float64)) for c in FEATURES])


def build_features(data: HMData, as_of, product_type: str, customer_ids=None, complete_through=None) -> FeatureSet:
    as_of = to_date(as_of)
    complete, _ = _resolve_complete(data, complete_through)
    _check_window(data, as_of, complete, HISTORY_DAYS)
    _check_combo(data, product_type, None)
    frame = _snapshot(data.con, as_of, product_type)
    excluded = pd.DataFrame({"customer_id": pd.Series([], dtype=str), "reason": pd.Series([], dtype=str)})
    if customer_ids is not None:
        wanted = pd.Index(pd.unique(pd.Series(list(customer_ids), dtype=str)))
        have = wanted.isin(frame["customer_id"])
        excluded = pd.DataFrame({"customer_id": wanted[~have], "reason": f"최근 {HISTORY_DAYS}일 구매 이력 없음"})
        frame = frame[frame["customer_id"].isin(wanted)].reset_index(drop=True)
    return FeatureSet(frame=frame, as_of=as_of, product_type=product_type, excluded=excluded)


def _validate_frame(frame: pd.DataFrame) -> None:
    missing = [c for c in ["customer_id", *FEATURES] if c not in frame.columns]
    if missing:
        raise ValueError(f"입력에 컬럼이 없습니다: {missing}")
    if frame["customer_id"].duplicated().any():
        raise ValueError("customer_id가 중복됩니다.")
    values = frame[FEATURES]
    if values.isna().any().any():
        raise ValueError("입력 변수에 결측이 있습니다.")
    if (values["type_cnt_90d"] < 0).any() or (values["days_since_last"] < 1).any() or (values["purchase_days_90d"] < 1).any():
        raise ValueError("입력 변수 범위 오류: type_cnt_90d >= 0, days_since_last >= 1, purchase_days_90d >= 1")



def _versions() -> dict:
    import sys
    return {"python": sys.version.split()[0], "platform": platform.platform(), "numpy": np.__version__,
            "pandas": pd.__version__, "scipy": scipy.__version__, "duckdb": duckdb.__version__,
            "joblib": joblib.__version__, "modeling.purchase_model": PACKAGE_VERSION}
