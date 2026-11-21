"""
Team_Project_Ecom Slack DM·채널 멘션 연결 파일입니다.

역할
----
1. Slack DM 또는 채널 멘션으로 질문을 받습니다.
2. 대화 그래프를 실행합니다 (멀티턴).
   - Orchestrator: 일상 대화는 바로 답하고, 업무 요청은 이전 대화를 반영해 질문을 다시 써서 특화 Agent로 보냄
   - General Analysis Agent(공통 분석 LangGraph): 조회 / 전처리 / 시각화
   - 대화 기억: LangGraph Checkpointer(runtime/conversation_memory.sqlite) + thread_id (DM=사용자별, 채널=채널+사용자별)
   데이터: common/data_sources.py에 등록된 출처 (현재 H&M 자사 데이터 + Amazon 외부 리뷰 데이터)
3. Delivery Agent를 호출합니다.
4. final_answer를 Slack으로 전송합니다.
5. 결과 DataFrame을 CSV로 생성해 첨부합니다.
6. Visualization PNG가 있으면 같이 첨부합니다.


설치
----
python -m pip install slack-bolt python-dotenv


환경변수
--------
SLACK_BOT_TOKEN
SLACK_APP_TOKEN

(선택) LangSmith 추적 — 대화를 thread 단위로 묶어 볼 수 있습니다.
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=cart-signal


실행
----
python slack_bot.py
"""


import logging
import json
import os
import re
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

    무시할 이벤트는 None을 반환합니다.
    유효한 멘션에서 질문이 비어 있으면 빈 문자열을 반환합니다.
    호출 흐름의 _enqueue()에서 이를 확인하고 입력 안내를 보냅니다.
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
    from env.model_settings import get_request_config


    result = (
        graph.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=
                            question
                    )
                ],

                "llm_config": get_request_config().public(),
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
# Slack 요청 유형별 실행 경로
# =========================================================



def _has_visualization_intent(question: str) -> bool:
    """차트 제외, 설명, 신규 생성 요청을 구분합니다."""
    text = str(question or "").strip()

    # 1. 차트 제외 요청을 가장 먼저 처리
    excluded = (
        r"(?:그래프|차트|시각화)\s*없이"
        r"|(?:그래프|차트|시각화)\s*(?:말고|빼고|제외)"
        r"|(?:표|테이블)\s*로\s*만"
        r"|(?:표|테이블)\s*만"
    )
    if re.search(excluded, text, re.I):
        return False

    chart_terms = (
        r"(?:막대그래프|선그래프|히스토그램|산점도|히트맵|"
        r"그래프|차트|시각화|bar\s*chart|scatter\s*plot)"
    )

    # 2. 명시적인 신규 차트 생성 요청
    create_after_chart = (
        chart_terms
        + r".{0,30}?"
        + r"(?:그려|만들어|생성해|시각화해|출력해|표시해)"
    )
    create_before_chart = (
        r"(?:그려|만들어|생성해|시각화해)"
        + r".{0,30}?"
        + chart_terms
    )

    if re.search(
        create_after_chart + "|" + create_before_chart,
        text,
        re.I,
    ):
        return True

    # 3. 기존 차트 설명·해석만 요청하는 경우 제외
    explanation = (
        r"(?:그래프|차트|시각화).{0,30}"
        r"(?:의미|해석|설명|읽는\s*법|무슨\s*뜻)"
        r"|(?:앞서|이전|기존|방금|이)\s*"
        r"(?:만든|그린|생성한)?\s*"
        r"(?:그래프|차트).{0,30}"
        r"(?:설명|해석|의미)"
    )
    if re.search(explanation, text, re.I):
        return False

    # 4. 신규 차트 표시 요청
    show_after_chart = (
        chart_terms
        + r".{0,30}?"
        + r"(?:보여줘|보여주세요|표시해줘|출력해줘)"
    )

    return bool(re.search(show_after_chart, text, re.I))


