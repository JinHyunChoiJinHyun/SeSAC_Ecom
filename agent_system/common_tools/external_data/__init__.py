"""백엔드 연결 전 '로컬 모드'에서만 쓰는 외부 데이터 읽기.

아키텍처상 외부 수집 데이터(무신사·네이버·유튜브·패션 매체)는 PostgreSQL external 스키마에 있고
Agent는 백엔드 API로만 조회합니다. 백엔드가 준비되기 전까지 시장 분석 Tool이 무신사 샘플로 동작하도록
이 폴더에서 샘플 파일을 읽습니다. 백엔드 주소(CART_SIGNAL_BACKEND_URL)를 설정하면 이 폴더는 쓰이지 않습니다.

    musinsa_sample.py   무신사 샘플 CSV (database/musinsa/ranking.csv · add_info.csv · review.csv)
"""
