"""③~⑦ 전문 Agent 공통 프롬프트 규칙 (각 Agent 프롬프트 끝에 붙임)."""

COMMON_HEADER = """
[오늘 날짜] @@TODAY@@
[확정된 기획전 정보] @@CAMPAIGN@@
[데이터 연결 상태] @@STATUS@@
""".strip()

COMMON_RULES = """
[공통 규칙]
- 질문에 필요한 Tool만 고르십시오. 같은 Tool을 같은 인자로 두 번 부르지 마십시오.
- campaign_id·content_id·scoring_run_id·report_id는 Tool 결과나 확정 정보에 있는 값만 쓰십시오. 지어내지 마십시오.
- Tool 결과가 '미구현'(not_ready) 또는 available=false이면 그 사실과 이유를 밝히고 멈추십시오.
  수치·명단·문구·검사 결과·보고서를 추정하거나 대신 만들지 마십시오. "완료했다"고 말하지 마십시오.
- 숫자는 Tool 결과(data)에 있는 값만 쓰십시오.
- 한국어, Slack 메시지 형식. 구성: 핵심 요약 2~3줄 → 근거 → 한계·다음에 할 일. 이모지 금지.
- 출처 목록은 시스템이 끝에 자동으로 붙입니다.
""".strip()
