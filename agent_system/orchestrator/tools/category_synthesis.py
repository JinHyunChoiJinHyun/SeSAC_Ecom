"""7단계 전용 종합 근거. LLM이 생성한 문장이 아닌 Tool의 구조화된 결과만 사용한다."""
from __future__ import annotations
import math
from collections import Counter
from pathlib import Path
import re
import pandas as pd
from shared_analysis.market.category_recommendation import naver_change_table


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _counts(values):
    if not isinstance(values, dict):
        return {}
    out = {}
    for k, v in values.items():
        n = _num(v)
        if n is not None and n >= 0 and n.is_integer():
            out[str(k)] = int(n)
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def capture_hm(result, category):
    """5단계 검색 Tool이 실제 반환한 집계값. LLM 문장/추정치 제외."""
    data = (result.get('product_result') or {}).get('search_products') or {}
    if not isinstance(data, dict):
        data = {}
    colors = _counts(data.get('colour_counts'))
    tags = _counts(data.get('style_tag_counts'))
    total = _num(data.get('total_articles'))
    models = _num(data.get('total_products'))
    return {'category': category, 'query': str(data.get('query') or ''),
            'basis': str(data.get('basis') or '5단계 H&M 검색 Tool의 상품 구성'),
            'total_articles': int(total) if total is not None and total >= 0 else None,
            'total_products': int(models) if models is not None and models >= 0 else None,
            'colour_counts': colors, 'style_tag_counts': tags}


def capture_market(result):
    """6단계 시장 구조와 리뷰 표에서 직렬화 가능한 근거만 복사한다."""
    market = dict(result.get('category_market') or {})
    rows = []
    frame = result.get('dataframe')
    if isinstance(frame, pd.DataFrame) and {'source','aspect'}.issubset(frame.columns):
        for row in frame.loc[frame['source'].eq('musinsa') & frame['aspect'].notna()].to_dict('records'):
            a = {'aspect': str(row['aspect'])}
            for key in ('mention_reviews','mention_rate','answered_reviews','complaint_rate'):
                n = _num(row.get(key))
                a[key] = int(n) if n is not None and key.endswith('_reviews') else n
            rows.append(a)
    market['review_aspects'] = rows
    return market


def _fallback_hm(category):
    # 5단계 Tool이 집계를 남기지 않은 경우에만 실제 상품 테이블에서 표준 카테고리 마스크로 재집계.
    from agent_system.common_tools.local_data import hm_products
    from shared_analysis.product.category_mapping import category_masks
    articles = hm_products.articles()
    selected = articles.loc[category_masks(articles)[category]]
    tags = Counter(t for values in selected['style_tags'] if isinstance(values,(list,tuple,set)) for t in set(values))
    return {'category': category, 'query': '', 'basis': '표준 category_masks 기준 H&M 전체 상품 재집계 (5단계 검색 범위와 다를 수 있음)',
            'total_articles': int(len(selected)), 'total_products': int(selected['product_code'].nunique()),
            'colour_counts': _counts(selected['colour_group_name'].value_counts().head(10).to_dict()),
            'style_tag_counts': _counts(dict(tags))}


def interpret_monthly_seasons(monthly: pd.DataFrame, category: str) -> str:
    """선택 상품군의 관측 월별 구매량만 이용해 두 시즌의 방향을 설명한다."""
    required = {'season', 'month', 'category', 'purchases'}
    if monthly is None or monthly.empty or not required.issubset(monthly.columns):
        return '월별 구매 변화: 비교 가능한 관측 자료가 부족합니다.'
    part = monthly.loc[monthly['category'].eq(category)].copy()
    if part.empty:
        return '월별 구매 변화: 선택 상품군의 관측 자료가 없습니다.'
    part['purchases'] = pd.to_numeric(part['purchases'], errors='coerce')
    if part['purchases'].isna().any() or (part['purchases'] < 0).any():
        return '월별 구매 변화: 구매 건수 검증이 필요합니다.'
    series = []
    for season, rows in part.groupby('season', sort=True):
        values = {}
        for row in rows.itertuples(index=False):
            key = str(row.month)[5:7]
            if key not in {'11', '12', '01'} or key in values or not float(row.purchases).is_integer():
                return '월별 구매 변화: 겨울 월별 중복·누락 또는 집계값을 확인해야 합니다.'
            values[key] = int(row.purchases)
        if set(values) != {'11','12','01'}:
            return '월별 구매 변화: 비교할 11월·12월·1월 기록이 모두 필요합니다.'
        def direction(before, after):
            return '증가' if after > before else ('감소' if after < before else '동일')
        series.append((season, direction(values['11'],values['12']),direction(values['12'],values['01'])))
    if len(series) != 2:
        return '월별 구매 변화: 두 겨울 시즌의 자료가 필요합니다.'
    def phrase(index, label):
        trends = [v[index] for v in series]
        if trends[0] == trends[1]:
            return f'두 겨울 시즌 모두 {label} 구매 건수가 {trends[0]}했습니다.' if trends[0] != '동일' else f'두 겨울 시즌 모두 {label} 구매 건수가 같았습니다.'
        return (f'{label} 구매 건수는 첫 시즌에 {trends[0]}, 두 번째 시즌에 {trends[1]}해 '
                '시즌별 방향이 달랐습니다.')
    return '월별 주요 발견: ' + ' '.join([phrase(1,'11월 대비 12월'), phrase(2,'12월 대비 1월')])


