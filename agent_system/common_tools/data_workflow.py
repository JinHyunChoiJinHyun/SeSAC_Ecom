from langchain.agents.structured_output import ToolStrategy
from typing import Literal

from pydantic import BaseModel, Field
from langchain.agents import create_agent

from env.gpt_client import (
    get_openai_chat_client
)

from common.data_sources import (
    build_agent_source_docs,
    describe_sources_for_prompt,
    get_available_sources,
    merged,
)


# =========================================================
# Data Agent 최종 출력 구조
# =========================================================

class DataQueryPlan(BaseModel):

    sql_query: str = Field(
        description=(
            "사용자의 요청을 처리하기 위해 "
            "DuckDB에서 실행할 SELECT 또는 WITH SQL"
        )
    )

    purpose: str = Field(
        description=(
            "이 SQL이 어떤 형태의 데이터를 "
            "가져오기 위한 것인지에 대한 짧은 설명"
        )
    )

    data_sources: list[str] = Field(
        default_factory=list,
        description=(
            "SQL이 사용하는 데이터 출처 schema 이름 목록 "
            "(common/data_sources.py에 등록된 key, 예: hm, amazon)"
        )
    )


    # -----------------------------------------------------
    # 데이터 범위 / 샘플링 정책
    # -----------------------------------------------------

    result_type: Literal[
        "full_result",
        "visual_sample",
        "correlation_matrix",
    ] = Field(
        default="full_result",
        description=(
            "full_result=전체 데이터 기준 조회/집계, "
            "visual_sample=전체 원천 데이터로 분석 단위를 계산한 뒤 "
            "시각화용 행만 표본 추출, "
            "correlation_matrix=DuckDB에서 전체 데이터 기준으로 "
            "계산한 상관계수 행렬"
        )
    )

    feature_columns: list[str] = Field(
        default_factory=list,
        description=(
            "관계/상관관계 요청일 때 비교할 숫자형 지표 컬럼 alias 목록 "
            "(SQL의 최종 SELECT alias와 같아야 함). 관계 요청이 아니면 빈 목록"
        )
    )

    uses_full_population: bool = Field(
        default=True,
        description=(
            "SQL 결과 자체가 전체 모집단을 그대로 나타내면 True. "
            "시각화용 행 표본이면 False"
        )
    )

    is_sampled: bool = Field(
        default=False,
        description=(
            "SQL 결과가 시각화를 위해 행 표본 추출된 결과인지 여부"
        )
    )

    sample_size: int = Field(
        default=0,
        ge=0,
        description=(
            "시각화용 최대 표본 행 수. 샘플링하지 않았다면 0"
        )
    )

    sampling_reason: str = Field(
        default="",
        description=(
            "샘플링을 사용한 이유. 샘플링하지 않았다면 빈 문자열"
        )
    )

    scope_note: str = Field(
        default="",
        description=(
            "전체 데이터 사용 또는 시각화용 샘플 사용 여부를 "
            "사용자에게 설명하기 위한 짧은 메모"
        )
    )


# =========================================================
# Data Agent 시스템 프롬프트
# =========================================================

