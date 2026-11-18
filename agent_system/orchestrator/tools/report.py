"""Orchestrator Tool — 보고서 요청 처리 (PDF + Word 2부, 규칙 판단 · LLM 없음).

    "보고서 만들어줘", "분석 보고서 줘"           → 일반 분석 보고서: 대화 중 분석 결과(analysis_log) 묶음
    "기획전 보고서 만들어줘", "실행 계획 보고서" → 기획전 실행 계획 보고서: 확정값 + 워크플로 단계 결과

만든 파일은 delivery_output/reports/에 저장하고 Slack에 첨부합니다 (result["attachments"]).
기획전 보고서는 ⑥ 결과 정리 Agent(백엔드 /reports/campaign) 연결 전이라 '초안'으로 표시합니다.
연결 후에는 handle_report_request의 campaign 분기에서 그 결과를 받아 같은 출력기(writers.py)로 내보내면 됩니다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_system.common_tools.report_export import builders, writers

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_ROOT / "delivery_output" / "reports"
KST = timezone(timedelta(hours=9))

_ACTION = r"(만들|작성|줘|주세요|뽑|내보|출력|생성|부탁|정리해)"
CAMPAIGN_REPORT = re.compile(r"(기획전|캠페인|실행\s*계획)\s*(실행\s*계획\s*)?(보고서|리포트).{0,8}" + _ACTION)
ANALYSIS_REPORT = re.compile(r"(보고서|리포트|report).{0,8}" + _ACTION, re.I)


def detect(message: str) -> str:
    """'campaign' / 'analysis' / '' (보고서 요청 아님)"""
    text = str(message or "")
    if CAMPAIGN_REPORT.search(text):
        return "campaign"
    if ANALYSIS_REPORT.search(text):
        return "analysis"
    return ""


def handle_report_request(kind: str, analysis_log: list | None, campaign: dict | None, run: dict | None,
                          now: datetime | None = None) -> dict:
    """보고서 만들기. 반환: {"reply", "attachments": [경로…], "errors": {...}}"""
    entries = list(analysis_log or [])
    if kind == "campaign":
        has_campaign = bool(((campaign or {}).get("values") or {})) or bool((run or {}).get("workflow"))
        if not has_campaign:
            return {"reply": "기획전 확정값이나 진행한 워크플로가 없어 기획전 실행 계획 보고서를 만들 수 없습니다.\n"
                             "'기획전 준비 시작하자'로 워크플로를 진행하거나 확정값을 정한 뒤 다시 요청해 주세요.",
                    "attachments": [], "errors": {}}
        doc = builders.build_campaign_report(campaign, run, entries, now)
        name, label = "기획전_실행계획_보고서", "기획전 실행 계획 보고서 (초안)"
    else:
        if not entries:
            return {"reply": "보고서로 묶을 분석 결과가 아직 없습니다. 분석을 먼저 요청해 주세요. "
                             "(예: \"H&M 여성 상품 유형별 구매 Top 10\")", "attachments": [], "errors": {}}
        doc = builders.build_analysis_report(entries, now)
        name, label = "분석_보고서", f"분석 보고서 (분석 {len(entries)}건)"

    stamp = (now or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y%m%d_%H%M%S")
    out = writers.export_report(doc, REPORT_DIR, f"CART_SIGNAL_{name}_{stamp}")
    lines = [f"{label}를 만들었습니다. PDF와 Word 파일을 첨부합니다."]
    if kind == "analysis":
        lines += [f"- {i}. {e.get('user_question') or e.get('question')}" for i, e in enumerate(entries, start=1)]
    else:
        lines.append("백엔드 보고서 기능 연결 전이라 대화에 기록된 확정값·단계 결과로 만든 초안입니다 (미진행 단계는 그대로 표시).")
    for ext, error in out["errors"].items():
        lines.append(f"- {ext.upper()} 생성 실패: {error}")
    if not out["files"]:
        lines[0] = f"{label}를 만들지 못했습니다."
    return {"reply": "\n".join(lines), "attachments": out["files"], "errors": out["errors"]}
