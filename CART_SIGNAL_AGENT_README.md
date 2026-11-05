# CART SIGNAL — 현재 구현된 Agent 구조

CART SIGNAL 프로젝트에서 **현재 실제 코드로 구현되어 있는 Agent와 실행 구조**를 정리한 문서입니다.

> 이 문서는 현재 구현 상태만 설명합니다.  
> `Top Supervisor Agent`, `Product Growth Agent`, `CRM Targeting Agent`, `Campaign Strategy Agent`는 아직 구현 전이므로 본문에서 다루지 않습니다.

---

## 1. 현재 Agent 구조

현재 시스템의 중심은 `Analysis Agent`이며, 내부 Supervisor가 사용자의 요청을 분석해 필요한 전문 Agent를 순서대로 호출합니다.

```text
사용자
  │
  ├─ CLI (main.py)
  └─ Slack (slack_bot.py)
          │
          ▼
Analysis Supervisor
          │
          ├─ Data Agent
          │    └─ SQL Executor
          │
          ├─ Prep Agent
          │    └─ Prep Executor
          │
          ├─ Model Agent
          │    └─ Model Tools
          │
          ├─ Visualization Agent
          │    └─ Visualization Tools
          │
          └─ Experiment Agent
               └─ 현재 미구현
          │
          ▼
Final Response
          │
          ▼
Delivery
  ├─ Slack 메시지
  ├─ CSV 첨부
  └─ PNG 첨부
```

현재 Analysis Agent는 LangGraph `StateGraph` 기반으로 구성되어 있습니다.

---

## 2. 폴더 구조

```text
WORKS/
├── agent_system/
│   ├── analysis_agent/
│   │   ├── supervisor.py
│   │   ├── data_agent.py
│   │   ├── prep_agent.py
│   │   ├── model_agent.py
│   │   ├── visualization_agent.py
│   │   ├── experiment_agent.py
│   │   ├── state.py
│   │   ├── nodes.py
│   │   └── graph.py
│   │
│   ├── tools/
│   │   ├── data_tools.py
│   │   ├── prep_tools.py
│   │   ├── model_tools.py
│   │   ├── visualization_tools.py
│   │   ├── experiment_tools.py
│   │   └── delivery_tools.py
│   │
│   └── delivery/
│       └── delivery_agent.py
│
├── modeling/
├── database/
├── visualization_output/
├── delivery_output/
├── logs/
├── env/
├── main.py
└── slack_bot.py
```

---

## 3. Analysis Supervisor

**파일**

```text
agent_system/analysis_agent/supervisor.py
```

### 역할

Analysis Supervisor는 현재 분석 시스템 내부의 라우터입니다.

사용자의 질문과 현재 Graph 상태를 확인하여 다음에 실행할 Agent를 결정합니다.

주요 판단 대상은 다음과 같습니다.

- 데이터 조회가 필요한지
- 전처리가 필요한지
- 모델 예측 또는 모델 비교가 필요한지
- 시각화가 필요한지
- 실험 분석 요청인지
- 필요한 작업이 완료되어 최종 응답으로 이동할지

### 주요 라우팅 대상

```text
data_agent
prep_agent
model_agent
experiment_agent
visualization_agent
final_response
```

현재 Supervisor는 **Top Supervisor가 아니라 Analysis Agent 내부 Supervisor**입니다.

즉 비즈니스 목적별 전문 Agent를 선택하는 역할이 아니라,

> "이 분석 요청을 처리하기 위해 다음에 어떤 분석 Agent를 실행해야 하는가?"

를 판단합니다.

---

## 4. Data Agent

**파일**

```text
agent_system/analysis_agent/data_agent.py
```

### 역할

사용자의 자연어 요청을 분석하여 **DuckDB에서 실행할 SQL 계획을 생성**합니다.

Data Agent는 직접 데이터를 분석하거나 모델을 실행하지 않습니다.

```text
사용자 요청
    ↓
Data Agent
    ↓
DataQueryPlan
    ↓
SQL Executor
    ↓
pandas DataFrame
```

### 주요 기능

- 필요한 데이터 범위 판단
- DuckDB `SELECT` / `WITH` SQL 생성
- 분석 목적 설명
- 전체 데이터 또는 시각화용 샘플 여부 판단
- 후속 Agent가 사용할 DataFrame 생성에 필요한 조회 계획 수립

### 주요 데이터

현재 Synerise 행동 데이터의 `raw`, `mart` schema를 사용합니다.

예:

```text
raw.product_buy
raw.add_to_cart
raw.remove_from_cart
raw.page_visit
raw.search_query
raw.product_properties
```

구매확률 모델 입력이 필요한 경우에는 raw 데이터를 다시 조합하지 않고 다음 MART를 우선 사용합니다.

