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
        self._target_ui_lock = Lock()
        self._resolved_target_messages = deque(maxlen=1000)


    def receive_report_file(self, event, body, client):
        """수정된 Word 첨부는 일반 텍스트 질문 대신 PDF 변환 큐로 보냅니다."""
        from interfaces.slack.report_finalize import _report_file, process_uploaded_docx, process_uploaded_png
        if event.get('bot_id') or not event.get('user') or not event.get('channel'):
            return False
        files = [f for f in event.get('files', []) if isinstance(f,dict)]
        jobs = []
        for f in files:
            name = str(f.get('name') or '').lower()
            if name.endswith('.docx') and not name.startswith('~$'):
                jobs.append((process_uploaded_docx,f))
            elif name.endswith('.png'):
                jobs.append((process_uploaded_png,f))
        if not jobs:
            return False
        # 동일 메시지에서는 Word 변환부터 실행하고 파일 ID 기준 중복 이벤트를 제거한다.
        jobs.sort(key=lambda job: job[0] is process_uploaded_png)
        with self.event_lock:
            if self.closing:
                return True
            fresh=[]
            for fn,f in jobs:
                event_key=('r42file',event.get('channel'),event.get('ts'),f.get('id') or body.get('event_id'))
                if event_key not in self.seen:
                    self.seen.append(event_key); fresh.append((fn,f))
            if fresh:
                self.executor.submit(self._r424_file_batch,client,event.copy(),fresh)
        return True

    def _r424_file_batch(self,client,event,jobs):
        from interfaces.slack.report_finalize import process_uploaded_png
        from agent_system.approval_demo import recipient_flow as r, delivery_r42 as d
        question = re.sub(r'<@[^>]+>', '', str(event.get('text') or '')).strip()
        kwargs=dict(channel=event['channel'],thread_ts=event.get('thread_ts') or event.get('ts'))
        try:
            resolver=getattr(self,'resolve_report_event',None)
            if resolver: resolver(event)
            state=r._read_state(self.base_dir,event)
            r._check_delivery_state(state,event)
            for fn,f in jobs:
                ok=fn(client,event,f,defer=True) if fn is process_uploaded_png else fn(client,event,f)
                if ok is not True: return
            if r.MAIL_INTENT.search(question):
                result=self.runner(question,event)
                client.chat_postMessage(**kwargs,text=result['final_answer'])
                if result.get('recipient_confirmation'): d.post_buttons(client,event,r._read_state(self.base_dir,event)['pending'])
                elif result.get('recipient_choose'): d.post_buttons(client,event,choose=True)
            else:
                state=r._read_state(self.base_dir,event)
                pending=d.prepare_send(self.base_dir,event) if state.get('pdf') and state.get('png') else None
                if pending: d.post_buttons(client,event,pending)
                elif state.get('target',{}).get('kind')=='r39':
                    status=(state.get('pending') or {}).get('status')
                    client.chat_postMessage(**kwargs,text='수신자 확인 버튼을 눌러 주세요.' if status=='AWAITING_CONFIRMATION' else '최종 PDF를 확인하고 수신자를 지정해 주세요.' if state.get('pdf') else '수정 Word를 첨부해 주세요.')
        except Exception as exc:
            client.chat_postMessage(**kwargs,text='파일 처리 실패: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))

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
        # CART_SIGNAL_APPROVAL_SESSION_R22: 발행 당시 분석 세션과 현재 세션을 일치시킨다.
        # 버튼 payload의 ID를 직접 신뢰해 다른 사용자의 세션을 열지 않는다.
        from agent_system.orchestrator.tools.analysis_sessions import current as _session_current
        from agent_system.orchestrator.tools.approval_session_guard import session_match
        _current_session = _session_current(build_slack_thread_id(event))
        if not session_match(parsed.get("thread_id", ""), _current_session):
            LOGGER.warning("APPROVAL_SESSION_MISMATCH channel=%s", event.get("channel"))
            client.chat_postEphemeral(
                channel=event["channel"], user=event["user"],
                thread_ts=event.get("thread_ts"),
                text="이 버튼은 현재 분석 세션의 승인 요청이 아닙니다. 최신 세션에서 새 승인을 요청해 주세요.",
            )
            return None
        action = parsed['action']
        is_target = action.get('type') == 'target_selection_review'
        if is_target:
            with self._target_ui_lock:
                if action.get('view') in ('open','close'):
                    # 화면 변경만 수행한다. Graph.invoke, 고객 재분석, 승인 상태 변경 없음.
                    try:
                        from agent_system.orchestrator.graph import get_graph, pending_interrupt
                        from interfaces.slack.target_review_r422 import toggle_blocks
                        message_key = (event['channel'], parsed['message_ts'])
                        graph = get_graph()
                        snap = graph.get_state({'configurable':{'thread_id':_current_session}})
                        values = getattr(snap, 'values', None) or {}
                        if message_key in self._resolved_target_messages and (values.get('workflow_run') or {}).get('campaign_target_r39',{}).get('status') != 'confirmed':
                            raise ValueError('승인 처리 중이거나 반려된 제안입니다.')
                        blocks = toggle_blocks(parsed, values.get('workflow_run') or {},
                                               values.get('campaign') or {},
                                               values.get('pending_approval') or pending_interrupt(graph,_current_session))
                        client.chat_update(channel=event['channel'], ts=parsed['message_ts'],
                                           text='고객 타깃 검토 · 선정 근거', blocks=blocks)
                    except Exception as exc:
                        LOGGER.warning('R40 근거 화면 갱신 실패 (%s)',type(exc).__name__)
                        client.chat_postEphemeral(channel=event['channel'],user=event['user'],
                                                  thread_ts=event.get('thread_ts'),
                                                  text='선정 근거를 갱신하지 못했습니다. 최신 고객 선정 결과를 확인해 주세요.')
                    return None
                # 승인/반려와 화면 토글이 동시에 도착해도 같은 메시지의 버튼을 되살리지 않는다.
                self._resolved_target_messages.append((event['channel'],parsed['message_ts']))
                return self._resolve_and_enqueue(client,event,parsed,resolved_blocks)
        return self._resolve_and_enqueue(client,event,parsed,resolved_blocks)

    def _resolve_and_enqueue(self, client, event, parsed, resolved_blocks):
        try:
            blocks=resolved_blocks(parsed["blocks"], parsed["label"], event["user"])
            if parsed['action'].get('type')=='target_selection_review' and parsed['action'].get('command')=='approve':
                import json, uuid
                for block in parsed['blocks']:
                    for button in block.get('elements',[]):
                        if str(button.get('action_id','')).endswith('_evidence'):
                            value=json.loads(button['value'])
                            if value.get('view') not in ('open','close'): continue
                            value['view']='open'
                            evidence={**button,'value':json.dumps(value,ensure_ascii=False),'text':{'type':'plain_text','text':'선정 근거 보기'}}
                            blocks.append({'type':'actions','block_id':'cartsignal_target_evidence_'+uuid.uuid4().hex,'elements':[evidence]})
            client.chat_update(channel=event["channel"], ts=parsed["message_ts"],
                               text=f"선택: {parsed['label']}", blocks=blocks)
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

        # R39: 실제로 보고서가 전달된 스레드에만 사내 발송 준비 상태를 등록한다.
        if result.get('r39_report') and delivery_status == 'success' and delivery_result.get('share_confirmation') is not False:
            try:
                from agent_system.approval_demo.recipient_flow import register_campaign_report
                register_campaign_report(self.base_dir, event, result['r39_report'])
            except Exception:
                LOGGER.exception('R39_REPORT_REGISTER_FAILED')
                self._notify(client, event, '사내 보고 등록에 실패했습니다. 보고서를 다시 요청해 주세요.')
        # 승인 대기·워크플로 멈춤이면 확인 버튼을 붙입니다 (글자로 '승인'을 쓰지 않아도 됨)
        final_review = any(a.get('type')=='final_page_review' for a in (result.get('actions') or []))
        if not (final_review and result.get('visualization_path') and (delivery_status!='success' or delivery_result.get('share_confirmation') is False)):
            self._post_actions(client, event, result)
        # 승인 수신자 확인은 별도 네/아니요 버튼으로 표시
        confirmation = result.get('recipient_confirmation') if isinstance(result, dict) else None
        if isinstance(result, dict) and result.get("recipient_choose"):
            from agent_system.approval_demo.delivery_r42 import post_buttons
            post_buttons(client,event,choose=True)
        if result.get("r424_mail_review"):
            from agent_system.approval_demo.delivery_r42 import post_buttons
            from agent_system.approval_demo.recipient_flow import _read_state
            post_buttons(client,event,_read_state(self.base_dir,event)["pending"])
        if confirmation:
            from agent_system.approval_demo.recipient_flow import _thread
            import json as _json
            try:
                value = {'nonce': confirmation['nonce'], 'thread': _thread(event),
                         'owner': event.get('user'), 'channel': event.get('channel'),
                         'thread_ts': event.get('thread_ts') or event.get('ts')}
                buttons = [{'type': 'button', 'action_id': 'cartrecipient_' + key,
                            'text': {'type': 'plain_text', 'text': label},
                            'style': 'primary' if key == 'yes' else 'danger',
                            'value': _json.dumps(value, ensure_ascii=False)}
                           for key, label in [('yes', '네'), ('no', '아니요')]]
                client.chat_postMessage(channel=event['channel'], thread_ts=event.get('thread_ts') or event.get('ts'),
                                        text='수신자 확인: 네 / 아니요',
                                        blocks=[{'type': 'actions', 'elements': buttons}])
            except Exception as exc:
                LOGGER.error('수신자 버튼 게시 실패: %s', type(exc).__name__)
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
        # CART_SIGNAL_RESET_R12: 명시적 초기화·취소는 모든 별도 라우터보다 먼저 처리한다.
        # 동일 채널·사용자의 새 분석은 새 체크포인트 ID를 사용하므로 이전 상품·승인·분석이 상속되지 않는다.
        from agent_system.orchestrator.tools.analysis_sessions import (
            current as _session_current,
            parse_start_command as _parse_new_analysis,
            start as _session_start,
            switch_allowed as _session_switch_allowed,
        )
        from agent_system.orchestrator.tools.analysis_topic_guard import reset as _topic_reset
        from agent_system.orchestrator.tools.memory import build_slack_thread_id as _build_session_id
        from agent_system.orchestrator.tools.workflow import CANCEL_COMMAND as _cancel_command

        _base_id = _build_session_id(event)
        _active_id = _session_current(_base_id)

        # CART_SIGNAL_R32_INTEGRATED: 모든 CART_SIGNAL 버튼은 승인자/메일 라우터보다 먼저 처리합니다.
        # 일반 채팅과 cartrecipient_* 이메일 승인 버튼의 동작은 변경하지 않습니다.
        if (event.get('cart_signal_action') or {}).get('type') == 'final_page_review':
            from agent_system.orchestrator.tools import campaign_page_final_r428 as final428
            try:
                snapshot = conversation_graph.get_state({'configurable':{'thread_id':_active_id}})
                values = getattr(snapshot,'values',None) or {}
                result = final428.handle_button(event['cart_signal_action'],BASE_DIR,event,values.get('workflow_run') or {},values.get('campaign') or {})
                return {**result,'thread_id':_active_id}
            except Exception as exc:
                LOGGER.warning('R4282 final page button failed (%s)',type(exc).__name__)
                return {'turn_type':'chitchat','final_answer':'완성본 검토 상태를 확인할 수 없습니다. 최신 완성본을 다시 조회해 주세요.','completed_steps':[]}
        if "cart_signal_action" in event:
            from agent_system.orchestrator.tools.button_dispatch_r32 import dispatch_cart_signal_button
            from env.model_settings import get_request_config

            def _run_cart_button(action):
                return run_slack_question(
                    question=question,
                    graph_runner=lambda q: run_conversation_turn(
                        question=q,
                        thread_id=_active_id,
                        llm_config=get_request_config().public(),
                        graph=conversation_graph,
                        action=action,
                        metadata={"source": "slack", "slack_channel": event.get("channel", ""),
                                  "slack_user": event.get("user", "")},
                    ),
                )

            result = dispatch_cart_signal_button(event, _run_cart_button)
            LOGGER.info("CART_SIGNAL_BUTTON_DIRECT_ROUTED action=%s base=%s active=%s",
                        str((event.get("cart_signal_action") or {}).get("type")), _base_id, _active_id)
            return result

        _start_payload = _parse_new_analysis(question)
        if _start_payload is not None:
            allowed, reason = _session_switch_allowed(conversation_graph, _active_id)
            if not allowed:
                return {"turn_type": "chitchat", "final_answer": reason, "completed_steps": []}
            _new_id = _session_start(_base_id)
            _topic_reset(_base_id)
            if _session_current(_base_id) != _new_id:
                raise RuntimeError("새 분석 세션 연결 확인 실패")
            _snapshot = conversation_graph.get_state({"configurable": {"thread_id": _new_id}})
            _values = dict((getattr(_snapshot, "values", None) or {})) if _snapshot else {}
            if _values.get("campaign") or _values.get("workflow_run") or _values.get("pending_approval"):
                raise RuntimeError("새 분석 세션에 이전 기획전 상태가 남아 있습니다")
            LOGGER.info("NEW_ANALYSIS_SESSION_CREATED base=%s active=%s", _base_id, _new_id)
            if not _start_payload:
                return {
                    "turn_type": "chitchat",
                    "final_answer": "새 기획전 분석을 시작했습니다. 이전 상품 선택·승인·고객 분석은 이번 분석에 적용되지 않습니다. 기획전 조건을 알려주시면 5개 대분류부터 비교하겠습니다. 기존 분석 이력과 DB는 보존됩니다.",
                    "completed_steps": [],
                }
            question = _start_payload
            _active_id = _new_id
        elif _cancel_command.fullmatch(str(question or "")):
            # 고객 타깃/메일 특수 라우터를 통과시키지 않고 현재 분석 워크플로를 직접 취소한다.
            from env.model_settings import get_request_config
            result = run_slack_question(
                question=question,
                graph_runner=lambda q: run_conversation_turn(
                    question=q,
                    thread_id=_active_id,
                    llm_config=get_request_config().public(),
                    graph=conversation_graph,
                    action=event.get("cart_signal_action"),
                    metadata={"source": "slack", "slack_channel": event.get("channel", ""),
                              "slack_user": event.get("user", "")},
                ),
            )
            LOGGER.info("WORKFLOW_CANCEL_ROUTED base=%s active=%s", _base_id, _active_id)
            return result
        else:
            LOGGER.info("ANALYSIS_SESSION_BOUND base=%s active=%s", _base_id, _active_id)

        # CART_APPROVAL_RECIPIENT_R3: 17단계 실DB 검증 및 자연어 수신자 확인
        from agent_system.approval_demo.recipient_flow import route_recipient_flow
        # 같은 분석 세션의 확정 기획전 값을 읽어 고객 선정에 활용한다.
        # 대화 상태 조회에 실패하면 추측하지 않고 명시적 조건을 요청한다.
        _target_campaign = {}
        try:
            from agent_system.orchestrator.tools.memory import build_slack_thread_id as _target_thread_id
            from agent_system.orchestrator.tools.analysis_sessions import current as _target_current
            _target_session = _target_current(_target_thread_id(event))
            _target_snapshot = conversation_graph.get_state({'configurable': {'thread_id': _target_session}})
            _target_values = (getattr(_target_snapshot, 'values', None) or {}) if _target_snapshot else {}
            _target_campaign = _target_values.get('campaign') or {}
            if not isinstance(_target_campaign, dict):
                _target_campaign = {}
        except Exception:
            _target_campaign = {}
        # R39: 기획전 승인 이후의 타깃/보고서 요청을 레거시 시연 라우터보다 우선한다.
        from agent_system.orchestrator.tools import campaign_target_r39 as target39
        from agent_system.approval_demo import recipient_flow as recipient39
        _target_run = (_target_values.get('workflow_run') or {}) if '_target_values' in locals() else {}
        from agent_system.orchestrator.tools import campaign_page_final_r428 as final428
        from interfaces.slack.target_review_r422 import evidence_request, replay
        prior_messages = (_target_values.get('messages') or []) if '_target_values' in locals() else []
        previous = str(getattr(prior_messages[-1], 'content', '')) if prior_messages else ''
        try:
            final_record428 = final428.read(BASE_DIR,event,_target_run) if _target_run.get('run_id') else None
        except (ValueError,OSError):
            final_record428 = None
        kind428 = final428.intent(question, previous, reviewing=bool(final_record428 and final_record428.get('status')=='review'))
        if kind428:
            return {**final428.handle(kind428,question,BASE_DIR,event,_target_run,_target_campaign),'thread_id':_active_id}
        if evidence_request(question):
            try: return replay(_target_run,_target_campaign,_active_id,event.get('user',''))
            except ValueError as exc: return {'turn_type':'chitchat','final_answer':str(exc),'completed_steps':[]}
        if re.fullmatch(r'(?:최종본(?:으로)?\s*(?:만들어|변환해)|PDF\s*(?:로)?\s*변환해)(?:줘|주세요)[.!]?',str(question).strip(),re.I):
            return {'turn_type':'chitchat','final_answer':'수정한 Word 파일을 첨부해 주세요. 첨부한 파일을 PDF로 변환합니다.','completed_steps':[]}
        from agent_system.orchestrator.tools import audience_scope_r426 as audience426
        if (_target_run.get('workflow') == 'trend_planning' and _target_run.get('status') != 'cancelled'
                and (audience426.age_change_request(question) or audience426.reselection_request(question) or audience426.confirmation_request(question)
                     or (target39.target_request(question) and (_target_run.get('campaign_target_r39') or {}).get('policy') == 'r40_v1'
                         and not audience426.current_target(_target_run.get('campaign_target_r39'), _target_campaign, _target_run)))):
            # 발송 중/결과 불명확 상태를 덮어쓰지 않는다. 원래 스레드와 다른 스레드도 검사.
            try:
                if recipient39.campaign_delivery_busy(BASE_DIR,event,_target_run.get('run_id')):
                    return {'turn_type':'chitchat','final_answer':'이메일 발송 중이거나 결과 확인이 필요합니다. 결과 확인 후 고객 타깃을 변경해 주세요.','completed_steps':[]}
            except (ValueError,OSError):
                return {'turn_type':'chitchat','final_answer':'기존 발송 상태를 확인하지 못해 고객 타깃을 변경하지 않았습니다.','completed_steps':[]}
            from env.model_settings import get_request_config
            return run_slack_question(question=question, graph_runner=lambda q: run_conversation_turn(
                question=q, thread_id=_active_id, llm_config=get_request_config().public(), graph=conversation_graph,
                action=event.get('cart_signal_action'), metadata={'source':'slack','slack_channel':event.get('channel',''),'slack_user':event.get('user','')}))
        if target39.in_campaign(_target_run) or target39.delivery_allowed(_target_run):
            from agent_system.approval_demo.mail_copy_r424 import is_edit
            selected_for_mail=target39.confirmed_for_report(_target_run,_target_campaign)
            if selected_for_mail and is_edit(question) and not re.search(r'고객군|세그먼트|핵심\s*타깃|교차판매|재활성화',str(question)):
                from agent_system.approval_demo.session_r424 import resolve
                from agent_system.approval_demo.delivery_r42 import revise_mail
                try:
                    resolve(BASE_DIR,event,target39.email_summary(_target_run,selected_for_mail))
                    revised=revise_mail(BASE_DIR,event,question)
                    return {'turn_type':'chitchat','final_answer':'메일 검토안을 갱신했습니다.','completed_steps':[],'r424_mail_review':True}
                except ValueError as exc:
                    return {'turn_type':'chitchat','final_answer':str(exc),'completed_steps':[]}
            if recipient39.MAIL_INTENT.search(str(question)) and (recipient39.ROLE_PATTERN.search(str(question)) or '메일' in question or '발송' in question):
                selected39 = target39.confirmed_for_report(_target_run, _target_campaign)
                from agent_system.orchestrator.tools.campaign_copy_r41 import approved as copy41_approved
                if selected39 and selected39.get('policy') == 'r40_v1' and not copy41_approved(_target_run, _target_campaign):
                    return {'turn_type':'chitchat','final_answer':'고객군별 문구를 승인하고 새 보고서를 생성해 주세요. 이메일은 발송하지 않았습니다.','completed_steps':[]}
                if selected39:
                    from agent_system.approval_demo.session_r424 import resolve
                    try: resolve(BASE_DIR,event,target39.email_summary(_target_run,selected39))
                    except ValueError as exc: return {'turn_type':'chitchat','final_answer':str(exc),'completed_steps':[]}
                if not selected39 or not recipient39.matches_campaign_report(BASE_DIR, event, target39.email_summary(_target_run, selected39)):
                    return {'turn_type':'chitchat', 'final_answer':'현재 기획전의 Word 보고서를 만든 스레드에서 수정본을 PDF로 변환한 뒤 사내 보고 대상을 지정해 주세요. 고객에게 직접 발송하지 않습니다.', 'completed_steps':[]}
                try: final428.sync_delivery(BASE_DIR,event,_target_run,_target_campaign)
                except ValueError as exc: return {'turn_type':'chitchat','final_answer':str(exc),'completed_steps':[]}
                recipient_result = recipient39.start_recipient_confirmation(question, event, BASE_DIR)
                if recipient_result is not None:
                    return recipient_result
            from agent_system.orchestrator.tools.target_start_r38 import target_start_intent
            from agent_system.orchestrator.tools.campaign_page import is_page_preview_replay_request
            from agent_system.orchestrator.tools.report import detect
            try:
                size_request = target39.parse_k(question) is not None
            except ValueError:
                size_request = True
            from agent_system.orchestrator.tools.campaign_copy_r41 import is_request as segment_copy_request
            if (segment_copy_request(question) or target39.target_request(question) or (target39.active(_target_run) and target_start_intent(question)) or size_request
                    or target39.target_result_request(question) or is_page_preview_replay_request(question) or detect(question)):
                from env.model_settings import get_request_config
                return run_slack_question(question=question, graph_runner=lambda q: run_conversation_turn(
                    question=q, thread_id=_active_id, llm_config=get_request_config().public(), graph=conversation_graph,
                    action=event.get('cart_signal_action'), metadata={'source':'slack','slack_channel':event.get('channel',''),'slack_user':event.get('user','')}))
            # 활성 기획전의 요청은 구형 고객 분류를 새로 실행하지 않는다.
            recipient_result = None
        else:
            # 새 분석에서 이전 기획전의 스레드 상태를 통해 발송하지 않는다.
            if recipient39.MAIL_INTENT.search(str(question)) and recipient39._read_state(BASE_DIR,event).get('target',{}).get('kind')=='r39':
                return {'turn_type':'chitchat','final_answer':'현재 기획전의 보고서를 먼저 생성해 주세요. 이전 기획전 파일은 재사용하지 않습니다.','completed_steps':[]}
            recipient_result = route_recipient_flow(question, event, BASE_DIR, campaign=_target_campaign)
        if recipient_result is not None:
            return recipient_result

        # CART_APPROVAL_SLACK_DEMO_V1: 명시적 승인시연 명령만 별도 처리한다.
        # 고객 이메일 전송 기능은 추가하지 않는다.
        from agent_system.approval_demo.slack_flow import run_approval_demo_command
        approval_result = run_approval_demo_command(question, event, repo_root=BASE_DIR)
        if approval_result is not None:
            return approval_result

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


    def resolve_report_event(event):
        from agent_system.orchestrator.tools.analysis_sessions import current
        from agent_system.orchestrator.tools import campaign_target_r39 as target
        from agent_system.approval_demo.session_r424 import resolve
        event.setdefault('channel_type','im' if str(event.get('channel','')).startswith('D') else 'channel')
        snapshot=conversation_graph.get_state({'configurable':{'thread_id':current(build_slack_thread_id(event))}})
        vals=getattr(snapshot,'values',{}) or {}
        run=vals.get('workflow_run') or {}
        if run.get('workflow')!='trend_planning':
            from agent_system.approval_demo.recipient_flow import _read_state
            if _read_state(BASE_DIR,event).get('target',{}).get('kind')=='r39':
                raise ValueError('이전 기획전의 파일 상태는 현재 분석에서 사용할 수 없습니다.')
            return event  # 일반 보고서 PDF 변환 호환
        selected=target.confirmed_for_report(run,vals.get('campaign') or {})
        if not target.delivery_allowed(run) or not selected: raise ValueError('현재 기획전 고객 타깃 승인을 먼저 완료해 주세요.')
        return resolve(BASE_DIR,event,target.email_summary(run,selected))
    bridge.resolve_report_event=resolve_report_event

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

                # file_shared의 이벤트 시간은 메시지/스레드 시간이 아니다.
                # shares에서 실제 공유 메시지가 하나로 식별될 때만 보조 이벤트를 처리한다.
                records = [r for entries in (info.get('shares') or {}).values() for r in entries.get(channel, []) if r.get('ts')]
                if len(records) != 1 or info.get('bot_id'):
                    return
                shared = records[0]
                adapted_event = {
                    "type": "message",
                    "subtype": "file_share",
                    "channel": channel,
                    "user": event.get("user_id") or info.get("user"),
                    "ts": shared["ts"],
                    "thread_ts": shared.get("thread_ts") or shared["ts"],
                    "files": [info],
                }

                # 원본 메시지 전체를 가져와 동시 첨부 및 요청 문구를 보존한다.
                try:
                    response = client.conversations_replies(channel=channel, ts=adapted_event['thread_ts'], limit=100)
                    original = next((m for m in response.get('messages',[]) if m.get('ts')==shared['ts']),None)
                    if not original or not original.get('files'):
                        return  # message/app_mention 이벤트가 처리한다.
                    adapted_event.update(text=original.get('text',''),files=original['files'])
                except Exception:
                    return
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

            if bridge.receive_report_file(event, body, client):
                return
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

        @app.action(re.compile("^cartrecipient_"))
        def handle_recipient_button(ack, body, client):
            ack()
            # 이메일 작업은 Slack 3초 응답 제한 밖에서 워커로 처리
            def _job():
                import json as _json
                from agent_system.approval_demo.recipient_flow import confirm_recipient, _thread
                try:
                    action = (body.get('actions') or [{}])[0]
                    value = _json.loads(action.get('value') or '{}')
                    user = (body.get('user') or {}).get('id')
                    channel = (body.get('channel') or {}).get('id') or (body.get('container') or {}).get('channel_id')
                    evt = {'user': user, 'channel': channel, 'thread_ts': value.get('thread_ts'), '_r424_state_key': value.get('thread')}
                    if (value.get('owner') != user or value.get('channel') != channel or
                            value.get('thread') != _thread(evt) or
                            action.get('action_id') not in ('cartrecipient_yes', 'cartrecipient_no','cartrecipient_send','cartrecipient_cancel','cartrecipient_pick_MANAGER','cartrecipient_pick_TEAM_LEADER','cartrecipient_pick_ASSISTANT_MANAGER','cartrecipient_pick_STAFF')):
                        message = '다른 사용자 또는 다른 스레드의 버튼입니다. 이메일은 전송하지 않았습니다.'
                    else:
                        from agent_system.approval_demo.recipient_flow import _read_state
                        from agent_system.orchestrator.tools import campaign_target_r39 as target39
                        stored = _read_state(BASE_DIR, evt)
                        saved = stored.get('target') or {}
                        if saved.get('kind')=='r39' and (saved.get('owner') != user or saved.get('channel',channel)!=channel):
                            return
                        if stored.get('archived'):
                            client.chat_postMessage(channel=channel,thread_ts=value.get('thread_ts'),text='이전 기획전 요청입니다. 이메일은 전송하지 않았습니다.')
                            return
                        if saved.get('kind') == 'r39':
                            from agent_system.orchestrator.tools.memory import build_slack_thread_id
                            from agent_system.orchestrator.tools.analysis_sessions import current
                            evt['channel_type'] = 'im' if str(channel).startswith('D') else 'channel'
                            snapshot = conversation_graph.get_state({'configurable': {'thread_id': current(build_slack_thread_id(evt))}})
                            values = getattr(snapshot, 'values', {}) or {}
                            run = values.get('workflow_run') or {}
                            selected = target39.confirmed_for_report(run, values.get('campaign') or {}) if target39.delivery_allowed(run) else None
                            from agent_system.orchestrator.tools.campaign_copy_r41 import approved as copy41_approved
                            if selected and selected.get('policy') == 'r40_v1' and (not copy41_approved(run, values.get('campaign') or {}) or saved.get('segment_copy_hash') != target39.email_summary(run, selected).get('segment_copy_hash')):
                                client.chat_postMessage(channel=channel, thread_ts=value.get('thread_ts'), text='승인된 고객군별 문구와 보고서가 일치하지 않습니다. 이메일은 전송하지 않았습니다.')
                                return
                            if not selected or saved.get('run_id') != run.get('run_id') or saved.get('fingerprint') != selected['fingerprint']:
                                client.chat_postMessage(channel=channel, thread_ts=value.get('thread_ts'), text='현재 기획전과 일치하지 않는 보고 요청입니다. 이메일은 전송하지 않았습니다.')
                                return
                        if saved.get('kind')=='r39' and action.get('action_id')=='cartrecipient_send':
                            from agent_system.orchestrator.tools.campaign_page_final_r428 import sync_delivery
                            try: sync_delivery(BASE_DIR,evt,run,values.get('campaign') or {},sending=True)
                            except ValueError as exc:
                                client.chat_postMessage(channel=channel,thread_ts=value.get('thread_ts'),text=str(exc))
                                return
                        from agent_system.approval_demo.delivery_r42 import ROLE_KEYS, prepare_send, post_buttons
                        action_id = action['action_id']
                        if action_id.startswith('cartrecipient_pick_'):
                            from datetime import datetime, timezone, timedelta
                            choice = _read_state(BASE_DIR,evt).get('recipient_choice') or {}
                            if choice.get('nonce') != value.get('nonce') or choice.get('owner') != user or datetime.now(timezone.utc)-datetime.fromisoformat(choice['created_at']) > timedelta(minutes=15):
                                client.chat_postMessage(channel=channel,thread_ts=value.get('thread_ts'),text='이전 담당자 선택 버튼입니다. 다시 요청해 주세요.')
                                return
                            from agent_system.approval_demo.recipient_flow import start_recipient_confirmation
                            role = next(k for k,v in ROLE_KEYS.items() if v == action_id.removeprefix('cartrecipient_pick_'))
                            result = start_recipient_confirmation(role+'에게 보내줘',evt,BASE_DIR,choice_nonce=value.get('nonce'))
                            client.chat_postMessage(channel=channel,thread_ts=value.get('thread_ts'),text=result['final_answer'])
                            if result.get('recipient_confirmation'):
                                post_buttons(client,evt,_read_state(BASE_DIR,evt)['pending'])
                            return
                        prior = _read_state(BASE_DIR,evt).get('pending') or {}
                        if action_id=='cartrecipient_send' and prior.get('nonce')==value.get('nonce') and prior.get('status')=='AWAITING_SEND':
                            try: client.chat_update(channel=channel,ts=(body.get('message') or {}).get('ts'),text='이메일 발송 처리 중',blocks=[])
                            except Exception: pass
                        message = confirm_recipient(BASE_DIR, evt, value.get('nonce'),
                                                    action_id in ('cartrecipient_yes','cartrecipient_send'), final=action_id in ('cartrecipient_send','cartrecipient_cancel'))
                        if action_id == 'cartrecipient_no' and prior.get('nonce') == value.get('nonce') and (_read_state(BASE_DIR,evt).get('pending') or {}).get('status') == 'CANCELLED':
                            post_buttons(client,evt,choose=True)
                        if action_id == 'cartrecipient_yes' and prior.get('nonce') == value.get('nonce') and prior.get('status') == 'AWAITING_CONFIRMATION':
                            pending42 = prepare_send(BASE_DIR,evt)
                            if pending42:
                                post_buttons(client,evt,pending42)
                        if action_id=='cartrecipient_send' and prior.get('nonce')==value.get('nonce') and prior.get('status')=='AWAITING_SEND':
                            after=(_read_state(BASE_DIR,evt).get('pending') or {}).get('status')
                            try: client.chat_update(channel=channel,ts=(body.get('message') or {}).get('ts'),text='이메일 발송 완료 · PDF + PNG' if after=='SENT' else message,blocks=[])
                            except Exception: pass
                        if saved.get('kind') == 'r39' and action_id in ('cartrecipient_yes','cartrecipient_send'):
                            # 실제 발송 성공 기록과 현재 Run을 재검사한 뒤 업무를 종료한다.
                            try:
                                config39 = {'configurable': {'thread_id': current(build_slack_thread_id(evt))}}
                                latest39 = conversation_graph.get_state(config39)
                                vals39 = getattr(latest39, 'values', {}) or {}
                                finished39 = target39.complete_after_report(vals39.get('workflow_run') or {}, vals39.get('campaign') or {}, _read_state(BASE_DIR, evt), value.get('nonce'))
                                if finished39 and not vals39.get('pending_approval'):
                                    conversation_graph.update_state(config39, {'workflow_run': finished39, 'turn_type': 'chitchat'}, as_node='orchestrator')
                            except Exception:
                                LOGGER.exception('R39_REPORT_COMPLETION_FAILED')
                                message += '\n발송은 완료됐지만 워크플로 종료 상태를 저장하지 못했습니다.'
                    client.chat_postMessage(channel=channel, thread_ts=value.get('thread_ts'), text=message)
                except Exception as exc:
                    LOGGER.exception('승인 수신자 처리 실패: %s', type(exc).__name__)
            bridge.executor.submit(_job)

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
