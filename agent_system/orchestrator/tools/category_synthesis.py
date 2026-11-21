"""7단계 전용 종합 근거. LLM이 생성한 문장이 아닌 Tool의 구조화된 결과만 사용한다."""
from __future__ import annotations
import math
from collections import Counter
from pathlib import Path
import re
import pandas as pd


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


def summarize(category, hm, market):
    hm = hm if hm.get('category') == category else {}
    market = market if market.get('category') == category else {}
    colors = list(_counts(hm.get('colour_counts')).items())[:5]
    tags = list(_counts(hm.get('style_tag_counts')).items())[:5]
    metrics = {str(k): v for k,v in (market.get('naver_metrics') or {}).items() if isinstance(v,dict) and v.get('category') == category}
    theme = market.get('theme_candidate')
    classes = market.get('keyword_classes') or {}
    if theme not in metrics: theme = None
    lines = [f'분석 범위: {category} / 5·6단계 저장 결과 정리 (새로운 추천·조회 없음)',
             f'6단계 테마 검토 후보: {theme or "보류 (근거 미확인)"}']
    if not metrics: lines.append('네이버 해당 카테고리 매핑 지표 미수집 또는 미확인. 관심 부족을 뜻하지 않습니다.')
    for k,v in metrics.items():
        g = _num(v.get('growth_percent'))
        lines.append(f"- {classes.get(k, '분류 미확인')} | 네이버 {k}: {v.get('prior_mean')} → {v.get('recent_mean')} / 변화 {f'{g:+.2f}%' if g is not None else '계산 불가'} / 기준 {v.get('latest_period') or '미확인'}")
    lines.append('제외: 6단계에서 확인된 제외 근거 없음. 상승의 지속 여부는 미확인.')
    total = hm.get('total_articles')
    if total is not None:
        lines.append(f"H&M 상품 구성: 색상별 상품 {total:,}개 / 모델 {hm.get('total_products')}개 / 범위: {hm.get('basis','미확인')}")
    if hm.get('query'): lines.append('검색 조건: '+hm['query'])
    lines.append('색상 구성 참고: '+(', '.join(f'{k} {v:,}개' for k,v in colors) or '자료 없음'))
    lines.append('스타일 구성 참고: '+(', '.join(f'{k} {v:,}개' for k,v in tags) or '자료 없음'))
    lines.append('H&M 빈도는 구매·인기 순위가 아니며 스타일 태그는 중복될 수 있습니다.')
    lines.append(f"무신사 전체 카테고리 표본: 상품 {market.get('sample_products','미확인')}개 / 연결 리뷰 {market.get('sample_reviews','미확인')}건")
    lines.append('리뷰 상세·판매 포인트는 11단계에서 확인합니다. 테마와 H&M 실제 상품의 직접 일치는 미검증입니다.')
    records = [{'source':'hm_colour','metric':k,'value':v,'unit':'article_count'} for k,v in colors]
    records += [{'source':'hm_style','metric':k,'value':v,'unit':'article_count'} for k,v in tags]
    records += [{'source':'musinsa_sample','metric':k,'value':market[k],'unit':'sample_count'} for k in ('sample_products','sample_reviews') if _num(market.get(k)) is not None]
    records += [{'source':'naver','metric':k,'value':_num(v.get('growth_percent')),'unit':'relative_interest_change_percent','prior_mean':v.get('prior_mean'),'recent_mean':v.get('recent_mean'),'latest_period':v.get('latest_period')} for k,v in metrics.items()]
    evidence={'category':category,'theme_candidate':theme,'hm':hm,'market':market,'keyword_classes':classes}
    return {'final_answer':'\n'.join(lines),'dataframe':pd.DataFrame(records),'data_sources':['hm','musinsa','naver'],'data_purpose':f'{category} 5~6단계 결과 정리','category_synthesis':evidence}


