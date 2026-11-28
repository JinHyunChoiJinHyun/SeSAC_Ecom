"""유튜브 영상 반응 계산 — analyze_youtube_trends.

입력 videos: DataFrame
    keyword(검색 키워드), video_id, title, channel_title, published_at, collected_at, view_count
    (같은 영상이 여러 번 수집됐으면 (keyword, video_id)마다 가장 최근 수집 행만 사용)
하루 평균 조회수 = 누적 조회수 ÷ 게시 후 경과일(수집 시각 기준, 최소 1일)

영상별 조회수가 없고 '키워드 후보' 표(조회수 상위 영상에 반복 등장한 키워드와 등장 채널 수)만 있을 때는
summarize_candidates()로 키워드별 등장 채널 수·순위·기준어 대비 배율만 계산합니다 (하루 평균 조회수는 계산하지 않음).
    candidates: DataFrame  category, keyword, channel_count (+ links, basis, collected_at)
"""

from __future__ import annotations

import pandas as pd

import re

from shared_analysis.market.common import clean_words, rnd

RECENT_DAYS = 30            # 최근 N일 안에 게시된 영상만 (가설)
MIN_VIDEOS = 3              # 영상이 이보다 적으면 '표본 부족' 표시 (가설)


def latest_rows(videos: pd.DataFrame) -> pd.DataFrame:
    df = videos.copy()
    # 시간대가 없는 날짜도 UTC 기준으로 해석
    df["published_at"] = pd.to_datetime(
        df["published_at"], utc=True, errors="coerce"
    )
    df["collected_at"] = pd.to_datetime(
        df["collected_at"], utc=True, errors="coerce"
    )
    df = df.dropna(subset=["published_at", "collected_at"])
    df = df.sort_values("collected_at").drop_duplicates(["keyword", "video_id"], keep="last")
    age = (df["collected_at"] - df["published_at"]).dt.total_seconds() / 86400
    df["age_days"] = age.clip(lower=1.0)
    df["daily_views"] = pd.to_numeric(df["view_count"], errors="coerce").astype(float) / df["age_days"]
    return df


def analyze_videos(videos: pd.DataFrame, keywords, base_keyword=None, recent_days=None) -> dict:
    keywords = clean_words(keywords)
    base = str(base_keyword or "").strip()
    days = int(recent_days or RECENT_DAYS)
    wanted = keywords + ([base] if base and base not in keywords else [])
    df = latest_rows(videos[videos["keyword"].isin(wanted)]) if videos is not None and not videos.empty else pd.DataFrame()
    stats = {}
    for keyword, group in (df.groupby("keyword", sort=False) if not df.empty else []):
        as_of = group["collected_at"].max()
        recent = group[group["published_at"] >= as_of - pd.Timedelta(days=days)]
        top = recent.sort_values("daily_views", ascending=False).head(3)
        stats[keyword] = {
            "keyword": keyword, "as_of": str(as_of.date()), "videos_total": len(group), "recent_videos": len(recent),
            "median_daily_views": rnd(recent["daily_views"].median(), 1) if len(recent) else None,
            "mean_daily_views": rnd(recent["daily_views"].mean(), 1) if len(recent) else None,
            "latest_published": str(recent["published_at"].max().date()) if len(recent) else None,
            "sample_warning": len(recent) < MIN_VIDEOS,
            "top_videos": [{"title": r.title, "channel": r.channel_title, "published": str(r.published_at.date()),
                            "daily_views": rnd(r.daily_views, 1)} for r in top.itertuples()],
        }
    base_median = (stats.get(base) or {}).get("median_daily_views") if base else None
    rows = []
    for keyword in keywords:
        if keyword not in stats:
            rows.append({"keyword": keyword, "recent_videos": 0, "note": "데이터 없음"})
            continue
        row = dict(stats[keyword])
        median = row["median_daily_views"]
        row["vs_base_ratio"] = rnd(median / base_median, 3) if median is not None and base_median else None
        rows.append(row)
    return {
        "keywords": keywords, "base_keyword": base or None, "base_median_daily_views": base_median,
        "basis": f"수집 시점 기준 최근 {days}일 안에 게시된 영상, 하루 평균 조회수 = 누적 조회수 ÷ 게시 후 경과일",
        "rows": rows,
        "notes": ["유튜브는 보조 근거입니다 (검색 결과 영상 표본).", f"영상 {MIN_VIDEOS}개 미만이면 sample_warning=True"],
    }


