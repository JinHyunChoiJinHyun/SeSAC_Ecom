"""
YouTube 키워드별 총조회수 - 정해 둔 키워드마다 최근 영상 조회수의 합을 구합니다.

키워드마다 최근 N일에 올라온 영상을 조회수순으로 검색하고, 걸러낸 뒤 남은 영상의 조회수를 더합니다.
검색어는 '니트 코디|니트 패션'처럼 패션 맥락을 붙여 보냅니다(키워드만 검색하면 '리트'·'NEET' 뉴스,
다른 나라 'sweater' DIY 영상이 상위를 차지함).
매일·매주 실행해 쌓으면 키워드별 관심도의 상승·하락을 보는 보조 근거가 됩니다.

- 조회수는 영상이 올라온 뒤 쌓인 누적 조회수입니다(기간 동안 늘어난 조회수가 아님).
  그래서 최근 N일 안에 올라온 영상만 모아 최근 관심도를 대신 봅니다.
- YouTube API는 키워드 전체의 총조회수를 주지 않으므로, 검색 상위 영상만 더한 값입니다.
- 키워드 없이 실행하면 naver_keyword_trend.DEFAULT_KEYWORDS(네이버와 같은 18개)를 씁니다.

걸러내는 영상
-------------
- 제목·태그·설명에 그 키워드가 실제로 없는 영상 (검색이 비슷한 말로 넓혀 찾은 경우)
- 제목·태그에 한글이 없는 영상
- 제목에 비의류 주제 단어(먹방, 게임, 주식 등)가 있는 영상
- 제목·태그·설명 앞 300자에 패션 단어(코디, 패션, 옷, 룩, 아우터, 핏 등)가 하나도 없는 영상
  ('코트'의 농구 코트·드라마 영상처럼 뜻이 여러 개인 키워드의 잡음을 줄임)

API 사용량
----------
검색 1페이지(최대 50개) = 100 units, 영상 정보 1번 = 1 unit. 하루 한도 10,000 units.
기본값(18개 키워드 × 1페이지)이면 약 1,818 units 입니다. 실행 전에 예상 사용량을 출력합니다.

실행
----
python youtube_keyword_views.py                            # 기본 18개, 최근 7일, 키워드당 상위 50개
python youtube_keyword_views.py --days 30 --max-videos 100
python youtube_keyword_views.py 트위드자켓 로퍼             # 키워드 직접 지정
python youtube_keyword_views.py --preview                  # 저장 없이 출력만

저장: data/youtube/keyword_views/keyword_views.csv 에 계속 쌓습니다(행 = 수집일 × 키워드).
      같은 날 같은 키워드를 다시 실행하면 그 행만 새 값으로 바뀝니다.
      키워드가 하나라도 실패하면 저장하지 않고 기존 파일을 그대로 둡니다.
종료 코드: 모두 성공하면 0, 실패가 있으면 1
환경변수: 루트 .env(없으면 env/.env)의 YOUTUBE_API_KEY
"""

import argparse
import csv
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


from naver_keyword_trend import DEFAULT_KEYWORDS
from youtube_keyword_candidates import (
    DESCRIPTION_CHARS, EXCLUDE_SIGNALS, FASHION_SIGNALS, HANGUL, KST, SAVE_DIR,
    QuotaExceeded, _to_video, load_env, make_api_getter, normalize,
)


# =========================================================
# 기본 설정 (실행할 때 --days, --max-videos 로 바꿀 수 있습니다)
# =========================================================

DAYS_BACK = 7      # 최근 며칠 안에 올라온 영상만 볼지
MAX_VIDEOS = 50    # 키워드당 검색 상위 몇 개까지 볼지 (50개 = 검색 1페이지)
PAGE_SIZE = 50     # YouTube 검색 1페이지 최대 개수

# 집계하려면 제목·태그·설명 앞부분에 이 중 하나가 있어야 합니다.
# (youtube_keyword_candidates 의 패션 신호어 + 키워드별 검색에서 자주 보이는 패션 표현)
VIEW_FASHION_WORDS = [normalize(w) for w in FASHION_SIGNALS + [
    "룩", "아우터", "쇼핑", "핏", "착용", "입는", "브랜드", "명품",
]]

