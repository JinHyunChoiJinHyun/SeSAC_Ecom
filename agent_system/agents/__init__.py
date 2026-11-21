"""전문 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」 ①~⑦).

    market_analysis_agent.py      ① 시장 분석      구현 (로컬: 무신사 샘플 / 백엔드 연결 시 전체)
    product_analysis_agent.py     ② 상품 분석      구현 (로컬: H&M DuckDB 읽기 전용)
    targeting_agent.py            ③ 타깃 선정      코드만 (예측 모델·백엔드 연결 전 미구현)
    content_generation_agent.py   ④ 콘텐츠 생성    코드만 (백엔드 /content 연결 전 미구현)
    validation_agent.py           ⑤ 검증           코드만 (백엔드 /validation 연결 전 미구현)
    reporting_agent.py            ⑥ 결과 정리      코드만 (백엔드 /reports 연결 전 미구현)
    campaign_execution_agent.py   ⑦ 캠페인 실행    코드만 (백엔드 /execution 연결 전 미구현, 승인 없이 실행 안 함)
③~⑦은 공통 실행 agent_system/base_agent.py를 씁니다.
"""
