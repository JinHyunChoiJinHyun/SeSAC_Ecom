from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from .data import ATTRIBUTE_COLUMNS, UNKNOWN_VALUES, HMData, article_attributes, to_date
from .errors import DataIncomplete, InsufficientHistory


def target_from_article(data: HMData, article_id: str) -> dict:
    row = article_attributes(data, [article_id]).iloc[0]
    return {key: row[col] for key, col in ATTRIBUTE_COLUMNS.items()}


def taste_scores(data: HMData, as_of, window_days: int, target: dict, customer_ids=None,
                 shrink_k: float | None = None, weights: dict | None = None, complete_through=None) -> pd.DataFrame:
    as_of = to_date(as_of)
    if window_days is None or int(window_days) != window_days or window_days <= 0:
        raise ValueError("window_days는 양의 정수로 명시해야 합니다.")
    unknown_keys = set(target) - set(ATTRIBUTE_COLUMNS)
    if unknown_keys:
        raise ValueError(f"지원하지 않는 속성: {sorted(unknown_keys)} (사용 가능: {list(ATTRIBUTE_COLUMNS)})")
    keys = [k for k in ATTRIBUTE_COLUMNS if target.get(k) is not None]
    if not keys:
        raise ValueError("target에 품목·색상·패턴 중 하나 이상을 지정해야 합니다.")
    if shrink_k is not None and shrink_k < 0:
        raise ValueError("shrink_k는 0 이상이어야 합니다.")
    if weights is not None:
        extra = set(weights) - set(keys)
        if extra:
            raise ValueError(f"target에 없는 속성의 가중치: {sorted(extra)}")
        if any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("가중치는 0 이상이고 합이 0보다 커야 합니다.")

    complete = data.data_end if complete_through is None else to_date(complete_through)
    if complete > data.data_end:
        raise DataIncomplete(f"complete_through {complete}가 데이터 마지막 거래일 {data.data_end}보다 늦습니다.")
    start = as_of - timedelta(days=int(window_days))
    if start < data.data_start:
        raise InsufficientHistory(f"조회 시작일 {start}가 데이터 시작일 {data.data_start}보다 앞섭니다.")
    if as_of - timedelta(days=1) > complete:
        raise DataIncomplete(f"기준일 {as_of} 직전까지의 구매 기록이 필요하지만 데이터는 {complete}까지만 확보돼 있습니다.")

    unknown_list = ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(UNKNOWN_VALUES))
    params = {"start": start, "end": as_of}
    select = ["COUNT(*) AS n_tx_window"]
    for k in keys:
        col = ATTRIBUTE_COLUMNS[k]
        params[f"v_{k}"] = target[k]
        select.append(f"COUNT(*) FILTER (WHERE a.{col} IS NOT NULL AND a.{col} NOT IN ({unknown_list})) AS {k}_valid")
        select.append(f"COUNT(*) FILTER (WHERE a.{col} = $v_{k}) AS {k}_match")
    df = data.con.execute(
        f"""SELECT t.customer_id, {', '.join(select)}
        FROM hm.transactions t JOIN hm.articles a ON a.article_id = t.article_id
        WHERE t.t_dat >= $start AND t.t_dat < $end
        GROUP BY t.customer_id ORDER BY t.customer_id""",
        params,
    ).df()
    df["customer_id"] = df["customer_id"].astype(str)
    if customer_ids is not None:
        wanted = pd.Index(pd.unique(pd.Series(list(customer_ids), dtype=str)), name="customer_id")
        df = df.set_index("customer_id").reindex(wanted).fillna(0).reset_index()
    count_cols = ["n_tx_window"] + [f"{k}_{s}" for k in keys for s in ("valid", "match")]
    df[count_cols] = df[count_cols].astype(np.int64)

    out = df[["customer_id", "n_tx_window"]].copy()
    for k in keys:
        valid = df[f"{k}_valid"].astype(float)
        match = df[f"{k}_match"].astype(float)
        if target[k] in UNKNOWN_VALUES:
            match[:] = np.nan
        ratio = np.where(valid > 0, match / valid.where(valid > 0), np.nan)
        out[f"{k}_match"] = (pd.Series(pd.NA, index=out.index, dtype="Int64") if target[k] in UNKNOWN_VALUES
                             else df[f"{k}_match"].astype("Int64"))
        out[f"{k}_valid"] = df[f"{k}_valid"]
        out[f"{k}_ratio"] = ratio
        if shrink_k is not None:
            out[f"{k}_ratio_shrunk"] = ratio * valid / (valid + shrink_k) if shrink_k > 0 else ratio
    if weights is not None:
        col = "ratio_shrunk" if shrink_k is not None else "ratio"
        total = sum(weights.values())
        parts = pd.concat([out[f"{k}_{col}"] * (w / total) for k, w in weights.items()], axis=1)
        out["taste_weighted"] = parts.sum(axis=1, skipna=False)
    return out.reset_index(drop=True)
