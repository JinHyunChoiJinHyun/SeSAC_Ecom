"""CART_SIGNAL 5개 카테고리 비교: H&M 실측 + 무신사 스냅샷 + 네이버 추이."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import unicodedata

import pandas as pd
from shared_analysis.product.category_mapping import CATEGORIES

CATEGORY_ALIASES = {
    '긴소매 티셔츠': '긴소매 티셔츠', '긴소매/티셔츠': '긴소매 티셔츠',
    '니트/스웨터': '니트·스웨터', '니트·스웨터': '니트·스웨터',
    '반소매/티셔츠': '반소매 티셔츠', '반소매 티셔츠': '반소매 티셔츠',
    '셔츠/블라우스': '셔츠·블라우스', '셔츠·블라우스': '셔츠·블라우스',
    '아우터': '아우터',
}
# 여기의 키워드는 추천 결과를 고정하지 않고 네이버 시계열을 해당 카테고리에 연결하기 위한 매핑이다.
KEYWORD_CATEGORY = {'경량패딩': '아우터', '패딩': '아우터', '코트': '아우터', '플리스': '아우터', '재킷': '아우터', '자켓': '아우터', '경량 패딩': '아우터', '일반 패딩': '아우터',
                    '니트': '니트·스웨터', '스웨터': '니트·스웨터',
                    '긴소매 티셔츠': '긴소매 티셔츠', '반소매 티셔츠': '반소매 티셔츠',
                    '셔츠': '셔츠·블라우스', '블라우스': '셔츠·블라우스'}


def normalize_category(value):
    return CATEGORY_ALIASES.get(unicodedata.normalize('NFC', str(value)).strip().replace('_','/'))


def combine_rows(hm_rows, musinsa_products, musinsa_reviews, naver_keywords):
    """카테고리를 지우지 않고 근거를 결합한다. 점수는 임의로 합산하지 않는다."""
    hm = {r['category']: r for r in hm_rows}
    p = musinsa_products.copy()
    r = musinsa_reviews.copy()
    for frame in (p, r):
        if 'category' not in frame:
            frame['category'] = None
        frame['mapped_category'] = frame['category'].map(normalize_category)
    output = []
    for category in CATEGORIES:
        pc = p[p['mapped_category'] == category]
        rc = r[r['mapped_category'] == category]
        row = hm.get(category, {})
        matched = sorted(k for k in naver_keywords if KEYWORD_CATEGORY.get(k) == category)
        output.append({
            'category': category,
            'hm_article_count': int(row.get('article_count', 0)),
            'hm_product_count': int(row.get('product_count', 0)),
            'hm_purchases': int(row.get('purchases', 0)),
            'musinsa_sample_products': int(pc['item_id'].nunique()) if 'item_id' in pc else 0,
            'musinsa_sample_reviews': int(len(rc)),
            'naver_keywords': matched,
            'naver_status': '배치 키워드 존재' if matched else '수집 데이터 없음',
        })
    return output


def suggest(rows, top_n=3):
    """H&M 실적 근거의 설명 가능한 임시 후보. 외부 자료를 판매량으로 환산하지 않는다."""
    ranked = sorted(rows, key=lambda x: (-x['hm_purchases'], x['category']))
    return [{**r, 'recommendation_basis': 'H&M 과거 겨울철 온라인 구매 기록 순위',
             'caveat': '최근 시장 인기 확정 아님. 무신사·네이버 근거는 별도 확인'}
            for r in ranked[:max(1, min(int(top_n), len(ranked)))]]


def save_charts(rows, naver, out, *, period_plan=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if any(f.name == 'AppleGothic' for f in font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = 'AppleGothic'
    plt.rcParams['axes.unicode_minus'] = False
    from shared_analysis.market import trend_analysis
    out.mkdir(parents=True, exist_ok=True)
    labels = [r['category'] for r in rows]
    values = [r['hm_purchases'] for r in rows]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(len(rows)), values)
    ax.set_xticks(range(len(rows)), labels, rotation=15, ha='right')
    ax.set_ylabel('H&M historical transaction records')
    ax.set_title('H&M historical core months / online / ages 20–39 / womenswear' if period_plan else 'H&M past winter / online / ages 20–39 / womenswear')
    if period_plan:
        ranges = ', '.join(f"{season['core']['start']}~{season['core']['end']}" for season in period_plan['seasons'])
        ax.set_xlabel(ranges, fontsize=8)
    fig.tight_layout(); fig.savefig(out/'hm_category_purchases.png', dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(len(rows)), [r['musinsa_sample_reviews'] for r in rows])
    ax.set_xticks(range(len(rows)), labels, rotation=15, ha='right')
    ax.set_ylabel('Collected reviews (sample size, not sales)')
    ax.set_title('Musinsa batch review coverage')
    fig.tight_layout(); fig.savefig(out/'musinsa_review_coverage.png', dpi=160); plt.close(fig)
    keys = sorted({k for row in rows for k in row['naver_keywords']})
    if keys and not naver.empty:
        prepared, _ = trend_analysis.prepare_series(naver, keys)
        prepared, _ = trend_analysis.weekly_if_daily(prepared)
        if not prepared.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            for k, g in prepared.groupby('keyword'):
                ax.plot(g['period'], g['ratio'], label=k)
            ax.set_ylabel('Naver relative interest (per request)')
            ax.set_title('Naver available trend series (do not compare cross-request levels)')
            ax.legend(); fig.autofmt_xdate(); fig.tight_layout()
            fig.savefig(out/'naver_available_trends.png', dpi=160); plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='audit_output/market_category_comparison')
    parser.add_argument('--top', type=int, default=3)
    args = parser.parse_args()
    from agent_system.common_tools.local_data.category_sales import category_sales
    from agent_system.common_tools.external_data import musinsa_sample, trend_files
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    sales = category_sales(age_min=20, age_max=39, channel='online', winter_only=True)
    musinsa = musinsa_sample.load()
    naver = trend_files.load_naver()
    keywords = set(naver['keyword'].dropna().astype(str)) if not naver.empty else set()
    rows = combine_rows(sales['rows'], musinsa['products'], musinsa['reviews'], keywords)
    recommended = suggest(rows, args.top)
    payload = {'filters': {'age': '20~39 (저장된 age)', 'channel': 'online',
                           'merchandise': 'Ladieswear/Divided', 'months': [12, 1, 2],
                           'customer_gender': '확인 불가'},
               'hm_as_of': sales['data_as_of'], 'musinsa_collected_at': musinsa.get('collected_at'),
               'notes': ['H&M은 과거 구매 실적, 무신사 리뷰 건수는 수집 표본 크기',
                         '네이버는 수집된 키워드만 분석; 없는 카테고리를 배제하지 않음',
                         '현재 후보 추천은 H&M 구매 기록 기준 임시 순위이며 외부 근거를 결합한 최종 추천이 아님'],
               'rows': rows, 'provisional_hm_top': recommended}
    (output/'comparison.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    pd.DataFrame(rows).to_csv(output/'comparison.csv', index=False, encoding='utf-8-sig')
    save_charts(rows, naver, output)
    print(pd.DataFrame(rows).to_string(index=False))
    print('\n[H&M 실적 기준 잠정 후보]')
    for r in recommended:
        print(f"- {r['category']}: {r['hm_purchases']:,}건 / 근거: 과거 온라인 겨울 구매")
    print(f'\n저장 위치: {output}')
    print('주의: 시장 통합 추천 알고리즘이 아니라 근거 분리 비교 및 H&M 실적 잠정 순위입니다.')


if __name__ == '__main__':
    main()


def save_monthly_category_chart(frame, destination, *, category=None, metric='purchases'):
    """두 겨울 시즌을 독립 패널로 비교한다. 상품군·기간은 데이터로 결정한다."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from shared_analysis.product.category_mapping import CATEGORIES
    if frame is None or frame.empty:
        raise ValueError('월별 관측값이 없습니다.')
    labels = ([category] if category is not None else list(CATEGORIES))
    plot = frame.loc[frame['category'].isin(labels)].copy()
    if plot.empty:
        raise ValueError('선택한 상품군의 관측값이 없습니다.')
    if metric not in ('purchases','purchases_per_day'):
        raise ValueError('지원하지 않는 분석 지표입니다.')
    if metric not in plot:
        if metric == 'purchases_per_day':
            from agent_system.orchestrator.tools.category_planning.seasonal_trends import monthly_daily_average
            plot = monthly_daily_average(plot)
        else:
            raise ValueError('구매 건수 칼럼이 없습니다.')
    seasons = sorted(plot['season'].dropna().unique().tolist())
    if not seasons:
        raise ValueError('시즌 식별자가 없습니다.')
    korean = next((x for x in ('AppleGothic','Malgun Gothic','NanumGothic','Noto Sans CJK KR')
                   if any(f.name==x for f in font_manager.fontManager.ttflist)), None)
    from agent_system.common_tools.execution_tools import visualization_tools as viz
    try:
        viz.configure_korean_font()
    except (AttributeError, OSError):
        pass
    with plt.rc_context({'font.family':korean or 'DejaVu Sans','axes.unicode_minus':False}):
        fig, axes = plt.subplots(1,len(seasons),figsize=(6.2*len(seasons),5.4),squeeze=False,sharey=True)
        palette = plt.get_cmap('tab10')
        try:
            for ax,season in zip(axes[0], seasons):
                part=plot.loc[plot['season'].eq(season)].copy()
                # X축은 겨울 시즌의 상대 월 순서 (11월,12월,1월 등)를 사용한다.
                months = sorted(part['month'].unique().tolist())
                months = sorted(months, key=lambda m: (int(m[5:7]) < 7, int(m[5:7])))
                for idx,label in enumerate(labels):
                    sub=part.loc[part['category'].eq(label)].set_index('month')
                    vals=[]
                    for month in months:
                        if month not in sub.index:
                            vals.append(float('nan'))
                        else:
                            point=sub.loc[month, metric]
                            if isinstance(point,pd.Series):
                                raise ValueError('같은 시즌·월·상품군의 집계가 중복됩니다.')
                            vals.append(float(point))
                    ax.plot(range(len(months)),vals,marker='o',linewidth=2,label=label if korean else f'Group {idx+1}',color=palette(idx))
                ax.set_xticks(range(len(months)),[f'{int(m[5:7])}월' if korean else m for m in months])
                a,b=months[0],months[-1]
                ax.set_title(f'{a[:4]}~{b[:4]} 겨울' if korean else f'{a} - {b}')
                ax.grid(axis='y',alpha=.22)
                if hasattr(viz,'apply_chart_border'): viz.apply_chart_border(ax)
            axes[0][0].set_ylabel(('월별 구매 기록(건)' if metric=='purchases' else '일평균 구매 기록(건/일)')
                                    if korean else ('Monthly transactions' if metric=='purchases' else 'Daily average transactions'))
            handles, legends=axes[0][-1].get_legend_handles_labels()
            fig.legend(handles,legends,loc='lower center',ncol=min(len(labels),5),bbox_to_anchor=(.5,-.01))
            fig.suptitle(('상품군별 과거 겨울 구매 추이' if category is None else f'{category} 과거 겨울 구매 추이')
                          if korean else 'Historical winter purchases')
            fig.tight_layout(rect=(0,.07,1,.95))
            destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
            fig.savefig(destination,dpi=150,bbox_inches='tight')
            return str(destination.resolve())
        finally: plt.close(fig)