def _is_user_facing_error(result: dict) -> bool:
    """사용자에게 그대로 안내해야 하는 오류인지 확인합니다.

    예: 수집 중이라 잠긴 데이터 출처 → "잠시 후 다시 시도" 안내를 Slack에 그대로 전달
    """
    from agent_system.common_tools.execution_tools.data_tools import SOURCE_UNAVAILABLE_PREFIX

    return str(result.get("error") or "").startswith(SOURCE_UNAVAILABLE_PREFIX)


def _ensure_graph_success(result: dict, label: str, require_visualization=False):
    """작업 실패를 완료로 간주하거나 이전 실행 이미지를 재사용하지 않습니다."""
    if not isinstance(result, dict):
        raise TypeError(f"{label}: LangGraph 결과가 dict가 아닙니다.")
    if result.get("error") or result.get("visualization_error"):
        raise RuntimeError(f"{label}: {result.get('error') or result.get('visualization_error')}")
    if not str(result.get("final_answer") or "").strip():
        raise ValueError(f"{label}: 최종 답변이 없습니다.")
    if require_visualization:
        visualization_result = result.get("visualization_result") or {}
        chart_path = result.get("visualization_path") or (
            visualization_result.get("file_path")
            if isinstance(visualization_result, dict) else None
        )
        if not chart_path or "visualization" not in (result.get("completed_steps") or []):
            raise ValueError(f"{label}: 시각화 결과가 생성되지 않았습니다.")


