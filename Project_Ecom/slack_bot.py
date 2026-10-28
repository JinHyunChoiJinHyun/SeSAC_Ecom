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
import time

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


STATUS_RUNNING = "🔄 요청을 처리하고 있습니다…"
STATUS_FAILED = (
    "⚠️ 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요. "
    "문제가 계속되면 담당자에게 문의해 주세요."
)
STATUS_SHUTDOWN = (
    "⚠️ 봇이 종료되어 요청을 처리하지 못했습니다. "
    "서비스가 재개된 후 다시 요청해 주세요."
)


def get_queued_status(ahead):
    """접수 시점의 대기 상황을 안내합니다."""
    if ahead == 0:
        return "⏳ 요청을 접수했습니다. 곧 처리를 시작합니다."
    return (
        "⏳ 요청을 접수했습니다. 앞에 처리 중이거나 "
        f"대기 중인 요청이 {ahead}건 있습니다."
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


    return question


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

        # -------------------------------------------------
        # 실행 중이거나 대기 중인 요청과 상태 메시지 정보를 관리합니다.
        # Future -> (client, event, status_ts)
        # -------------------------------------------------
        self.pending = {}
        # 종료가 시작되면 새로운 요청을 접수하지 않습니다.
        self.closing = False


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
        if question is None:
            return

        event = event.copy()
        event_key = (
            body.get("team_id"),
            event.get("channel"),
            body.get("event_id") or event.get("ts"),
        )

        # -------------------------------------------------
        # 중복 Event 차단
        # 접수 순서와 대기 건수도 같은 잠금 안에서 관리합니다.
        # -------------------------------------------------
        with self.event_lock:
            if event_key in self.seen:
                return
            self.seen.append(event_key)

            # -------------------------------------------------
            # 질문 확인
            # 빈 멘션은 대기열에 넣지 않고 즉시 입력을 안내합니다.
            # -------------------------------------------------
            if not question:
                self._notify(
                    client, event,
                    "봇 멘션과 함께 요청할 내용을 입력해 주세요.",
                )
                return

            # 종료 중에 도착한 요청은 등록하지 않고 안내합니다.
            if self.closing:
                self._notify(client, event, STATUS_SHUTDOWN)
                return

            # -------------------------------------------------
            # 접수 시점의 실행 중 + 대기 중 요청 수를 안내합니다.
            # 상태 메시지를 먼저 게시해 작업 시작 전에 ts를 확보합니다.
            # -------------------------------------------------
            ahead = sum(not future.done() for future in self.pending)
            status_ts = self._post_status(
                client, event, get_queued_status(ahead),
            )
            # -------------------------------------------------
            # 실제 요청 처리는 별도 Thread에서 순차 실행합니다.
            # -------------------------------------------------
            future = self.executor.submit(
                self._process, event, question, client, status_ts,
            )
            self.pending[future] = (client, event, status_ts)

        # 이미 완료된 Future는 즉시 콜백을 실행하므로 잠금 밖에서 등록합니다.
        future.add_done_callback(self._forget)
        return future

    # =====================================================
    # 완료된 요청 정리
    # =====================================================

    def _forget(self, future):
        """완료되거나 취소된 요청을 목록에서 제거합니다."""
        with self.event_lock:
            self.pending.pop(future, None)

    # =====================================================
    # 요청 상태 메시지 게시 및 수정
    # =====================================================

    def _post_status(self, client, event, text):
        """요청별 상태 메시지를 게시합니다."""
        try:
            response = client.chat_postMessage(
                channel=event["channel"],
                text=text,
                thread_ts=event.get("thread_ts"),
            )
            return response.get("ts")
        except Exception as exc:
            LOGGER.error(
                "Slack 상태 메시지 전송 실패 (%s)", type(exc).__name__,
            )
            return None

    def _set_status(self, client, event, status_ts, text):
        """기존 상태 메시지를 수정합니다."""
        # 게시에 실패해 ts가 없으면 수정은 건너뛰고 요청 처리는 계속합니다.
        if not status_ts:
            return False
        try:
            client.chat_update(
                channel=event["channel"], ts=status_ts, text=text,
            )
            return True
        except Exception as exc:
            LOGGER.error(
                "Slack 상태 메시지 수정 실패 (%s)", type(exc).__name__,
            )
            return False

    # =====================================================
    # 종료로 중단된 요청 안내
    # =====================================================

    def _notify_shutdown(self, client, event, status_ts):
        """중단된 요청에 종료 사실을 안내합니다."""
        if not self._set_status(client, event, status_ts, STATUS_SHUTDOWN):
            self._notify(client, event, STATUS_SHUTDOWN)


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
        status_ts=None,
    ):
        # =================================================
        # 처리 시작 상태와 경과 시간 기록
        # =================================================
        started = time.perf_counter()
        self._set_status(client, event, status_ts, STATUS_RUNNING)

        # =================================================
        # Multi Agent 실행
        # =================================================
        try:
            result = self.runner(question)
            # 예외 없이 오류 State가 반환된 경우도 실패로 처리합니다.
            if result.get("error"):
                raise RuntimeError(result["error"])

            # 최종 답변이 실제로 생성되었는지 확인합니다.
            answer = result.get("final_answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("최종 답변이 없습니다.")
        except Exception as exc:
            LOGGER.exception(
                "프로젝트 실행 실패 (%s)", type(exc).__name__,
            )
            # 기존 상태 수정에 실패한 경우에만 새 실패 안내를 보냅니다.
            if not self._set_status(client, event, status_ts, STATUS_FAILED):
                self._notify(client, event, STATUS_FAILED)
            return

        # =================================================
        # 처리 완료 상태 안내
        # 결과 생성 시간만 표시하고 기존 Delivery로 결과를 전송합니다.
        # =================================================
        elapsed = time.perf_counter() - started
        self._set_status(
            client, event, status_ts,
            f"✅ 요청 처리 완료 ({elapsed:.1f}초). 결과를 전송합니다.",
        )

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
                    "요청 처리는 완료되었지만 결과를 전송하지 못했습니다. "
                    "잠시 후 다시 시도해 주세요. "
                    "문제가 계속되면 담당자에게 문의해 주세요.",
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
                    "요청 처리는 완료되었지만 결과를 전송하지 못했습니다. "
                    "잠시 후 다시 시도해 주세요. "
                    "문제가 계속되면 담당자에게 문의해 주세요.",
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
                    "다시 요청해 주시고, 문제가 계속되면 담당자에게 문의해 주세요.",
            )


    # =====================================================
    # 종료
    # =====================================================

    def close(self):
        """대기 요청을 취소하고 실행 중인 요청을 마무리합니다."""
        # -------------------------------------------------
        # 새 요청 접수를 막고 취소 전 요청 목록을 보관합니다.
        # -------------------------------------------------
        with self.event_lock:
            self.closing = True
            # 취소 콜백이 목록을 지우기 전에 요청 정보를 복사합니다.
            jobs = list(self.pending.items())

        try:
            # -------------------------------------------------
            # 아직 실행되지 않은 대기 요청은 취소하고 종료를 안내합니다.
            # 실행 중인 요청은 결과 전송까지 마칠 수 있도록 유지합니다.
            # -------------------------------------------------
            self.executor.shutdown(wait=False, cancel_futures=True)
            for future, job in jobs:
                if future.cancelled():
                    self._notify_shutdown(*job)

            running = [(future, job) for future, job in jobs if not future.done()]
            if not running:
                return

            print(
                "실행 중인 요청을 마무리한 뒤 종료합니다. "
                "바로 끄려면 Ctrl+C를 한 번 더 누르세요."
            )
            # Windows에서도 두 번째 Ctrl+C를 받도록 짧게 나누어 기다립니다.
            while not all(future.done() for future, _ in running):
                time.sleep(0.5)
        except KeyboardInterrupt:
            # 두 번째 Ctrl+C에는 실행 중인 요청에도 중단을 안내합니다.
            for future, job in jobs:
                if not future.done():
                    self._notify_shutdown(*job)
            raise


# =========================================================
# 첫 번째 종료 입력 대기
# =========================================================

def wait_until_interrupted():
    """메인 스레드에서 종료 입력을 기다립니다."""
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("종료 요청을 받았습니다. 대기 중인 요청에 안내를 보냅니다.")


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


        # Windows의 기본 Ctrl+C 처리를 유지하도록 직접 연결 후 기다립니다.
        handler.connect()
        wait_until_interrupted()


    finally:
        # Socket을 먼저 닫고 요청 처리를 마무리합니다.
        try:
            try:
                # =================================================
                # Socket 종료
                # =================================================
                if handler is not None:
                    handler.close()
            finally:
                # =================================================
                # ThreadPool 종료
                # =================================================
                bridge.close()
        except KeyboardInterrupt:
            # 실행 중인 Worker를 기다리지 않고 프로세스를 즉시 종료합니다.
            print("강제 종료합니다.", flush=True)
            os._exit(1)


# =========================================================
# 실행
# =========================================================

if __name__ == "__main__":

    main()