```text
mart.purchase_training_data
```

### 중요한 원칙

Data Agent는 SQL로 구매확률을 임의 계산하지 않습니다.

```text
purchase_count / cart_add_count
```

같은 비율을 `purchase_probability`로 만들지 않고, 실제 구매확률은 Model Agent의 저장된 모델을 통해 계산합니다.

---

## 5. Prep Agent

**파일**

```text
agent_system/analysis_agent/prep_agent.py
```

### 역할

Data Agent가 생성한 DataFrame을 확인하고 **어떤 전처리가 필요한지 계획**합니다.

Prep Agent 자체가 DataFrame을 직접 수정하지는 않으며, 실제 pandas 처리는 Prep Executor / Prep Tools에서 수행합니다.

### 지원 작업

현재 계획 가능한 주요 전처리는 다음과 같습니다.

```text
check_dataframe_info
check_missing_values
check_duplicates
convert_timestamp
sort_by_timestamp
create_repeat_count_feature
check_infinite_values
replace_infinite_with_nan
handle_feature_missing_values
```

### 데이터 처리 원칙

- 동일 이벤트가 있다고 해서 자동 삭제하지 않음
- timestamp 변환 필요 여부 판단
- 결측치와 무한대 값 확인
- Feature 의미에 따라 결측치 처리 방식 결정
- 반복 행동이 실제 행동일 가능성을 고려

---

## 6. Model Agent

**파일**

```text
agent_system/analysis_agent/model_agent.py
```

### 역할

사용자의 모델 관련 질문을 해석하여 **어떤 모델 작업을 실행할지 계획**합니다.

Model Agent는 직접 LightGBM을 실행하거나 모델을 학습하지 않습니다.

```text
사용자 요청
    ↓
Model Agent
    ↓
ModelPlan
    ↓
Model Tools
    ↓
저장된 모델 실행 / 모델 성능 조회
```

### 현재 지원 작업

```text
purchase_prediction
promotion_target
model_comparison
model_info
```

### 현재 구매예측 기준

장바구니 추가 이벤트를 기준으로 구매 가능성을 예측합니다.

```text
24시간
48시간
72시간
```

현재 저장된 모델에는 다음 모델군이 포함되어 있습니다.

```text
baseline_category
baseline_remove
logistic_regression
lightgbm
```

현재 모델 비교 결과에서는 각 예측 시점의 LightGBM이 PR-AUC 기준 Champion으로 설정되어 있습니다.

### 현재 구매예측 Feature

```text
page_visit_count_7d_before
past_sku_buy_count
page_visit_count_after_cart
search_count_after_cart
same_sku_remove_count_after_cart
cart_add_count_after_cart
product_price
product_category
```

### 고객 Top-N 처리

모델 자체의 예측 단위는 장바구니 이벤트입니다.

사용자가 다음과 같이 요청하면:

```text
장바구니 추가 후 24시간 기준으로
구매확률이 높은 고객 10명을 찾아줘.
```

전체 이벤트에 대해 예측한 뒤,

```text
client_id별 가장 높은 purchase_probability 1건 선택
→ 고객 중복 제거
→ 구매확률 내림차순 정렬
→ 상위 N명 반환
```

방식으로 고객 단위 결과를 제공합니다.

### 해석 주의

`purchase_probability`는 고객의 **구매 성향 예측값**입니다.

다음 의미로 해석하지 않습니다.

```text
프로모션을 보내면 구매할 확률
캠페인으로 인해 추가 구매가 발생할 확률
uplift
```

실제 캠페인 효과는 별도의 실험 데이터와 A/B Test가 필요합니다.

---

## 7. Visualization Agent

**파일**

```text
agent_system/analysis_agent/visualization_agent.py
```

### 역할

사용자의 질문과 현재 DataFrame 구조를 확인하여 **어떤 차트를 어떤 컬럼으로 만들지 계획**합니다.

실제 matplotlib 실행과 PNG 생성은 Visualization Tools가 담당합니다.

### 현재 지원 차트

```text
bar
line
histogram
boxplot
scatter
heatmap
```

### 주요 판단 정보

- 사용자 요청
- DataFrame 행 수
- 컬럼명
- 데이터 타입
- 숫자형 Feature
- datetime 컬럼
- 범주형 컬럼
- 샘플 데이터

시각화 결과는 기본적으로 다음 위치에 저장됩니다.

```text
WORKS/visualization_output/
```

---

## 8. Experiment Agent

**파일**

```text
agent_system/analysis_agent/experiment_agent.py
```

### 현재 상태

**아직 실제 기능은 구현되지 않았습니다.**

현재 Graph에는 `experiment_agent` Node가 등록되어 있고 Supervisor도 통계 검정 / A/B Test 관련 요청을 감지할 수 있지만, Experiment Node를 실행하면 현재는 다음과 같은 미구현 상태를 반환합니다.

