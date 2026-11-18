from env.model_settings import model_request
import pandas as pd

import uuid

from agent_system.orchestrator.graph import (
    run_turn as run_conversation_turn,
)

from env.logger import (
    configure_logging,
    request_log,
)


# =========================================================
# Main
# =========================================================

def main():

    # =====================================================
    # Logging 설정
    # =====================================================
    #
    # 기존 print() 내용을
    #
    # 1. 터미널에 출력
    # 2. logs/app.log에 저장
    #
    # 두 곳에 동시에 기록합니다.
    # =====================================================

    configure_logging(
        capture_print=True,
        retention_days=30,
    )


    from common.data_sources import get_available_sources

    print(
        "CART SIGNAL General Analysis 시작 ("
        + " + ".join(source.label for source in get_available_sources())
        + ")"
    )


    print(
        "종료하려면 exit, 새 대화를 시작하려면 '새 대화' 입력\n"
    )

    # 터미널 대화 ID (같은 실행 안에서 이전 질문을 기억합니다)
    thread_id = f"cli:{uuid.uuid4().hex[:8]}"


    while True:

        # =================================================
        # 사용자 입력
        # =================================================

        user_input = input(
            "질문 > "
        ).strip()


        if user_input.lower() in [
            "exit",
            "quit",
            "종료",
        ]:

            print(
                "프로그램을 종료합니다."
            )

            break


        if user_input in ["새 대화", "reset", "/reset"]:

            thread_id = f"cli:{uuid.uuid4().hex[:8]}"
            print("새 대화를 시작합니다.\n")
            continue


        if not user_input:
            continue


        # =================================================
        # 요청 단위 Logging
        # =================================================

        try:

            with request_log(
                user_input
            ) as run_id, model_request() as llm_config:

                # =========================================
                # Graph 실행
                # =========================================

                result = run_conversation_turn(
                    question=user_input,
                    thread_id=thread_id,
                    llm_config=llm_config.public(),
                    metadata={"source": "cli"},
                )


                # =========================================
                # 최종 사용자 응답
                # =========================================

                print(
                    "\nAgent >"
                )

                print(
                    result.get("final_answer", "")
                )

                if result.get("turn_type") == "chitchat":
                    continue

                # 다시 쓴 질문 표시는 agent_system/orchestrator/settings.py의
                # SHOW_REWRITTEN_QUESTION 설정에 따라 final_answer 맨 위에 들어갑니다.


                # =========================================
                # Data Agent 원본 DataFrame
                # =========================================

                df = result.get(
                    "dataframe"
                )


                if isinstance(
                    df,
                    pd.DataFrame
                ):

                    print(
                        "\n[DataFrame 상태]"
                    )


                    print(
                        "type :",
                        type(
                            df
                        )
                    )


                    print(
                        "shape :",
                        df.shape
                    )


                    print(
                        "columns :",
                        list(
                            df.columns
                        )
                    )


                # =========================================
                # Prep Agent 처리 DataFrame
                # =========================================

                processed_df = result.get(
                    "processed_dataframe"
                )


                if isinstance(
                    processed_df,
                    pd.DataFrame
                ):

                    print(
                        "\n[Processed DataFrame 상태]"
                    )


                    print(
                        "type :",
                        type(
                            processed_df
                        )
                    )


                    print(
                        "shape :",
                        processed_df.shape
                    )


                    print(
                        "columns :",
                        list(
                            processed_df.columns
                        )
                    )


                # =========================================
                # Prep 작업 목록
                # =========================================

                prep_actions = result.get(
                    "prep_actions"
                )


                if prep_actions:

                    print(
                        "\n[Prep 작업]"
                    )


                    print(
                        prep_actions
                    )


                # =========================================
                # Data Quality Report
                # =========================================

                data_quality_report = (
                    result.get(
                        "data_quality_report"
                    )
                )


                if data_quality_report:

                    print(
                        "\n[Data Quality Report]"
                    )


                    print(
                        "plan_reason :",
                        data_quality_report.get(
                            "plan_reason"
                        )
                    )


                # =========================================
                # Visualization 결과
                # =========================================

                visualization_result = (
                    result.get(
                        "visualization_result"
                    )
                )


                if visualization_result:

                    print(
                        "\n[Visualization 결과]"
                    )


                    print(
                        "chart_type :",
                        visualization_result.get(
                            "chart_type"
                        )
                    )


                    print(
                        "title :",
                        visualization_result.get(
                            "title"
                        )
                    )


                    print(
                        "file_path :",
                        visualization_result.get(
                            "file_path"
                        )
                    )


                # =========================================
                # 완료 작업
                # =========================================

                print(
                    "\n[완료된 작업]"
                )


                print(
                    result.get(
                        "completed_steps",
                        []
                    )
                )


                # =========================================
                # 마지막 Agent
                # =========================================

                print(
                    "\n[마지막 Agent]"
                )


                print(
                    result.get(
                        "current_agent"
                    )
                )


                # =========================================
                # 요청 ID
                # =========================================

                print(
                    "\n[Run ID]"
                )


                print(
                    run_id
                )


                print(
                    "\n"
                    + "-" * 60
                    + "\n"
                )


        # =================================================
        # Graph 실행 오류
        # =================================================

        except Exception as e:

            print(
                "\n[Graph 실행 오류]"
            )


            print(
                repr(e)
            )


            print(
                "\n"
                + "-" * 60
                + "\n"
            )


            continue


# =========================================================
# 실행
# =========================================================

if __name__ == "__main__":

    main()