"""17단계 실제 선정 검증 + 최종 PDF 완료 후 직급별 승인 수신자 확인.

개인 고객 정보는 저장하지 않는다. 고객 대상 발송 API는 제공하지 않는다.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _lock_file(f):
    """파일 배타 잠금. Linux·Mac은 fcntl, Windows는 msvcrt를 사용한다."""
    if os.name == 'nt':
        import msvcrt
        import time
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)
    import fcntl
    fcntl.flock(f, fcntl.LOCK_EX)


def _unlock_file(f):
    if os.name == 'nt':
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(f, fcntl.LOCK_UN)


ROLES = ('과장', '팀장', '대리', '사원')
MAIL_INTENT = re.compile(r'보내|보낼|전달|발송|전송|메일|이메일|승인\s*요청|공유')
ROLE_PATTERN = re.compile(r'(과장|팀장|대리|사원)(?:님)?(?:께|에게|한테|으로|한테도)?')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _thread(event):
    override = event.get('_r424_state_key')
    if override and re.fullmatch('[0-9a-f]{32}', override):
        return override
    channel = str(event.get('channel') or '').strip()
    thread = str(event.get('thread_ts') or event.get('ts') or '').strip()
    if not channel or not thread:
        raise ValueError('Slack 채널과 스레드 정보가 필요합니다.')
    return hashlib.sha256(f'{channel}\0{thread}'.encode()).hexdigest()[:32]


def _private(root):
    return Path(root) / 'backup' / 'approval_demo_private'


def _state_path(root, event):
    return _private(root) / 'threads' / (_thread(event) + '.json')


def _read_state(root, event):
    path = _state_path(root, event)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def _write_state(root, event, state):
    path = _state_path(root, event)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(state, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)



def _delivery_busy(state):
    return (state.get('pending') or {}).get('status') in ('SENDING', 'UNKNOWN')


def campaign_delivery_busy(root, event, run_id):
    """같은 사용자·채널·기획전의 모든 발송 상태에서 진행/불명확 여부를 검사한다."""
    if not run_id: raise ValueError('기획전 ID를 확인할 수 없습니다.')
    for path in (_private(root)/'threads').glob('*.json'):
        state=json.loads(path.read_text(encoding='utf-8'))
        target=state.get('target') or {}
        if (not state.get('archived') and target.get('kind')=='r39' and target.get('run_id')==run_id
                and target.get('owner')==event.get('user') and target.get('channel')==event.get('channel')
                and _delivery_busy(state)):
            return True
    return False


def _check_delivery_state(state, event):
    target = state.get('target') or {}
    if state.get('archived'):
        raise ValueError('이전 기획전 요청입니다.')
    if target.get('kind') == 'r39' and (target.get('owner') != event.get('user') or target.get('channel', event.get('channel')) != event.get('channel')):
        raise ValueError('다른 사용자의 기획전입니다.')
    if _delivery_busy(state):
        raise ValueError('이메일 발송 중이거나 결과 확인이 필요합니다. 중복 발송하지 않습니다.')


def _locked_delivery(fn):
    # 동일 기획전 상태의 읽기·수정·기록을 발송 승인과 같은 파일 락으로 직렬화한다.
    from functools import wraps
    from inspect import signature
    @wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature(fn).bind(*args, **kwargs)
        root, event = bound.arguments['root'], bound.arguments['event']
        path = _state_path(root, event).with_suffix('.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+') as lock:
            _lock_file(lock)
            return fn(*args, **kwargs)
    return wrapped


def _nonce_hash(nonce):
    return hashlib.sha256(str(nonce or '').encode()).hexdigest()


def _migrate_delivery_history(state):
    # 이전 버전의 단일 발송 기록도 최초 이력으로 보존한다.
    history = state.setdefault('delivery_history', [])
    sent = state.get('report_sent') or {}
    if sent and not any(x.get('smtp_request_id', x.get('request_id')) == sent.get('request_id') for x in history):
        pending = state.get('pending') or {}
        item = {'request_id': sent.get('request_id'), 'smtp_request_id': sent.get('request_id'),
                'status': 'SENT', 'at': sent.get('at'), 'legacy': True}
        if pending.get('status') == 'SENT':
            item.update(person=pending.get('person'), nonce_hash=_nonce_hash(pending.get('nonce')),
                        pdf_hash=pending.get('pdf_hash'), png_hash=pending.get('png_hash'), mail_hash=pending.get('mail_hash'))
        history.append(item)
    return history


@_locked_delivery
def create_recipient_choice(root, event):
    state = _read_state(root, event)
    _check_delivery_state(state, event)
    if (state.get('target') or {}).get('owner') != event.get('user'):
        raise ValueError('보고서를 요청한 사용자만 수신자를 선택할 수 있습니다.')
    if state.get('target', {}).get('kind') != 'r39' or state.get('pdf'):
        _ready(state)
    _migrate_delivery_history(state)
    state.pop('pending', None)
    choice = {'nonce': secrets.token_urlsafe(18), 'owner': event.get('user'), 'created_at': _now()}
    state['recipient_choice'] = choice
    _write_state(root, event, state)
    return choice


def _reply(text, **extra):
    return {'turn_type': 'chitchat', 'final_answer': text, 'completed_steps': [], **extra}


def match_send_intent(question):
    """한 문장을 하드코딩하지 않고 직급과 전달 의도를 함께 검사한다."""
    question = str(question or '').strip()
    if not MAIL_INTENT.search(question):
        return None
    roles = list(dict.fromkeys(ROLE_PATTERN.findall(question)))
    return roles if roles else None


# 전용 명령어 대신 고객 선정 업무를 요청하는 자연어 의도를 감지한다.
# 구매 가능성에 대한 단순 설명이나 이미 선정된 결과 문의는 실행 요청과 구분한다.
_TARGET_SUBJECT = re.compile(r'타[깃겟]|고객|대상자|구매\s*가능성|누구에게\s*(?:홍보|추천|마케팅)')
_TARGET_ACTION = re.compile(r'선정|선별|추천|추출|뽑|찾|고르|정하|정해|골라|시작|진행|분석')
_TARGET_INQUIRY = re.compile(r'기준|방법|설명|의미|정의|왜|어떻게|뭐야|무엇|어떤\s*뜻|알려\s*줘|되돌|취소')
_TARGET_HISTORY = re.compile(r'선정\s*(?:된|했|결과|완료)|추천\s*(?:된|했|결과)')


def is_target_selection_request(question: str) -> bool:
    """고객 후보 선정을 *실행*하라는 요청인지 판단 (단순 조회·설명 제외)."""
    text = re.sub(r'\s+', ' ', str(question or '')).strip()
    if not text or text.startswith('타깃검증 |'):
        return False
    if MAIL_INTENT.search(text) and re.search(r'(?:팀장|과장|대리|사원|보고서|PDF|이메일|메일)', text, re.I):
        return False
    if _TARGET_INQUIRY.search(text) or _TARGET_HISTORY.search(text):
        return False
    if re.search(r'고객(?:에게|한테|들?에게)\s*(?:보내|발송|전송)', text):
        return False
    # 요청 대상이 상품/카테고리이면 고객이 문맥(연령·타깃층)으로 등장해도
    # 고객 선정 도구를 실행하지 않는다. 고객 자체를 직접 선정하라는 요청은 우선한다.
    product_request = re.search(
        r'(?:주력\s*상품|추천\s*상품|대표\s*상품|기획전\s*상품|'
        r'(?:상품|제품|품목|카테고리|대분류|소분류)(?:을|를|부터|중|의|별)?\s*'
        r'(?:추천|선정|선별|비교|골라|정해|분석)|'
        r'(?:상품|제품|카테고리)\s*추천)',
        text,
    )
    explicit_customer_request = re.search(
        r'(?:고객|타[깃겟]|대상자)(?:들)?\s*(?:을|를|부터|중에서)?\s*'
        r'(?:선정|선별|추천|추출|뽑|찾|분석)|'
        r'(?:고객|타[깃겟]|대상자)\s*(?:선정|선별|추천|추출|뽑|찾)|'
        r'(?:구매할|구매할\s*가능성이\s*높은|구매\s*가능성이\s*높은)\s*고객.*?'
        r'(?:선정|추천|추출|뽑|찾)|'
        r'(?:타[깃겟]\s*고객|고객\s*타[깃겟]).*?(?:선정|추천|추출|뽑|찾)',
        text,
    )
    if product_request and not explicit_customer_request:
        return False
    return bool(_TARGET_SUBJECT.search(text) and _TARGET_ACTION.search(text))


# 이미 선정된 고객의 분석·특성 요청은 새 타깃 선정이나 보고서 작성으로 보내지 않는다.
_SELECTED_CUSTOMERS = re.compile(r'선정\s*(?:된|한|했던)\s*(?:고객|타깃|대상)|(?:해당|이|그|앞서|방금)\s*(?:고객|대상)|고객\s*(?:세그먼트|특성|분포)|타깃\s*(?:세그먼트|특성)')
_ANALYSIS_ACTION = re.compile(r'분석|세그먼트|특성|분포|구성|나눠|요약|근거|보여|알려')


def is_selected_customer_analysis_request(question: str) -> bool:
    """이미 선정된 고객을 분석하라는 후속 요청만 처리 (설명·보고서·발송 분리)."""
    text = re.sub(r'\s+', ' ', str(question or '')).strip()
    if not text or MAIL_INTENT.search(text) or re.search(r'보고서|PDF|메일|발송|전송|승인', text, re.I):
        return False
    if re.search(r'기준이\s*뭐|방법이\s*뭐|어떻게\s*선정', text):
        return False
    return bool(_SELECTED_CUSTOMERS.search(text) and _ANALYSIS_ACTION.search(text))


def _validated_segment_summary(source: dict, total: int) -> dict | None:
    summary = source.get('segment_summary')
    if not isinstance(summary, dict) or summary.get('kind') not in ('category_purchase_recency', 'model_rank_bands'):
        return None
    rows = summary.get('segments')
    if (type(summary.get('total')) is not int or summary['total'] != total
            or not isinstance(rows, list) or not rows):
        raise ValueError('고객 세그먼트 집계 형식 또는 전체 인원 불일치')
    if any(not isinstance(row, dict) or not str(row.get('name') or '').strip()
           or type(row.get('count')) is not int or row['count'] < 0
           or not isinstance(row.get('basis'), str) for row in rows):
        raise ValueError('고객 세그먼트별 데이터 오류')
    if sum(row['count'] for row in rows) != total:
        raise ValueError('세그먼트별 인원 합계 불일치')
    # 출력할 집계 수치와 범위를 제한하고 고객 ID 등 불필요한 값을 저장하지 않는다.
    return {'kind': summary['kind'], 'total': total,
            'segments': [{'name': row['name'][:60], 'count': row['count'],
                          'share_percent': round(100 * row['count'] / total, 2),
                          'basis': row['basis'][:120]} for row in rows[:10]],
            'basis': str(summary.get('basis') or '')[:240],
            'limits': str(summary.get('limits') or '')[:240]}


def format_target_and_segments(target: dict) -> str:
    """검증된 과거 고객 선정과 18단계 집계를 한 메시지로 표시한다."""
    summary = target.get('segment_summary')
    scope = target.get('scope_label', '미확인')
    total = target.get('total_scored_customers')
    if type(total) is not int or total <= 0:
        raise ValueError('고객 선정 결과 전체 인원이 올바르지 않습니다.')
    scope_display = scope.replace('카테고리 Outdoor', '아우터').replace('카테고리 ', '')
    basis_key = target.get('selection_basis')
    basis = ('최근 90일 해당 상품군 구매 이력' if basis_key == 'hm_category_purchase_history_90d'
             else '특정 품목·색상에 대한 모델 순위' if basis_key == 'model_combo_rank'
             else '별도로 검증된 고객 순위 기준')
    lines = ['고객 타깃 분석 결과',
             f'• 분석 대상: {scope_display}',
             f'• 분석 기준일: {target.get("source_date")} (직전 90일 데이터)',
             f'• 분석 고객: {total:,}명',
             f'• 분석 방식: {basis}']
    if summary:
        lines.append('구매 특성별 고객 분포:' if summary['kind'] == 'category_purchase_recency'
                     else '모델 순위별 고객 분포:')
        for group in summary['segments']:
            lines.append(f'• {group["name"]}: {group["count"]:,}명 ({group["share_percent"]:.2f}%) — {group["basis"]}')
        lines.append('집계 기준: ' + summary['basis'])
    else:
        lines.append('고객 그룹별 집계 데이터가 없어 세그먼트 분포는 표시하지 않습니다.')
    lines.append('※ 과거 H&M 데이터 기반 시뮬레이션입니다. 분석 고객 수는 실제 발송 가능 고객 수가 아니며, '
                 '구매 이력 기반 구간은 구매 예측 확률을 의미하지 않습니다.')
    return '\n'.join(lines)


def _campaign_scope(campaign):
    """확인 가능한 캠페인 조건만 추출한다. 추정한 정보로 다른 스레드를 매칭하지 않는다."""
    if not isinstance(campaign, dict):
        return None
    values = campaign.get('values')
    if not isinstance(values, dict):
        return None
    group = str(values.get('category') or values.get('target_category') or '').strip()
    group = {'아우터': 'Outdoor', 'outerwear': 'Outdoor'}.get(group.casefold(), group)
    # 현재 고객 선정의 기본 범위는 category 또는 정확한 품목/색상이다.
    product = str(values.get('target_product_type') or '').strip()
    colour = str(values.get('target_colour') or '').strip()
    if product and colour:
        return {'kind': 'combo', 'product': product, 'colour': colour}
    if group:
        return {'kind': 'category', 'category': group}
    return None


def _matches_campaign(target, campaign):
    """모델 조합과 카테고리 이력은 절대 서로 대체하지 않는다."""
    scope = _campaign_scope(campaign)
    if not scope:
        return False
    if scope['kind'] == 'combo':
        return (target.get('selection_basis') != 'hm_category_purchase_history_90d'
                and target.get('product_type') == scope['product']
                and target.get('colour') == scope['colour'])
    return (target.get('selection_basis') == 'hm_category_purchase_history_90d'
            and target.get('category_group') == scope['category'])


def _cross_thread_target(root, event, campaign):
    """같은 채널·요청자의 최근 검증 결과가 하나뿐일 때만 읽기 전용 조회한다.

    저장된 상태의 PDF/승인/메일은 가져오지 않는다. 오래된 R6 상태는
    채널·요청자와 캠페인 정보가 없으므로 자동 조회 대상에서 제외한다.
    """
    if not _campaign_scope(campaign):
        return None, '현재 기획전 조건을 확인할 수 없습니다. 고객을 선정한 원래 스레드에서 분석해 주세요.'
    channel = str(event.get('channel') or '').strip()
    requester = str(event.get('user') or '').strip()
    if not channel or not requester:
        return None, 'Slack 요청자 정보가 없어 다른 스레드의 고객 분석을 조회할 수 없습니다.'
    directory = _private(root) / 'threads'
    now = datetime.now(timezone.utc)
    matches = []
    for path in directory.glob('*.json') if directory.is_dir() else []:
        if path == _state_path(root, event) or not path.is_file():
            continue
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            target = state.get('target')
            if not isinstance(target, dict):
                continue
            meta = target.get('slack_scope') or {}
            if meta.get('channel') != channel or meta.get('owner') != requester:
                continue
            if not _matches_campaign(target, campaign):
                continue
            validated = datetime.fromisoformat(target.get('validated_at', ''))
            if validated.tzinfo is None or not timedelta(0) <= now - validated <= timedelta(hours=24):
                continue
            # 저장 결과가 손상되면 건너뛰고 조용히 잘못된 데이터를 노출하지 않는다.
            total = target.get('total_scored_customers')
            if type(total) is not int or total <= 0 or not isinstance(target.get('segment_summary'), dict):
                continue
            _validated_segment_summary(target, total)
            matches.append(target)
        except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError):
            continue
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, '같은 상품 조건의 고객 선정 기록이 여러 개 있습니다. 정확한 결과를 확인하려면 원래 스레드에서 요청해 주세요.'
    return None, '현재 기획전과 일치하는 최근 고객 선정 결과를 찾지 못했습니다. 고객을 선정한 원래 스레드에서 요청해 주세요.'


def show_selected_customer_analysis(root, event, campaign=None) -> dict:
    state = _read_state(root, event)
    target = state.get('target')
    if isinstance(target, dict):
        return _reply(format_target_and_segments(target))
    target, reason = _cross_thread_target(root, event, campaign)
    if target is None:
        return _reply(reason)
    # 다른 스레드에는 결과를 복제하거나 승인용 상태를 기록하지 않는다.
    return _reply('기존에 선정한 고객의 분석 결과입니다 (새 고객 선정 없음).\n'
                  + format_target_and_segments(target))


def recipients_file(root):
    return _private(root) / 'approvers.json'


def load_recipient(root, role):
    from .delivery_r42 import configured_roles, enabled
    if enabled():
        from .workflow import _validate_address
        email = _validate_address(configured_roles().get(role))
        return {'role': role, 'name': '', 'email': email}
    path = recipients_file(root)
    if not path.is_file():
        raise ValueError('backup/approval_demo_private/approvers.json 파일을 먼저 작성해 주세요.')
    directory = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(directory, dict) or role not in directory:
        raise ValueError(f'{role} 수신자가 설정되지 않았습니다.')
    record = directory[role]
    if not isinstance(record, dict):
        raise ValueError('수신자 파일 형식이 올바르지 않습니다.')
    name = str(record.get('name') or '').strip()
    email = str(record.get('email') or '').strip().lower()
    if not name or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email):
        raise ValueError(f'{role}의 이름·이메일 설정이 올바르지 않습니다.')
    # 파일의 역할과 SMTP 안전 허용 목록은 별개의 조건이다.
    from .workflow import _validate_address
    _validate_address(email)
    return {'role': role, 'name': name, 'email': email}


def validate_target_for_thread(root, event, question, campaign=None):
    """실제 R2+DB를 실행하고 검증된 집계만 해당 Slack 스레드에 보관한다."""
    question = str(question or '').strip()
    if not question:
        raise ValueError('고객 선정 요청이 비어 있습니다.')
    from agent_system.agents.dynamic_targeting import calculate_requested_target
    from agent_system.validation.specialized_selection_gate import check_selection
    result = calculate_requested_target(question, campaign or {}, Path(root))
    verdict = check_selection('targeting', question, result)
    if verdict.get('status') != 'PASS' or result.get('error') or result.get('not_ready'):
        raise ValueError('타깃 선정 검증 실패: ' + ', '.join(verdict.get('errors') or []))
    source = result.get('target_result') or {}
    if source.get('purpose') != 'rank_only':
        raise ValueError('실제 발송 명단을 승인 테스트에 사용할 수 없습니다.')
    rows = source.get('rows') or []
    total = source.get('total_scored_customers')
    if not rows or type(total) is not int or total < len(rows):
        raise ValueError('검증된 고객 수와 미리보기가 필요합니다.')
    state = _read_state(root, event)
    segment_summary = _validated_segment_summary(source, total)
    state['target'] = {
        'source_date': str(source.get('score_as_of')),
        'selection_basis': str(source.get('selection_basis') or ('model_combo_rank' if source.get('model_run_id') else '')),
        'scope_label': (f"{source['product_type']} × {source['colour']}" if source.get('product_type') else
                        f"카테고리 {source.get('category_group', '미지정')}"),
        'total_scored_customers': total,
        'preview_count': len(rows),
        'candidate_count': max(1, min(len(rows)-1, round(len(rows)*.8))),
        'control_count': len(rows) - max(1, min(len(rows)-1, round(len(rows)*.8))),
        'segment_summary': segment_summary,
        # 서로 다른 스레드에서 조회할 때 사용하는 최소 메타데이터 (고객 ID 저장 안 함).
        'category_group': str(source.get('category_group') or ''),
        'product_type': str(source.get('product_type') or ''),
        'colour': str(source.get('colour') or ''),
        'slack_scope': {'channel': str(event.get('channel') or ''),
                        'owner': str(event.get('user') or '')},
        'validated_at': _now(),
    }
    if state['target']['source_date'] != '2020-09-23' or not state['target']['selection_basis'] or len(rows) < 2:
        raise ValueError('시연용 과거 데이터·선정 근거를 확인할 수 없습니다.')
    # 고객 범위를 다시 선정하면 이전 보고서 PDF와의 정합성을 다시 확인해야 한다.
    state.pop('pdf', None)
    state.pop('pending', None)
    _write_state(root, event, state)
    return _reply(format_target_and_segments(state['target']))


def register_campaign_report(root, event, summary):
    """R39: 보고서 전송 성공 이후 집계만 등록. 같은 요청자의 새 보고서는 이전 PDF를 무효화한다."""
    owner = str(event.get('user') or '')
    if (not owner or summary.get('kind') != 'r39' or not summary.get('run_id')
            or not summary.get('fingerprint') or summary.get('source_date') != '2020-09-23'
            or not summary.get('groups') or sum(g['count'] for g in summary['groups']) != summary['candidate_count']):
        raise ValueError('기획전 보고서 집계 확인 실패')
    old = _read_state(root, event)
    old_target = old.get('target') or {}
    # 같은 Run의 보고서를 다른 스레드에 생성해도 별도의 발송 상태를 만들지 않는다.
    from .session_r424 import resolve
    if not old_target:
        try:
            resolve(root,event,summary)
            old = _read_state(root,event); old_target = old.get('target') or {}
        except ValueError:
            candidates=[]
            for path in (_private(root)/'threads').glob('*.json'):
                saved=json.loads(path.read_text(encoding='utf-8'))
                t=saved.get('target') or {}
                if (not saved.get('archived') and t.get('kind')=='r39' and t.get('run_id')==summary.get('run_id')
                        and t.get('owner')==owner and t.get('channel')==event.get('channel') and re.fullmatch('[0-9a-f]{32}',path.stem)):
                    candidates.append(path.stem)
            if len(candidates)>1:
                raise ValueError('이전 기획전의 발송 이력이 여러 스레드에 있습니다. 원래 보고서 스레드에서 새 보고서를 생성해 주세요.')
            if candidates:
                event['_r424_state_key']=candidates[0]
                old=_read_state(root,event); old_target=old.get('target') or {}
    if old_target.get('kind') == 'r39' and old_target.get('owner') != owner:
        raise ValueError('다른 요청자의 보고서 스레드입니다.')
    return _register_campaign_report_locked(root, event, summary)


@_locked_delivery
def _register_campaign_report_locked(root, event, summary):
    owner = str(event.get('user') or '')
    old = _read_state(root, event)
    old_target = old.get('target') or {}
    _check_delivery_state(old, event)
    _migrate_delivery_history(old)
    if old_target and old_target.get('run_id') != summary.get('run_id'):
        archive_event=dict(event, _r424_state_key=hashlib.sha256(('archive:'+_thread(event)+':'+str(old_target.get('run_id'))).encode()).hexdigest()[:32])
        _write_state(root,archive_event,{**old,"archived":True})
    state = {'target': {**summary, 'owner': owner, 'channel': event.get('channel')}}
    if old_target.get('run_id') == summary.get('run_id'):
        # 새 Word 초안에는 기존 최종 PDF를 자동 첨부하지 않는다. 발송 이력은 유지한다.
        for key in ('delivery_history', 'report_sent'):
            if key in old: state[key] = old[key]
        if all(old_target.get(k) == summary.get(k) for k in ('fingerprint', 'segment_copy_hash')) and old.get('png'):
            state['png'] = old['png']
    _write_state(root, event, state)


def matches_campaign_report(root, event, summary):
    saved = _read_state(root, event).get('target') or {}
    return (saved.get('kind') == 'r39' and saved.get('owner') == event.get('user')
            and saved.get('run_id') == summary.get('run_id') and saved.get('fingerprint') == summary.get('fingerprint')
            and saved.get('segment_copy_hash') == summary.get('segment_copy_hash'))


@_locked_delivery
def mark_pdf_ready(root, event, pdf_path):
    """Slack 업로드가 성공한 PDF만 별도 비공개 저장(고객 데이터 메일 자동 발송 없음)."""
    pdf_path = Path(pdf_path)
    if pdf_path.suffix.lower() != '.pdf' or not pdf_path.is_file() or not 0 < pdf_path.stat().st_size <= 15*1024*1024:
        raise ValueError('보고서 PDF 형식·크기 오류')
    with pdf_path.open('rb') as pdf_file:
        signature = pdf_file.read(5)
    if signature != b'%PDF-':
        raise ValueError('올바른 PDF가 아닙니다.')
    state = _read_state(root, event)
    if (state.get('target') or {}).get('kind') == 'r39' and state['target'].get('owner') != event.get('user'):
        raise ValueError('보고서를 요청한 사용자만 최종 PDF를 등록할 수 있습니다.')
    _check_delivery_state(state, event)
    _migrate_delivery_history(state)
    files = _private(root) / 'final_reports'
    files.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = files / (_thread(event) + '_' + secrets.token_hex(6) + '.pdf')
    with pdf_path.open('rb') as src:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    state['pdf'] = {'path': str(destination), 'saved_at': _now(), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}
    pending=state.get('pending') or {}
    if state.get('target',{}).get('kind')=='r39' and pending.get('status') in ('WAITING_PNG','AWAITING_SEND'):
        pending.update(status='WAITING_PNG',nonce=secrets.token_urlsafe(18),created_at=_now())
    elif pending.get('status')!='AWAITING_CONFIRMATION':
        state.pop('pending',None)
    state.pop('recipient_choice', None)
    _write_state(root, event, state)
    return destination


def _ready(state):
    if not state.get('target'):
        raise ValueError('먼저 17단계 고객 선정을 요청해 주세요. 예: 이제 고객 타깃 선정하자')
    pdf = state.get('pdf') or {}
    path = Path(pdf.get('path') or '')
    if not pdf or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pdf.get('sha256'):
        raise ValueError('이 Slack 스레드에서 최종 보고서 PDF 변환 성공을 확인하지 못했습니다.')
    return path


@_locked_delivery
def start_recipient_confirmation(question, event, root, *, choice_nonce=None):
    if not MAIL_INTENT.search(str(question or '')):
        return None
    if re.search(r'고객(?:들)?(?:에게|한테|께)', str(question)):
        return _reply('실제 고객 이메일 발송은 지원하지 않습니다.')
    roles = match_send_intent(question) or []
    # 사전에 등록된 이름으로 지칭해도 같은 수신자 확인 과정을 진행한다.
    if not roles and recipients_file(root).is_file():
        try:
            directory = json.loads(recipients_file(root).read_text(encoding='utf-8'))
            if isinstance(directory, dict):
                for role, person in directory.items():
                    if role in ROLES and isinstance(person, dict):
                        name = str(person.get('name') or '').strip()
                        if len(name) >= 2 and name in str(question):
                            roles.append(role)
        except (ValueError, OSError):
            pass
    if not roles:
        if '고객' in str(question):
            return _reply('실제 고객 이메일 발송은 지원하지 않습니다. 팀원 승인 요청만 가능합니다.')
        try:
            _check_delivery_state(_read_state(root, event), event)
        except (ValueError, OSError) as exc:
            return _reply(str(exc))
        return _reply('어느 승인자에게 보고서를 보낼까요? 과장·팀장·대리·사원 중 지정해 주세요.', recipient_choose=True)
    if len(roles) != 1:
        return _reply('승인자를 한 명만 지정해 주세요. 예: 팀장님께 승인 요청해줘')
    try:
        state = _read_state(root, event)
        _check_delivery_state(state, event)
        if choice_nonce is not None:
            choice = state.get('recipient_choice') or {}
            if choice.get('nonce') != choice_nonce or choice.get('owner') != event.get('user') or datetime.now(timezone.utc) - datetime.fromisoformat(choice['created_at']) > timedelta(minutes=15):
                raise ValueError('이전 담당자 선택 버튼입니다. 다시 요청해 주세요.')
        history = _migrate_delivery_history(state)
        if state.get('target',{}).get('kind')!='r39' or state.get('pdf'):
            _ready(state)
        person = load_recipient(root, roles[0])
        owner = str(event.get('user') or '')
        if not owner:
            raise ValueError('Slack 요청자 정보가 필요합니다.')
        nonce = secrets.token_urlsafe(18)
        state.pop('recipient_choice', None)
        state['pending'] = {'nonce': nonce, 'owner': owner, 'person': person,
                            'created_at': _now(), 'status': 'AWAITING_CONFIRMATION',
                            'delivery_request_id': secrets.token_hex(7)}
        _write_state(root, event, state)
        display = f"{person['name']} {person['role']}님".strip()
        return _reply(f"{display} ({person['email']}) 맞을까요?\n" +
                      ('기존 발송 이력이 있습니다. 새 발송 요청으로 진행합니다.\n' if history else '') +
                      '네/아니요 버튼을 눌러 주세요. 기획전 메일은 첨부 검토 후 최종 발송 승인에서만 전송됩니다.',
                      recipient_confirmation={'nonce': nonce, 'role': person['role']})
    except (ValueError, OSError) as exc:
        return _reply(str(exc))


def _build_review(summary, person):
    from .workflow import _utc, check_review
    count = summary['preview_count']
    review = {
        'schema_version': 1, 'request_id': secrets.token_hex(7),
        'approval_token': secrets.token_urlsafe(20), 'mode': 'team_email_approval_demo',
        'status': 'DRAFT', 'created_at': _utc(), 'approver': person['email'],
        'source_purpose': 'rank_only', 'source_date': summary['source_date'],
        'selection_basis': summary['selection_basis'], 'scope_label': summary['scope_label'],
        'total_scored_customers': summary['total_scored_customers'], 'preview_count': count,
        'candidate_count': summary['candidate_count'], 'control_count': summary['control_count'],
        'segment_description': '미리보기 순위 기반 시연 분할(무작위 대조군 아님)',
        'message_draft': '고객에게 실제 발송하지 않는 기획전 보고서 승인 시연입니다. 첨부된 PDF를 확인해 주세요.',
        'customer_delivery_enabled': False, 'customer_email_count': 0,
        'history': [{'at': _utc(), 'event': 'PREPARED'}],
    }
    if summary.get('kind') == 'r39':
        review['r39_report_delivery'] = True
        review['target_groups'] = [{'name': str(g['name']), 'count': int(g['count'])} for g in summary['groups']]
        review['segment_description'] = summary['selection_basis'] if summary['selection_basis'].startswith('R40') else '색상별 상위 순위 선정 및 고객 중복 제거'
        review['message_draft'] = '고객군별 마케팅 메시지와 기획전 운영 전략은 첨부 보고서에 수록했습니다.'
    if check_review(review)['status'] != 'PASS':
        raise ValueError('승인 요청 구조 검증 실패')
    return review


def confirm_recipient(root, event, nonce, yes, *, final=False):
    """최종 버튼에서만 SMTP 실행. 다른 사용자·오래된 버튼·두 번 클릭 차단."""
    path = _state_path(root, event)
    lock_path = path.with_suffix('.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        _lock_file(lock)
        try:
            state = _read_state(root, event)
            target = state.get('target') or {}
            if state.get('archived') or (target.get('kind') == 'r39' and (target.get('owner') != event.get('user') or target.get('channel', event.get('channel')) != event.get('channel'))):
                return '이전 기획전 또는 다른 사용자의 요청입니다. 전송하지 않았습니다.'
            pending = state.get('pending') or {}
            history = _migrate_delivery_history(state)
            if final and nonce and any(x.get('status') == 'SENT' and x.get('nonce_hash') == _nonce_hash(nonce) for x in history):
                return '이미 발송 완료된 이메일입니다. 중복 발송하지 않습니다.'
            if not pending or pending.get('nonce') != nonce or pending.get('owner') != event.get('user'):
                return '이전 요청이거나 다른 사용자의 버튼입니다. 전송하지 않았습니다.'
            from .delivery_r42 import enabled, checked_png
            r42 = state.get('target',{}).get('kind') == 'r39' and (enabled() or bool(state['target'].get('segment_copy_hash')))
            expected = 'AWAITING_SEND' if final else 'AWAITING_CONFIRMATION'
            if pending.get('status') != expected:
                return '이미 처리 중이거나 완료된 요청입니다.'
            try:
                created = datetime.fromisoformat(pending['created_at'])
                if datetime.now(timezone.utc) - created > timedelta(minutes=15):
                    pending['status'] = 'EXPIRED'
                    _write_state(root, event, state)
                    return '확인 요청이 만료됐습니다. 다시 수신자를 지정해 주세요.'
                pdf = _ready(state) if final or not r42 or state.get('pdf') else None
            except (ValueError, OSError) as exc:
                return '최종 PDF 확인 실패: ' + str(exc)
            if not yes:
                pending['status'] = 'CANCELLED'
                _write_state(root, event, state)
                return '발송 요청을 취소했습니다. 이메일은 전송하지 않았습니다.'
            # 발송 직전에 허용 목록과 이메일 파일을 재검사한다.
            person = load_recipient(root, pending['person']['role'])
            if person != pending['person']:
                return '승인자 설정이 변경됐습니다. 수신자 확인을 다시 해 주세요.'
            if r42 and not final:
                pending.update(status='WAITING_PNG', nonce=secrets.token_urlsafe(18), created_at=_now())
                _write_state(root,event,state)
                return '수신자가 확인되었습니다. 수정 Word를 첨부해 주세요.' if not state.get('pdf') else '수신자가 확인되었습니다.' if state.get('png') else '수신자가 확인되었습니다. 최종 기획전 PNG를 첨부해 주세요. 최종 PDF는 자동 첨부됩니다.'
            if r42:
                try:
                    png = checked_png(root,state)
                    if pending.get('pdf_hash') != state['pdf']['sha256'] or pending.get('png_hash') != state['png']['sha256']:
                        raise ValueError('첨부가 변경됐습니다.')
                except (ValueError,OSError) as exc:
                    return str(exc)
            if r42:
                from .mail_copy_r424 import checked, digest
                try:
                    if digest(checked(pending['mail_draft'])) != pending.get('mail_hash'):
                        raise ValueError('메일 문구 무결성 오류')
                except (ValueError,KeyError,TypeError):
                    return '메일 문구 확인 실패. 새 검토안을 생성해 주세요.'
            pending['status'] = 'SENDING'
            pending.setdefault('delivery_request_id', secrets.token_hex(7))
            if state['target'].get('kind') == 'r39':
                history.append({'request_id': pending['delivery_request_id'], 'status': 'SENDING', 'at': _now(),
                                'person': dict(person), 'nonce_hash': _nonce_hash(nonce),
                                'pdf_hash': state['pdf']['sha256'], 'png_hash': pending.get('png_hash'), 'mail_hash': pending.get('mail_hash')})
            _write_state(root, event, state)
        finally:
            _unlock_file(lock)
    # 중복 발송 방지를 위해 SENDING 기록을 먼저 남기고 SMTP 실행.
    from .workflow import store_review
    from .mail import send_team_approval
    review = _build_review(state['target'], person)
    review['request_id'] = pending['delivery_request_id']
    review['slack_requester'] = event.get('user')
    review['report_pdf_path'] = str(pdf)
    review['report_pdf_sha256'] = state['pdf']['sha256']
    if r42:
        review['report_png_path'] = str(png)
        review['report_png_sha256'] = state['png']['sha256']
    if r42 and pending.get('mail_draft'):
        from .mail_copy_r424 import body, digest
        if digest(pending['mail_draft']) != pending.get('mail_hash'):
            return '메일 문구 무결성 오류. 발송하지 않았습니다.'
        review['mail_subject'] = pending['mail_draft']['subject']
        review['mail_body'] = body(state['target'], pending['mail_draft'])
    record = _private(root) / 'slack_records' / (review['request_id'] + '.json')
    store_review(review, record)
    try:
        sent = send_team_approval(record, confirm=True)
    except Exception as exc:
        # SMTP가 전송 후 연결 실패하면 실제 도착 여부가 불분명하므로 자동 재시도하지 않는다.
        with lock_path.open('a+') as lock:
            _lock_file(lock)
            current = _read_state(root, event)
            if (current.get('pending') or {}).get('nonce') == nonce:
                current['pending']['status'] = 'UNKNOWN'
                for item in current.get('delivery_history', []):
                    if item.get('request_id') == pending['delivery_request_id']:
                        item.update(status='UNKNOWN', error_type=type(exc).__name__, checked_at=_now())
                _write_state(root, event, current)
        return f'메일 발송 결과를 확정할 수 없습니다 ({type(exc).__name__}). Gmail 발신함을 확인한 뒤 관리자에게 문의해 주세요. 자동 재전송하지 않습니다.'
    if state['target'].get('kind') == 'r39':
        # 발송 완료를 별도로 기록하며 고객 발송이나 회신 승인 절차로 넘어가지 않는다.
        with lock_path.open('a+') as lock:
            _lock_file(lock)
            current = _read_state(root, event)
            if (current.get('pending') or {}).get('nonce') == nonce:
                current['pending']['status'] = 'SENT'
                current['report_sent'] = {'at': _now(), 'request_id': sent['request_id']}
                for item in current.get('delivery_history', []):
                    if item.get('request_id') == pending['delivery_request_id']:
                        item.update(status='SENT', sent_at=_now(), smtp_request_id=sent['request_id'])
                _write_state(root, event, current)
        return f"{person['role']} {person['name']}님께 최종 기획전 보고서 PDF" + ('와 PNG를 전송했습니다.' if r42 else '를 전송했습니다.')
    with lock_path.open('a+') as lock:
        _lock_file(lock)
        current = _read_state(root, event)
        if (current.get('pending') or {}).get('nonce') == nonce:
            current['pending']['status'] = 'SENT'
            _write_state(root, event, current)
    return f"{person['role']} {person['name']}님께 최종 PDF 승인 요청을 전송했습니다. 요청 ID: {sent['request_id']}. 고객에게는 발송하지 않았습니다."


def route_recipient_flow(question, event, root, campaign=None):
    text = str(question or '').strip()
    if is_selected_customer_analysis_request(text):
        return show_selected_customer_analysis(root, event, campaign=campaign)
    legacy = text.startswith('타깃검증 |')
    if legacy or is_target_selection_request(text):
        target_question = text.split('|', 1)[1].strip() if legacy else text
        # 명시된 분석 대상 또는 검증된 캠페인 조건 없이 임의 카테고리를 추측하지 않는다.
        try:
            return validate_target_for_thread(root, event, target_question, campaign=campaign)
        except Exception as exc:
            from agent_system.agents.dynamic_targeting import TargetSelectionError
            if isinstance(exc, TargetSelectionError) and str(exc) == 'TARGET_SELECTION_REQUIRED':
                return _reply('어떤 상품·카테고리의 고객을 선정할까요? 현재 기획전 카테고리가 확인되지 않습니다. '
                              '예: 아우터 구매 고객 선정해줘 / Sweater Black 타깃 추천해줘')
            return _reply(f'17단계 타깃 선정·검증 실패 ({type(exc).__name__}). '
                          '상품 분류·기준일·DB·모델과 검증 결과를 확인해 주세요. 고객 명단은 확정하지 않았습니다.')
    return start_recipient_confirmation(text, event, root)
