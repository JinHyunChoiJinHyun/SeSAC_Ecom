"""Orchestrator Tool — manage_context: 이전 대화와 확정된 상품·타깃·일정 참조 + 대화 기억 관리.

LLM을 쓰지 않는 규칙·코드 기능만 모았습니다.
- 대화 기록·직전 분석 요약을 LLM 입력 텍스트로 만들기
- 오래된 메시지 정리 (MAX_STORED_MESSAGES)
- 시간 기준 자동 리셋 (settings.AUTO_RESET_ENABLED / AUTO_RESET_HOURS)
- 후속 질문에서 직전 그래프 형태 유지 (settings.KEEP_PREVIOUS_CHART_TYPE)
- 다시 쓴 질문 표시 (settings.SHOW_REWRITTEN_QUESTION)
- 다음 턴에 쓸 직전 분석 요약 만들기 (DataFrame은 기억에 넣지 않음)
"""

import re
from datetime import datetime, timedelta, timezone

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from agent_system.orchestrator import settings

# 대화 기억에 남길 최대 메시지 수 (사용자+봇). 넘으면 오래된 것부터 지웁니다.
MAX_STORED_MESSAGES = 40
# 대화 기록 중 LLM에 보여 줄 최근 메시지 수
HISTORY_MESSAGE_LIMIT = 12
# 기록의 봇 답변 한 개당 최대 글자 수
HISTORY_ANSWER_CHARS = 700
# 기억에 남기는 결과 미리보기 크기
PREVIEW_ROWS = 10
PREVIEW_CHARS = 1500
MEMORY_ANSWER_CHARS = 1200

# 차트 종류 한글 이름
CHART_TYPE_LABELS = {
    "bar": "막대그래프",
    "line": "선그래프",
    "histogram": "히스토그램",
    "boxplot": "상자그림",
    "scatter": "산점도",
    "heatmap": "히트맵",
}


def _now() -> datetime:
    """현재 시각 (UTC). 테스트에서 바꿀 수 있도록 함수로 둡니다."""
    return datetime.now(timezone.utc)


# =========================================================
# LLM 입력 텍스트
# =========================================================

def format_history(messages) -> str:
    """최근 대화 기록을 LLM 입력용 텍스트로 만듭니다."""

    lines = []
    for message in list(messages)[-HISTORY_MESSAGE_LIMIT:]:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        if isinstance(message, HumanMessage):
            lines.append(f"사용자: {content}")
        elif isinstance(message, AIMessage):
            if len(content) > HISTORY_ANSWER_CHARS:
                content = content[:HISTORY_ANSWER_CHARS] + " …(생략)"
            lines.append(f"CART SIGNAL: {content}")
    return "\n".join(lines) if lines else "(이전 대화 없음)"


def format_last_analysis(last_analysis: dict | None) -> str:
    """직전 분석 요약을 LLM 입력용 텍스트로 만듭니다."""

    if not last_analysis:
        return "(직전 분석 없음)"

    parts = [
        f"- 분석한 질문: {last_analysis.get('question', '')}",
        f"- 처리한 Agent: {last_analysis.get('route_label') or last_analysis.get('route') or '알 수 없음'}",
        f"- 분석 내용: {last_analysis.get('purpose', '')}",
        f"- 데이터 출처: {', '.join(last_analysis.get('sources') or []) or '알 수 없음'}",
        f"- 결과 행 수: {last_analysis.get('row_count', 0)}",
    ]
    if last_analysis.get("chart_type"):
        chart_label = CHART_TYPE_LABELS.get(last_analysis.get("chart_type"), last_analysis.get("chart_type"))
        parts.append(f"- 그래프: {chart_label} ({last_analysis.get('chart_type')}) / {last_analysis.get('chart_title', '')}")
    else:
        parts.append("- 그래프: 없음 (표로 전달)")
    if last_analysis.get("scope_note"):
        parts.append(f"- 범위 메모: {last_analysis.get('scope_note')}")
    if last_analysis.get("error"):
        parts.append(f"- 오류: {last_analysis.get('error')}")
    if last_analysis.get("sql"):
        parts.append(f"- 사용한 SQL:\n{last_analysis.get('sql')}")
    if last_analysis.get("preview"):
        parts.append(f"- 결과 미리보기(상위 행):\n{last_analysis.get('preview')}")
    return "\n".join(parts)


def manage_context(question: str, history_messages, last_analysis, campaign: dict | None = None,
                   pending_approval: dict | None = None) -> str:
    """manage_context Tool: 이전 대화 · 직전 분석 · 확정된 기획전 정보(상품·타깃·일정)를 모아
    대화 판단 LLM 입력 텍스트를 만듭니다."""

    from agent_system.orchestrator.tools.conversation_state import format_campaign

    pending = ""
    if pending_approval:
        pending = ("[승인 대기 중]\n"
                   f"{pending_approval.get('question', '')}\n"
                   "(사용자가 이번 메시지에서 다른 질문을 했습니다. 질문에 답하면 승인 요청은 다시 안내됩니다)\n\n")
    return (
        "[이전 대화]\n"
        f"{format_history(history_messages)}\n\n"
        "[직전 분석 요약]\n"
        f"{format_last_analysis(last_analysis)}\n\n"
        "[확정된 기획전 정보]\n"
        f"{format_campaign(campaign)}\n\n"
        f"{pending}"
        "[이번 사용자 메시지]\n"
        f"{question}"
    )


# 이전 이름 호환
build_intent_input = manage_context


