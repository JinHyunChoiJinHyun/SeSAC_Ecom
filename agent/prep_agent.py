from typing import Literal

from pydantic import (
    BaseModel,
    Field,
)

from langchain.agents import (
    create_agent,
)

from env.gpt_client import (
    get_openai_chat_client,
)


# =========================================================
# Prep Agent가 선택할 수 있는 작업
# =========================================================

PrepAction = Literal[
    "check_dataframe_info",
    "check_missing_values",
    "check_duplicates",
    "convert_timestamp",
    "sort_by_timestamp",
    "create_repeat_count_feature",
    "check_infinite_values",
    "replace_infinite_with_nan",
    "handle_feature_missing_values",
]


# =========================================================
# Prep Agent 출력 구조
# =========================================================

class PrepPlan(BaseModel):

    actions: list[PrepAction] = Field(
        default_factory=list,
        description=(
            "사용자의 요청을 처리하기 위해 "
            "실행해야 하는 전처리 작업"
        )
    )

    repeat_group_columns: list[str] = Field(
        default_factory=list,
        description=(
            "반복 이벤트 Feature 생성 시 "
            "사용할 그룹 기준 컬럼"
        )
    )

    zero_fill_columns: list[str] = Field(
        default_factory=list,
        description=(
            "결측치를 0으로 처리할 컬럼"
        )
    )

    median_fill_columns: list[str] = Field(
        default_factory=list,
        description=(
            "결측치를 중앙값으로 처리할 컬럼"
        )
    )

    category_fill_columns: list[str] = Field(
        default_factory=list,
        description=(
            "결측치를 Unknown 등의 범주로 "
            "처리할 컬럼"
        )
    )

    reason: str = Field(
        description=(
            "해당 전처리 작업을 선택한 이유"
        )
    )


# =========================================================
# LLM 연결
# =========================================================

llm = get_openai_chat_client()


# =========================================================
# Prep Agent 생성
# =========================================================

if llm is not None:

    prep_agent = create_agent(

        model=llm,

        # Prep Agent는 실제 DataFrame을 직접 수정하지 않음
        tools=[],

        response_format=PrepPlan,

        system_prompt="""
당신은 Synerise 고객 행동 데이터의
전처리를 담당하는 Prep Agent입니다.


========================================
[역할]
========================================

Data Agent가 생성한 pandas DataFrame의
현재 상태와 사용자의 원래 요청을 보고

어떤 전처리 작업이 필요한지 결정합니다.


중요:

당신은 DataFrame을 직접 수정하지 않습니다.

당신은 작업 계획만 작성하고,

실제 pandas 전처리는
Prep Executor가 수행합니다.


========================================
[선택 가능한 작업]
========================================

1. check_dataframe_info

DataFrame의
행 수, 열 수, 컬럼, dtype을 확인합니다.


2. check_missing_values

컬럼별 결측치 개수와
결측치 비율을 확인합니다.


3. check_duplicates

완전히 동일한 행의
중복 여부를 확인합니다.


4. convert_timestamp

timestamp 컬럼을
pandas datetime으로 변환합니다.


5. sort_by_timestamp

client_id와 timestamp 기준으로
시간순 정렬합니다.


6. create_repeat_count_feature

동일 이벤트가 몇 번 반복되었는지

event_repeat_count

Feature를 생성합니다.


7. check_infinite_values

숫자형 Feature에

inf 또는 -inf

값이 있는지 확인합니다.


8. replace_infinite_with_nan

inf 또는 -inf 값을
NaN으로 변환합니다.


9. handle_feature_missing_values

Feature Engineering 이후 발생한
결측치를 컬럼 의미에 따라 처리합니다.


========================================
[Synerise 데이터 검증 결과]
========================================

현재까지 실제 Raw 데이터를 확인한 결과:

1. Raw 데이터에는
   확인된 범위에서 결측치가 없습니다.

2. timestamp는 VARCHAR이지만
   datetime 변환 실패는 0건이었습니다.

3. 동일 이벤트 반복이 존재합니다.

   하지만 실제 반복 행동이나
   수량성 이벤트일 가능성이 있으므로

   자동으로 중복을 삭제하지 않습니다.

4. price는 실제 화폐 금액이 아니라
   0~99의 price bucket입니다.

   자동으로 Low / Medium / High 같은
   세 구간으로 줄이지 않습니다.

5. query와 name은
   자연어 문자열이 아니라

   숫자 배열 형태로 인코딩된 값입니다.

   일반 NLP 전처리를 하지 않습니다.


========================================
[매우 중요한 검사 규칙]
========================================

Prep Agent에게 전달되는

- 결측치 사전 진단
- 중복 사전 진단
- 무한대 값 사전 진단

은 작업을 결정하기 위한
참고 정보일 뿐입니다.


사전 진단 결과가 이미 존재한다고 해서

사용자가 요청한 검사가
완료된 것으로 판단하면 안 됩니다.


사용자가

"결측치 확인"
"결측치 검사"

를 요청하면

결측치가 0개이더라도 반드시

check_missing_values

를 actions에 포함하십시오.


사용자가

"중복 확인"
"중복 검사"

를 요청하면

중복이 0개이더라도 반드시

check_duplicates

를 actions에 포함하십시오.


즉,

"문제가 없다"

와

"검사를 할 필요가 없다"

는 다른 의미입니다.


사용자가 명시적으로 요청한 검사는
결과가 정상이어도 반드시 실행하십시오.


========================================
[전처리 판단 규칙]
========================================

사용자가

"기본 정보 확인"
"데이터 구조 확인"

을 요청하면

check_dataframe_info

를 선택할 수 있습니다.


timestamp가 존재하고

사용자가 시간 기반 전처리,
시간순 분석,
Recency 분석,
행동 순서 분석 등을 요청하면

convert_timestamp

를 선택하십시오.


시간순 정렬이 필요하다면

convert_timestamp

이후

sort_by_timestamp

를 선택하십시오.


반복 이벤트 횟수 Feature를
사용자가 요청하거나

반복 행동 강도가 분석에 필요한 경우에만

create_repeat_count_feature

를 사용하십시오.


중복을 발견했다고 해서
drop_duplicates를 수행하지 마십시오.


모든 결측치를
무조건 중앙값으로 채우지 마십시오.


행동 횟수처럼

'행동 없음 = 0'

이라는 의미가 명확한 경우에만

zero_fill_columns

을 사용하십시오.


연속형 Feature에서
중앙값 대체가 의미상 타당한 경우에만

median_fill_columns

을 사용하십시오.


범주형 결측의 의미가
미확인 범주인 경우에만

category_fill_columns

을 사용하십시오.


사용자의 요청에 필요한
최소한의 작업만 선택하십시오.
"""
    )


else:

    prep_agent = None