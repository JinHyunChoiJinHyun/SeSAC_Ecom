"""대분류 확정 후 저장된 추천 후보로 Slack 근거표와 차트를 만든다."""
from pathlib import Path
import re
import pandas as pd


def _korean_font_context():
    """설치된 한글 폰트를 개별 차트에만 지정해 다른 Agent 그래프에 영향이 없도록 한다."""
    import matplotlib
    from matplotlib import font_manager
    candidates = ('AppleGothic', 'Apple SD Gothic Neo', 'NanumGothic',
                  'Malgun Gothic', '맑은 고딕', 'Noto Sans CJK KR',
                  'Noto Sans KR', 'Noto Sans KR UI', 'UnDotum')
    installed = {f.name for f in font_manager.fontManager.ttflist}
    found = next((name for name in candidates if name in installed), None)
    if found:
        return matplotlib.rc_context({'font.family': found, 'axes.unicode_minus': False})
    # 없는 글꼴 이름을 지정하면 fallback과 한글 깨짐 경고가 반복된다.
    # 폰트가 전혀 없는 환경에서는 경고를 유지해 누락을 숨기지 않는다.
    import logging
    logging.getLogger(__name__).warning('KOREAN_CHART_FONT_UNAVAILABLE 한글 폰트 설치 필요')
    return matplotlib.rc_context({'axes.unicode_minus': False})


def _top_colors(option, limit=5):
    """저장된 유형별 색상 구매 기록에서 구매량 상위 색상만 추린다."""
    raw = option.get('colors') or []
    ranked = sorted((item for item in raw if isinstance(item, dict) and str(item.get('name') or '').strip()),
                    key=lambda item: (-int(item.get('purchases') or 0), str(item.get('name') or '')))
    return ranked[:limit]


def build_confirmation_evidence(run):
    """상품군 승인 후에는 유형별 비교만 제공하고 색상은 후속 단계로 미룬다."""
    options = run.get('subtype_options') or []
    from .period_context import period_note, purchase_label
    plan = run.get('period_plan')
    label = '과거핵심월온라인구매(건)' if plan else '과거겨울온라인구매(건)'
    rows = []
    for rank, option in enumerate(options, 1):
        growth = option.get('growth')
        market = option.get('musinsa_evidence') or {}
        rows.append({
            '추천순서': rank, '세부유형': option['keyword'],
            '네이버관심도변화(%)': round(growth * 100, 2) if growth is not None else None,
            label: int(option.get('purchases') or 0),
            '무신사관련상품(잠정)': (int(market.get('related_products') or 0)
                           if market.get('status') == 'observed' else None),
            '무신사연결리뷰(건)': (int(market.get('related_reviews') or 0)
                           if market.get('status') == 'observed' else None),
            '근거구분': '네이버 상승 확인' if growth is not None and growth >= .1
                      else ('네이버 상승 미확인' if growth is not None else '네이버 미수집'),
            '구매상위색상5개': ' · '.join(
                f"{c['name']} {int(c.get('purchases') or 0):,}건" for c in _top_colors(option)),
        })
    frame = pd.DataFrame(rows)
    if not options:
        return frame, '추천 보류: H&M 구매 기록으로 검증된 상품 유형 후보가 없습니다.'
    if plan and not frame.empty:
        frame['사전월유형구매(건)'] = [o.get('before_purchases') for o in options]
        frame['핵심월기간'] = options[0].get('period_description')
    rising = [o for o in options if o.get('growth') is not None and o['growth'] >= .1]
    first = rising[0] if rising else options[0]
    summary = ('■ 상품 유형 비교 결과\n'
               f'- 네이버 상승 신호 및 H&M 구매 확인 후보: {len(rising)}개 / 기타 참고 후보: {len(options) - len(rising)}개\n'
               f'- 우선 검토 유형: {first["keyword"]} (확인된 근거 수준별 후보이며, 자동 확정 아님)\n'
               f'- H&M {purchase_label(plan)}: {int(first.get("purchases") or 0):,}건\n'
               '- 네이버 변화율은 키워드별 독립 정규화 지수의 기간 내 변화입니다. 키워드 사이 검색량 순위가 아닙니다.\n'
               '- 무신사 상품명·리뷰는 참고 표본이며 판매량을 뜻하지 않습니다.\n'
               '- 유형은 상품 설명 규칙 기반이고 서로 중복될 수 있습니다.\n'
               '상품 유형별 비교표(CSV)에 각 유형의 구매 상위 색상 5개를 함께 표시했습니다.\n'
               '상위 색상은 상품 유형 선택을 돕는 참고 정보이며, 색상은 선택한 유형의 상세 그래프를 확인한 뒤 1~3개 결정합니다.\n'
               '다른 유형을 더 분석할 수 있습니다. 유형과 색상 1~3개를 함께 선택하세요. 예: 경량패딩 블랙이랑 그린카키로 할게.')
    top_color_lines = []
    for option in options:
        colors = _top_colors(option)
        if colors:
            top_color_lines.append(f"- {option['keyword']}: " + ' · '.join(
                f"{c['name']} {int(c.get('purchases') or 0):,}건" for c in colors))
        else:
            top_color_lines.append(f"- {option['keyword']}: 색상별 구매 근거 미확보")
    summary += '\n■ 유형별 구매 상위 색상(최대 5개)\n' + '\n'.join(top_color_lines)
    if plan:
        summary += '\n' + period_note(plan)
    return frame, summary


