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
    # 색상을 명시하면 동일 모델의 다른 색상도 후보로 유지.
    merged = merged.drop_duplicates(["product_code", "colour_group_name"] if colors else "product_code")
    if colors:
        if len(colors) > limit:
            raise ValueError("선택 색상 수가 상품 후보 한도를 초과합니다.")
        first = merged.groupby("colour_group_name", sort=False).head(1)
        merged = pd.concat([first, merged.loc[~merged.index.isin(first.index)]]).head(limit).sort_values(["winter_online_purchases", "article_id"], ascending=[False, True])
    else:
        merged = merged.head(limit)
    columns = ["article_id", "product_code", "prod_name", "product_type_name", "garment_group_name", "colour_group_name", "detail_desc", "winter_online_purchases", "winter_online_customers"]
    return merged[columns].reset_index(drop=True)


def load_purchase_inputs():
    """H&M 2018~2020 데이터의 20~39세·온라인·겨울(12~2월) 조건으로 검색."""
    from agent_system.common_tools.local_data import hm_products
    from common.db_client import get_analysis_connection
    articles = hm_products.articles()
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
              AND EXTRACT(MONTH FROM t.t_dat) IN (12, 1, 2)
            GROUP BY 1
        """).df()
    finally:
        connection.close()
    return articles, purchases


def build_verified_step8(category: str, *, subtype=None, colors=None) -> dict:
    articles, purchases = load_purchase_inputs()
    rows = choose_products(articles, purchases, category, subtype=subtype, colors=colors)
    if colors and not set(colors).issubset(set(rows['colour_group_name'])):
        rows = rows.iloc[0:0]
    if rows.empty:
        return {"final_answer": f"{category}에 해당하는 H&M 여성용 상품 중 20~39세 고객의 과거 겨울 온라인 구매 기록이 있는 검증 후보를 찾지 못했습니다.",
                "product_result": {"search_products": {"rows": []}}, "data_sources": ["hm"]}
    lines = [f"■ {category} 실제 H&M 상품 후보 ({len(rows)}개)",
             "기준: H&M 과거 데이터 / 20~39세 고객 / 온라인 채널 2 / 12~2월 / 여성용(Ladieswear·Divided) 상품",
             "상품 구매 기록 순으로 정렬했으며 최신 판매량·현재 인기 순위가 아닙니다.",
             "고객 성별은 확인할 수 없으며, 고객 나이도 거래 시점의 나이로 보증되지 않습니다.",
             f"세부 유형: {subtype or '미지정'} / 색상: {', '.join(colors or []) or '미지정'} / 유형은 상품명·설명 규칙 매칭이며 실측 성능 미검증.",
             "선택한 색상은 각각 최소 한 상품을 포함한 뒤 구매 기록순으로 후보를 구성합니다." if colors else "동일 모델은 구매 기록 상위 색상 1개를 선택합니다."]
    for index, row in rows.iterrows():
        lines.append(f"{index+1}. {row['prod_name']} / article_id={row['article_id']} / 유형={row['product_type_name']} / 색상={row['colour_group_name']} / 과거 겨울 온라인 구매 기록={int(row['winter_online_purchases']):,}건 / 구매 고객={int(row['winter_online_customers']):,}명")
    rows["keyword_match_verified"] = bool(subtype)
    rows["selected_subtype"] = subtype or ""
    # 게이트는 Tool 결과와 같은 구조의 명시적 DB 조회 행에서만 후보를 인정한다.
    return {"final_answer": "\n".join(lines), "dataframe": rows,
            "product_result": {"search_products": {"rows": rows.to_dict(orient="records")}},
            "data_sources": ["hm"], "data_purpose": f"{category}: H&M 과거 겨울철 온라인 구매 실적 기반 후보"}
