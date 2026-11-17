# CART SIGNAL 백엔드 (FastAPI + PostgreSQL Docker) ↔ 에이전트 v15 연결

팀 백엔드(Docker PostgreSQL 운영 DB + FastAPI 헬스 체크)에 에이전트(Orchestrator 멀티턴)를 연결했습니다.
Slack 봇과 **같은 Orchestrator 그래프·대화 기억(LangGraph Checkpointer)**을 사용합니다.

```
클라이언트 ──HTTP──▶ FastAPI backend/main.py
                       ├ /health, /health/db        팀 기존 (PostgreSQL 연결 확인)
                       ├ /health/agent              분석 DB(DuckDB)·켜진 Agent·대화 기억 위치
                       └ /chat, /conversations, /files
                              │ backend/services/chat_service.py
                              ▼
                     agent_system/orchestrator/graph.py run_turn()
                     Orchestrator → General Analysis (H&M·Amazon DuckDB)
                     대화 기억: runtime/conversation_memory.sqlite (thread_id별)
운영 DB: PostgreSQL (docker compose) ← database/operations/connection.py (SQLAlchemy)
```

## 처음 실행

```bash
pip install -r requirements.txt
cp .env.example .env               # DB_USER / DB_PASSWORD / DB_NAME / DB_PORT(5433) 입력
docker compose up -d               # PostgreSQL 컨테이너 (cartsignal_postgres)
uvicorn backend.main:app --reload  # http://127.0.0.1:8000/docs
```

OpenAI·Slack 키와 모델 설정은 기존처럼 `env/.env`에 둡니다. DB 정보는 루트 `.env`(docker compose가 읽음)에 두며,
`env/.env`에 넣어도 `connection.py`가 읽습니다.

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | API 응답 확인 (팀 기존) |
| GET | `/health/db` | PostgreSQL 연결 확인 (팀 기존) |
| GET | `/health/agent` | DuckDB 출처 파일, 켜진 특화 Agent, 대화 기억 파일 |
| POST | `/chat` | 대화 한 턴 `{"message": "...", "user_id": "dongyeon", "thread_id": null}` |
| GET | `/conversations/{thread_id}` | 저장된 대화 기억 |
| DELETE | `/conversations/{thread_id}` | 대화 기억 삭제 (새 대화로 시작) |
| GET | `/files/{png\|csv}/{file_name}` | 결과 그래프·CSV 내려받기 |

### 멀티턴
- `thread_id`를 비우면 `api:<user_id>` — 같은 `user_id`로 보내면 이전 대화를 이어 갑니다.
- Slack 대화를 이어 보려면 `thread_id`에 Slack 규칙 그대로 (`dm:<Slack 사용자 ID>`, `ch:<채널>:<사용자>`).

```bash
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "H&M 여성 상품 유형별 구매 건수 Top 10", "user_id": "dongyeon"}'
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "남성은?", "user_id": "dongyeon"}'          # 이전 대화 반영
```

응답: `answer`, `turn_type`(chitchat/analysis/approval_request/approval), `route`, `standalone_question`, `is_followup`, `sql`,
`table`(최대 100행), `files`(png·csv 내려받기 URL), `error`, `awaiting_approval`(승인 대기 중), `campaign`(승인 처리 후 확정값)

승인 흐름: "카테고리는 니트로 확정" → `turn_type=approval_request`, `awaiting_approval=true` →
같은 user_id로 "승인" → `turn_type=approval`, `campaign.values.category="니트"`

## 테스트

```bash
pytest -v                     # 백엔드 연결 7개 + PostgreSQL 연결 1개 (.env 없으면 건너뜀)
pytest -m db -v               # PostgreSQL 연결만
python tests/multiturn_stub_test.py   # 에이전트 멀티턴 (기존 스크립트, pytest 대상 아님)
```

## 주의
- 그래프(matplotlib)가 스레드 안전하지 않아 한 번에 1턴만 처리합니다 (`CART_SIGNAL_MAX_CONCURRENT`, 기본 1).
- Slack 봇과 백엔드를 동시에 띄우면 두 프로세스가 같은 대화 기억 파일(SQLite)을 씁니다.
  같은 thread_id에 동시에 요청이 들어오지 않게 운영하세요.
- 운영 DB(PostgreSQL)는 지금 연결 확인만 합니다. 대화 기억을 PostgreSQL로 옮기려면
  `langgraph-checkpoint-postgres`의 `PostgresSaver`로 `agent_system/orchestrator/tools/memory.py`의 Checkpointer만 바꾸면 됩니다.
- 팀 zip의 옛 에이전트 코드(Synerise·예측 모델·supervisor), `db_setup.py`, `database/sql/`(Synerise SQL)은 v15에서 쓰지 않아 넣지 않았습니다.

## API 보안 설정 (인증 적용 후)

서버 시작 전 **서버 환경변수**를 설정합니다. 아래 값은 설명용이며 실제 운영에서는 무작위로 생성한 32자 이상의 서로 다른 비밀값을 사용하세요. 비밀값을 Git에 커밋하지 마세요.

```bash
# 예시 형식 (실제 키는 별도로 생성)
export CART_SIGNAL_API_KEYS='{"alice":"REPLACE_WITH_RANDOM_KEY_AT_LEAST_32_CHARS"}'
export CART_SIGNAL_FILE_SIGNING_KEY='REPLACE_WITH_SEPARATE_RANDOM_SECRET_AT_LEAST_32_CHARS'
```

`POST /chat`, `GET/DELETE /conversations/{thread_id}`, `GET /files/...` 요청에
`X-API-Key: <발급된 키>` 헤더가 필요합니다. 키 누락/오류는 401, 인증 설정 누락은 503으로 거절합니다.
`POST /chat` 본문에서는 `user_id`를 보내지 마세요. 서버가 키를 검증한 후 사용자를 결정합니다.
`thread_id` 생략 시 인증 사용자별 API 전용 스레드를 사용하며, 응답의 `thread_id`로 후속 요청할 수 있습니다.
이전 버전의 `api:<user_id>` 스레드 및 Slack `dm:`/`ch:` 스레드는 API에서 이어받을 수 없습니다.
파일 URL은 1시간 유효한 서명 URL이며 **같은 인증 헤더**가 필요합니다. 결과 파일은 `runtime/api_downloads/` 아래에 고유한 이름으로 보관합니다.
이 폴더는 웹 서버의 정적 파일 공개 경로로 마운트하지 마세요. 파일 만료 시 별도 정리 정책이 필요합니다.
