"""카테고리 후보 비교: 직전 시장 분석 문맥과 로컬 네이버 자료만 사용."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from shared_analysis.market import trend_analysis
from agent_system.tools import market_tools

MATCH = re.compile(r"(?:방금|앞서|위에서|직전|나온).{0,45}(?:카테고리|시장).{0,50}(?:비교|그래프|차트|선정|근거)")


def is_category_followup(question: str) -> bool:
    text = str(question or '').strip().strip('“”\"')
    return bool(MATCH.search(text)) or bool(re.search(r'카테고리\s*후보.{0,30}(비교|그래프|선정)', text))


def _source_keywords(series: pd.DataFrame, previous_answers: str) -> list[str]:
    # 이전 시장 분석에서 실제로 언급된 키워드 중 네이버 파일에 있는 것만 채택.
    keys = sorted({str(k).strip() for k in series['keyword'].dropna().unique() if str(k).strip()}, key=len, reverse=True)
    # 일반 단어·부분 문자열이 오검출되지 않도록 앞서 도출된 카테고리 목록을 우선한다.
    candidates = []
    for k in keys:
        if re.search(r'(?<![가-힣A-Za-z])'+re.escape(k)+r'(?![가-힣A-Za-z])', previous_answers) and k not in candidates:
            candidates.append(k)
    return candidates[:6]


def build_category_comparison(question: str, previous_answers: str) -> dict:
    """검증된 후보만 표시; 비교 근거가 없으면 그래프를 꾸미지 않는다."""
    from agent_system.common_tools.external_data import trend_files

    status = trend_files.naver_status()
    if not status.get('available'):
        return {'final_answer': '■ 카테고리 비교·선정 근거\n네이버 배치 데이터가 없어 비교 그래프를 만들 수 없습니다.',
                'comparison_status': 'source_unavailable'}
    series = trend_files.load_naver()
    if series is None or series.empty or not {'keyword','period','ratio'}.issubset(series.columns):
        return {'final_answer': '■ 카테고리 비교·선정 근거\n비교 가능한 네이버 시계열 열(keyword, period, ratio)이 없습니다.',
                'comparison_status': 'invalid_source'}
    candidates = _source_keywords(series, previous_answers)
    if not candidates:
        return {'final_answer': '■ 카테고리 비교·선정 근거\n직전 시장 분석에서 확인되는 후보 키워드를 찾지 못했습니다. 이전에 제시된 후보를 다시 명시해 주세요.',
                'comparison_status': 'no_candidates'}
    prepared, request_info = trend_analysis.prepare_series(series, candidates)
    prepared, unit = trend_analysis.weekly_if_daily(prepared)
    rows = []
    for candidate in candidates:
        grp = prepared.loc[prepared['keyword'].eq(candidate)].sort_values('period')
        if len(grp) < 8:
            continue
        recent, prior = grp.tail(4), grp.iloc[-8:-4]
        older = float(prior['ratio'].mean())
        if older <= 0:
            continue
        growth = float(recent['ratio'].mean()) / older - 1
        rows.append({'keyword':candidate, 'growth':growth,
                     'recent_mean':round(float(recent['ratio'].mean()),2),
                     'prior_mean':round(older,2),
                     'last_period':str(grp['period'].max().date()),
                     'request_id':str((request_info.get(candidate) or {}).get('request_id') or '')})
    rows.sort(key=lambda d:d['growth'], reverse=True)
    verified = {r['keyword'] for r in rows}
    missing = [k for k in candidates if k not in verified]
    notes = ['■ 카테고리 비교·선정 근거', '출처: 네이버 쇼핑인사이트 로컬 배치 CSV (상대 관심도, 검색 건수 아님)',
             '기준: 각 키워드의 최근 4개 관측 구간 평균과 직전 4개 구간 평균의 증감률']
    for r in rows:
        pct = f"{r['growth']*100:+.1f}%"
        notes.append(f"- {r['keyword']}: {pct} (최근 평균 {r['recent_mean']}, 직전 평균 {r['prior_mean']}; 마지막 관측 {r['last_period']})")
    if missing:
        notes.append('비교 근거 부족(시계열 8개 구간 이상 필요): '+', '.join(missing))
    notes.append('주의: 네이버 관심도는 요청별 상대지수이므로 다른 수집 요청의 절대 지수를 직접 비교하지 않습니다. 각 키워드의 자체 증가율만 비교합니다.')
    notes.append('기획전 기간의 관심 지속 여부는 별도 근거 없이는 예측 확정하지 않습니다.')
    result = {'final_answer':'\n'.join(notes), 'comparison_status':'partial' if len(rows)<2 else 'verified',
              'data_sources':['naver'], 'route':'market_analysis', 'completed_steps':[],
              'dataframe':pd.DataFrame(rows)}
    if len(rows)>=2:
        # 성장률 비교는 동일한 상대지수 기반의 개별 증가율만 사용
        try:
            from agent_system.tools.visualization_tools import create_bar_chart
            chart_frame = pd.DataFrame({'keyword':[r['keyword'] for r in rows],
                                        'growth_percent':[round(r['growth']*100,2) for r in rows]})
            chart = create_bar_chart(df=chart_frame, x_column='keyword', y_column='growth_percent',
                                     title='카테고리별 최근 관심도 증감률', x_label='카테고리', y_label='증감률 (%)')
            p = chart.get('file_path') if isinstance(chart,dict) else None
            if chart.get('success') and p and Path(p).is_file():
                result.update(visualization_path=str(p),visualization_result=chart,
                              completed_steps=['visualization'])
            else:
                result['final_answer'] += '\n[시각화 미완료] 데이터 비교는 가능하지만 그래프 파일 생성이 실패했습니다.'
        except Exception as exc:
            result['final_answer'] += f'\n[시각화 미완료] {type(exc).__name__}: {exc}'
    elif len(rows)==1:
        result['final_answer'] += '\n[비교 그래프 미생성] 실측으로 검증된 후보가 1개라 카테고리 간 비교 그래프를 만들 수 없습니다.'
    else:
        result['final_answer'] += '\n[비교 그래프 미생성] 근거가 충족된 후보가 없습니다.'
    return result
