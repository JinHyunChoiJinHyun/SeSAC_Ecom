import pandas as pd

from langchain_core.messages import (
    HumanMessage,
)

from make_graph import (
    graph,
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
    #
    # 로그 파일은 날짜별로 분리되고
    # 30일간 보관합니다.
    # =====================================================

    configure_logging(
        capture_print=True,
        retention_days=30,
    )


    print(
        "Synerise Multi Agent 시작"
    )


    print(
        "종료하려면 exit 입력\n"
    )


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


        # =================================================
        # 요청 단위 Logging
        # =================================================
        #
        # 사용자 질문 하나마다
        # 새로운 run_id가 생성됩니다.
        #
        # 예:
        #
        # run_id=83ab129f
        #
        # 이 블록 안에서 발생하는
        # Supervisor / Data / Prep / Visualization 등의
        # 모든 print 로그는 같은 run_id로 저장됩니다.
        # =================================================

        try:

            with request_log(
                user_input
            ) as run_id:

                # =========================================
                # Graph 실행
                # =========================================

                result = graph.invoke(
                    {
                        "messages": [
                            HumanMessage(
                                content=
                                    user_input
                            )
                        ],

                        "completed_steps":
                            [],
                    },

                    # -------------------------------------
                    # 비정상 Routing 반복 방지용 안전장치
                    # -------------------------------------

                    config={
                        "recursion_limit": 30
                    }
                )


                # =========================================
                # 최종 사용자 응답
                # =========================================

                messages = result.get(
                    "messages",
                    []
                )


                if messages:

                    last_message = (
                        messages[-1]
                    )


                    print(
                        "\nAgent >"
                    )


                    print(
                        last_message.content
                    )


                # =========================================
                # 생성 SQL
                # =========================================

                sql_query = result.get(
                    "sql_query"
                )


                if sql_query:

                    print(
                        "\n[생성 SQL]"
                    )


                    print(
                        sql_query
                    )


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
                # 현재 Agent
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
        # Graph 또는 전체 요청 실행 오류
        # =================================================
        #
        # request_log 내부에서 예외가 발생하면
        # logger.py가 traceback까지 로그 파일에 기록한 뒤
        # 여기로 전달합니다.
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