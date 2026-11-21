"""CART SIGNAL 데이터 출처 등록부 (Source Registry).

새 데이터(예: 무신사 크롤링, 유튜브 조회수)를 추가할 때는
이 파일의 DATA_SOURCES에 항목 하나를 추가하고,
common/source_docs/<key>/ 아래에 Agent용 설명 문서를 작성하면 됩니다.
DB 연결·SQL 허용 범위·Agent 프롬프트·답변 출처 표시·Slack 표 라벨이 모두 여기서 읽어 갑니다.

자세한 절차: common/source_docs/README.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DOCS_DIR = Path(__file__).resolve().parent / "source_docs"


@dataclass(frozen=True)
class DataSource:
    """데이터 출처 1개의 설정."""

    # ---- 식별 -------------------------------------------------------------
    # SQL에서 쓰는 schema 이름이자 출처 키 (예: hm → hm.transactions)
    key: str
    # 사용자에게 보이는 이름
    label: str
    # 답변의 "데이터 출처"에 표시할 설명 (기간 포함 권장)
    display: str
    # 대화 Agent가 "할 수 있는 일"을 설명할 때 쓰는 한 줄
    capability: str

    # ---- DuckDB 파일 ----------------------------------------------------
    # 기본 파일 경로 (프로젝트 기준 상대 경로)
    default_path: str
    # 파일 경로를 바꿀 환경변수 이름
    path_env: str
    # ATTACH 별칭 (파일 단위 catalog 이름)
    catalog: str
    # 파일 안에서 테이블이 들어 있는 schema 이름 (보통 key와 같음)
    schema: str = ""
    # search_path에 추가로 넣을 파일 내부 schema (예: H&M 파일의 main)
    extra_search_schemas: tuple[str, ...] = ()
    # True면 파일이 없을 때 봇이 시작되지 않음. False면 파일이 없을 때 건너뜀
    required: bool = False

    # ---- 답변 표시 ---------------------------------------------------------
    # 이 출처를 사용한 답변 끝에 덧붙일 주의 문구
    answer_notes: tuple[str, ...] = ()
    # 질문 예시 (봇 안내·미지원 안내에서 사용)
    example_questions: tuple[str, ...] = ()

    # ---- 컬럼 의미 (시각화·전처리·Slack 표에서 사용) -------------------------
    # 숫자여도 연속형 지표가 아닌 식별자·코드 컬럼
    id_columns: tuple[str, ...] = ()
    # Slack 표에서 앞부분만 보여 줄 긴 ID 컬럼
    shorten_id_columns: tuple[str, ...] = ()
    # 날짜/시간 컬럼 후보 (앞쪽이 우선). epoch 정수는 값의 크기로 초/밀리초를 자동 판단
    timestamp_columns: tuple[str, ...] = ()
    # 시간순 정렬 등에 쓰는 개체 컬럼 후보 (고객·상품 등)
    entity_columns: tuple[str, ...] = ()
    # 반복 기록 수를 셀 때 기본 그룹 컬럼 조합 (앞쪽 조합이 우선)
    repeat_group_presets: tuple[tuple[str, ...], ...] = ()
    # Slack 표 한글 라벨
    column_labels: dict = field(default_factory=dict)
    # Slack 표에서 소수점 4자리로 보여 줄 지표
    decimal_metrics: tuple[str, ...] = ()
    # Slack 표 컬럼 우선순위 (클수록 앞)
    column_priority: dict = field(default_factory=dict)
    # 0행 결과 재시도 때 실제 값 목록을 보여 줄 범주형 컬럼 {"schema.table": [컬럼, ...]}
    category_hint_columns: dict = field(default_factory=dict)

    # ------------------------------------------------------------------------
    @property
    def db_schema(self) -> str:
        return self.schema or self.key

    @property
    def path(self) -> Path:
        raw = os.getenv(self.path_env) or str(PROJECT_ROOT / self.default_path)
        return Path(raw)

    def is_available(self) -> bool:
        return self.path.exists()

    def doc(self, agent: str) -> str:
        """common/source_docs/<key>/<agent>.md 내용을 반환합니다 (없으면 빈 문자열)."""
        doc_path = SOURCE_DOCS_DIR / self.key / f"{agent}.md"
        try:
            return doc_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""


# =========================================================================
# 등록된 데이터 출처
# =========================================================================

DATA_SOURCES: list[DataSource] = [
    DataSource(
        key="hm",
        label="H&M",
        display="H&M 자사 데이터 (2018-09-20 ~ 2020-09-22)",
        capability="H&M 자사 데이터(2018-09-20 ~ 2020-09-22): 구매 기록, 상품 속성, 고객 속성 조회·집계",
        default_path="database/hm_data.duckdb",
        path_env="CART_SIGNAL_HM_DB",
        catalog="hm_data",
        schema="hm",
        extra_search_schemas=("main",),
        required=True,
        example_questions=(
            "최근 3개월 니트를 가장 많이 산 고객 Top 20",
            "상품 유형별 월별 온라인 판매 추이를 선그래프로",
        ),
        id_columns=(
            "customer_id", "article_id", "product_code", "postal_code",
            "sales_channel_id", "index_code",
        ),
        shorten_id_columns=("customer_id",),
        timestamp_columns=("t_dat",),
        entity_columns=("customer_id", "article_id", "product_code"),
        repeat_group_presets=(("t_dat", "customer_id", "article_id"),),
        column_labels={
            "customer_id": "고객 ID", "article_id": "상품 ID", "product_code": "상품 모델",
            "prod_name": "상품명", "product_type_name": "상품 유형", "product_group_name": "상품군",
            "colour_group_name": "색상", "perceived_colour_master_name": "색 계열",
            "graphical_appearance_name": "외관", "index_group_name": "부문",
            "garment_group_name": "의류군", "department_name": "부서",
            "sales_channel_id": "채널", "t_dat": "구매일",
            "purchase_count": "구매 건수", "buyer_count": "구매 고객 수",
            "avg_price_index": "평균 가격지수",
            "age": "나이", "age_band": "연령대", "club_member_status": "회원 상태",
            "image_path": "이미지 경로", "image_available": "이미지", "file_bytes": "파일 크기",
        },
        decimal_metrics=("avg_price_index",),
        column_priority={
            "customer_id": 100, "article_id": 95, "prod_name": 94,
            "product_type_name": 90, "colour_group_name": 85, "purchase_count": 99,
            "buyer_count": 98, "avg_price_index": 80,
        },
        category_hint_columns={
            "hm.articles": [
                "index_group_name", "index_name", "product_group_name", "product_type_name",
                "garment_group_name", "department_name", "section_name", "colour_group_name",
                "perceived_colour_master_name", "perceived_colour_value_name",
                "graphical_appearance_name",
            ],
            "hm.customers": ["club_member_status", "fashion_news_frequency"],
        },
    ),
    DataSource(
        key="amazon",
        label="Amazon",
        display="Amazon Reviews 2023 외부 리뷰 데이터",
        capability="Amazon Reviews 2023 의류 리뷰: 별점·리뷰 수·리뷰 문구 집계 (판매량 데이터는 없음)",
        default_path="database/amazon_data.duckdb",
        path_env="CART_SIGNAL_AMAZON_DB",
        catalog="amazon_data",
        schema="amazon",
        required=False,
        answer_notes=(
            "Amazon 데이터에는 판매량이 없습니다. 리뷰 수·별점은 판매 성과가 아닙니다.",
        ),
        example_questions=("Amazon 여성 스웨터 리뷰의 별점 분포",),
        id_columns=("parent_asin", "asin", "user_id"),
        timestamp_columns=("timestamp",),
        entity_columns=("user_id", "parent_asin", "asin"),
        repeat_group_presets=(("user_id", "parent_asin", "timestamp"), ("user_id", "parent_asin")),
        column_labels={
            "parent_asin": "상품 그룹 ID", "asin": "옵션 ID", "store": "브랜드",
            "main_category": "대분류", "rating": "별점", "average_rating": "평균 별점",
            "avg_rating": "평균 별점", "rating_number": "평가 수", "review_count": "리뷰 수",
            "helpful_vote": "도움 투표", "verified_purchase": "구매 인증",
        },
        decimal_metrics=("avg_rating", "average_rating"),
        column_priority={
            "parent_asin": 95, "review_count": 99, "avg_rating": 97, "rating": 96,
            "rating_number": 92, "store": 70,
        },
    ),
    # ---------------------------------------------------------------------
    # 예) 무신사 크롤링 데이터를 적재한 뒤 아래 주석을 풀고
    #     common/source_docs/musinsa/data.md 를 작성하면 Agent가 바로 사용합니다.
    #
    # DataSource(
    #     key="musinsa",
    #     label="무신사",
    #     display="무신사 크롤링 데이터 (수집일 기준)",
    #     capability="무신사 랭킹·상품·리뷰 크롤링 데이터 조회·집계",
    #     default_path="database/musinsa_data.duckdb",
    #     path_env="CART_SIGNAL_MUSINSA_DB",
    #     catalog="musinsa_data",
    #     schema="musinsa",
    #     answer_notes=("무신사 데이터는 수집 시점의 스냅샷입니다.",),
    # ),
]


# =========================================================================
# 조회 함수
# =========================================================================

def _validate_registry():
    keys = [source.key for source in DATA_SOURCES]
    if len(keys) != len(set(keys)):
        raise ValueError(f"데이터 출처 key가 중복되었습니다: {keys}")
    catalogs = [source.catalog for source in DATA_SOURCES]
    if len(catalogs) != len(set(catalogs)):
        raise ValueError(f"데이터 출처 catalog가 중복되었습니다: {catalogs}")
    for source in DATA_SOURCES:
        for name in (source.key, source.catalog, source.db_schema, *source.extra_search_schemas):
            if not name.replace("_", "").isalnum() or not name[0].isalpha():
                raise ValueError(f"영문·숫자·_ 로 된 이름만 사용할 수 있습니다: {name}")


_validate_registry()


def get_sources() -> list[DataSource]:
    """등록된 모든 출처."""
    return list(DATA_SOURCES)


def get_available_sources() -> list[DataSource]:
    """DB 파일이 실제로 있는 출처만 (Agent 프롬프트·SQL 허용 범위에 사용)."""
    return [source for source in DATA_SOURCES if source.is_available()]


def get_source(key: str) -> DataSource | None:
    for source in DATA_SOURCES:
        if source.key == key:
            return source
    return None


def source_keys(available_only: bool = True) -> list[str]:
    sources = get_available_sources() if available_only else get_sources()
    return [source.key for source in sources]


def merged(attribute: str):
    """출처별 설정(dict 또는 tuple)을 하나로 합칩니다."""
    values = [getattr(source, attribute) for source in DATA_SOURCES]
    if values and isinstance(values[0], dict):
        result = {}
        for value in values:
            result.update(value)
        return result
    result = []
    for value in values:
        for item in value:
            if item not in result:
                result.append(item)
    return tuple(result)


def build_agent_source_docs(agent: str, sources: list[DataSource] | None = None) -> str:
    """연결된 출처의 Agent용 설명 문서를 이어 붙입니다.

    agent: "data" / "prep" / "visualization"
    """
    sources = get_available_sources() if sources is None else sources
    sections = []
    for source in sources:
        text = source.doc(agent)
        if text:
            sections.append(text)
    return "\n\n\n".join(sections)


def describe_sources_for_prompt(sources: list[DataSource] | None = None) -> str:
    """프롬프트용 '조회 가능한 schema' 목록."""
    sources = get_available_sources() if sources is None else sources
    return "\n".join(f"- {source.key:<10} → {source.display}" for source in sources)
