"""카테고리 단위 시장 근거: 상품명 필터 없이 수집 표본 분석."""
from __future__ import annotations
import pandas as pd
from shared_analysis.market.category_comparison import normalize_category, KEYWORD_CATEGORY
from agent_system.common_tools.external_data.naver_keyword_policy import apply_keyword_policy, keyword_tier
from shared_analysis.market.category_recommendation import summarize_naver, naver_change_table
from shared_analysis.market import review_analysis, selling_points
from shared_analysis.market.price_quality import analyze_metrics, render_metrics


def analyze_category(category, products, reviews, naver, collected_at=None):
    # 카테고리 정보가 없을 때 전체 데이터를 임의로 귀속하지 않음
    p = products.loc[products['category'].map(normalize_category).eq(category)].copy()
    r = reviews.loc[reviews['category'].map(normalize_category).eq(category)].copy()
    p = p.drop_duplicates('item_id')
    r = r.loc[r['item_id'].astype(str).isin(p['item_id'].astype(str))]
    if 'review_id' in r:
        r = r.drop_duplicates(['item_id', 'review_id'])
    review = review_analysis.analyze_reviews(p, r, [], all_products=True)
    selling = selling_points.analyze_selling_points(p, r, [], all_products=True)
    keys = sorted(set(naver['keyword'].dropna().astype(str))) if naver is not None and not naver.empty else []
    # 신규 CSV 매핑을 먼저 반영해 최초 호출부터 9개 아우터 키워드를 모두 사용합니다.
    apply_keyword_policy(KEYWORD_CATEGORY)
    mapped = [k for k in keys if KEYWORD_CATEGORY.get(k) == category]
    metrics = summarize_naver(naver, [{'naver_keywords': mapped}], KEYWORD_CATEGORY)
    from agent_system.orchestrator.tools.category_planning.subtype_selection import ranked_keywords
    positive = ranked_keywords(metrics)
    # 키워드별 증가율만으로 유형을 한 개 확정하지 않습니다.
    # 소분류 선정은 후속 단계의 H&M 구매 근거와 상품 유형 검증을 거칩니다.
    theme = None
    lines = [f'■ 무신사 {category} 전체 랭킹·리뷰 표본', f'수집 상품 {len(p):,}개 / 연결된 리뷰 {len(r):,}건 / 수집 기준: {collected_at or "미확인"}', '상품명 키워드 필터 없음. 랭킹 스냅샷이며 실제 판매량이나 시장 전체 순위가 아닙니다.']
    if not len(p): lines.append('해당 카테고리 수집 상품 없음.')
    if not len(r): lines.append('연결된 리뷰 없음. 리뷰 비율 산출 불가.')
    category_audit = None
    if category == '아우터' and not p.empty and 'name' in p:
        from agent_system.common_tools.external_data.musinsa_category_audit import audit_outer
        audit = audit_outer(p)
        category_audit = {str(k): int(v) for k,v in audit['audit_status'].value_counts().items()}
        lines.append('아우터 상품명 분류 점검: ' + ', '.join(f'{k} {v}개' for k,v in category_audit.items()) + ' (상품명 기반 잠정 판정, 원본 제외 없음)')
    market_metrics = analyze_metrics(p, r)
    lines += ['■ 가격·할인·평점·리뷰 표본 지표', *render_metrics(market_metrics)]
    if 'brand' in p:
        lines.append('표본 브랜드 빈도: ' + ', '.join(f'{k} {v}개' for k,v in p['brand'].value_counts().head(5).items()))
    overview_lines = list(lines)
    name_rows = sorted((a for a in selling['rows'] if a['product_names'] > 0), key=lambda x: (-x['product_names'], x['term']))[:8]
    if name_rows: overview_lines.append('표본 상품명 특징 빈도: ' + ', '.join(f"{a['term']} {a['product_names']}개" for a in name_rows))
    overview_lines.append('리뷰 상세 언급률과 판매 포인트는 11단계에서 표시합니다.')
    review_start = len(lines)
    def pct(v): return f'{v*100:.1f}%' if v is not None else '자료 없음'
    for a in sorted(review['rows'], key=lambda x: -x['mention_reviews']):
        lines.append(f"- {a['aspect']}: 본문 언급 {a['mention_reviews']}건 ({pct(a['mention_rate'])}); 선택 응답 {a['answered_reviews']}건, 규칙상 기준 이탈/불만 {pct(a.get('complaint_rate'))}")
    lines.append('언급률 분모는 연결 리뷰 전체, 불만/기준 이탈 비율 분모는 해당 항목 응답. 크기·두께 등의 기준 이탈이 실제 불만을 뜻하지 않을 수 있습니다.')
    for a in sorted(selling['rows'], key=lambda x: -x['reviews'])[:8]:
        lines.append(f"- 표현 '{a['term']}': 상품명 {a['product_names']}개 / 리뷰 {a['reviews']}건 (긍정·부정 구분 없는 표현 빈도)")
    review_lines = lines[review_start:]
    trend_start = len(lines)
    lines += ['', '■ 네이버 수집 키워드 상대 관심도']
    if not mapped: lines.append('해당 카테고리로 매핑된 수집 키워드 없음. 관심 부족을 의미하지 않습니다.')
    if mapped:
        lines.extend(naver_change_table({k: metrics[k] for k in mapped if k in metrics}, include_window=True))
    unknown = [k for k in keys if k not in KEYWORD_CATEGORY]
    if unknown: lines.append('카테고리 미분류 키워드: ' + ', '.join(unknown))
    if positive:
        lines.append(f'최근 관심도 상승 신호: {len(positive)}개 키워드에서 확인 (선정 순위 아님).')
    lines.append('소분류 추천 보류: H&M 유형별 구매 기록과 무신사 관련 상품 근거를 교차 검증한 후 선택합니다.')
    lines.append('네이버 상대 관심도는 판매량·절대 검색량이 아니며 기획전 기간 지속을 보장하지 않습니다. 무신사 카테고리 전체 및 H&M 상품을 해당 테마의 실제 상품으로 단정하지 않습니다.')
    classes = {k: ('상승 신호' if k in positive else '참고') for k in mapped}
    rows = [{'source':'musinsa','metric':'sample_products','value':len(p)}, {'source':'musinsa','metric':'sample_reviews','value':len(r)}]
    rows += [{'source':'musinsa','metric':a['aspect'],**a} for a in review['rows']]
    rows += [{'source':'musinsa_expression','metric':a['term'],**a} for a in selling['rows']]
    rows += [{'source':'musinsa_metric','metric':key,**metric} for key,metric in market_metrics['metrics'].items()]
    rows += [{'source':'naver','metric':k,'keyword_tier':keyword_tier(k),**v} for k,v in metrics.items()]
    return {'final_answer':'\n'.join(lines),'dataframe':pd.DataFrame(rows), 'data_sources':['musinsa','naver'], 'data_purpose':f'{category} 외부 시장 근거', 'category_market':{'market_metrics':market_metrics,'category_audit':category_audit,'category':category,'sample_products':len(p),'sample_reviews':len(r),'naver_metrics':metrics,'theme_candidate':theme, 'keyword_classes':classes, 'keyword_tiers':{k:keyword_tier(k) for k in mapped}, 'market_summary':'\n'.join([f'무신사 {category} 상품군 수집 표본: 상품 {len(p):,}개 / 연결 리뷰 {len(r):,}건 (판매량 아님)', '원본 수집일: '+str(collected_at or '미확인'), '상품군 분류 점검: '+(', '.join(f'{k} {v}개' for k,v in (category_audit or {}).items()) if category_audit else '원본 카테고리 기준; 상품명 세부 판정 별도'), '가격·할인·평점과 상품명·리뷰 상세는 후속 상품 유형 분석에서 별도 확인합니다. 현재 첨부 CSV는 표본 규모와 구매 시계열 근거입니다.', '상품 유형별 네이버 검색 추세는 상품군 선택 후 비교합니다.']), 'review_summary':'\n'.join(overview_lines + review_lines + [f'무신사 {category} 상품군의 수집 리뷰이며 H&M 상품 직접 리뷰가 아닙니다.'])}}