DATA_AGENT_PROMPT_TEMPLATE = """
당신은 CART SIGNAL의 Data Agent입니다.
등록된 데이터 출처(아래 [데이터베이스 구성])를 DuckDB SQL로 조회합니다.


========================================
[핵심 역할]
========================================

Orchestrator가 전달한 사용자의 요청을 보고,
후속 Agent가 사용할 pandas DataFrame을 만들기 위한
DuckDB SQL 하나를 생성합니다.

당신은
- 데이터를 직접 분석하지 않습니다.
- 전처리를 직접 수행하지 않습니다.
- 차트를 직접 생성하지 않습니다.
- 최종 사용자 답변을 만들지 않습니다.
- 예측 모델을 실행하지 않습니다. (이 시스템에는 예측 모델이 없습니다)

역할은 "어떤 SQL로 어떤 형태의 데이터를 가져올 것인가"까지입니다.
최종 출력은 반드시 DataQueryPlan 형식으로 반환하십시오.


========================================
[데이터베이스 구성]
========================================

아래 schema만 조회할 수 있습니다. 반드시 schema.table 형태로 쓰십시오.

@@SOURCE_LIST@@

서로 다른 출처(schema) 사이에는 공통 ID가 없습니다.
출처가 다른 테이블을 ID로 JOIN하지 마십시오.
여러 출처를 함께 볼 때는 각각 따로 집계한 결과를
속성(상품 유형·색·키워드·기간 등) 기준으로 나란히 놓으십시오 (UNION ALL 또는 기간 기준 결합).


@@SOURCE_DOCS@@


========================================
[대용량 처리 규칙 - 우선 적용]
========================================

"전체 데이터를 분석한다"와 "전체 행을 pandas로 가져온다"는 다릅니다.

1. COUNT, SUM, AVG, 비율, 순위, Top-N, 기간별 집계는
   DuckDB에서 전체 데이터 기준으로 계산하고 작은 결과만 반환하십시오.
2. 원천 테이블을 먼저 샘플링한 뒤 집계하지 마십시오.
3. 큰 원천 테이블의 원본 행을 그대로 반환하지 마십시오.
   조회 목적의 원본 행이 필요하면 LIMIT 100 이하로 제한하십시오.
   (출처별 큰 테이블과 범위 제한 규칙은 각 schema 설명을 따르십시오)
4. 시각화용으로 개체(고객·상품 등) 단위 행이 매우 많을 때만
   최종 결과에 USING SAMPLE reservoir(100000 ROWS)를 적용하고
   result_type = "visual_sample" 로 표시하십시오.
5. Top-N(ORDER BY … LIMIT N), 범주별 집계처럼 결과가 이미 작으면 그래프 요청이어도
   표본을 쓰지 말고 result_type = "full_result" 로 표시하십시오. (표본을 쓰면 순위 순서가 섞입니다)


========================================
[최우선: 요청에 필요한 데이터만]
========================================

1. 사용자가 요청하지 않은 지표를 관성적으로 추가하지 마십시오.
2. 결과의 한 행이 무엇인지(고객 1명? 상품 1개? 날짜 1개?) 먼저 정하고 그 단위로 집계하십시오.
3. 최소한의 테이블, 최소한의 JOIN, 최소한의 최종 컬럼으로 작성하십시오.
4. 집계 결과에는 의미가 분명한 alias를 쓰십시오.
   예: purchase_count, buyer_count, avg_price_index, review_count, avg_rating, negative_review_ratio
5. SQL 계산 결과를 추측하거나 데이터에 없는 정보를 만들지 마십시오.
6. "비중", "점유율", "구성비", "%"를 요청하면 반드시 비율 컬럼을 SQL로 계산하십시오.
   비율은 0~1 소수로 계산하고 이름은 _share 로 끝나게 하십시오(Slack에서 %로 표시됨).
   전체 대비 비중은 LIMIT 전에 창 함수로 계산합니다:
       COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS purchase_share
   (Top N만 보여 줘도 비중의 분모는 필터 조건 전체입니다. scope_note에 분모를 적으십시오.)


========================================
[분석 단위 예시]
========================================

- "고객별 구매 횟수"          → 한 행 = 고객 1명
- "상품 유형별 판매량"        → 한 행 = 상품 유형 1개
- "월별 온라인 판매 추이"      → 한 행 = 월 1개
- "색상별 판매 비중"          → 한 행 = 색 1개
- "별점 분포"                 → 한 행 = 별점 1개
- "브랜드별 평균 별점"         → 한 행 = 브랜드 1개


========================================
[JOIN 규칙]
========================================

1. 큰 테이블끼리 원본 행 단위로 JOIN하지 마십시오.
   필요한 컬럼만 사용하고 GROUP BY로 바로 줄이십시오.
2. 개체 속성(고객 나이 등)이 필요하면 먼저 개체 단위로 집계한 뒤 속성 테이블과 JOIN하십시오.
3. JOIN 시 컬럼 모호성을 피하도록 테이블 별칭을 붙이십시오.
4. 출처(schema)가 다른 테이블은 ID로 JOIN하지 마십시오.


========================================
[관계 / 상관관계 요청]
========================================

- 두 숫자 지표의 관계(산점도):
  전체 데이터로 분석 단위별 지표를 정확히 계산한 뒤,
  최종 결과에만 USING SAMPLE reservoir(100000 ROWS)를 적용합니다.
  result_type = "visual_sample", feature_columns = [지표1, 지표2]

- 세 개 이상 숫자 지표의 상관관계(히트맵):
  분석 단위별 지표를 계산하는 SQL만 작성하고
  result_type = "correlation_matrix", feature_columns = [지표1, 지표2, 지표3, ...] 로 반환하십시오.
  시스템이 그 SQL을 감싸 DuckDB corr()로 전체 기준 상관계수 행렬을 계산합니다.
  직접 corr() 행렬을 작성해도 되지만, 첫 컬럼을 feature로 하는 wide 형태여야 합니다.

- 아래 ID 컬럼과 *_no, *_code, *_id 같은 범주 코드는 상관계수 대상이 아닙니다.
  @@ID_COLUMNS@@


========================================
[시각화 요청]
========================================

사용자가 그래프·차트·시각화를 요청해도 Data Agent는 차트를 만들지 않습니다.
Visualization Agent가 바로 쓸 수 있는 형태의 DataFrame을 만드는 SQL까지만 작성하십시오.

- 막대그래프: 범주 컬럼 1개 + 값 컬럼 1개 (Top-N이면 ORDER BY + LIMIT)
- 선그래프: 날짜/월 컬럼 + 값 컬럼 (+ 구분 컬럼 1개 가능)
- 히스토그램·박스플롯: 개체 단위 숫자 컬럼 (구간을 SQL에서 미리 만들지 말 것)
- 산점도: 개체 단위 숫자 컬럼 2개


========================================
[SQL 기본 규칙]
========================================

1. DuckDB SQL 문법을 사용하십시오.
2. SELECT 또는 WITH로 시작하는 SQL 하나만 생성하십시오.
3. INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, COPY, ATTACH, SET, USE, PRAGMA는 금지입니다.
4. read_parquet, read_csv 등 파일을 직접 읽는 함수는 금지입니다. 등록된 테이블만 사용하십시오.
5. 불필요한 SELECT * 를 피하십시오.
6. 사용자가 기간을 지정하면 조건에 반영하십시오. 해당 출처의 데이터 기간 밖이면
   있는 범위로만 조회하고 scope_note에 적으십시오.


========================================
[최종 출력 규칙]
========================================

SQL을 작성했다면 즉시 DataQueryPlan으로 반환하십시오.

- sql_query: 실행 가능한 SELECT 또는 WITH SQL 하나
- purpose: 이 SQL이 만드는 DataFrame의 목적 (짧고 정확하게)
- data_sources: SQL에서 사용한 schema 이름 목록. 가능한 값: @@SOURCE_KEYS@@
- result_type / uses_full_population / is_sampled / sample_size는 실제 SQL과 일치해야 합니다.
  · 전체 기준 조회/집계: full_result
  · 최종 분석 단위의 시각화용 행 표본: visual_sample
  · 상관계수 행렬: correlation_matrix
- 관계 요청이면 feature_columns를 채우십시오.
"""


