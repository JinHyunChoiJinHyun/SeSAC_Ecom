"""
backend/services/evidence_service.py
실행 중 계산한 분석 결과를 analysis_snapshot에 저장하고, 다음 Tool이 snapshot_id로 다시 읽게 한다.

- 트랜잭션은 호출한 서비스가 정한다. 여기서는 flush만 하고 commit·rollback 하지 않는다 (아키텍처 3차: commit은 서비스가 명시)
    → 서비스가 응답 데이터까지 만든 뒤 commit하므로, 중간에 실패하면 스냅샷도 함께 rollback된다
    → 예외 시 rollback은 get_operations_session 의존성이 한다
- Evidence(근거) 객체는 만들지 않는다. 라벨("판매 2020-08-26~09-22 (online)" 등)은 결과 맥락을 아는 서비스가 붙인다
"""
from datetime import date
from typing import Any

from pydantic_core import to_jsonable_python
from sqlalchemy.orm import Session

from backend.core.exceptions import (
    InvalidRequestError,
    NotFoundError,
    PermissionDenied
) 
from database.operations.orm.evidence.analysis_snapshot import AnalysisSnapshot
from backend.repositories.operations.evidence_repository import AnalysisSnapshotRepository
from backend.schemas.common import SourceType

class EvidenceService:
    def __init__(self, session: Session):
        self.session = session
        self.snapshots = AnalysisSnapshotRepository(session) # repository 객체 생성

    def save_snapshot(
            self,
            *,
            run_id: str,
            tool_name: str,
            source_type: SourceType,
            params: dict[str, Any],
            result: dict[str, Any],
            analysis_date: date
    ) -> int:
        """저장 후 snapshot_id 반환"""
        snapshot = self.snapshot.create(
            run_id=run_id,
            tool_name=tool_name,
            source_type=source_type,
            # JSON 칼럼의 경우 date/Decimal 등을 그대로 입력 불가 -> JSON 기본 타입으로 변환 후 저장
            params=to_jsonable_python(params),
            result=to_jsonable_python(result),
            analysis_date=analysis_date
        )
        self.session.flush() # SQL 실행, 트랜젝션 최종 확정 X
        return snapshot.snapshot_id

    def load_snapshot(
            self,
            snapshot_id: int,
            *,
            run_id: str,
            expected_tool_name: str
    ) -> AnalysisSnapshot:
        """다음 Tool이 앞 단계 결과 리딩 시
        - 404 = 없음
        - 403 = 다른 run의 결과
        - 422 = 다른 Tool의 결과
        """
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"스냅샷 {snapshot_id}이 없습니다")
        # 다른 실행의 분석 결과 가져오는 문제 방지
        # (예: A와 B 동시 실행 중 B가 A의 분석 결과를 가져오는 경우)
        if snapshot.run_id != run_id:
            raise PermissionDenied(403, f"스냅샷 {snapshot_id}은 현재 run의 결과가 아닙니다")
        # 같은 실행이지만 잘못된 종류의 분석 결과를 가져오는 문제 방지
        # (예: 판매 분석이 필요한데 키워드 매칭 결과를 가져오는 경우) 
        if snapshot.tool_name != expected_tool_name:
            raise InvalidRequestError(
                f"스냅샷 {snapshot_id}은 {snapshot.tool_name} 결과입니다. {expected_tool_name} 결과의 스냅샷이 필요합니다"
            )
        return snapshot