def _chart(result, run_id):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    # 한글 글꼴이 없는 환경에서는 영문 대체 라벨을 쓰고 원문은 CSV/본문에 유지.
    font = next((font for font in ('AppleGothic','Malgun Gothic','Noto Sans CJK KR','NanumGothic') if any(f.name == font for f in font_manager.fontManager.ttflist)), None)
    frame = result['dataframe']
    if frame.empty: return None
    evidence = result['category_synthesis']
    aliases = {'풀집업':'Full zip','후드':'Hood','스탠드칼라':'Stand collar','코튼':'Cotton','울':'Wool','사이즈':'Size','보온성':'Warmth','색감':'Colour','두께감':'Thickness','퀄리티':'Quality','경량패딩':'Lightweight padded outerwear'}
    def label(x, i):
        return str(x) if font or str(x).isascii() else aliases.get(str(x), f'Item {i+1} (see table)')
    groups=[('hm_colour','H&M colour assortment','Article count'),('hm_style','H&M style tags','Article count'),('musinsa_sample','Musinsa sample coverage','Sample count'),('naver','Naver within-keyword change','Relative change %')]
    with plt.rc_context({'font.family': font or 'DejaVu Sans','axes.unicode_minus':False}):
        fig, axes=plt.subplots(2,2,figsize=(12,8))
        try:
            for ax,(source,title,unit) in zip(axes.flat,groups):
                rows=frame.loc[frame['source'].eq(source)].dropna(subset=['value'])
                ax.set_title(title); ax.set_xlabel(unit)
                if rows.empty:
                    ax.text(.5,.5,'No data',ha='center',va='center',transform=ax.transAxes); ax.set_yticks([]); continue
                pairs=[(label(r.metric,i),float(r.value)) for i,r in enumerate(rows.itertuples())]
                ax.barh([p[0] for p in pairs][::-1],[p[1] for p in pairs][::-1]); ax.margins(x=.18)
                for i,(_,v) in enumerate(pairs[::-1]): ax.annotate(f'{v:,.1f}' if '%' in unit else f'{v:,.0f}',(v,i),xytext=(3,0),textcoords='offset points',va='center',fontsize=8)
            fig.suptitle('Campaign evidence | different units and sources, not a combined ranking')
            fig.tight_layout(rect=(0,0,1,.96))
            root=Path(__file__).resolve().parents[3]
            name=re.sub(r'[^a-zA-Z0-9_-]','_',str(run_id)) or 'run'
            category=re.sub(r'[^\w·-]','_',str(evidence['category']))
            dest=root/'visualization_output'/'category_synthesis'/name/f'{category}_evidence.png'
            dest.parent.mkdir(parents=True,exist_ok=True); fig.savefig(dest,dpi=130)
            return str(dest.resolve())
        finally: plt.close(fig)


def build_category_summary(category, hm, market, run_id):
    # 결과 정리 단계에서는 누락된 자료도 재조회하지 않는다.
    result = summarize(category, hm or {}, market or {})
    try:
        path = _chart(result,run_id)
        if path: result['visualization_path'] = path
    except Exception as exc:
        result['final_answer'] += f'\n7단계 그래프 생성 불가 ({type(exc).__name__}); 표와 본문은 유지됩니다.'
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
        if not frame.empty: frame = frame.loc[frame['source'].eq('naver') | frame['metric'].isin(['sample_products','sample_reviews'])]
        sources = ['musinsa','naver']
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
    lines = ['8단계 검증 후보 비교 (구매 기록 순, 재검색·재추천 없음).', '상품명·설명 기준 유형 매칭; 실제 기능 검증 아님.' if frame.get('keyword_match_verified', pd.Series(False,index=frame.index)).all() else '테마 직접 일치 검증은 수행하지 않았습니다.']
    for row in rows:
        lines.append(f"- {row['article_id']} | {row['prod_name']} | 구매 {row['winter_online_purchases']:,}건 | 스타일 {row.get('style_tags','미확인')} | 색상 {row.get('colour_group_name','미확인')} | 유형 {'규칙 매칭' if row.get('keyword_match_verified') else '미검증'}")
    result = {'final_answer':'\n'.join(lines),'dataframe':frame,'data_sources':['hm'],'data_purpose':f'{category} 8단계 후보 시각화'}
    try: result['visualization_path'] = purchase_chart(frame, folder/f'{cat}_winter_purchases.png')
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