def summarize(category, hm, market):
    """상품군 검토 결과: 유형별 네이버 지수·색상·스타일은 이 단계에서 출력하지 않는다."""
    hm = hm if hm.get('category') == category else {}
    market = market if market.get('category') == category else {}
    # 5단계 H&M 수치·6단계 무신사 표본을 7단계에서 그대로 반복하지 않는다.
    lines = [f'{category} 상품군 분석의 주요 발견']
    records = [{'source':'musinsa_sample','metric':k,'value':market[k],'unit':'sample_count'}
               for k in ('sample_products','sample_reviews') if _num(market.get(k)) is not None]
    evidence = {'category':category,'theme_candidate':None,'hm':hm,'market':market,
                'keyword_classes':market.get('keyword_classes') or {}}
    return {'final_answer':'\n'.join(lines),'dataframe':pd.DataFrame(records),
            'data_sources':['hm','musinsa'],'data_purpose':f'{category} 상품군 근거 정리',
            'category_synthesis':evidence}


def build_category_summary(category, hm, market, run_id, *, period_plan=None, period_rows=None):
    # 조회 실패 시 수치 추측 또는 다른 유형의 그래프로 대체하지 않는다.
    result = summarize(category, hm or {}, market or {})
    if period_plan is not None:
        try:
            from agent_system.orchestrator.tools.category_planning.seasonal_trends import load_monthly_purchases, verify_monthly_totals
            from shared_analysis.market.category_comparison import save_monthly_category_chart, save_weekly_campaign_chart
            monthly = load_monthly_purchases(period_plan)
            if period_rows:
                verify_monthly_totals(monthly, period_rows)
            safe = re.sub(r'[^a-zA-Z0-9_-]', '_', str(run_id)) or 'run'
            folder = Path(__file__).resolve().parents[3] / 'visualization_output' / 'category_synthesis' / safe
            result['visualization_path'] = save_monthly_category_chart(monthly, folder / 'hm_monthly_category.png', category=category)
            result['attachments'] = []
            # 월별 일평균은 총건수와 같은 패턴이므로 그래프 중복 노출을 피하고 CSV에서만 유지한다.
            from agent_system.orchestrator.tools.category_planning.seasonal_trends import load_aligned_weekly_purchases
            try:
                weekly = load_aligned_weekly_purchases(period_plan, categories=[category])
                weekly_chart = save_weekly_campaign_chart(weekly, folder / 'hm_aligned_weekly.png', category=category)
                result['attachments'].append(weekly_chart)
                from agent_system.orchestrator.tools.category_planning.campaign_evidence import summarize_campaign_window
                evidence = summarize_campaign_window(weekly, [{'category': category}])[category]
                result['final_answer'] += (f"\n기획전 대응 기간: 과거 두 겨울 시즌 합산 "
                    f"{evidence['purchases']:,}건, 일평균 {evidence['purchases_per_day']:,.1f}건 "
                    f"(시즌별 {evidence['days_per_season']}일 관측).")
                weekly['source'] = 'hm_campaign_aligned_weekly'
                weekly['metric'] = 'purchases_per_day'
                result['dataframe'] = pd.concat([result['dataframe'],weekly],ignore_index=True)
            except Exception as exc:
                result['final_answer'] += f'\n기획전 대응 주별 구매 추이: 자료 확인 필요 ({type(exc).__name__}: {exc})'
            monthly = monthly.loc[monthly['category'].eq(category)].copy()
            result['final_answer'] += '\n' + interpret_monthly_seasons(monthly, category)
            monthly['source']='hm_monthly'
            monthly['metric']='purchases'
            result['dataframe']=pd.concat([result['dataframe'],monthly],ignore_index=True)
            result['final_answer'] += '\n월별·주별 집계 원자료는 첨부 CSV에 있습니다.'
        except Exception as exc:
            result['final_answer'] += f'\n월별 구매 추이 그래프 생성 보류: {type(exc).__name__}: {exc}'
    return result