def save_weekly_campaign_chart(frame, destination, *, category=None):
    """실제 기획전 날짜에 대응하는 과거 기간을 주차별로 비교한다."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from shared_analysis.product.category_mapping import CATEGORIES
    labels=[category] if category else list(CATEGORIES)
    plot=frame.loc[frame['category'].isin(labels)].copy()
    if plot.empty: raise ValueError('기획전 대응 기간의 주별 구매 기록이 없습니다.')
    korean=next((x for x in ('AppleGothic','Malgun Gothic','NanumGothic','Noto Sans CJK KR') if any(f.name==x for f in font_manager.fontManager.ttflist)),None)
    seasons=sorted(plot['season'].unique())
    # 실제 관측 일수를 기준으로 주차 라벨을 만든다. 숫자축의 1.5주차 같은 보간 눈금을 제거한다.
    def week_ticks(part):
        days = part.groupby('week_index')['observed_days'].nunique()
        if (days > 1).any():
            raise ValueError('같은 시즌의 주차별 관측 일수가 상품군마다 다릅니다.')
        counts = part.groupby('week_index')['observed_days'].first().to_dict()
        numbers = sorted(int(i) for i in counts)
        names = [f'{i}주차' + (f'({int(counts[i])}일)' if int(counts[i]) != 7 else '') for i in numbers]
        return numbers, names
    with plt.rc_context({'font.family':korean or 'DejaVu Sans','axes.unicode_minus':False}):
        fig,axes=plt.subplots(1,len(seasons),figsize=(6.2*len(seasons),5.1),squeeze=False,sharey=True)
        try:
            for ax,season in zip(axes[0],seasons):
                part=plot.loc[plot.season.eq(season)]
                for idx,label in enumerate(labels):
                    sub=part.loc[part.category.eq(label)].sort_values('week_index')
                    ax.plot(sub['week_index'],sub['purchases_per_day'],label=label if korean else f'Group {idx+1}',marker='o',linewidth=2,color=plt.get_cmap('tab10')(idx))
                ax.set_title(f'{int(part.start_year.iloc[0])}년 시작 시즌' if korean else f'Season {season+1}')
                ticks, labels_for_ticks = week_ticks(part)
                ax.set_xticks(ticks, labels_for_ticks)
                ax.set_xlim(min(ticks)-0.18, max(ticks)+0.18)
                ax.set_xlabel('기획전 대응 주차' if korean else 'Campaign-aligned week')
                ax.grid(axis='y',alpha=.2)
            axes[0][0].set_ylabel('주차별 일평균 구매 기록(건/일)' if korean else 'Transactions per day')
            handles,names=axes[0][-1].get_legend_handles_labels()
            fig.legend(handles,names,loc='lower center',ncol=min(len(labels),5))
            fig.suptitle('과거 기획전 대응 기간의 주별 구매 추이' if korean else 'Historical campaign-aligned weekly purchases')
            fig.tight_layout(rect=(0,.07,1,.95))
            destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
            fig.savefig(destination,dpi=150,bbox_inches='tight');return str(destination.resolve())
        finally:plt.close(fig)
