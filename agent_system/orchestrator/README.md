# Orchestrator — 멀티턴 대화 관리 + 업무 분기

11/16 회의 결정: **Orchestrator 1개 + 전문 Agent 7개**. 이전의 Conversation Agent는 없애고,
그 기능(대화 기억·일상 대화·후속 질문 재작성)을 Orchestrator의 Tool로 옮겼습니다.

## 구조

```
Slack 메시지
  → Orchestrator (agent_system/orchestrator/orchestrator.py)
       ① 대화 맥락 불러오기   LangGraph Checkpointer가 thread_id로 이전 상태를 넣어 줌
       ② 기억 정리            오래된 메시지 정리, 12시간 지나면 자동 리셋          tools/context.py
       ③ 대화 판단 (LLM)      일상 대화 → 바로 답변 / 업무 요청 → 후속 질문 재작성   tools/intent.py
       ④ 확정값 결정이면      승인 요청 → check_approval(interrupt)              tools/approval.py
       ⑤ 라우팅               처리할 특화 Agent 선택                              tools/routing.py
       ⑥ 다음 노드            조건부 엣지 (Agent / check_approval / END)          tools/workflow.py
  → 특화 Agent (registry.py에서 enabled=True)
       general_analysis  자유 데이터 질문 (SQL·전처리·시각화)  ← 켜짐 (기본 Agent)
       market_analysis   시장 관심도·외부 리뷰 분석            ← 켜짐 (agent_system/agents/market_analysis_agent.py)
       product_analysis  H&M 상품 검색·키워드 매칭·판매 비교   ← 켜짐 (agent_system/agents/product_analysis_agent.py)
       targeting · content_generation ·
       validation · reporting · campaign_execution             ← 코드 있음(agent_system/agents/), 데이터·모델·백엔드
                                                                  연결 전이라 enabled=False · Tool은 '미구현' 반환
  → Slack Delivery (답변·표·PNG·CSV) + Checkpointer가 새 상태 저장
```

## 멀티턴 방식: StateGraph + Checkpointer + thread_id

| 구성 | 위치 | 하는 일 |
|---|---|---|
| `StateGraph(OrchestratorState)` | `graph.py` | 대화 상태(messages, last_analysis, last_activity_at …)와 노드 정의 |
| Checkpointer (`SqliteSaver`) | `tools/memory.py` `get_checkpointer()` | `runtime/conversation_memory.sqlite`에 thread별 상태 저장. 봇을 재시작해도 이어짐 |
| `thread_id` | `tools/memory.py` `build_slack_thread_id()` | DM `dm:<사용자>`, 채널 `ch:<채널>:<사용자>` (설정으로 스레드 단위 가능) |
| 실행 | `graph.py` `run_turn()` | `graph.invoke({"messages": [이번 질문]}, config={"configurable": {"thread_id": ...}})` |

이번 메시지만 넘기면 LangGraph가 같은 `thread_id`의 이전 상태를 불러와 합치고, 실행이 끝나면 저장합니다.
별도의 대화 저장 코드는 없습니다. (v12까지의 Slack 멀티턴도 같은 방식이었고, 이번에 Orchestrator 아래로 정리했습니다.)

## Orchestrator Tool (멘토 조언 5개 기능)

| 기능 | 방식 | 파일 | 하는 일 |
|---|---|---|---|
| `route_request` | LLM Router | `tools/intent.py`, `tools/routing.py` | 의도 판단(일상 대화 / 업무 요청 / 확정값 결정), 후속 질문 재작성, 담당 Agent 선택 |
| `manage_context` | State | `tools/context.py` `manage_context()` | 이전 대화 · 직전 분석 · **확정된 상품·타깃·일정(campaign)** · 승인 대기를 대화 판단에 넣고, Agent에도 `request.campaign`으로 전달 |
| `update_conversation_state` | State 업데이트 | `tools/conversation_state.py` | 확정값을 `campaign`에 반영(변경 기록 포함). 사용자 결정은 승인 후, Agent의 `decisions`는 바로 반영 |
| `check_approval` | LangGraph `interrupt` | `tools/approval.py` | 승인 질문을 보내고 그래프를 멈춤 → 다음 메시지가 승인/반려/수정/취소면 `Command(resume=...)`로 이어서 실행 |
| `manage_workflow` | Conditional Edge | `tools/workflow.py` | orchestrator·Agent 다음 노드 결정 (Agent / check_approval / END). 단계별 다음 Agent 규칙 자리 `STAGE_NEXT_AGENT` |
| (기억) | Checkpointer | `tools/memory.py` | SQLite Checkpointer, Slack thread_id 규칙, 이전 저장본 정리 |

