"""대분류 확정 후 저장된 추천 후보로 Slack 근거표와 차트를 만든다."""
from pathlib import Path
import re
import pandas as pd


def build_confirmation_evidence(run):
    options = run.get('subtype_options') or []
    from .period_context import period_note, purchase_label
    plan = run.get('period_plan')
    label = '과거핵심월온라인구매(건)' if plan else '과거겨울온라인구매(건)'
    rows = []
    for rank, o in enumerate(options, 1):
        total = int(o.get('purchases') or 0)
        for color in o.get('colors') or []:
            count = int(color['purchases'])
            rows.append({'추천순서': rank, '세부유형': o['keyword'],
                         '네이버관심도변화(%)': round(o['growth'] * 100, 2) if o.get('growth') is not None else None,
                         label: total, '색상': color['name'],
                         '색상구매(건)': count, '유형내색상비중(%)': round(100 * count / total, 1) if total else 0})
    frame = pd.DataFrame(rows, columns=['추천순서','세부유형','네이버관심도변화(%)',
                                       label,'색상','색상구매(건)','유형내색상비중(%)'])
    if not options:
        return frame, '조건을 함께 충족한 세부 유형이 없어 추천을 보류합니다.'
    if plan and not frame.empty:
        frame['사전월유형구매(건)'] = [options[i - 1].get('before_purchases') for i in frame['추천순서']]
        frame['핵심월기간'] = options[0].get('period_description')
    first = options[0]
    colors = ', '.join(f"{c['name']} {c['purchases']:,}건" for c in first.get('colors', [])[:5])
    market = [o for o in options if o.get('growth') is not None]
    growth_note = (f"- 네이버 최근 상대 관심도 변화: {first['growth']:+.2%}\n"
                   if first.get('growth') is not None else '- 네이버 상승 근거: 미확보\n')
    summary = ('■ 세부 유형 추천 근거 요약\n'
               f"- 시장·내부 근거 검증 후보 {len(market)}개 / H&M 구매 참고 후보 {len(options) - len(market)}개\n"
               f"- 추천 유형: {first['keyword']} (확보된 근거 수준별 검토)\n"
               + growth_note
               + f"- H&M {purchase_label(plan)}: {first['purchases']:,}건\n"
               + f"- 주요 색상별 구매: {colors}\n"
               + '- 판단: 네이버 상승 근거와 H&M 구매 기록을 구분해 순위 표시\n'
               + '- 주의: 검색 관심도는 현재 판매량이 아니며, 유형 매칭은 상품 설명 규칙 기반입니다.\n'
               + '후보 근거표(CSV)와 구매 그래프를 첨부했습니다.\n'
               + '세부 유형 하나 및 색상 1~3개를 채팅으로 선택하세요. 예: 경량패딩 블랙, 그린 카키')
    if plan:
        summary += '\n' + period_note(plan)
    if first.get('recent_window'):
        summary += f"\n- 비교 기간: {first.get('prior_window') or '미확인'} → {first['recent_window']}"
    return frame, summary


def build_confirmation_chart(run, path):
    options = run.get('subtype_options') or []
    if not options or not options[0].get('colors'):
        return None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    first = options[0]
    colors = first['colors'][:8]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    try:
        labels = [c['name'] for c in colors][::-1]
        counts = [c['purchases'] for c in colors][::-1]
        axes[0].barh(labels, counts)
        axes[0].set_title('H&M core-month online purchases by colour' if run.get('period_plan') else 'H&M historical winter online purchases by colour')
        axes[0].set_xlabel('Historical purchase records')
        for i, v in enumerate(counts):
            axes[0].annotate(f'{v:,}', (v, i), xytext=(4, 0), textcoords='offset points', va='center')
        axes[0].margins(x=.2)
        # 서로 다른 네이버 검색 요청의 정규화 관심도 수치가 아닌 키워드별 변화율만 표시한다.
        shown = [o for o in options if o.get('growth') is not None][:6]
        growth = [o['growth'] * 100 for o in shown]
        axes[1].bar(range(len(shown)), growth)
        axes[1].set_xticks(range(len(shown)), [f'Candidate {i+1}' for i in range(len(shown))])
        axes[1].set_title('Naver relative interest changes per keyword')
        axes[1].set_ylabel('Change (%)')
        axes[1].axhline(10, linestyle='--', linewidth=1)
        for i, v in enumerate(growth):
            axes[1].annotate(f'{v:+.1f}%', (i, v), xytext=(0, 4), textcoords='offset points', ha='center')
        fig.suptitle('Historical H&M transactions vs Naver relative interest: different units')
        fig.tight_layout(rect=(0, 0, 1, .94))
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=140)
        return str(path.resolve())
    finally:
        plt.close(fig)


def enrich_confirmation_result(result, run):
    if (run.get('status') != 'waiting_input' or run.get('waiting_key') != 'subtype_colors'
            or not run.get('category_confirmed') or not run.get('subtype_flow')):
        return result
    frame, description = build_confirmation_evidence(run)
    out = dict(result, workflow_display=True)
    out['final_answer'] = str(out.get('final_answer') or '') + '\n\n' + description
    if not frame.empty:
        out['dataframe'] = frame
        safe = re.sub(r'[^A-Za-z0-9_-]', '_', str(run.get('run_id') or 'unknown'))
        path = Path(__file__).resolve().parents[4] / 'visualization_output' / 'category_confirmation' / safe / 'subtype_evidence.png'
        try:
            out['visualization_path'] = build_confirmation_chart(run, path)
        except Exception as exc:
            out['final_answer'] += f'\n[시각화 실패] 표는 보존됩니다. ({type(exc).__name__})'
    out['data_sources'] = ['hm', 'naver']
    return out
