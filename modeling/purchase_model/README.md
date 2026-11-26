# modeling/purchase_model — 구매 예측 모델(Purchase model) + 취향 속성 점수 (H&M)

기획전 **품목 × 색상** 조합을 이후 30일 안에 살 가능성을 **기준일별 하나의 구매 예측 모델**로 계산합니다.
취향 점수(품목·색상·패턴 구매 비율)는 별도 함수입니다. 두 점수를 합쳐 최종 고객을 고르는 규칙은 아직 없습니다.

- 이전 방식인 조합별 LR3(품목×색상마다 별도 모델)는 2026-11-22 프로젝트에서 제외했습니다. 코드·테스트·모델은 저장소 밖 `C:\works\experiments\hm_targeting\archive\targeting_lr3_20261122\`에 보관돼 있습니다.
- 선택 근거: 확인한 평가 범위에서 조합별 LR3와 성능 차이가 작았고, 전체 조합을 하나의 모델·버전으로 관리할 수 있어 기본 방식으로 선택했습니다. 학습 포함 조합 모두의 예측 성능을 검증했다는 뜻은 아닙니다.

> **현재 제한**
> - 저장된 구매 예측 모델은 **학습 as_of = 2020-09-23** 하나(학습 포함 조합 2,207개)입니다(`modeling/purchase_model/models/`).
> - 현재 데이터(마지막 거래일 2020-09-22)로 점수를 계산할 수 있는 기준일은 **2020-09-23 하루**입니다.
> - 팀 `ANALYSIS_AS_OF = 2020-09-22`(마지막 포함 거래일)은 이 코드에 **as_of = 2020-09-23**으로 넘깁니다.
> - 그보다 이른 기준일은 모델이 없어 `ModelNotReady`(또는 `FutureModel`) 오류가 납니다.

## 사용

```python
from modeling.purchase_model import connect_hm, score_combo, score_article

with connect_hm() as data:
    r = score_combo(data, "Sweater", "Black", "2020-09-23")
    r.scores     # customer_id, score_logit, prob_uncalibrated, rank
    r.excluded   # customer_ids로 요청했지만 최근 90일 구매 이력이 없어 점수를 내지 않은 고객
    r2 = score_article(data, "<article_id 10자리 문자열>", "2020-09-23")  # 상품 ID → 품목·색상
