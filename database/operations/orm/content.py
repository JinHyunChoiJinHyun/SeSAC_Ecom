import uuid

from datetime import datetime
from typing import Any

from sqlalchemy import(
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.operations.base import Base

class ContentType:
    """콘텐츠 유형 정의 (촬영 기획은 제외)"""

    PROMOTION_PAGE = "promotion_page"
    CAMPAIGN_MESSAGE = "campaign_message"

    # 콘텐츠 묶어서 관리
    ALL = (PROMOTION_PAGE, CAMPAIGN_MESSAGE)

def _format_in_clause(values: tuple[str, ...]) -> str:
    """튜플의 값을 SQL IN 조건에 사용할 문자열로 변환"""
    return ", ".join(f"'{value}'" for value in values)

class Content(Base):
    """생성된 콘텐츠를 버전별로 저장하는 테이블
    - 한 행 = 한 콘텐츠 유형의 한 버전 (수정 불가)
    - 콘텐츠와 대상 명단 해시화
        - 승인 시 콘텐츠와 대상 명단 해시화하여 저장
        - 발송 시점에 콘텐츠와 대상 명단을 다시 해시화하여 기존 승인 해시와 비교
        - content_hash = SHA-256(content_data + 대상 명단)
        - URL 슬롯은 해시 계산에서 제외 (utils/hashing.py)

    content_data 구조 예:
    {
        "slots": {
            "headline": {
                "text": "린넨 셔츠, 이번 여름 검색량 2배",
                "evidence_ids": ["trend:naver_linen_2026w20", "hm_product:0108775015"]
            },
            ...
        }
    }
    """

    __tablename__ = "contents"

    # 기본 키
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    # 콘텐츠를 생성한 Workflow ID (콘텐츠를 생성한 Workflow 실행 건 식별 및 관련 이력 추적)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="RESTRICT"),
        nullable=False
    )

    # 해시화에 사용할 대상 명단 버전의 ID
    audience_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audience_versions.id", ondelete="RESTRICT"),
        nullable=False
    )

    # 콘텐츠 유형
    content_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    )

    # 콘텐츠 버전
    version: Mapped[str] = mapped_column(
        Integer,
        nullable=False
    )

    # 생성 및 검증에 사용한 탬플릿 버전
    template_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    # 실제 콘텐츠 데이터 (슬롯별 문구 + 근거 ID)
    content_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False
    )

    # SHA-256 (content_data + 대상 명단)
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False
    )

    # 콘텐츠 생성 시각
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    __table_args__ = (
        # 콘텐츠 유형 검증
        CheckConstraint(
            f"content_type IN ({_format_in_clause(ContentType.ALL)})",
            name="ck_contents_content_type"
        ),

        # 버전은 1 이상부터 사용
        CheckConstraint(
            "version >= 1",
            name="ck_contents_version"
        ),

        # SHA-256 16진수 64자 검증
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$",
            name="ck_contents_content_hash_format"
        ),

        # slot 객체 (문구별 근거 정보를 담는 객체) 존재 및 자료형 검증 
        CheckConstraint(
            # slots 타입이 object이면 'object' 아니면 해당 타입 또는 빈 문자열 반환 후 비교
            "COALESCE(jsonb_typeof(content_data -> 'slots'), '') = 'object'",
        ),

        # 동일한 Workflow 내 콘텐츠 유형별 버전 중복 방지
        UniqueConstraint(
            "run_id",
            "content_type",
            "version",
            name="uq_contents_run_type_version"
        ),

        # 실행 ID와 콘텐츠 해시로 콘텐츠를 조회하여 재계산된 해시와 기존 해시 비교
        Index(
            "ix_contents_run_id_content_hash",
            "run_id",
            "content_hash"
        ),

        # 해당 명단 버전이 현재 콘텐츠에서 사용 중인지 확인
        Index(
            "ix_contents_audience_version_id",
            "audience_version_id"
        )
    )

    def __repr__(self) -> str:
        return (
            f"Content(id={self.id}, "
            f"run_id={self.run_id}, "
            f"content_type={self.content_type}, "
            f"version={self.version})"
        )
   
CONTENTS_IMMUTABLE_FUNCION_SQL = """
CREATE OR REPLACE FUNCTION contents_block_modify() RETURNS trigger AS $$
"""