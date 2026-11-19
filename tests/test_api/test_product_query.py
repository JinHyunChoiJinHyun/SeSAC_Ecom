import pytest

from database.analytics.connection import analytics_connection
from backend.services.product_service import ProductQueryService

# --------------------------------------------------
# 2단계: 서비스 직접 호출
# --------------------------------------------------
def test_service_get_product_details(sample_article_id):
    with analytics_connection() as con:
        result = ProductQueryService(analytics=con).get_product_details(sample_article_id)

    assert result.data is not None
    assert result.evidence[0].source_type == "hm_product"
    assert result.evidence[0].source_id == sample_article_id

# --------------------------------------------------
# 3단계: API
# --------------------------------------------------
def test_api_get_product_details(client, sample_article_id):
    res = client.get(f"/products/{sample_article_id}")

    assert res.status_code == 200, res.text

    body = res.json()

    assert body["data"]["article_id"] == sample_article_id
    assert body["evidence"][0]["source_type"] == "hm_product"