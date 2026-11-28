# 카테고리 기획전 모듈

이 폴더는 CART_SIGNAL의 카테고리 검토, 소분류 선택, 실제 H&M 상품 검증, 승인 근거를 담당합니다.

- `review_selection.py`: 카테고리 검토·확정 자연어 파싱
- `subtype_selection.py`: 세부 유형·색상 추출 및 정렬
- `verified_products.py`: H&M 과거 구매 기반 후보 검증
- `candidate_gate.py`: 실제 Tool 반환 결과 기반 상품 ID 게이트
- `product_evidence.py`: 상품 근거표 및 차트 보강
- `confirmation_report.py`: 대분류 승인 후 표·차트 근거
- `step10_confirmation.py`: 선택 상품 5개 최종 승인 내용
- `workflow_bridge.py`: Slack의 카테고리 분석 연결

시장 분석·비교 함수는 `shared_analysis/market/category_*.py`에 있습니다.

기존 루트 실행 스크립트 대신 아래 명령으로 실행합니다(필요한 DB·외부 데이터가 있을 때).

```bash
python -m shared_analysis.market.category_comparison
python -m shared_analysis.market.category_comparison_v2
```

기존 루트 파일은 옮겨졌으므로 새 import 경로를 사용하세요.
