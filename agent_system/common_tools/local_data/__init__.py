"""백엔드 연결 전 '로컬 모드'에서만 쓰는 H&M 데이터 읽기.

아키텍처상 Agent는 DB에 직접 붙지 않고 백엔드 API(/products/*)만 호출합니다.
백엔드가 준비되기 전까지 상품 분석 Tool이 동작하도록, 이 폴더에서 H&M DuckDB를 '읽기 전용'으로 조회합니다.
백엔드 주소(CART_SIGNAL_BACKEND_URL)를 설정하면 이 폴더는 쓰이지 않습니다.

    hm_products.py   H&M 상품(articles)·구매 기록(transactions) 조회 — 백엔드 hm_product_repository 역할의 임시판
"""
