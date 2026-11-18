"""시장 분석 계산 공통 함수."""

from __future__ import annotations

from datetime import date

import pandas as pd


def clean_words(words) -> list[str]:
    """중복·빈 값 제거 (순서 유지)."""
    if isinstance(words, str):
        words = [words]
    seen, out = set(), []
    for word in words or []:
        word = str(word).strip()
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def rnd(value, digits=4):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return round(float(value), digits)


def to_date(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).date()
    except Exception:
        return None


def phrase_match(name: str, phrase: str) -> bool:
    """'하프집업 니트'처럼 띄어 쓴 키워드는 단어가 모두 들어 있어야 일치 (공백·대소문자 무시)."""
    text = str(name).lower().replace(" ", "")
    words = [w for w in str(phrase).lower().split() if w]
    return bool(words) and all(w in text for w in words)


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON으로 보낼 수 있는 행 목록 (날짜는 문자열, NaN은 None)."""
    if df is None or df.empty:
        return []
    out = df.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict("records")
