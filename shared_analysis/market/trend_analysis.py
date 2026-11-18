"""네이버 쇼핑인사이트 추이 계산 — get_shopping_trends · analyze_trend_sustainability.

입력 series: DataFrame
    keyword, period(구간 시작일), ratio(상대 클릭량)          필수
    request_id, collected_at                                 선택 (있으면 키워드마다 가장 최근 요청만 사용)
주의: 네이버 ratio는 '한 번의 요청 안' 상대값(최대 100)입니다.
    → 서로 다른 요청의 값끼리 크기를 비교하면 안 됩니다. 증가율(같은 시계열 안의 비율)은 비교할 수 있습니다.
"""

from __future__ import annotations

import pandas as pd

from shared_analysis.market.common import clean_words, records, rnd, to_date

RECENT_PERIODS = 4          # 최근 N구간 vs 직전 N구간 (week면 4주 vs 4주) (가설)
GROWTH_UP = 0.10            # 증가율 +10% 이상 → 상승 (가설)
GROWTH_DOWN = -0.10         # 증가율 -10% 이하 → 하락 (가설)
SERIES_TAIL = 12            # 요약에 보여 줄 키워드별 최근 구간 수


def prepare_series(series: pd.DataFrame, keywords: list[str]) -> tuple[pd.DataFrame, dict]:
    """요청한 키워드만, 키워드마다 한 요청의 시계열로 정리. 반환: (series, 키워드별 요청 정보)"""
    if series is None or series.empty:
        empty = pd.DataFrame({
            "keyword": pd.Series(dtype="str"),
            "period": pd.Series(dtype="datetime64[ns]"),
            "ratio": pd.Series(dtype="float64"),
        })
        return empty, {}
    df = series[series["keyword"].isin(keywords)].copy()
    df["period"] = pd.to_datetime(df["period"])
    df["ratio"] = pd.to_numeric(df["ratio"], errors="coerce")
    df = df.dropna(subset=["period", "ratio"])
    df = df[df["ratio"].between(0, 100)]
    info = {}
    if "request_id" in df:
        # 키워드마다 요청 1개의 시계열만 사용 (CodeRabbit 리뷰)
        #   요청이 1개면 그대로 / 여러 개면 수집일이 있는 행 중 최신 요청 (ID·수집일은 같은 행에서)
        #   여러 요청인데 수집일이 하나도 없으면 최신 요청을 정할 수 없으므로 그 키워드는 비교 불가로 뺌
        has_time = "collected_at" in df
        if has_time:
            df["collected_at"] = pd.to_datetime(df["collected_at"], errors="coerce")
        df["request_id"] = df["request_id"].astype(str)
        parts = []
        for keyword, group in df.groupby("keyword", sort=False):
            requests = group["request_id"].unique()
            if len(requests) == 1:
                chosen = requests[0]
                times = group["collected_at"].dropna() if has_time else pd.Series(dtype="datetime64[ns]")
                collected = times.max() if len(times) else None
            else:
                timed = group.dropna(subset=["collected_at"]) if has_time else group.iloc[0:0]
                if timed.empty:
                    info[keyword] = {"request_id": None, "collected_at": None, "comparable": False,
                                     "note": f"요청 {len(requests)}개가 섞여 있고 수집일이 없어 최신 요청을 정할 수 없음"}
                    continue
                last = timed.sort_values(["collected_at", "request_id"]).iloc[-1]
                chosen, collected = last["request_id"], last["collected_at"]
            parts.append(group[group["request_id"] == chosen])
            info[keyword] = {"request_id": str(chosen),
                             "collected_at": str(collected) if collected is not None and pd.notna(collected) else ""}
        df = pd.concat(parts) if parts else df.iloc[0:0]
    elif "collected_at" in df:
        df["collected_at"] = pd.to_datetime(df["collected_at"])
        # 구간별 최신값을 이어 붙이지 않고 키워드별 최신 수집 배치만 사용
        latest_batch = df.groupby("keyword")["collected_at"].transform("max")
        df = df[df["collected_at"].eq(latest_batch)]
    df = df.drop_duplicates(["keyword", "period"], keep="last").sort_values(["keyword", "period"])
    return df[["keyword", "period", "ratio"]], info


def summarize_trends(series: pd.DataFrame, keywords, start_date=None, end_date=None) -> dict:
    """get_shopping_trends: 키워드별 추이 요약 + 시계열."""
    keywords = clean_words(keywords)
    df, info = prepare_series(series, keywords)
    start, end = to_date(start_date), to_date(end_date)
    if start:
        df = df[df["period"].dt.date >= start]
    if end:
        df = df[df["period"].dt.date <= end]
    rows = []
    for keyword in keywords:
        group = df[df["keyword"] == keyword]
        if group.empty:
            continue
        rows.append({
            "keyword": keyword, "periods": len(group),
            "first_period": str(group["period"].min().date()), "latest_period": str(group["period"].max().date()),
            "latest_ratio": rnd(group["ratio"].iloc[-1], 2), "max_ratio": rnd(group["ratio"].max(), 2),
            "recent_values": [rnd(v, 2) for v in group["ratio"].tail(SERIES_TAIL)],
            "request": info.get(keyword, {}),
        })
    notes = ["ratio는 같은 요청 안의 상대값(최대 100)입니다. 요청이 다른 키워드끼리 크기를 비교하지 마십시오."]
    notes += [f"{k}: 비교 불가 — {v['note']}" for k, v in info.items() if v.get("comparable") is False]
    return {
        "keywords": keywords, "rows": rows, "series": records(df),
        "missing_keywords": [k for k in keywords if k not in {r["keyword"] for r in rows}],
        "notes": notes,
    }


