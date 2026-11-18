"""메인 시나리오 3단계 — 타깃 선정과 발송 준비 (원본 17~22단계). Targeting → Content → Validation → Reporting.

점수·추천·세분화 → 명단 → 세그먼트 메시지 → 검증 → 명단 확정
타깃 선정 Agent 구현 전이라 지금은 17단계에서 멈춥니다.
"""

from agent_system.workflows.base import Stage, Step

STAGE = Stage(
    key="s3_targeting",
    title="타깃 선정과 발송 준비",
    requires=("products",),
    steps=(
        Step(17, "타깃 점수·고객별 추천 상품", owner="targeting", instruction=(
            "기획전 상품 '{products}'별 타깃 점수를 계산하고 고객별 추천 상품을 지정해 주세요.")),
        Step(18, "세그먼트 요약", owner="reporting", optional=True, instruction=(
            "4분면(핵심 타깃·재활성화·교차 판매·제외)별 인원, 세그먼트 요약, 대표 근거, 발송 규모·채널 제안을 정리해 주세요.\n{previous}")),
        Step(19, "발송 명단 확정", owner="targeting", instruction=(
            "연락 가능 고객(Active=1)만 남기고 고객당 발송 상한을 적용해 명단을 확정하고, 핵심 타깃 무작위 10%를 대조군으로 두세요. "
            "빠진 고객은 사유를 기록해 주세요.")),
        Step(20, "세그먼트별 메시지", owner="content_generation", instruction=(
            "공통 소구점 '{selling_points}'·고객별 취향 근거·추천 상품·기획전 링크로 세그먼트별 문구를 작성해 주세요.")),
        Step(21, "메시지 근거 검사", owner="validation", instruction="문구의 근거를 검사하고 걸린 표현을 표시해 주세요.\n{previous}"),
        Step(22, "명단·문구 1차 확정", kind="select", owner="marketer", selects="target", instruction=(
            "발송 명단과 문구를 확인하고 확정해 주세요. (예: \"타깃은 핵심 타깃 세그먼트로 확정\")")),
    ),
)
