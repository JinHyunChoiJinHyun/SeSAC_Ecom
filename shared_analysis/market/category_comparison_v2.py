"""기존 v1 비교 자료와 실제 배치를 사용해 근거 중심 2차 후보 보고서 출력."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd
from shared_analysis.market.category_recommendation import summarize_naver, musinsa_ranking_evidence, recommend_with_evidence
from shared_analysis.market.category_comparison import KEYWORD_CATEGORY, CATEGORY_ALIASES, combine_rows, save_charts


def save_recent_chart(naver, metrics, output):
    from shared_analysis.market import trend_analysis
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if any(f.name == 'AppleGothic' for f in font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = 'AppleGothic'
    plt.rcParams['axes.unicode_minus'] = False
    if not metrics:
        return
    series, _ = trend_analysis.prepare_series(naver, list(metrics))
    weekly, _ = trend_analysis.weekly_if_daily(series)
    if weekly.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    for keyword, grp in weekly.groupby('keyword'):
        tail = grp.sort_values('period').tail(8)
        ax.plot(tail['period'], tail['ratio'], marker='o', label=keyword)
    ax.set_title('Recent 8 complete weeks - Naver relative interest')
    ax.set_ylabel('Relative interest (within each request)')
    ax.legend(); fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(output / 'naver_recent_8weeks.png', dpi=160)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='audit_output/market_category_comparison_v2')
    p.add_argument('--top', type=int, default=3)
    args = p.parse_args()
    from agent_system.common_tools.local_data.category_sales import category_sales
    from agent_system.common_tools.external_data import musinsa_sample, trend_files
    output = Path(args.out); output.mkdir(parents=True, exist_ok=True)
    sales = category_sales(age_min=20, age_max=39, channel='online', winter_only=True)
    m = musinsa_sample.load()
    naver = trend_files.load_naver()
    keywords = set(naver['keyword'].dropna().astype(str)) if not naver.empty else set()
    rows = combine_rows(sales['rows'], m['products'], m['reviews'], keywords)
    nm = summarize_naver(naver, rows, KEYWORD_CATEGORY)
    mr = musinsa_ranking_evidence(m['products'], CATEGORY_ALIASES)
    recommended = recommend_with_evidence(rows, nm, mr, args.top)
    payload = {'filters': {'age': '20~39 (저장된 age)', 'sales_channel_id': 2,
                            'merchandise': 'Ladieswear/Divided', 'months': [12,1,2],
                            'customer_gender': '미확인'},
               'hm_as_of': sales.get('data_as_of'), 'musinsa_collected_at': m.get('collected_at'),
               'rows': rows, 'naver_metrics': nm, 'musinsa_ranking_evidence': mr,
               'evidence_based_candidates': recommended,
               'recommendation_policy': '상승 근거 확인된 카테고리 우선 검토 → H&M 과거 거래 기록 순. 통합 시장 순위/예측값 아님.',
               'notes': ['네이버 서로 다른 요청의 상대값 직접 비교 금지',
                         '무신사 랭킹은 카테고리 내 순위이고 수집 리뷰수는 판매량 아님',
                         'H&M 고객 성별은 확인 불가',
                         '최근 상승 신호는 기획전 기간 지속을 보장하지 않음']}
    (output/'comparison_v2.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    pd.DataFrame(rows).to_csv(output/'comparison_v2.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([{'category': q['category'], 'hm_purchases': q['hm_purchases'],
                   'hm_purchases_per_model': q['hm_purchases_per_model'],
                   'recent_positive_signal': q['recent_positive_signal']} for q in recommended]).to_csv(
                       output/'recommended_candidates.csv', index=False, encoding='utf-8-sig')
    save_charts(rows, naver, output)
    save_recent_chart(naver, nm, output)
    print('\n[후보 2~3개: 근거 기반 검토 순서 (시장 점유율 순위 아님)]')
    for rank, c in enumerate(recommended, 1):
        print(f'{rank}. {c["category"]} / 최근 관심 상승 신호: {c["recent_positive_signal"]}')
        for reason in c['reasons']:
            print('  - ' + reason)
    print('\n[네이버 키워드별 최근 변화]')
    for keyword, entry in nm.items():
        print(keyword, entry.get('growth_percent'), entry.get('judgement'), entry.get('recent_window'))
    print('\n결과:', output)

if __name__ == '__main__': main()
