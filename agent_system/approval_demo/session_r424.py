"""현재 그래프의 기획전과 정확히 일치하는 등록 상태만 재사용한다."""
import json
import re

def resolve(root,event,summary):
    from .recipient_flow import _private, _read_state, _thread
    if not summary or not event.get('user') or not event.get('channel'):
        raise ValueError('현재 기획전의 확정 보고서를 먼저 생성해 주세요.')
    def match(t):
        return (t.get('kind')=='r39' and t.get('owner')==event['user'] and t.get('channel',event['channel'])==event['channel'] and all(t.get(k)==summary.get(k) for k in ('run_id','fingerprint','segment_copy_hash')))
    state = _read_state(root,event)
    if state.get('archived'): raise ValueError('이전 기획전 요청입니다.')
    local=state.get('target') or {}
    if local and not match(local):
        raise ValueError('이 스레드는 다른 기획전의 보고서입니다. 현재 기획전에서 요청해 주세요.')
    if match(local): return event
    candidates=[]
    for path in (_private(root)/'threads').glob('*.json'):
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('archived'): continue
            t=saved.get('target') or {}
            if match(t) and re.fullmatch('[0-9a-f]{32}',path.stem): candidates.append(path.stem)
        except (ValueError,OSError): pass
    if len(candidates)!=1: raise ValueError('현재 기획전의 보고서 등록을 하나로 확인할 수 없습니다. 원래 보고서 스레드에서 요청해 주세요.')
    event['_r424_state_key']=candidates[0]
    return event
