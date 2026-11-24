"""터미널 v2와 같은 분석 함수를 사용하는 Slack 카테고리 분석 어댑터."""
from __future__ import annotations
import re
from pathlib import Path
import pandas as pd
from shared_analysis.market.category_recommendation import musinsa_ranking_evidence
from shared_analysis.market.category_comparison import KEYWORD_CATEGORY, CATEGORY_ALIASES, combine_rows, save_charts
from agent_system.common_tools.external_data.naver_keyword_policy import apply_keyword_policy
from shared_analysis.market.category_comparison import save_monthly_category_chart
from shared_analysis.market.category_comparison_v2 import save_recent_chart  # 기존 테스트/외부 호출 호환성


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


def build_category_result(request: str, output_dir: str | Path, top_n: int = 3, *, period_plan=None) -> dict:
    scope = parse_scope(request)
    from agent_system.common_tools.local_data.category_sales import category_sales
    from agent_system.common_tools.external_data import musinsa_sample
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    from .period_context import complete_scope, period_note, scope_days, scope_text, period_table_note
    if period_plan is not None and scope['channel'] != 'online':
        raise ValueError('기간 연동 상품 선정은 온라인 채널만 지원합니다. 다른 채널로 임의 대체하지 않습니다.')
    sales = category_sales(**scope, period_plan=period_plan) if period_plan is not None else category_sales(**scope)
    before = {}
    if period_plan is not None and complete_scope(period_plan, 'before'):
        before = {r['category']: r['purchases'] for r in category_sales(**scope, period_plan=period_plan, scope='before')['rows']}
    m = musinsa_sample.load()
    # 상품군 선정에는 소분류 키워드별 네이버 지수를 조회하지 않는다.
    rows = combine_rows(sales['rows'], m['products'], m['reviews'], set())
    naver_metrics = {}
    ranking = musinsa_ranking_evidence(m['products'], CATEGORY_ALIASES)
    if period_plan is not None:
        for row in rows:
            row['hm_before_purchases'] = before.get(row['category'])
            row['hm_core_days'] = scope_days(period_plan, 'core')
            row['hm_before_days'] = scope_days(period_plan, 'before')
            row['hm_core_purchases_per_day'] = round(row['hm_purchases']/row['hm_core_days'], 2)
            row['hm_before_purchases_per_day'] = (round(row['hm_before_purchases']/row['hm_before_days'], 2)
                                                  if row['hm_before_purchases'] is not None else None)
            row['analysis_period'] = scope_text(period_plan)
    candidates = []
    for r in sorted(rows, key=lambda row: (-int(row.get('hm_purchases', 0)), row['category'])):
        if period_plan is not None and not r['hm_purchases']:
            continue
        models = int(r.get('hm_product_count') or 0)
        notes = [f"H&M 과거 핵심 월 온라인 구매 기록 {r['hm_purchases']:,}건 (20~39세, 여성용 상품)" if period_plan else
                 f"H&M 과거 겨울 온라인 구매 기록 {r['hm_purchases']:,}건 (20~39세, 여성용 상품)"]
        if models:
            notes.append(f"모델당 구매 기록 약 {r['hm_purchases']/models:,.1f}건 (상품군 크기 참고)")
        if r['musinsa_sample_products']:
            notes.append(f"무신사 해당 상품군 수집 상품 {r['musinsa_sample_products']:,}개 (판매량 아님)")
        candidates.append({**r, 'hm_purchases_per_model': round(r['hm_purchases']/models,2) if models else None,
                           'reasons': notes, 'naver': [], 'recent_positive_signal': False,
                           'musinsa_ranking': ranking.get(r['category'],{})})
    # 임시 추천 순서는 검증된 핵심 월 구매 기록 기준으로 유지한다.
    # 기획전 대응 기간은 추가 근거이며, 동일한 날짜 범위에서만 집계한다.
    if not rows or not candidates:
        raise ValueError('분석 대상 상품군/후보가 없어 결과를 생성할 수 없습니다.')
    # DB 월별 거래의 합계가 선정표와 일치할 때만 추이 그래프를 첨부한다.
    monthly = None
    weekly = None
    weekly_warning = None
    chart_warning = None
    if period_plan is not None:
        (output/'hm_category_daily_average.png').unlink(missing_ok=True)
        try:
            from .seasonal_trends import load_monthly_purchases, verify_monthly_totals
            monthly = load_monthly_purchases(period_plan)
            verify_monthly_totals(monthly, rows)
            save_monthly_category_chart(monthly, output/'hm_category_purchases.png')
            # 월별 일평균 계산값은 CSV에 남기고 중복 차트는 만들지 않는다.
            try:
                from shared_analysis.market.category_comparison import save_weekly_campaign_chart
                from .seasonal_trends import load_aligned_weekly_purchases
                weekly = load_aligned_weekly_purchases(period_plan)
                save_weekly_campaign_chart(weekly, output/'hm_category_campaign_weekly.png')
            except Exception as exc:
                weekly = None
                weekly_warning = f'기획전 대응 주별 구매 추이 표시 보류: {type(exc).__name__}: {exc}'
        except Exception as exc:
            chart_warning = f'H&M 월별 구매 추이 표시 보류: {type(exc).__name__}: {exc}'
            (output/'hm_category_purchases.png').unlink(missing_ok=True)
    else:
        chart_warning = '월별 비교 기간이 없어 구매 추이 그래프를 생성하지 않았습니다.'
    if weekly is not None:
        from .campaign_evidence import summarize_campaign_window
        try:
            window_evidence = summarize_campaign_window(weekly, rows)
        except ValueError as exc:
            weekly_warning = f'기획전 대응 기간 근거 검증 실패: {exc}'
            weekly = None
            (output/'hm_category_campaign_weekly.png').unlink(missing_ok=True)
        else:
            for row in rows:
                info = window_evidence[row['category']]
                row['hm_campaign_aligned_purchases'] = info['purchases']
                row['hm_campaign_aligned_days'] = info['observed_days']
                row['hm_campaign_aligned_per_day'] = round(info['purchases_per_day'], 2)
            for candidate in candidates:
                info = window_evidence[candidate['category']]
                candidate.update(hm_campaign_aligned_purchases=info['purchases'],
                                 hm_campaign_aligned_days=info['observed_days'],
                                 hm_campaign_aligned_per_day=round(info['purchases_per_day'], 2))
                candidate['reasons'].append(
                    f"과거 기획전 대응 기간 구매 기록 {info['purchases']:,}건 "
                    f"(2개 시즌 각 {info['days_per_season']}일, 총 {info['observed_days']}일; "
                    f"일평균 {info['purchases_per_day']:,.1f}건)")
    candidates = candidates[:top_n]
    frame = pd.DataFrame(rows)
    if monthly is not None:
        month_csv=monthly.copy();month_csv['source']='hm_monthly';month_csv['metric']='purchases'
        frame=pd.concat([frame,month_csv],ignore_index=True)
    if weekly is not None:
        week_csv=weekly.copy();week_csv['source']='hm_campaign_aligned_weekly';week_csv['metric']='purchases_per_day'
        frame=pd.concat([frame,week_csv],ignore_index=True)
    lines = ['■ 5개 상품군 비교 및 주력 상품군 추천',
             '조건: 20~39세 고객 / ' + ('온라인' if scope['channel'] == 'online' else '오프라인') +
             (' / H&M 여성용 상품 / 과거 핵심 월 거래' if period_plan else ' / H&M 여성용 상품 / 과거 12~2월 거래'),
             '고객 성별은 H&M DB에서 확인할 수 없습니다. 과거 거래 기록과 현재 외부 동향은 별도 근거입니다.',
             '', '추천 후보 (검토 순서, 확정된 시장 인기 순위 아님)']
    if period_plan is not None:
        lines.insert(2, period_note(period_plan))
        lines.insert(3, '추천 순서는 H&M 핵심 월 구매 건수 기준이며, 기획전 대응 기간의 실측 구매 기록을 별도 교차 확인합니다.')
    for i, c in enumerate(candidates, 1):
        lines.append(f'{i}. {c["category"]}')
        lines.extend('  - ' + reason for reason in c['reasons'])
        # 상품 유형별 네이버 추세·무신사 리뷰는 상품군 선택 이후에만 사용한다.
        if period_plan is not None:
            lines.append('  - ' + period_table_note(period_plan, c['hm_purchases'], before.get(c['category'])))
    if chart_warning:
        lines.append(chart_warning)
    if weekly_warning:
        lines.append(weekly_warning)
        lines.append('기획전 대응 기간의 구매 근거는 조회되지 않아 추천 순위 판단에 반영하지 않았습니다.')
    lines.extend(['', '무신사 수집 상품 수는 표본 규모이며 판매량·시장 순위를 뜻하지 않습니다.',
                  '', '■ 다음 단계 — 상품군 선택',
                  '후보 상품군을 선택하면 해당 상품군의 상품 유형별 구매 이력·외부 트렌드를 분석합니다.'])
    # CART_SIGNAL_VISUALIZATION_COPY_V1
    # 분석 원본은 audit_output에 보존하고, Slack 첨부 전용 PNG만 허용된 경로로 복사합니다.
    chart = (output / 'hm_category_purchases.png').resolve()
    slack_chart = None
    slack_attachments = []
    if chart.is_file() and chart.stat().st_size > 0:
        import hashlib
        import shutil
        # 파일 이름만 사용해 경로 이탈을 막고, 실행별 결과가 섞이지 않도록 구분합니다.
        run_key = hashlib.sha256(str(output.resolve()).encode('utf-8')).hexdigest()[:16]
        allowed_root = (Path(__file__).resolve().parents[4] / 'visualization_output').resolve()
        target_dir = (allowed_root / run_key).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = (target_dir / chart.name).resolve()
        if not target_path.is_relative_to(allowed_root):
            raise ValueError('시각화 대상 경로가 허용 폴더를 벗어났습니다.')
        shutil.copy2(chart, target_path)
        if target_path.is_file() and target_path.stat().st_size > 0:
            slack_chart = str(target_path)
        for extra_name in ('hm_category_campaign_weekly.png',):
            extra=output/extra_name
            if extra.is_file() and extra.stat().st_size>0:
                extra_target=(target_dir/extra_name).resolve()
                if not extra_target.is_relative_to(allowed_root):raise ValueError('추가 그래프 경로 이탈')
                shutil.copy2(extra,extra_target)
                slack_attachments.append(str(extra_target))
    return {'final_answer': '\n'.join(lines), 'dataframe': frame,
            'visualization_path': slack_chart, 'attachments':slack_attachments, 'stage_alignment_output':True,
            'rows': rows, 'candidates': candidates, 'naver_metrics': naver_metrics, 'monthly_purchases': monthly,
            'data_sources': ['hm', 'musinsa']}
