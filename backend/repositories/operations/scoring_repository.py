"""점수 계산 결과 저장"""

from collections.abc import Iterable, Mapping
from itertools import islice # 원하는 개수만큼 데이터 반환
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from database.operations.orm.scoring.scoring_run import ScoringRun
from database.operations.orm.scoring.target_score import TargetScore

INSERT_CHUNK_SIZE = 5_000

def save_scoring_result(
        session: Session,
        scoring_run_data: Mapping[str, Any], # 계산에 사용된 모델 정보
        target_scores_data: Iterable[Mapping[str, Any]], # 계산된 상품·고객별 점수 결과 N건
        chunk_size: int = INSERT_CHUNK_SIZE
) -> ScoringRun:
    """점수 계산 실행 정보 1건과 상품 & 고객별 점수 결과 N건을 현재 트랜잭션에 추가"""

    # 0. chunk_size 검증
    if chunk_size <= 0:
        raise ValueError("chunk_size는 1 이상이어야 합니다.")

    # 1. 부모 먼저 INSERT (target_score의 FK가 참조해야 함)
    scoring_run = ScoringRun(**scoring_run_data)
    session.add(scoring_run)
    session.flush()

    # 2. 점수의 경우 ORM 객체 없이 청크 단위로 일괄 INSERT
    run_id = scoring_run.scoring_run_id
    rows = iter(target_scores_data) # 반복자 객체로 변환

    inserted = 0

    while chunk := list(islice(rows, chunk_size)):
        session.execute(
            insert(TargetScore),
            [{**row, "scoring_run_id": run_id} for row in chunk]
        )
        inserted += len(chunk)

    if inserted != scoring_run.total_candidate_count:
        raise ValueError(
            f"저장한 점수 수({inserted})가 total_candidate_count"
            f"({scoring_run.total_candidate_count})와 다릅니다."
        )

    return scoring_run

    
    