def _norm(word) -> str:
    """'블랙 (상의 전체)' → '블랙', 공백 제거·소문자 (키워드 후보 비교용)."""
    return re.sub(r"\s+", "", re.sub(r"\(.*?\)", "", str(word))).lower()


def summarize_candidates(candidates: pd.DataFrame, keywords, base_keyword=None, related_limit: int = 5,
                         top_n: int = 10) -> dict:
    """키워드 후보 표 → 요청 키워드별 등장 채널 수·순위, 기준어 대비 배율, 관련 후보.

    채널 수 순위 = 같은 수집 표 안에서 등장 채널 수가 많은 순 (같으면 같은 순위)
    vs_base_ratio = 키워드 등장 채널 수 ÷ 기준어 등장 채널 수
    관련 후보 = 키워드의 단어가 모두 들어간 다른 후보 (예: 니트 → 그레이 니트)
    """
    keywords = clean_words(keywords)
    base = str(base_keyword or "").strip()
    if candidates is None or candidates.empty:
        return {"keywords": keywords, "rows": [], "top_candidates": [],
                "notes": ["유튜브 키워드 후보 데이터가 없습니다."]}
    df = candidates.copy()
    df["channel_count"] = pd.to_numeric(df["channel_count"], errors="coerce").fillna(0).astype(int)
    if "collected_at" in df and df["collected_at"].notna().any():
        latest = df["collected_at"].astype(str).max()       # 여러 수집 파일이면 최신 수집만
        df = df[df["collected_at"].astype(str) == latest]
    else:
        latest = None
    df = df.sort_values(["channel_count", "keyword"], ascending=[False, True]).reset_index(drop=True)
    df["rank"] = df["channel_count"].rank(method="min", ascending=False).astype(int)
    df["_key"] = df["keyword"].map(_norm)
    total = len(df)

    def row_for(word):
        hit = df[df["_key"] == _norm(word)]
        return hit.iloc[0] if len(hit) else None

    base_row = row_for(base) if base else None
    base_count = int(base_row["channel_count"]) if base_row is not None else None
    rows = []
    for keyword in keywords:
        hit = row_for(keyword)
        words = [w for w in str(keyword).lower().split() if w]
        related = df[df["_key"].map(lambda k: bool(words) and all(w in k for w in words)) &
                     (df["_key"] != _norm(keyword))].head(related_limit)
        row = {"keyword": keyword,
               "related_candidates": [{"keyword": r.keyword, "channel_count": int(r.channel_count)}
                                      for r in related.itertuples()]}
        if hit is None:
            row.update(channel_count=None, rank=None, note="키워드 후보에 없음")
        else:
            count = int(hit["channel_count"])
            row.update(category=str(hit.get("category", "") or ""), channel_count=count, rank=int(hit["rank"]),
                       rank_of=total, evidence_links=list(hit.get("links") or [])[:3],
                       vs_base_ratio=rnd(count / base_count, 3) if base_count else None)
        rows.append(row)
    top = [{"keyword": r.keyword, "category": str(getattr(r, "category", "") or ""),
            "channel_count": int(r.channel_count)} for r in df.head(top_n).itertuples()]
    basis = str(df["basis"].dropna().iloc[0]) if "basis" in df and df["basis"].notna().any() else ""
    return {
        "keywords": keywords, "base_keyword": base or None, "base_channel_count": base_count,
        "collected_at": latest, "candidates_total": total, "rows": rows, "top_candidates": top,
        "basis": ("등장 채널 수 = 조회수 상위 영상 표본에서 그 키워드가 나온 채널 수"
                  + (f" ({basis})" if basis else "") + ", vs_base_ratio = 키워드 채널 수 ÷ 기준어 채널 수"),
        "notes": ["유튜브는 보조 근거입니다.",
                  "영상별 조회수·게시일이 없는 키워드 후보 표라 '하루 평균 조회수'는 계산하지 않았습니다."],
    }
