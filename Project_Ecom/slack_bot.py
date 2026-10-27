"""
Team_Project_Ecom Slack DM·채널 멘션 연결 파일입니다.

역할
----
1. Slack DM 또는 채널 멘션으로 질문을 받습니다.
2. 기존 LangGraph Multi Agent를 실행합니다.
3. Delivery Agent를 호출합니다.
4. final_answer를 Slack으로 전송합니다.
5. 결과 DataFrame을 CSV로 생성해 첨부합니다.
6. Visualization PNG가 있으면 같이 첨부합니다.
7. 향후 Model / Experiment 결과도 첨부할 수 있는 구조를 사용합니다.


설치
----
python -m pip install slack-bolt python-dotenv


환경변수
--------
SLACK_BOT_TOKEN
SLACK_APP_TOKEN


실행
----
python slack_bot.py
"""


import logging
import os

from collections import (
    deque,
)

from concurrent.futures import (
    ThreadPoolExecutor,
)

from pathlib import (
    Path,
)

from threading import (
    Lock,
)


# =========================================================
# 기본 설정
# =========================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
)


LOGGER = logging.getLogger(
    "ecom.slack"
)


# =========================================================
# Slack DM 질문 추출
# =========================================================

def get_dm_question(
    event,
):
    """
    사용자가 보낸 일반 1:1 DM에서
    질문만 꺼냅니다.

    Bot 메시지나 시스템 메시지는 무시합니다.
    """

    if (
        event.get(
            "channel_type"
        )
        != "im"
        or event.get(
            "bot_id"
        )
        or event.get(
            "subtype"
        )
        or not event.get(
            "user"
        )
    ):

        return None


    text = (
        event.get(
            "text"
        )
    )


    if not isinstance(
        text,
        str,
    ):

        return None


    return (
        text.strip()
        or None
    )


# =========================================================
# Slack 채널 멘션 질문 추출
# =========================================================

def get_mention_question(
    event,
    bot_user_id,
):
    """
    채널에서 Bot을 멘션한 메시지에서
    Bot 호출 표시를 제거하고
    실제 질문만 반환합니다.
    """

    if (
        not bot_user_id
        or event.get(
            "bot_id"
        )
        or event.get(
            "subtype"
        )
        or not event.get(
            "user"
        )
        or not event.get(
            "ts"
        )
        or event.get(
            "channel_type"
        )
        == "im"
    ):

        return None


    text = (
        event.get(
            "text"
        )
    )


    mention = (
        f"<@{bot_user_id}>"
    )


    if (
        not isinstance(
            text,
            str,
        )
        or mention
        not in text
    ):

        return None


    question = (
        text
        .replace(
            mention,
            ""
        )
        .strip()
    )


    return (
        question
        or None
    )


# =========================================================
# Multi Agent 실행
# =========================================================

def run_project_agent(
    graph,
    question,
):
    """
    기존 main.py와 동일한 입력 구조로
    LangGraph Multi Agent를 실행합니다.
    """

    from langchain_core.messages import (
        HumanMessage,
    )


    result = (
        graph.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=
                            question
                    )
                ],

                "completed_steps":
                    [],
            },

            config={
                "recursion_limit":
                    30
            },
        )
    )


    # =====================================================
    # final_answer 확인
    # =====================================================

    answer = (
        result.get(
            "final_answer"
        )
    )


    if (
        not isinstance(
            answer,
            str,
        )
        or not answer.strip()
    ):

        raise ValueError(
            "프로젝트가 final_answer를 "
            "반환하지 않았습니다."
        )


    return result


# =========================================================
# Slack Bridge
# =========================================================