# =========================================================
# 기억 정리 · 자동 리셋
# =========================================================

def trim_messages(messages) -> list:
    """MAX_STORED_MESSAGES를 넘는 오래된 메시지를 지우는 RemoveMessage 목록."""

    if len(messages) <= MAX_STORED_MESSAGES:
        return []
    return [
        RemoveMessage(id=message.id)
        for message in messages[: len(messages) - MAX_STORED_MESSAGES]
        if getattr(message, "id", None)
    ]


def remove_all(messages) -> list:
    return [RemoveMessage(id=message.id) for message in messages if getattr(message, "id", None)]


def should_auto_reset(last_activity_at: str, now: datetime | None = None) -> bool:
    """마지막 메시지 후 설정한 시간이 지났으면 True."""

    if not settings.AUTO_RESET_ENABLED or not last_activity_at:
        return False
    try:
        last = datetime.fromisoformat(last_activity_at)
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = now or _now()
    return now - last >= timedelta(hours=settings.AUTO_RESET_HOURS)


# =========================================================
# 후속 질문 보정
# =========================================================

# 다시 쓴 질문에 이미 그래프 요청이 있는지 확인하는 단어 (common_tools/router.py와 같은 기준)
_CHART_WORDS = (
    "그래프", "차트", "시각화", "히스토그램", "산점도", "상자그림", "박스플롯", "히트맵",
    "plot", "chart", "histogram", "scatter", "boxplot", "heatmap",
)
# 사용자가 그래프 없이 표만 원한다는 표현 ("대표로"처럼 다른 단어 안의 "표로"는 제외)
_TABLE_ONLY_PATTERN = re.compile(r"(?<![가-힣])표\s*(로|만)|그래프\s*없이|차트\s*없이|숫자만|텍스트로")


def keep_previous_chart_type(standalone: str, user_question: str,
                             last_analysis: dict, is_followup: bool) -> str:
    """직전 분석이 그래프였고 후속 질문에 형태 지정이 없으면 같은 그래프 종류를 붙입니다."""

    if not settings.KEEP_PREVIOUS_CHART_TYPE or not is_followup:
        return standalone
    chart_type = (last_analysis or {}).get("chart_type", "")
    label = CHART_TYPE_LABELS.get(chart_type)
    if not label:
        return standalone
    lowered = f"{standalone} {user_question}".lower()
    if any(word in lowered for word in _CHART_WORDS):
        return standalone
    if _TABLE_ONLY_PATTERN.search(user_question or ""):
        return standalone
    return f"{standalone.rstrip(' .')} (직전과 같이 {label}로 보여줘)"


REWRITTEN_NOTICE_PREFIX = "이전 대화를 반영해"


def _normalize(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def add_rewritten_question_notice(final_answer: str, user_question: str,
                                  standalone_question: str, is_followup: bool) -> str:
    """후속 질문을 다시 써서 분석했으면 답변 맨 위에 실제 분석 질문을 한 줄 보여 줍니다."""

    if not settings.SHOW_REWRITTEN_QUESTION:
        return final_answer
    if not is_followup or not standalone_question:
        return final_answer
    if _normalize(standalone_question) == _normalize(user_question):
        return final_answer

    notice = f'{REWRITTEN_NOTICE_PREFIX} "{standalone_question.strip()}"(으)로 분석했습니다.'
    return f"{notice}\n\n{final_answer}"


# =========================================================
# 결과 → 기억
# =========================================================

def memory_answer(final_answer: str) -> str:
    """기억에 남길 답변: 분석 기준(SQL) 부분을 빼고 앞부분만 남깁니다."""

    text = str(final_answer or "")
    for marker in ("\n\n---\n분석 기준", "\n\n==============================\n[기반 데이터 검증 정보]"):
        if marker in text:
            text = text.split(marker, 1)[0]
    text = text.strip()
    if len(text) > MEMORY_ANSWER_CHARS:
        text = text[:MEMORY_ANSWER_CHARS] + " …(생략)"
    return text or "(분석 결과를 전달했습니다)"


def summarize_analysis(question: str, result: dict, spec=None) -> dict:
    """다음 턴에서 참고할 직전 분석 요약을 만듭니다 (DataFrame 제외)."""

    df = result.get("processed_dataframe")
    if not isinstance(df, pd.DataFrame):
        df = result.get("dataframe")

    preview = ""
    row_count = 0
    columns = []
    if isinstance(df, pd.DataFrame):
        row_count = int(len(df))
        columns = [str(column) for column in df.columns]
        preview = df.head(PREVIEW_ROWS).to_string(index=False)
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS] + " …"

    visualization = result.get("visualization_result") or {}

    return {
        "question": question,
        "purpose": str(result.get("data_purpose") or ""),
        "sql": str(result.get("sql_query") or ""),
        "sources": list(result.get("data_sources") or []),
        "row_count": row_count,
        "columns": columns,
        "preview": preview,
        "scope_note": str(result.get("data_scope_note") or ""),
        "chart_type": str(visualization.get("chart_type") or "") if isinstance(visualization, dict) else "",
        "chart_title": str(visualization.get("title") or "") if isinstance(visualization, dict) else "",
        "error": str(result.get("error") or ""),
        # 어느 Agent가 처리했는지 (후속 질문을 같은 Agent로 보내기 위해)
        "route": spec.name if spec else str(result.get("route") or ""),
        "route_label": spec.label if spec else str(result.get("route_label") or ""),
    }
