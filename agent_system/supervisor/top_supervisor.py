"""
Top Supervisor Agent

사용자의 질문 의도를 파악하여 적절한 실행 Agent를 선택한다.

목표 라우팅:
- purchase_targeting
- product_affinity
- campaign_timing
- general_analysis
- multi

현재 상태:
- 구조 골격
- 실제 LLM 라우팅 정책은 후속 구현
"""

from typing import Literal


RouteName = Literal[
    "purchase_targeting",
    "product_affinity",
    "campaign_timing",
    "general_analysis",
    "multi",
]


def route_request(user_request: str) -> RouteName:
    """실제 라우팅 정책을 연결할 자리."""
    raise NotImplementedError(
        "Top Supervisor 라우팅 정책은 아직 구현되지 않았습니다."
    )
