"""run_events_append_only

run_events는 감사 로그 테이블이므로 UPDATE, DELETE, TRUNCATE를 DB 수준에서 차단

Revision ID: bdc839f2fc7e
Revises: 
Create Date: 2026-11-17 12:16:01.482798

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bdc839f2fc7e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 수정 / 삭제 요청을 거부하는 공통 함수
    op.execute(
        """
        CREATE OR REPLACE FUNCTION run_events_block_modification()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'run_events는 추가만 가능합니다 (% 차단)', TG_OP
                USING ERRCODE = 'restrict_violation',
                    HINT = '이벤트 정정이 필요하면 새 이벤트를 추가하세요.';
        END;
        $$;
        """
    )

    # 행 단위 UPDATE, DELETE 차단
    op.execute(
        """
        CREATE TRIGGER trg_run_events_block_update_delete
        BEFORE UPDATE OR DELETE ON run_events
        FOR EACH ROW
        EXECUTE FUNCTION run_events_block_modification();
        """
    )

    # 테이블 전체 TRUNCATE 차단
    op.execute(
        """
        CREATE TRIGGER trg_run_events_block_truncate
        BEFORE TRUNCATE ON run_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION run_events_blcok_modification();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_run_events_block_truncate ON run_events;")
    op.execute("DROP TRIGGER IF EXISTS trg_run_events_block_update_delete ON run_events;")
    op.execute("DROP FUNCTION IF EXISTS run_events_block_modification();")