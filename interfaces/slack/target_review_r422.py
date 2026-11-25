"""R40 근거 보기/접기: 동일 버튼 메시지만 갱신하는 읽기 전용 UI."""
from __future__ import annotations
import uuid
import hashlib
import re


def confirmed_token(target):
    # 기존 승인 세션의 토큰은 승인 결과의 식별값에서 복원한다.
    return target.get("evidence_token") or hashlib.sha256((str(target.get("fingerprint"))+"|"+str(target.get("output_dir"))).encode()).hexdigest()[:32]


def evidence_request(text):
    return bool(re.search(r"고객.*(?:선정|분류|타[깃겟]|분석).*?(?:근거|기준|결과).*?(?:보여|알려|설명|확인|조회)|고객군.*어떤.*기준|고객.*(?:선정|타[깃겟]).*근거[.!?]?$",str(text)))


def replay(run,campaign,thread,owner):
    from agent_system.orchestrator.tools import campaign_target_r39 as legacy
    target=legacy.confirmed_for_report(run,campaign) if legacy.delivery_allowed(run) else None
    if not target or target.get("policy")!="r40_v1":
        raise ValueError("현재 기획전의 승인된 고객 선정 근거가 없습니다.")
    result = {"turn_type":"chitchat","final_answer":legacy.confirmed_notice(target)+"\n\n아래 선정 근거 보기 버튼에서 승인된 분석 근거를 조회할 수 있습니다. (재계산 없음)","completed_steps":[],"thread_id":thread,"actions":[{"type":"target_selection_review","fixed_policy":True,"read_only":True,"run_id":run["run_id"],"token":confirmed_token(target)}]}
    from agent_system.orchestrator.tools.target_presentation_r422 import optional_chart
    path = optional_chart(target, run)
    if path:
        result["attachments"] = [path]
    else:
        result["final_answer"] += "\n4분면 이미지 생성에 실패했습니다. 텍스트 근거는 조회할 수 있습니다."
    return result


def toggle_blocks(parsed, run, campaign, pending=False):
    from agent_system.orchestrator.tools import campaign_target_r39 as legacy
    from agent_system.orchestrator.tools.audience_scope_r426 import current_target
    from agent_system.orchestrator.tools.target_presentation_r422 import evidence_lines
    from interfaces.slack.actions import build_action_blocks
    action = parsed['action']
    target = run.get('campaign_target_r39') or {}
    confirmed = bool(legacy.delivery_allowed(run) and legacy.confirmed_for_report(run,campaign) and target.get('status')=='confirmed')
    valid = (action.get('run_id') == run.get('run_id') and action.get('token') == confirmed_token(target)) if confirmed else legacy.review_action_valid(run, action)
    if ((pending and not confirmed) or target.get('policy') != 'r40_v1'
            or action.get('command') != 'evidence' or action.get('view') not in ('open','close')
            or not valid
            or not current_target(target, campaign, run)):
        raise ValueError('이전 선정 근거 버튼입니다. 최신 고객 선정 결과를 확인해 주세요.')
    opened = action['view'] == 'open'
    blocks = build_action_blocks([{'type':'target_selection_review','fixed_policy':True,
                                   'run_id':run['run_id'],'token':confirmed_token(target) if confirmed else target['review_token'],'read_only':confirmed}],
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
