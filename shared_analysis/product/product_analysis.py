"""상품별 판매·속성 분석, 후보 비교 (아키텍처 shared_analysis/product/product_analysis.py).

순수 Python: DB·API·LLM에 의존하지 않습니다.
DB 조회(H&M 구매 기록 집계)는 호출하는 쪽(backend product_service 또는 agent 로컬 모드)이 하고,
여기서는 같은 기간·조건으로 집계된 표를 받아 비교 지표만 계산합니다.

판매 지표 정의 (H&M 데이터 특성)
    purchases    구매 기록 행 수. 같은 날·고객·상품 반복 행은 수량으로 보고 그대로 셉니다 (daily_purchases qty 합과 같음)
    customers    구매 고객 수 (중복 제거)
    share        비교한 상품들 구매 합계 중 비중
    growth_rate  직전 같은 길이 기간 대비 증가율 (직전 0건이면 None)
    price_index  H&M price 평균 (정규화된 비교 지표, 금액 아님)
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

DEFAULT_WEEKS = 4
SMALL_SAMPLE = 30           # 구매 기록이 이보다 적으면 표본이 작다고 표시
INFO_COLUMNS = ["prod_name", "product_type_name", "colour_group_name", "perceived_colour_master_name",
                "index_group_name"]


def _round(value, digits=4):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return round(float(value), digits)


def make_period(as_of: date, weeks: int = DEFAULT_WEEKS, end_date: date | None = None) -> dict:
    """비교 기간: end(포함)에서 weeks주 거꾸로. 직전 기간은 바로 앞 같은 길이.

    as_of   데이터 마지막 날 (H&M = 2020-09-22, 아키텍처 ANALYSIS_AS_OF). 오늘 날짜를 쓰지 않습니다.
    end_date를 주면 그 날까지 (as_of보다 늦으면 as_of로 줄임).
    """
    weeks = max(1, int(weeks or DEFAULT_WEEKS))
    end = min(end_date, as_of) if end_date else as_of
    start = end - timedelta(days=7 * weeks - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=7 * weeks - 1)
    note = ""
    if end_date and end_date > as_of:
        note = f"요청 종료일 {end_date}이 데이터 마지막 날 {as_of} 이후라 {as_of}까지로 계산"
    return {"start": start.isoformat(), "end": end.isoformat(), "weeks": weeks,
            "previous_start": prev_start.isoformat(), "previous_end": prev_end.isoformat(),
            "data_as_of": as_of.isoformat(), "note": note}


def summarize_sales(totals: pd.DataFrame, weekly: pd.DataFrame, info: pd.DataFrame, key: str = "article_id") -> dict:
    """같은 기간·조건의 상품별 구매 실적 비교.

    totals  key, period('current'|'previous'), purchases, customers, price_index
    weekly  key, week_start, purchases          (current 기간만)
    info    key + INFO_COLUMNS                  (상품 이름·색상 표시용)
    반환    {"rows": [...], "total_purchases", "small_sample": [key…]}
    """
    current = totals[totals["period"] == "current"].set_index(key) if not totals.empty else pd.DataFrame()
    previous = totals[totals["period"] == "previous"].set_index(key) if not totals.empty else pd.DataFrame()
    keys = list(dict.fromkeys(info[key].astype(str))) if not info.empty else []
    total = float(current["purchases"].sum()) if not current.empty else 0.0
    info_by_key = info.drop_duplicates(key).set_index(key) if not info.empty else pd.DataFrame()

    rows = []
    for k in keys:
        cur = current.loc[k] if k in current.index else None
        prev = previous.loc[k] if k in previous.index else None
        purchases = int(cur["purchases"]) if cur is not None else 0
        prev_purchases = int(prev["purchases"]) if prev is not None else 0
        customers = int(cur["customers"]) if cur is not None else 0
        week_rows = weekly[weekly[key] == k].sort_values("week_start") if not weekly.empty else weekly
        row = {key: k}
        for column in INFO_COLUMNS:
            if column in info_by_key.columns and k in info_by_key.index:
                row[column] = info_by_key.loc[k, column]
        row.update({
            "purchases": purchases,
            "share": _round(purchases / total) if total else None,
            "customers": customers,
            "purchases_per_customer": _round(purchases / customers, 3) if customers else None,
            "previous_purchases": prev_purchases,
            "growth_rate": _round((purchases - prev_purchases) / prev_purchases) if prev_purchases else None,
            "price_index": _round(cur["price_index"], 6) if cur is not None else None,
            "weekly": [{"week_start": str(w["week_start"])[:10], "purchases": int(w["purchases"])}
                       for _, w in week_rows.iterrows()],
        })
        rows.append(row)
    rows.sort(key=lambda r: (-r["purchases"], str(r[key])))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {"rows": rows, "total_purchases": int(total),
            "small_sample": [r[key] for r in rows if r["purchases"] < SMALL_SAMPLE]}


def compare_candidates(details: list[dict], keyword_rows: list[dict], sales_rows: list[dict],
                       keywords: list[str]) -> dict:
    """후보 상품의 키워드 일치도와 판매 근거 비교.

    details       get_product_details 결과 rows (article_id, prod_name, style_tags …)
    keyword_rows  후보별·키워드별 match_rate (keyword_matching.match_keyword 결과를 후보로 좁힌 것)
    sales_rows    summarize_sales 결과 rows
    정렬 기준     ① 완전 일치한 키워드 수 ② 평균 키워드 일치율 ③ 구매 건수 (모두 큰 순)
    추천을 단정하지 않고, 기준별 1위를 함께 돌려줍니다.
    """
    sales_by_id = {r["article_id"]: r for r in sales_rows}
    rows = []
    for detail in details:
        article_id = detail["article_id"]
        matches = [r for r in keyword_rows if r["article_id"] == article_id]
        full = [r["keyword"] for r in matches if r["match_rate"] >= 1.0]
        mean_rate = sum(r["match_rate"] for r in matches) / len(keywords) if keywords else None
        sale = sales_by_id.get(article_id, {})
        rows.append({
            "article_id": article_id,
            "prod_name": detail.get("prod_name"),
            "colour_group_name": detail.get("colour_group_name"),
            "style_tags": detail.get("style_tags", []),
            "full_match_keywords": full,
            "full_match_count": len(full),
            "keyword_match_rate": _round(mean_rate) if mean_rate is not None else None,
            "purchases": sale.get("purchases"),
            "share": sale.get("share"),
            "growth_rate": sale.get("growth_rate"),
            "customers": sale.get("customers"),
        })
    rows.sort(key=lambda r: (-r["full_match_count"], -(r["keyword_match_rate"] or 0), -(r["purchases"] or 0),
                             r["article_id"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    def top(field):
        valid = [r for r in rows if r.get(field) is not None]
        return max(valid, key=lambda r: r[field])["article_id"] if valid else None

    return {
        "rows": rows,
        "sort_rule": "① 완전 일치 키워드 수 ② 평균 키워드 일치율 ③ 구매 건수 (큰 순)",
        "best_by": {"keyword_match": top("keyword_match_rate"), "purchases": top("purchases"),
                    "growth_rate": top("growth_rate")},
    }
