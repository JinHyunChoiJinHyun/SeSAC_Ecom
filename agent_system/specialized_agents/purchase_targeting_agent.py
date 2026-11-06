"""
Purchase Targeting Agent

핵심 판단
---------
- 누구에게 프로모션할 것인가?

현재 24/48/72시간 구매확률 Ranking 요청은
mart.purchase_predictions에 저장된 결과를 먼저 사용합니다.

팀 합의
-------
- 별도의 mart.model_predictions 테이블은 만들지 않습니다.
- mart.purchase_predictions가 공통 Prediction Store 역할을 담당합니다.
- 현재 팀원이 purchase_predictions의 컬럼을 수정 중입니다.
- 따라서 지금은 persist_result=False를 유지하고 읽기만 합니다.
- 최종 컬럼 확정 후 prediction_store.py의 저장 규칙을 수정하고
  persist_result=True를 연결합니다.

현재 흐름:
1. mart.purchase_predictions 조회
2. 저장 결과가 있으면 바로 반환
3. 저장 결과가 없으면 기존 Shared Analysis Graph가 fallback 처리
"""

from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd

from agent_system.tools.model_profile_tools import (
    run_prediction_profile,
)
from agent_system.tools.prediction_store import (
    get_ranked_predictions,
    save_prediction_dataframe,
)
from modeling.model_registry import (
    ModelProfile,
    get_model_profile,
    resolve_model_profile,
)


@dataclass
class PurchaseTargetingRequest:
    model_key: str | None = None
    prediction_hour: int | None = None
    target: str = "same_product_purchase"
    top_n: int = 10
    sort_order: str = "desc"
    use_cached_result: bool = True
    persist_result: bool = False


class PurchaseTargetingAgent:
    """구매 타겟팅 판단을 담당합니다."""

    def select_profile(
        self,
        request: PurchaseTargetingRequest,
    ) -> ModelProfile:

        if request.model_key:
            return get_model_profile(
                request.model_key
            )

        return resolve_model_profile(
            task=
                "purchase_prediction",

            prediction_hour=
                request.prediction_hour,

            target=
                request.target,
        )

    def _rank_customers(
        self,
        prediction_df: pd.DataFrame,
        *,
        top_n: int,
        ascending: bool,
    ) -> pd.DataFrame:

        required = {
            "client_id",
            "purchase_probability",
        }

        if not required.issubset(
            prediction_df.columns
        ):
            raise ValueError(
                "고객 타겟팅 결과에 필요한 컬럼이 없습니다: "
                f"{sorted(required)}"
            )

        ranked = (
            prediction_df
            .copy()
        )

        ranked[
            "purchase_probability"
        ] = pd.to_numeric(
            ranked[
                "purchase_probability"
            ],
            errors=
                "coerce",
        )

        return (
            ranked
            .sort_values(
                "purchase_probability",
                ascending=
                    ascending,
                na_position=
                    "last",
            )
            .drop_duplicates(
                subset=[
                    "client_id"
                ],
                keep=
                    "first",
            )
            .head(
                max(
                    1,
                    int(
                        top_n
                    ),
                )
            )
            .reset_index(
                drop=True
            )
        )

    def run(
        self,
        *,
        df: pd.DataFrame | None,
        request: PurchaseTargetingRequest,
    ) -> dict:

        profile = (
            self.select_profile(
                request
            )
        )

        ascending = (
            str(
                request.sort_order
            )
            .lower()
            == "asc"
        )

        # =================================================
        # 1. 저장된 예측 결과 우선 조회
        # =================================================
        if request.use_cached_result:
            try:
                cached = (
                    get_ranked_predictions(
                        model_key=
                            profile.key,

                        top_n=
                            request.top_n,

                        ascending=
                            ascending,

                        observation_window_hours=
                            profile.observation_window_hours,
                    )
                )

                if not cached.empty:
                    source_table = (
                        cached.attrs.get(
                            "prediction_source",
                            "prediction_store",
                        )
                    )

                    print(
                        "[Purchase Targeting Agent]"
                    )

                    print(
                        "[Prediction Cache HIT]"
                    )

                    print(
                        f"source_table = {source_table}"
                    )

                    print(
                        f"model_key = {profile.key}"
                    )

                    print(
                        "prediction_hour = "
                        f"{profile.observation_window_hours}"
                    )

                    print(
                        f"result_count = {len(cached)}"
                    )

                    return {
                        "success":
                            True,

                        "source":
                            "prediction_cache",

                        "source_table":
                            source_table,

                        "model_key":
                            profile.key,

                        "model_profile":
                            profile.to_dict(),

                        "result_dataframe":
                            cached,

                        "result_count":
                            len(
                                cached
                            ),

                        "error":
                            "",
                    }

            except Exception as exc:
                print(
                    "[Prediction Cache 조회 실패]"
                )

                print(
                    repr(
                        exc
                    )
                )

        # =================================================
        # 2. Cache miss → 실제 모델 추론
        # =================================================
        if not isinstance(
            df,
            pd.DataFrame,
        ):
            print(
                "[Prediction Cache MISS]"
            )

            return {
                "success":
                    False,

                "model_key":
                    profile.key,

                "error":
                    (
                        "저장된 예측 결과가 없으며 "
                        "새 예측에 사용할 DataFrame도 없습니다."
                    ),
            }

        prediction_result = (
            run_prediction_profile(
                df=
                    df,

                model_key=
                    profile.key,

                task=
                    profile.task,

                target=
                    profile.target,

                prediction_hour=
                    profile.observation_window_hours,
            )
        )

        if not prediction_result.get(
            "success"
        ):
            return prediction_result

        prediction_df = (
            prediction_result.get(
                "prediction_dataframe"
            )
        )

        if not isinstance(
            prediction_df,
            pd.DataFrame,
        ):
            return {
                "success":
                    False,

                "model_key":
                    profile.key,

                "error":
                    "Model 결과 DataFrame이 없습니다.",
            }

        ranked = (
            self._rank_customers(
                prediction_df,
                top_n=
                    request.top_n,
                ascending=
                    ascending,
            )
        )

        # =================================================
        # 3. 예측 결과 저장
        # =================================================
        #
        # [현재는 persist_result=False 유지]
        #
        # 팀 합의:
        # - mart.model_predictions는 만들지 않음
        # - mart.purchase_predictions가 공통 Prediction Store 역할
        #
        # 현재 팀원이 purchase_predictions 컬럼을 수정 중이므로
        # 이 단계에서는 새 예측 결과를 저장하지 않습니다.
        #
        # 최종 컬럼 확정 후:
        # 1) prediction_store.py 저장 Mapping 수정
        # 2) 중복 방지 Key 확정
        # 3) persist_result=True 연결
        #
        # 현재 실수로 True가 되더라도 prediction_store.py에서
        # 쓰기 작업을 차단하도록 안전장치를 두었습니다.
        # =================================================
        if request.persist_result:
            try:
                save_prediction_dataframe(
                    prediction_df,
                    model_key=
                        profile.key,
                    task=
                        profile.task,
                    observation_window_hours=
                        profile.observation_window_hours,
                    model_version=(
                        prediction_result.get(
                            "model_version",
                            "",
                        )
                        or profile.model_version
                    ),
                )

            except Exception:
                prediction_result[
                    "store_warning"
                ] = (
                    "예측은 완료되었지만 "
                    "Prediction Store 저장에 실패했습니다."
                )

        return {
            **prediction_result,
            "source":
                "model_inference",
            "result_dataframe":
                ranked,
            "result_count":
                len(
                    ranked
                ),
        }


