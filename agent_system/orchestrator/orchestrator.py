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
                               copy_package=clean, copy_review_phase='', report_draft={})
                new_campaign = deepcopy(campaign)
                new_campaign['values'].pop('page', None)
                new_campaign['values'].pop('report', None)
                new_campaign['log'] = [entry for entry in new_campaign.get('log', [])
                                       if entry.get('key') not in ('page','report')]
                notice = ('대표 문구 승인과 보고서 초안을 초기화했습니다. 이전 분석·소구점은 유지됩니다.\n\n'
                          + render_options(clean))
                return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                        'route':'', 'campaign':new_campaign, 'workflow_run':new_run,
                        'messages':trim+[AIMessage(content=notice,name='orchestrator')]}
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                notice = f'카피 단계로 돌아가지 못했습니다: {exc}'
        return {**common, 'turn_type':'chitchat', 'reply':notice, 'standalone_question':'',
                'route':'', 'messages':trim+[AIMessage(content=notice,name='orchestrator')]}

    # ── manage_workflow: 진행 중인 워크플로 명령 (규칙, LLM 없음)
    if getattr(settings, "ENABLE_WORKFLOWS", True):
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
                (f"선택 유형: {updates.get('subtype')} / 색상: {updates.get('colors')} (상품 설명 규칙 매칭, 성능 미검증)\n" if run.get("subtype_flow") else "검증된 과거 온라인 구매 기록 기반 후보이며 테마의 실제 상품 여부는 미검증입니다.\n") +
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
            updated_campaign = update_conversation_state(campaign, {'page': approved}, source='user')
            from agent_system.workflows.registry import get_workflow
            wf = get_workflow('trend_planning')
            steps = list(wf.steps())
            step17 = next(i for i, (_, item) in enumerate(steps) if item.no == 17)
            new_run = deepcopy(run)
            new_run.update(step_index=step17, status='paused', waiting_key='',
                           copy_review_phase='', report_draft={})
            result_text = ('완성형 카피 ' + str(choice['number']) + '안을 최종 확정했습니다.\n\n'
                           + '\n'.join([choice['headline']] + [str(x).strip() for x in (choice.get('subcopies') or []) if str(x).strip()])
                           + '\n\n기획전 보고서 초안을 작성하려면 "기획전 보고서 초안 작성해줘"를 입력하세요.')
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
        emit_progress("📄 Word 보고서 초안 작성 중...")
        made = report.handle_report_request(report_kind, [] if was_reset else state.get("analysis_log"),
                                            campaign, run)
        store_turn_result(state["turn_id"], {"turn_type": "report", "final_answer": made["reply"],
                                             "attachments": made["attachments"], "report_kind": report_kind,
                                             "report_errors": made["errors"]})
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
        turn_type = "workflow" if workflow.waiting_value_confirmed(run, campaign) else "chitchat"
        return {**base, "turn_type": turn_type, "reply": text, "standalone_question": "", "campaign": campaign,
                "messages": trim + [AIMessage(content=text, name="orchestrator")]}

    # ── workflow: 업무 흐름 시작 (워크플로가 단계 순서를 고정)
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

        result = normalize_agent_result(spec.name, spec.load_runner()(request))
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