### 승인 흐름 예시 (Slack)

```
사용자: 카테고리는 니트로 확정할게
봇    : [승인 요청] 아래 내용을 기획전 확정값으로 저장할까요? - 카테고리: 니트   ← interrupt (그래프 멈춤)
사용자: 1위가 뭐였지?                  ← 승인과 무관 → 평소처럼 답하고 승인 질문 다시 붙임 (계속 대기)
사용자: 승인                           ← Command(resume) → campaign.values.category = "니트"
사용자: 확정한 카테고리 월별 추이 보여줘 ← manage_context: "니트 월별 …"로 재작성해 General Analysis 실행
```

### 승인 대기 중 받은 답의 처리 (`tools/approval.py`, 규칙 판단 · LLM 없음)

| 답 | 처리 |
|---|---|
| 승인 · 네 · 좋아 | 확정값 저장 |
| 반려 · 아니 | 저장하지 않음 |
| "카디건으로 수정" | 수정 요청을 기록하고, 다시 확정해 달라고 안내 |
| 취소 | 승인 요청 취소 |
| 다른 질문 ("1위가 뭐였지?") | 평소처럼 답한 뒤, 승인 질문을 다시 붙이고 계속 대기 |

특화 Agent가 결과에 넣을 수 있는 값: `decisions`(바로 저장) · `approval_request`(`{"title","question","updates"}`, 승인 후 저장)

## 워크플로 (agent_system/workflows/)

아키텍처 원칙 5(혼합형 오케스트레이션): 대화 판단이 "어떤 워크플로인가"까지만 정하고, 단계 순서·마케터 선택·승인은 워크플로가 고정합니다.

| 워크플로 | 파일 | 단계 |
|---|---|---|
| `trend_planning` 트렌드 기반 기획전 | `workflows/trend_planning/` | s1 주력 상품 선정(2~10) → s2 페이지 틀(11~16) → s3 타깃·발송(17~22) → s4 보고·승인(23~29) |
| `photography_planning` 촬영·콘텐츠 기획 | `workflows/photography_planning.py` | 메인 주력 상품 확정 후 시작, 4단계 |

- 진행: `orchestrator` → `workflow` 노드(`tools/workflow.py run_workflow_node`)가 단계 순서대로 전문 Agent 실행
- 마케터 선택 단계: 질문을 남기고 멈춤 → 마케터가 값을 말하면 확정값 승인(check_approval) → 승인되면 다음 단계 자동 진행
- 담당 Agent 구현 전: 시각화 같은 선택 단계(optional)는 건너뛰고, 필수 단계는 멈춤 → 구현 후 "워크플로 계속"
- 대기 중 다른 질문: 평소처럼 답하고 끝에 현재 단계 안내 (같은 단계로 복귀)
- 명령: "워크플로 상태" · "워크플로 계속"(멈춘 단계 다시 시도) · "워크플로 취소"(확정값은 유지)
- run 상태는 지금 LangGraph state(`workflow_run`)에 저장. 백엔드 run API가 생기면 `workflows/base.py record_run_event()`에서 전송
- 끄기: `settings.ENABLE_WORKFLOWS = False`

## 보고서 (PDF + Word 2부)

