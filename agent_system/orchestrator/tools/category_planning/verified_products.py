"""8단계 상품 후보: H&M 실거래/상품 데이터로 재현 가능한 후보 추출."""
from __future__ import annotations

import pandas as pd
from shared_analysis.product.category_mapping import category_masks

CATEGORY_SET = {"긴소매 티셔츠", "니트·스웨터", "반소매 티셔츠", "셔츠·블라우스", "아우터"}


def purchase_candidates(articles: pd.DataFrame, purchases: pd.DataFrame, category: str, subtype=None, colors=None) -> pd.DataFrame:
    """여성용 상품 + 범주 + 실제 겨울 온라인 구매. 색상·유형에 일치하는 전체 행."""
    if category not in CATEGORY_SET:
        raise ValueError(f"지원하지 않는 카테고리: {category}")
    required = {"article_id", "product_code", "index_group_name", "product_type_name", "garment_group_name", "prod_name", "detail_desc"}
    if required - set(articles.columns):
        raise ValueError(f"상품 컬럼 누락: {sorted(required - set(articles.columns))}")
    products = articles.loc[category_masks(articles)[category] & articles["index_group_name"].isin(["Ladieswear", "Divided"])].copy()
    if subtype:
        from agent_system.orchestrator.tools.category_planning.subtype_selection import subtype_mask
        products = products.loc[subtype_mask(products, subtype)].copy()
    if colors:
        products = products.loc[products['colour_group_name'].isin(colors)].copy()
    if 'colour_group_name' not in products:
        products['colour_group_name'] = ''
    products["article_id"] = products["article_id"].astype(str).str.zfill(10)
    products["product_code"] = products["product_code"].astype(str)
    transactions = purchases.copy()
    transactions["article_id"] = transactions["article_id"].astype(str).str.zfill(10)
    valid = transactions[transactions["winter_online_purchases"] > 0]
    merged = products.merge(valid[["article_id", "winter_online_purchases", "winter_online_customers"]], on="article_id", how="inner")
    merged = merged.sort_values(["winter_online_purchases", "article_id"], ascending=[False, True])
    return merged


def choose_products(articles, purchases, category, limit=5, *, subtype=None, colors=None):
    merged = purchase_candidates(articles, purchases, category, subtype, colors)
    from .product_suitability import classify_candidates
    merged = classify_candidates(merged, subtype)
    # 유형 충돌 상품은 자동 승인 후보에서 제외하고 별도 검토 대상으로 보존한다.
    if subtype:
        merged = merged.loc[merged["suitability_status"].eq("rule_matched")].copy()
    # 색상을 명시하면 동일 모델의 다른 색상도 후보로 유지.
    merged = merged.drop_duplicates(["product_code", "colour_group_name"] if colors else "product_code")
    if colors:
        if len(colors) > limit:
            raise ValueError("선택 색상 수가 상품 후보 한도를 초과합니다.")
        first = merged.groupby("colour_group_name", sort=False).head(1)
        merged = pd.concat([first, merged.loc[~merged.index.isin(first.index)]]).head(limit).sort_values(["winter_online_purchases", "article_id"], ascending=[False, True])
    else:
        merged = merged.head(limit)
    columns = ["article_id", "product_code", "prod_name", "product_type_name", "garment_group_name", "colour_group_name", "detail_desc", "winter_online_purchases", "winter_online_customers", "suitability_status", "suitability_reason", "suitability_evidence"]
    return merged[columns].reset_index(drop=True)


def load_purchase_inputs(*, period_plan=None, scope="core"):
    """H&M 20~39세·온라인 구매 조회. 기간 계획 미지정 시 기존 겨울 조건."""
    from agent_system.common_tools.local_data import hm_products
    from common.db_client import get_analysis_connection
    articles = hm_products.articles()
    from agent_system.common_tools.local_data.campaign_period import purchase_date_filter
    date_sql, date_params = purchase_date_filter(hm_products.data_as_of(), period_plan=period_plan, scope=scope)
    connection = get_analysis_connection()
    try:
        purchases = connection.execute("""
            SELECT CAST(t.article_id AS VARCHAR) AS article_id,
                   COUNT(*) AS winter_online_purchases,
                   COUNT(DISTINCT t.customer_id) AS winter_online_customers
            FROM hm.transactions AS t
            JOIN hm.customers AS c ON c.customer_id = t.customer_id
            WHERE t.sales_channel_id = 2
              AND c.age BETWEEN 20 AND 39
        """ + date_sql + " GROUP BY 1", date_params).df()
    finally:
        connection.close()
    return articles, purchases


