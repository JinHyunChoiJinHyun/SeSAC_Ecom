"""10단계 실제 검증 후보 선택 및 테마 저장 보호."""
import re


def validated_updates(run, category, ids=None):
    if category != run.get('review_category'):
        raise ValueError('현재 검토 카테고리와 일치하지 않습니다. 먼저 카테고리 분석을 실행하세요.')
    verified = list(dict.fromkeys(str(x) for x in (run.get('verified_candidate_ids') or [])))
    if not verified or any(not re.fullmatch(r'\d{10}', v) for v in verified):
        raise ValueError('검증된 상품 후보가 없습니다. 카테고리 재분석부터 진행하세요.')
    chosen = verified[:5] if ids is None else list(dict.fromkeys(str(x) for x in ids))
    if not chosen or len(chosen)>5 or not set(chosen).issubset(verified):
        raise ValueError('현재 카테고리에서 검증된 상품 ID만 선택할 수 있습니다.')
    plan = run.get('period_plan')
    period_updates = {}
    if plan:
        from agent_system.orchestrator.tools.category_planning.period_context import scope_text, run_schedule
        rows_by_id = {str(r['article_id']): r for r in run.get('verified_candidate_rows') or []}
        if any(rows_by_id.get(i, {}).get('analysis_period') != scope_text(plan) for i in chosen):
            raise ValueError('승인 후보의 과거 분석 기간이 현재 Run과 일치하지 않습니다.')
        period_updates['schedule'] = run_schedule(run)
    market = run.get('category_market') or {}
    theme = market.get('theme_candidate') if market.get('category') == category else None
    if run.get('subtype_flow'):
        subtype = run.get('selected_subtype')
        colors = set(run.get('selected_colors') or [])
        rows = {str(r['article_id']):r for r in run.get('verified_candidate_rows') or []}
        if not subtype or not colors or any(i not in rows or rows[i].get('selected_subtype') != subtype or not rows[i].get('keyword_match_verified') or rows[i].get('suitability_status', 'rule_matched') != 'rule_matched' or rows[i].get('colour_group_name') not in colors for i in chosen):
            raise ValueError('세부 유형·색상 근거가 검증된 상품만 확정할 수 있습니다.')
        if {rows[i]['colour_group_name'] for i in chosen} != colors:
            raise ValueError('선택한 모든 색상의 상품을 포함해야 합니다.')
        return {'category':category,'products':', '.join(chosen),'theme':subtype,'subtype':subtype,'colors':', '.join(run['selected_colors']), **period_updates}
    return {'category':category,'products':', '.join(chosen),'theme':str(theme or '미확정 (추가 검토 필요)'), **period_updates}


def selected_ids(text):
    """명시적인 상품 확정 요청만 인식. 일반 분석·다른 질문은 기존 라우팅 유지."""
    s=str(text or '').strip()
    match = re.fullmatch(r'(?:주력\s*)?상품(?:은|을|이)?\s*(?:article_id\s*=?\s*)?(\d+(?:\s*[,，]\s*\d+)*)\s*(?:로|으로)?\s*(?:확정|선택|승인)(?:해\s*줘|해\s*주세요|하자|할게)?[.!~]*', s, flags=re.I)
    return re.findall(r'\d+', match.group(1)) if match else None


def verify_requested_updates(run, updates):
    ids = re.findall(r'(?<!\d)\d+(?!\d)',str(updates.get('products') or ''))
    return validated_updates(run, str(updates.get('category') or run.get('review_category') or ''), ids)


# CART_SIGNAL_NATURAL_CATEGORY_APPROVAL_V1
def selected_category(text):
    """명시적인 카테고리 확정 요청만 인식합니다."""
    from agent_system.orchestrator.tools.category_planning.review_selection import CATEGORIES

    s = str(text or "").strip().rstrip(".!?~ ")

    for category in CATEGORIES:
        pattern = (
            re.escape(category)
            + r"(?:\s*(?:으로|로|을|를|은|는)?\s*"
            + r"(?:결정|확정|선택|진행)"
            + r"(?:할게요|할게|하자|하겠습니다|"
            + r"해\s*줘|해\s*주세요|해주세요|"
            + r"합니다|할래요|할래)?)?"
        )

        if re.fullmatch(pattern, s):
            return category

    return None
