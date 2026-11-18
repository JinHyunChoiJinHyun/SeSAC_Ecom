"""CART SIGNAL의 Slack 표 변환기.

LLM 요청 문구 또는 요청 행 수가 아니라 결과 컬럼의 의미와 구조로 표를 꾸밉니다.
표 데이터가 안전하게 파싱되지 않으면 원문을 그대로 보존합니다.
"""

import io
import re

from common.data_sources import merged

MAX_TABLE_ROWS = 20
MAX_TABLE_COLUMNS = 6

# 출처와 관계없이 쓰이는 컬럼 라벨
_GENERIC_LABELS = {
    "channel": "채널", "customer_count": "고객 수", "title": "제목",
    "month": "월", "date": "날짜", "rank": "순위",
}
_GENERIC_PRIORITY = {"title": 90}

# 출처별 라벨·지표·긴 ID·우선순위는 common/data_sources.py 등록부에서 합칩니다.
_LABELS = {**_GENERIC_LABELS, **merged("column_labels")}
# 소수점 4자리로 보여줄 지표
_METRICS = set(merged("decimal_metrics"))
# 64자 해시 같은 긴 ID는 Slack 표에서 앞부분만 보여 줍니다. (CSV에는 전체 값)
_SHORTEN_IDS = set(merged("shorten_id_columns"))
_PRIORITY = {**_GENERIC_PRIORITY, **merged("column_priority")}


def _is_number(value):
    return bool(re.fullmatch(r"[-+]?(?:\d[\d,]*)(?:\.\d+)?(?:[eE][-+]?\d+)?%?|True|False", str(value)))


def _find_table(lines, start):
    """pandas DataFrame.to_string()으로 생성된 고정폭 표를 보수적으로 감지합니다."""
    import pandas as pd

    header = lines[start].strip().split()
    if not 2 <= len(header) <= 18:
        return None
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+(?:_[가-힣]+)*", h) for h in header):
        return None
    if start + 1 >= len(lines) or not lines[start + 1].strip():
        return None

    candidates = []
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith(("[", "-", "SELECT ", "FROM ", "WHERE ")):
            break
        tokens = line.strip().split()
        if len(tokens) not in (len(header), len(header) + 1):
            break
        if not any(_is_number(x) for x in tokens):
            break
        candidates.append(line)
        if len(candidates) >= MAX_TABLE_ROWS:
            break
    if not candidates:
        return None

    try:
        df = pd.read_fwf(io.StringIO("\n".join([lines[start]] + candidates)), dtype=str)
        if (len(df.columns) == len(header) and len(df) == len(candidates)
                and list(df.columns) == header and not df.isna().any().any()):
            return header, df.astype(str).values.tolist()
    except Exception:
        pass

    token_rows = [line.strip().split() for line in candidates]
    if all(len(row) == len(header) for row in token_rows):
        return header, token_rows
    return None


def _raw(value):
    return {"type": "raw_text", "text": str(value)[:2000]}


def _fmt(value, column):
    if column in _SHORTEN_IDS and len(str(value)) > 16:
        return str(value)[:12] + "…"
    if column.endswith(("_ratio", "_rate", "_share")):
        try:
            ratio = float(str(value).replace(",", ""))
            if 0 <= ratio <= 1:
                return f"{ratio:.2%}"
        except (ValueError, TypeError):
            pass
    if column in _METRICS:
        try:
            return f"{float(str(value).replace(',', '')):.4f}"
        except (ValueError, TypeError):
            pass
    return str(value)


def _choose_columns(header, rows):
    """열의 중요도·상수 여부로 주요 열을 선택합니다. 요청 문자열/고객 수 조건 없음."""
    if len(header) <= MAX_TABLE_COLUMNS:
        chosen = list(header)
    else:
        def score(index):
            name = header[index]
            values = [row[index] for row in rows]
            constant = len(set(values)) <= 1
            # 중복된 설정/플래그 열은 결과 표에서 우선순위가 낮습니다.
            return (_PRIORITY.get(name, 50) - (45 if constant else 0), -index)

        chosen_indices = sorted(range(len(header)), key=score, reverse=True)[:MAX_TABLE_COLUMNS]
        # 원래 컬럼 순서를 보존해 결과를 비교하기 쉽게 합니다.
        chosen = [header[i] for i in sorted(chosen_indices)]

    # Top-N류 결과(식별자 + 정렬 지표)는 순위 열을 함께 표시합니다.
    has_rank = (
        any(name in header for name in ("customer_id", "article_id", "parent_asin", "prod_name"))
        and any(name in header for name in ("purchase_count", "review_count", "buyer_count"))
    )
    # 순위 열 때문에 표가 과도하게 넓어지지 않도록 열 수를 제한합니다.
    if has_rank and len(chosen) > MAX_TABLE_COLUMNS - 1:
        chosen = chosen[:MAX_TABLE_COLUMNS - 1]
    return chosen, has_rank


def _table_payload(header, rows):
    chosen, show_rank = _choose_columns(header, rows)
    # 중복 헤더도 각각의 원래 열 위치에 대응하도록 순차 매핑
    selected_positions = {}
    indices = []
    for name in chosen:
        positions = selected_positions.setdefault(
            name,
            [i for i, column in enumerate(header) if column == name]
        )
        if not positions:
            raise ValueError(f"선택한 열을 찾지 못했습니다: {name}")
        indices.append(positions.pop(0))
    display_header = (["순위"] if show_rank else []) + [_LABELS.get(c, c) for c in chosen]
    body = []
    for i, row in enumerate(rows, start=1):
        vals = ([str(i)] if show_rank else []) + [_fmt(row[j], c) for c, j in zip(chosen, indices)]
        body.append(vals)
    settings = [
        {"align": "right" if body and all(_is_number(r[col]) for r in body) else "left"}
        for col in range(len(display_header))
    ]
    return {
        "type": "table",
        "rows": [[_raw(v) for v in display_header]] + [[_raw(v) for v in row] for row in body],
        "column_settings": settings,
    }


def split_text_and_tables(text):
    """('text', 문자열), ('table', Slack Block Kit) 순서로 반환합니다.

    원본 CSV 첨부는 Delivery가 별도로 수행합니다. 분석 값은 변경하지 않습니다.
    """
    lines = text.splitlines()
    parts, buffer = [], []
    i, in_fence = 0, False
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _find_table(lines, i)
        if match:
            header, rows = match
            if buffer:
                parts.append(("text", "\n".join(buffer)))
                buffer = []
            parts.append(("table", _table_payload(header, rows)))
            i += 1 + len(rows)
        else:
            buffer.append(line)
            i += 1
    if buffer:
        parts.append(("text", "\n".join(buffer)))
    return [(kind, value) for kind, value in parts if kind != "text" or value.strip()]
