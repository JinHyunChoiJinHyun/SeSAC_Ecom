"""10단계: 검증된 상품 후보와 마케터 선택을 승인 화면으로 요약한다."""
from agent_system.agents.validation_agent import validated_updates


def build_step10_request(run):
    """검증 게이트와 동일한 선택 집합을 재검증하고 단일 승인 요청을 반환한다."""
    category = str(run.get('review_category') or '').strip()
    colors = list(run.get('selected_colors') or [])
    if run.get('subtype_flow') and not 1 <= len(colors) <= 3:
        raise ValueError('색상은 1~3개를 선택해야 합니다.')

    updates = validated_updates(run, category)
    # 내부 products 값은 기존 ID 검증용으로 유지하되, 판매 SKU로 승인하지 않는다.
    run["reference_product_role"] = "historical_reference"
    ids = [item.strip() for item in updates['products'].split(',') if item.strip()]
    rows = {str(row.get('article_id')): row for row in run.get('verified_candidate_rows') or []}
    if not ids or any(article_id not in rows for article_id in ids):
        raise ValueError('승인할 실제 H&M 후보 상품 상세 근거가 부족합니다.')

    from .period_context import period_note, purchase_label
    plan = run.get("period_plan")
    basis = purchase_label(plan)
    lines = [
        '■ 과거 H&M 판매 참고 사례 확인 및 상품 구성 방향 검토',
        f'대분류: {category}',
        f'소분류: {updates.get("subtype") or updates.get("theme") or "미확정"}',
        f'선택 색상 ({len(colors)}개): {", ".join(colors)}' if run.get('subtype_flow') else '색상: 상품별 검증 내역 참고',
        '과거 판매 참고 상품 (실제 판매 편성 상품 아님):',
    ]
    for i, article_id in enumerate(ids, 1):
        row = rows[article_id]
        count = row.get('winter_online_purchases')
        count_note = f' / {basis} {int(count):,}건' if count is not None else ''
        lines.append(f'{i}. {row.get("prod_name") or "상품명 확인 필요"} / ID {article_id} / 색상 {row.get("colour_group_name") or "확인 필요"}{count_note}')
    lines.extend([
        f'분석 기준: 선택한 상품 유형·색상과 과거 H&M {basis} 기록.',
        '승인 내용: 이 사례를 근거로 상품 종류·색상 방향을 검토합니다. 과거 상품 ID를 실제 판매 상품으로 확정하지 않습니다.',
        '소재·디자인 특징은 이후 소구점 단계에서 편성 조건으로 별도 선택합니다.',
        *([period_note(plan)] if plan else []),
        '참고: 상품 유형은 상품명·설명 규칙 기반이며 현재 재고나 소재·기능 성능을 보장하지 않습니다.',
        # Slack이 승인·반려 버튼을 별도로 렌더링하므로 본문에 질문을 반복하지 않는다.
    ])
    return {
        'title': '과거 사례 확인 및 기획전 상품 방향 승인',
        'question': '\n'.join(lines),
        'updates': updates,
        'source': 'user',
    }
