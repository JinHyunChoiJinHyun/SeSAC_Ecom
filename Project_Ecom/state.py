from typing import Any

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """
    전체 Agent가 공유하는 State입니다.

    각 전문 Agent는 자신의 작업 결과를
    이 State에 저장하고,
    Supervisor Agent는 State를 확인하여
    다음 Agent를 결정합니다.
    """


    # =====================================================
    # 공통 상태
    # =====================================================

    # 사용자의 원래 질문
    question: str

    # Supervisor가 선택한 다음 Agent
    next_agent: str

    # 현재 작업 중인 Agent
    current_agent: str

    # 완료된 작업 기록
    completed_steps: list[str]

    # Supervisor의 판단 이유
    supervisor_reason: str

    # 전체 작업 중 발생한 오류
    error: str


    # =====================================================
    # Data Agent
    # =====================================================

    # Data Agent가 생성한 SQL
    sql_query: str

    # SQL의 목적
    data_purpose: str

    # SQL 실행 결과
    dataframe: Any

    # DataFrame 정보
    data_row_count: int
    data_columns: list[str]

    # Data 관련 오류
    data_error: str


    # =====================================================
    # Prep Agent
    # =====================================================

    # 전처리 완료 DataFrame
    processed_dataframe: Any

    # 수행한 전처리 작업
    prep_actions: list[str]

    # 데이터 품질 검사 결과
    data_quality_report: dict

    # Prep 오류
    prep_error: str


    # =====================================================
    # Model Agent
    # =====================================================

    # 모델 실행 결과
    model_result: Any

    # 사용한 모델 이름
    model_name: str

    # 모델 버전
    model_version: str

    # Model 오류
    model_error: str


    # =====================================================
    # Experiment Agent
    # =====================================================

    # 통계 검정 / A-B Test 결과
    experiment_result: Any

    # Experiment 오류
    experiment_error: str


    # =====================================================
    # Visualization Agent
    # =====================================================

    # 생성한 그래프 결과
    visualization_result: Any

    # 그래프 파일 경로 등
    visualization_path: str

    # Visualization 오류
    visualization_error: str


    # =====================================================
    # 최종 답변
    # =====================================================

    final_answer: str