def build_verified_step8(category: str, *, subtype=None, colors=None, period_plan=None) -> dict:
    from .period_context import purchase_label, scope_text, period_note
    purchase_basis = purchase_label(period_plan)
    articles, purchases = load_purchase_inputs(period_plan=period_plan) if period_plan is not None else load_purchase_inputs()
    rows = choose_products(articles, purchases, category, subtype=subtype, colors=colors)
    review_rows = []
    if subtype:
        from .product_suitability import classify_candidates
        raw = purchase_candidates(articles, purchases, category, subtype=subtype, colors=colors)
        classified = classify_candidates(raw, subtype)
        review = classified.loc[classified["suitability_status"].eq("review_required")].copy()
        review = review.drop_duplicates(["product_code", "colour_group_name"] if colors else "product_code")
        review_rows = review[["article_id", "prod_name", "colour_group_name", "winter_online_purchases",
                              "suitability_status", "suitability_reason"]].head(10).to_dict(orient="records")
    missing_colors = sorted(set(colors or []) - set(rows['colour_group_name']))
    if rows.empty or missing_colors:
        detail = (f"선택 색상 중 자동 승인 가능한 유형 일치 상품이 없는 색상: {', '.join(missing_colors)}. "
                  if missing_colors else "자동 승인 가능한 유형 일치 후보가 없습니다. ")
        return {"final_answer": detail + "상품명·설명 규칙만으로 유형을 확정할 수 없어 검토 또는 색상 재선택이 필요합니다. 현재 후보를 승인하지 않습니다.",
                "review_candidate_rows": review_rows,
                "product_result": {"search_products": {"rows": []}}, "data_sources": ["hm"]}
    lines = [f"■ {category} 과거 H&M 판매 참고 사례 ({len(rows)}개)",
             ("기준: H&M 과거 데이터 / 20~39세 고객 / 온라인 채널 2 / 여성용(Ladieswear·Divided) 상품" + "\n" + period_note(period_plan)
              if period_plan else "기준: H&M 과거 데이터 / 20~39세 고객 / 온라인 채널 2 / 12~2월 / 여성용(Ladieswear·Divided) 상품"),
             "과거 구매 기록순 참고 사례입니다. 현재 기획전에 판매할 상품을 확정하거나 추천하는 목록이 아닙니다.",
             "고객 성별은 확인할 수 없으며, 고객 나이도 거래 시점의 나이로 보증되지 않습니다.",
             f"세부 유형: {subtype or '미지정'} / 색상: {', '.join(colors or []) or '미지정'} / 유형은 상품명·설명 규칙 매칭이며 실측 성능 미검증.",
             "선택한 색상별로 과거 참고 사례를 최소 한 개 포함해 디자인 특징을 비교합니다." if colors else "동일 모델은 구매 기록 상위 색상 1개를 선택합니다.",
             "유형 충돌 상품은 구매 실적이 높아도 자동 승인 후보에서 제외하며 별도 검토합니다."]
    for index, row in rows.iterrows():
        lines.append(f"{index+1}. {row['prod_name']} / article_id={row['article_id']} / 유형={row['product_type_name']} / 색상={row['colour_group_name']} / {purchase_basis} 기록={int(row['winter_online_purchases']):,}건 / 구매 고객={int(row['winter_online_customers']):,}명")
    if review_rows:
        lines.append(f"자동 승인에서 제외한 유형 확인 필요 상품: {len(review_rows)}개(최대 10개 표시)")
        for item in review_rows:
            lines.append(f"- {item['prod_name']} / {item['article_id']} / {item['suitability_reason']}")
    if period_plan is not None:
        rows["analysis_period"] = scope_text(period_plan)
    rows["keyword_match_verified"] = bool(subtype) & rows["suitability_status"].eq("rule_matched")
    rows["selected_subtype"] = subtype or ""
    # 게이트는 Tool 결과와 같은 구조의 명시적 DB 조회 행에서만 후보를 인정한다.
    return {"final_answer": "\n".join(lines), "dataframe": rows,
            "review_candidate_rows": review_rows,
            "product_result": {"search_products": {"rows": rows.to_dict(orient="records")}},
            "data_sources": ["hm"], "data_purpose": f"{category}: H&M {purchase_basis} 실적 기반 후보"}