# 검색할 때 키워드 뒤에 붙이는 말 (search_query 참고)
SEARCH_CONTEXT = ["코디", "패션"]

CSV_NAME = "keyword_views.csv"
CSV_COLUMNS = ["수집일", "키워드", "기간(일)", "검색 영상 수", "집계 영상 수", "총조회수", "최고 조회 영상"]


# =========================================================
# 핵심 함수
# =========================================================

def estimate_units(keyword_count, max_videos=MAX_VIDEOS):
    """예상 API 사용량: 키워드마다 검색 페이지 수 × (검색 100 + 영상 정보 1)."""
    return keyword_count * math.ceil(max_videos / PAGE_SIZE) * 101


def search_query(keyword):
    """검색어에 패션 맥락을 붙입니다. 예: '니트' → '니트 코디|니트 패션' ('|'는 OR).
    키워드만 검색하면 비슷한 발음('리트', 'NEET')이나 다른 나라 영상('sweater' DIY)이 상위를 차지합니다."""
    return "|".join(f"{keyword} {word}" for word in SEARCH_CONTEXT)


def exclude_reason(video, keyword):
    """집계에서 뺄 이유를 반환합니다. 통과하면 None."""
    title = video.get("title", "") or ""
    tags = video.get("tags", []) or []
    description = video.get("description", "") or ""
    head = title + " " + " ".join(tags)
    if normalize(keyword) not in normalize(head + " " + description):
        return "키워드 없음"
    if not HANGUL.search(head):
        return "한글 없음"
    if any(word in normalize(title) for word in EXCLUDE_SIGNALS):
        return "비의류 주제"
    # '코트'(농구 코트), 드라마 속 '코트'처럼 뜻이 여러 개인 키워드를 거르기 위해 패션 단어를 요구합니다.
    # 설명은 앞부분만 봅니다(뒤쪽 쇼핑 링크·협찬 문구의 '옷' 같은 단어로 통과하지 않도록).
    context = normalize(head + " " + description[:DESCRIPTION_CHARS])
    if not any(word in context for word in VIEW_FASHION_WORDS):
        return "패션 단어 없음"
    return None


def _keyword_list(keywords):
    """앞뒤 공백을 지우고, 같은 키워드는 한 번만."""
    result, seen = [], set()
    for keyword in keywords:
        name = str(keyword or "").strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def search_keyword(get, keyword, published_after, published_before, max_videos=MAX_VIDEOS):
    """키워드 하나를 조회수순으로 검색해 상위 max_videos개를 걸러 집계합니다."""
    videos, seen, excluded = [], set(), {}
    searched = 0
    token = None
    for _ in range(math.ceil(max_videos / PAGE_SIZE)):
        params = {
            "part": "id",
            "q": search_query(keyword),
            "type": "video",
            "order": "viewCount",
            "regionCode": "KR",
            "relevanceLanguage": "ko",
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            "maxResults": min(PAGE_SIZE, max_videos - searched),
        }
        if token:
            params["pageToken"] = token
        result = get("search", params)

        ids = []
        for item in result.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if video_id and video_id not in seen and searched < max_videos:
                seen.add(video_id)
                ids.append(video_id)
                searched += 1
        if ids:
            details = get("videos", {"part": "snippet,statistics", "id": ",".join(ids)})
            by_id = {item["id"]: item for item in details.get("items", [])}
            for video_id in ids:
                if video_id not in by_id:
                    continue
                video = _to_video(by_id[video_id], rank=len(videos) + 1)
                reason = exclude_reason(video, keyword)
                if reason:
                    excluded[reason] = excluded.get(reason, 0) + 1
                else:
                    videos.append(video)

        token = result.get("nextPageToken")
        if not token or searched >= max_videos:
            break

    top = max(videos, key=lambda v: v["view_count"], default=None)
    return {
        "searched": searched,
        "videos": len(videos),
        "total_views": sum(v["view_count"] for v in videos),
        "top_url": top["url"] if top else "",
        "top_views": top["view_count"] if top else 0,
        "excluded": excluded,
    }


