"""Orchestrator Agent — 멀티턴 대화 관리 + 업무 분기 + 승인·단계 흐름 제어.

Orchestrator Tool (멘토 조언 5개 기능)
    route_request              현재 질문의 의도 파악 및 담당 Agent 결정   tools/intent.py + tools/routing.py (LLM Router)
    manage_context             이전 대화와 확정된 상품·타깃·일정 참조     tools/context.py (State)
    update_conversation_state  새로운 결정 사항을 대화 상태에 반영        tools/conversation_state.py (State 업데이트)
    check_approval             사용자 승인 여부 확인 후 다음 단계 진행    tools/approval.py (LangGraph interrupt)
    manage_workflow            대화 단계에 따라 실행 흐름 제어            tools/workflow.py (Conditional Edge)
    + tools/memory.py          Checkpointer · Slack thread_id 규칙

한 턴의 처리 (orchestrator_node)
    1. Checkpointer가 thread_id로 이전 상태(messages · last_analysis · campaign · pending_approval)를 넣어 줌
    2. 기억 정리 · 12시간 자동 리셋                                        context
    3. 워크플로 명령('워크플로 취소/계속/상태')이면 규칙으로 바로 처리     manage_workflow
       보고서 요청('보고서 만들어줘')이면 ⑥ Reporting Agent가 Word 초안 작성  tools/report.py
    4. 대화 판단 (LLM): chitchat / analysis / decision / workflow         manage_context + route_request
    5. decision이면 승인 요청(pending_approval) 작성                     check_approval 준비
       analysis이면 후속 질문 재작성 → 담당 Agent 선택                    route_request
       workflow이면 워크플로 run 시작 → workflow 노드                     manage_workflow (agent_system/workflows/)
    6. 다음 노드는 조건부 엣지가 결정                                     manage_workflow
"""

import re
from threading import Lock

from langchain_core.messages import AIMessage, HumanMessage

from agent_system.orchestrator import settings
from agent_system.orchestrator.registry import AgentRequest, normalize_agent_result
from agent_system.common_tools.report_export import analysis_log as report_log
from agent_system.orchestrator.tools import approval, context, intent, report, routing, workflow
from agent_system.orchestrator.tools.conversation_state import normalize_updates, update_conversation_state

# 이번 턴의 Agent 결과 전체(DataFrame 포함). 기억(checkpoint)에는 넣지 않고
# run_turn()이 꺼내 Slack Delivery에 넘깁니다.
TURN_RESULTS: dict = {}
_TURN_LOCK = Lock()


def store_turn_result(turn_id: str, result: dict):
    with _TURN_LOCK:
        TURN_RESULTS[turn_id] = result


def pop_turn_result(turn_id: str):
    with _TURN_LOCK:
        return TURN_RESULTS.pop(turn_id, None)


# =========================================================
# Orchestrator Node
# =========================================================

