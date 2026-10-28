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


    # -----------------------------------------------------
    # 데이터 범위 / 샘플링 정책 메타데이터
    # -----------------------------------------------------

    # full_result:
    #   전체 데이터 기준의 조회/집계 결과
    # visual_sample:
    #   시각화 목적의 행 단위 표본
    # correlation_matrix:
    #   DuckDB에서 전체 데이터 기준으로 계산한 상관계수 행렬
    data_result_type: str

    # SQL 결과가 전체 모집단을 그대로 나타내는지 여부
    # visual_sample이면 False, correlation_matrix/full_result면 보통 True
    data_uses_full_population: bool

    # SQL 단계에서 시각화용 샘플을 사용했는지 여부
    data_is_sampled: bool

    # 요청한 최대 샘플 행 수. 샘플링하지 않았으면 0
    data_sample_size: int

    # 샘플링 사유
    data_sampling_reason: str

    # 사용자에게 설명할 데이터 범위 메모
    data_scope_note: str


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