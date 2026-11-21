"""터미널 v2와 같은 분석 함수를 사용하는 Slack 카테고리 분석 어댑터."""
from __future__ import annotations
import re
from pathlib import Path
import pandas as pd
from market_category_recommendation import summarize_naver, musinsa_ranking_evidence, recommend_with_evidence
from run_market_category_compare import KEYWORD_CATEGORY, CATEGORY_ALIASES, combine_rows, save_charts
from run_market_category_compare_v2 import save_recent_chart


def parse_scope(request: str) -> dict:
    """지원하지 않는 고객조건은 묵시적으로 20~39세로 바꾸지 않는다."""
    txt = str(request or '')
    if not re.search(r'20\s*[·,~/~-]\s*30\s*대|20\s*~\s*39\s*세|20\s*30\s*대|2030\s*대|20대\s*(?:와|및|,|·|/|~)\s*30대', txt):
        raise ValueError('현재 연결된 카테고리 분석은 20~39세 조건만 검증됐습니다. 요청한 연령 필터를 임의 대체하지 않습니다.')
    if '온라인' in txt and '오프라인' in txt:
        raise ValueError('온라인·오프라인 혼합 요청은 아직 연결되지 않았습니다.')
    if '온라인' in txt:
        channel = 'online'
    elif '오프라인' in txt:
        channel = 'offline'
    else:
        raise ValueError('요청에 온라인/오프라인 채널 조건을 명시해 주세요.')
    return {'age_min': 20, 'age_max': 39, 'channel': channel, 'winter_only': True}


def build_category_result(request: str, output_dir: str | Path, top_n: int = 3) -> dict:
    from agent_system.common_tools.local_data.category_sales import category_sales
    from agent_system.common_tools.external_data import musinsa_sample, trend_files

    scope = parse_scope(request)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    sales = category_sales(**scope)
    m = musinsa_sample.load()
    naver = trend_files.load_naver()
    keywords = set(naver['keyword'].dropna().astype(str)) if not naver.empty else set()
    rows = combine_rows(sales['rows'], m['products'], m['reviews'], keywords)
    naver_metrics = summarize_naver(naver, rows, KEYWORD_CATEGORY)
    ranking = musinsa_ranking_evidence(m['products'], CATEGORY_ALIASES)
    candidates = recommend_with_evidence(rows, naver_metrics, ranking, top_n)
    if not rows or not candidates:
        raise ValueError('분석 대상 카테고리/후보가 없어 결과를 생성할 수 없습니다.')
    save_charts(rows, naver, output)
    save_recent_chart(naver, naver_metrics, output)
    frame = pd.DataFrame(rows)
    lines = ['■ 5개 카테고리 비교 및 주력 후보',
             '조건: 20~39세 고객 / ' + ('온라인' if scope['channel'] == 'online' else '오프라인') +
             ' / H&M 여성용 상품 / 과거 12~2월 거래',
             '고객 성별은 H&M DB에서 확인할 수 없습니다. 과거 거래 기록과 현재 외부 동향은 별도 근거입니다.',
             '', '추천 후보 (검토 순서, 확정된 시장 인기 순위 아님)']
    for i, c in enumerate(candidates, 1):
        lines.append(f'{i}. {c["category"]}')
        lines.extend('  - ' + reason for reason in c['reasons'])
    lines.extend(['', '네이버 미수집 카테고리는 관심 부족이 아니라 데이터 없음입니다.',
                  '무신사 수집 리뷰 수·랭킹은 판매량이 아닙니다.',
                  '최근 상승은 기획전 기간 지속을 보장하지 않습니다.',
                  '', '■ 카테고리 선택',
                  '후보를 보고 카테고리 하나를 선택해 주세요. 확정 전에는 상품 심층 분석으로 넘어가지 않습니다.'])
    # CART_SIGNAL_VISUALIZATION_COPY_V1
    # 분석 원본은 audit_output에 보존하고, Slack 첨부 전용 PNG만 허용된 경로로 복사합니다.
    chart = (output / 'hm_category_purchases.png').resolve()
    slack_chart = None
    if chart.is_file() and chart.stat().st_size > 0:
        import hashlib
        import shutil
        # 파일 이름만 사용해 경로 이탈을 막고, 실행별 결과가 섞이지 않도록 구분합니다.
        run_key = hashlib.sha256(str(output.resolve()).encode('utf-8')).hexdigest()[:16]
        allowed_root = (Path(__file__).resolve().parent / 'visualization_output').resolve()
        target_dir = (allowed_root / run_key).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = (target_dir / chart.name).resolve()
        if not target_path.is_relative_to(allowed_root):
            raise ValueError('시각화 대상 경로가 허용 폴더를 벗어났습니다.')
        shutil.copy2(chart, target_path)
        if target_path.is_file() and target_path.stat().st_size > 0:
            slack_chart = str(target_path)
    return {'final_answer': '\n'.join(lines), 'dataframe': frame,
            'visualization_path': slack_chart,
            'rows': rows, 'candidates': candidates, 'naver_metrics': naver_metrics}