def market_view(result, step):
    """6단계 계산 결과를 보존하고 단계별 출력만 분리."""
    market = dict(result['category_market'])
    frame = result['dataframe']
    # pandas의 NaN을 JSON null로 변환, 원본 리뷰 본문은 저장하지 않는다.
    import json
    saved = json.loads(frame.to_json(orient='records', force_ascii=False))
    market['saved_rows'] = saved
    return market, render_market(market, step)


def render_market(market, step):
    frame = pd.DataFrame(market.get('saved_rows') or [])
    if step == 6:
        answer = market['market_summary']
        if not frame.empty: frame = frame.loc[frame['metric'].isin(['sample_products','sample_reviews'])]
        sources = ['musinsa']
    elif step == 11:
        answer = market['review_summary'] + '\n6단계와 동일한 카테고리 표본을 재사용한 리뷰·판매 포인트 집계입니다.'
        if not frame.empty: frame = frame.loc[frame['source'].ne('naver')]
        sources = ['musinsa']
    else: raise ValueError('지원 단계가 아닙니다.')
    return {'final_answer':answer,'dataframe':frame,'data_sources':sources,'data_purpose':f"{market['category']} {step}단계 근거"}


def product_summary(rows, category, run_id):
    """8단계 검증 후보의 저장된 값으로 표와 그래프, 실제 이미지 모음 작성."""
    from agent_system.orchestrator.tools.category_planning.product_evidence import _chart as purchase_chart
    frame = pd.DataFrame(rows)
    if frame.empty: raise ValueError('8단계 저장 후보 없음')
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', str(run_id))
    cat = re.sub(r'[^\w·-]', '_', str(category))
    folder = Path(__file__).resolve().parents[3]/'visualization_output'/'category_product_evidence'/safe
    lines = ['과거 H&M 판매 참고 사례 비교 (구매 기록 순, 실제 판매 상품 편성 아님).', '상품명·설명 기준 유형 매칭; 실제 기능 검증 아님.' if frame.get('keyword_match_verified', pd.Series(False,index=frame.index)).all() else '테마 직접 일치 검증은 수행하지 않았습니다.']
    for row in rows:
        lines.append(f"- {row['article_id']} | {row['prod_name']} | 구매 {row['winter_online_purchases']:,}건 | 스타일 {row.get('style_tags','미확인')} | 색상 {row.get('colour_group_name','미확인')} | 유형 {'규칙 매칭' if row.get('keyword_match_verified') else '미검증'}")
    result = {'final_answer':'\n'.join(lines),'dataframe':frame,'data_sources':['hm'],'data_purpose':f'{category} 8단계 후보 시각화'}
    try: result['visualization_path'] = purchase_chart(frame, folder/f'{cat}_historical_reference_purchases.png')
    except Exception as exc: result['final_answer'] += f'\n구매 그래프 생성 실패 ({type(exc).__name__})'
    try:
        image_path = product_image_sheet(rows, folder/f'{cat}_products.png')
        if image_path: result['attachments'] = [image_path]
        else: result['final_answer'] += '\n실제 첨부 가능한 상품 이미지 없음.'
    except Exception as exc: result['final_answer'] += f'\n상품 이미지 모음 생성 실패 ({type(exc).__name__})'
    return result


def product_image_sheet(rows, destination):
    from agent_system.common_tools.local_data import hm_images
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    files = []
    for row in rows:
        path = hm_images.image_file(row['article_id'])
        if path:
            try: files.append((row, mpimg.imread(path)))
            except (OSError, ValueError, SyntaxError): pass
    if not files: return None
    fig, axes = plt.subplots(1,len(files), figsize=(3*len(files),4.4), squeeze=False)
    try:
        for ax,(row,img) in zip(axes[0],files):
            ax.imshow(img); ax.axis('off')
            ax.set_title(str(row['prod_name'])[:28]+'\n'+str(row['article_id']), fontsize=9)
        fig.suptitle('H&M historical candidate images | see product evidence')
        fig.tight_layout()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(destination,dpi=140)
        return str(destination.resolve())
    finally: plt.close(fig)
