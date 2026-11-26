"""CART SIGNAL 로컬 선정·추천 검증 게이트.

백엔드 API 불필요. 시장/상품/타깃 특화 Agent의 선정 요청에 적용한다.
PASS는 구조·근거 최소 요건 통과이지 추천의 인과적 정확성을 보증하지 않는다.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from agent_system.orchestrator.tools.category_planning.candidate_gate import validated_candidates
from agent_system.validation.common_validation import MAX_VALIDATION_RETRIES

_SELECTION = re.compile(r"추천|선정|골라|뽑아|선택|정해|후보|발송\s*대상|타깃\s*추출")
_ID = re.compile(r"(?<!\d)\d{10}(?!\d)")
_ROUTES = {"market_analysis", "product_analysis", "targeting"}
_MARKET_TOOLS = ("get_shopping_trends", "analyze_trend_sustainability",
                 "analyze_youtube_trends", "analyze_competitor_reviews",
                 "analyze_selling_points")


def _market_evidence(result: dict) -> bool:
    data = result.get("market_result") or {}
    if not isinstance(data, dict):
        return False
    for name in _MARKET_TOOLS:
        block = data.get(name)
        if isinstance(block, dict) and any(isinstance(block.get(k), list) and block[k]
                                           for k in ("rows", "items", "series")):
            return True
    return False


def _target_evidence(result: dict, question: str = "") -> tuple[bool, list[str]]:
    """추후 모델 출력 스키마가 확정되면 이 어댑터를 확장한다.

    민감 고객 식별자는 로그·답변에 넣지 않는다.
    단순 텍스트 고객 추천은 항상 검증 불가로 취급한다.
    """
    data = result.get("target_result") or {}
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return False, ["TARGET_STRUCTURED_EVIDENCE_MISSING"]
    errors = []
    # 사용자 요청은 불신 데이터의 purpose 필드보다 우선한다.
    # 기존 호출부가 최상위에 purpose를 넣더라도 발송 조건을 건너뛰지 않는다.
    outer_purpose = result.get("purpose")
    inner_purpose = data.get("purpose")
    delivery_requested = bool(re.search(r"발송\s*(?:대상|명단|고객)|발송할|수신\s*대상", question or ""))
    delivery = delivery_requested or outer_purpose == "send_audience" or inner_purpose == "send_audience"
    if outer_purpose and inner_purpose and outer_purpose != inner_purpose:
        errors.append("TARGET_PURPOSE_CONFLICT")
    if delivery and inner_purpose != "send_audience" and outer_purpose != "send_audience":
        errors.append("TARGET_PURPOSE_MISSING")
    keys = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("TARGET_ROW_INVALID")
            continue
        customer = str(row.get("customer_id") or "").strip()
        score = row.get("score")
        if not customer:
            errors.append("TARGET_CUSTOMER_ID_MISSING")
        else:
            keys.append(customer)
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not 0 <= score <= 1:
            errors.append("TARGET_SCORE_INVALID")
        # 실제 발송 명단에서만 동의/제외/상한을 필수로 한다.
        if delivery:
            if row.get("contact_allowed") is not True or row.get("excluded") is not False:
                errors.append("TARGET_DELIVERY_ELIGIBILITY_INVALID")
    if len(keys) != len(set(keys)):
        errors.append("TARGET_CUSTOMER_DUPLICATE")
    if delivery and (data.get("send_cap_checked") is not True and result.get("send_cap_checked") is not True):
        errors.append("TARGET_SEND_CAP_NOT_CHECKED")
    return not errors, sorted(set(errors))


def check_selection(route: str, question: str, result: dict) -> dict:
    """독립 특화 Agent 선정 요청의 반환 근거 검증."""
    is_delivery = route == "targeting" and (
        bool(re.search(r"발송\s*(?:대상|명단|고객)|발송할|수신\s*대상", question or ""))
        or result.get("purpose") == "send_audience"
        or isinstance(result.get("target_result"), dict)
        and result["target_result"].get("purpose") == "send_audience"
    )
    if route not in _ROUTES or (not _SELECTION.search(question or "") and not is_delivery):
        return {"status": "NOT_APPLICABLE", "passed": True, "retryable": False, "errors": []}
    if result.get("not_ready") or result.get("error"):
        return {"status": "FAIL", "passed": False, "retryable": False,
                "errors": ["AGENT_UNAVAILABLE_OR_ERROR"]}
    errors = []
    if route == "product_analysis":
        ids = validated_candidates(result)
        if not ids:
            errors.append("PRODUCT_TOOL_ROWS_MISSING")
        answer_ids = set(_ID.findall(str(result.get("final_answer") or "")))
        if answer_ids and not answer_ids.issubset(set(ids)):
            errors.append("PRODUCT_UNVERIFIED_ID")
        decisions = result.get("decisions") or {}
        if isinstance(decisions, dict):
            selected = set(_ID.findall(str(decisions.get("products") or "")))
            if selected and not selected.issubset(set(ids)):
                errors.append("PRODUCT_DECISION_ID_MISMATCH")
    elif route == "market_analysis":
        if not _market_evidence(result):
            errors.append("MARKET_TOOL_ROWS_MISSING")
    elif route == "targeting":
        ok, problems = _target_evidence(result, question)
        if not ok:
            errors.extend(problems)
    return {"status": "FAIL" if errors else "PASS", "passed": not errors,
            "retryable": bool(errors) and not any(code in errors for code in (
                "TARGET_DELIVERY_ELIGIBILITY_INVALID", "TARGET_SEND_CAP_NOT_CHECKED",
                "TARGET_PURPOSE_CONFLICT", "TARGET_PURPOSE_MISSING")),
            "errors": sorted(set(errors)),
            "scope": "로컬 Tool 근거 및 필수 구조 확인 (LLM 의미 검증 아님)"}


def _block_result(result: dict, verdict: dict) -> dict:
    result = dict(result)
    result["selection_validation"] = verdict
    if verdict["status"] != "FAIL":
        return result
    result["error"] = "SELECTION_VALIDATION_FAILED: " + ", ".join(verdict["errors"])
    result["final_answer"] = (
        "선정·추천 결과가 검증을 통과하지 못해 확정할 수 없습니다. "
        "원본 데이터·상품 근거·선정 조건을 확인해 주세요. "
        "오류 코드: " + ", ".join(verdict["errors"])
    )
    for key in ("decisions", "approval_request", "dataframe", "processed_dataframe",
                "extra_tables", "product_images", "visualization_path", "visualization_result"):
        result.pop(key, None)
    return result


def validate_with_retries(route: str, question: str,
                          produce: Callable[[int, str], dict]) -> dict:
    """최초 생성 + 수정 재생성 최대 2회. 같은 결과를 재검사하지 않는다.

    성공/실패 상태를 result에 기록. API 호출이나 발송 액션은 이 함수에서 수행하지 않는다.
    """
    attempts = []
    feedback = ""
    latest = {}
    for index in range(MAX_VALIDATION_RETRIES + 1):
        try:
            latest = produce(index + 1, feedback)
            if not isinstance(latest, dict):
                latest = {"error": "INVALID_AGENT_RESULT"}
        except Exception as exc:
            latest = {"error": "AGENT_EXECUTION_ERROR: " + type(exc).__name__}
        verdict = check_selection(route, question, latest)
        attempts.append({"attempt": index + 1, "status": verdict["status"],
                         "errors": verdict["errors"]})
        if verdict["status"] != "FAIL" or not verdict["retryable"]:
            break
        # 오류 코드만 보완 요청으로 전달. 기존 LLM 답변/개인정보는 입력하지 않는다.
        feedback = ("이전 생성 결과가 로컬 검증에서 실패했습니다 (" +
                    ", ".join(verdict["errors"]) + "). "
                    "사용자 의도를 유지하고 실제 Tool 결과를 재조회하여 근거 있는 선정 결과를 다시 만드세요. "
                    "허구의 상품 ID·시장 수치·고객 정보를 만들지 마세요.")
    verdict = dict(verdict, attempt=len(attempts), max_attempts=MAX_VALIDATION_RETRIES + 1,
                   history=attempts)
    return _block_result(latest, verdict)
