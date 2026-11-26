"""특화 Agent 등록부 (Agent Registry).

Orchestrator는 여기에 등록된 Agent 중에서 요청을 처리할 Agent 하나를 고릅니다.
새 특화 Agent(신제품 기획, 트렌드 분석 등)를 추가할 때는

    1. agent_system/agents/<이름>_agent.py 에 run(request) 함수 작성 (③~⑦은 agent_system/base_agent.py 공통 실행 사용)
       (입력: AgentRequest / 출력: 아래 "결과 형식"을 따르는 dict)
    2. 아래 AGENTS 목록에 AgentSpec 하나 추가

만 하면 됩니다. Orchestrator 그래프·Slack 전달 코드는 고치지 않습니다.

팀 규칙: 특화 Agent의 Tool은 반드시 agent_system/common_tools/tool_call_log.run_logged_tool()로 실행해
어떤 Tool을 어떤 인자로 불렀고 결과(성공/사용 불가/오류)가 어땠는지 로그를 남기고,
Agent 실행 끝에 log_tool_summary()로 요약을 남깁니다. (예: agent_system/agents/market_analysis_agent.py)

11/16 회의 결정 (전문 Agent 7개)은 모두 등록되어 있습니다.
①·②는 켜져 있고(enabled=True), ③~⑦은 코드만 있고 데이터·예측 모델·백엔드 API 연결 전이라 enabled=False입니다
(호출해도 Tool이 '미구현'을 돌려줌). 연결되면 enabled=True로 바꾸면 Orchestrator·워크플로가 바로 사용합니다.
자세한 절차: agent_system/orchestrator/README.md

결과 형식 (모든 Agent 공통, Delivery와 대화 기억이 이 이름을 읽습니다)
--------------------------------------------------------------------
필수
    final_answer        str    Slack에 보낼 최종 답변
선택
    dataframe           DataFrame   결과 표 (CSV 첨부, 직전 분석 미리보기에 사용)
    processed_dataframe DataFrame   전처리 결과 (있으면 dataframe보다 우선)
    visualization_path  str    그래프 PNG 경로 (visualization_output/ 아래)
    visualization_result dict  {"chart_type": ..., "title": ..., "file_path": ...}
    sql_query           str    사용한 SQL (후속 질문 때 참고)
    data_sources        list   사용한 데이터 출처 key (예: ["hm", "amazon"])
    data_purpose        str    분석 내용 한 줄
    data_scope_note     str    범위 메모
    error               str    오류 (있으면 Slack에 실패로 처리)
    decisions           dict   바로 반영할 확정값 {"category": "니트"} (update_conversation_state)
    approval_request    dict   사용자 승인 후 반영할 확정값 {"title", "question", "updates": {...}} (check_approval)
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd


# 대화 그래프에서 이미 쓰는 노드 이름 (Agent 이름으로 쓸 수 없음)
RESERVED_NAMES = {"orchestrator", "__start__", "__end__"}


@dataclass
class AgentRequest:
    """Orchestrator가 특화 Agent에 넘기는 입력."""

    question: str                     # 이전 대화를 반영해 다시 쓴 질문 (분석 기준)
    user_question: str                # 사용자가 실제로 보낸 원문
    is_followup: bool = False         # 직전 분석을 이어받는 후속 요청인지
    last_analysis: dict = field(default_factory=dict)  # 직전 분석 요약 (같은 Agent였을 때만 채움)
    llm_config: dict | None = None    # 요청에 고정된 LLM 설정 (API 키 없음)
    campaign: dict = field(default_factory=dict)       # 확정된 기획전 정보 {"values": {...}, "stage": ...} (manage_context)
    # 사람이 명시적으로 승인한 실행 Tool 이름 (예: ("send_approval_email",)). 비어 있으면 메일·발송 같은
    # 실행 Tool은 호출되지 않습니다 (agent_system/base_agent.py). 승인 기록 기능이 생기면 그 기록으로 채웁니다.
    approved_actions: tuple = ()


@dataclass(frozen=True)
class AgentSpec:
    """특화 Agent 1개의 등록 정보."""

    # Orchestrator가 고르는 이름이자 그래프 노드 이름 (영문 소문자·숫자·_)
    name: str
    # 사용자·로그에 보이는 이름
    label: str
    # Orchestrator(LLM)가 이 Agent를 고를지 판단하는 설명. 무엇을 하고 무엇을 하지 않는지 구체적으로
    description: str
    # 이 Agent로 보내야 하는 질문 예시 (Orchestrator 프롬프트에 들어감)
    examples: tuple[str, ...] = ()
    # 실행 함수 경로 "모듈:함수" (함수 시그니처: run(request: AgentRequest) -> dict)
    runner: str = ""
    # False면 등록만 해 두고 라우팅 대상에서 제외
    enabled: bool = True
    # 판단이 애매하거나 Orchestrator가 실패했을 때 보낼 기본 Agent (1개만 True)
    is_default: bool = False
    # 이 Agent가 쓸 Tool 이름 (11/16 설계안). 안내·문서용이며 실행에는 영향 없음
    tools: tuple[str, ...] = ()
    # 메인·서브 시나리오에서 맡는 일 (안내·문서용)
    scenario: str = ""

    def load_runner(self) -> Callable[[AgentRequest], dict]:
        module_name, _, function_name = self.runner.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, function_name)


# =========================================================================
# 등록된 특화 Agent
# =========================================================================

AGENTS: list[AgentSpec] = [
    AgentSpec(
        name="general_analysis",
        label="일반 분석",
        # 회의록에는 없지만 사용자의 잡다한 데이터 질문을 처리하기 위해 유지 (다음 멘토링 때 확인)
        description=(
            "등록된 데이터(H&M 구매·상품·고객, Amazon 리뷰 등)를 SQL로 조회·집계하고, "
            "필요하면 전처리·그래프(막대·선·히스토그램·산점도·히트맵)까지 만드는 공통 분석. "
            "Top N, 추이, 비중, 분포, 비교, 상관관계 같은 일반 데이터 질문을 처리합니다. "
            "예측 모델은 없습니다."
        ),
        examples=(
            "H&M 여성 상품 유형별 구매 건수 Top 10이랑 비중 알려줘",
            "월별 온라인 판매 추이를 선그래프로",
            "Amazon 여성 스웨터 리뷰 별점 분포",
        ),
        runner="agent_system.specialized_agents.general_analysis_agent:run",
        is_default=True,
    ),
    # ---------------------------------------------------------------------
    # 11/16 회의 결정 · 「CART_SIGNAL 에이전트 및 Tool 설계안」: 전문 Agent 7개
    # 구현 전이라 enabled=False. runner 모듈을 만들고 True로 바꾸면 Orchestrator가 라우팅합니다.
    # MVP 우선순위: P0 = 시장 분석·상품 분석·콘텐츠 생성·검증·결과 정리 / P1 = 타깃 선정·캠페인 실행
    # ---------------------------------------------------------------------
    AgentSpec(
        name="market_analysis",
        label="시장 분석",
        description=(
            "시장 관심도와 외부 상품 리뷰를 분석해 기획전 방향과 콘텐츠 근거를 제공합니다. "
            "네이버 쇼핑인사이트 검색 추이, 관심 상승률과 기획전 기간까지의 지속 가능성, 유튜브 영상 추이, "
            "무신사 유사 상품 리뷰의 언급률·불만 비율, 경쟁 상품의 소구점(소재·핏·관리·디자인), "
            "셀럽 착장·패션 매체의 트렌드 근거를 다룹니다. 자사(H&M) 상품 선정은 하지 않습니다."
        ),
        examples=("요즘 니트 관련 키워드 중 뜨는 게 뭐야?", "하프집업 니트 리뷰에서 고객이 자주 보는 항목은?",
                  "퍼플 니트가 왜 주목받는지 근거 찾아줘"),
        runner="agent_system.agents.market_analysis_agent:run",
        enabled=True,   # 구현됨. 백엔드 연결 전에는 무신사 샘플만, 네이버·유튜브·패션 매체는 '미연결' 표시
        tools=("get_shopping_trends", "analyze_trend_sustainability", "analyze_youtube_trends",
               "analyze_competitor_reviews", "analyze_selling_points", "search_fashion_trends"),
        scenario="메인: 카테고리 선정·키워드 평가·경쟁 상품 소구점 분석 / 서브: 퍼플 트렌드 근거, 리뷰 기반 촬영 포인트",
    ),
    AgentSpec(
        name="product_analysis",
        label="상품 분석",
        description=(
            "자사(H&M) 상품 정보와 구매 기록을 분석해 적합한 상품을 고릅니다. "
            "카테고리·속성·스타일 태그로 상품 검색, 트렌드 키워드와 상품 속성 매칭, "
            "같은 기간·조건의 상품별 구매 실적 비교, 상품 상세(설명·소재·색상·이미지) 조회, "
            "후보 상품의 키워드 일치도와 판매 근거 비교를 합니다. "
            "상품을 고르거나 후보를 비교하는 질문용이며, 단순 집계(Top N·월별 추이·비중·분포)는 general_analysis가 맡습니다."
        ),
        examples=("하프집업 니트 중 우리 상품 후보 찾아줘", "브라운이랑 네이비 후보 최근 4주 온라인 구매 비교해줘",
                  "퍼플 니트랑 케이블 니트에 맞는 H&M 상품 있어?"),
        runner="agent_system.agents.product_analysis_agent:run",
        enabled=True,   # 구현됨. 백엔드 연결 전에는 로컬 H&M DuckDB(읽기 전용), 기간은 데이터 마지막 날 기준
        tools=("search_products", "match_product_keywords", "analyze_product_sales", "get_product_details",
               "compare_product_candidates"),
        scenario="메인: 트렌드에 맞는 H&M 주력 상품 추천 / 서브: 브라운·네이비 구매 기록 비교, 첫 화면 상품 결정",
    ),
    AgentSpec(
        name="targeting",
        label="타깃 선정",
        description=(
            "고객 구매 이력과 취향을 바탕으로 고객별·상품별 구매 가능성 점수를 계산하고, "
            "고객별 추천 상품을 정하며, 핵심 타깃·재활성화·교차 판매·제외 그룹으로 나눈 뒤 "
            "연락 가능 여부·발송 상한·대조군을 반영한 최종 발송 명단을 만듭니다."
        ),
        examples=("이번 기획전 발송 대상 뽑아줘", "핵심 타깃 고객에게 어떤 상품을 추천할지 정해줘"),
        runner="agent_system.agents.model_targeting_agent:run",
        enabled=True,   # 코드 있음. 데이터·예측 모델·백엔드 API 연결 후 True (지금은 Tool이 '미구현' 반환)
        tools=("calculate_target_scores", "recommend_customer_products", "segment_customers",
               "build_campaign_audience"),
        scenario="메인: 고객 세분화·고객별 추천 상품·발송 명단 (점수 계산과 추천은 별도 Tool)",
    ),
    AgentSpec(
        name="content_generation",
        label="콘텐츠 생성",
        description=(
            "확정된 상품·소구점·타깃 정보로 마케팅 콘텐츠를 만듭니다. "
            "기획전 대표 문구·상품 카드 문구, 기획전 HTML 페이지 틀, 세그먼트별 맞춤 메시지, "
            "리뷰·상품 정보 기반 필수 촬영 컷과 추가 컷 제안을 생성합니다."
        ),
        examples=("기획전 대표 문구 만들어줘", "핵심 타깃용 메시지 써줘", "꼭 찍어야 할 촬영 컷 알려줘"),
        runner="agent_system.agents.content_generation_agent:run",
        enabled=False,   # 코드 있음. 데이터·예측 모델·백엔드 API 연결 후 True (지금은 Tool이 '미구현' 반환)
        tools=("generate_campaign_copy", "build_campaign_page", "generate_segment_messages",
               "plan_product_photography"),
        scenario="메인: 기획전 문구·HTML 페이지·세그먼트 메시지 / 서브: 촬영 컷 기획 (요청서 작성은 결과 정리)",
    ),
    AgentSpec(
        name="validation",
        label="검증",
        description=(
            "생성된 결과물이 실제 데이터와 승인된 조건에 맞는지 확인합니다. "
            "분석 수치·출처·상품 속성 근거, 허위·과장 표현·미확인 수치·금지 문구, "
            "기획전 기간·혜택·링크·발송 조건을 검사합니다. 무신사 리뷰를 H&M 상품 특성으로 단정하지 않도록 막습니다."
        ),
        examples=("이 문구에 근거 없는 표현 있는지 확인해줘",),
        runner="agent_system.agents.validation_agent:run",
        enabled=False,   # 코드 있음. 데이터·예측 모델·백엔드 API 연결 후 True (지금은 Tool이 '미구현' 반환)
        tools=("validate_data_evidence", "validate_marketing_copy", "validate_campaign_conditions"),
        scenario="메인: 페이지·메시지·보고서 근거 검사 / 서브: 촬영 요청서 근거 확인",
    ),
    AgentSpec(
        name="reporting",
        label="결과 정리",
        description=(
            "각 Agent의 결과를 시각화하고 실행 문서로 통합합니다. "
            "트렌드·상품 비교·타깃 결과 그래프, 기획전 실행 계획 보고서, "
            "촬영 우선순위·구도·상품 정보를 담은 촬영 요청서, 보고서·요청서 PDF 출력, "
            "대화 중 분석을 묶은 일반 분석 보고서를 만듭니다 (문서 작성 담당)."
        ),
        examples=("지금까지 결과로 실행 계획 보고서 만들어줘", "촬영 요청서 한 장으로 정리해줘"),
        runner="agent_system.agents.reporting_agent:run",
        enabled=False,   # 코드 있음. 데이터·예측 모델·백엔드 API 연결 후 True (지금은 Tool이 '미구현' 반환)
        tools=("visualize_analysis_results", "generate_campaign_report", "generate_photography_brief",
               "export_report_pdf", "generate_analysis_report"),
        scenario="메인: 후보 시각화·실행 계획 보고서 / 서브: 촬영 요청서 PDF",
    ),
    AgentSpec(
        name="campaign_execution",
        label="캠페인 실행",
        description=(
            "승인 절차와 실제 마케팅 실행을 관리합니다. 팀장 승인 요청 메일, 승인·수정 요청·반려 상태 관리, "
            "승인된 HTML 파일 전달, 기획전 페이지 URL 확인, 확정 타깃에게 메시지 발송, 발송 결과·사후 확인 예약. "
            "실제 발송이 포함되므로 명시적인 승인 없이 실행하지 않습니다."
        ),
        examples=("팀장님께 승인 요청 보내줘",),
        runner="agent_system.agents.campaign_execution_agent:run",
        enabled=False,   # 코드 있음. 데이터·예측 모델·백엔드 API 연결 후 True (지금은 Tool이 '미구현' 반환)
        tools=("send_approval_email", "track_approval_status", "deliver_approved_page", "check_campaign_url",
               "send_campaign_messages", "schedule_campaign_followup"),
        scenario="메인: 승인 요청 → 승인 후 페이지 전달·URL 확인·발송·사후 확인 (규모가 부담되면 Orchestrator와 합칠 수 있음)",
    ),
]


# =========================================================================
# 조회 함수
# =========================================================================

def _validate_registry():
    names = [spec.name for spec in AGENTS]
    if len(names) != len(set(names)):
        raise ValueError(f"Agent 이름이 중복되었습니다: {names}")
    for spec in AGENTS:
        if spec.name in RESERVED_NAMES:
            raise ValueError(f"Agent 이름으로 쓸 수 없습니다: {spec.name}")
        if not spec.name.replace("_", "").isalnum() or not spec.name[0].isalpha() or spec.name != spec.name.lower():
            raise ValueError(f"Agent 이름은 영문 소문자·숫자·_ 만 사용합니다: {spec.name}")
        if ":" not in spec.runner:
            raise ValueError(f"runner는 '모듈:함수' 형식이어야 합니다: {spec.name}")
    defaults = [spec.name for spec in AGENTS if spec.enabled and spec.is_default]
    if len(defaults) != 1:
        raise ValueError(f"enabled이고 is_default=True인 Agent가 정확히 1개여야 합니다: {defaults}")


_validate_registry()


def get_agents(enabled_only: bool = True) -> list[AgentSpec]:
    return [spec for spec in AGENTS if spec.enabled or not enabled_only]


def get_agent(name: str) -> AgentSpec | None:
    for spec in AGENTS:
        if spec.name == name:
            return spec
    return None


def get_default_agent() -> AgentSpec:
    return next(spec for spec in AGENTS if spec.enabled and spec.is_default)


def normalize_agent_result(name: str, result: Any) -> dict:
    """Agent 결과가 공통 형식을 따르는지 확인하고 dict로 맞춥니다.

    형식이 틀리면 Slack에서 실패로 처리되도록 error를 채웁니다.
    """

    if not isinstance(result, dict):
        return {
            "final_answer": f"{name} Agent가 올바른 결과를 반환하지 않았습니다.",
            "error": f"{name} Agent 결과가 dict가 아닙니다: {type(result).__name__}",
        }

    result = dict(result)
    answer = result.get("final_answer")
    if not isinstance(answer, str) or not answer.strip():
        result["error"] = result.get("error") or f"{name} Agent가 final_answer를 반환하지 않았습니다."
        result["final_answer"] = str(answer or "") or "답변을 만들지 못했습니다."

    for key in ("dataframe", "processed_dataframe"):
        if key in result and result[key] is not None and not isinstance(result[key], pd.DataFrame):
            result[key] = None

    return result