# =========================================================
# Slack / CLI 자연어 요청 판별
# =========================================================

def parse_purchase_targeting_question(
    question: str,
) -> PurchaseTargetingRequest | None:
    """
    저장된 24/48/72 구매예측 결과로 바로 처리할 수 있는
    고객 Ranking 질문을 판별합니다.

    예:
    - 장바구니 추가 후 24시간 기준으로 구매확률이 높은 고객 10명을 찾아줘.
    - 48시간 기준 구매 가능성이 낮은 고객 20명 알려줘.

    해당하지 않는 질문은 None을 반환하여
    기존 Shared Analysis Graph가 처리하도록 합니다.
    """

    query = (
        str(
            question
            or ""
        )
        .strip()
        .lower()
    )

    if not query:
        return None

    customer_keywords = [
        "고객",
        "타겟",
        "대상자",
    ]

    probability_keywords = [
        "구매확률",
        "구매 확률",
        "구매가능성",
        "구매 가능성",
    ]

    ranking_keywords = [
        "높은",
        "높게",
        "상위",
        "top",
        "낮은",
        "낮게",
        "하위",
        "bottom",
    ]

    if not any(
        keyword in query
        for keyword in customer_keywords
    ):
        return None

    if not any(
        keyword in query
        for keyword in probability_keywords
    ):
        return None

    if not any(
        keyword in query
        for keyword in ranking_keywords
    ):
        return None

    # -----------------------------------------------------
    # 현재 실제 저장 모델이 있는 24/48/72시간만
    # 빠른 Cache 경로에서 처리합니다.
    # -----------------------------------------------------
    hour_match = re.search(
        r"\b(24|48|72)\s*(?:시간|h)\b",
        query,
    )

    if not hour_match:
        return None

    prediction_hour = int(
        hour_match.group(
            1
        )
    )

    # -----------------------------------------------------
    # 요청 인원 수
    # -----------------------------------------------------
    top_n = 10

    top_n_patterns = [
        r"고객\s*(\d+)\s*명",
        r"상위\s*(\d+)\s*명",
        r"하위\s*(\d+)\s*명",
        r"top\s*(\d+)",
        r"bottom\s*(\d+)",
        r"(\d+)\s*명의?\s*고객",
    ]

    for pattern in top_n_patterns:
        match = re.search(
            pattern,
            query,
        )

        if match:
            top_n = int(
                match.group(
                    1
                )
            )
            break

    top_n = max(
        1,
        min(
            top_n,
            100,
        ),
    )

    # -----------------------------------------------------
    # 높은 확률 / 낮은 확률
    # -----------------------------------------------------
    low_keywords = [
        "낮은",
        "낮게",
        "하위",
        "bottom",
    ]

    sort_order = (
        "asc"
        if any(
            keyword in query
            for keyword in low_keywords
        )
        else "desc"
    )

    return PurchaseTargetingRequest(
        prediction_hour=
            prediction_hour,

        top_n=
            top_n,

        sort_order=
            sort_order,

        use_cached_result=
            True,

        # -------------------------------------------------
        # [중요]
        # 현재는 mart.purchase_predictions 읽기 전용 단계입니다.
        #
        # 팀원이 테이블 컬럼을 수정 중이므로 False를 유지합니다.
        # 최종 Schema 확정 후 prediction_store.py 저장 규칙을
        # 수정한 다음에만 True 연결을 검토합니다.
        # -------------------------------------------------
        persist_result=
            False,
    )


