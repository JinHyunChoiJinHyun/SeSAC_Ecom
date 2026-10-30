CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS mart.purchase_predictions (
    predicted_at TIMESTAMP NOT NULL, -- 예측 실행 시각
    cart_event_id BIGINT NOT NULL, -- 장바구니 이벤트 ID
    client_id BIGINT NOT NULL, -- 프로모션 대상 고객
    sku BIGINT NOT NULL, -- 프로모션 대상 상품
    hours_after_cart INTEGER NOT NULL, -- 예측 기준 시점 (장바구니 추가 시점 이후 몇 시간인지)
    cart_timestamp TIMESTAMP, -- 장바구니 추가 시점
    purchase_probability DOUBLE NOT NULL, -- 예측 구매 확률 (0~1)
    promotion_segment VARCHAR NOT NULL, -- 프로모션 구분: low(제외) / target(대상) / high(제외)
    model_name VARCHAR NOT NULL -- 예측에 사용한 모델 파일 이름
)