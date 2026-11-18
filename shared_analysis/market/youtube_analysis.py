"""유튜브 영상 반응 계산 — analyze_youtube_trends.

입력 videos: DataFrame
    keyword(검색 키워드), video_id, title, channel_title, published_at, collected_at, view_count
    (같은 영상이 여러 번 수집됐으면 (keyword, video_id)마다 가장 최근 수집 행만 사용)
하루 평균 조회수 = 누적 조회수 ÷ 게시 후 경과일(수집 시각 기준, 최소 1일)
"""

from __future__ import annotations

import pandas as pd

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