def _build_cached_targeting_answer(
    *,
    request: PurchaseTargetingRequest,
    agent_result: dict,
) -> str:
    """Slack에 전달할 간단한 구매 타겟팅 답변을 만듭니다."""

    result_df = (
        agent_result.get(
            "result_dataframe"
        )
    )

    if not isinstance(
        result_df,
        pd.DataFrame,
    ) or result_df.empty:
        raise ValueError(
            "구매 타겟팅 결과 DataFrame이 없습니다."
        )

    direction = (
        "낮은"
        if request.sort_order
        == "asc"
        else "높은"
    )

    display_columns = [
        column
        for column in [
            "client_id",
            "sku",
            "purchase_probability",
            "promotion_segment",
            "model_name",
        ]
        if column in result_df.columns
    ]

    preview_df = (
        result_df[
            display_columns
        ]
        .copy()
        if display_columns
        else result_df.copy()
    )

    if (
        "purchase_probability"
        in preview_df.columns
    ):
        preview_df[
            "purchase_probability"
        ] = (
            pd.to_numeric(
                preview_df[
                    "purchase_probability"
                ],
                errors=
                    "coerce",
            )
            .round(
                6
            )
        )

    preview_text = (
        preview_df
        .to_string(
            index=False
        )
    )

    source_table = (
        agent_result.get(
            "source_table",
            "prediction_store",
        )
    )

    return (
        f"장바구니 추가 후 {request.prediction_hour}시간 기준으로 "
        f"구매확률이 {direction} 고객 "
        f"{len(result_df)}명을 조회했습니다.\n\n"
        f"{preview_text}\n\n"
        f"- 조회 소스: {source_table}\n"
        "- 저장된 예측 결과를 조회했으며, "
        "이번 Slack 요청에서 모델 재추론은 실행하지 않았습니다."
    )


def try_run_cached_purchase_targeting(
    question: str,
) -> dict | None:
    """
    Slack/CLI 진입점에서 호출하는 빠른 경로입니다.

    처리 가능한 구매확률 Ranking 요청이면
    저장된 예측 테이블을 조회해 Graph State와 호환되는 dict를 반환합니다.

    처리 대상이 아니거나 Cache가 없으면 None을 반환하여
    기존 Shared Analysis Graph로 fallback합니다.
    """

    request = (
        parse_purchase_targeting_question(
            question
        )
    )

    if request is None:
        return None

    print(
        "[Purchase Targeting Router]"
    )

    print(
        "저장된 구매예측 결과를 우선 조회합니다."
    )

    try:
        agent = (
            get_purchase_targeting_agent()
        )

        result = (
            agent.run(
                df=
                    None,

                request=
                    request,
            )
        )

    except Exception as exc:
        print(
            "[Purchase Targeting Router 실패]"
        )

        print(
            repr(
                exc
            )
        )

        return None

    if not result.get(
        "success"
    ):
        print(
            "[Purchase Targeting Router]"
        )

        print(
            "Cache 결과가 없어 Shared Analysis Graph로 fallback합니다."
        )

        return None

    result_df = (
        result.get(
            "result_dataframe"
        )
    )

    if not isinstance(
        result_df,
        pd.DataFrame,
    ) or result_df.empty:
        return None

    final_answer = (
        _build_cached_targeting_answer(
            request=
                request,

            agent_result=
                result,
        )
    )

    # -----------------------------------------------------
    # 기존 Delivery Agent가 그대로 사용할 수 있는
    # Shared Analysis 결과 형식으로 맞춥니다.
    # -----------------------------------------------------
    return {
        "final_answer":
            final_answer,

        "model_result_dataframe":
            result_df,

        "model_result": {
            "success":
                True,

            "task":
                "purchase_prediction",

            "source":
                result.get(
                    "source"
                ),

            "source_table":
                result.get(
                    "source_table"
                ),

            "model_key":
                result.get(
                    "model_key"
                ),
        },

        "completed_steps": [
            "purchase_targeting"
        ],

        "error":
            "",
    }


def get_purchase_targeting_agent() -> PurchaseTargetingAgent:
    return PurchaseTargetingAgent()
