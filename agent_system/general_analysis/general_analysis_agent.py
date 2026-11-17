"""
General Analysis Agent

일반 데이터 조회·전처리·모델 분석·시각화 요청을 독립적으로 처리한다.

현재 단계에서는 기존 shared_analysis graph를 재사용한다.
"""

from agent_system.shared_analysis.graph import graph as shared_analysis_graph


def get_general_analysis_graph():
    """General Analysis Agent가 사용할 공통 분석 그래프를 반환한다."""
    return shared_analysis_graph