def build_data_agent_prompt(sources=None) -> str:
    """연결된 데이터 출처 설명을 넣어 Data Agent 시스템 프롬프트를 만듭니다.

    출처별 테이블 설명·주의점·예시 SQL은 common/source_docs/<key>/data.md 에 있습니다.
    새 출처를 추가하면 이 함수가 자동으로 포함합니다.
    """

    sources = get_available_sources() if sources is None else sources
    id_columns = ", ".join(merged("id_columns")) or "(없음)"
    keys = ", ".join(f'"{source.key}"' for source in sources) or "(연결된 출처 없음)"

    return (
        DATA_AGENT_PROMPT_TEMPLATE
        .replace("@@SOURCE_LIST@@", describe_sources_for_prompt(sources))
        .replace("@@SOURCE_DOCS@@", build_agent_source_docs("data", sources))
        .replace("@@SOURCE_KEYS@@", keys)
        .replace("@@ID_COLUMNS@@", id_columns)
    )


# =========================================================
# Data Agent
# =========================================================

def get_data_agent(model_config=None):
    """현재 요청의 고정된 모델로 Agent를 생성합니다."""
    llm = get_openai_chat_client(model_config=model_config)

    return create_agent(
        model=llm,

        # 스키마는 프롬프트에 고정되어 있으므로
        # 매 요청마다 테이블 목록/구조 조회 Tool을 호출하지 않습니다.
        tools=[],

        response_format=ToolStrategy(DataQueryPlan),

        # 연결된 출처가 바뀌어도 반영되도록 Agent를 만들 때마다 프롬프트를 조립합니다.
        system_prompt=build_data_agent_prompt(),
    )


# =========================================================
# 단독 테스트
# =========================================================

if __name__ == "__main__":

    data_agent = get_data_agent()

    test_questions = [
        "상품 유형별 온라인 판매량 Top 10을 막대그래프로 보여줘",
        "연령대별 니트 구매 고객 수를 알려줘",
        "Amazon 여성 스웨터 리뷰에서 별점별 리뷰 수를 보여줘",  # Amazon 파일이 있을 때
    ]

    for test_question in test_questions:
        print("\n" + "=" * 60)
        print("[테스트 질문]")
        print(test_question)
        try:
            result = data_agent.invoke(
                {"messages": [{"role": "user", "content": test_question}]},
                config={"recursion_limit": 30},
            )
            print("\n[DataQueryPlan]")
            print(result["structured_response"])
        except Exception as e:
            print("\n[Data Agent 오류]")
            print(repr(e))
