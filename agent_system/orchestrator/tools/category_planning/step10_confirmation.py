"""10단계: 검증된 상품 후보와 마케터 선택을 승인 화면으로 요약한다."""
from agent_system.orchestrator.tools.category_approval import validated_updates


def build_step10_request(run):
    """검증 게이트와 동일한 선택 집합을 재검증하고 단일 승인 요청을 반환한다."""
    category = str(run.get('review_category') or '').strip()
    colors = list(run.get('selected_colors') or [])
    if run.get('subtype_flow') and not 1 <= len(colors) <= 3:
        raise ValueError('색상은 1~3개를 선택해야 합니다.')

    updates = validated_updates(run, category)
    ids = [item.strip() for item in updates['products'].split(',') if item.strip()]
    rows = {str(row.get('article_id')): row for row in run.get('verified_candidate_rows') or []}
    if not ids or any(article_id not in rows for article_id in ids):
        raise ValueError('승인할 실제 H&M 후보 상품 상세 근거가 부족합니다.')

    lines = [
        '■ 10단계 — 주력상품 선정 최종 확인',
        f'대분류: {category}',
        f'소분류: {updates.get("subtype") or updates.get("theme") or "미확정"}',
        f'선택 색상 ({len(colors)}개): {", ".join(colors)}' if run.get('subtype_flow') else '색상: 상품별 검증 내역 참고',
        '선정 상품 목록 (H&M 실제 상품 ID):',
    ]
    for i, article_id in enumerate(ids, 1):
        row = rows[article_id]
        count = row.get('winter_online_purchases')
        count_note = f' / 과거 겨울 온라인 구매 {int(count):,}건' if count is not None else ''
        lines.append(f'{i}. {row.get("prod_name") or "상품명 확인 필요"} / ID {article_id} / 색상 {row.get("colour_group_name") or "확인 필요"}{count_note}')
    lines.extend([
        '선정 기준: 사용자 지정 유형·색상, 검증된 H&M 상품 ID, 과거 겨울 온라인 구매 기록.',
        '참고: 상품 유형은 상품명·설명 규칙 기반이며 현재 재고나 소재·기능 성능을 보장하지 않습니다.',
        # Slack이 승인·반려 버튼을 별도로 렌더링하므로 본문에 질문을 반복하지 않는다.
    ])
    return {
        'title': '10단계 주력상품 선정 최종 승인',
        'question': '\n'.join(lines),
        'updates': updates,
        'source': 'user',
    }
