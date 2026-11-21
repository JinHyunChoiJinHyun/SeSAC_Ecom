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


def save_charts(rows, naver, out):
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
    ax.set_title('H&M past winter / online / ages 20–39 / womenswear')
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