class SlackBridge:
    """
    Slack DM과 채널 멘션 요청을 받아
    Multi Agent를 순차 실행합니다.

    동시에 여러 요청이 들어오더라도
    ThreadPoolExecutor(max_workers=1)을 사용해
    한 번에 하나씩 분석합니다.
    """

    def __init__(
        self,
        runner,
        delivery_runner,
        base_dir=BASE_DIR,
    ):

        self.runner = (
            runner
        )


        self.delivery_runner = (
            delivery_runner
        )


        self.base_dir = (
            Path(
                base_dir
            )
        )


        # -------------------------------------------------
        # 동시에 여러 분석이 실행되지 않도록
        # Worker 1개만 사용합니다.
        # -------------------------------------------------

        self.executor = (
            ThreadPoolExecutor(
                max_workers=1
            )
        )


        # -------------------------------------------------
        # 같은 Slack Event가 재전송되는 경우
        # 중복 실행 방지
        # -------------------------------------------------

        self.seen = (
            deque(
                maxlen=1000
            )
        )


        self.event_lock = (
            Lock()
        )


    # =====================================================
    # DM 수신
    # =====================================================

    def receive(
        self,
        event,
        body,
        client,
    ):

        question = (
            get_dm_question(
                event
            )
        )


        return (
            self._enqueue(
                event=
                    event,

                body=
                    body,

                client=
                    client,

                question=
                    question,
            )
        )


    # =====================================================
    # 채널 멘션 수신
    # =====================================================

    def receive_mention(
        self,
        event,
        body,
        client,
        bot_user_id,
    ):

        question = (
            get_mention_question(
                event=
                    event,

                bot_user_id=
                    bot_user_id,
            )
        )


        if (
            question
            is None
        ):

            return


        reply_event = (
            event.copy()
        )


        # -------------------------------------------------
        # 채널 멘션은 원본 메시지의 스레드로 답변
        # -------------------------------------------------

        reply_event[
            "thread_ts"
        ] = (
            event.get(
                "thread_ts"
            )
            or event[
                "ts"
            ]
        )


        return (
            self._enqueue(
                event=
                    reply_event,

                body=
                    body,

                client=
                    client,

                question=
                    question,
            )
        )


    # =====================================================
    # 요청 Queue 등록
    # =====================================================

    def _enqueue(
        self,
        event,
        body,
        client,
        question,
    ):

        if (
            question
            is None
        ):

            return


        event_key = (
            body.get(
                "team_id"
            ),

            event.get(
                "channel"
            ),

            body.get(
                "event_id"
            )
            or event.get(
                "ts"
            ),
        )


        # -------------------------------------------------
        # 중복 Event 차단
        # -------------------------------------------------

        with self.event_lock:

            if (
                event_key
                in self.seen
            ):

                return


            self.seen.append(
                event_key
            )


        # -------------------------------------------------
        # Slack Event 처리는 빠르게 종료하고
        # 실제 분석은 별도 Thread에서 순차 실행
        # -------------------------------------------------

        return (
            self.executor.submit(

                self._process,

                event.copy(),

                question,

                client,
            )
        )


    # =====================================================
    # Slack 안내 메시지
    # =====================================================

    def _notify(
        self,
        client,
        event,
        text,
    ):

        try:

            client.chat_postMessage(
                channel=
                    event[
                        "channel"
                    ],

                text=
                    text,

                thread_ts=
                    event.get(
                        "thread_ts"
                    ),
            )


        except Exception as exc:

            LOGGER.error(
                "Slack 안내 전송 실패 (%s)",
                type(exc).__name__,
            )


    # =====================================================
    # 실제 요청 처리
    # =====================================================

    def _process(
        self,
        event,
        question,
        client,
    ):

        # =================================================
        # 질문 확인
        # =================================================

        if not question:

            self._notify(
                client=
                    client,

                event=
                    event,

                text=
                    "봇 멘션과 함께 "
                    "분석할 질문을 입력해 주세요.",
            )

            return


        # =================================================
        # Multi Agent 실행
        # =================================================

        try:

            result = (
                self.runner(
                    question
                )
            )


            answer = (
                result.get(
                    "final_answer"
                )
            )


            if (
                not isinstance(
                    answer,
                    str,
                )
                or not answer.strip()
            ):

                raise ValueError(
                    "최종 답변이 없습니다."
                )


        except Exception as exc:

            LOGGER.exception(
                "프로젝트 실행 실패 (%s)",
                type(exc).__name__,
            )


            self._notify(
                client=
                    client,

                event=
                    event,

                text=
                    "분석 중 오류가 발생했습니다. "
                    "실행 터미널과 로그를 확인해 주세요.",
            )


            return


        # =================================================
        # Delivery Agent 실행
        # =================================================
        #
        # 여기에서
        #
        # final_answer
        # CSV
        # PNG
        # Model 결과
        # Experiment 결과
        #
        # 를 Slack으로 전달합니다.
        # =================================================

        try:

            delivery_result = (
                self.delivery_runner(

                    result=
                        result,

                    client=
                        client,

                    channel_id=
                        event[
                            "channel"
                        ],

                    thread_ts=
                        event.get(
                            "thread_ts"
                        ),

                    base_dir=
                        self.base_dir,
                )
            )


        except Exception as exc:

            LOGGER.exception(
                "Delivery Agent 실행 실패 (%s)",
                type(exc).__name__,
            )


            self._notify(
                client=
                    client,

                event=
                    event,

                text=
                    "분석은 완료되었지만 "
                    "결과 전달 중 오류가 발생했습니다. "
                    "실행 로그를 확인해 주세요.",
            )


            return


        # =================================================
        # Delivery 실패
        # =================================================

        if (
            delivery_result.get(
                "status"
            )
            == "failed"
        ):

            LOGGER.error(
                "Slack Delivery 실패: %s",
                delivery_result.get(
                    "error"
                ),
            )


            self._notify(
                client=
                    client,

                event=
                    event,

                text=
                    "분석은 완료되었지만 "
                    "Slack 결과 전송에 실패했습니다.",
            )


            return


        # =================================================
        # 일부 첨부 실패
        # =================================================

        if (
            delivery_result.get(
                "status"
            )
            == "partial_success"
        ):

            LOGGER.warning(
                "Slack Delivery 일부 실패: %s",
                delivery_result,
            )


            self._notify(
                client=
                    client,

                event=
                    event,

                text=
                    "최종 답변은 전송했지만 "
                    "일부 결과 파일을 첨부하지 못했습니다. "
                    "실행 로그를 확인해 주세요.",
            )


    # =====================================================
    # 종료
    # =====================================================

    def close(
        self,
    ):

        # -------------------------------------------------
        # 아직 실행되지 않은 대기 요청은 취소하고
        # 실행 중인 요청은 종료될 때까지 기다립니다.
        # -------------------------------------------------

        self.executor.shutdown(
            wait=True,
            cancel_futures=True,
        )


