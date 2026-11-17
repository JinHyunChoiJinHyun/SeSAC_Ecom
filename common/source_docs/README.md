# 새 데이터 출처 추가 방법

CART SIGNAL의 Agent들은 `common/data_sources.py`의 등록부(`DATA_SOURCES`)와
이 폴더(`common/source_docs/<key>/`)의 설명 문서를 읽어 동작합니다.
새 데이터를 붙일 때 Agent 코드(nodes.py, 각 workflow)는 고치지 않습니다.

## 1. 데이터를 DuckDB 파일로 적재

- 출처마다 **별도 파일**로 만듭니다. 예: `database/musinsa_data.duckdb`
- 파일 안에 출처 key와 같은 이름의 schema를 만들고 테이블을 넣습니다.
  예: `CREATE SCHEMA musinsa; CREATE TABLE musinsa.ranking AS ...`
- 봇은 이 파일을 **읽기 전용**으로만 엽니다. 적재·갱신은 별도 수집 스크립트가 합니다.
- 수집 스크립트가 파일을 쓰기로 열고 있는 동안에는 봇이 그 파일을 열 수 없습니다.
  선택 출처(required=False)는 그 조회에서만 빠지고 다른 출처 조회는 계속됩니다.
  그 출처를 묻는 질문에는 "잠시 후 다시 시도" 안내가 나갑니다.
  빠지는 시간을 없애려면 임시 파일(`musinsa_data.duckdb.tmp`)에 다 쓴 뒤
  `os.replace()`로 바꿔치기하십시오.
- 조회수·랭킹처럼 계속 바뀌는 값은 덮어쓰지 말고 `collected_at`(수집 시각) 컬럼을 두고 쌓으십시오.

## 2. 등록부에 항목 추가 (`common/data_sources.py`)

`DATA_SOURCES`에 `DataSource(...)` 하나를 추가합니다. 파일에 주석으로 된 무신사 예시가 있습니다.

| 항목 | 의미 |
|---|---|
| `key` | SQL schema 이름 (예: `musinsa` → `musinsa.ranking`) |
| `label`, `display`, `capability` | 답변·대화 Agent에 보이는 이름과 설명 |
| `default_path`, `path_env`, `catalog` | DB 파일 위치, 경로 환경변수, ATTACH 별칭 |
| `required` | `True`면 파일이 없을 때 봇이 멈춤. 보조 데이터는 `False` |
| `answer_notes` | 이 출처를 쓴 답변에 붙일 주의 문구 (예: "수집 시점 스냅샷입니다") |
| `example_questions` | 안내 문구에 보일 질문 예시 |
| `id_columns` | 숫자여도 지표가 아닌 ID·코드 컬럼 (상관계수·산점도에서 제외) |
| `shorten_id_columns` | Slack 표에서 줄여 보일 긴 ID |
| `timestamp_columns`, `entity_columns`, `repeat_group_presets` | 전처리 자동 선택 기준 |
| `column_labels`, `decimal_metrics`, `column_priority` | Slack 표 한글 라벨·소수 표시·컬럼 순서 |
| `category_hint_columns` | 0행 결과 때 실제 값 목록을 보여 줄 범주형 컬럼 |

`required=False` 출처는 DB 파일이 없으면 자동으로 빠집니다
(연결·SQL 허용 범위·Agent 프롬프트 모두). 파일을 넣으면 봇 재시작 후 바로 쓰입니다.

## 3. Agent용 설명 문서 작성 (`common/source_docs/<key>/`)

| 파일 | 읽는 Agent | 내용 |
|---|---|---|
| `data.md` (필수) | Data Agent | 테이블·컬럼·행 수, 실제 값 목록, 주의점, 예시 SQL |
| `prep.md` (선택) | Prep Agent | 결측·이상값 등 데이터 검증 결과 |
| `visualization.md` (선택) | Visualization Agent | 표현 주의사항 (예: 판매량 아님) |

`hm/`, `amazon/` 문서를 참고해 같은 형식으로 쓰면 됩니다.
특히 WHERE 조건에 쓰일 범주형 컬럼의 **실제 값 목록**을 적어 두어야 0행 결과가 줄어듭니다.

## 4. 확인

```bash
python tests/data_source_registry_test.py   # 등록부 동작 테스트 (가짜 출처로 확인)
python tests/e2e_stub_test.py               # 기존 분석 흐름 회귀 테스트
```