def build_confirmation_chart(run, path):
    """색상 대신 주력 상품 유형의 H&M 구매 규모와 네이버 변화율을 비교한다."""
    options = run.get('subtype_options') or []
    if not options:
        return None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    with _korean_font_context():
        return _render_confirmation_chart(run, path, options, plt)


def _render_confirmation_chart(run, path, options, plt):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    try:
        purchase_order = sorted(options, key=lambda o: int(o.get('purchases') or 0))
        names = [o['keyword'] for o in purchase_order]
        counts = [int(o.get('purchases') or 0) for o in purchase_order]
        axes[0].barh(names, counts)
        axes[0].set_title('H&M 유형별 과거 핵심 월 구매 기록' if run.get('period_plan')
                          else 'H&M 유형별 과거 겨울 구매 기록')
        axes[0].set_xlabel('구매 기록 (건)')
        axes[0].margins(x=.24)
        for i, count in enumerate(counts):
            axes[0].annotate(f'{count:,}', (count, i), xytext=(4, 0),
                             textcoords='offset points', va='center')
        observed = [o for o in options if o.get('growth') is not None]
        observed = sorted(observed, key=lambda o: o['growth'])
        if observed:
            keyword_names = [o['keyword'] for o in observed]
            growth = [o['growth'] * 100 for o in observed]
            axes[1].barh(keyword_names, growth)
            axes[1].axvline(0, linewidth=1)
            axes[1].set_title('네이버 유형별 검색 관심도 기간 변화')
            axes[1].set_xlabel('각 키워드의 상대 변화율 (%)')
            lower, upper = min(growth), max(growth)
            margin = max(15, (upper-lower)*.15)
            axes[1].set_xlim(min(0, lower)-margin, max(0, upper)+margin)
            for i, value in enumerate(growth):
                axes[1].annotate(f'{value:+.1f}%', (value, i),
                                 xytext=(4 if value >= 0 else -4, 0), textcoords='offset points',
                                 va='center', ha='left' if value >= 0 else 'right')
        else:
            axes[1].text(.5, .5, '네이버 키워드 변화율 자료 없음',
                         transform=axes[1].transAxes, ha='center', va='center')
            axes[1].set_axis_off()
        fig.suptitle('상품 유형 비교: H&M 구매 기록과 네이버 상대 관심도는 서로 다른 지표')
        fig.tight_layout(rect=(0, 0, 1, .94))
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=140)
        return str(path.resolve())
    finally:
        plt.close(fig)



def build_selected_color_evidence(run):
    """선택한 유형 한 가지에 대해서만 색상 구매 기록을 제공한다."""
    selected = str(run.get('selected_subtype') or '')
    option = next((o for o in run.get('subtype_options') or [] if o.get('keyword') == selected), None)
    if not option:
        return pd.DataFrame(), '선택한 유형의 검증 근거를 찾지 못했습니다.'
    total = int(option.get('purchases') or 0)
    rows = [dict(상품유형=selected, 색상=c['name'], 색상구매건수=int(c['purchases']),
                 유형내비중=round(100 * int(c['purchases']) / total, 2) if total else 0,
                 거래확인상품수=int(c.get('article_count') or 0))
            for c in option.get('colors') or []]
    frame = pd.DataFrame(rows, columns=['상품유형','색상','색상구매건수','유형내비중','거래확인상품수'])
    summary = (f'■ {selected} 색상별 과거 구매 근거\n'
               f'- 해당 유형 H&M 과거 핵심 월 구매 기록: {total:,}건 (유형 중복 매칭 가능)\n'
               '- 색상별 구매 건수는 실제 확인된 상품의 과거 기록이며 현재 인기·재고가 아닙니다.\n'
               '- 아직 선택·승인되지 않았습니다. 다른 유형도 계속 분석할 수 있습니다.\n'
               '- 선택 시 유형 1개와 색상 1~3개를 함께 입력하세요. 예: 경량패딩 블랙이랑 그린카키로 할게')
    return frame, summary


