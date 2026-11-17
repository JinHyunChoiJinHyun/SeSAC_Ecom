"""테스트용 가짜 Synerise 원본 데이터(Parquet) 생성. 실제 데이터·DB는 사용하지 않습니다."""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


def _to_parquet(df: pd.DataFrame, path: Path):
    """pyarrow 없이 DuckDB로 Parquet 저장"""
    con = duckdb.connect()
    try:
        con.register("df", df.reset_index(drop=True))
        con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()


def write_synthetic_parquet(out_dir: Path, seed: int = 0, days: int = 30,
                            n_clients: int = 400, n_skus: int = 60, n_carts: int = 3000) -> Path:
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp("2026-01-01")
    end = start + pd.Timedelta(days=days)

    def ts(values):
        return pd.Series(values).dt.strftime("%Y-%m-%d %H:%M:%S")  # 원본처럼 문자열

    cart_time = start + pd.to_timedelta(rng.uniform(0, days * 86400, n_carts), unit="s")
    cart = pd.DataFrame({
        "client_id": rng.integers(1, n_clients + 1, n_carts).astype("int64"),
        "sku": rng.integers(1, n_skus + 1, n_carts).astype("int64"),
        "timestamp": cart_time,
    })

    # 담은 뒤 60분 안 행동 → 구매 확률에 영향
    visits_after = rng.poisson(1.5, n_carts)
    visit_rows = []
    for (client, t, k) in zip(cart["client_id"], cart["timestamp"], visits_after):
        for _ in range(k):
            visit_rows.append((client, t + pd.Timedelta(minutes=float(rng.uniform(1, 59)))))
    noise_n = 4000
    visit_rows += list(zip(rng.integers(1, n_clients + 1, noise_n),
                           start + pd.to_timedelta(rng.uniform(0, days * 86400, noise_n), unit="s")))
    page_visit = pd.DataFrame(visit_rows, columns=["client_id", "timestamp"])
    page_visit["url"] = rng.integers(1, 500, len(page_visit))

    prob = 1 / (1 + np.exp(-(-2.2 + 0.6 * visits_after)))
    bought = rng.uniform(size=n_carts) < prob
    buy_time = cart["timestamp"] + pd.to_timedelta(rng.uniform(61, 47 * 60, n_carts), unit="m")
    buy = cart.loc[bought, ["client_id", "sku"]].assign(timestamp=buy_time[bought])
    buy = buy[buy["timestamp"] < end]

    remove = cart.sample(frac=0.15, random_state=seed)[["client_id", "sku", "timestamp"]].copy()
    remove["timestamp"] = remove["timestamp"] + pd.Timedelta(minutes=20)

    search_n = 3000
    search = pd.DataFrame({
        "client_id": rng.integers(1, n_clients + 1, search_n).astype("int64"),
        "timestamp": start + pd.to_timedelta(rng.uniform(0, days * 86400, search_n), unit="s"),
        "query": "q",
    })

    # 모든 테이블이 끝 시점 근처까지 있도록 (data_end = 각 테이블 MAX의 최솟값)
    tail = end - pd.Timedelta(minutes=1)
    last = {"client_id": 1, "sku": 1, "timestamp": tail}
    tables = {
        "add_to_cart": pd.concat([cart, pd.DataFrame([last])]),
        "product_buy": pd.concat([buy, pd.DataFrame([last])]),
        "remove_from_cart": pd.concat([remove, pd.DataFrame([last])]),
        "page_visit": pd.concat([page_visit, pd.DataFrame([{"client_id": 1, "timestamp": tail, "url": 1}])]),
        "search_query": pd.concat([search, pd.DataFrame([{"client_id": 1, "timestamp": tail, "query": "q"}])]),
    }
    for name, df in tables.items():
        df = df.copy()
        df["timestamp"] = ts(df["timestamp"].reset_index(drop=True)).values
        _to_parquet(df, out_dir / f"{name}.parquet")

    _to_parquet(pd.DataFrame({
        "sku": np.arange(1, n_skus + 1, dtype="int64"),
        "category": rng.integers(1, 8, n_skus).astype("int64"),
        "price": rng.integers(1, 100, n_skus).astype("int64"),
    }), out_dir / "product_properties.parquet")
    return out_dir
