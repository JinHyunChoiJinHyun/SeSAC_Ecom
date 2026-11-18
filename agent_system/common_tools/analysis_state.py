from typing import Any

from langgraph.graph import MessagesState


class AnalysisState(MessagesState):
    """
    공통 분석 그래프(common_tools/graph.py) 전용 State입니다.

    General Analysis Agent가 질문 하나를 처리하는 동안만 쓰입니다.
        Step Router → Data → SQL Executor → (Prep) → (Visualization) → Final Response
    각 단계가 결과(SQL·DataFrame·그래프 경로 등)를 여기에 저장하고,
    분석 그래프의 Step Router(common_tools/router.py)가 이 값을 보고 다음 단계를 정합니다.

    아래 State와는 다릅니다 (이름이 비슷해 헷갈리지 않도록 구분).
    - 대화 상태: agent_system/orchestrator/graph.py 의 OrchestratorState
      (대화 기록·직전 분석 요약·확정값·승인 대기, Checkpointer가 thread_id별로 저장)
    - 확정값 저장 함수: agent_system/orchestrator/tools/conversation_state.py
    이 State는 Checkpointer에 저장되지 않으며, 다른 전문 Agent와 공유하지 않습니다.
    아래 next_agent·current_agent·supervisor_reason의 "Agent/Router"는 분석 그래프 안의 단계를 뜻합니다.
    """


    # =====================================================
    # 공통 상태
    # =====================================================

    # 요청 시작 때 고정한 채팅 모델. API 키는 포함하지 않습니다.
    llm_config: dict[str, str]

    # 사용자의 원래 질문
    question: str

    # Router가 선택한 다음 Agent
    next_agent: str

    # 현재 작업 중인 Agent
    current_agent: str

    # 완료된 작업 기록
    completed_steps: list[str]

    # Router의 판단 이유
    supervisor_reason: str

    # 전체 작업 중 발생한 오류
    error: str


    # =====================================================
    # Data Agent
    # =====================================================

    # Data Agent가 생성한 SQL
    sql_query: str

    # 멀티턴 후속 요청일 때 직전 분석의 SQL (Data Agent 참고용)
    previous_sql: str

    # SQL이 사용한 데이터 출처 key 목록 (common/data_sources.py, 예: hm / amazon)
    data_sources: list[str]

    # SQL의 목적
    data_purpose: str

    # SQL 실행 결과
    dataframe: Any

    # DataFrame 정보
    data_row_count: int
    data_columns: list[str]

    # Data 관련 오류
    data_error: str

    # 0행 결과·SQL 실행 오류 시 Data Agent 재시도 횟수와 전달할 피드백
    data_retry_count: int
    data_retry_feedback: str


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