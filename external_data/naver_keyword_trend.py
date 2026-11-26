"""
네이버 쇼핑인사이트 키워드 일별 클릭 추이 (데이터랩 '쇼핑인사이트 > 검색어 통계' 다운로드와 같은 값)

지정한 키워드를 하나씩 따로 요청해 시작일(기본 2026-01-01)부터 어제까지의 일별 클릭 지수를 CSV 하나로 저장합니다.

- 키워드마다 단독 요청이라 각 키워드의 기간 내 최고점이 100입니다(데이터랩 화면과 같은 숫자).
  그래서 같은 파일 안에서도 키워드끼리 크기 비교는 할 수 없고, 날짜별 흐름만 봅니다.
- 시작일을 바꾸면 기간 내 최고점이 달라져 같은 날짜의 값도 달라질 수 있습니다.
- 요청 수 = 키워드 수.
- 키워드 없이 실행하면 DEFAULT_KEYWORDS(18개)를 조회합니다.
- 키워드 하나에 검색어는 1개만 보냅니다. (주제어 하나에 검색어를 여러 개 넣으면 API가 HTTP 400으로 거절)
- 분야는 기본 패션의류입니다. 신발·가방·액세서리는 --category 패션잡화 로 따로 실행하세요
  (분야가 틀리면 클릭이 거의 0으로 나올 수 있습니다).

실행
----
python naver_keyword_trend.py                                 # 기본 18개
python naver_keyword_trend.py 경량패딩
python naver_keyword_trend.py 로퍼 크로스백 --category 패션잡화
python naver_keyword_trend.py 경량패딩 --start 2025-09-01    # 시작일 변경
python naver_keyword_trend.py 경량패딩 --preview             # 저장 없이 출력만

저장: data/naver/keyword_trend.csv  (행 = 날짜, 열 = 키워드, 실행할 때마다 덮어씀)
      키워드가 하나라도 실패하면 저장하지 않고 기존 파일을 그대로 둡니다.
종료 코드: 모두 성공하면 0, 실패가 있으면 1 (자동 실행에서 실패 확인용)
      열 이름은 입력한 그대로입니다. 띄어 쓴 키워드는 공백을 지운 검색어로 조회합니다
      (예: 열 '트위드 자켓' = 검색어 '트위드자켓'. 실제 검색어는 결과의 terms 에 있음).
환경변수: NAVER_API_HUB_CLIENT_ID, NAVER_API_HUB_CLIENT_SECRET (루트 .env, 없으면 env/.env)
"""

import argparse
import csv
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


# =========================================================
# 기본 설정
# =========================================================

PROJECT_DIR = Path(__file__).resolve().parent          # external_data/ (데이터 저장 기준)
ROOT_DIR = PROJECT_DIR.parent                          # 프로젝트 루트 (env/ 는 다른 팀 코드와 함께 씀)
# API 키 위치: 프로젝트 루트 .env 를 먼저 읽고, 없는 값만 예전 위치 env/.env 에서 채웁니다(대시보드와 같은 순서).
ENV_PATHS = [ROOT_DIR / ".env", ROOT_DIR / "env" / ".env"]
ENV_PATH = ENV_PATHS[-1]   # 예전 이름 호환용


def load_env():
    """루트 .env → env/.env 순서로 읽습니다. 이미 있는 환경변수는 덮어쓰지 않습니다."""
    for path in ENV_PATHS:
        if path.is_file():
            load_dotenv(path)
SAVE_DIR = PROJECT_DIR / "data" / "naver"
CSV_NAME = "keyword_trend.csv"

API_URL = "https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords"
KST = timezone(timedelta(hours=9))

DEFAULT_START = date(2026, 1, 1)
EARLIEST_DATE = date(2017, 8, 1)     # 쇼핑인사이트 조회 가능 시작일

CATEGORY_CODES = {"패션의류": "50000000", "패션잡화": "50000001", "스포츠/레저": "50000007"}
DEFAULT_CATEGORY = "패션의류"   # --category 를 주지 않으면 모든 키워드를 이 분야로 요청

