"""
backend/repositories/operations/evidence_repository.py

analysis_snapshot 테이블에 대한 데이터 접근 담당
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.operations.orm.evidence.analysis_snapshot import AnalysisSnapshot
from backend.schemas.common import SourceType

class AnalysisSnapshotRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
            self,
            *,
            run_id: str,
            tool_name: str,
            source_type: SourceType,
            params: dict[str, Any],
            result: dict[str, Any],
            analysis_date: date
    ) -> AnalysisSnapshot:
        """분석 결과 Snapshot 생성"""
        snapshot = AnalysisSnapshot(
            run_id=run_id,
            tool_name=tool_name,
            source_type=source_type,
            params=params,
            result=result,
            analysis_date=analysis_date
        )

        self.session.add(snapshot)

        return snapshot

    def get(self, snapshot_id: int) -> AnalysisSnapshot | None:
        """Snapshot ID로 분석 결과 조회"""
        statement = select(AnalysisSnapshot).where(
            AnalysisSnapshot.snapshot_id == snapshot_id
        )

        return self.session.scalar(statement)