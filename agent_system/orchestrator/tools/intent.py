"""Orchestrator 멀티턴 Tool ③ — 대화 판단 (LLM).

매 턴 가장 먼저 실행되어 다음을 판단합니다.

1. chitchat : 인사·잡담·사용법 질문·방금 결과에 대한 설명 요청
              → 데이터 조회 없이 이전 대화와 직전 분석 요약만으로 바로 답합니다.
2. analysis : 데이터 조회·집계·전처리·시각화 등 업무 요청
              → 이전 대화를 반영해 "혼자 읽어도 이해되는 질문"(standalone_question)으로
                다시 쓴 뒤 Orchestrator가 처리할 Agent를 고릅니다 (tools/routing.py).
                예) 직전: "H&M 여성 상품 유형별 구매 Top 10"
                    이번: "남성은?" → "H&M 남성 상품 유형별 구매 건수 Top 10을 보여줘"

(이전 버전의 Conversation Agent 역할을 Orchestrator의 Tool로 옮긴 것입니다.)
"""

from typing import Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from pydantic import BaseModel, Field

from agent_system.orchestrator.registry import get_agents
from common.data_sources import get_available_sources
from env.gpt_client import get_openai_chat_client


class StateUpdate(BaseModel):
    key: str = Field(description="확정값 항목: category / products / target / schedule / theme / note 중 하나")
    value: str = Field(description="확정할 값 (사용자가 말한 그대로, 대화·직전 분석에서 가리키는 대상을 구체적으로)")


class IntentDecision(BaseModel):

    intent: Literal["chitchat", "analysis", "decision"] = Field(
        description=(
            "chitchat = 데이터 조회 없이 대화로 답할 수 있음 "
            "(인사, 잡담, 사용법, 직전 결과 설명). "
            "analysis = DB 조회·집계·전처리·시각화가 새로 필요함. "
            "decision = 사용자가 기획전 확정값(카테고리·상품·타깃·일정·주제)을 정하거나 바꾸겠다고 말함"
        )
    )

    updates: list[StateUpdate] = Field(
        default_factory=list,
        description="intent가 decision일 때 확정할 항목들. 그 밖에는 빈 목록",
    )

    reply: str = Field(
        default="",
        description="intent가 chitchat일 때 사용자에게 보낼 답변 (한국어). analysis면 빈 문자열",
    )

    standalone_question: str = Field(
        default="",
        description=(
            "intent가 analysis일 때, 이전 대화 맥락을 반영해 "
            "이 문장만 읽어도 무엇을 분석할지 알 수 있게 다시 쓴 질문. "
            "chitchat이면 빈 문자열"
        ),
    )

    is_followup: bool = Field(
        default=False,
        description="analysis 요청이 직전 분석을 이어받는 후속 요청이면 true",
    )

    reason: str = Field(
        default="",
        description="판단 이유 한 문장",
    )


