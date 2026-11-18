"""add_contents_immutable_trigger

Revision ID: df8e8f78eca5
Revises: bdc839f2fc7e
Create Date: 2026-11-17 17:03:13.972439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df8e8f78eca5'
down_revision: Union[str, Sequence[str], None] = 'bdc839f2fc7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """contents 테이블의 UPDATE 및 DELETE 방지"""
    # 1. 데이터 변경을 차단하는 함수 생성
    op.execute("""
        CREATE OR REPLACE FUNCTION contents_block_modify()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'contents row is immutable: % not allowed; insert a new version instead (id=%)',
                TG_OP, OLD.id;
        END;
        $$ LANGUAGE plpsql;
    """)

    # 2. UPDATE 및 DELETE 시 위 함수가 실행되도록 트리거 생성
    op.execute("""
        CREATE TRIGGER trg_contents_block_modify
        BEFORE UPDATE OR DELETE ON contents
        FOR EACH ROW
        EXECUTE FUNCTION contents_block_modify();
    """)

def downgrade() -> None:
    """contents 테이블의 변경 방지 기능 제거"""

    # 1. 트리거 제거
    op.execute("""
        DROP TRIGGER IF EXISTS trg_contents_block_modify ON contents;
    """)

    # 2. 함수 제거
    op.execute("""
        DROP FUNCTION IF EXISTS contents_block_modify();
    """)