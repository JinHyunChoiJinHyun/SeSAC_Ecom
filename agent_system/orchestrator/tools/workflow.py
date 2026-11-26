"""Orchestrator Tool — manage_workflow: 대화 단계에 따라 실행 흐름 제어 (Conditional Edge + 워크플로 실행).

그래프 연결
    orchestrator ──after_orchestrator──▶ <특화 Agent> | workflow | check_approval | END
    <특화 Agent>  ──after_agent─────────▶ check_approval | END
    workflow     ──after_workflow──────▶ check_approval | END
    check_approval ──after_approval────▶ workflow (선택 대기 중인 값이 승인되면 다음 단계 진행) | END

워크플로 (agent_system/workflows/, 아키텍처 원칙 5 혼합형 오케스트레이션)
    - 대화 판단(intent)이 "기획전 준비 전체 진행" 같은 요청을 workflow로 판단하면 run을 만들고 workflow 노드로 보냄
    - run_workflow_node가 단계 순서대로 전문 Agent를 실행하다가
        마케터 선택(select)  → 질문을 남기고 멈춤 (status=waiting_input)
        담당 Agent 구현 전   → optional이면 건너뜀, 아니면 멈춤 (status=blocked)
        팀장 승인 등 gate    → 멈춤 (status=blocked)
    - 마케터가 값을 고르면 대화 판단이 decision으로 받아 확정값 승인(check_approval)을 거치고,
      승인되면 after_approval이 workflow 노드로 보내 다음 단계를 이어서 실행
    - 대기 중 다른 질문은 평소처럼 답하고 끝에 현재 단계 안내를 붙임 (같은 단계로 복귀)
    - 명령: "워크플로 계속"(멈춘 단계 다시 시도) · "워크플로 상태" · "워크플로 취소"
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage
from langgraph.graph import END

from agent_system.orchestrator.registry import AgentRequest, get_agent, normalize_agent_result
from agent_system.workflows import base
from agent_system.workflows.registry import get_workflow

CHECK_APPROVAL = "check_approval"
WORKFLOW_NODE = "workflow"
OWNER_LABELS = {"marketer": "마케터", "team_lead": "팀장"}
PREVIOUS_CHARS = 1500           # 다음 단계 안내문 {previous}에 넣을 직전 결과 길이
MAX_HISTORY = 40

# 진행 중인 워크플로 명령 (규칙 판단, LLM 없음)
CANCEL_COMMAND = re.compile(
    r"^\s*(?:워크플로|기획전\s*준비)\s*"
    r"(?:전체\s*)?(?:취소|중단|종료|그만)"
    r"(?:해\s*줘|해\s*주세요|해주세요|해줘|할게|하자|해)?"
    r"\s*[.!?~]*\s*$"
)

# 메시지 전체가 계속 명령일 때만 재개 (CodeRabbit 리뷰: "워크플로 계속하지 마"·"워크플로 계속해도 돼?"는 명령 아님)
CONTINUE_COMMAND = re.compile(
    r"^\s*(?:워크플로|기획전\s*준비)\s*(?:를\s*|을\s*)?"
    r"(?:계속|재개|이어서|이어\s*가기|다시\s*시도)\s*"
    r"(?:진행\s*)?(?:해\s*줘|해\s*주세요|해주세요|해줘|할게|하자|해|하기)?"
    r"\s*[.!~]*\s*$"
)
STATUS_COMMAND = re.compile(
    r"(?:워크플로|기획전\s*준비)"
    r".*?(?:현재\s*)?(?:상태|어디까지|진행\s*상황|몇\s*단계)"
)


# 답변에서 버튼을 가리키는 표시 (이 표시가 있는 답변에는 워크플로 버튼을 붙임, graph.run_turn)
BUTTON_LABELS = ("[다시 시도]", "[진행 상황]", "[워크플로 취소]")


def is_workflow_command(message: str) -> bool:
    """'워크플로 계속/취소/상태' 명령인지 (Slack 버튼을 붙일지 판단할 때도 사용)."""
    text = str(message or "")
    return bool(CANCEL_COMMAND.search(text) or CONTINUE_COMMAND.search(text) or STATUS_COMMAND.search(text))


# ---------------------------------------------------------------- 조건부 엣지
def after_orchestrator(state) -> str:
    if state.get("turn_type") == "workflow" and base.is_active(state.get("workflow_run")):
        return WORKFLOW_NODE
    if state.get("turn_type") == "analysis" and state.get("route"):
        return state["route"]
    if state.get("pending_approval"):
        return CHECK_APPROVAL
    return END


def after_agent(state) -> str:
    return CHECK_APPROVAL if state.get("pending_approval") else END


def after_workflow(state) -> str:
    return CHECK_APPROVAL if state.get("pending_approval") else END


def waiting_value_confirmed(run: dict | None, campaign: dict | None) -> bool:
    """선택 대기 중인 확정값이 run 시작 이후 확정됐는지."""
    run = run or {}
    return (run.get("status") == "waiting_input" and bool(run.get("waiting_key"))
            and base.confirmed_since(campaign, run["waiting_key"], run.get("started_at", "")))


def after_approval(state) -> str:
    """10단계 상품 승인에 한해서 13단계 입력 대기까지 자동 진행합니다."""
    run = state.get("workflow_run") or {}
    approval = state.get("approval_result") or {}
    if (state.get("pending_approval") or approval.get("kind") != "approve"
            or not ({"products", "selling_points", "page"} & set(approval.get("approved_keys") or []))
            or run.get("workflow") != "trend_planning"
            or run.get("waiting_key") not in ("products", "selling_points", "page")
            or not waiting_value_confirmed(run, state.get("campaign"))):
        return END
    _, _, step = current_step(run)
    return WORKFLOW_NODE if step and step.no in (10, 13, 16) else END


# ---------------------------------------------------------------- 안내 문구
def owner_label(owner: str) -> str:
    if owner in OWNER_LABELS:
        return OWNER_LABELS[owner]
    spec = get_agent(owner)
    return f"{spec.label} Agent" if spec else owner


def current_step(run: dict | None):
    workflow = get_workflow((run or {}).get("workflow", ""))
    if not workflow:
        return None, None, None
    steps = workflow.steps()
    index = int((run or {}).get("step_index", 0))
    if 0 <= index < len(steps):
        stage, step = steps[index]
        return workflow, stage, step
    return workflow, None, None


def run_status_text(run: dict | None, campaign: dict | None = None) -> str:
    """대화 판단 입력·답변 끝 안내에 쓰는 현재 단계 설명. 진행 중인 run이 없으면 빈 문자열."""
    if not run or run.get("status") in ("done", "cancelled", None):
        return ""
    if (run.get('page_preview') or {}).get('status') == 'approved' and run.get('workflow') == 'trend_planning':
        status = (run.get('campaign_target_r39') or {}).get('status')
        if status == 'review':
            if (run.get('campaign_target_r39') or {}).get('proposal'):
                return '고객 타깃 선정 제안 · 승인 대기'
            return '고객 구매 예측 분석 완료 · 색상별 선정 규모 입력 대기'
        if status == 'confirmed':
            if (run.get('campaign_target_r39') or {}).get('policy') == 'r40_v1':
                phase = (run.get('segment_copy_r41') or {}).get('status')
                return {'review':'고객군별 문구 검토 · 승인 대기','approved':'고객군별 문구 승인 완료 · 보고서 요청 대기','rejected':'고객군별 문구 반려 · 재생성 대기'}.get(phase,'고객 타깃 확정 · 고객군별 문구 생성 대기')
            return '고객 타깃 확정 완료 · 기획전 보고서 요청 대기'
        if run.get('target_start_pending'):
            return '페이지 틀 승인 완료 · 고객 타깃 분석 요청 대기'
    workflow, stage, step = current_step(run)
    if not workflow or step is None:
        return ""
    head = f"{step.title}"
    if run.get("status") == "waiting_input":
        if run.get("waiting_key") == "campaign_period":
            from agent_system.orchestrator.tools.category_planning.period_context import date_prompt
            return date_prompt(run)
        if run.get("waiting_key") == "category_confirmation":
            category = run.get("review_category") or "선택한 카테고리"
            return (f"{category} 분석 완료 — 상품군 미선택.\n"
                    f"'{category}로 할게'라고 입력하면 승인 요청이 표시됩니다. "
                    "다른 카테고리도 검토할 수 있습니다.")
        if run.get("waiting_key") == "category_decision":
            category = run.get("review_category") or "선택한 카테고리"
            return f"{category} 상품군 승인 대기 중. [{category} 승인] 버튼을 눌러야 주력 상품 유형 분석이 진행됩니다."
        if run.get("waiting_key") == "subtype_colors":
            from agent_system.orchestrator.tools.category_planning.subtype_selection import guide, color_guide
            options = run.get("subtype_options") or []
            return ("주력 상품 유형 자유 분석 중. 예: 경량패딩 분석해줘, 코트 분석해줘. "
                    "선택 시 유형과 색상 1~3개를 함께 입력하세요. "
                    "예: 경량패딩 블랙이랑 그린카키로 할게. "
                    "비교 재표시: 주력 상품 유형 비교 다시 보여줘")
        if step.no == 10 and run.get("subtype_flow"):
            return f"{head} — 과거 상품 사례 확인·기획전 유형 및 색상 방향 승인 대기 중. 유형: {run.get('selected_subtype')} / 색상: {', '.join(run.get('selected_colors') or [])}. [승인] 또는 [반려] 버튼을 선택해 주세요."
        if step.no == 16 and workflow.name == "trend_planning":
            if (run.get('page_preview') or {}).get('status') == 'rejected':
                return "페이지 틀이 반려되었습니다. 수정할 부분을 채팅으로 알려주시거나 '페이지 틀 다시 만들어줘'로 새 시안을 생성해 주세요."
            if (run.get('page_preview') or {}).get('status') == 'review':
                return "기획전 페이지 틀 승인 대기 중. 이미지 재표시·수정·재생성·되돌리기 후 [승인]을 누르면 타깃 선정으로 진행합니다."
            return "대표 문구 3안 중 하나를 자연어로 선택해 주세요. 예: 3번으로 할게"
        if step.no == 13 and workflow.name == "trend_planning":
            from agent_system.orchestrator.tools.campaign_selection import guide
            summary = run.get("campaign_summary")
            return f"{head} — 소구점 선택 대기 중 (2~3개 권장, 후보 범위 내 자유 선택)." if summary else "워크플로 12단계 재실행이 필요합니다."
        return (f"{head} — 선택 대기 중. {base.render(step.instruction, run, campaign)}\n"
                f"(값을 말씀하시면 확정값 '{step.selects}'로 승인받은 뒤 다음 단계로 진행합니다)")
    if run.get("status") == "paused":
        return f"■ {step.title} — 다음 단계 대기 중. 진행하려면 '워크플로 계속'을 입력해 주세요."
    if run.get("status") == "blocked":
        return f"{head} — 멈춤: {run.get('blocked_reason', '')} (아래 [다시 시도] 버튼으로 다시 시도, [워크플로 취소]로 종료)"
    return f"{head} — 진행 중"


def workflow_reminder(run: dict | None, campaign: dict | None = None) -> str:
    text = run_status_text(run, campaign)
    return f"[진행 중인 워크플로] {text}" if text and (run or {}).get("status") == "waiting_input" else ""


# ---------------------------------------------------------------- 실행
def _history(run: dict, step, status: str, summary: str = "") -> list:
    item = {"no": step.no, "title": step.title, "owner": step.owner, "status": status, "summary": summary[:300]}
    return (list(run.get("history") or []) + [item])[-MAX_HISTORY:]


def _run_agent_step(step, run: dict, state) -> dict:
    spec = get_agent(step.owner)
    request = AgentRequest(
        question=base.render(step.instruction, run, state.get("campaign")),
        user_question=run.get("request", ""),
        is_followup=False,
        last_analysis={},
        llm_config=state.get("llm_config"),
        campaign=state.get("campaign") or {},
    )
    try:
        return normalize_agent_result(spec.name, spec.load_runner()(request))
    except Exception as exc:  # Agent 실패는 그 단계에서 멈추고 '워크플로 계속'으로 다시 시도
        return {"final_answer": f"{spec.label} Agent 실행에 실패했습니다.", "error": f"{type(exc).__name__}: {exc}"}


def _campaign_topic_from_request(request: str) -> str:
    """최초 요청의 '기획전' 앞에 명시된 주제를 추출한다 (추측하거나 고정 제목을 선택하지 않음)."""
    request = re.sub(r"\s+", " ", str(request or "")).strip()
    # '기획전'이 여러 번 등장해도 첫 요청의 제목 표현을 우선한다.
    match = re.search(r"기획전", request)
    if not match:
        return ""

    prefix = request[:match.start()]
    # 기간 표현을 숫자만 지우지 않고 조사까지 한 덩어리로 제거한다.
    # 예: '3주 뒤부터 한 달간' -> '' ('뒤부터 간'이 남지 않도록).
    prefix = re.sub(
        r"(?:\d+\s*주\s*뒤부터|\d+\s*주\s*후부터|다음\s*달부터|이번\s*달부터)"
        r"\s*(?:(?:한|두|세|\d+)\s*(?:달|개월|주)\s*(?:간|동안)?(?:\s*까지)?(?:\s*의)?|\d+\s*일\s*(?:간|동안)?)?",
        " ", prefix,
    )
    # 기간·타깃 조건을 제목으로 오인하지 않도록 가장 마지막 주제 구간을 사용한다.
    prefix = re.split(r"대상으로|대상으로는|위한|위해서|목적으로|[,，.!?。]", prefix)[-1]
    prefix = re.sub(r"(?:온라인|오프라인|온\s*·\s*오프라인|비대면)\s*", "", prefix)
    prefix = re.sub(r"(?:\d{1,2}\s*[~-]\s*\d{1,2}대|\d{1,2}대|\d{4}년|\d+주|\d+개월|한\s*달|두\s*달)\s*", "", prefix)
    prefix = re.sub(r"(?:고객|회원|대상|타깃|타겟|진행|상품을|제품을)\s*", "", prefix)
    prefix = re.sub(r"[^0-9A-Za-z가-힣\s·&/-]", " ", prefix)
    tokens = prefix.split()
    if not tokens:
        return ""
    # 주제가 길거나 정형화되지 않은 입력은 임의 축약 대신 안전한 일반 제목으로 처리한다.
    topic = " ".join(tokens)
    if len(tokens) > 7 or len(topic) > 36:
        return ""
    return topic


def _public_workflow_heading(workflow, run: dict) -> str:
    """최초 요청에 명시된 주제로 제목을 만든다. 내부 run_id는 출력하지 않는다."""
    if workflow.name != "trend_planning":
        return f"■ {workflow.title} 진행 안내"
    topic = _campaign_topic_from_request(run.get("request") or "")
    return f"■ {topic} 기획전 분석" if topic else "■ 기획전 분석 결과"


def run_workflow_node(state) -> dict:
    """현재 run을 다음 멈출 지점(마케터 선택·구현 전 Agent·gate·완료)까지 실행합니다."""
    from agent_system.orchestrator.orchestrator import store_turn_result
    from agent_system.orchestrator.tools import context

    run = dict(state.get("workflow_run") or {})
    campaign = state.get("campaign") or {}
    workflow = get_workflow(run.get("workflow", ""))
    if not workflow or not base.is_active(run):
        return {}

    steps = workflow.steps()
    index = int(run.get("step_index", 0))
    # R40/R42.6: 연령 제한 모집단에서 검토까지 진행한다. 기존 R39 세션은 유지한다.
    from agent_system.orchestrator.tools import campaign_target_r39 as target39
    if target39.active(run) and index < len(steps) and steps[index][1].no >= 17:
        try:
            saved = run.get('campaign_target_r39') or {}
            from .audience_scope_r426 import current_target
            if run.get('period_plan'):
                from .category_planning.analysis_scope_r427 import run_scope
                run_scope(run)
            if saved and not current_target(saved, campaign, run):
                raise ValueError('기존 고객 연령 조건 또는 분석 결과가 변경되었습니다. 고객 타깃 다시 선정해줘라고 요청해 주세요.')
            if saved.get('status') == 'confirmed':
                answer = target39.confirmed_notice(saved)
            elif saved.get('status') == 'review':
                answer = target39.proposal_notice(saved) if saved.get('proposal') else target39.scoring_notice(saved)
            else:
                from . import campaign_target_r40 as target40
                saved = target40.score_campaign(campaign, run, root=target39.ROOT)
                run['campaign_target_r39'] = saved
                answer = target39.proposal_notice(saved)
            run.update(status='paused', waiting_key='', target_start_pending=False)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('기획전 구매 모델 분석 실패')
            run.update(status='paused', waiting_key='', target_start_pending=True)
            answer = f'고객 구매 예측 분석을 완료하지 못했습니다 ({type(exc).__name__}). ' + (str(exc) if isinstance(exc, ValueError) and any(x in str(exc) for x in ('모델 미지원', '단일 모델 품목', '확정 참고 상품', '확정된 기획전 색상', '동일 품목', '고객 연령', '연령 조건', '연령 필터', '지정한 연령', '고객 모집단')) else '모델 파일·DB·확정 품목과 색상을 확인해 주세요.')
        store_turn_result(state['turn_id'], {'turn_type':'workflow', 'final_answer':answer, 'completed_steps':[], 'workflow_run': {'workflow':'trend_planning', 'status':'paused'}})
        return {'workflow_run':run, 'turn_type':'workflow', 'reply':answer,
                'messages':[AIMessage(content=answer, name=WORKFLOW_NODE)]}
    sections, results, last_spec, last_question = [], [], None, ""
    # R30: 이번 실행에서 생성된 12단계 소구점 근거만 별도 추적한다.
    selling_point_result = None
    step10_approval_request = None
    from agent_system.common_tools.report_export import analysis_log as report_log
    log = list(state.get("analysis_log") or [])
    run["status"] = "running"
    run["waiting_key"] = ""

    while index < len(steps):
        stage, step = steps[index]
        if stage.key != run.get("stage"):
            missing = [k for k in stage.requires if not ((campaign.get("values") or {}).get(k))]
            if missing:
                run.update(status="blocked", blocked_reason=f"{stage.title}에 필요한 확정값 없음: {missing}")
                sections.append(f"■ {stage.title}: 필요한 확정값({', '.join(missing)})이 없어 멈춥니다.")
                break
            run["stage"] = stage.key
            run = base.record_run_event(run, "stage_transition", stage=stage.key)

        if run.get("period_required") and index > 0 and not run.get("period_plan"):
            run.update(status="blocked", blocked_reason="검증된 공통 분석 기간 없음")
            sections.append("공통 분석 기간이 없어 과거 구매 분석을 진행할 수 없습니다. 새 분석을 시작하세요.")
            break
        # CART_SIGNAL_CATEGORY_BRIDGE_V1: 2~3단계를 하나의 검증된 분석으로 실행, 4단계 선택에서 정지
        if (workflow.name == "trend_planning" and stage.key == "s1_product_selection"
                and step.no == 2 and index == 0
                and (run.get("period_required") or run.get("period_plan") or "겨울" in str(run.get("request", "")))):
            from pathlib import Path
            from agent_system.orchestrator.tools.category_planning.workflow_bridge import build_category_result
            try:
                safe_run_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run["run_id"]))
                output = Path("audit_output/slack_category_bridge") / safe_run_id
                if run.get("period_required") and not run.get("period_plan"):
                    from agent_system.orchestrator.tools.category_planning.period_context import date_prompt
                    if not run.get("campaign_dates"):
                        run.update(status="waiting_input", waiting_key="campaign_period")
                        run = base.record_run_event(run, "waiting_input", key="campaign_period", step=1)
                        sections.append(date_prompt(run))
                        break
                    from agent_system.common_tools.local_data.campaign_period import load_period_plan
                    plan = load_period_plan(**run["campaign_dates"], buffer_months=1, after_months=0)
                    interpretation = run.get("schedule_interpretation") or {}
                    if interpretation.get("precision") in ("month", "calculated"):
                        plan = {**plan, "schedule_interpretation": dict(interpretation)}
                    from .category_planning.analysis_scope_r427 import bind_plan
                    from .audience_scope_r426 import require_scope
                    run["period_plan"] = bind_plan(plan, require_scope(run))
                    if "campaign_scope" in run:
                        from .campaign_scope_r43 import bind_plan as bind_campaign_scope
                        run["period_plan"] = bind_campaign_scope(run["period_plan"], run["campaign_scope"])
                if run.get("period_plan") is not None:
                    from .category_planning.analysis_scope_r427 import bind_plan
                    from .audience_scope_r426 import require_scope
                    run["period_plan"] = bind_plan(run["period_plan"], require_scope(run))
                    if "campaign_scope" in run:
                        from .campaign_scope_r43 import bind_plan as bind_campaign_scope
                        run["period_plan"] = bind_campaign_scope(run["period_plan"], run["campaign_scope"])
                    result = build_category_result(run.get("request", ""), output, period_plan=run["period_plan"])
                    run["category_period_rows"] = result["rows"]
                else:
                    result = build_category_result(run.get("request", ""), output)
            except Exception as exc:
                run.update(status="blocked", blocked_reason=f"상품군 분석 실패: {type(exc).__name__}: {exc}")
                sections.append("■ 상품군 비교 실패\n" + run["blocked_reason"])
                break
            sections.append(result["final_answer"])
            for skip_idx in (0, 1):
                _, completed = steps[skip_idx]
                run["history"] = _history(run, completed, "done", "공통 카테고리 비교 분석")
            run["previous"] = context.memory_answer(result["final_answer"])[:PREVIOUS_CHARS]
            results.append(result)
            index = 2
            run.update(status="waiting_input", waiting_key="category")
            run = base.record_run_event(run, "waiting_input", step=4, key="category")
            break

        # 예정 일정은 분석/초안에만 사용한다. 운영 확정 전 외부 실행은 금지한다.
        if workflow.name == "trend_planning" and step.no >= 24 and run.get("period_plan"):
            from agent_system.orchestrator.tools.category_planning.period_context import execution_schedule_ready
            if not execution_schedule_ready(run["period_plan"]):
                run.update(status="paused", blocked_reason="실제 운영 일자 미확정")
                sections.append("예상 일정으로 분석과 보고서 작성은 가능하지만 승인 요청·발송은 할 수 없습니다. "
                                "실제 시작일·종료일을 포함한 새 분석에서 운영 일정을 확정해 주세요.")
                break
        # 대분류 탐색 단계는 색상/스타일 결정 단계가 아니므로 화면 제목만 조정한다.
        if workflow.name == "trend_planning" and step.no == 5 and run.get("review_category"):
            label = "■ 상품군 구매 기록 및 시계열 분석"
        else:
            label = f"■ {step.title}"
        # CART_SIGNAL_CATEGORY_REVIEW_LOOP_V1: 4단계는 검토 카테고리 선택, 최종 확정은 10단계
        if (workflow.name == "trend_planning" and step.no == 4
                and run.get("review_category")):
            sections.append(f"■ 상품군 분석: {run['review_category']}")
            index += 1
            continue
        if (workflow.name == "trend_planning" and step.no == 10
                and run.get("review_category") and run.get("review_buffer")):
            reviews = dict(run.get("category_reviews") or {})
            reviews[run["review_category"]] = dict(run["review_buffer"])
            run["category_reviews"] = reviews
            if (run.get("category_synthesis") or {}).get("category") == run["review_category"]:
                evidence = dict(run.get("category_review_evidence") or {})
                evidence[run["review_category"]] = dict(run["category_synthesis"])
                run["category_review_evidence"] = evidence
            if not base.confirmed_since(campaign, "products", run.get("started_at", "")):
                sections.append("■ 다른 카테고리도 분석하려면 '니트·스웨터 분석해줘'처럼 입력하세요. "
                                "과거 H&M 참고 사례와 유형·색상 방향을 승인하기 전에는 다음 단계로 넘어가지 않습니다.")
        if workflow.name == "trend_planning" and step.no == 16:
            package = run.get("copy_package") or {}
            if not package or package.get("status") != "verified":
                run.update(status="blocked", blocked_reason="15단계 문구 검증 결과 없음")
                sections.append("검증된 대표 문구 후보가 없어 16단계 선택을 중단합니다.")
                break
            if not base.confirmed_since(campaign, "page", run.get("started_at", "")):
                run.update(status="waiting_input", waiting_key="page")
                run = base.record_run_event(run, "waiting_input", step=16, key="page")
                break
        if workflow.name == "trend_planning" and step.no == 13:
            from agent_system.orchestrator.tools.campaign_selection import guide
            summary = run.get("campaign_summary")
            if not summary:
                run.update(status="paused")
                sections.append("12단계 추천 결과가 없습니다. 워크플로 12단계 재실행을 입력하세요.")
                break
            if not base.confirmed_since(campaign, "selling_points", run.get("started_at", "")):
                run.update(status="waiting_input", waiting_key="selling_points")
                sections.append(f"{label}\n{guide(summary)}")
                run = base.record_run_event(run, "waiting_input", step=13, key="selling_points")
                break
        if step.kind == "select":
            if base.confirmed_since(campaign, step.selects, run.get("started_at", "")):
                value = (campaign.get("values") or {}).get(step.selects, "")
                if workflow.name == "trend_planning" and step.no == 16:
                    from agent_system.orchestrator.tools.campaign_copy import _selected
                    chosen = _selected(value)
                    package = run.get("copy_package") or {}
                    matching = next((c for c in package.get("candidates") or []
                                     if c.get("number") == chosen.get("number")
                                     and c.get("headline") == chosen.get("headline")), None)
                    if package.get("status") != "verified" or not matching:
                        run.update(status="blocked", blocked_reason="16단계 선택 문구 근거 불일치")
                        sections.append("대표 문구 확정 근거가 달라져 다음 단계로 진행하지 않습니다.")
                        break
                    sections.append(f"■ 16단계 대표 문구 확정\n{matching['headline']}\n"
                                    "기획전 페이지 틀에 반영할 문구를 저장했습니다. 17단계 타깃 분석은 별도 검증 후 진행합니다.")
                    run["history"] = _history(run, step, "done", matching["headline"])
                    index += 1
                    run["status"] = "paused"
                    break
                if workflow.name == "trend_planning" and step.no == 13:
                    sections.append("■ 13단계 소구점 구성 승인 완료")
                else:
                    sections.append(f"{label}\n확정: {value}")
                run["history"] = _history(run, step, "done", f"확정: {value}")
                index += 1
                continue
            run.update(status="waiting_input", waiting_key=step.selects)
            if step.no == 10 and run.get("subtype_flow"):
                # 8~9단계 상품 ID 검증 후 같은 턴에서 승인 요청을 생성한다.
                # 실패할 경우 승인 버튼을 만들지 않고 검증 오류를 안내한다.
                try:
                    from agent_system.orchestrator.tools.category_planning.step10_confirmation import build_step10_request
                    step10_approval_request = build_step10_request(run)
                    # 승인 요청 본문과 Slack 버튼이 이미 표시되므로 중복 텍스트를 추가하지 않는다.
                except ValueError as exc:
                    run.update(status="blocked", blocked_reason=f"10단계 상품 검증 실패: {exc}")
                    sections.append(f"{label}\n과거 사례·편성 방향 승인을 요청할 수 없습니다: {exc}")
            else:
                sections.append(f"{label}\n{base.render(step.instruction, run, campaign)}")
            run = base.record_run_event(run, "waiting_input" if step10_approval_request or run["status"] == "waiting_input" else "blocked", step=step.no, key=step.selects)
            break

        if step.kind == "gate":
            run.update(status="blocked", blocked_reason=f"{step.no}단계 {step.title}은(는) 승인 기능 구현 전")
            sections.append(f"{label}\n{step.instruction} (승인 기록 기능 구현 전이라 여기서 멈춥니다)")
            break

        # CART_SIGNAL_CATEGORY_SYNTHESIS_V1: 7단계 전용 종합 구현 (Reporting 전체 활성화하지 않음)
        local_step7 = (workflow.name == "trend_planning" and stage.key == "s1_product_selection"
                       and step.no in (7, 9) and bool(run.get("review_category")))
        spec = get_agent(step.owner)
        local_step12 = workflow.name == "trend_planning" and step.no == 12
        local_copy = workflow.name == "trend_planning" and step.no in (14, 15)
        if spec is None or (not spec.enabled and not (local_step7 or local_step12 or local_copy)):
            name = spec.label if spec else step.owner
            if step.optional:
                sections.append(f"{label}\n건너뜀: {name} Agent 구현 전 (이전 단계 결과 표로 대신)")
                run["history"] = _history(run, step, "skipped")
                index += 1
                if workflow.name == "trend_planning" and run.get("review_category") and 5 <= step.no <= 9:
                    continue
                run["status"] = "paused"
                break
            run.update(status="blocked", blocked_reason=f"{name} Agent 구현 전")
            sections.append(f"{label}\n{name} Agent가 아직 구현되지 않아 여기서 멈춥니다. "
                            "구현 후 아래 [다시 시도] 버튼을 누르면 이 단계부터 이어서 진행합니다.")
            run = base.record_run_event(run, "blocked", step=step.no, reason="agent_not_implemented")
            break

        # CART_SIGNAL_VERIFIED_PRODUCT_SEARCH_V1
        if (workflow.name == "trend_planning" and step.no == 5 and run.get("review_category")
                and run.get("period_plan")):
            from agent_system.orchestrator.tools.category_planning.period_context import build_period_product_result
            try:
                from .category_planning.analysis_scope_r427 import run_scope
                run_scope(run)
                result = build_period_product_result(run["review_category"], run["period_plan"], bool(run.get("subtype_flow")))
                expected = next((r['hm_purchases'] for r in run.get('category_period_rows', [])
                                 if r['category'] == run['review_category']), None)
                expected_before = next((r.get('hm_before_purchases') for r in run.get('category_period_rows', [])
                                        if r['category'] == run['review_category']), None)
                if (expected is None or result['period_comparison']['core_purchases'] != expected
                        or result['period_comparison']['before_purchases'] != expected_before):
                    raise ValueError('대분류 집계와 상품 구매 집계가 불일치합니다. 새 분석을 시작하세요.')
            except Exception as exc:
                result = {"final_answer": "월 단위 상품 분석 실패", "error": f"{type(exc).__name__}: {exc}"}
        elif workflow.name == "trend_planning" and step.no in (6, 11):
            from agent_system.common_tools.external_data.category_market import build_category_market
            from agent_system.orchestrator.tools.category_synthesis import market_view, render_market
            try:
                selected_category = (run.get("review_category") if step.no == 6 else (campaign.get("values") or {}).get("category"))
                saved = run.get("category_market") or {}
                if step.no == 11 and saved.get("category") == selected_category and "saved_rows" in saved:
                    result = render_market(saved, 11)
                else:
                    saved, result = market_view(build_category_market(selected_category), step.no)
                    if step.no == 11:
                        result["final_answer"] = "6단계 저장 집계가 없어 이번 단계에서 새로 조회했습니다.\n" + result["final_answer"].replace("6단계와 동일한 카테고리 표본을 재사용한 리뷰·판매 포인트 집계입니다.", "현재 배치의 카테고리 표본 집계입니다.")
                    run["category_market"] = saved
            except Exception as exc:
                result = {"final_answer": "카테고리 시장 데이터 조회 실패", "error": f"{type(exc).__name__}: {exc}"}
        elif local_copy:
            from agent_system.orchestrator.tools.campaign_copy import (
                generate_candidates, validate_candidates, render_options,
            )
            try:
                summary = run.get("campaign_summary") or {}
                if step.no == 14:
                    run["copy_package"] = generate_candidates(summary, campaign)
                    result = {"final_answer": "검토한 기획전 유형·색상·디자인 특징을 기준으로 대표 문구 3안을 작성했습니다. 15단계에서 근거를 검사합니다.",
                              "data_sources": ["hm", "musinsa"]}
                else:
                    package = validate_candidates(run.get("copy_package") or {}, summary, campaign)
                    run["copy_package"] = package
                    result = {"final_answer": render_options(package), "data_sources": ["hm", "musinsa"]}
            except (ValueError, TypeError, KeyError) as exc:
                result = {"final_answer": "대표 문구 생성 또는 검증 실패", "error": str(exc)}
        elif local_step12:
            from agent_system.orchestrator.tools.campaign_selection import build_summary, ids_from
            from agent_system.tools.product_tools import get_product_details
            try:
                result = build_summary(run, campaign, get_product_details(ids_from(campaign)))
                run["campaign_summary"] = result["campaign_summary"]
                from agent_system.orchestrator.tools.campaign_selection import build_selling_point_evidence
                try:
                    result.update(build_selling_point_evidence(result["campaign_summary"]))
                except (ValueError, OSError, RuntimeError) as exc:
                    import logging
                    logging.getLogger(__name__).warning('SELLING_POINT_EVIDENCE_FAILED %s', type(exc).__name__)
            except Exception as exc:
                result = {"final_answer": "12단계 추천 생성 실패", "error": f"{type(exc).__name__}: {exc}"}
        elif local_step7 and step.no == 7:
            from agent_system.orchestrator.tools.category_synthesis import build_category_summary
            try:
                if run.get("period_plan"):
                    from .category_planning.analysis_scope_r427 import run_scope
                    run_scope(run)
                result = build_category_summary(run["review_category"], run.get("hm_category_snapshot"),
                                                run.get("category_market"), state["turn_id"],
                                                period_plan=run.get("period_plan"), period_rows=run.get("category_period_rows"))
                run["category_synthesis"] = result["category_synthesis"]
                if run.get("period_plan"):
                    # 5단계에 이미 출력된 기간 안내와 핵심·사전 구매 실적을 7단계에서 중복하지 않는다.
                    run["category_synthesis"]["period_comparison"] = run["hm_period_comparison"]
                if run.get("subtype_flow"):
                    from agent_system.orchestrator.tools.category_planning.subtype_selection import rank_options, guide
                    from agent_system.orchestrator.tools.category_planning.subtype_selection import review_options
                    from agent_system.orchestrator.tools.category_planning.subtype_selection import collect_musinsa_subtype_evidence
                    inventory = run.get("subtype_inventory") or {}
                    market_evidence = collect_musinsa_subtype_evidence(run["review_category"], inventory.keys(), **({"product_gender": run["campaign_scope"]["product_gender"]} if "campaign_scope" in run else {}))
                    run["musinsa_subtype_evidence"] = market_evidence
                    options = review_options(run["review_category"], (run.get("category_market") or {}).get("naver_metrics") or {}, inventory, market_evidence)
                    run["subtype_options"] = options
                    run["category_synthesis"]["subtype_options"] = options
                    run["category_synthesis"]["theme_candidate"] = options[0]["keyword"] if options and options[0].get("growth") is not None else None
                    if run.get("category_confirmed"):
                        result["final_answer"] += "\n\n" + guide(options)
                    else:
                        result["final_answer"] += ("\n\n상품군 분석이 완료됐습니다. "
                                                   "상품군을 선택하면 주력 상품 유형별 구매 근거를 비교합니다.")
                    if not options:
                        result["error"] = "H&M 구매 기록이 확인된 세부 유형 후보 없음"
                result["stage_alignment_output"] = True
            except Exception as exc:
                result = {"final_answer": "7단계 결과 정리 실패", "error": f"{type(exc).__name__}: {exc}"}
        elif local_step7 and step.no == 9:
            from agent_system.orchestrator.tools.category_synthesis import product_summary
            try:
                result = product_summary(run.get("verified_candidate_rows") or [], run["review_category"], state["turn_id"])
                result["stage_alignment_output"] = True
            except Exception as exc:
                result = {"final_answer": "9단계 상품 정리 실패", "error": f"{type(exc).__name__}: {exc}"}
        elif step.no == 8 and stage.key == "s1_product_selection":
            # LLM의 상품 조회 여부와 무관하게 실제 H&M DB 조회로 8단계 후보 생성
            from agent_system.orchestrator.tools.category_planning.verified_products import build_verified_step8
            try:
                reviewed_category = str(run.get("review_category") or run.get("current_review_category") or
                                        (campaign.get("values") or {}).get("category") or "").strip()
                if run.get("subtype_flow") and not (run.get("selected_subtype") and run.get("selected_colors")):
                    raise ValueError("세부 유형과 색상을 먼저 선택하세요.")
                if run.get("subtype_flow"):
                    selection = {"subtype": run["selected_subtype"], "colors": run["selected_colors"]}
                else:
                    selection = {}
                if run.get("period_plan"):
                    from .category_planning.analysis_scope_r427 import run_scope
                    run_scope(run)
                    selection["period_plan"] = run["period_plan"]
                elif run.get('request'):
                    from .audience_scope_r426 import require_scope
                    selection['audience'] = require_scope(run)
                result = build_verified_step8(reviewed_category, **selection)
                # CART_SIGNAL_PRODUCT_EVIDENCE_V1
                from agent_system.orchestrator.tools.category_planning.product_evidence import enrich_verified_result
                result = enrich_verified_result(result, reviewed_category, str(run.get("run_id", "unknown")), include_chart=False, include_naver=False)
            except Exception as exc:
                result = {"final_answer": "8단계 H&M 상품 조회에 실패했습니다.",
                          "error": f"{type(exc).__name__}: {exc}"}
        else:
            result = _run_agent_step(step, run, state)
        # CART_SIGNAL_CANDIDATE_GATE_V1
        # 8단계가 실제 Tool 응답에 나온 H&M 상품 ID를 반환했는지 검증
        if step.no == 8 and stage.key == "s1_product_selection":
            from agent_system.orchestrator.tools.category_planning.candidate_gate import gate_result
            valid, candidate_ids = gate_result(result)
            if not valid:
                run.update(status="blocked", blocked_reason="8단계: 검증된 실제 H&M 상품 후보 없음")
                sections.append(f"{label}\n" + str(result.get("final_answer") or ""))
                sections.append("실제 상품 ID가 담긴 Tool 결과가 없어 10단계 진입을 막았습니다. "
                                "네이버 미수집은 정상이며, H&M 상품 검색 결과를 먼저 확인해야 합니다.")
                run = base.record_run_event(run, "blocked", step=step.no, reason="no_valid_candidates")
                break
            run["verified_candidate_ids"] = candidate_ids
            run["verified_candidate_rows"] = result["dataframe"].to_dict(orient="records")

        if result.get("not_ready"):
            # Agent는 켜져 있지만 데이터·모델·백엔드 연결 전 (가짜 결과 없이 미구현 안내만 돌려줌)
            run = base.record_run_event(run, "not_ready", step=step.no, agent=spec.name)
            if step.optional:
                sections.append(f"{label}\n건너뜀: {spec.label} Agent 미구현 (연결 전)")
                run["history"] = _history(run, step, "skipped")
                index += 1
                if workflow.name == "trend_planning" and run.get("review_category") and 5 <= step.no <= 9:
                    continue
                run["status"] = "paused"
                break
            run.update(status="blocked", blocked_reason=f"{spec.label} Agent 미구현 (연결 전)")
            sections.append(f"{label}\n{str(result.get('final_answer', '')).strip()}")
            break
        # 11단계: 랭킹 상품명·성별 정보로 관련 표본을 선별하고 리뷰는 그 상품에 한정한다.
        # 전체 카테고리 통계는 시장 snapshot에 별도로 보존한다.
        if workflow.name == "trend_planning" and step.no == 11 and not result.get("error"):
            from agent_system.orchestrator.tools.campaign_selection import review_brief
            from agent_system.orchestrator.tools.musinsa_ranked_evidence import (load_ranked_evidence, render_ranked)
            market = dict(run.get("category_market") or {})
            subtype = ((campaign.get("values") or {}).get("subtype") or run.get("selected_subtype")
                       or (campaign.get("values") or {}).get("category"))
            try:
                ranked = load_ranked_evidence(market.get("category") or "", str(subtype or ""), **({"product_gender": run["campaign_scope"]["product_gender"]} if "campaign_scope" in run else {}))
                market["ranked_evidence"] = ranked
                run["category_market"] = market
                scoped = render_ranked(ranked)
                # CSV에도 분석 범위가 표시된 랭킹 근거 행을 추가한다. 기존 전체 리뷰 행은 보존한다.
                import pandas as pd
                evidence_rows = [
                    {'source': 'musinsa_ranked_related', 'metric': 'related_products',
                     'value': ranked['related_products'], 'scope': ranked['gender_scope']},
                    {'source': 'musinsa_ranked_related', 'metric': 'lightweight_named_products',
                     'value': ranked['lightweight_named_products'], 'scope': ranked['gender_scope']},
                    {'source': 'musinsa_ranked_related', 'metric': 'related_reviews',
                     'value': ranked['related_reviews'], 'scope': ranked['gender_scope']},
                ]
                evidence_rows += [{'source': 'musinsa_ranked_related',
                                   'metric': 'shape_' + item['label'], 'value': item['products'],
                                   'scope': ranked['gender_scope']}
                                  for item in ranked['related_shapes']]
                evidence_rows += [{'source': 'musinsa_ranked_reviews',
                                   'metric': item['label'], 'value': item['mentions'],
                                   'denominator': item['denominator'], 'scope': ranked['gender_scope']}
                                  for item in ranked['aspects']]
                original_frame = result.get('dataframe')
                frame = pd.concat([original_frame if isinstance(original_frame, pd.DataFrame) else pd.DataFrame(),
                                   pd.DataFrame(evidence_rows)], ignore_index=True)
                result = dict(result, final_answer=scoped, dataframe=frame)
            except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
                result = dict(result, final_answer=review_brief(market, subtype)
                              + "\n랭킹 상품·리뷰 연결 분석은 확인할 수 없어 전체 카테고리 참고 근거만 표시합니다. "
                              + f"({type(exc).__name__})")
        answer = str(result.get("final_answer", "")).strip()
        sections.append(f"{label}\n{answer}")
        run = base.record_run_event(run, "agent_call", step=step.no, agent=spec.name,
                                    error=str(result.get("error") or ""))
        if result.get("error"):
            run.update(status="blocked", blocked_reason=f"{step.no}단계 실패: {result['error']}")
            run["history"] = _history(run, step, "failed", str(result["error"]))
            sections.append("이 단계에서 멈춥니다. 아래 [다시 시도] 버튼으로 다시 시도할 수 있습니다.")
            break
        if workflow.name == "trend_planning" and step.no == 5 and run.get("review_category") and run.get("period_plan"):
            from agent_system.orchestrator.tools.category_synthesis import capture_hm
            run["hm_category_snapshot"] = capture_hm(result, run["review_category"])
            run["hm_period_comparison"] = result["period_comparison"]
            run["subtype_inventory"] = result["subtype_inventory"]
        elif workflow.name == "trend_planning" and step.no == 5 and run.get("review_category"):
            from agent_system.orchestrator.tools.category_synthesis import capture_hm, _fallback_hm
            snapshot = capture_hm(result, run["review_category"])
            if not (snapshot.get("colour_counts") or snapshot.get("style_tag_counts")):
                try:
                    snapshot = _fallback_hm(run["review_category"])
                    sections.append("5단계 구조화 Tool 집계가 없어 표준 카테고리 기준으로 재집계했습니다. 7단계에는 해당 집계를 사용합니다.")
                except Exception as exc:
                    sections.append(f"5단계 구조화 집계 미확인 ({type(exc).__name__}). 7단계에서 추정하지 않습니다.")
            run["hm_category_snapshot"] = snapshot
            if run.get("subtype_flow"):
                from agent_system.orchestrator.tools.category_planning.subtype_selection import build_inventory
                from agent_system.orchestrator.tools.category_planning.verified_products import load_purchase_inputs
                try:
                    from .audience_scope_r426 import require_scope, label
                    age = require_scope(run)
                    articles, purchases = load_purchase_inputs(audience=age)
                    run["subtype_inventory"] = build_inventory(run["review_category"], articles, purchases)
                    for item in run['subtype_inventory'].values():
                        item['analysis_age_label'] = label(age)
                    note = "5단계 세부 유형별 과거 " + ("핵심 월" if run.get('period_plan') else "겨울") + " 온라인 구매 집계 저장 완료. 7단계에서 네이버 상승 근거와 대조합니다."
                    sections.append(note)
                    result["final_answer"] = answer = answer + "\n" + note
                except Exception as exc:
                    run["subtype_inventory"] = {}
                    run.update(status="blocked", blocked_reason=f"5단계 유형별 구매 집계 실패: {type(exc).__name__}")
                    sections.append(run["blocked_reason"])
                    run["history"] = _history(run, step, "failed", run["blocked_reason"])
                    break
        # R30: 후속 단계의 표·그림이 12단계 소구점 첨부를 덮지 못하게 한다.
        if workflow.name == "trend_planning" and step.no == 12 and not result.get("error"):
            selling_point_result = result
        results.append(result)
        last_spec, last_question = spec, base.render(step.instruction, run, campaign)
        log = report_log.append(log, report_log.make_entry(   # 기획전 실행 계획 보고서 재료
            result, entry_id=f"{state['turn_id']}_{index}", question=last_question,
            user_question=f"{step.no}단계 {step.title}", route=spec.name, route_label=spec.label,
            kind="workflow_step", workflow={"run_id": run["run_id"], "workflow": workflow.name,
                                            "title": workflow.title, "step_no": step.no, "step_title": step.title}))
        run["previous"] = context.memory_answer(answer)[:PREVIOUS_CHARS]
        run["history"] = _history(run, step, "done", run["previous"])
        # CART_SIGNAL_CATEGORY_REVIEW_LOOP_V1: 5~9단계의 실행별 요약 보존
        if (workflow.name == "trend_planning" and 5 <= step.no <= 9
                and run.get("review_category")):
            buffer = dict(run.get("review_buffer") or {})
            buffer[str(step.no)] = {"title": step.title, "summary": answer[:1800]}
            run["review_buffer"] = buffer
            if step.no == 9:
                reviews = dict(run.get("category_reviews") or {})
                reviews[run["review_category"]] = buffer
                run["category_reviews"] = reviews
        index += 1
        if workflow.name == "trend_planning" and step.no == 7 and run.get("review_category"):
            if not run.get("category_confirmed"):
                run.update(status="waiting_input", waiting_key="category_confirmation")
                sections.append(f"■ {run['review_category']} 상품군 선택\n"
                                f"분석 결과를 확인한 뒤 '{run['review_category']}로 할게'라고 입력하세요. "
                                "다른 카테고리도 분석할 수 있습니다.")
                run = base.record_run_event(run, "waiting_input", step=8, key="category_confirmation")
                break
            if run.get("subtype_flow"):
                run.update(status="waiting_input", waiting_key="subtype_colors")
                run = base.record_run_event(run, "waiting_input", step=8, key="subtype_colors")
                break
        # 5~9단계 기존 검토 루프와 11~12단계 성공 경로만 연속 실행합니다.
        if not (workflow.name == "trend_planning" and (
                (run.get("review_category") and 5 <= step.no <= 9)
                or step.no in (11, 12, 13, 14, 15))):
            run["status"] = "paused"
            break

    run["step_index"] = index
    if index >= len(steps) and run.get("status") == "running":
        run["status"] = "done"
        sections.append(f"■ {workflow.title} 워크플로를 모두 마쳤습니다.")
        run = base.record_run_event(run, "done")

    prefix = str(state.get("reply") or "").strip() if state.get("turn_type") in ("approval", "workflow") else ""
    header = _public_workflow_heading(workflow, run)
    final_answer = "\n\n".join(([prefix] if prefix else []) + [header] + sections)
    turn_result = {
        "final_answer": final_answer,
        "turn_type": "workflow",
        "workflow_run": {k: run.get(k) for k in ("run_id", "workflow", "stage", "status", "step_index", "waiting_key")},
        "data_sources": sorted({s for r in results for s in (r.get("data_sources") or [])}),
        "data_purpose": f"워크플로 {workflow.title}: " + ", ".join(
            h["title"] for h in run.get("history", []) if h["status"] == "done") or "",
        "completed_steps": [f"{workflow.name}:{h['no']}" for h in run.get("history", []) if h["status"] == "done"],
        "awaiting_input": run["status"] == "waiting_input",
    }
    # Slack 표·그래프: 마지막으로 표·그래프를 만든 단계 결과
    table_result = next((r for r in reversed(results) if r.get("dataframe") is not None), None)
    if table_result is not None:
        turn_result["dataframe"] = table_result["dataframe"]
    chart_result = next((r for r in reversed(results) if r.get("visualization_path")), None)
    if chart_result is not None:
        turn_result["visualization_path"] = chart_result["visualization_path"]
        turn_result["visualization_result"] = chart_result.get("visualization_result")
    # 이번 실행에서 생성된 다른 결과 정리 PNG도 전달 (기존 보안 검사 유지).
    extras = []
    for r in results:
        if not r.get("stage_alignment_output"):
            continue
        for path in ([r["visualization_path"]] if r.get("visualization_path") else []) + list(r.get("attachments") or []):
            if path != turn_result.get("visualization_path") and path not in extras:
                extras.append(path)
    if extras:
        turn_result["attachments"] = extras
    # R30: 상품 구성 승인 직후에는 11단계 시장 요약보다 12단계 소구점 근거를 전달한다.
    if selling_point_result is not None:
        from pathlib import Path as _EvidencePath
        from agent_system.orchestrator.tools.campaign_selection import build_selling_point_evidence
        evidence = selling_point_result
        graph_path = evidence.get("visualization_path")
        if (evidence.get("dataframe") is None or not graph_path
                or not _EvidencePath(graph_path).is_file()):
            try:
                evidence = build_selling_point_evidence(run.get("campaign_summary") or {})
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("SELLING_POINT_APPROVAL_DELIVERY_FAILED")
                # 이전 11단계의 시장 표를 소구점 근거표인 것처럼 전달하지 않는다.
                turn_result.pop("dataframe", None)
                turn_result.pop("visualization_path", None)
                turn_result.pop("visualization_result", None)
                turn_result["final_answer"] += ("\n\n소구점 근거 CSV·그래프를 준비하지 못했습니다. "
                                                "'소구점 다시 보여줘'로 다시 확인해 주세요.")
                evidence = None
        if evidence is not None:
            turn_result["dataframe"] = evidence["dataframe"]
            turn_result["visualization_path"] = evidence["visualization_path"]
            turn_result["data_purpose"] = "소구점 후보별 H&M·무신사 근거"
    store_turn_result(state["turn_id"], turn_result)

    update = {
        "workflow_run": run,
        "analysis_log": log,
        "turn_type": "workflow",
        "reply": final_answer,
        "messages": [AIMessage(content=context.memory_answer(final_answer), name=WORKFLOW_NODE)],
    }
    if step10_approval_request:
        update["pending_approval"] = step10_approval_request
    if results:   # 후속 질문("그 후보들 비교해줘")이 마지막 Agent 결과를 이어받도록
        update["last_analysis"] = context.summarize_analysis(last_question, results[-1], last_spec)
    return update


# ---------------------------------------------------------------- 시작·명령 (orchestrator_node에서 사용)
def start_run(workflow_name: str, request: str, campaign: dict | None, previous_run: dict | None) -> tuple[dict, str]:
    """새 run 만들기. 반환: (run, 시작할 수 없을 때 안내문)"""
    workflow = get_workflow(workflow_name)
    if workflow is None:
        return {}, f"등록되지 않은 워크플로입니다: {workflow_name}"
    if workflow_name == 'trend_planning':
        from .campaign_scope_r43 import unsupported_scope
        reason = unsupported_scope(request)
        if reason:
            return {}, reason
    if base.is_active(previous_run):
        return {}, ("이미 진행 중인 워크플로가 있습니다.\n" + run_status_text(previous_run, campaign)
                    + "\n새로 시작하려면 먼저 아래 [워크플로 취소] 버튼으로 진행 중인 워크플로를 끝내 주세요.")
    missing = [k for k in workflow.requires if not ((campaign or {}).get("values") or {}).get(k)]
    if missing:
        return {}, (f"'{workflow.title}'을(를) 시작하려면 먼저 확정값이 필요합니다: {', '.join(missing)}\n"
                    "메인 기획전 워크플로에서 주력 상품을 확정한 뒤 다시 요청해 주세요.")
    parent = (previous_run or {}).get("run_id", "") if (previous_run or {}).get("workflow") == workflow.parent else ""
    run = base.record_run_event(base.new_run(workflow, request, parent_run_id=parent), "run_started")
    if workflow_name == "trend_planning":
        from agent_system.orchestrator.tools.category_planning.period_context import interpret_schedule
        run["period_required"] = True
        from .campaign_season import explicit_seasons
        run['marketing_seasons'] = explicit_seasons(request)
        from .campaign_scope_r43 import parse_scope as parse_campaign_scope
        run['campaign_scope'] = parse_campaign_scope(request)
        from .audience_scope_r426 import initial_scope
        scope = initial_scope(run)
        if scope is not None:
            run['target_audience'] = scope
            run['audience_revision'] = 0
        try:
            interpreted = interpret_schedule(request)
        except ValueError:
            interpreted = None
        run["campaign_dates"] = interpreted["dates"] if interpreted else None
        run["schedule_interpretation"] = interpreted
    return run, ""


def handle_command(message: str, run: dict | None, campaign: dict | None) -> tuple[str, dict | None, bool]:
    """'워크플로 취소/계속/상태' 명령 처리. 반환: (답변, 바뀐 run 또는 None, 계속 실행할지)"""
    if not run or run.get("status") in ("done", "cancelled"):
        if CANCEL_COMMAND.search(message) or CONTINUE_COMMAND.search(message) or STATUS_COMMAND.search(message):
            return "진행 중인 워크플로가 없습니다.", None, False
        return "", None, False
    if (run.get("period_required") and run.get("waiting_key") == "campaign_period"
            and not (CANCEL_COMMAND.search(message) or CONTINUE_COMMAND.search(message) or STATUS_COMMAND.search(message))):
        from agent_system.orchestrator.tools.category_planning.period_context import date_prompt, interpret_schedule
        try:
            interpreted = interpret_schedule(message)
            # 이전 버전의 일정 확인 대기 Run도 재개할 수 있다.
            if message.strip() == "일정 확정" and run.get("period_proposal"):
                interpreted = {"dates": run["period_proposal"], "precision": "exact"}
        except ValueError as exc:
            return str(exc) + "\n" + date_prompt(run), None, False
        if not interpreted:
            return date_prompt(run), None, False
        if 'campaign_scope' in run and re.search(r'여성|남성|남자|여자|남녀|공용|키즈|아동|온라인|오프라인|매장|유니섹스', message):
            from .campaign_scope_r43 import parse_scope as parse_campaign_scope
            try:
                # 날짜 답변에 명시된 조건만 비교한다. 미명시는 기존 범위를 유지한다.
                new = parse_campaign_scope(message)
                old = run['campaign_scope']
                if re.search(r'여성|남성|남자|여자|남녀|공용|키즈|아동|유니섹스', message) and new['product_gender'] != old['product_gender']:
                    raise ValueError('최초 상품군 조건과 다릅니다.')
                if re.search(r'온라인|오프라인|매장', message) and new['channel'] != old['channel']:
                    raise ValueError('최초 채널 조건과 다릅니다.')
            except ValueError as exc:
                return str(exc) + ' 조건 변경은 새 기획전으로 요청해 주세요.', None, False
        dates = interpreted["dates"]
        revised = dict(run, campaign_dates=dates, schedule_interpretation=interpreted,
                       period_proposal=None, status="running", waiting_key="", blocked_reason="")
        from .campaign_season import explicit_seasons
        seasons = explicit_seasons(message)
        if seasons:
            revised['marketing_seasons'] = seasons
        return "", base.record_run_event(revised, "campaign_period_resolved", **dates, precision=interpreted["precision"]), True
    if 'campaign_scope' in run and re.search(r'(?:상품\s*성별|상품군\s*성별|판매\s*채널|분석\s*채널|고객\s*성별|기획전\s*대상|기획전\s*조건)', message) and re.search(r'변경|수정|바꿔|다시', message):
        return '확정된 상품군·채널·고객 조건은 분석 중 바꾸지 않습니다. 현재 워크플로 취소 후 새 요청으로 시작하세요.', None, False
    # 기간 변경으로 승인 근거를 조용히 바꾸지 않는다.
    if run.get("period_plan") and re.search(r"(?:기획전|운영)\s*(?:일정|기간)", message) and re.search(r"변경|수정|다시", message):
        return "현재 분석의 운영 일정은 고정되어 있습니다. 날짜를 변경하려면 워크플로 취소 후 새 분석을 시작하세요.", None, False
    # 보고서 검토 버튼은 별도 단계 실행을 발생시키지 않는다.
    if message.startswith('__cart_report_review__:'):
        command = message.split(':', 1)[1]
        draft = run.get('report_draft') or {}
        if draft.get('status') != 'review':
            return '검토 중인 보고서 초안이 없습니다.', None, False
        if command == 'edit':
            return '보고서 수정 요청을 채팅으로 입력해 주세요. 예: 보고서에 네이버 관심도 근거를 추가해줘.', None, False
        if command == 'cancel':
            revised = dict(run, report_draft={**draft, 'status':'cancelled'})
            return '보고서 초안 검토를 취소했습니다. 분석·카피 승인값은 유지됩니다.', revised, False
        if command == 'approve':
            revised = dict(run, report_draft={**draft, 'status':'approved'})
            return '보고서 초안을 승인했습니다. 이는 문서 검토 승인일 뿐 고객 선정·발송이나 Word 파일 생성 완료를 의미하지 않습니다.', revised, False
        return '지원하지 않는 보고서 검토 버튼입니다.', None, False
    # 타깃 모델 연결 전 임시 보고서. 실제 17~29단계를 완료 처리하지 않는다.
    if message.strip() in ("기획전 보고서 초안 작성해줘", "기획전 보고서 초안 만들어줘", "보고서 초안 작성해줘"):
        workflow, _, step = current_step(run)
        if (not workflow or workflow.name != "trend_planning" or not step or step.no != 17
                or run.get("status") != "paused"):
            return "대표 문구를 확정한 뒤 타깃 분석 전 대기 상태에서만 보고서 초안을 작성할 수 있습니다.", None, False
        from agent_system.orchestrator.tools.provisional_report import build_draft
        try:
            report = build_draft(run, campaign)
        except ValueError as exc:
            return str(exc), None, False
        revised = dict(run, report_draft={"status": "review", "revision": 1, "text": report, "target_verified": False, "history": []})
        return report, base.record_run_event(revised, "provisional_report_drafted", target_verified=False), False
    # 보고서 수정은 새 SQL 조회가 아닌 현재 Run의 확정 근거만 사용한다.
    from agent_system.orchestrator.tools.provisional_report import is_report_revision, revise_draft
    if is_report_revision(message):
        workflow, _, step = current_step(run)
        if (not workflow or workflow.name != "trend_planning" or not step or step.no != 17
                or run.get("status") != "paused"):
            return "현재 Run의 보고서 초안 수정은 대표 문구 확정 후에만 가능합니다.", None, False
        try:
            report = revise_draft(run, campaign, message)
        except ValueError as exc:
            return str(exc), None, False
        previous = run.get('report_draft') or {}
        history = list(previous.get('history') or []) + [previous.get('text')]
        revised = dict(run, report_draft={"status": "review", "revision": int(previous.get('revision') or 0)+1,
                                          "text": report, "target_verified": False,
                                          "history": history[-20:]})
        return report, base.record_run_event(revised, "provisional_report_revised",
                                             target_verified=False), False
    # 기존 13단계 승인 요청을 취소한 뒤, 11~12단계의 새 랭킹 분석만 다시 수행한다.
    if message.strip() == "워크플로 11단계 재실행":
        wf, _, step = current_step(run)
        if (not wf or wf.name != "trend_planning" or not step or step.no not in (12, 13)
                or base.confirmed_since(campaign, "selling_points", run.get("started_at", ""))):
            return "11~13단계 소구점 승인 전 상태에서만 재실행할 수 있습니다.", None, False
        revised = dict(run, status="running", waiting_key="", blocked_reason="", campaign_summary={})
        revised["step_index"] = next(i for i, (_, item) in enumerate(wf.steps()) if item.no == 11)
        revised["category_market"] = {k: v for k, v in (run.get("category_market") or {}).items()
                                      if k != "ranked_evidence"}
        return "", base.record_run_event(revised, "step11_ranked_retry"), True
    if message.strip() == "워크플로 12단계 재실행":
        wf, _, step = current_step(run)
        if not wf or wf.name != "trend_planning" or not step or step.no not in (12, 13) or base.confirmed_since(campaign, "selling_points", run.get("started_at", "")):
            return "12~13단계 미승인 상태에서만 재실행할 수 있습니다.", None, False
        revised = dict(run, status="running", waiting_key="", blocked_reason="", campaign_summary={})
        revised["step_index"] = next(i for i, (_, s) in enumerate(wf.steps()) if s.no == 12)
        return "", base.record_run_event(revised, "step12_retry"), True
    # CART_SIGNAL_CATEGORY_REVIEW_LOOP_V1: 4·10단계의 카테고리 검토 명령
    workflow, stage, step = current_step(run)
    if (workflow is not None and workflow.name == "trend_planning" and step is not None
            and 4 <= step.no <= 10
            and run.get("status") in ("waiting_input", "blocked", "paused")
            and not base.confirmed_since(campaign, "products", run.get("started_at", ""))):
        from agent_system.orchestrator.tools.category_planning.review_selection import parse_category_review, parse_category_confirmation
        from agent_system.orchestrator.tools.category_planning.review_selection import CATEGORIES
        confirmation = parse_category_confirmation(message)
        category = parse_category_review(message)
        if run.get("waiting_key") == "category_decision" and message.strip() in (
                f"{run.get('review_category')} 대분류 승인 요청 reject",
                f"{run.get('review_category')} 대분류 승인 요청 cancel"):
            revised = dict(run, status="waiting_input", waiting_key="category_confirmation", category_confirmed=False,
                           category_decision_token="")
            revised = base.record_run_event(revised, "category_approval_withdrawn")
            return "대분류 승인 요청을 종료했습니다. 기존 분석 결과는 유지됩니다. 다른 카테고리를 분석하거나 다시 확정해 주세요.", revised, False
        # 대분류 확정은 분석 요청과 별개다. 동일 카테고리 분석 결과가 있으면 재조회하지 않는다.
        if confirmation and run.get("waiting_key") == "category_confirmation" and confirmation == run.get("review_category"):
            from uuid import uuid4
            revised = dict(run, status="waiting_input", waiting_key="category_decision", blocked_reason="",
                           category_decision_token=uuid4().hex)
            revised = base.record_run_event(revised, "category_approval_requested", category=confirmation)
            return (f"상품군을 {confirmation}(으)로 확정하시겠습니까?\n"
                    f"아래 [{confirmation} 승인] 버튼을 눌러 주세요. 승인 전에는 세부 유형 분석 결과를 확정하지 않습니다."), revised, False
        if (message.strip() == f"{run.get('review_category')} 확정 승인"
                and run.get("waiting_key") == "category_decision"):
            confirmation = run["review_category"]
            revised = dict(run, category_confirmed=True, blocked_reason="", category_decision_token="")
            revised = base.record_run_event(revised, "category_confirmed", category=confirmation)
            if revised.get("subtype_flow"):
                from agent_system.orchestrator.tools.category_planning.subtype_selection import guide
                revised.update(status="waiting_input", waiting_key="subtype_colors", preview_subtype="", selected_subtype="", selected_colors=[])
                options = revised.get("subtype_options") or []
                return (f"■ 상품군 승인 완료: {confirmation}\n"
                        + guide(options)), revised, False
            revised.update(status="running", waiting_key="")
            return "", revised, True
        # 이미 분석한 다른 카테고리를 확정하려면 해당 카테고리 분석을 먼저 수행한다.
        if confirmation and run.get("waiting_key") in ("category",):
            category = confirmation
        if (confirmation and run.get("waiting_key") == "category_decision"
                and confirmation == run.get("review_category")):
            return f"{confirmation} 상품군 승인 대기 중입니다. [{confirmation} 승인] 버튼을 눌러 주세요.", None, False
        # 이미 대분류가 확정됐으면 동일 카테고리 결정 문구로 분석을 반복하지 않는다.
        if (confirmation and confirmation == run.get("review_category")
                and run.get("category_confirmed") and run.get("waiting_key") == "subtype_colors"):
            from agent_system.orchestrator.tools.category_planning.subtype_selection import guide
            return "상품군은 이미 확정됐습니다. 주력 상품 유형 비교는 '주력 상품 유형 비교 다시 보여줘'로 다시 볼 수 있습니다.", None, False
        # 10단계는 대분류 결정이 아니라 상품 목록 승인 단계다.
        if step.no == 10 and run.get("status") == "waiting_input":
            if confirmation and confirmation == run.get("review_category"):
                return "상품군은 이미 검토했습니다. 10단계에서는 검증된 주력 상품을 승인해 주세요.", None, False
            if str(message).strip().rstrip(".! ") in CATEGORIES:
                return "", None, False
        if category and not run.get("category_confirmed"):
            revised = dict(run)
            revised.update(status="running", waiting_key="", step_index=2,
                           review_category=category, review_previous="", previous="",
                           category_confirmed=False)
            revised["subtype_flow"] = category in CATEGORIES
            revised["subtype_options"] = []
            revised["subtype_inventory"] = {}
            revised["musinsa_subtype_evidence"] = {}
            revised["selected_subtype"] = ""
            revised["selected_colors"] = []
            revised["review_buffer"] = {}
            revised["verified_candidate_ids"] = []
            revised["verified_candidate_rows"] = []
            revised["category_market"] = {}
            revised["hm_category_snapshot"] = {}
            revised.pop("hm_period_comparison", None)
            revised["category_synthesis"] = {}
            revised["blocked_reason"] = ""
            revised = base.record_run_event(revised, "category_review_started", category=category)
            return "", revised, True
    if (run.get("waiting_key") == "subtype_colors" and run.get("status") == "waiting_input"
            and run.get("category_confirmed")
            and not (CANCEL_COMMAND.search(message) or STATUS_COMMAND.search(message) or CONTINUE_COMMAND.search(message))):
        from agent_system.orchestrator.tools.category_planning.subtype_selection import parse_selection, guide, color_guide
        from agent_system.orchestrator.tools.category_planning.exploration_r28 import requested_subtype_review, render_subtype_review
        options = run.get("subtype_options") or []
        # 분석 요청은 절대 확정/선택 상태를 변경하지 않는다.
        reviewed = requested_subtype_review(message, options)
        if reviewed:
            revised = dict(run, preview_subtype=reviewed)
            option = next(o for o in options if o['keyword'] == reviewed)
            return render_subtype_review(option), revised, False
        try:
            selection = parse_selection(message, options)
            if not selection:
                return guide(options), None, False
        except ValueError as exc:
            return str(exc) + "\n" + guide(options), None, False
        if not selection["colors"]:
            # 유형만 말했을 때는 상세 분석 미리보기만 제공한다.
            option = next(o for o in options if o["keyword"] == selection["subtype"])
            revised = dict(run, preview_subtype=selection["subtype"])
            return ("유형과 색상을 아직 확정하지 않았습니다.\n" + color_guide(option)
                    + "\n유형과 색상 1~3개를 한 문장으로 선택하면 승인 요청으로 이어집니다."), revised, False
        # 유형과 색상을 함께 명시했을 때에만 기존 8~10단계 검증·승인 경로 재사용.
        revised = dict(run, selected_subtype=selection["subtype"], selected_colors=selection["colors"], preview_subtype="")
        revised.update(status="running", waiting_key="", blocked_reason="")
        return "", base.record_run_event(revised, "subtype_colors_selected", subtype=selection["subtype"], colors=selection["colors"]), True
    if CANCEL_COMMAND.search(message):
        cancelled = base.record_run_event({**run, "status": "cancelled", "waiting_key": ""}, "cancelled")
        return f"워크플로를 취소했습니다. (run {run.get('run_id')}) 확정값은 그대로 둡니다.", cancelled, False
    if CONTINUE_COMMAND.search(message):
        # CART_SIGNAL_CATEGORY_REVIEW_LOOP_V1: 10단계 승인 완료 뒤 다음 단계 허용
        if (run.get("status") == "waiting_input" and run.get("waiting_key") == "products"
                and base.confirmed_since(campaign, "products", run.get("started_at", ""))):
            resumed = base.record_run_event({**run, "status": "running", "blocked_reason": ""}, "resumed")
            return "", resumed, True
        if run.get("status") == "waiting_input" and not waiting_value_confirmed(run, campaign):
            return run_status_text(run, campaign), None, False
        resumed = base.record_run_event({**run, "status": "running", "blocked_reason": ""}, "resumed")
        return "", resumed, True
    if STATUS_COMMAND.search(message):
        done = [f"{h['no']}단계 {h['title']}({h['status']})" for h in run.get("history", [])]
        return ("[워크플로 상태] " + run_status_text(run, campaign)
                + ("\n지난 단계: " + ", ".join(done) if done else "")), None, False
    return "", None, False