def run_slack_question(question, graph_runner):
    """Slack 요청 한 턴을 실행하고 결과를 확인합니다.

    graph_runner(question)는 Orchestrator 그래프(Orchestrator → 특화 Agent)를 실행합니다.

    - 일상 대화(chitchat): Orchestrator 답변을 그대로 전달
    - 분석(analysis): 그래프 요청이면 차트가 실제로 생성됐는지 확인
      (후속 질문은 재작성된 질문 기준으로 그래프 요청 여부를 판단)
    """

    result = graph_runner(question)

    if not isinstance(result, dict):
        raise TypeError("대화 그래프 결과가 dict가 아닙니다.")

    if result.get("turn_type") in ("chitchat", "approval_request", "approval"):
        print(f"[Slack Router] Orchestrator 답변 ({result.get('turn_type')})")
        return result

    if _is_user_facing_error(result):
        print("[Slack Router] 데이터 출처 일시 사용 불가 안내")
        return result

    if result.get("turn_type") == "report":
        print("[Slack Router] 보고서 전달 (PDF·Word 첨부)")
        return result

    if result.get("turn_type") == "workflow":
        # 워크플로는 여러 단계 결과를 한 번에 전달 (그래프는 단계 결과에 있을 때만 첨부)
        print("[Slack Router] 워크플로 단계 결과")
        return result

    analysed_question = result.get("standalone_question") or question
    if result.get("comparison_status"):
        # 비교 가능 데이터 부족은 오류가 아니라 근거 부족이라는 분석 결과임
        print(f"[Slack Router] 카테고리 비교 결과: {result['comparison_status']}", flush=True)
        return result
    if _has_visualization_intent(analysed_question):
        print("[Slack Router] 시각화 요청: 차트 생성 확인")
        _ensure_graph_success(result, "시각화", require_visualization=True)
    else:
        print("[Slack Router] 분석 요청")

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
    답변 생성부터 Slack 결과 전송까지 한 번에 하나의 요청을 처리합니다.
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
        # 답변 생성과 Slack 결과 전송을 포함한 요청을 순차 처리하도록
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


    def receive_report_file(self, event, body, client):
        """수정된 Word 첨부는 일반 텍스트 질문 대신 PDF 변환 큐로 보냅니다."""
        from interfaces.slack.report_finalize import _report_file, process_uploaded_docx

        file_info = _report_file(event)
        if file_info is None:
            return False
        event_key = (body.get("team_id"), event.get("channel"),
                     body.get("event_id") or event.get("ts"))
        with self.event_lock:
            if event_key in self.seen:
                return True
            self.seen.append(event_key)
            if self.closing:
                return True
            self.executor.submit(process_uploaded_docx, client, event.copy(), file_info)
        return True

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
    # 버튼 클릭 수신 (승인·반려·워크플로 계속 등, interfaces/slack/actions.py)
    # =====================================================

    def receive_action(self, body, client):
        """버튼 클릭 → 요청한 사용자인지 확인 → 버튼을 선택 결과로 바꾸고 → 다른 요청처럼 순서대로 처리."""
        from agent_system.orchestrator.tools.memory import build_slack_thread_id
        from interfaces.slack.actions import parse_action, resolved_blocks

        parsed = parse_action(body)
        if parsed is None:
            return None
        event = parsed["event"]
        # CART_SIGNAL_BUTTON_ID_DIAGNOSTIC_V1
        LOGGER.info(
            "[버튼 권한 진단] clicked_user=%s channel=%s "
            "clicked_thread=%s original_thread=%s "
            "thread_ts=%s message_ts=%s",
            event.get("user"),
            event.get("channel"),
            build_slack_thread_id(event),
            parsed["thread_id"],
            event.get("thread_ts"),
            parsed.get("message_ts"),
        )
        # CART_SIGNAL_BUTTON_OWNER_CHECK_V2
        # 세션 ID와 기본 Slack 대화 ID는 다를 수 있으므로 요청자 ID로 권한 검사합니다.
        owner = parsed.get("requester_user_id", "")
        LOGGER.info("[버튼 요청자 확인] clicked_user=%s owner=%s", event.get("user"), owner)
        if not owner or event.get("user") != owner:
            # 다른 사용자 또는 이전 버전 버튼: 처리하지 않고 안내
            try:
                client.chat_postEphemeral(channel=event["channel"], user=event["user"],
                                          thread_ts=event.get("thread_ts"),
                                          text=("이전 버전 버튼입니다. 새 승인 요청 버튼을 사용해 주세요."
                                                if not owner else "이 버튼은 요청한 사용자만 누를 수 있습니다."))
            except Exception as exc:
                LOGGER.error("버튼 안내 전송 실패 (%s)", type(exc).__name__)
            return None
        try:
            client.chat_update(channel=event["channel"], ts=parsed["message_ts"],
                               text=f"선택: {parsed['label']}",
                               blocks=resolved_blocks(parsed["blocks"], parsed["label"], event["user"]))
        except Exception as exc:  # 버튼을 못 지워도 처리는 계속 (같은 버튼 두 번째 클릭은 아래에서 차단)
            LOGGER.error("버튼 메시지 수정 실패 (%s)", type(exc).__name__)
        event = {**event, "cart_signal_action": parsed["action"]}
        return self._enqueue(
            event=event,
            body={"team_id": parsed["team_id"], "event_id": f"action:{parsed['message_ts']}"},
            client=client,
            question=f"[버튼] {parsed['label']}",
        )

    # =====================================================
    # 사용자 확인 버튼 게시
    # =====================================================

    def _post_actions(self, client, event, result):
        """결과가 사용자 확인(승인·워크플로)을 기다리면 버튼 메시지를 붙입니다."""
        from interfaces.slack.actions import build_action_blocks

        try:
            blocks = build_action_blocks(
                result.get("actions"), result.get("thread_id", ""),
                requester_user_id=event.get("user", ""),
            )
        except Exception as exc:
            LOGGER.error("버튼 만들기 실패 (%s)", type(exc).__name__)
            return False
        if not blocks:
            return False
        try:
            client.chat_postMessage(channel=event["channel"], thread_ts=event.get("thread_ts"),
                                    text="버튼으로 선택해 주세요.", blocks=blocks)
            return True
        except Exception as exc:  # 버튼을 못 붙여도 글자로 답할 수 있음
            LOGGER.error("버튼 메시지 전송 실패 (%s)", type(exc).__name__)
            return False

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
            # 작업 시작 전에 상태 메시지 게시를 시도하고, 성공 시 반환된 ts로 수정합니다.
            # 게시에 실패하거나 ts가 없어도 요청 처리는 계속합니다.
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

            return True

        except Exception as exc:

            LOGGER.error(
                "Slack 안내 전송 실패 (%s)",
                type(exc).__name__,
            )
            return False


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
        # 결과 검증과 Slack 전송까지 같은 요청 ID로 기록합니다.
        from env.logger import request_log

        from env.model_settings import model_request, ModelSettingsError

        with request_log(question):
            try:
                with model_request():
                    return self._process_request(event, question, client, status_ts)
            except ModelSettingsError:
                LOGGER.error("요청 모델 설정을 확인하지 못했습니다.")
                if not self._set_status(client, event, status_ts, STATUS_FAILED):
                    self._notify(client, event, STATUS_FAILED)
                self._record_result(event, "failed", "not_attempted", error_type="ModelSettingsError")

    def _record_result(self, event, analysis_status, delivery_status="unknown", **details):
        """안내 문구 대신 명시적인 처리 결과를 공통 로그에 기록합니다."""
        from env.logger import get_logger

        get_logger().info(
            "ADMIN_RESULT %s",
            json.dumps(
                {
                    "analysis_status": analysis_status,
                    "delivery_status": delivery_status,
                    "channel": event.get("channel", ""),
                    **details,
                },
                ensure_ascii=False,
            ),
        )

    def _process_request(self, event, question, client, status_ts=None):
        # =================================================
        # 처리 시작 상태와 경과 시간 기록
        # =================================================
        started = time.perf_counter()
        self._set_status(client, event, status_ts, "🧭 요청 분석 및 담당 Agent 선택 중...")

        # =================================================
        # Multi Agent 실행
        # =================================================
        try:
            # 대화 기억(멀티턴)을 위해 Slack 이벤트도 함께 넘깁니다.
            # CART_SIGNAL_PROGRESS_V1: 같은 작업 스레드에서 Agent 단계별 Slack 상태 갱신
            from agent_system.orchestrator.tools.progress_status import progress_scope
            with progress_scope(lambda text: self._set_status(client, event, status_ts, text)):
                result = self.runner(question, event)
            # 예외 없이 오류 State가 반환된 경우도 실패로 처리합니다.
            # 단, 출처 일시 사용 불가처럼 사용자에게 안내할 오류는 답변으로 전달합니다.
            if result.get("error") and not _is_user_facing_error(result):
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
            notified = self._set_status(client, event, status_ts, STATUS_FAILED)
            if not notified:
                notified = self._notify(client, event, STATUS_FAILED)
            # 실패 안내 전송 여부와 답변 생성 실패를 별도로 기록합니다.
            self._record_result(
                event, "failed", "not_attempted",
                failure_notice_status="success" if notified else "failed",
                error_type=type(exc).__name__,
            )
            return

        # =================================================
        # 답변 생성·검증 완료 안내 및 결과 전송 시작
        # 표시 시간은 결과 생성·검증까지이며 Slack 전송 완료를 뜻하지 않습니다.
        # =================================================
        self._record_result(event, "completed")
        elapsed = time.perf_counter() - started
        self._set_status(
            client, event, status_ts,
            f"✅ 요청 처리 완료 ({elapsed:.1f}초). 결과를 전송합니다.",
        )

        # =================================================
        # Delivery Agent 실행
        # =================================================
        # 최종 답변을 전송하고, 첨부 가능한 시각화 파일과
        # Model의 별도 결과 파일이 있으면 함께 전송합니다.
        # CSV용 결과 DataFrame 선택은 별도 결과 파일 수집과 독립적이며,
        # Model → Prep → Data 순서로 선택합니다.
        # 행 수 제한 등 전송 정책에 따라 CSV 생성·첨부 여부를 결정합니다.
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

            self._record_result(
                event, "completed", "failed", error_type=type(exc).__name__,
            )
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


        # 답변 생성과 Slack 전송 결과를 구분합니다.
        delivery_status = delivery_result.get("status", "unknown")
        if delivery_status not in {"success", "failed", "partial_success"}:
            delivery_status = "unknown"
        self._record_result(event, "completed", delivery_status)

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


        # CART_SIGNAL_DELIVERY_ORDER_V1: 실패 안내와 파일 공유 확인 이후 버튼 게시
        if delivery_result.get("share_confirmation") is False:
            self._notify(client, event, "파일 업로드는 완료됐지만 Slack 공유 게시를 확인하지 못했습니다. 첨부파일이 모두 보이는지 확인한 뒤 선택해 주세요.")

        # 승인 대기·워크플로 멈춤이면 확인 버튼을 붙입니다 (글자로 '승인'을 쓰지 않아도 됨)
        self._post_actions(client, event, result)
        # CART_SIGNAL_TOPIC_BUTTONS_V1
        if isinstance(result, dict) and result.get("topic_confirmation"):
            from interfaces.slack.topic_confirmation_buttons import build_blocks
            from agent_system.orchestrator.tools.memory import build_slack_thread_id
            try:
                blocks = build_blocks(build_slack_thread_id(event))
                if blocks:
                    client.chat_postMessage(channel=event["channel"],
                                            thread_ts=event.get("thread_ts"),
                                            text="새 분석 세션으로 분리할까요?",
                                            blocks=blocks)
            except Exception as exc:
                LOGGER.error("주제 전환 버튼 게시 실패 (%s)", type(exc).__name__)
        LOGGER.info("[Slack 선택 안내 게시 완료 시도]")


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

    # 프로젝트 루트의 .env 파일만 사용합니다.
    load_dotenv(BASE_DIR / ".env")


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
    )


    configure_logging(
        capture_print=True,
        retention_days=30,
    )


    # =====================================================
    # LangGraph
    # =====================================================

    from agent_system.specialized_agents.general_analysis_agent import (
        get_general_analysis_graph,
    )

    # 분석 그래프를 먼저 불러와 import 오류를 시작 시점에 확인합니다.
    get_general_analysis_graph()

    # 멀티턴 대화 그래프 (SQLite 대화 기억 + LangSmith thread 메타데이터)
    from agent_system.orchestrator.graph import (
        get_graph as get_conversation_graph,
        run_turn as run_conversation_turn,
    )
    from agent_system.orchestrator.tools.memory import (
        build_slack_thread_id,
        langsmith_enabled,
        MEMORY_DB_PATH,
    )

    conversation_graph = get_conversation_graph()
    print(f"[대화 기억] {MEMORY_DB_PATH}")
    print(f"[LangSmith 추적] {'사용' if langsmith_enabled() else '사용 안 함'}")


    # =====================================================
    # Delivery Agent
    # =====================================================

    from interfaces.slack.delivery import (
        deliver_to_slack,
    )


    # =====================================================
    # Agent Runner
    # =====================================================

    def runner(question, event):
        # Orchestrator 그래프: 일상 대화는 바로 답하고,
        # 업무 요청은 이전 대화를 반영해 다시 쓴 질문으로 특화 Agent(현재 General Analysis)에 넘깁니다.
        from env.model_settings import get_request_config

        # CART_SIGNAL_ANALYSIS_SESSION_ROUTING_V1
        from agent_system.orchestrator.tools.analysis_sessions import current, parse_start_command, start, switch_allowed
        base_thread_id = build_slack_thread_id(event)
        thread_id = current(base_thread_id)
        from agent_system.orchestrator.tools.analysis_topic_guard import before_turn, remember, reset, abandon_pending
        # CART_SIGNAL_TOPIC_GUARD_V1
        next_question = parse_start_command(question)
        if next_question is not None:
            allowed, reason = switch_allowed(conversation_graph, thread_id)
            if not allowed:
                return {"turn_type": "chitchat", "final_answer": reason, "completed_steps": []}
            thread_id = start(base_thread_id)
            reset(base_thread_id)
            if not next_question:
                return {
                    "turn_type": "chitchat",
                    "final_answer": "새 분석 세션을 시작했습니다. 이전 분석·보고서는 보존됩니다. 새 주제의 분석을 요청해 주세요.",
                    "completed_steps": [],
                }
            question = next_question
        else:
            action, proposed_question = before_turn(base_thread_id, question)
            if action == "ask":
                allowed, reason = switch_allowed(conversation_graph, thread_id)
                if not allowed:
                    abandon_pending(base_thread_id)
                    return {"turn_type": "chitchat", "final_answer": reason, "completed_steps": []}
                return {
                    "turn_type": "chitchat",
                    "topic_confirmation": True,
                    "final_answer": "분석 주제가 바뀐 것 같아요. 새 분석 세션으로 분리할까요?\n"
                                    "아래 버튼으로 선택해 주세요\n"
                                    f"대기 중인 요청: {proposed_question}",
                    "completed_steps": [],
                }
            if action == "switch":
                allowed, reason = switch_allowed(conversation_graph, thread_id)
                if not allowed:
                    return {"turn_type": "chitchat", "final_answer": reason, "completed_steps": []}
                thread_id = start(base_thread_id)
                reset(base_thread_id)
                question = proposed_question
            elif action == "stay":
                question = proposed_question
        result = run_slack_question(
            question=question,
            graph_runner=lambda q: run_conversation_turn(
                question=q,
                thread_id=thread_id,
                llm_config=get_request_config().public(),
                graph=conversation_graph,
                action=event.get("cart_signal_action"),   # Slack 버튼이면 버튼 값 그대로 처리
                metadata={
                    "source": "slack",
                    "slack_channel": event.get("channel", ""),
                    "slack_user": event.get("user", ""),
                },
            ),
        )
        if isinstance(result, dict) and result.get("turn_type") == "analysis":
            remember(base_thread_id, question)
        return result


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

            if bridge.receive_report_file(event, body, client):
                return

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

        # CART_SIGNAL_FILE_SHARED_HANDLER_V1
        @app.event("file_shared")
        def handle_file_shared(event, body, client):
            """Slack 파일 공유 이벤트를 기존 DOCX 변환 처리에 전달합니다."""
            import logging

            logger = logging.getLogger("cart_signal.file_shared")
            file_id = event.get("file_id")
            if not file_id:
                logger.warning("file_shared 이벤트에 file_id가 없습니다.")
                return

            try:
                response = client.files_info(file=file_id)
                info = response.get("file") or {}
                if not info:
                    logger.warning("파일 정보를 조회하지 못했습니다: %s", file_id)
                    return

                channel = event.get("channel_id") or event.get("channel")
                if not channel:
                    shares = info.get("shares") or {}
                    for entries in shares.values():
                        for channel_id, records in entries.items():
                            if records:
                                channel = channel_id
                                break
                        if channel:
                            break

                if not channel:
                    logger.warning("파일 공유 채널을 확인하지 못했습니다: %s", file_id)
                    return

                adapted_event = {
                    "type": "message",
                    "subtype": "file_share",
                    "channel": channel,
                    "user": event.get("user_id") or info.get("user"),
                    "ts": event.get("event_ts") or event.get("ts"),
                    "files": [info],
                }

                accepted = bridge.receive_report_file(
                    adapted_event,
                    body,
                    client,
                )
                logger.info(
                    "Word 파일 처리 연결: file_id=%s, accepted=%s",
                    file_id,
                    accepted,
                )
            except Exception:
                logger.exception("file_shared 처리 중 오류가 발생했습니다.")

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
        # 버튼 클릭 (승인·반려·워크플로 계속 등)
        # Slack 앱 설정에서 Interactivity & Shortcuts를 켜야 합니다.
        # =================================================

        @app.action(re.compile("^carttopic_"))
        def handle_topic_button(ack, body, client):
            ack()
            from interfaces.slack.topic_confirmation_buttons import handle_click
            handle_click(bridge, body, client)

        @app.action(
            re.compile("^cartsignal_")
        )
        def handle_button(
            ack,
            body,
            client,
        ):
            ack()   # 3초 안에 응답해야 Slack이 오류를 표시하지 않음
            bridge.receive_action(
                body=body,
                client=client,
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