| 요청 | 보고서 | 내용 |
|---|---|---|
| "보고서 만들어줘", "분석 보고서로 정리해줘" | 일반 분석 보고서 | 대화 중 분석 결과(최근 10건): 질문·답변·표(앞 20행)·그래프·분석 기준(SQL) |
| "기획전 보고서 만들어줘", "실행 계획 보고서 작성해줘" | 기획전 실행 계획 보고서 | 확정값, 워크플로 단계별 상태·결과, 남은 단계 (백엔드 연결 전에는 '초안') |

- 규칙으로 판단(LLM 없음)하고 `tools/report.py` → `common_tools/report_export/`(문서 구조·PDF·Word)로 만듭니다.
- 재료: Agent·워크플로 단계가 끝날 때마다 `analysis_log`에 기록 (표는 `runtime/report_data/`). 실패·미구현 결과는 기록 안 함.
- 파일은 `delivery_output/reports/`에 저장하고 Slack에 첨부합니다 (`result["attachments"]`).
- 기록한 결과만 옮기며 새 숫자를 만들지 않습니다. 12시간 자동 리셋 때 기록도 비웁니다.
- 필요 패키지: `reportlab`, `python-docx`. PDF 한글 글꼴은 AppleGothic·나눔고딕·맑은 고딕 순으로 찾고, `CART_SIGNAL_REPORT_FONT`로 지정 가능.
- ⑥ 결과 정리 Agent(백엔드 `/reports/campaign`)가 연결되면 기획전 보고서는 그 결과를 같은 문서 구조로 받아 같은 출력기로 내보냅니다.

## 라우팅 판단 순서

1. 켜진 Agent가 1개면 그 Agent (LLM 호출 없음) — 지금은 3개(general_analysis·market_analysis·product_analysis)라 3번 LLM 판단 사용
2. 후속 질문이고 직전 분석을 처리한 Agent가 있으면 같은 Agent (`KEEP_ROUTE_ON_FOLLOWUP = True`)
3. 그 밖에는 LLM이 Agent의 `description`·`examples`를 보고 선택
4. LLM 실패·등록되지 않은 이름 → 기본 Agent(`general_analysis`)

## 전문 Agent 추가 방법

1. `agent_system/agents/<이름>_agent.py`에 `run(request) -> dict` 작성 (Tool은 `agent_system/tools/<이름>_tools.py`,
   계산은 `shared_analysis/<영역>/`, 프롬프트는 `agent_system/prompts/` — 아키텍처 구조)
   - 입력 `AgentRequest`: `question`(다시 쓴 질문), `user_question`, `is_followup`, `last_analysis`, `llm_config`
   - 출력: `final_answer` 필수, 선택 `dataframe`, `visualization_path`, `visualization_result`, `sql_query`,
     `data_sources`, `data_purpose`, `data_scope_note`, `error` (`registry.py` 위쪽 주석 참고)
   - **Tool 호출 로그 필수**: Tool은 `agent_system/common_tools/tool_call_log.py`의 `run_logged_tool()`로 실행하고,
     끝에 `log_tool_summary()`를 부릅니다 (`[Tool 호출]`·`[Tool 결과]`·`[Tool 요약]` 로그가 logs/app.log에 남음)
2. `registry.py`에서 해당 Agent의 `enabled=False` → `True`
3. 봇 재시작 → 라우팅 프롬프트·그래프 노드·대화 판단 안내에 자동 반영

## 확인

```bash
python tests/multiturn_stub_test.py     # 멀티턴·기억 유지·재시작 후 이어짐·대화 분리 (51)
python tests/orchestrator_stub_test.py  # 라우팅·후속 질문·실패 대비·Agent 등록 (20)
python tests/orchestrator_flow_test.py  # 5개 기능: 확정값·승인(interrupt)·재시작 후 승인·조건부 엣지 (36)
python multiturn_check.py --stub        # Slack 없이 시나리오 실행 (실제 DB)
python print_langgraph.py               # langgraph_output/orchestrator_graph.png
python show_conversations.py            # 저장된 대화 기억 조회
```