def orchestrator_node(state) -> dict:

    # CART_SIGNAL_PROGRESS_V1
    from agent_system.orchestrator.tools.progress_status import emit_progress
    emit_progress("🧭 요청 분석 및 담당 Agent 선택 중...")
    print("[Orchestrator]")

    messages = list(state.get("messages") or [])
    question = state.get("user_question") or (messages[-1].content if messages else "")
    history = messages[:-1]  # 마지막 메시지는 이번 질문
    last_analysis = state.get("last_analysis") or {}
    campaign = state.get("campaign") or {}
    pending = state.get("pending_approval") or {}
    run = state.get("workflow_run") or {}
    now = context._now()

    # ── 기억 정리 · 자동 리셋 (확정값 campaign은 settings.KEEP_CAMPAIGN_ON_RESET이면 유지)
    trim = context.trim_messages(messages)
    was_reset = context.should_auto_reset(state.get("last_activity_at", ""), now)
    common = {"last_activity_at": now.isoformat(), "was_reset": was_reset}
    if was_reset:
        print(f"[대화 자동 리셋] 마지막 메시지 후 {settings.AUTO_RESET_HOURS}시간 이상 경과 → 새 대화로 시작")
        trim = context.remove_all(history)
        history, last_analysis = [], {}
        common["last_analysis"] = {}
        report_log.remove_files(state.get("analysis_log") or [])
        common["analysis_log"] = []          # 보고서 재료도 새 대화에서 다시 모음
        if not getattr(settings, "KEEP_CAMPAIGN_ON_RESET", True):
            campaign, pending = {}, {}
            common.update(campaign={}, pending_approval={})

    # R29: 소구점 재표시는 승인·워크플로 상태를 변경하지 않는 읽기 전용 요청이다.
    # LLM 및 13단계 번호 선택보다 먼저 처리한다.
    if re.fullmatch(r"\s*(?:소구점|판매\s*포인트|마케팅\s*소구점)(?:\s*(?:분석|후보|추천))?\s*(?:다시\s*(?:보여\s*줘|보여주세요|알려\s*줘|출력\s*해줘)|재표시|다시\s*분석\s*해줘)\s*[.!]?\s*", str(question), re.I):
        from agent_system.orchestrator.tools.campaign_selection import (
            replay_selling_point_report, build_selling_point_evidence,
        )
        summary = run.get('campaign_summary') or {}
        replay_ok = False
        if pending:
            notice = '진행 중인 승인 요청을 먼저 처리하거나 취소해 주세요. 기존 승인 상태는 유지됩니다.'
        elif run.get('workflow') != 'trend_planning' or not summary:
            notice = '현재 대화에 저장된 소구점 분석 결과가 없습니다. 기획전 상품 구성 승인 후 분석해 주세요.'
        else:
            try:
                notice = replay_selling_point_report(summary, campaign, run.get('category_market'))
                evidence = build_selling_point_evidence(summary)
                store_turn_result(state['turn_id'], {
                    'turn_type': 'analysis', 'replay_delivery': True, 'final_answer': notice,
                    'data_sources': ['hm', 'musinsa'], 'data_purpose': '소구점 기존 근거 재표시',
                    **evidence,
                })
                replay_ok = True
            except (ValueError, OSError, RuntimeError) as exc:
                notice = f'저장된 소구점 근거를 재표시할 수 없습니다: {exc}'
        return {**common, 'turn_type': 'chitchat', 'reply': notice,
                'standalone_question': '', 'route': '', 'route_reason': '',
                'messages': trim + [AIMessage(content=notice, name='orchestrator')]}

    # R42.6: 고객 연령 변경 및 기존 전체 모집단 결과의 명시적 재선정.
    from agent_system.orchestrator.tools import audience_scope_r426 as audience426
    from agent_system.orchestrator.tools import campaign_target_r39 as target426
    age_change = audience426.age_change_request(question)
    reselect = audience426.reselection_request(question)
    confirm426 = audience426.confirmation_request(question)
    old_target = run.get('campaign_target_r39') or {}
    stale_target = bool(old_target and not audience426.current_target(old_target, campaign, run))
    # 이전 R39 검토 세션의 기존 마이그레이션 분기는 유지한다.
    stale_r40 = stale_target and old_target.get('policy') == 'r40_v1'
    scoped_legacy = stale_target and bool(audience426.effective_scope(run))
    if (run.get('workflow') == 'trend_planning' and run.get('status') not in ('cancelled', None)
            and (age_change or reselect or confirm426 or ((stale_r40 or scoped_legacy) and target426.target_request(question)))):
        try:
            if pending:
                raise ValueError('다른 승인 요청을 먼저 처리하거나 취소해 주세요.')
            from agent_system.orchestrator.tools.category_planning.analysis_scope_r427 import plan_scope
            if age_change:
                requested_age = audience426.parse_age(question)
                if requested_age != audience426.effective_scope(run):
                    raise ValueError('연령 변경은 시장·상품·고객 분석 전체에 영향을 줍니다. 기존 기획전은 보존하고 새 분석 시작 후 새 연령을 포함한 기획전 요청을 입력해 주세요.')
            if run.get('period_plan') and plan_scope(run['period_plan']) != audience426.require_scope(run):
                raise ValueError('기존 시장·상품 분석과 고객 연령이 다릅니다. 새 분석을 시작해 주세요.')
            if not target426.approved(run):
                raise ValueError('고객 재선정 전에 현재 기획전의 상품·페이지 승인부터 완료해 주세요.')
            from agent_system.workflows.registry import get_workflow
            step17 = next(i for i, (_, step) in enumerate(get_workflow('trend_planning').steps()) if step.no == 17)
            if confirm426:
                proposal426 = run.get('audience_reselect_pending') or {}
                if not proposal426 or proposal426.get('fingerprint') != target426.fingerprint(campaign,run):
                    raise ValueError('현재 기획전의 재선정 요청이 없습니다. 다시 요청해 주세요.')
                if str(question).strip().endswith('취소'):
                    revised_run = dict(run)
                    revised_run.pop('audience_reselect_pending',None)
                    notice426 = '재선정을 취소했습니다. 기존 고객 선정과 발송 이력은 유지됩니다.'
                    return {**common,'turn_type':'chitchat','reply':notice426,'standalone_question':'','route':'',
                            'workflow_run':revised_run,'messages':trim+[AIMessage(content=notice426,name='orchestrator')]}
                if proposal426.get('scope') != audience426.require_scope(run):
                    raise ValueError('이전 연령 변경 요청은 새 분석이 필요합니다. 현재 재선정 요청을 다시 생성해 주세요.')
                from agent_system.orchestrator.tools import campaign_target_r40 as target40
                try:
                    revised_run,revised_campaign = audience426.prepare_reselection(run,campaign,proposal426['scope'],step17,target40.score_campaign,target426.ROOT)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('R426 고객 재선정 실패')
                    raise ValueError('고객 재선정에 실패했습니다. 기존 상태와 파일은 보존했습니다. 모델·DB를 확인한 뒤 재선정 승인을 다시 요청해 주세요.')
                return {**common,'turn_type':'workflow','reply':'','standalone_question':'','route':'',
                        'workflow_run':revised_run,'campaign':revised_campaign,'messages':trim}
            if age_change:
                scope426 = audience426.parse_age(question)
            else:
                scope426 = audience426.require_scope(run)
            revised_run = dict(run, audience_reselect_pending={'scope':scope426,'fingerprint':target426.fingerprint(campaign,run)})
            notice426 = ('고객 타깃 재선정 요청: '+audience426.label(scope426)+' (기존 시장·상품 분석 연령 유지). '
                         '새 분석이 성공하면 기존 타깃·고객군 문구·보고서·발송 승인이 무효화됩니다. 기존 파일과 발송 이력은 보존합니다. '
                         '진행하려면 “고객 타깃 재선정 승인”, 취소하려면 “고객 타깃 재선정 취소”라고 입력해 주세요.')
            return {**common,'turn_type':'chitchat','reply':notice426,'standalone_question':'','route':'',
                    'workflow_run':revised_run,'messages':trim+[AIMessage(content=notice426,name='orchestrator')]}
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            notice426 = str(exc)
            return {**common,'turn_type':'chitchat','reply':notice426,'standalone_question':'','route':'',
                    'messages':trim+[AIMessage(content=notice426,name='orchestrator')]}
    if stale_target and (stale_r40 or scoped_legacy) and (target426.target_result_request(question) or str(question).startswith('__cart_target_review__:')):
        notice426 = '이전 고객 선정은 현재 연령 조건으로 검증되지 않았습니다. “고객 타깃 다시 선정해줘”라고 요청해 주세요.'
        return {**common,'turn_type':'chitchat','reply':notice426,'standalone_question':'','route':'',
                'messages':trim+[AIMessage(content=notice426,name='orchestrator')]}

    # R41: 승인된 R40 타깃의 고객군별 문구 전용 경로.
    from agent_system.orchestrator.tools import campaign_copy_r41 as copy41
    if copy41.is_request(question) and ((run.get('campaign_target_r39') or {}).get('policy') == 'r40_v1' or str(question).startswith(copy41.PREFIX) or '고객군별' in str(question) or '세그먼트별' in str(question)):
        extra41 = {}
        try:
            package41 = copy41.handle(question, run, campaign, pending)
            extra41['workflow_run'] = dict(run, segment_copy_r41=package41)
            notice41 = copy41.render(package41)
        except (ValueError, TypeError, KeyError) as exc:
            notice41 = str(exc)
        return {**common, **extra41, 'turn_type':'chitchat', 'reply':notice41, 'standalone_question':'', 'route':'', 'messages':trim+[AIMessage(content=notice41,name='orchestrator')]}

    # CART_SIGNAL_COPY_REPLAY_R33: LLM/16단계 선택보다 먼저 현재 저장된 문구를 읽기 전용 재표시.
    from agent_system.orchestrator.tools.copy_replay_r33 import (
        is_copy_replay_request, copy_replay_notice,
    )
    if is_copy_replay_request(question):
        notice, _shown = copy_replay_notice(run, campaign, pending)
        return {**common, 'turn_type': 'chitchat', 'reply': notice,
                'standalone_question': '', 'route': '', 'route_reason': '',
                'messages': trim + [AIMessage(content=notice, name='orchestrator')]}

    # R38: 시안 승인만으로 타깃 모델을 호출하지 않는다.
    from agent_system.orchestrator.tools.target_start_r38 import (
        waiting_for_target_start, target_start_intent, start_targeting, WAIT_NOTICE,
    )
    from agent_system.orchestrator.tools import campaign_target_r39 as target39
    from agent_system.orchestrator.tools import campaign_page as page39
    if target39.approved(run) and page39.is_page_preview_replay_request(question):
        from agent_system.common_tools.report_export.builders import _approved_page_preview_blocks
        from agent_system.common_tools.report_export.document import Image
        import json
        try:
            page_value = json.loads((campaign.get('values') or {}).get('page') or '{}')
            blocks = _approved_page_preview_blocks(run, page_value)
            image = next((block for block in blocks if isinstance(block, Image)), None)
            if image is None:
                raise ValueError('승인 이미지 검증 실패')
            notice = f"확정된 기획전 페이지 틀입니다.\n승인 완료 · {(run['page_preview'])['revision']}차 시안"
            if state.get('turn_id'):
                store_turn_result(state['turn_id'], {'turn_type':'chitchat', 'replay_delivery':True, 'final_answer':notice, 'visualization_path':image.path})
        except (ValueError, TypeError, KeyError, OSError):
            notice = '승인된 페이지 이미지를 확인하지 못했습니다. 기존 승인 상태는 유지됩니다.'
        return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
    # R42.1: 명시적 신규 선정은 미승인 R39 제안을 재사용하지 않는다.
    if getattr(settings, 'ENABLE_WORKFLOWS', True) and target39.active(run):
        from agent_system.orchestrator.tools.target_start_r38 import migrate_legacy_review
        from agent_system.workflows.registry import get_workflow
        migrated, migration_notice = migrate_legacy_review(
            run, campaign, pending, question, get_workflow('trend_planning').steps())
        if migrated is not None:
            return {**common, 'turn_type':'workflow', 'reply':'', 'standalone_question':'',
                    'route':'', 'workflow_run':migrated, 'messages':trim}
        if migration_notice:
            return {**common, 'turn_type':'chitchat', 'reply':migration_notice,
                    'standalone_question':'', 'route':'',
                    'messages':trim+[AIMessage(content=migration_notice,name='orchestrator')]}
    if target39.active(run):
        saved39 = run.get('campaign_target_r39') or {}
        if saved39.get('status') in ('review', 'confirmed'):
            notice = ''
            try:
                command39 = str(question).removeprefix('__cart_target_review__:') if str(question).startswith('__cart_target_review__:') else ''
                k39 = target39.parse_k(question)
                if command39:
                    if pending:
                        raise ValueError('다른 승인 요청을 먼저 처리해 주세요.')
                    selected39 = target39.review_selection(saved39, command39, campaign, run)
                    new_run39 = dict(run, campaign_target_r39=selected39)
                    extra39 = {'workflow_run': new_run39}
                    if command39 == 'approve':
                        new_run39.update(status='paused', waiting_key='', report_draft={}, segment_copy_r41={})
                        from agent_system.workflows.registry import get_workflow
                        new_run39['step_index'] = next(i for i, (_, step) in enumerate(get_workflow('trend_planning').steps()) if step.no == 23)
                        notice = target39.confirmed_notice(selected39)
                        extra39['campaign'] = update_conversation_state(campaign, {'target': f"{target39.selection_basis(selected39)} / 중복 제거 {selected39['total']:,}명"}, source='user')
                    elif command39 == 'evidence':
                        notice = target39.proposal_notice(selected39, evidence=True)
                    else:
                        notice = target39.scoring_notice(selected39) if selected39.get('policy') == 'r40_v1' else '기존 모델 분석 결과를 유지합니다. 선정 규모를 다시 입력해 주세요.\n' + target39.scoring_notice(selected39)
                    return {**common, **extra39, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'',
                            'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
                if k39 is not None:
                    if saved39.get('policy') == 'r40_v1':
                        raise ValueError('R40은 전체 모집단 기준 고정 비율 정책입니다. 임의 인원 변경은 지원하지 않습니다.')
                    if pending:
                        raise ValueError('다른 승인 요청을 먼저 처리해 주세요.')
                    if saved39.get('status') == 'confirmed':
                        raise ValueError('이미 고객 타깃이 확정되었습니다. 기존 선정 결과는 유지합니다.')
                    proposed39 = target39.propose_selection(saved39, k39, campaign, run)
                    notice = target39.proposal_notice(proposed39)
                    return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'',
                            'workflow_run':dict(run, campaign_target_r39=proposed39), 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
                if (saved39.get('policy') == 'r40_v1' and saved39.get('status') == 'review' and not saved39.get('proposal')
                        and target39.target_request(question)):
                    if pending:
                        raise ValueError('다른 승인 요청을 먼저 처리해 주세요.')
                    from agent_system.orchestrator.tools.campaign_target_r40 import propose
                    saved39 = propose(saved39, campaign, run, target39.ROOT)
                    notice = target39.proposal_notice(saved39)
                    return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'',
                            'workflow_run':dict(run, campaign_target_r39=saved39), 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
                if saved39.get('status') == 'review' and re.fullmatch(r'\s*(?:승인|반려|확정|승인합니다|확정합니다|고객 타깃 승인)[.!]?\s*', str(question)):
                    notice = target39.proposal_notice(saved39) if saved39.get('proposal') else target39.scoring_notice(saved39)
                elif target39.target_request(question) or target39.target_result_request(question) or target_start_intent(question) == 'continue':
                    notice = target39.confirmed_notice(saved39) if saved39['status'] == 'confirmed' else (target39.proposal_notice(saved39) if saved39.get('proposal') else target39.scoring_notice(saved39))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                notice = str(exc) if isinstance(exc, ValueError) else '저장된 고객 점수 확인에 실패했습니다.'
            if notice:
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
    _target_intent = target_start_intent(question) or ('target' if target39.target_request(question) else '')

    _target_wait = waiting_for_target_start(run)
    if (target39.active(run) and run.get('status') == 'paused' and not run.get('campaign_target_r39')
            and _target_intent == 'target' and not _target_wait):
        # 이전 버전에서 멈춘 승인 Run도 명시적 요청 시 신규 모델 분석으로 복구.
        run = dict(run, target_start_pending=True)
        _target_wait = True
    _preview_status = (run.get('page_preview') or {}).get('status')
    if (getattr(settings, 'ENABLE_WORKFLOWS', True) and run.get('workflow') == 'trend_planning'):
        _target_notice = ''
        if _target_wait and _target_intent:
            _next_run, _target_notice = start_targeting(run, campaign, pending, question if target_start_intent(question) else "고객 타깃 선정해줘")
            if _next_run is not None:
                return {**common, 'turn_type':'workflow', 'reply':'', 'standalone_question':'',
                        'route':'', 'workflow_run':_next_run, 'messages':trim}
        elif (target39.in_campaign(run) and _preview_status != 'approved' and _target_intent == 'target'):
            _target_notice = '먼저 기획전 페이지 틀을 승인해 주세요.'
        elif _preview_status == 'approved' and str(question).strip() == '__cart_page_preview__:approve':
            _target_notice = WAIT_NOTICE if _target_wait else '이미 승인된 페이지 틀입니다.'
        elif (_preview_status == 'approved' and run.get('target_start_pending') is False
              and _target_intent == 'target' and run.get('status') not in ('done', 'cancelled')):
            _target_notice = '고객 타깃 분석은 이미 시작했습니다. 현재 분석 결과를 확인하거나 이어서 진행해 주세요.'
        if _target_notice:
            return {**common, 'turn_type':'chitchat', 'reply':_target_notice, 'standalone_question':'',
                    'route':'', 'messages':trim+[AIMessage(content=_target_notice,name='orchestrator')]}

    # R35: 확정 카피 이후 별도의 페이지 이미지 검토 단계.
    # 기존 소구점·카피를 보존하고, 페이지 시안 수정/되돌리기/승인만 수행한다.
    preview = run.get('page_preview') or {}
    if run.get('workflow') == 'trend_planning' and preview and preview.get('status') in ('review', 'rejected'):
        from copy import deepcopy
        from agent_system.orchestrator.tools import campaign_page as page_tool
        command = str(question or '').strip()
        button_command = command.split(':', 1)[1] if command.startswith('__cart_page_preview__:') else ''
        replay = page_tool.is_page_preview_replay_request(command)
        regenerate = (button_command == 'regenerate' or page_tool.is_page_preview_regenerate_request(command))
        undo = (button_command == 'undo' or (not replay and page_tool.is_page_preview_undo_request(command)))
        reject = button_command == 'reject'
        prompt_edit = button_command == 'edit'
        approve = button_command == 'approve'
        edit = ('' if button_command or replay or regenerate or undo else page_tool.parse_page_preview_edit_request(command))
        if approve or reject or prompt_edit or regenerate or replay or undo or edit:
            if pending:
                notice = '진행 중인 다른 승인 요청을 먼저 처리해 주세요. 페이지 시안은 변경하지 않았습니다.'
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'',
                        'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            if prompt_edit:
                notice = '페이지 틀에서 바꿀 부분을 채팅으로 알려주세요. 예: 상단 배너를 따뜻한 톤으로 바꿔줘, 상품 카드 강조해줘.'
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                        'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            if approve:
                if preview.get('status') != 'review':
                    notice = '반려된 페이지 틀은 수정·재생성한 다음 새 시안을 승인해 주세요.'
                    return {**common, 'turn_type':'chitchat','reply':notice,'standalone_question':'',
                            'route':'','messages':trim+[AIMessage(content=notice,name='orchestrator')]}
                from agent_system.workflows.registry import get_workflow
                wf = get_workflow('trend_planning')
                step17 = next(i for i, (_, step) in enumerate(wf.steps()) if step.no == 17)
                try:
                    migrated = page_tool.migrate_preview_paths(preview, run['run_id'])
                except (ValueError, RuntimeError, OSError, KeyError, TypeError):
                    notice = '페이지 시안을 확인하지 못했습니다. 페이지 틀 다시 보여줘를 입력해 주세요.'
                    return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                            'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
                from agent_system.orchestrator.tools.target_start_r38 import approve_preview, WAIT_NOTICE
                new_run = approve_preview(run, migrated, step17)
                notice = WAIT_NOTICE
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                        'route':'', 'workflow_run':new_run,
                        'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            try:
                preview = page_tool.migrate_preview_paths(preview, run['run_id'])
                new_run = deepcopy(run)
                new_run['page_preview'] = preview
                if regenerate:
                    new_run['page_preview'] = page_tool.regenerate_preview(preview, run['run_id'], run.get('copy_package') or {}, campaign)
                    notice_prefix = '페이지 틀을 새 스타일로 다시 만들었습니다.'
                elif undo:
                    new_run['page_preview'] = page_tool.undo_preview(preview)
                    notice_prefix = '직전 페이지 틀 수정만 되돌렸습니다.'
                elif edit:
                    new_run['page_preview'] = page_tool.revise_preview(preview, run['run_id'], run.get('copy_package') or {}, campaign, edit)
                    notice_prefix = '요청한 페이지 구성 변경을 반영했습니다.'
                else:
                    if reject:
                        new_run['page_preview']['status'] = 'rejected'
                        new_run['page_preview']['button_epoch'] = int(preview.get('button_epoch') or 0) + 1
                    notice_prefix = '기존 페이지 틀을 다시 보여드립니다.' if replay else '페이지 틀을 반려했습니다. 수정 요청 또는 재생성 후 다시 승인해 주세요.'
                shown = new_run['page_preview']
                notice = page_tool.preview_message(shown, notice_prefix)
                current_path = (shown.get('current') or {}).get('image_path') or ''
                # 되돌리기 및 재표시는 기존 파일을 그대로 쓰며, 없을 때는 동일 사양으로만 복구한다.
                from pathlib import Path as _PreviewPath
                if not _PreviewPath(current_path).is_file():
                    page_tool.render_preview_image(shown['current']['spec'], _PreviewPath(current_path))
                # R35 Hotfix: turn_id가 있는 경우에만 결과 저장
                turn_id = state.get('turn_id')
                if turn_id:
                    store_turn_result(turn_id, {'turn_type':'chitchat', 'replay_delivery':True,
                                      'final_answer':notice, 'visualization_path':current_path,
                                      'page_preview_revision':shown['revision']})
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                        'route':'', 'workflow_run':new_run,
                        'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
                notice = f'페이지 시안을 변경하지 않았습니다: {exc}'
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                        'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}

    # 최종 승인 이후에도 동일 Run의 분석/소구점은 남기고 카피 단계만 재진입.
    if str(question).strip() in ('대표 문구 단계로 되돌리기', '카피 단계로 되돌리기'):
        from copy import deepcopy
        from agent_system.workflows.registry import get_workflow
        from agent_system.orchestrator.tools.campaign_copy import generate_candidates, validate_candidates, render_options
        from agent_system.workflows import base
        if run.get('workflow') != 'trend_planning' or not (campaign.get('values') or {}).get('selling_points'):
            notice = '소구점이 확정된 기획전 Run에서만 카피 단계로 돌아갈 수 있습니다.'
        elif pending:
            notice = '진행 중인 승인 요청을 먼저 취소해 주세요.'
        else:
            try:
                summary = run.get('campaign_summary') or {}
                clean = validate_candidates(generate_candidates(summary, campaign), summary, campaign)
                wf = get_workflow('trend_planning')
                step16 = next(i for i, (_, item) in enumerate(wf.steps()) if item.no == 16)
                new_run = deepcopy(run)
                new_run.update(step_index=step16, status='waiting_input', waiting_key='page',
                               copy_package=clean, copy_review_phase='', report_draft={}, page_preview={}, target_start_pending=False, campaign_target_r39={})
                new_campaign = deepcopy(campaign)
                new_campaign['values'].pop('page', None)
                new_campaign['values'].pop('report', None)
                new_campaign['values'].pop('target', None)
                new_campaign['log'] = [entry for entry in new_campaign.get('log', [])
                                       if entry.get('key') not in ('page','report','target')]
                notice = ('대표 문구 승인과 보고서 초안을 초기화했습니다. 이전 분석·소구점은 유지됩니다.\n\n'
                          + render_options(clean))
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                        'route':'', 'campaign':new_campaign, 'workflow_run':new_run,
                        'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                notice = f'카피 단계로 돌아가지 못했습니다: {exc}'
        return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}

    # CART_SIGNAL_CATEGORY_ROUTE_R23: 정확한 상품군 상세 분석 요청은 LLM/일반 SQL보다 우선한다.
    # 조회·시각화·CSV는 기존 trend_planning 5~9단계만 사용한다.
    from agent_system.orchestrator.tools.category_review_route import (
        category_review_request, workflow_category_ready, category_route_notice,
    )
    _review_category = category_review_request(question)
    if _review_category is not None:
        if (getattr(settings, "ENABLE_WORKFLOWS", True)
                and workflow_category_ready(run)
                and not pending
                and run.get("waiting_key") != "category_decision"):
            _text, _changed_run, _resume = workflow.handle_command(question, run, campaign)
            if _resume and _changed_run is not None:
                return {**common, "turn_type": "workflow", "reply": "", "standalone_question": "",
                        "route": "", "route_reason": "", "conversation_reason": "상품군 상세 분석 우선 라우팅",
                        "is_followup": False, "workflow_run": _changed_run, "messages": trim}
            if _text:
                _notice = _text
                _update = {**common, "turn_type": "chitchat", "reply": _notice,
                           "standalone_question": "", "route": "", "route_reason": "",
                           "conversation_reason": "상품군 상세 분석 단계 안내", "is_followup": False,
                           "messages": trim + [AIMessage(content=_notice, name="orchestrator")]}
                if _changed_run is not None:
                    _update["workflow_run"] = _changed_run
                return _update
        _notice = ("진행 중인 승인 요청을 먼저 처리해 주세요. "
                   "이전 승인값으로 상품군 분석을 대체하지 않습니다." if pending
                   else category_route_notice(_review_category, run))
        return {**common, "turn_type": "chitchat", "reply": _notice,
                "standalone_question": "", "route": "", "route_reason": "",
                "conversation_reason": "상품군 상세 분석의 부정확한 SQL 차단", "is_followup": False,
                "messages": trim + [AIMessage(content=_notice, name="orchestrator")]}

    # 확정 상품군의 유형 비교를 자유 SQL 라우터로 보내지 않는다.
    # 과거 일반 승인으로 campaign만 갱신된 Run은 승인 이력을 검증해 안전하게 복구한다.
    from agent_system.orchestrator.tools.subtype_route_guard import (
        is_subtype_analysis_request, matches_current_approval,
        confirm_category_run, subtype_status_reply,
    )
    if (run.get("workflow") == "trend_planning"
            and run.get("status") == "waiting_input"
            and is_subtype_analysis_request(question)):
        synced = None
        if matches_current_approval(run, campaign):
            synced = confirm_category_run(run, str(run.get("review_category") or ""))
        active_run = synced or run
        notice = subtype_status_reply(active_run)
        answer = {**common, "turn_type": "chitchat", "reply": notice,
                  "standalone_question": "", "route": "", "route_reason": "",
                  "conversation_reason": "상품군 유형 비교 경로 보호", "is_followup": False,
                  "messages": trim + [AIMessage(content=notice, name="orchestrator")]}
        if synced is not None:
            answer["workflow_run"] = synced
        return answer

    # ── manage_workflow: 진행 중인 워크플로 명령 (규칙, LLM 없음)
    if getattr(settings, "ENABLE_WORKFLOWS", True) and not (target39.active(run) and report.detect(question)):
        # CART_SIGNAL_NATURAL_CATEGORY_APPROVAL_V1
        from agent_system.orchestrator.tools.category_approval import selected_category

        # 10단계 확정 요청이 재분석 처리로 넘어가지 않도록 분리
        if (
            run.get("workflow") == "trend_planning"
            and run.get("status") == "waiting_input"
            and run.get("waiting_key") == "products"
            and selected_category(question) is not None
        ):
            text, changed_run, resume = "", None, False
        else:
            text, changed_run, resume = workflow.handle_command(
                question, run, campaign
            )
        if resume and _target_wait:
            return {**common, 'turn_type':'chitchat', 'reply':WAIT_NOTICE, 'standalone_question':'',
                    'route':'', 'messages':trim+[AIMessage(content=WAIT_NOTICE,name='orchestrator')]}
        if resume:
            return {**common, "turn_type": "workflow", "reply": "", "standalone_question": "", "route": "",
                    "route_reason": "", "conversation_reason": "워크플로 계속", "is_followup": False,
                    "workflow_run": changed_run, "messages": trim}
        if text:
            update = {**common, "turn_type": "chitchat", "reply": text, "standalone_question": "", "route": "",
                      "route_reason": "", "conversation_reason": "워크플로 명령", "is_followup": False,
                      "messages": trim + [AIMessage(content=text, name="orchestrator")]}
            if changed_run is not None:
                update["workflow_run"] = changed_run
            return update

    # CART_SIGNAL_STEP10_VERIFIED_APPROVAL_V1: 현재 검증 후보만 승인 제안
    if (getattr(settings, "ENABLE_WORKFLOWS", True)
            and run.get("workflow") == "trend_planning"
            and run.get("status") == "waiting_input"
            and run.get("waiting_key") == "products"):
        from agent_system.orchestrator.tools.workflow import current_step
        from agent_system.orchestrator.tools.category_approval import selected_ids, validated_updates, selected_category
        from agent_system.orchestrator.tools.category_planning.review_selection import CATEGORIES
        _, _, current = current_step(run)
        chosen = selected_category(question)
        ids = selected_ids(question)
        if run.get("subtype_flow") and re.fullmatch(r"(?:좋아[,.]?\s*)?추천\s*상품(?:으?로|대로)\s*(?:할게|갈게|확정할게|선택할게)[.! ]*", question.strip()):
            chosen = run.get("review_category")
        if current is not None and current.no == 10 and (chosen in CATEGORIES or ids is not None):
            category = chosen if chosen in CATEGORIES else str(run.get("review_category") or "")
            try:
                updates = validated_updates(run, category, ids)
            except ValueError as exc:
                notice = str(exc)
                return {**common, "turn_type": "chitchat", "reply": notice, "standalone_question": "",
                        "route": "", "route_reason": "", "is_followup": False,
                        "messages": trim + [AIMessage(content=notice, name="orchestrator")]}
            request = approval.build_approval_request(updates, title=f"{category} 카테고리 및 주력 상품 승인", source="user")
            request["question"] = (
                f"[10단계 확정 제안] 카테고리: {category}\n"
                f"8단계 검증 상품 ID: {updates['products']}\n"
                f"기획전 테마 제안: {updates['theme']}\n" +
                (f"운영 일정: {updates['schedule']}\n" if updates.get("schedule") else "") +
                (f"선택 유형: {updates.get('subtype')} / 색상: {updates.get('colors')} (상품 설명 규칙 매칭, 성능 미검증)\n" if run.get("subtype_flow") else "검증된 과거 확정 채널 구매 기록 기반 후보이며 테마의 실제 상품 여부는 미검증입니다.\n") +
                "확정하려면 [승인], 아니면 [반려]를 눌러 주세요."
            )
            return {**common, "turn_type": "approval_request", "reply": request["question"],
                    "standalone_question": "", "route": "", "route_reason": "", "is_followup": False,
                    "pending_approval": request,
                    "messages": trim + [AIMessage(content=request["question"], name="orchestrator")]}

    # 13단계 입력은 구조화된 검증 경로만 사용 (일반 LLM 결정 저장 우회 방지).
    if (run.get("workflow") == "trend_planning" and run.get("status") == "waiting_input"
            and run.get("waiting_key") == "selling_points"):
        from agent_system.orchestrator.tools.campaign_selection import guide, updates as selection_updates
        summary = run.get("campaign_summary")
        try:
            if pending: raise ValueError("기존 승인 요청을 먼저 취소해 주세요.")
            if not summary: raise ValueError("워크플로 12단계 재실행을 입력하세요.")
            selected = selection_updates(question, summary)
        except ValueError as exc:
            notice = str(exc) + ("\n" + guide(summary) if summary else "")
            return {**common, "turn_type": "chitchat", "reply": notice, "standalone_question": "", "route": "",
                    "messages": trim + [AIMessage(content=notice, name="orchestrator")]}
        request = approval.build_approval_request(selected, title="13단계 기획전 구성 승인", source="user")
        from agent_system.orchestrator.tools.campaign_copy import approval_brief
        request["question"] = approval_brief(summary, selected["selling_points"], campaign, run.get("category_market"))
        request["step13_input"] = question
        return {**common, "turn_type": "approval_request", "reply": request["question"], "standalone_question": "", "route": "",
                "pending_approval": request, "messages": trim + [AIMessage(content=request["question"], name="orchestrator")]}

    # 16단계: 자연어로 대표 문구 하나를 선택하고, 검증된 후보에 한해 승인 요청.
    if (run.get("workflow") == "trend_planning" and run.get("status") == "waiting_input"
            and run.get("waiting_key") == "page"):
        from agent_system.orchestrator.tools.campaign_copy import parse_copy_choice, selected_copy_value
        package = run.get("copy_package") or {}
        # 과거 버전의 수정 이력과 현재 검증 규칙이 충돌하면, 사용자의 명시적 요청으로
        # 카피만 초기 상태로 복원한다. 분석·소구점 승인 및 워크플로 Run은 유지한다.
        from agent_system.orchestrator.tools.copy_revision import is_copy_reset_request
        if is_copy_reset_request(question):
            try:
                from copy import deepcopy
                from agent_system.orchestrator.tools.campaign_copy import (
                    generate_candidates, validate_candidates, render_options,
                )
                summary = run.get('campaign_summary') or {}
                clean = validate_candidates(generate_candidates(summary, campaign), summary, campaign)
                new_run = deepcopy(run)
                new_run['copy_package'] = clean
                new_run['copy_review_phase'] = ''
                notice = ('카피 수정 이력을 초기화했습니다. 기존 분석·소구점·기획전 주제는 유지됩니다. '
                          '기존 카피 수정 내용만 삭제하고 최초 3안으로 복구했습니다.\n\n'
                          + render_options(clean))
                return {**common, 'turn_type': 'chitchat', 'reply': notice,
                        'standalone_question': '', 'route': '', 'workflow_run': new_run,
                        'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
            except (ValueError, TypeError, KeyError) as exc:
                notice = f'카피만 초기화하지 못했습니다: {exc}'
                return {**common, 'turn_type': 'chitchat', 'reply': notice,
                        'standalone_question': '', 'route': '',
                        'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
        # 단일 확정 UI: 카피 수정본을 별도로 승인하지 않고 1~3안 직접 확정한다.
        if question.startswith('__cart_copy_review__:'):
            from copy import deepcopy
            from agent_system.orchestrator.tools.campaign_copy import generate_candidates, validate_candidates, render_options
            from agent_system.orchestrator.tools.copy_revision import regenerate_copy, replay_copy_step
            command = question.split(':', 1)[1]
            if command == 'edit':
                new_run = deepcopy(run)
                new_run['copy_review_phase'] = ''
                notice = '추가 수정할 안 번호와 원하는 문체를 입력해 주세요.'
                return {**common, 'turn_type': 'chitchat', 'reply': notice,
                        'standalone_question': '', 'route': '', 'workflow_run': new_run,
                        'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
            if command == 'undo':
                if not package.get('revision'):
                    notice = '되돌릴 카피 수정 이력이 없습니다.'
                    return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                            'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
                try:
                    summary = run.get('campaign_summary') or {}
                    previous = generate_candidates(summary, campaign)
                    for step in (package.get('revision_steps') or [])[:-1]:
                        previous = replay_copy_step(previous, step)
                    previous = validate_candidates(previous, summary, campaign)
                    new_run = deepcopy(run)
                    new_run['copy_package'] = previous
                    new_run['copy_review_phase'] = 'review' if previous.get('revision') else ''
                    notice = '직전 카피 수정만 취소했습니다.\n\n' + render_options(previous)
                    return {**common, 'turn_type': 'chitchat', 'reply': notice,
                            'standalone_question': '', 'route': '', 'workflow_run': new_run,
                            'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
                except (ValueError, TypeError, KeyError) as exc:
                    notice = f'카피 되돌리기 실패: {exc}'
                    return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                            'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            if command in ('choose1', 'choose2', 'choose3'):
                question = command[-1] + '안으로 할게'
            else:
                notice = '사용할 수 없는 카피 버튼입니다.'
                return {**common, 'turn_type': 'chitchat', 'reply': notice,
                        'standalone_question': '', 'route': '',
                        'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
        # 수정 요청을 우선 분류한다. '3안 다듬어줘'를 기존 3안 승인으로 처리하지 않는다.
        from agent_system.orchestrator.tools.copy_revision import parse_revision_request
        feedback = parse_revision_request(question)
        choice = None if feedback else parse_copy_choice(question, package)
        # 이미 검증된 문구의 수정 요청은 새로운 3안을 만들고 같은 Run에 저장한다.
        # 승인 요청이 없는 상태에서만 재생성하며, 확정된 상품과 소구점은 변경하지 않는다.
        from agent_system.orchestrator.tools.copy_revision import parse_revision_request, regenerate_copy
        if feedback:
            try:
                from agent_system.orchestrator.tools.campaign_copy import validate_candidates, render_options
                from copy import deepcopy
                summary = run.get('campaign_summary') or {}
                revision = int(package.get('revision') or 0)
                from agent_system.orchestrator.tools.campaign_copy import generate_candidates
                original = package if revision else generate_candidates(summary, campaign)
                revised = regenerate_copy(original, feedback, revision)
                checked = validate_candidates(revised, summary, campaign)
                new_run = deepcopy(run)
                new_run['copy_package'] = checked
                new_run['copy_review_phase'] = 'review'
                targets = checked.get('revision_targets') or [1, 2, 3]
                notice = ('요청한 표현 조건으로 ' + ', '.join(f'{n}안' for n in targets)
                          + '을 다시 작성했습니다. 나머지 안은 유지했습니다.\n\n' + render_options(checked))
                return {**common, 'turn_type': 'chitchat', 'reply': notice, 'standalone_question': '',
                        'route': '', 'workflow_run': new_run,
                        'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
            except (ValueError, TypeError, KeyError) as exc:
                notice = (f'문구를 재생성하지 않았습니다: {exc} 기존 3안은 유지됩니다. '
                          '이전 수정 이력이 충돌한다면 \"카피 수정 이력 초기화\"를 입력해 카피 단계만 복구할 수 있습니다.')
                return {**common, 'turn_type': 'chitchat', 'reply': notice, 'standalone_question': '',
                        'route': '', 'messages': trim + [AIMessage(content=notice, name='orchestrator')]}
        if not choice:
            notice = "대표 문구 1개를 선택해 주세요. 예: '3번으로 할게' 또는 '첫 번째가 좋아'"
            return {**common, "turn_type": "chitchat", "reply": notice, "standalone_question": "", "route": "",
                    "messages": trim + [AIMessage(content=notice, name="orchestrator")]}
        # 재확인 팝업 없이 선택한 완성형 카피 1안을 한 번에 확정한다.
        # 선택 시에도 기존 approval 경로와 동일한 재현 검증 및 의미 검증을 수행한다.
        from copy import deepcopy
        from agent_system.orchestrator.tools.campaign_copy import validate_candidates
        from agent_system.validation.semantic_campaign_validator import review_campaign_copy
        try:
            summary = run.get('campaign_summary') or {}
            verified = validate_candidates({**package, 'status': 'draft'}, summary, campaign)
            review_campaign_copy(package, summary, campaign)
            if package.get('status') != 'verified' or verified != package:
                raise ValueError('선택한 카피의 근거 검증이 완료되지 않았습니다.')
            approved = selected_copy_value(choice, package)
            from agent_system.orchestrator.tools.campaign_page import create_initial_preview, preview_message
            new_run = deepcopy(run)
            generated = create_initial_preview(run['run_id'], choice, package, campaign)
            new_run.update(status='waiting_input', waiting_key='page',
                           copy_review_phase='', page_preview=generated, report_draft={})
            updated_campaign = update_conversation_state(campaign, {'page': approved}, source='user')
            result_text = preview_message(generated, '대표 문구 ' + str(choice['number']) + '안을 확정했습니다. 기획전 페이지 틀 미리보기를 생성했습니다.')
            # R35 Hotfix: turn_id가 있는 경우에만 결과 저장
            turn_id = state.get('turn_id')
            if turn_id:
                store_turn_result(turn_id, {'turn_type':'chitchat', 'replay_delivery':True,
                                  'final_answer':result_text,
                                  'visualization_path':generated['current']['image_path'],
                                  'page_preview_revision':generated['revision']})
            return {**common, 'turn_type': 'chitchat', 'reply': result_text,
                    'standalone_question': '', 'route': '', 'campaign': updated_campaign,
                    'workflow_run': new_run,
                    'messages': trim + [AIMessage(content=result_text, name='orchestrator')]}
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            notice = f'카피를 확정하지 않았습니다: {exc}'
            return {**common, 'turn_type': 'chitchat', 'reply': notice,
                    'standalone_question': '', 'route': '',
                    'messages': trim + [AIMessage(content=notice, name='orchestrator')]}

    # ── 보고서 요청: 일반 분석 보고서 / 기획전 실행 계획 보고서 (PDF + Word, 규칙 판단)
    report_kind = report.detect(question)
    if report_kind:
        if target39.active(run) or target39.delivery_allowed(run):
            if not target39.confirmed_for_report(run, campaign):
                notice = '현재 고객 연령 조건으로 타깃을 선정하고 승인해 주세요. 기존 결과라면 “고객 타깃 다시 선정해줘”라고 요청해 주세요.'
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            if (run.get('campaign_target_r39') or {}).get('policy') == 'r40_v1' and not copy41.approved(run, campaign):
                notice = '고객군별 마케팅 문구를 생성하고 승인해 주세요. “고객군별 마케팅 문구 생성해줘”라고 입력하면 검토안을 생성합니다.'
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            report_kind = 'campaign'
        if (run.get('workflow') == 'trend_planning'
                and (run.get('page_preview') or {}).get('status') != 'approved'
                and (run.get('copy_package') or {}).get('status') == 'verified'):
            notice = '페이지 틀을 승인하고 고객 타깃 선정을 마쳐 주세요. 기획전 보고서 초안은 아직 만들지 않습니다.'
            return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'',
                    'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
        if (run.get('workflow') == 'trend_planning' and (run.get('page_preview') or {}).get('status') == 'approved'
                and not (campaign.get('values') or {}).get('target')):
            notice = '고객 타깃이 아직 확정되지 않았습니다. 먼저 타깃 분석과 선정을 마쳐 주세요. 보고서 초안은 생성하지 않습니다.'
            return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'', 'route':'',
                    'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
        emit_progress("📄 Word 보고서 초안 작성 중...")
        made = report.handle_report_request(report_kind, [] if was_reset else state.get("analysis_log"),
                                            campaign, run)
        report_result = {"turn_type": "report", "final_answer": made["reply"],
                         "attachments": made["attachments"], "report_kind": report_kind,
                         "report_errors": made["errors"]}
        selected39 = target39.confirmed_for_report(run, campaign) if target39.delivery_allowed(run) else None
        if selected39 and made['attachments'] and not made['errors']:
            report_result['r39_report'] = dict(target39.email_summary(run, selected39), campaign_theme=str((campaign.get('values') or {}).get('subtype') or '기획전'))
            report_result['final_answer'] += '\n최종 Word를 이 스레드에 다시 첨부하면 PDF로 변환됩니다. 이후 과장·팀장·대리·사원 중 보고 대상을 지정해 주세요.'
        store_turn_result(state["turn_id"], report_result)
        return {**common, "turn_type": "report", "reply": made["reply"], "standalone_question": "", "route": "",
                "route_reason": "", "conversation_reason": f"보고서 요청 ({report_kind})", "is_followup": False,
                "messages": trim + [AIMessage(content=made["reply"], name="orchestrator")]}

    # ── manage_context + route_request(1): 대화 판단 (LLM)
    try:
        result = intent.get_intent_agent(state.get("llm_config")).invoke(
            {"messages": [HumanMessage(content=context.manage_context(question, history, last_analysis,
                                                                     campaign, pending, now=now,
                                                                     workflow_status=workflow.run_status_text(
                                                                         run, campaign)))]},
            config={"recursion_limit": 10},
        )
        decision = result.get("structured_response")
        if decision is None:
            raise ValueError("대화 판단 결과가 없습니다.")
        intent_name = decision.intent
        reply = (decision.reply or "").strip()
        standalone = (decision.standalone_question or "").strip() or question
        is_followup = bool(decision.is_followup)
        reason = decision.reason
        updates = normalize_updates(getattr(decision, "updates", []) or [])
        workflow_name = str(getattr(decision, "workflow", "") or "").strip()
    except Exception as exc:
        # 대화 판단에 실패해도 요청은 원문 그대로 처리합니다.
        print(f"[대화 판단 오류] {exc!r} → 원문으로 분석 진행")
        intent_name, reply, standalone, is_followup, updates = "analysis", "", question, False, {}
        workflow_name = ""
        reason = f"대화 판단 실패: {type(exc).__name__}"

    if intent_name == "decision" and run.get("period_plan") and "schedule" in updates and run.get("status") not in ("done", "cancelled"):
        from agent_system.orchestrator.tools.category_planning.period_context import schedule_matches
        if not schedule_matches(run, updates["schedule"]):
            notice = "현재 Run의 운영 일정은 분석 근거와 함께 고정되어 있습니다. 변경하려면 워크플로를 취소하고 새 분석을 시작해 주세요."
            return {**common, "turn_type": "chitchat", "reply": notice, "standalone_question": "", "route": "",
                    "messages": trim + [AIMessage(content=notice, name="orchestrator")]}
    if intent_name == "decision" and not updates:
        intent_name = "chitchat" if reply else "analysis"
    if intent_name == "chitchat" and not reply:
        intent_name = "analysis"
    if intent_name == "workflow" and not getattr(settings, "ENABLE_WORKFLOWS", True):
        intent_name = "analysis"

    print(f"intent = {intent_name} / reason = {reason}")

    base = {**common, "conversation_reason": reason, "is_followup": False, "route": "", "route_reason": ""}

    # ── decision: 확정값 저장 요청 → 승인 필요 (check_approval) 또는 바로 반영
    if intent_name == "decision":
        if (run.get("workflow") == "trend_planning" and run.get("status") == "waiting_input"
                and run.get("waiting_key") == "products" and {"category", "products", "theme", "subtype", "colors"}.intersection(updates)):
            notice = "10단계에서는 현재 검토한 카테고리명 또는 검증 상품 ID를 명시해 주세요. 예: 아우터 / 주력 상품은 0700144001로 확정"
            return {**base, "turn_type": "chitchat", "reply": notice, "standalone_question": "",
                    "messages": trim + [AIMessage(content=notice, name="orchestrator")]}
        if getattr(settings, "REQUIRE_APPROVAL_FOR_DECISIONS", True):
            request = approval.build_approval_request(updates, title="확정값 저장", source="user")
            return {
                **base, "turn_type": "approval_request", "reply": request["question"],
                "standalone_question": "", "pending_approval": request,
                "messages": trim + [AIMessage(content=request["question"], name="orchestrator")],
            }
        campaign = update_conversation_state(campaign, updates, source="user")
        text = "확정값을 저장했습니다.\n" + "\n".join(f"- {k}: {v}" for k, v in updates.items())
        # 워크플로가 이 값을 기다리고 있었으면 다음 단계로 진행
        turn_type = "workflow" if not _target_wait and workflow.waiting_value_confirmed(run, campaign) else "chitchat"
        return {**base, "turn_type": turn_type, "reply": text, "standalone_question": "", "campaign": campaign,
                "messages": trim + [AIMessage(content=text, name="orchestrator")]}

    # ── workflow: 업무 흐름 시작 (워크플로가 단계 순서를 고정)
    if intent_name == "workflow" and _target_wait:
        return {**base, 'turn_type':'chitchat', 'reply':WAIT_NOTICE, 'standalone_question':'',
                'messages':trim+[AIMessage(content=WAIT_NOTICE,name='orchestrator')]}
    if intent_name == "workflow":
        new_run, problem = workflow.start_run(workflow_name, question, campaign, run)
        if problem:
            return {**base, "turn_type": "chitchat", "reply": problem, "standalone_question": "",
                    "messages": trim + [AIMessage(content=problem, name="orchestrator")]}
        print(f"[워크플로 시작] {workflow_name} run={new_run['run_id']}")
        return {**base, "turn_type": "workflow", "reply": "", "standalone_question": "", "workflow_run": new_run,
                "messages": trim}

    if intent_name == "chitchat":
        return {**base, "turn_type": "chitchat", "reply": reply, "standalone_question": "",
                "messages": trim + [AIMessage(content=reply, name="orchestrator")]}

    # ── analysis: 후속 질문 보정 → route_request(2): 담당 Agent 선택
    standalone, dropped = context.drop_inherited_conditions(question, standalone, is_followup, campaign)
    if dropped:
        print(f"[조건 검사] {dropped}")
    standalone = context.keep_previous_chart_type(standalone, question, last_analysis, is_followup)
    print(f"standalone_question = {standalone} / is_followup = {is_followup}")
    route = routing.route_request(
        standalone, is_followup=is_followup, last_route=last_analysis.get("route", ""),
        llm_config=state.get("llm_config"),
    )
    print(f"route = {route.route} / {route.reason}")

    return {**base, "turn_type": "analysis", "reply": "", "standalone_question": standalone,
            "is_followup": is_followup, "route": route.route, "route_reason": route.reason, "messages": trim}


# =========================================================
# 특화 Agent Node
# =========================================================

def make_agent_node(spec):
    """등록된 특화 Agent 하나를 실행하는 그래프 노드를 만듭니다."""

    def agent_node(state) -> dict:

        # 등록만 된 미구현 Agent는 라우팅되지 않으므로 상태도 표시되지 않습니다.
        from agent_system.orchestrator.tools.progress_status import emit_progress, agent_progress
        emit_progress(agent_progress(spec.name))
        print(f"[{spec.label} Agent] {spec.name} 실행")

        question = state.get("standalone_question") or state.get("user_question") or ""
        last_analysis = state.get("last_analysis") or {}
        campaign = state.get("campaign") or {}

        request = AgentRequest(
            question=question,
            user_question=state.get("user_question") or question,
            is_followup=bool(state.get("is_followup")),
            # 직전 분석은 같은 Agent가 처리했을 때만 넘깁니다 (다른 Agent의 SQL이 섞이지 않도록)
            last_analysis=last_analysis if last_analysis.get("route", spec.name) == spec.name else {},
            llm_config=state.get("llm_config"),
            campaign=campaign,                      # manage_context: 확정된 상품·타깃·일정
        )

        # 검증은 결과 저장·승인·대화 상태 갱신보다 먼저 수행한다.
        from agent_system.validation.specialized_selection_gate import validate_with_retries
        runner = spec.load_runner()
        result = validate_with_retries(
            spec.name, question,
            lambda attempt, feedback: normalize_agent_result(
                spec.name,
                runner(request if not feedback else AgentRequest(
                    question=question + "\n\n[검증 보완 요청] " + feedback,
                    user_question=request.user_question,
                    is_followup=request.is_followup,
                    last_analysis=request.last_analysis,
                    llm_config=request.llm_config,
                    campaign=request.campaign,
                    approved_actions=(),
                )),
            ),
        )
        result.update(turn_type="analysis", standalone_question=question, route=spec.name, route_label=spec.label)
        store_turn_result(state["turn_id"], result)

        entry = report_log.make_entry(result, entry_id=state["turn_id"], question=question,
                                      user_question=state.get("user_question") or question,
                                      route=spec.name, route_label=spec.label)
        update = {
            "analysis_log": report_log.append(state.get("analysis_log"), entry),   # 보고서 재료
            "last_analysis": context.summarize_analysis(question, result, spec),
            "messages": [AIMessage(content=context.memory_answer(result.get("final_answer", "")), name=spec.name)],
        }
        # update_conversation_state: Agent가 바로 확정한 값
        if isinstance(result.get("decisions"), dict) and result["decisions"]:
            update["campaign"] = update_conversation_state(campaign, result["decisions"], source=spec.name)
        # check_approval: Agent가 사용자 승인을 요청한 값
        request_data = result.get("approval_request")
        if isinstance(request_data, dict) and normalize_updates(request_data.get("updates") or {}):
            update["pending_approval"] = {
                "title": request_data.get("title") or f"{spec.label} 결과 확정",
                "question": request_data.get("question") or approval.build_approval_request(
                    request_data["updates"])["question"],
                "updates": normalize_updates(request_data["updates"]),
                "source": spec.name,
            }
        return update

    agent_node.__name__ = f"{spec.name}_node"
    return agent_node