def _window_mean(group: pd.DataFrame, start, end):
    part = group[(group["period"] >= start) & (group["period"] <= end)]
    return (float(part["ratio"].mean()), len(part)) if len(part) else (None, 0)


def judge(growth, last_year_change) -> str:
    if growth is None:
        return "근거 부족 (직전 구간 값 0)"
    if growth >= GROWTH_UP:
        if last_year_change is None:
            return "상승 (지속 근거 미확인)"
        return "상승 · 지속 근거 있음" if last_year_change >= 0 else "상승 · 작년엔 기획전 시기에 하락"
    if growth <= GROWTH_DOWN:
        return "하락"
    return "정체"


def analyze_sustainability(series: pd.DataFrame, keywords, base_keyword=None,
                           campaign_start=None, campaign_end=None) -> dict:
    """analyze_trend_sustainability

    증가율        = 최근 N구간 평균 ÷ 직전 N구간 평균 - 1
    기준어 대비   = 키워드 증가율 - 기준어(예: "니트") 증가율 (%p)
    작년 같은 시기 = (작년 기획전 기간 평균) ÷ (작년 '지금' 구간 평균) - 1  → 계절 패턴 근거
    """
    keywords = clean_words(keywords)
    base = str(base_keyword or "").strip()
    df, request_info = prepare_series(series, keywords + ([base] if base and base not in keywords else []))
    k = RECENT_PERIODS
    c_start, c_end = to_date(campaign_start), to_date(campaign_end)
    year = pd.DateOffset(years=1)

    metrics = {}
    for keyword, group in df.groupby("keyword", sort=False):
        group = group.sort_values("period")
        info = {"keyword": keyword, "periods": len(group), "latest_period": str(group["period"].max().date())}
        if len(group) < 2 * k:
            info.update(recent_growth=None, judgement="근거 부족 (구간 수 부족)")
            metrics[keyword] = info
            continue
        recent, prior = group.tail(k), group.iloc[-2 * k:-k]
        prior_mean = float(prior["ratio"].mean())
        growth = float(recent["ratio"].mean()) / prior_mean - 1 if prior_mean > 0 else None
        info.update(recent_mean=rnd(recent["ratio"].mean(), 2), prior_mean=rnd(prior_mean, 2), recent_growth=rnd(growth),
                    recent_window=f"{recent['period'].min().date()} ~ {recent['period'].max().date()}",
                    prior_window=f"{prior['period'].min().date()} ~ {prior['period'].max().date()}")
        ly_change, ly_note = None, "기획전 기간 미입력"
        if c_start and c_end:
            ly_now, n_now = _window_mean(group, recent["period"].min() - year, recent["period"].max() - year)
            ly_campaign, n_campaign = _window_mean(group, pd.Timestamp(c_start) - year, pd.Timestamp(c_end) - year)
            if ly_now and ly_campaign is not None and n_now and n_campaign:
                ly_change = ly_campaign / ly_now - 1
                ly_note = (f"작년 {(recent['period'].min() - year).date()}~{(recent['period'].max() - year).date()} 대비 "
                           f"작년 {(pd.Timestamp(c_start) - year).date()}~{(pd.Timestamp(c_end) - year).date()}")
            else:
                ly_note = "작년 같은 시기 데이터 없음"
        info.update(last_year_campaign_change=rnd(ly_change), last_year_basis=ly_note, judgement=judge(growth, ly_change))
        metrics[keyword] = info

    base_growth = (metrics.get(base) or {}).get("recent_growth") if base else None
    rows = []
    for keyword in keywords:
        if keyword not in metrics:
            if (request_info.get(keyword) or {}).get("comparable") is False:
                rows.append({"keyword": keyword, "judgement": "비교 불가 (" + request_info[keyword]["note"] + ")"})
            else:
                rows.append({"keyword": keyword, "judgement": "데이터 없음"})
            continue
        row = dict(metrics[keyword])
        growth = row.get("recent_growth")
        row["growth_vs_base_pp"] = rnd(growth - base_growth) if growth is not None and base_growth is not None else None
        rows.append(row)
    return {
        "keywords": keywords, "base_keyword": base or None, "base_recent_growth": base_growth,
        "basis": f"최근 {k}구간 평균 ÷ 직전 {k}구간 평균 - 1, 상승 ≥ {GROWTH_UP:+.0%}, 하락 ≤ {GROWTH_DOWN:+.0%}",
        "campaign": {"start": str(c_start) if c_start else None, "end": str(c_end) if c_end else None},
        "rows": rows, "missing_keywords": [r["keyword"] for r in rows if r["judgement"] == "데이터 없음"],
        "notes": ["growth_vs_base_pp = 키워드 증가율 - 기준어 증가율 (0.05 = 5%p 더 빠르게 증가)",
                  "작년 같은 시기 흐름은 계절 패턴 근거이며 올해를 보장하지 않습니다.",
                  "판정 기준값은 가설이며 팀 합의가 필요합니다."],
    }
