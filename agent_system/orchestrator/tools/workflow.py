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
    workflow, stage, step = current_step(run)
    if not workflow or step is None:
        return ""
    head = f"{step.title}"
    if run.get("status") == "waiting_input":
        if run.get("waiting_key") == "category_confirmation":
            category = run.get("review_category") or "선택한 카테고리"
            return (f"{category} 분석 완료 — 대분류 미확정.\n"
                    f"'{category}로 할게'라고 입력하면 승인 요청이 표시됩니다. "
                    "다른 카테고리도 검토할 수 있습니다.")
        if run.get("waiting_key") == "category_decision":
            category = run.get("review_category") or "선택한 카테고리"
            return f"{category} 대분류 승인 대기 중. [{category} 승인] 버튼을 눌러야 세부 유형 추천이 진행됩니다."
        if run.get("waiting_key") == "subtype_colors":
            from category_subtype_selection import guide, color_guide
            options = run.get("subtype_options") or []
            selected = next((o for o in options if o["keyword"] == run.get("selected_subtype")), None)
            return ("세부 유형·색상 선택 대기 중. "
                    + (color_guide(selected) if selected else "소분류 1개와 색상 1~3개를 선택하세요. 예: 경량패딩으로 하고 블랙이랑 그린 카키로 갈게"))
        if step.no == 10 and run.get("subtype_flow"):
            return f"{head} — 주력상품 최종 승인 대기 중. 유형: {run.get('selected_subtype')} / 색상: {', '.join(run.get('selected_colors') or [])}. [승인] 또는 [반려] 버튼을 선택해 주세요."
        if step.no == 16 and workflow.name == "trend_planning":
            return "대표 문구 3안 중 하나를 자연어로 선택해 주세요. 예: 3번으로 할게"
        if step.no == 13 and workflow.name == "trend_planning":
            from agent_system.orchestrator.tools.campaign_selection import guide
            summary = run.get("campaign_summary")
            return f"{head} — 소구점 2~3개 선택 대기 중." if summary else "워크플로 12단계 재실행이 필요합니다."
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
    sections, results, last_spec, last_question = [], [], None, ""
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

        # CART_SIGNAL_CATEGORY_BRIDGE_V1: 2~3단계를 하나의 검증된 분석으로 실행, 4단계 선택에서 정지
        if (workflow.name == "trend_planning" and stage.key == "s1_product_selection"
                and step.no == 2 and index == 0
                and "겨울" in str(run.get("request", ""))):
            from pathlib import Path
            from category_workflow_bridge import build_category_result
            try:
                safe_run_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run["run_id"]))
                output = Path("audit_output/slack_category_bridge") / safe_run_id
                result = build_category_result(run.get("request", ""), output)
            except Exception as exc:
                run.update(status="blocked", blocked_reason=f"카테고리 통합 분석 실패: {type(exc).__name__}: {exc}")
                sections.append("■ 카테고리 비교 실패\n" + run["blocked_reason"])
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

        label = f"■ {step.title}"
        # CART_SIGNAL_CATEGORY_REVIEW_LOOP_V1: 4단계는 검토 카테고리 선택, 최종 확정은 10단계
        if (workflow.name == "trend_planning" and step.no == 4
                and run.get("review_category")):
            sections.append(f"■ 카테고리 검토: {run['review_category']} (아직 최종 확정 아님)")
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
                                "주력 상품 ID를 승인하기 전에는 다음 단계로 넘어가지 않습니다.")
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
                    from category_step10_confirmation import build_step10_request
                    step10_approval_request = build_step10_request(run)
                    # 승인 요청 본문과 Slack 버튼이 이미 표시되므로 중복 텍스트를 추가하지 않는다.
                except ValueError as exc:
                    run.update(status="blocked", blocked_reason=f"10단계 상품 검증 실패: {exc}")
                    sections.append(f"{label}\n주력상품 승인을 요청할 수 없습니다: {exc}")
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
        if workflow.name == "trend_planning" and step.no in (6, 11):
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
                    result = {"final_answer": "확정 소구점과 실제 상품을 기준으로 대표 문구 3안을 작성했습니다. 15단계에서 근거를 검사합니다.",
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
            except Exception as exc:
                result = {"final_answer": "12단계 추천 생성 실패", "error": f"{type(exc).__name__}: {exc}"}
        elif local_step7 and step.no == 7:
            from agent_system.orchestrator.tools.category_synthesis import build_category_summary
            try:
                result = build_category_summary(run["review_category"], run.get("hm_category_snapshot"),
                                                run.get("category_market"), state["turn_id"])
                run["category_synthesis"] = result["category_synthesis"]
                if run.get("subtype_flow"):
                    from category_subtype_selection import rank_options, guide
                    from category_subtype_selection import review_options
                    options = review_options(run["review_category"], (run.get("category_market") or {}).get("naver_metrics") or {}, run.get("subtype_inventory") or {})
                    run["subtype_options"] = options
                    run["category_synthesis"]["subtype_options"] = options
                    run["category_synthesis"]["theme_candidate"] = options[0]["keyword"] if options and options[0].get("growth") is not None else None
                    if run.get("category_confirmed"):
                        result["final_answer"] += "\n\n" + guide(options)
                    else:
                        result["final_answer"] += ("\n\n대분류 검토 중입니다. "
                                                   "확정하면 검증된 세부 유형과 색상별 구매 근거를 안내합니다.")
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
            from category_verified_products import build_verified_step8
            try:
                reviewed_category = str(run.get("review_category") or run.get("current_review_category") or
                                        (campaign.get("values") or {}).get("category") or "").strip()
                if run.get("subtype_flow") and not (run.get("selected_subtype") and run.get("selected_colors")):
                    raise ValueError("세부 유형과 색상을 먼저 선택하세요.")
                if run.get("subtype_flow"):
                    result = build_verified_step8(reviewed_category, subtype=run["selected_subtype"], colors=run["selected_colors"])
                else:
                    result = build_verified_step8(reviewed_category)
                # CART_SIGNAL_PRODUCT_EVIDENCE_V1
                from category_product_evidence import enrich_verified_result
                result = enrich_verified_result(result, reviewed_category, str(run.get("run_id", "unknown")), include_chart=False, include_naver=False)
            except Exception as exc:
                result = {"final_answer": "8단계 H&M 상품 조회에 실패했습니다.",
                          "error": f"{type(exc).__name__}: {exc}"}
        else:
            result = _run_agent_step(step, run, state)
        # CART_SIGNAL_CANDIDATE_GATE_V1
        # 8단계가 실제 Tool 응답에 나온 H&M 상품 ID를 반환했는지 검증
        if step.no == 8 and stage.key == "s1_product_selection":
            from category_candidate_gate import gate_result
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
                ranked = load_ranked_evidence(market.get("category") or "", str(subtype or ""))
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
        if workflow.name == "trend_planning" and step.no == 5 and run.get("review_category"):
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
                from category_subtype_selection import build_inventory
                from category_verified_products import load_purchase_inputs
                try:
                    articles, purchases = load_purchase_inputs()
                    run["subtype_inventory"] = build_inventory(run["review_category"], articles, purchases)
                    note = "5단계 세부 유형별 과거 겨울 온라인 구매 집계 저장 완료. 7단계에서 네이버 상승 근거와 대조합니다."
                    sections.append(note)
                    result["final_answer"] = answer = answer + "\n" + note
                except Exception as exc:
                    run["subtype_inventory"] = {}
                    run.update(status="blocked", blocked_reason=f"5단계 유형별 구매 집계 실패: {type(exc).__name__}")
                    sections.append(run["blocked_reason"])
                    run["history"] = _history(run, step, "failed", run["blocked_reason"])
                    break
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
                sections.append(f"■ {run['review_category']} 대분류 확정 대기\n"
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
    header = f"[{workflow.title} 워크플로 · run {run['run_id']}]"
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
    if base.is_active(previous_run):
        return {}, ("이미 진행 중인 워크플로가 있습니다.\n" + run_status_text(previous_run, campaign)
                    + "\n새로 시작하려면 먼저 아래 [워크플로 취소] 버튼으로 진행 중인 워크플로를 끝내 주세요.")
    missing = [k for k in workflow.requires if not ((campaign or {}).get("values") or {}).get(k)]
    if missing:
        return {}, (f"'{workflow.title}'을(를) 시작하려면 먼저 확정값이 필요합니다: {', '.join(missing)}\n"
                    "메인 기획전 워크플로에서 주력 상품을 확정한 뒤 다시 요청해 주세요.")
    parent = (previous_run or {}).get("run_id", "") if (previous_run or {}).get("workflow") == workflow.parent else ""
    run = base.record_run_event(base.new_run(workflow, request, parent_run_id=parent), "run_started")
    return run, ""


def handle_command(message: str, run: dict | None, campaign: dict | None) -> tuple[str, dict | None, bool]:
    """'워크플로 취소/계속/상태' 명령 처리. 반환: (답변, 바뀐 run 또는 None, 계속 실행할지)"""
    if not run or run.get("status") in ("done", "cancelled"):
        if CANCEL_COMMAND.search(message) or CONTINUE_COMMAND.search(message) or STATUS_COMMAND.search(message):
            return "진행 중인 워크플로가 없습니다.", None, False
        return "", None, False
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
        revised = dict(run, report_draft={"status": "draft_only", "text": report, "target_verified": False})
        return report, base.record_run_event(revised, "provisional_report_drafted", target_verified=False), False
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
        from category_review_selection import parse_category_review, parse_category_confirmation
        from category_review_selection import CATEGORIES
        confirmation = parse_category_confirmation(message)
        category = parse_category_review(message)
        # 대분류 확정은 분석 요청과 별개다. 동일 카테고리 분석 결과가 있으면 재조회하지 않는다.
        if confirmation and run.get("waiting_key") == "category_confirmation" and confirmation == run.get("review_category"):
            revised = dict(run, status="waiting_input", waiting_key="category_decision", blocked_reason="")
            revised = base.record_run_event(revised, "category_approval_requested", category=confirmation)
            return (f"대분류를 {confirmation}(으)로 확정하시겠습니까?\n"
                    f"아래 [{confirmation} 승인] 버튼을 눌러 주세요. 승인 전에는 세부 유형 분석 결과를 확정하지 않습니다."), revised, False
        if (message.strip() == f"{run.get('review_category')} 확정 승인"
                and run.get("waiting_key") == "category_decision"):
            confirmation = run["review_category"]
            revised = dict(run, category_confirmed=True, blocked_reason="")
            revised = base.record_run_event(revised, "category_confirmed", category=confirmation)
            if revised.get("subtype_flow"):
                from category_subtype_selection import guide
                revised.update(status="waiting_input", waiting_key="subtype_colors")
                options = revised.get("subtype_options") or []
                return (f"■ 대분류 승인 완료: {confirmation}\n"
                        + guide(options) + "\n\n원하는 소분류와 색상 1~3개를 채팅으로 입력해 주세요."), revised, False
            revised.update(status="running", waiting_key="")
            return "", revised, True
        # 이미 분석한 다른 카테고리를 확정하려면 해당 카테고리 분석을 먼저 수행한다.
        if confirmation and run.get("waiting_key") in ("category",):
            category = confirmation
        if (confirmation and run.get("waiting_key") == "category_decision"
                and confirmation == run.get("review_category")):
            return f"{confirmation} 대분류 승인 대기 중입니다. [{confirmation} 승인] 버튼을 눌러 주세요.", None, False
        # 이미 대분류가 확정됐으면 동일 카테고리 결정 문구로 분석을 반복하지 않는다.
        if (confirmation and confirmation == run.get("review_category")
                and run.get("category_confirmed") and run.get("waiting_key") == "subtype_colors"):
            from category_subtype_selection import guide
            return "이미 대분류가 확정되었습니다.\n" + guide(run.get("subtype_options") or []), None, False
        # 10단계는 대분류 결정이 아니라 상품 목록 승인 단계다.
        if step.no == 10 and run.get("status") == "waiting_input":
            if confirmation and confirmation == run.get("review_category"):
                return "대분류는 이미 검토했습니다. 10단계에서는 검증된 주력 상품을 승인해 주세요.", None, False
            if str(message).strip().rstrip(".! ") in CATEGORIES:
                return "", None, False
        if category:
            revised = dict(run)
            revised.update(status="running", waiting_key="", step_index=2,
                           review_category=category, review_previous="", previous="",
                           category_confirmed=False)
            revised["subtype_flow"] = category == "아우터"
            revised["subtype_options"] = []
            revised["subtype_inventory"] = {}
            revised["selected_subtype"] = ""
            revised["selected_colors"] = []
            revised["review_buffer"] = {}
            revised["verified_candidate_ids"] = []
            revised["verified_candidate_rows"] = []
            revised["category_market"] = {}
            revised["hm_category_snapshot"] = {}
            revised["category_synthesis"] = {}
            revised["blocked_reason"] = ""
            revised = base.record_run_event(revised, "category_review_started", category=category)
            return "", revised, True
    if (run.get("waiting_key") == "subtype_colors" and run.get("status") == "waiting_input"
            and not (CANCEL_COMMAND.search(message) or STATUS_COMMAND.search(message) or CONTINUE_COMMAND.search(message))):
        from category_subtype_selection import parse_selection, guide, color_guide
        options = run.get("subtype_options") or []
        proposal = message
        if run.get("selected_subtype") and not any(o["keyword"] in message for o in options) and not re.search(r"유형\s*:", message):
            proposal = run["selected_subtype"] + "로 갈게. " + message
        try:
            selection = parse_selection(proposal, options)
            if not selection:
                return guide(options), None, False
        except ValueError as exc:
            return str(exc) + "\n" + guide(options), None, False
        revised = dict(run, selected_subtype=selection["subtype"], selected_colors=selection["colors"])
        if not selection["colors"]:
            option = next(o for o in options if o["keyword"] == selection["subtype"])
            return color_guide(option), revised, False
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
