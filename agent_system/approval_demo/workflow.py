"""모델 결과의 미리보기 기반 선정·대조군 시뮬레이션 및 메일 승인 기록.

주의: rank_only 모델 출력은 발송 명단이 아니며 실제 고객 연락처를 취급하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets

_STATUSES = {'DRAFT', 'PENDING', 'APPROVED', 'REJECTED'}
_EMAIL = re.compile(r'^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$')


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _validate_address(email: str):
    email = str(email or '').strip().lower()
    if not _EMAIL.fullmatch(email):
        raise ValueError('올바른 팀원 이메일 주소가 필요합니다.')
    allow = {v.strip().lower() for v in os.getenv('CART_APPROVAL_TEST_RECIPIENTS', '').split(',') if v.strip()}
    from .delivery_r42 import configured_roles, enabled
    if enabled():
        allow = {v for v in configured_roles().values() if v}
    if not allow or email not in allow:
        raise ValueError('승인자 주소가 CART_APPROVAL_TEST_RECIPIENTS 허용 목록에 없습니다.')
    return email


def check_review(review: dict):
    """입력 구조와 안전 조건 검증. 모델 점수를 실제 구매 확률로 표현하지 않는다."""
    errors = []
    if review.get('mode') != 'team_email_approval_demo':
        errors.append('DEMO_MODE_REQUIRED')
    if review.get('customer_delivery_enabled') is not False:
        errors.append('CUSTOMER_DELIVERY_FORBIDDEN')
    if review.get('customer_email_count') != 0:
        errors.append('CUSTOMER_EMAIL_FORBIDDEN')
    if review.get('source_purpose') != 'rank_only':
        errors.append('RANK_ONLY_REQUIRED')
    if review.get('source_date') != '2020-09-23':
        errors.append('HISTORICAL_DATE_REQUIRED')
    if not review.get('selection_basis'):
        errors.append('SELECTION_BASIS_REQUIRED')
    total = review.get('preview_count')
    target = review.get('candidate_count')
    control = review.get('control_count')
    if not all(type(n) is int and n >= 0 for n in (total, target, control)) or target + control != total or total == 0:
        errors.append('COUNTS_INCONSISTENT')
    if review.get('status') not in _STATUSES:
        errors.append('STATUS_INVALID')
    if not isinstance(review.get('message_draft'), str) or not review['message_draft'].strip():
        errors.append('MESSAGE_DRAFT_REQUIRED')
    # 메일로 전달할 본문에 고객 ID, 이메일, 목록이 포함되지 않았는지 간단한 구조 검증.
    if 'customer_ids' in review or 'customer_rows' in review or 'customer_emails' in review:
        errors.append('CUSTOMER_DATA_EXPOSURE')
    return {'status': 'PASS' if not errors else 'FAIL', 'errors': sorted(set(errors))}


def prepare_review(agent_result: dict, *, message_draft: str, approver: str,
                   candidate_share: float = .8) -> dict:
    """17단계의 rank_only 후보 미리보기에서 18~21단계 시연 요약을 구성한다.

    실제 고객 명단 전체를 만들지 않는다. 모델 출력 100명 미리보기만 사용하고
    80:20 구성을 예시로 보여준다. 무작위 대조군이 아닌 재현 가능한 시연용 분할.
    """
    approver = _validate_address(approver)
    if not isinstance(agent_result, dict) or agent_result.get('error') or agent_result.get('not_ready'):
        raise ValueError('타깃 Agent 결과가 실패했거나 사용할 수 없습니다.')
    source = agent_result.get('target_result')
    if not isinstance(source, dict) or source.get('purpose') != 'rank_only':
        raise ValueError('rank_only 결과만 승인 시연에 사용할 수 있습니다.')
    rows = source.get('rows')
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValueError('1~100명의 구조화된 미리보기만 받습니다.')
    if not 0 < candidate_share < 1:
        raise ValueError('대조군 비율이 올바르지 않습니다.')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not str(row.get('customer_id') or '').strip():
            raise ValueError('고객 ID가 없는 행이 있습니다.')
        key = str(row['customer_id'])
        if key in seen:
            raise ValueError('고객 ID가 중복되었습니다.')
        seen.add(key)
        score = row.get('score')
        if type(score) not in (float, int) or not 0 <= score <= 1:
            raise ValueError('점수 형식 또는 범위가 잘못되었습니다.')
    # 순위 기준 정렬: 기존 rank가 있으면 연속된 1..N이어야 한다.
    ranks = [r.get('rank') for r in rows]
    if ranks != list(range(1, len(rows) + 1)):
        raise ValueError('미리보기 순위가 1부터 연속되지 않습니다.')
    # 고객을 안전한 난수로 나누는 실험이 아닌 상위 순위 기반 데모 분할임을 명시한다.
    if len(rows) < 2:
        raise ValueError('후보·대조군 시연에 최소 2명이 필요합니다.')
    candidate_count = max(1, min(len(rows) - 1, round(len(rows) * candidate_share)))
    source_basis = str(source.get('selection_basis') or ('model_combo_rank' if source.get('model_run_id') else '')).strip()
    if not source_basis:
        raise ValueError('구매 이력 또는 모델 선정 근거가 필요합니다.')
    if str(source.get('score_as_of')) != '2020-09-23':
        raise ValueError('현재 R2 지원 기준일이 아닙니다.')
    total = source.get('total_scored_customers')
    if type(total) is not int or total < len(rows):
        raise ValueError('전체 점수 대상자 수를 확인해야 합니다.')
    message_draft = str(message_draft or '').strip()
    if not message_draft or len(message_draft) > 3000:
        raise ValueError('검토할 메시지 초안을 1~3000자로 입력하세요.')
    # 승인 ID와 토큰은 고객 데이터에서 파생하지 않는다.
    request_id = secrets.token_hex(7)
    review = {
        'schema_version': 1,
        'request_id': request_id,
        'approval_token': secrets.token_urlsafe(20),
        'mode': 'team_email_approval_demo',
        'status': 'DRAFT',
        'created_at': _utc(),
        'approver': approver,
        'source_purpose': 'rank_only',
        'source_date': str(source['score_as_of']),
        'selection_basis': source_basis,
        'scope_label': (f"{source.get('product_type')} × {source.get('colour')}" if source.get('product_type')
                        else f"카테고리 {source.get('category_group', '미지정')}"),
        'total_scored_customers': total,
        'preview_count': len(rows),
        'candidate_count': candidate_count,
        'control_count': len(rows) - candidate_count,
        'segment_description': '상위 순위 미리보기 기반 시연 분할(실험용 무작위 대조군 아님)',
        'message_draft': message_draft,
        'customer_delivery_enabled': False,
        'customer_email_count': 0,
        'limitations': [
            '2020년 이력 기반 과거 시뮬레이션이며 2026~2027년 현재 대상 고객이 아님',
            '표본은 전체 고객이 아닌 Agent 미리보기 최대 100명',
            '수신 동의·연령·제외 고객 확인 불가: 실제 발송 대상 아님',
            '대조군은 무작위 실험 배정이 아닌 시연용 구분',
            '구매 예측 모델 출력은 보정된 구매 확률이 아님',
            '승인 메일은 팀원에게만 보내고 고객에게 전송하지 않음',
        ],
        'history': [{'at': _utc(), 'event': 'PREPARED'}],
    }
    verdict = check_review(review)
    if verdict['status'] != 'PASS':
        raise ValueError('시연 결과 검증 실패: ' + ','.join(verdict['errors']))
    return review


def store_review(review: dict, path: str | Path):
    path = Path(path)
    if path.exists():
        raise FileExistsError('기존 승인 기록은 덮어쓸 수 없습니다.')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump(review, file, ensure_ascii=False, indent=2)
    return path


def load_review(path: str | Path) -> dict:
    with open(path, encoding='utf-8') as file:
        return json.load(file)


def update_review(path: str | Path, review: dict):
    """기존 승인 파일을 원자적으로 교체한다. 디스크 저장은 승인 상태만 기록한다."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    tmp = path.with_suffix(path.suffix + '.' + secrets.token_hex(5) + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(review, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