def build_selected_color_chart(run, path):
    """유형 선택이 끝난 후에만 해당 유형 색상 비교를 그린다."""
    selected = str(run.get('selected_subtype') or '')
    option = next((o for o in run.get('subtype_options') or [] if o.get('keyword') == selected), None)
    if not option or not option.get('colors'):
        return None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    top = option['colors'][:10]
    with _korean_font_context():
        return _render_selected_color_chart(selected, path, top, plt)


def _render_selected_color_chart(selected, path, top, plt):
    fig, ax = plt.subplots(figsize=(9, 5))
    try:
        names = [c['name'] for c in top][::-1]
        counts = [int(c['purchases']) for c in top][::-1]
        ax.barh(names, counts)
        ax.set_title(f'{selected}: H&M 색상별 과거 겨울 구매 기록')
        ax.set_xlabel('구매 기록 (건)')
        ax.margins(x=.20)
        for i, n in enumerate(counts):
            ax.annotate(f'{n:,}', (n, i), xytext=(4, 0), textcoords='offset points', va='center')
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=140)
        return str(path.resolve())
    finally:
        plt.close(fig)


def is_subtype_replay_request(question):
    """상품 유형 비교를 재표시하는 명시적 명령. 승인/선택으로 해석하지 않는다."""
    s = str(question or '').strip().rstrip(' .!?~')
    return bool(re.fullmatch(
        r'(?:주력\s*상품\s*유형|상품\s*유형|세부\s*유형|소분류)\s*'
        r'(?:분석|비교|추천)(?:\s*결과)?\s*'
        r'(?:다시\s*(?:보여\s*줘|보여주세요|분석해\s*줘|분석해줘|해\s*줘|해주세요)|보여\s*줘)', s))


def enrich_confirmation_result(result, run, *, mode="auto"):
    if (run.get('status') != 'waiting_input' or run.get('waiting_key') != 'subtype_colors'
            or not run.get('category_confirmed') or not run.get('subtype_flow')):
        return result
    from .subtype_selection import recover_display_options
    old_options = run.get('subtype_options') or []
    run = {**run, 'subtype_options': recover_display_options(run.get('review_category') or run.get('selected_category') or run.get('category_confirmed'), run.get('subtype_options') or [])}
    recovered_keywords = [(o['keyword'], o['growth']) for old, o in zip(old_options, run['subtype_options'])
                          if old.get('growth') is None and o.get('growth') is not None]
    selected = bool(run.get('selected_subtype')) and not run.get('selected_colors')
    color_mode = (mode == 'colors' or (mode == 'auto' and selected))
    frame, description = (build_selected_color_evidence(run) if color_mode
                          else build_confirmation_evidence(run))
    out = dict(result, workflow_display=True)
    out['final_answer'] = str(out.get('final_answer') or '') + '\n\n' + description
    if recovered_keywords:
        # 데이터 복원 내역은 사용자용 리포트가 아니라 운영 로그에만 기록한다.
        import logging
        logging.getLogger(__name__).info(
            'NAVER_KEYWORD_GROWTH_RECOVERED category=%s keywords=%s',
            run.get('review_category') or run.get('selected_category') or run.get('category_confirmed'),
            ', '.join(str(name) for name, _ in recovered_keywords),
        )
    if not frame.empty:
        out['dataframe'] = frame
        safe = re.sub(r'[^A-Za-z0-9_-]', '_', str(run.get('run_id') or 'unknown'))
        filename = 'selected_subtype_colors.png' if color_mode else 'subtype_evidence.png'
        path = Path(__file__).resolve().parents[4] / 'visualization_output' / 'category_confirmation' / safe / filename
        try:
            out['visualization_path'] = (build_selected_color_chart(run, path) if color_mode
                                         else build_confirmation_chart(run, path))
        except Exception as exc:
            out['final_answer'] += f'\n[시각화 실패] 표는 보존됩니다. ({type(exc).__name__})'
    out['data_sources'] = ['hm', 'naver']
    return out
