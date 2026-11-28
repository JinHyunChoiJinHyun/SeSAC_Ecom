"""백엔드 연결 전 '로컬 모드'에서만 쓰는 외부 데이터 읽기.

아키텍처상 외부 수집 데이터(무신사·네이버·유튜브·패션 매체)는 PostgreSQL external 스키마에 있고
Agent는 백엔드 API로만 조회합니다. 백엔드가 준비되기 전까지 시장 분석 Tool이 무신사 샘플로 동작하도록
이 폴더에서 샘플 CSV 또는 로컬 DuckDB를 읽습니다. 백엔드 주소(CART_SIGNAL_BACKEND_URL)를 설정하면 이 폴더는 쓰이지 않습니다.

    musinsa_sample.py   무신사 CSV (database/musinsa/{카테고리}_ranking·_review·_add_info.csv, 예전 샘플 ranking.csv도 읽음)
    trend_files.py      네이버(database/naver/)·유튜브(database/youtube/) 배치 CSV — 배치 DB 완성 전 임시

    CART_SIGNAL_EXTERNAL_SOURCE=duckdb: common.external_data_store의 읽기 전용 조회를 사용합니다.
    적재·검사 CLI: python -m common.external_data_store verify
    별도 로컬 시장 DB는 일반 분석 SQL 출처 등록과 구분합니다.
"""