def build_category_market(category):
    # R11_MUSINSA_DB_AUDIT: 통합 CSV→카테고리별 DuckDB 조회와 실행 소스 기록
    import logging
    import os
    _logger = logging.getLogger(__name__)
    from common.external_data_store import enabled as _r8_enabled
    if _r8_enabled():
        from agent_system.common_tools.external_data.musinsa_sample import category_frames
        from agent_system.common_tools.external_data.trend_files import load_naver
        p, r, collected = category_frames(category)
        _logger.info("MARKET_DATA_SOURCE source=duckdb category=%s products=%d reviews=%d", category, len(p), len(r))
        # DB에 적재된 범위가 비었는데 '표본 0개'로 정상 처리하면 잘못된 선정 근거가 됨
        if p.empty:
            raise ValueError(f"무신사 DuckDB 조회 결과가 비었습니다: {category}. DB 카테고리 매핑과 Slack 실행 환경을 확인하세요.")
        result = analyze_category(category, p, r, load_naver(), collected)
        result['category_market']['external_source'] = 'duckdb'
        return result
    _logger.info("MARKET_DATA_SOURCE source=csv category=%s env=%s", category, os.getenv('CART_SIGNAL_EXTERNAL_SOURCE', '<unset>'))
    from agent_system.common_tools.external_data import musinsa_sample, trend_files
    from datetime import datetime, timezone
    # 전역 중복 제거 이전의 카테고리별 랭킹/리뷰 파일을 읽습니다.
    sets = [(k, v) for k, v in musinsa_sample.file_sets().items()
            if normalize_category(k) == category]
    if len(sets) > 1:
        raise ValueError("동일 카테고리 파일 세트가 여러 개입니다.")
    if sets:
        _, files = sets[0]
        p = pd.read_csv(files['ranking'], dtype={'item_id': str})
        r = pd.read_csv(files['review'], dtype={'item_id': str, 'review_id': str}) if 'review' in files else pd.DataFrame(columns=['item_id','satisfaction','review'])
        if 'add_info' in files:
            info = pd.read_csv(files['add_info'], dtype={'item_id': str})
            if 'item_id' in info:
                extras = [c for c in ('gender','rating','review_count') if c in info and c not in p]
                if extras:
                    p = p.merge(info[['item_id', *extras]].drop_duplicates('item_id'), on='item_id', how='left')
        p['category'] = category
        r['category'] = category
        stamp = datetime.fromtimestamp(files['ranking'].stat().st_mtime, tz=timezone.utc).date()
        collected = f"미상 (랭킹 파일 수정일 {stamp})"
        if 'collected_at' in p:
            dates = pd.to_datetime(p['collected_at'], errors='coerce').dropna()
            if len(dates): collected = str(dates.max().date())
    else:
        p = pd.DataFrame(columns=['category','item_id','name','ranking'])
        r = pd.DataFrame(columns=['category','item_id','satisfaction','review'])
        collected = None
    return analyze_category(category, p, r, trend_files.load_naver(), collected)