# 키워드 없이 실행할 때 조회하는 기본 목록 (무신사 크롤링 5개 대분류 기준 18개, 모두 패션의류)
DEFAULT_KEYWORDS = [
    # 아우터 (무신사 아우터에 카디건 포함)
    "경량패딩", "패딩", "롱패딩", "숏패딩", "코트", "플리스", "무스탕", "바람막이", "가디건", "재킷",
    # 니트·스웨터
    "스웨터", "니트",
    # 긴소매 티셔츠
    "긴팔티", "롱슬리브", "목폴라",
    # 반소매 티셔츠
    "반팔티",
    # 셔츠·블라우스
    "셔츠", "블라우스",
]


class NaverApiError(RuntimeError):
    pass


# =========================================================
# 네이버 API
# =========================================================

def make_naver_client(client_id, client_secret, retries=2, timeout=20):
    """API 키를 감춘 호출 함수를 만듭니다. 키는 출력하지 않습니다."""

    def post(body):
        for attempt in range(retries):
            try:
                response = requests.post(
                    API_URL,
                    headers={
                        "X-NCP-APIGW-API-KEY-ID": client_id,
                        "X-NCP-APIGW-API-KEY": client_secret,
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=timeout,
                )
            except requests.RequestException as error:
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                raise NaverApiError(f"네이버 API에 연결하지 못했습니다 ({type(error).__name__}).") from None

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise NaverApiError("네이버 API 응답을 읽지 못했습니다.") from None

            if response.status_code >= 500 and attempt < retries - 1:
                time.sleep(1)
                continue
            if response.status_code == 429 and attempt < retries - 1:
                # 요청이 너무 많음 → 잠깐 쉬고 다시 시도 (Retry-After 가 있으면 그만큼, 최대 10초)
                try:
                    wait = min(float(response.headers.get("Retry-After", 2)), 10)
                except ValueError:
                    wait = 2
                time.sleep(wait)
                continue

            message = ""
            try:
                payload = response.json()
                message = (
                    payload.get("errorMessage")
                    or (payload.get("error") or {}).get("message", "")
                    or payload.get("message", "")
                )
            except (ValueError, AttributeError):
                pass
            raise NaverApiError(f"네이버 API 요청 실패: HTTP {response.status_code} {message}".strip())
        raise NaverApiError("네이버 API 요청에 실패했습니다.")

    return post


def client_from_env():
    """루트 .env(없으면 env/.env)에서 API 키를 읽어 호출 함수를 만듭니다. 키는 출력하지 않습니다."""
    load_env()
    client_id = os.getenv("NAVER_API_HUB_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_API_HUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            ".env(루트 또는 env/)에 NAVER_API_HUB_CLIENT_ID와 NAVER_API_HUB_CLIENT_SECRET를 설정해 주세요. "
            "(NAVER 클라우드 플랫폼 콘솔 → NAVER API HUB → Application 등록에서 발급)"
        )
    return make_naver_client(client_id, client_secret)


# =========================================================
# 핵심 함수
# =========================================================

def _keyword_items(keywords):
    """키워드 목록 → [(표시 이름, 검색어)]. 같은 키워드는 한 번만.
    검색어는 공백만 지운 값입니다(예: '트위드 자켓' → 트위드자켓). 표기는 입력한 그대로입니다."""
    items, seen = [], set()
    for keyword in keywords:
        name = str(keyword or "").strip()
        if name and name not in seen:
            seen.add(name)
            items.append((name, re.sub(r"\s+", "", name)))
    return items


def get_keyword_trend(keywords, start=DEFAULT_START, category=None, client=None, today=None):
    """
    키워드마다 단독 요청으로 start부터 어제까지의 일별 클릭 지수를 받습니다. 출력·저장은 하지 않습니다.

    반환 dict: start_date, end_date, dates, category, series(키워드 -> 날짜별 값 목록), terms(키워드 -> 검색어), failures
    """
    category = category or DEFAULT_CATEGORY
    if category not in CATEGORY_CODES:
        raise ValueError(f"category는 {list(CATEGORY_CODES)} 중 하나여야 합니다: {category}")
    today = today or datetime.now(KST).date()
    end = today - timedelta(days=1)
    if not (EARLIEST_DATE <= start <= end):
        raise ValueError(f"시작일은 {EARLIEST_DATE} ~ {end} 사이여야 합니다: {start}")
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    result = {
        "start_date": start.isoformat(), "end_date": end.isoformat(), "dates": dates,
        "category": category, "series": {}, "terms": {}, "failures": [],
    }

    items = _keyword_items(keywords)
    if items:
        client = client or client_from_env()
    for name, term in items:
        body = {
            "startDate": result["start_date"],
            "endDate": result["end_date"],
            "timeUnit": "date",
            "category": CATEGORY_CODES[category],
            "keyword": [{"name": term, "param": [term]}],   # 검색어는 1개만 (여러 개면 HTTP 400)
        }
        try:
            response = client(body)
        except NaverApiError as error:
            result["failures"].append({"keyword": name, "error": str(error)})
            continue
        entry = next((e for e in (response or {}).get("results", []) or [] if e.get("title") == term), None)
        if entry is None:
            result["failures"].append({"keyword": name, "error": "응답에 검색어 결과가 없습니다."})
            continue
        values = {p.get("period"): float(p.get("ratio") or 0) for p in entry.get("data", []) or []}
        # 네이버는 클릭이 없는 날을 응답에서 빼므로, 빠진 날은 0으로 채웁니다.
        result["series"][name] = [round(values.get(d, 0.0), 2) for d in dates]
        result["terms"][name] = term
    return result


def save_csv(result, folder=SAVE_DIR):
    """행 = 날짜, 열 = 키워드. 실행할 때마다 같은 파일을 덮어씁니다."""
    path = Path(folder) / CSV_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(result["series"])
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["날짜"] + names)
        for i, d in enumerate(result["dates"]):
            writer.writerow([d] + [_number(result["series"][n][i]) for n in names])
    return path