```text
Experiment Agent는 아직 구현되지 않았습니다.
```

따라서 현재 프로젝트에서 실제 실험 효과를 계산하거나 A/B Test 결과를 분석하는 기능은 제공하지 않습니다.

---

## 9. Final Response

**파일**

```text
agent_system/analysis_agent/nodes.py
```

각 Agent와 Executor의 실행 결과는 LangGraph State에 저장됩니다.

작업이 완료되면 `final_response` Node가 이를 사용자에게 읽기 쉬운 형태로 정리합니다.

현재 최종 응답에는 작업에 따라 다음 내용이 포함될 수 있습니다.

```text
분석 결과
DataFrame 요약
모델 예측 결과
모델 선택 근거
시각화 정보
사용된 SQL
데이터 범위 설명
해석상 주의사항
```

---

## 10. Delivery

**파일**

```text
agent_system/delivery/delivery_agent.py
```

Delivery는 분석을 수행하는 Agent와 분리되어 있으며, **완성된 결과를 사용자에게 전달하는 역할**을 담당합니다.

### 현재 역할

- Slack 최종 답변 전송
- 결과 DataFrame CSV 생성
- CSV 파일 첨부
- Visualization PNG 첨부
- 결과 크기에 따른 CSV 생성 여부 결정

### 최종 DataFrame 선택 우선순위

```text
Experiment 결과
    ↓
Model 결과
    ↓
Prep 결과
    ↓
Data 결과
```

현재 Model 결과가 존재하면 `model_result_dataframe`을 우선적으로 전달합니다.

### CSV 정책

전체 결과가 지나치게 큰 경우 자동 CSV 생성을 생략합니다.

현재 자동 첨부 기준:

```text
100,000행 이하
```

생성된 CSV는 다음 위치에 저장됩니다.

```text
WORKS/delivery_output/
```

---

## 11. 현재 실행 흐름 예시

### 구매확률 상위 고객 요청

사용자 질문:

```text
장바구니 추가 후 24시간 기준으로
구매확률이 높은 고객 10명을 찾아줘.
```

실행 흐름:

```text
Analysis Supervisor
    ↓
Data Agent
    ↓
SQL Executor
    ↓
mart.purchase_training_data 조회
    ↓
Analysis Supervisor
    ↓
Model Agent
    ↓
LightGBM 24h Champion 모델
    ↓
전체 이벤트 구매확률 예측
    ↓
고객 중복 제거 + Top 10
    ↓
Final Response
    ↓
Delivery
    ↓
Slack 응답 + CSV
```

---

## 12. 실행 진입점

### CLI

```bash
python main.py
```

### Slack

```bash
python slack_bot.py
```

### 구조 검증

```bash
python 07_refactor_structure_smoke_test.py
```

### Model Graph 테스트

```bash
python 06_model_graph_integration_test.py --case comparison
python 06_model_graph_integration_test.py --case direct
python 06_model_graph_integration_test.py --case prediction
```

---

## 13. 현재 구현 상태 요약

| 구성 요소 | 상태 | 역할 |
|---|---|---|
| Analysis Supervisor | ✅ 구현 | 분석 내부 Agent 라우팅 |
| Data Agent | ✅ 구현 | SQL 조회 계획 생성 |
| SQL Executor | ✅ 구현 | DuckDB SQL 실행 |
| Prep Agent | ✅ 구현 | 전처리 계획 생성 |
| Prep Executor / Tools | ✅ 구현 | 실제 DataFrame 전처리 |
| Model Agent | ✅ 구현 | 모델 작업 계획 |
| Model Tools | ✅ 구현 | 저장 모델 예측·성능 비교 |
| Visualization Agent | ✅ 구현 | 시각화 계획 생성 |
| Visualization Tools | ✅ 구현 | 실제 차트 생성 |
| Experiment Agent | ⏳ 미구현 | 통계 실험 / A/B Test 예정 |
| Final Response | ✅ 구현 | 분석 결과 최종 응답 구성 |
| Delivery | ✅ 구현 | Slack·CSV·PNG 결과 전달 |

---

## 14. 핵심 구분

현재 시스템에서 역할을 간단히 정리하면 다음과 같습니다.

```text
Analysis Supervisor
= 어떤 분석 Agent를 실행할지 결정

Data Agent
= 어떤 데이터를 가져올지 결정

Prep Agent
= 어떤 전처리를 할지 결정

Model Agent
= 어떤 모델 작업을 할지 결정

Visualization Agent
= 어떤 차트를 만들지 결정

Executor / Tools
= 실제 계산과 파일 생성을 수행

Delivery
= 완성된 결과를 사용자에게 전달
```