```

- **모델**: 기준일(as_of)마다 하나. 학습 as_of 이전 12개 월요일(U+30일 ≤ as_of, U+30일 ≤ complete_through+1일, U−90일 ≥ 첫 거래일)의 학습 가능 전체 조합을 함께 학습합니다.
  - 점수 = a_c + x̃·(β0 + Γz_c + u_c). 입력 x는 같은 품목 90일 구매 수(log1p), 마지막 구매 후 일수(log), 90일 구매일 수(log)이고, z_c는 조합·품목 학습 구매율과 상품군입니다. 상품군은 그 품목×색상 상품 중 가장 많은 상품군(MODE)이며, 동점이면 DuckDB `MODE()`가 하나를 고르고 어느 쪽인지는 보장되지 않습니다(현재 데이터에서는 Umbrella×White 1건).
  - λ=10, 뉴턴 감소량 ≤ 1e-9(최대 100회)로 고정합니다(`model.LAMBDA`, `DEC_TOL`, `MAX_ITER`).
- **지원 범위**: 그 모델의 **학습에 포함된 조합만** 점수를 냅니다. `supported_combos(model)`로 확인합니다.
  - 학습 양성이 0인 조합, Unknown 속성, 카탈로그에 없는 조합은 `UnsupportedCombo`(사유 `not_in_model` / `unknown` / `not_in_catalog`)입니다.
  - 지원하지 않는 조합을 다른 모델로 **자동 대체하지 않습니다**.
- **customer_ids**: 기준일 입력을 만든 뒤 지정한 고객만 남기고, **순위는 남은 고객 안에서** 매깁니다. 최근 90일 구매 이력이 없는 고객은 점수를 만들지 않고 `excluded`로 돌려줍니다.
- **날짜**: `score_as_of`보다 늦은 as_of의 모델은 쓰지 않습니다(없으면 `ModelNotReady`). 입력 기간이 데이터 시작 전이면 `InsufficientHistory`, 데이터 완료 범위를 넘으면 `DataIncomplete`입니다.
- **점수의 한계**
  - `prob_uncalibrated`는 보정되지 않은 값입니다. 순위 용도로 쓰십시오.
  - 전환 근거: 이미 결과를 본 평가 기간(2020-01-30~04-29 시작 4날짜, 일반 조합 40개)의 전환 전 재평가에서, 같은 K=10,000 기대 적중 합이 조합별 LR3의 99% 이상이라는 사전 기준을 충족했습니다(1.0025배). 큰 성능 향상을 확인한 것은 아니며, 통계적 동등성이나 모든 조합·다른 예측 기간(사전 예측 포함)의 성능을 검증한 것이 아닙니다. 상품별 손실(예: Bra×Light Pink)도 있었습니다.
- **학습·저장**
  ```
  python -m modeling.purchase_model.train --as-of 2020-09-23 --complete-through 2020-09-22
  ```
  - 저장 위치: `modeling/purchase_model/models/as_of=<as_of>/<run_id>/{model.joblib, metadata.json}`
  - `.gitignore`의 `models/` 규칙으로 Git에 올라가지 않습니다.
  - `model.joblib`은 numpy 배열과 기본형만 담아 실험 코드 없이 불러옵니다. 임시 폴더(`.tmp-…`)에 저장하고 다시 불러와 완전 일치를 확인한 뒤 최종 폴더 이름으로 바꿉니다.
  - 별도 목록(registry) 파일은 없습니다. 모델 선택은 `models/as_of=*/<run_id>/` 폴더를 직접 훑어, 점수 기준일 이하 as_of 중 최신(같은 as_of면 최신 run_id)을 고릅니다. 임시 폴더는 건너뛰고, 폴더 이름과 모델 내용(as_of, run_id)이 다르면 `ModelMismatch`입니다. `list_models()`로 저장된 모델을 볼 수 있습니다.
  - CLI 종료 코드: 0 학습·저장 완료, 1 실패.
  - 학습 중 실패해도 외부에서 받은 연결은 닫지 않고, 만든 임시 테이블은 지웁니다.
  - 전체 학습(약 2,200개 조합)은 이 PC 기준 약 2.5분, 최대 메모리 약 3.3~4.4GB였습니다(실험 스크립트 3.3GB, 이 패키지 학습 명령 4.4GB).
- 의존성: duckdb, joblib, numpy, pandas, scipy (모두 requirements.txt에 있음, 추가 설치 없음)

## 기준일

| 날짜 | 의미 |
|---|---|
| 학습 as_of | 학습 결과를 사용할 수 있는 정보 기준일. 학습 월요일 U는 U + 30일 ≤ as_of, U + 30일 ≤ complete_through + 1일, U − 90일 ≥ 첫 거래일을 만족하는 최근 12개 (부족하면 `InsufficientHistory`) |
| 점수 계산 as_of | 입력 거래를 끊는 기준일. `[as_of − 90일, as_of)` 거래만 쓰고 당일은 제외. 기준일 이하 as_of의 모델만 고르며, 없으면 `ModelNotReady`. `score_model`에 더 늦은 as_of 모델을 직접 넘기면 `FutureModel` |
| 기획전 시작일 | 위 두 날짜와 항상 같지는 않음 |

## 취향 점수 (구현 완료, 산식 정책 미확정)

- 반환값: 속성별 `*_match`(일치 거래 수), `*_valid`(Unknown 제외 거래 수), `*_ratio`(분모 0이면 NaN)
- 선택 반환값
  - `shrink_k`를 줄 때만 `*_ratio_shrunk` = ratio × valid ÷ (valid + k)
  - `weights`를 줄 때만 `taste_weighted`(가중 평균). 가중치를 준 속성 중 하나라도 NaN이면 NaN
- 대상 속성이 Unknown이면 그 속성은 미산출입니다. 미산출을 0점으로 바꾸거나 가중치를 다시 나누지 않습니다.
- 미확정 항목: 속성 조합, 가중치, k, 조회 기간(`window_days`는 필수 인자), 고객군, 구매 예측 모델 점수와 결합하는 규칙
- 예시의 90일·k·가중치는 권장값이 아닙니다.
- 비율 기반 값으로 순위를 매길 때는 동점 판정에 주의하십시오. 같은 분수 값도 float 끝자리가 달라질 수 있습니다.

## 오류

`ArticleNotFound`, `ComboNotFound`, `InvalidCombo`, `InsufficientHistory`, `DataIncomplete`, `SingleClass`, `NotConverged`, `ModelMismatch`, `FutureModel`, `ModelNotReady`, `SaveFailed`, `UnsupportedCombo`(지원 불가, `.reason` = `unknown` / `not_in_catalog` / `not_in_model`) — 모두 `TargetingError`를 상속합니다.

## 출처와 검증

- 저장소 밖 실험 코드(`hm_targeting/pooled_v2`)와 비교했습니다(반올림 없음).
  - as_of 2020-09-23: 조합 2,207개 목록·순서 동일, 계수 최대 차이 4.4e-14, 목적함수 동일.
  - 평가 기준일 4개(2020-01-30, 02-29, 03-30, 04-29): 계수 최대 차이 9.8e-14, 목적함수 차이 ≤ 7.5e-9(상대 1.6e-16, 집계 행 정렬에 따른 합산 순서 차이). 일반 평가 조합 고객 점수 최대 차이 8.5e-14, K = 1,000·5,000·10,000·25,000 선정 변화 0건.
  - 실험 폴더가 없는 새 프로세스에서 저장 모델 선택·로드·점수 계산을 확인했습니다.
- 패키지 분리·이름 변경 전(`modeling/targeting`, `modeling/targeting_pooled`) 결과와 실제 데이터로 비교해 계수, Sweater × Black·Trousers × Black 점수, 취향 점수가 완전히 같습니다.
