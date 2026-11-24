"""최종 PDF·PNG 등록과 직급별 사내 발송 승인 보조 기능."""
from __future__ import annotations
import hashlib
import os
import secrets
from pathlib import Path
from io import BytesIO
from .recipient_flow import _locked_delivery, _check_delivery_state, _migrate_delivery_history

ROLE_KEYS = {'과장':'MANAGER', '팀장':'TEAM_LEADER', '대리':'ASSISTANT_MANAGER', '사원':'STAFF'}
MAX_PNG = 3 * 1024 * 1024
MAX_TOTAL = 18 * 1024 * 1024


def configured_roles():
    return {role: os.getenv(f'CART_RECIPIENT_{key}_EMAIL', '').strip().lower()
            for role, key in ROLE_KEYS.items()}


def enabled():
    return any(configured_roles().values())


def validate_png(content):
    from PIL import Image
    if not 0 < len(content) <= MAX_PNG or not content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('PNG 형식 또는 3MB 크기 제한 오류')
    with Image.open(BytesIO(content)) as image:
        if image.format != 'PNG' or image.width * image.height > 20_000_000 or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('단일 PNG 및 2천만 픽셀 이하만 지원합니다.')
        image.verify()
    with Image.open(BytesIO(content)) as image:
        image.load()
    return content


