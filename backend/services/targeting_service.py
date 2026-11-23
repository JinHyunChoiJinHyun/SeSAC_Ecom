"""타깃 점수 계산 서비스 (아키텍처 [6] 추론 흐름)

서비스가 단계를 직접 호출한다.
  prop  : backend.ml.inference                       (학습 모델 또는 대체 규칙)
  pref  : shared_analysis.targeting.preference_fit   (규칙)
  final : shared_analysis.targeting.scoring          (백분위 평균 → 4분면·순위·근거)
  저장  : score_repository                           (scoring_run 1건 + target_score N건)

계산과 저장은 한 트랜잭션이다.
성공하면 commit, 실패하면 rollback 후 run_event에 별도 트랜잭션으로 기록한다.
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.scoring
from backend.model
