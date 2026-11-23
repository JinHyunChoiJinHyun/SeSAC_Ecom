"""구매 가능성(propensity) 예측"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from backend.model