@_locked_delivery
def register_png(root, event, content, file_id=None):
    from .recipient_flow import _read_state, _write_state, _private, _thread, _now
    state = _read_state(root, event)
    target = state.get('target') or {}
    if target.get('kind') != 'r39' or target.get('owner') != event.get('user'):
        raise ValueError('보고서를 요청한 사용자가 원래 기획전 스레드에 첨부해 주세요.')
    _check_delivery_state(state, event)
    _migrate_delivery_history(state)
    if file_id and file_id == (state.get('png') or {}).get('file_id'):
        return state
    validate_png(content)
    folder = _private(root) / 'final_reports'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = folder / (_thread(event) + '_' + secrets.token_hex(6) + '.png')
    with os.fdopen(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
        f.write(content)
    state['png'] = {'path': str(dest), 'sha256': hashlib.sha256(content).hexdigest(), 'saved_at': _now(), 'file_id':file_id}
    # 수신자 선택은 보존하되 기존 발송 버튼은 무효화한다.
    pending = state.get('pending') or {}
    if pending.get('status') in ('WAITING_PNG','AWAITING_SEND'):
        pending.update(nonce=secrets.token_urlsafe(18), status='WAITING_PNG', created_at=_now())
    elif pending.get('status') != 'AWAITING_CONFIRMATION':
        state.pop('pending', None)
    _write_state(root, event, state)
    return state


def checked_png(root, state):
    from .recipient_flow import _private
    record = state.get('png') or {}
    path = Path(record.get('path') or '').resolve()
    if path.parent != (_private(root)/'final_reports').resolve() or path.suffix != '.png' or not path.is_file() or path.stat().st_size > MAX_PNG:
        raise ValueError('최종 PNG를 먼저 등록해 주세요.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != record.get('sha256'):
        raise ValueError('PNG 무결성 확인 실패')
    validate_png(data)
    return path


@_locked_delivery
def prepare_send(root, event):
    from .recipient_flow import _read_state, _write_state, _ready, _now, load_recipient
    state = _read_state(root,event)
    _check_delivery_state(state, event)
    pending = state.get('pending') or {}
    if pending.get('owner') != event.get('user') or pending.get('status') not in ('WAITING_PNG','AWAITING_SEND'):
        return None
    if not state.get('png') or not state.get('pdf'):
        return None
    pdf = _ready(state)
    png = checked_png(root,state)
    if pdf.stat().st_size + png.stat().st_size > MAX_TOTAL:
        raise ValueError('총 첨부 크기 18MB 초과')
    person = load_recipient(root,pending['person']['role'])
    if person != pending['person']:
        raise ValueError('수신자 설정 변경: 다시 지정해 주세요.')
    from .mail_copy_r424 import generate, digest
    # 동일 검토안 재표시만으로 승인 토큰을 만료시키지 않는다.
    if (pending.get('status') == 'AWAITING_SEND' and pending.get('mail_draft')
            and pending.get('mail_hash') == digest(pending['mail_draft'])
            and pending.get('pdf_hash') == state['pdf']['sha256']
            and pending.get('png_hash') == state['png']['sha256']):
        return pending
    if not pending.get('mail_draft'):
        pending['mail_draft'], pending['mail_mode'] = generate(state['target'],person['role'])
    pending['mail_hash'] = digest(pending['mail_draft'])
    pending.update(status='AWAITING_SEND',nonce=secrets.token_urlsafe(18),created_at=_now(),pdf_hash=state['pdf']['sha256'],png_hash=state['png']['sha256'])
    _write_state(root,event,state)
    return pending


def post_buttons(client, event, pending=None, *, choose=False):
    from .recipient_flow import _thread, _read_state, _write_state, _ready, _now
    import json
    base = {'thread':_thread(event),'owner':event.get('user'),'channel':event.get('channel'),'thread_ts':event.get('thread_ts') or event.get('ts')}
    if choose:
        from .recipient_flow import create_recipient_choice
        try:
            choice = create_recipient_choice(Path(__file__).resolve().parents[2], event)
        except (ValueError, OSError) as exc:
            client.chat_postMessage(channel=event['channel'], thread_ts=base['thread_ts'], text=str(exc))
            return
        base['nonce'] = choice['nonce']
        pairs = [('pick_'+key,role) for role,key in ROLE_KEYS.items()]
    else:
        base['nonce'] = pending['nonce']
        pairs = [('send','발송 승인'),('cancel','발송 취소')] if pending['status']=='AWAITING_SEND' else [('yes','네'),('no','아니요')]
    elements=[{'type':'button','action_id':'cartrecipient_'+key,'text':{'type':'plain_text','text':label},'value':json.dumps(base,ensure_ascii=False)} for key,label in pairs]
    text = '담당자를 선택해 주세요.' if choose else ('최종 PDF + PNG 발송 승인: '+pending['person']['role']+' '+pending['person']['email'] if pending['status']=='AWAITING_SEND' else '수신자 확인: '+pending['person']['role']+' '+pending['person']['email'])
    if not choose and pending['status']=='AWAITING_SEND':
        from .recipient_flow import _build_review
        from .mail_copy_r424 import body
        state = _read_state(Path(__file__).resolve().parents[2],event)
        text += '\n제목: '+pending['mail_draft']['subject']+'\n첨부: CART_SIGNAL_최종_보고서.pdf / CART_SIGNAL_최종_기획전.png\n본문:\n' + body(state['target'],pending['mail_draft']) + '\n위 PDF와 PNG를 검토한 후 승인해 주세요.\n수정 예: 메일 제목을 “검토 요청”으로 바꿔줘 / 본문을 정중하게 수정해줘' + ('\n기본 템플릿 또는 이전 문구 사용 (LLM 생성·검증 실패)' if pending.get('mail_mode')=='fallback' else '')
    client.chat_postMessage(channel=event['channel'],thread_ts=base['thread_ts'],text=text,blocks=[{'type':'section','text':{'type':'plain_text','text':text}},{'type':'actions','elements':elements}])


@_locked_delivery
def revise_mail(root,event,text):
    from .recipient_flow import _read_state,_write_state,_now
    from .mail_copy_r424 import edit,digest
    state=_read_state(root,event); _check_delivery_state(state,event); pending=state.get('pending') or {}
    if pending.get('status')!='AWAITING_SEND' or pending.get('owner')!=event.get('user'):
        raise ValueError('먼저 최종 이메일 검토안을 생성해 주세요.')
    draft,mode=edit(state['target'],pending['person']['role'],pending['mail_draft'],text)
    pending.update(mail_draft=draft,mail_mode=mode,mail_hash=digest(draft),nonce=secrets.token_urlsafe(18),created_at=_now())
    _write_state(root,event,state)
    return pending
