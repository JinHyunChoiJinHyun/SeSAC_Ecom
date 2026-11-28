"""Orchestrator Tool — 보고서 요청을 알아보고 ⑥ Reporting Agent에 넘김 (규칙 판단 · LLM 없음).

문서 작성(Word 초안·최종 PDF)은 Reporting Agent가 담당합니다 (agent_system/agents/reporting_agent.py, 11/20).
이 파일은 요청 판단(detect)과 저장 폴더(REPORT_DIR)만 정합니다.

    "보고서 만들어줘", "분석 보고서 줘"           → 일반 분석 보고서: 대화 중 분석 결과(analysis_log) 묶음
    "기획전 보고서 만들어줘", "실행 계획 보고서" → 기획전 실행 계획 보고서: 확정값 + 워크플로 단계 결과

만든 Word 초안은 delivery_output/reports/에 저장하고 Slack에 첨부합니다 (result["attachments"]).
마케터가 Word를 고쳐 다시 올리면 Reporting Agent가 최종 PDF로 바꿉니다 (interfaces/slack/report_finalize.py).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_ROOT / "delivery_output" / "reports"
KST = timezone(timedelta(hours=9))

_ACTION = r"(만들|작성|줘|주세요|뽑|내보|출력|생성|부탁|정리해)"
CAMPAIGN_REPORT = re.compile(r"(기획전|캠페인|실행\s*계획)\s*(실행\s*계획\s*)?(보고서|리포트).{0,8}" + _ACTION)
ANALYSIS_REPORT = re.compile(r"(보고서|리포트|report).{0,8}" + _ACTION, re.I)


def detect(message: str) -> str:
    """'campaign' / 'analysis' / '' (보고서 요청 아님)"""
    text = str(message or "")
    if re.search(r"(?:취소|보내|발송|변환|수정하지|만들지|작성하지)",text):
        return ""
    if re.search(r"(?:기획전|캠페인).{0,30}(?:제안서|보고서|리포트).{0,15}"+_ACTION,text) or re.search(r"(?:운영\s*제안서|제안서\s*초안).{0,15}"+_ACTION,text):
        return "campaign"
    if re.search(r"(?:워드|Word)\s*(?:파일)?(?:로)?\s*(?:정리|작성|만들|생성)",text,re.I):
        return "analysis"
    if CAMPAIGN_REPORT.search(text):
        return "campaign"
    if ANALYSIS_REPORT.search(text):
        return "analysis"
    return ""


def handle_report_request(kind: str, analysis_log: list | None, campaign: dict | None, run: dict | None,
                          now: datetime | None = None) -> dict:
    """보고서 만들기 → ⑥ Reporting Agent에 넘김. 반환: {"reply", "attachments": [경로…], "errors": {...}}"""
    from agent_system.agents import reporting_agent
    made = reporting_agent.create_report(kind, analysis_log, campaign, run, report_dir=REPORT_DIR, now=now)
    return {"reply": made["reply"], "attachments": made["attachments"], "errors": made["errors"]}
