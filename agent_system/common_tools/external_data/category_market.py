"""카테고리 단위 시장 근거: 상품명 필터 없이 수집 표본 분석."""
from __future__ import annotations
import pandas as pd
from shared_analysis.market.category_comparison import normalize_category, KEYWORD_CATEGORY
from shared_analysis.market.category_recommendation import summarize_naver
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
    mapped = [k for k in keys if KEYWORD_CATEGORY.get(k) == category]
    metrics = summarize_naver(naver, [{'naver_keywords': mapped}], KEYWORD_CATEGORY)
    from agent_system.orchestrator.tools.category_planning.subtype_selection import ranked_keywords
    positive = ranked_keywords(metrics)
    theme = positive[0] if positive else None
    lines = [f'■ 무신사 {category} 전체 랭킹·리뷰 표본', f'수집 상품 {len(p):,}개 / 연결된 리뷰 {len(r):,}건 / 수집 기준: {collected_at or "미확인"}', '상품명 키워드 필터 없음. 랭킹 스냅샷이며 실제 판매량이나 시장 전체 순위가 아닙니다.']
    if not len(p): lines.append('해당 카테고리 수집 상품 없음.')
    if not len(r): lines.append('연결된 리뷰 없음. 리뷰 비율 산출 불가.')
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
    for k in mapped:
        m = metrics.get(k, {})
        g = m.get('growth_percent')
        lines.append(f"- {k}: 직전 4구간 {m.get('prior_mean')} / 최근 4구간 {m.get('recent_mean')} / 변화 {f'{g:+.2f}%' if g is not None else '계산 불가'} / 기준 {m.get('latest_period') or '미확인'} / {m.get('unit') or '구간 미확인'}")
        if g is not None: lines.append(f"  비교 구간: {m.get('prior_window')} → {m.get('recent_window')}")
    unknown = [k for k in keys if k not in KEYWORD_CATEGORY]
    if unknown: lines.append('카테고리 미분류 키워드: ' + ', '.join(unknown))
    if theme: lines.append(f'기획전 테마 검토 제안: {theme} (수집된 +10% 이상 상승 키워드 중 상대 변화율 기준 1순위; 상품 일치 별도 검증, 시장 전체 인기 순위 아님).')
    lines.append('네이버 상대 관심도는 판매량·절대 검색량이 아니며 기획전 기간 지속을 보장하지 않습니다. 무신사 카테고리 전체 및 H&M 상품을 해당 테마의 실제 상품으로 단정하지 않습니다.')
    classes = {k: ('상승 1순위(잠정)' if k == theme else '상승 후보' if k in positive else '참고') for k in mapped}
    rows = [{'source':'musinsa','metric':'sample_products','value':len(p)}, {'source':'musinsa','metric':'sample_reviews','value':len(r)}]
    rows += [{'source':'musinsa','metric':a['aspect'],**a} for a in review['rows']]
    rows += [{'source':'musinsa_expression','metric':a['term'],**a} for a in selling['rows']]
    rows += [{'source':'musinsa_metric','metric':key,**metric} for key,metric in market_metrics['metrics'].items()]
    rows += [{'source':'naver','metric':k,**v} for k,v in metrics.items()]
    return {'final_answer':'\n'.join(lines),'dataframe':pd.DataFrame(rows), 'data_sources':['musinsa','naver'], 'data_purpose':f'{category} 외부 시장 근거', 'category_market':{'market_metrics':market_metrics,'category':category,'sample_products':len(p),'sample_reviews':len(r),'naver_metrics':metrics,'theme_candidate':theme, 'keyword_classes':classes, 'market_summary':'\n'.join(overview_lines + lines[trend_start:]), 'review_summary':'\n'.join(overview_lines + review_lines + ['무신사 아우터 등 카테고리 전체의 경쟁 상품 리뷰이며 H&M 상품 직접 리뷰가 아닙니다.'])}}


def build_category_market(category):
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