# =========================================================
# Main
# =========================================================

def main():

    # =====================================================
    # 필요한 라이브러리
    # =====================================================

    from dotenv import (
        load_dotenv,
    )


    from slack_bolt import (
        App,
    )


    from slack_bolt.adapter.socket_mode import (
        SocketModeHandler,
    )


    # =====================================================
    # .env 로드
    # =====================================================

    load_dotenv(
        BASE_DIR
        / "env"
        / ".env"
    )


    load_dotenv(
        BASE_DIR
        / ".env"
    )


    # =====================================================
    # Slack Token
    # =====================================================

    bot_token = (
        os.getenv(
            "SLACK_BOT_TOKEN",
            "",
        )
        .strip()
    )


    app_token = (
        os.getenv(
            "SLACK_APP_TOKEN",
            "",
        )
        .strip()
    )


    # =====================================================
    # Token 검사
    # =====================================================

    if not bot_token.startswith(
        "xoxb-"
    ):

        raise ValueError(
            "SLACK_BOT_TOKEN을 "
            "환경변수에 설정해 주세요."
        )


    if not app_token.startswith(
        "xapp-"
    ):

        raise ValueError(
            "SLACK_APP_TOKEN을 "
            "환경변수에 설정해 주세요."
        )


    # =====================================================
    # 프로젝트 작업 디렉터리
    # =====================================================

    os.chdir(
        BASE_DIR
    )


    # =====================================================
    # Logging 설정
    # =====================================================

    from env.logger import (
        configure_logging,
        request_log,
    )


    configure_logging(
        capture_print=True,
        retention_days=30,
    )


    # =====================================================
    # LangGraph
    # =====================================================

    from make_graph import (
        graph,
    )


    # =====================================================
    # Delivery Agent
    # =====================================================

    from delivery_agent import (
        deliver_to_slack,
    )


    # =====================================================
    # Agent Runner
    # =====================================================

    def runner(
        question,
    ):

        with request_log(
            question
        ):

            return (
                run_project_agent(
                    graph=
                        graph,

                    question=
                        question,
                )
            )


    # =====================================================
    # Slack Bridge
    # =====================================================

    bridge = (
        SlackBridge(
            runner=
                runner,

            delivery_runner=
                deliver_to_slack,

            base_dir=
                BASE_DIR,
        )
    )


    handler = None


    try:

        # =================================================
        # Slack App
        # =================================================

        app = App(
            token=
                bot_token
        )


        # =================================================
        # DM Event
        # =================================================

        @app.event(
            "message"
        )
        def handle_dm(
            event,
            body,
            client,
        ):

            bridge.receive(
                event=
                    event,

                body=
                    body,

                client=
                    client,
            )


        # =================================================
        # Channel Mention Event
        # =================================================

        @app.event(
            "app_mention"
        )
        def handle_mention(
            event,
            body,
            client,
            context,
        ):

            bridge.receive_mention(

                event=
                    event,

                body=
                    body,

                client=
                    client,

                bot_user_id=
                    context.get(
                        "bot_user_id"
                    ),
            )


        # =================================================
        # Socket Mode
        # =================================================

        handler = (
            SocketModeHandler(
                app,
                app_token,
            )
        )


        print(
            "Slack DM·멘션 봇을 시작합니다."
        )


        print(
            "종료: Ctrl+C"
        )


        handler.start()


    finally:

        # =================================================
        # Socket 종료
        # =================================================

        if (
            handler
            is not None
        ):

            handler.close()


        # =================================================
        # ThreadPool 종료
        # =================================================

        bridge.close()


# =========================================================
# 실행
# =========================================================

if __name__ == "__main__":

    main()