def get_keyword_views(keywords, get, days=DAYS_BACK, max_videos=MAX_VIDEOS, now=None):
    """
    키워드별 총조회수를 구합니다. 출력·저장은 하지 않습니다.

    반환 dict: run_date, start, end, days, max_videos, results(키워드 -> 집계), failures
    """
    if days < 1:
        raise ValueError(f"days는 1 이상이어야 합니다: {days}")
    if max_videos < 1:
        raise ValueError(f"max_videos는 1 이상이어야 합니다: {max_videos}")
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    result = {
        "run_date": now.astimezone(KST).date().isoformat(),
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "max_videos": max_videos,
        "results": {},
        "failures": [],
    }

    keywords = _keyword_list(keywords)
    for i, keyword in enumerate(keywords):
        try:
            result["results"][keyword] = search_keyword(get, keyword, result["start"], result["end"], max_videos)
        except QuotaExceeded as error:
            # 한도를 다 쓰면 남은 키워드도 모두 실패로 기록하고 멈춥니다.
            for rest in keywords[i:]:
                result["failures"].append({"keyword": rest, "error": str(error)})
            break
        except RuntimeError as error:
            result["failures"].append({"keyword": keyword, "error": str(error)})
    return result


# =========================================================
# 저장·출력
# =========================================================

def save_csv(result, folder=SAVE_DIR / "keyword_views"):
    """수집일 × 키워드 행을 쌓습니다. 같은 수집일·키워드 행은 새 값으로 바꿉니다."""
    path = Path(folder) / CSV_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    new_keys = {(result["run_date"], k) for k in result["results"]}

    rows = []
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as file:
            rows = [r for r in csv.DictReader(file) if (r["수집일"], r["키워드"]) not in new_keys]
    for keyword, r in result["results"].items():
        rows.append({
            "수집일": result["run_date"],
            "키워드": keyword,
            "기간(일)": result["days"],
            "검색 영상 수": r["searched"],
            "집계 영상 수": r["videos"],
            "총조회수": r["total_views"],
            "최고 조회 영상": r["top_url"],
        })

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def print_result(result):
    print(f"\n{result['run_date']} 수집 | 최근 {result['days']}일 업로드, 키워드당 상위 {result['max_videos']}개")
    for keyword, r in result["results"].items():
        excluded = ", ".join(f"{k} {n}" for k, n in r["excluded"].items()) or "없음"
        print(f"  {keyword}: 총조회수 {r['total_views']:,} (영상 {r['videos']}/{r['searched']}개, 제외: {excluded})")
    for failure in result["failures"]:
        print(f"  [실패] {failure['keyword']}: {failure['error']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="YouTube 키워드별 총조회수")
    parser.add_argument("keywords", nargs="*", help="조회할 키워드. 없으면 기본 18개(네이버와 같은 목록)")
    parser.add_argument("--days", type=int, default=DAYS_BACK, help=f"최근 며칠 안의 영상만 (기본 {DAYS_BACK})")
    parser.add_argument("--max-videos", type=int, default=MAX_VIDEOS, help=f"키워드당 검색 상위 몇 개 (기본 {MAX_VIDEOS})")
    parser.add_argument("--preview", action="store_true", help="저장하지 않고 결과만 출력")
    args = parser.parse_args(argv)

    keywords = _keyword_list(args.keywords or DEFAULT_KEYWORDS)
    print(f"키워드 {len(keywords)}개, 예상 사용량 약 {estimate_units(len(keywords), args.max_videos):,} units"
          " (하루 한도 10,000)")

    load_env()
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print(".env(루트 또는 env/)에 YOUTUBE_API_KEY를 설정해 주세요.")
        return 1

    result = get_keyword_views(keywords, make_api_getter(api_key), days=args.days, max_videos=args.max_videos)
    print_result(result)

    failed = bool(result["failures"]) or not result["results"]
    if not args.preview:
        if failed:
            print(f"\n[저장 안 함] 실패한 키워드가 있어 기존 {CSV_NAME} 을 그대로 둡니다.")
        else:
            print(f"\n[저장] {save_csv(result)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