def _number(value):
    """정수면 소수점을 빼고 저장·출력합니다. 예: 45.0 → 45, 12.5 → 12.5"""
    return int(value) if float(value).is_integer() else value


def print_result(result):
    print(f"\n{result['start_date']} ~ {result['end_date']} ({len(result['dates'])}일, 일별, 분야 {result['category']})")
    for name, values in result["series"].items():
        peak = max(range(len(values)), key=lambda i: values[i])
        term = result["terms"][name]
        label = name if term == name else f"{name} (검색어 '{term}')"   # 공백 제거로 달라질 때만 표시
        print(f"  {label}: 마지막 날 {_number(values[-1])}, 최고 {result['dates'][peak]}")
    for failure in result["failures"]:
        print(f"  [실패] {failure['keyword']}: {failure['error']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="네이버 쇼핑인사이트 키워드 일별 클릭 추이")
    parser.add_argument("keywords", nargs="*", help="조회할 키워드 (예: 경량패딩 트위드자켓). 없으면 기본 18개(DEFAULT_KEYWORDS)")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START,
                        help=f"시작일 (기본 {DEFAULT_START}). 어제까지 조회합니다")
    parser.add_argument("--category", choices=list(CATEGORY_CODES), help="분야 (기본 패션의류. 신발·가방·액세서리는 패션잡화)")
    parser.add_argument("--preview", action="store_true", help="저장하지 않고 출력만")
    args = parser.parse_args(argv)

    result = get_keyword_trend(args.keywords or DEFAULT_KEYWORDS, start=args.start, category=args.category)
    print_result(result)
    failed = bool(result["failures"]) or not result["series"]
    if not args.preview:
        if failed:
            # 일부만 성공한 결과로 덮어쓰면 기존 파일의 열이 사라지므로 저장하지 않음
            print(f"\n[저장 안 함] 실패한 키워드가 있어 기존 {CSV_NAME} 을 그대로 둡니다.")
        else:
            print(f"\n[저장] {save_csv(result)}")
    print("\n※ 키워드마다 단독 요청이라 각자 기간 내 최고점이 100입니다. 키워드끼리 크기 비교는 할 수 없습니다.")
    # 종료 코드: 실패가 있으면 1 (자동 실행·스케줄러가 실패를 알아챌 수 있도록), 모두 성공하면 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
