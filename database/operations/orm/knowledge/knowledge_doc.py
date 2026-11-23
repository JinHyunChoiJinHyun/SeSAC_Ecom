from datetime import date, datetime

from sqlalchemy import (
    String,
    Text,
    Integer,
    Date,
    DateTime,
    func,
    CheckConstraint,
    UniqueConstraint
)

from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from database.operations.base import Base

class KnowledgeDoc(Base):
    __tablename__ = "knowledge_doc"

    doc_id: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        primary_key=True
    )

    source_doc_id: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        index=True
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    knowledge_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True
    )

    # 중복 방지, 앞뒤 청크 조회, 순서 배치 등에 필요
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    body: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict
    )

    version: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        index=True
    )

    # 지식 적용이 시작된 날짜
    effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True
    )

    # 지식 적용이 종료된 날짜
    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True
    )

    embedding: Mapped[list[float]] = mapped_column(
        Vector(1024),
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )


    __table_args__ = (
        UniqueConstraint("source_doc_id", "version", "chunk_index"),
        CheckConstraint(
            "status IN ('active', 'inactive')", 
            name="status_valid"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", 
            name="effective_dates_valid"
        ),
        {"schema": "knowledge"} # 테이블이 속할 스키마 규칙 지정
    )