"""R40 근거 보기/접기: 동일 버튼 메시지만 갱신하는 읽기 전용 UI."""
from __future__ import annotations
import uuid


def toggle_blocks(parsed, run, campaign, pending=False):
    from agent_system.orchestrator.tools import campaign_target_r39 as legacy
    from agent_system.orchestrator.tools.target_presentation_r422 import evidence_lines
    from interfaces.slack.actions import build_action_blocks
    action = parsed['action']
    target = run.get('campaign_target_r39') or {}
    if (pending or target.get('policy') != 'r40_v1'
            or action.get('command') != 'evidence' or action.get('view') not in ('open','close')
            or not legacy.review_action_valid(run, action)
            or target.get('fingerprint') != legacy.fingerprint(campaign,run)):
        raise ValueError('이전 선정 근거 버튼입니다. 최신 고객 선정 결과를 확인해 주세요.')
    opened = action['view'] == 'open'
    blocks = build_action_blocks([{'type':'target_selection_review','fixed_policy':True,
                                   'run_id':run['run_id'],'token':target['review_token']}],
                                 parsed['thread_id'],parsed['requester_user_id'])
    import json
    button = next(e for e in blocks[1]['elements'] if e['action_id'].endswith('_evidence'))
    value = json.loads(button['value']); value['view'] = 'close' if opened else 'open'
    button['value'] = json.dumps(value,ensure_ascii=False,separators=(',',':'))
    button['text']['text'] = '선정 근거 접기' if opened else '선정 근거 보기'
    blocks[1]['block_id'] += '_' + uuid.uuid4().hex
    if opened:
        lines = evidence_lines(target)
        # Block Kit section 최대 3000자보다 훨씬 작게 구성.
        for heading, group in [('분석 데이터와 분류 기준',lines[:6]),('정책 선정 검증 및 한계',lines[6:])]:
            def escape(v): return v.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
            body = '*' + heading + '*\n' + '\n\n'.join(escape(v) for v in group)
            if len(body)>3000: raise ValueError('선정 근거가 너무 깁니다.')
            blocks.append({'type':'section','text':{'type':'mrkdwn','text':body}})
    return blocks