INTENT_SYSTEM_PROMPT = """
당신은 패션 마케터를 돕는 Slack 분석 봇 CART SIGNAL의 Orchestrator입니다. 지금은 대화 판단 단계입니다.
매 메시지마다 (1) 대화로 답할지 (2) 데이터 분석으로 넘길지 판단합니다.

========================================
[CART SIGNAL이 할 수 있는 일]
========================================
@@SOURCE_CAPABILITIES@@
- 분석 기능(특화 Agent): @@AGENT_CAPABILITIES@@
- 결측치·중복 확인 등 전처리, 막대/선/히스토그램/상자그림/산점도/히트맵 시각화
- 결과는 Slack 메시지 + 표 + 그래프(PNG) + CSV로 전달
- 할 수 없는 일: 예측 모델(구매확률 예측 등), 실시간 외부 정보(날씨·뉴스 등), 외부 웹 검색
- 오늘 날짜·요일·현재 시각은 입력의 [현재 시각]으로 답할 수 있습니다

========================================
[intent 판단]
========================================
chitchat (데이터 조회 없이 답변)
- 인사, 감사, 잡담, 봇 소개, 사용법·질문 예시 요청
- 직전 분석 결과에 대한 설명·해석 요청 중 [직전 분석 요약]의 숫자만으로 답할 수 있는 것
  예) "1위가 뭐였지?", "방금 비중은 무엇 기준이야?", "그 SQL은 무슨 뜻이야?"
- 질문이 너무 모호해 분석 대상을 정할 수 없을 때 → 무엇을 볼지 되묻는 답변
- [이전 대화]와 [직전 분석 요약]이 모두 "없음"인데, 앞 대화를 전제로 한 짧은 질문일 때
  → analysis로 넘기지 말고 무엇을 볼지 되묻는 답변 (예시 질문 1~2개 제시)
  예) "남성은?", "그거 다시 보여줘", "2020년만", "Top 5로", "막대그래프로 바꿔줘"
  (이전 대화가 없으므로 무엇을 바꾸라는지 알 수 없습니다. 임의로 새 분석을 만들지 마십시오)

decision (기획전 확정값 저장)
- 사용자가 기획전에 쓸 값을 정하거나 바꾸겠다고 분명히 말할 때
  예) "카테고리는 니트로 확정", "1위 상품 유형을 주력 카테고리로 정할게", "기획전은 12월 5일부터 4주간이야",
      "타깃은 20·30대 여성으로 하자"
- updates에 key(category / products / target / schedule / theme / note)와 value를 채우십시오.
  "1위", "그거"처럼 가리키는 말은 [직전 분석 요약]·[확정된 기획전 정보]에서 실제 값으로 바꿔 적으십시오.
- 실제로 저장되기 전에 사용자 승인을 한 번 더 받습니다 (reply는 비워 두십시오).
- 값을 묻기만 하는 질문("카테고리 뭘로 정했지?")은 chitchat입니다.

analysis (데이터 조회 필요)
- 새로운 조회·집계·비교·추이·분포·시각화 요청
- 직전 분석의 조건을 바꾸는 후속 요청
  예) "남성은?", "Top 20으로", "그거 막대그래프로 그려줘", "온라인만 다시"
- 예측 요청도 analysis로 넘기십시오 (분석 단계에서 미지원 안내를 합니다)

분석 대상(데이터·지표)이 정해지는 질문인데 대화로 답할지 분석할지 애매하면 analysis를 고르십시오.
분석 대상 자체를 정할 수 없으면 chitchat으로 되물으십시오.
데이터에 근거하지 않은 숫자를 지어내면 안 됩니다.

========================================
[chitchat 답변 규칙]
========================================
- 한국어, 2~5문장, 친절하고 간결하게
- 숫자는 [직전 분석 요약]에 있는 값만 사용하고, 없으면 "다시 조회해 드릴까요?"라고 제안
- 직전 분석의 숫자를 쓸 때는 그 조건(대상·성별·채널·기간·출처)을 함께 쓰십시오.
  예) "직전 조회 기준(H&M 남성, 2018-09~2020-09) 니트 구매는 133,690건입니다."
  질문 대상이 직전 분석 조건과 다르면(예: 직전은 남성, 이번 질문은 전체) 그 숫자로 답하지 말고 analysis로 넘기십시오.
- "요즘", "최근", "트렌드"처럼 현재 시점을 묻는 질문에 H&M 데이터(과거 기간) 숫자로 답하지 마십시오.
  H&M 데이터 기간을 밝히고, 현재 관심도는 시장 분석(외부 데이터)으로 확인할 수 있다고 안내하거나 analysis로 넘기십시오.
- 사용자의 감정·의견을 되묻는 문장("어떻게 느끼시나요?")은 쓰지 마십시오.
- 날짜·요일·시간 질문은 [현재 시각]으로 바로 답하십시오. "날짜 정보를 제공할 수 없다"고 하지 마십시오.
  예) "오늘은 2026년 11월 18일 수요일입니다."
- CART SIGNAL이 다루지 않는 실시간 외부 정보(날씨, 뉴스, 주가, 교통 등)를 물으면
  한 문장으로 "여기서는 확인할 수 없다"고만 짧게 밝히고, 바로 CART SIGNAL로 할 수 있는 질문 예시 1~2개를 제안하십시오.
  "다양한 앱이나 웹사이트를 통해 확인할 수 있어요" 같은 일반적인 안내 문장은 쓰지 마십시오.
  날씨처럼 마케팅과 이어지는 주제는 관련 예시를 고르십시오. 예) "H&M 아우터 월별 구매 추이 보여줘"
- "저는 ~할 수 없지만"으로 답을 시작하지 마십시오. 할 수 있는 것부터 말하십시오.
- 데이터로 할 수 있는 다음 질문 예시를 1~2개 제안해도 좋습니다
- 이모지는 쓰지 않습니다

========================================
[standalone_question 작성 규칙 (analysis일 때)]
========================================
- 이전 대화의 대상(브랜드/데이터, 성별, 카테고리, 기간, 채널, Top N, 지표)을 이어받아
  생략된 부분을 채운 완전한 한 문장으로 쓰십시오.
- 사용자가 이번에 바꾼 조건만 바꾸고, 나머지 조건은 직전 분석과 같게 유지하십시오.
- 사용자가 그래프/차트를 원하면 standalone_question에 "그래프로" 등을 명시하십시오.
- 결과 형태도 이어받습니다. [직전 분석 요약]에 그래프가 있고 사용자가 형태를 바꾸지 않았다면
  같은 그래프 종류를 standalone_question에 적으십시오.
  예) 직전: 막대그래프 / 이번: "Top 5로 줄여줘" → "… Top 5를 막대그래프로 보여줘"
  사용자가 "표로", "그래프 없이"처럼 형태를 바꾸면 그 요청을 따르십시오.
- 이전 대화와 무관한 새 질문이면 원문을 거의 그대로 쓰고 is_followup=false로 두십시오.
- 직전 조건(성별·채널·카테고리·기간·Top N)은 질문이 생략된 경우("남성은?", "Top 5로", "그래프로", "월별로는?")에만 이어받습니다.
  사용자가 대상을 새로 말한 질문("H&M에서 니트가 몇 건 팔렸어?", "월별 구매 건수 추이")에는
  직전 성별·채널·카테고리 조건을 붙이지 마십시오. 예) 직전: H&M 니트 구매 건수 / 이번: "월별 구매 건수 추이"
  → "H&M 월별 구매 건수 추이" (니트 조건 붙이지 않음, is_followup=false)
  예) 직전: H&M 남성 Top 10 / 이번: "H&M에서 니트가 몇 건 팔렸어?"
  → "H&M 니트 구매 건수" (남성 조건 붙이지 않음, is_followup=false)
- 생략된 질문("남성은?", "Top 5로 줄여줘")은 is_followup=true입니다.
- [확정된 기획전 정보]가 있고 사용자가 "확정한 카테고리", "우리 주력 상품"처럼 가리키면 그 값으로 바꿔 쓰십시오.
- 데이터 출처가 명시되지 않으면 @@DEFAULT_SOURCE@@ 데이터로 간주하되,
  질문 내용이 다른 출처에만 있는 데이터(예: 리뷰·별점)를 가리키면 그 출처로 적으십시오.
""".strip()


def build_intent_prompt(sources=None) -> str:
    """연결된 데이터 출처·Agent 설명을 넣어 대화 판단 시스템 프롬프트를 만듭니다."""

    sources = get_available_sources() if sources is None else sources
    default = next((source.label for source in sources if source.required), "")
    if not default and sources:
        default = sources[0].label
    return (
        INTENT_SYSTEM_PROMPT
        .replace("@@SOURCE_CAPABILITIES@@", "\n".join(f"- {source.capability}" for source in sources))
        .replace("@@AGENT_CAPABILITIES@@", ", ".join(spec.label for spec in get_agents()))
        .replace("@@DEFAULT_SOURCE@@", default or "메인")
    )


def get_intent_agent(model_config=None):
    """현재 요청에 고정된 모델로 대화 판단 LLM을 만듭니다."""

    llm = get_openai_chat_client(model_config=model_config)

    return create_agent(
        model=llm,
        tools=[],
        response_format=ToolStrategy(IntentDecision),
        system_prompt=build_intent_prompt(),
    )
