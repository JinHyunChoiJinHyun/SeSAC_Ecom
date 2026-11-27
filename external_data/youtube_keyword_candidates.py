"""
YouTube 패션 키워드 후보 수집 - 키워드 사전과 규칙으로 키워드를 뽑습니다.

수집
----
- 넓은 검색어(패션 코디|룩북|데일리룩|착장|ootd)로 최근 30일 영상을 조회수순으로 검색합니다.
- 한국 지역·한국어 결과를 우선하고, 쇼츠와 일반 영상을 모두 포함합니다.
- 중복 영상, 패션 관련성이 낮은 영상, 제목·태그에 한글이 없는 영상을 제외하고 최대 200개를 모읍니다.

키워드 판단 (규칙 기반)
----------------------
- 태그·해시태그: 덩어리째 분석하고, 사전에 없는 단어도 2개 채널 이상이면 후보('미분류' 포함)
- 제목·설명(앞부분 1,500자): 사전에 있는 단어만 인정
- 표기 차이는 사전으로 통합합니다(예: 트위드 자켓 → 트위드 재킷).
- 색상·소재는 단독으로 세지 않고 아이템과 묶어 셉니다(예: 브라운 팬츠).
  색상은 같은 상품 카테고리 안에서도 묶어 셉니다(예: 브라운 (아우터 전체)).
  스타일(꾸안꾸, 올드머니)만 단독 키워드로 둡니다.
- 채널 수는 channel_id 기준이며, 같은 채널에서 여러 번 나와도 1로 셉니다.

실행
----
python youtube_keyword_candidates.py --preview        # 저장하지 않고 결과 일부만 출력
python youtube_keyword_candidates.py                  # database/youtube/keyword_candidates/keyword_candidates_YYYYMMDD.csv 저장

환경변수
--------
루트 .env(없으면 env/.env)의 YOUTUBE_API_KEY
"""

import argparse
import csv
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


# =========================================================
# 기본 설정
# =========================================================

PROJECT_DIR = Path(__file__).resolve().parent          # external_data/ (수집 코드 위치)
ROOT_DIR = PROJECT_DIR.parent                          # 프로젝트 루트 (env/ 는 다른 팀 코드와 함께 씀)
# API 키 위치: 프로젝트 루트 .env 를 먼저 읽고, 없는 값만 예전 위치 env/.env 에서 채웁니다(대시보드와 같은 순서).
ENV_PATHS = [ROOT_DIR / ".env", ROOT_DIR / "env" / ".env"]
ENV_PATH = ENV_PATHS[-1]   # 예전 이름 호환용


def load_env():
    """루트 .env → env/.env 순서로 읽습니다. 이미 있는 환경변수는 덮어쓰지 않습니다."""
    for path in ENV_PATHS:
        if path.is_file():
            load_dotenv(path)
SAVE_DIR = ROOT_DIR / "database" / "youtube"   # 프로젝트 루트 database/ (에이전트가 읽는 위치)

API_URL = "https://www.googleapis.com/youtube/v3"
KST = timezone(timedelta(hours=9))

# 수집 범위를 잡기 위한 넓은 검색어입니다. '|'는 YouTube 검색의 OR 조건입니다.
SEARCH_QUERY = "패션 코디|룩북|데일리룩|착장|ootd"
DAYS_BACK = 30
MAX_VIDEOS = 200
# 검색 1페이지 = 최대 50개, 100 units. 필터로 약 1/3이 빠지므로 200개에 넉넉한 10페이지(최대 1,000 units)
MAX_PAGES = 10
MIN_CHANNELS = 2
DESCRIPTION_CHARS = 300

BASIS_TEXT = "최근 30일 조회수 상위 표본에서 반복 등장한 키워드"
CSV_COLUMNS = ["수집일", "상품 카테고리", "키워드", "등장 채널 수", "근거 링크", "기준"]
LINK_SEPARATOR = " | "

CATEGORY_ORDER = [
    "아우터", "상의", "하의", "원피스", "신발", "가방", "액세서리",
    "스타일", "미분류",
]


# =========================================================
# 키워드 사전
# 형식: 대표 표기 -> 표기 변형 목록 (대표 표기 포함)
# 철자만 다른 표기는 같은 항목에 넣고, 다른 아이템·소재는 별도 항목으로 둡니다.
# =========================================================

ITEMS = {
    "아우터": {
        "재킷": ["재킷", "자켓", "쟈켓"],
        "코트": ["코트"],
        "트렌치코트": ["트렌치코트", "트렌치"],
        "패딩": ["패딩"],
        "경량 패딩": ["경량패딩"],
        "숏패딩": ["숏패딩"],
        "롱패딩": ["롱패딩"],
        "패딩 조끼": ["패딩조끼", "패딩베스트"],
        "조끼": ["조끼"],
        "점퍼": ["점퍼", "잠바", "점바"],
        "블레이저": ["블레이저", "블레이져"],
        "카디건": ["카디건", "가디건"],
        "무스탕": ["무스탕"],
        "플리스": ["플리스", "후리스"],
        "바람막이": ["바람막이"],
        "야상": ["야상"],
        "집업": ["집업"],
        "아노락": ["아노락"],
    },
    "상의": {
        "니트": ["니트"],
        "스웨터": ["스웨터", "스웨타", "쉐타"],
        "셔츠": ["셔츠", "남방"],
        "블라우스": ["블라우스"],
        "티셔츠": ["티셔츠"],
        "맨투맨": ["맨투맨", "스웻셔츠"],
        "후드티": ["후드티", "후디"],
        "터틀넥": ["터틀넥", "폴라티"],
        "크롭탑": ["크롭탑", "크롭티"],
        "나시": ["나시", "슬리브리스"],
    },
    "하의": {
        # 바지와 팬츠는 같은 아이템이라 하나로 셉니다. 청바지·반바지는 별도 항목입니다.
        "팬츠": ["팬츠", "바지"],
        "슬랙스": ["슬랙스"],
        "청바지": ["청바지"],
        "조거 팬츠": ["조거팬츠"],
        "와이드 팬츠": ["와이드팬츠"],
        "카고 팬츠": ["카고팬츠"],
        "부츠컷": ["부츠컷"],
        "반바지": ["반바지"],
        "레깅스": ["레깅스"],
        "스커트": ["스커트", "치마"],
        "미니스커트": ["미니스커트"],
        "롱스커트": ["롱스커트"],
    },
    "원피스": {
        "원피스": ["원피스"],
        "드레스": ["드레스"],
    },
    "신발": {
        "운동화": ["운동화"],
        "스니커즈": ["스니커즈", "스니커스"],
        "부츠": ["부츠"],
        "어그부츠": ["어그부츠", "어그"],
        "로퍼": ["로퍼"],
        "구두": ["구두"],
        "메리제인": ["메리제인"],
        "샌들": ["샌들"],
        "슬리퍼": ["슬리퍼"],
        "플랫슈즈": ["플랫슈즈"],
        "힐": ["힐"],
    },
    "가방": {
        "가방": ["가방"],
        "백팩": ["백팩"],
        "숄더백": ["숄더백"],
        "토트백": ["토트백"],
        "크로스백": ["크로스백"],
        "에코백": ["에코백"],
        "클러치": ["클러치"],
    },
    "액세서리": {
        "스카프": ["스카프"],
        "머플러": ["머플러"],
        "목도리": ["목도리"],
        "모자": ["모자"],
        "비니": ["비니"],
        "볼캡": ["볼캡", "캡모자"],
        "벨트": ["벨트"],
        "장갑": ["장갑"],
        "귀걸이": ["귀걸이", "귀고리"],
        "목걸이": ["목걸이"],
        "선글라스": ["선글라스"],
        "양말": ["양말"],
        "스타킹": ["스타킹"],
        "넥워머": ["넥워머"],
    },
}

ATTRIBUTES = {
    "소재": {
        "트위드": ["트위드"],
        "울": ["울"],
        "캐시미어": ["캐시미어", "캐시미야"],
        "레더": ["레더", "가죽"],
        "스웨이드": ["스웨이드", "스웨드"],
        "코듀로이": ["코듀로이", "골덴"],
        "데님": ["데님"],
        "퍼": ["퍼"],
        "시어링": ["시어링"],
        "린넨": ["린넨", "리넨"],
        "벨벳": ["벨벳"],
        "앙고라": ["앙고라"],
        "알파카": ["알파카"],
        "새틴": ["새틴", "사틴"],
        "니트웨어": ["니트웨어"],
    },
    "색상": {
        "블랙": ["블랙", "검정", "검정색", "black"],
        "화이트": ["화이트", "흰색", "white"],
        "아이보리": ["아이보리", "ivory"],
        "베이지": ["베이지", "beige"],
        "브라운": ["브라운", "갈색", "brown"],
        "카멜": ["카멜", "camel"],
        "그레이": ["그레이", "회색", "gray", "grey"],
        "차콜": ["차콜", "charcoal"],
        "네이비": ["네이비", "남색", "navy"],
        "버건디": ["버건디", "burgundy"],
        "카키": ["카키", "khaki"],
        "올리브": ["올리브", "olive"],
        "레드": ["레드", "빨간", "빨강", "빨간색", "red"],
        "핑크": ["핑크", "분홍", "분홍색", "pink"],
        "블루": ["블루", "파랑", "파란", "파란색", "blue"],
        "스카이블루": ["스카이블루", "하늘색", "skyblue"],
        "그린": ["그린", "초록", "초록색", "green"],
        "옐로우": ["옐로우", "옐로", "노랑", "노란", "노란색", "yellow"],
        "퍼플": ["퍼플", "보라", "보라색", "purple"],
        "라벤더": ["라벤더", "lavender"],
        "민트": ["민트", "mint"],
        "크림": ["크림", "cream"],
        "모카": ["모카", "mocha"],
    },
    "스타일": {
        "올드머니": ["올드머니"],
        "꾸안꾸": ["꾸안꾸"],
        "미니멀": ["미니멀"],
        "클래식": ["클래식"],
        "캐주얼": ["캐주얼"],
        "스트릿": ["스트릿", "스트리트"],
        "고프코어": ["고프코어"],
        "빈티지": ["빈티지"],
        "프레피": ["프레피"],
        "놈코어": ["놈코어"],
        "페미닌": ["페미닌"],
        "아메카지": ["아메카지"],
        "블록코어": ["블록코어"],
        "발레코어": ["발레코어"],
        "레트로": ["레트로"],
        "레이어드": ["레이어드"],
        "오버핏": ["오버핏"],
    },
}

# 수집용 포괄 표현과 키워드가 될 수 없는 일반어입니다. 후보에서 제외합니다.
STOP_WORDS = {
    "패션", "룩북", "룩", "데일리룩", "코디", "착장", "ootd", "옷", "의류", "스타일링",
    "스타일", "하울", "여성의류", "남성의류", "패션정보", "패션트렌드", "트렌드",
    "lookbook", "fashion", "outfit", "style", "shorts", "쇼츠", "vlog", "브이로그",
    "grwm", "추천", "추천템", "템", "아이템", "브랜드", "디자이너", "디자이너브랜드", "쇼핑",
    "세일", "할인", "이벤트", "협찬", "광고", "리뷰", "언박싱", "신상", "컬러", "색상",
    "색", "소재", "기본", "기본템", "필수템", "꿀템", "이상", "조합", "두께", "길이",
    "사이즈", "가격", "퀄리티", "정보", "모델", "화보", "여자", "남자", "여성", "남성",
    "중년", "시니어", "직장인", "오피스", "출근", "출근룩", "데일리", "겨울", "가을",
    "봄", "여름", "간절기", "연말", "하객", "데이트", "학생", "대학생", "요즘", "인기",
    "제일", "최신", "신년", "겨울철", "겨울준비", "위시리스트", "코디법", "입는법",
    "꿀팁", "팁", "유튜버", "옷잘", "여행", "체형", "체형별", "트렌디", "키작녀",
    "pinterest", "youtube", "instagram", "tiktok", "cm", "kg",
    # 브랜드·쇼핑몰·채널 이름은 상품 키워드가 아닙니다(목록에 넣은 것만 걸러집니다).
    "무신사", "유니클로", "zara", "자라", "루피타", "쇼핑몰", "에이블리", "지그재그",
    "29cm", "더블유컨셉", "wconcept", "탑텐", "스파오", "prismlivestudio",
    # 상품이 아닌 일반어입니다.
    "가성비", "오오티디", "라이브", "일상", "생활", "모임", "내돈내산", "오늘뭐입지",
    "핀터레스트", "엄마옷", "초겨울", "늦가을", "체형커버", "트렌드컬러", "럭셔리",
    # 카테고리 이름 자체는 키워드가 아닙니다.
    "아우터", "상의", "하의", "신발", "가방류", "액세서리", "악세서리", "잡화",
}

# 미분류 후보에서 제외할 형용사형 끝말입니다(예: 세련된, 귀티나는).
ADJECTIVE_ENDINGS = re.compile(r"(는|한|된|운|적|게)$")

# 영문 해시태그 중 이 단어가 들어간 것은 일반 표현으로 보고 미분류 후보에서 제외합니다
# (예: outfitideas, winteroutfits, fashionover50). quietluxury처럼 이 단어가 없으면 남습니다.
ENGLISH_GENERIC_PARTS = (
    "outfit", "fashion", "style", "look", "inspo", "idea", "ootd", "daily", "haul",
    "short", "winter", "fall", "autumn", "spring", "summer", "trend", "chic",
    "elegant", "modern", "vlog", "live", "grwm",
)

# 토큰 앞에 붙는 수식어입니다. 두 글자 이상만 앞부분에서 떼어냅니다.
PREFIX_MODIFIERS = sorted(
    [
        "겨울철", "겨울", "가을", "여름", "간절기", "여자", "여성", "남자", "남성",
        "중년", "시니어", "데일리", "출근", "직장인", "오피스", "기본", "신상", "요즘",
        "인기", "연말", "하객", "데이트", "대학생", "학생", "키작녀", "빅사이즈",
    ],
    key=len, reverse=True,
)

# 토큰 끝에 붙는 일반 표현입니다.
GENERIC_SUFFIXES = sorted(
    [
        "코디법", "코디", "추천템", "추천", "룩북", "룩", "스타일링", "스타일", "하울",
        "패션", "필수템", "기본템", "꿀템", "템", "브랜드", "쇼핑", "매치", "조합",
        "입는법", "활용", "리뷰", "착용", "착장", "모음", "브이로그", "꿀팁", "팁",
        "쇼츠", "유튜버", "중년", "여자", "여성", "매는법", "하는법", "방법",
    ],
    key=len, reverse=True,
)

# 문장 속 단어 끝의 조사입니다. 사전 단어 확인용으로만 떼어냅니다.
JOSA_SUFFIXES = sorted(
    [
        "으로", "에서", "까지", "부터", "대신", "처럼", "보다", "이랑", "에게", "하고",
        "을", "를", "이", "가", "은", "는", "도", "로", "에", "의", "만", "과", "와", "랑",
    ],
    key=len, reverse=True,
)

# 패션 관련성 판단용 신호어와 제외어입니다.
FASHION_SIGNALS = [
    "코디", "룩북", "데일리룩", "착장", "ootd", "하울", "패션", "스타일링", "출근룩",
    "lookbook", "fashion", "outfit", "옷",
]
EXCLUDE_SIGNALS = [
    "메이크업", "화장품", "네일", "헤어", "다이어트", "먹방", "게임", "요리", "레시피",
    "주식", "부동산", "운동루틴", "홈트",
]

HANGUL = re.compile(r"[가-힣]")
HASHTAG = re.compile(r"#([^\s#]+)")
URL = re.compile(r"https?://\S+")
WORD_SPLIT = re.compile(r"[^0-9a-z가-힣]+")
LEADING_NUMBER = re.compile(r"^\d+(대|fw|ss|년|월)?")


def normalize(text):
    """비교용 표기: 소문자, 공백·기호 제거."""
    return re.sub(r"[^0-9a-z가-힣]", "", str(text).lower())


def _variant_table(groups):
    table = []
    for category, entries in groups.items():
        for canonical, variants in entries.items():
            for variant in variants:
                table.append((normalize(variant), canonical, category))
    # 긴 표기를 먼저 맞춰 '경량패딩'이 '패딩'보다 우선하게 합니다.
    return sorted(table, key=lambda row: len(row[0]), reverse=True)


ITEM_VARIANTS = _variant_table(ITEMS)
ATTRIBUTE_VARIANTS = _variant_table(ATTRIBUTES)


# =========================================================
# 토큰 분석
# =========================================================

def _strip_prefixes(token):
    changed = True
    while changed and token:
        changed = False
        stripped = LEADING_NUMBER.sub("", token)
        if stripped != token:
            token, changed = stripped, True
            continue
        for prefix in PREFIX_MODIFIERS:
            if token.startswith(prefix) and len(token) > len(prefix):
                token, changed = token[len(prefix):], True
                break
    return token


def _strip_suffixes(token, suffixes):
    changed = True
    while changed and token:
        changed = False
        for suffix in suffixes:
            if token.endswith(suffix) and len(token) > len(suffix):
                token, changed = token[: -len(suffix)], True
                break
    return token


def _split_attributes(text):
    """문자열 전체가 소재·색상·스타일 단어로만 이루어졌으면 목록을, 아니면 None을 반환합니다."""
    found = []
    while text:
        for variant, canonical, category in ATTRIBUTE_VARIANTS:
            if text.endswith(variant):
                found.insert(0, (category, canonical))
                text = text[: -len(variant)]
                break
        else:
            return None
    return found or None


def _is_unknown_candidate(token):
    if not 2 <= len(token) <= 15 or token.isdigit() or token in STOP_WORDS:
        return False
    if HANGUL.search(token):
        return ADJECTIVE_ENDINGS.search(token) is None
    # 영문 토큰은 3글자 이상이고 일반 영단어가 없는 것만 인정합니다(예: y2k, quietluxury).
    if any(part in token for part in ENGLISH_GENERIC_PARTS):
        return False
    return len(re.sub(r"[^a-z]", "", token)) >= 3


def parse_token(token, allow_unknown):
    """
    토큰 하나를 분석해 (카테고리, 키워드) 목록과 구조 정보를 반환합니다.

    allow_unknown=True는 태그·해시태그처럼 이미 덩어리로 나뉜 표현에만 사용합니다.
    """
    info = {"item": None, "bare_item": False, "attributes": None}
    text = normalize(token)
    if not text or text in STOP_WORDS:
        return [], info

    text = _strip_prefixes(_strip_suffixes(text, GENERIC_SUFFIXES))
    if not text or text in STOP_WORDS:
        return [], info

    for variant, canonical, category in ITEM_VARIANTS:
        if not text.endswith(variant):
            continue
        mentions = [(category, canonical)]
        info["item"] = (category, canonical)
        rest = _strip_prefixes(text[: -len(variant)])
        if not rest or rest in STOP_WORDS:
            info["bare_item"] = True
            return mentions, info
        attributes = _split_attributes(rest)
        if attributes:
            mentions += attributes
            words = " ".join(name for _, name in attributes)
            mentions.append((category, f"{words} {canonical}"))
        elif allow_unknown and _is_unknown_candidate(rest):
            mentions.append((category, f"{rest} {canonical}"))
        return mentions, info

    attributes = _split_attributes(text)
    if attributes:
        info["attributes"] = attributes
        return list(attributes), info

    if allow_unknown and _is_unknown_candidate(text):
        return [("미분류", text)], info

    return [], info


def _parse_prose(text):
    """문장 속 단어는 사전 단어만 인정하고, '소재/색상 + 아이템'이 띄어 쓰인 경우도 묶습니다."""
    mentions = []
    previous_attributes = None
    for word in WORD_SPLIT.split(str(text).lower()):
        if not word:
            continue
        found, info = parse_token(word, allow_unknown=False)
        if not found:
            stripped = _strip_suffixes(word, JOSA_SUFFIXES)
            if stripped != word:
                found, info = parse_token(stripped, allow_unknown=False)
        mentions += found

        if previous_attributes and info["item"] and info["bare_item"]:
            category, canonical = info["item"]
            words = " ".join(name for _, name in previous_attributes)
            mentions.append((category, f"{words} {canonical}"))
        previous_attributes = info["attributes"]
    return mentions


def _parse_tag(tag):
    """한 단어 태그는 덩어리로, 여러 단어 태그는 문장처럼 분석합니다."""
    if len(str(tag).split()) > 1:
        return _parse_prose(tag)
    return parse_token(tag, allow_unknown=True)[0]


def extract_mentions(video):
    """영상 한 개에서 (카테고리, 키워드) 집합을 추출합니다."""
    title = video.get("title", "") or ""
    description = video.get("description", "") or ""
    tags = video.get("tags", []) or []

    hashtags = HASHTAG.findall(title) + HASHTAG.findall(description)
    title_text = HASHTAG.sub(" ", title)
    description_text = HASHTAG.sub(" ", URL.sub(" ", description))[:DESCRIPTION_CHARS]

    mentions = []
    for tag in list(tags) + hashtags:
        mentions += _parse_tag(tag)
    mentions += _parse_prose(title_text)
    mentions += _parse_prose(description_text)
    return set(mentions)


# =========================================================
# 색상·소재 이름
# =========================================================

COLOR_NAMES = set(ATTRIBUTES["색상"])
MATERIAL_NAMES = set(ATTRIBUTES["소재"])


def color_group_keyword(color, category):
    """카테고리 단위 색상 키워드 표기입니다. 예: 브라운 (아우터 전체)"""
    return f"{color} ({category} 전체)"


# =========================================================
# 영상 필터
# =========================================================

def fashion_filter_reason(video):
    """수집에서 제외할 이유를 반환합니다. 통과하면 None입니다."""
    title = video.get("title", "") or ""
    tags = video.get("tags", []) or []
    head = title + " " + " ".join(tags)

    if not HANGUL.search(head):
        return "한글 없음"

    title_text = normalize(title)
    head_text = normalize(head)
    has_item = any(
        category not in ("미분류",)
        for category, _ in extract_mentions({"title": title, "tags": tags})
    )

    if any(word in title_text for word in EXCLUDE_SIGNALS) and not has_item:
        return "비의류 주제"
    if not has_item and not any(word in head_text for word in FASHION_SIGNALS):
        return "패션 신호 없음"
    return None


# =========================================================
# YouTube API
# =========================================================

class QuotaExceeded(RuntimeError):
    pass


def make_api_getter(api_key, retries=3):
    """API 키를 감춘 GET 함수를 만듭니다. 키는 출력하지 않습니다."""

    def get(endpoint, params):
        for attempt in range(retries):
            try:
                response = requests.get(
                    f"{API_URL}/{endpoint}",
                    params={**params, "key": api_key},
                    timeout=20,
                )
            except requests.RequestException:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("YouTube API에 연결하지 못했습니다.") from None

            if response.status_code == 200:
                return response.json()

            reason = ""
            try:
                reason = response.json()["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError, TypeError):
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise QuotaExceeded("YouTube API 일일 쿼터를 모두 사용했습니다.")
            if response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"YouTube {endpoint} 요청에 실패했습니다. HTTP {response.status_code} {reason}".strip()
            )
        raise RuntimeError(f"YouTube {endpoint} 요청에 실패했습니다.")

    return get


def _to_video(item, rank):
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    return {
        "video_id": item["id"],
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "tags": snippet.get("tags", []) or [],
        "channel_id": snippet.get("channelId", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "published_at": snippet.get("publishedAt", ""),
        "view_count": int(statistics.get("viewCount", 0) or 0),
        "url": f"https://www.youtube.com/watch?v={item['id']}",
        "rank": rank,
    }


def collect_videos(get, now=None, max_videos=MAX_VIDEOS, max_pages=MAX_PAGES):
    """
    조회수순 검색 결과를 페이지 단위로 받아 필터를 통과한 영상을 최대 max_videos개 모읍니다.

    get(endpoint, params) -> dict 형태의 함수를 받으므로 테스트에서 가짜 응답을 넣을 수 있습니다.
    """
    now = now or datetime.now(timezone.utc)
    published_after = (now - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
    published_before = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    videos, seen = [], set()
    stats = {"pages": 0, "searched": 0, "duplicates": 0, "excluded": {}}
    token = None

    for _ in range(max_pages):
        params = {
            "part": "id",
            "q": SEARCH_QUERY,
            "type": "video",
            "order": "viewCount",
            "regionCode": "KR",
            "relevanceLanguage": "ko",
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            "maxResults": 50,
        }
        if token:
            params["pageToken"] = token

        result = get("search", params)
        stats["pages"] += 1

        page_ids = []
        for item in result.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            stats["searched"] += 1
            if video_id in seen:
                stats["duplicates"] += 1
                continue
            seen.add(video_id)
            page_ids.append(video_id)

        if page_ids:
            details = get("videos", {"part": "snippet,statistics", "id": ",".join(page_ids)})
            by_id = {item["id"]: item for item in details.get("items", [])}
            # 검색 순서(조회수순)를 그대로 유지합니다.
            for video_id in page_ids:
                if video_id not in by_id:
                    continue
                video = _to_video(by_id[video_id], rank=len(seen))
                reason = fashion_filter_reason(video)
                if reason:
                    stats["excluded"][reason] = stats["excluded"].get(reason, 0) + 1
                    continue
                video["rank"] = len(videos) + 1
                videos.append(video)
                if len(videos) >= max_videos:
                    return videos, stats

        token = result.get("nextPageToken")
        if not token:
            break

    return videos, stats


# =========================================================
# 집계와 저장
# =========================================================

def build_candidates(videos, mentions_by_video, min_channels=MIN_CHANNELS):
    """
    키워드별 등장 채널을 모아 min_channels 이상인 후보만 반환합니다.

    mentions_by_video: video_id -> {(카테고리, 키워드), ...}
    """
    found = {}
    for video in videos:
        channel = video.get("channel_id") or video.get("channel_title", "")
        for category, keyword in mentions_by_video.get(video["video_id"], ()):
            # 띄어쓰기만 다른 표기는 같은 키워드로 봅니다. 표시는 처음 나온 표기를 씁니다.
            entry = found.setdefault((category, normalize(keyword)), {"keyword": keyword, "channels": {}})
            per_channel = entry["channels"]
            best = per_channel.get(channel)
            # 채널별 대표 영상은 그 채널에서 조회수가 가장 높은 영상입니다.
            if best is None or video["view_count"] > best["view_count"]:
                per_channel[channel] = video

    candidates = []
    for (category, _), entry in found.items():
        keyword, per_channel = entry["keyword"], entry["channels"]
        if len(per_channel) < min_channels:
            continue
        evidence = sorted(per_channel.values(), key=lambda v: (-v["view_count"], v["rank"]))
        candidates.append({
            "category": category,
            "keyword": keyword,
            "channels": len(per_channel),
            "links": [video["url"] for video in evidence],
        })

    return sorted(
        candidates,
        key=lambda c: (
            c["category"] == "미분류",
            -c["channels"],
            CATEGORY_ORDER.index(c["category"]),
            c["keyword"],
        ),
    )


def save_csv(candidates, path, collected_date=None):
    """collected_date: 맨 앞 수집일 열 값(YYYY-MM-DD). 없으면 오늘(한국 시간)."""
    collected_date = collected_date or datetime.now(KST).strftime("%Y-%m-%d")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_COLUMNS)
        for c in candidates:
            writer.writerow([
                collected_date,
                c["category"],
                c["keyword"],
                c["channels"],
                LINK_SEPARATOR.join(c["links"]),
                BASIS_TEXT,
            ])
    return path


# =========================================================
# 규칙 기반 추출 (색상·소재는 아이템에 묶음)
# =========================================================

# 설명란 상품 목록까지 읽습니다. 제목·설명 문장에서는 사전 단어만 인정하므로
# 범위를 넓혀도 새 노이즈는 적습니다. 패션 필터는 DESCRIPTION_CHARS(300자)를 씁니다.
RULE_DESCRIPTION_CHARS = 1500

MODIFIER_NAMES = COLOR_NAMES | MATERIAL_NAMES


def _split_modifiers(keyword):
    """'브라운 스웨이드 팬츠' → (['브라운', '스웨이드'], '팬츠'). 앞쪽의 색상·소재만 떼어냅니다."""
    words = keyword.split()
    count = 0
    while count < len(words) - 1 and words[count] in MODIFIER_NAMES:
        count += 1
    return words[:count], " ".join(words[count:])


def to_item_bound(raw_mentions):
    """
    규칙 추출 결과를 '아이템에 묶인 색상·소재' 기준으로 정리합니다.

    - 색상·소재 단독 행은 버립니다.
    - '브라운 스웨이드 팬츠'처럼 여럿이 붙으면 '브라운 팬츠', '스웨이드 팬츠'로 나눕니다.
    - 색상이 붙은 아이템은 카테고리 단위 색상(브라운 (하의 전체))도 추가합니다.
    """
    mentions = set()
    for category, keyword in raw_mentions:
        if category in ("색상", "소재"):
            continue
        modifiers, item = _split_modifiers(keyword)
        if not modifiers:
            mentions.add((category, keyword))
            continue
        mentions.add((category, item))
        for name in modifiers:
            mentions.add((category, f"{name} {item}"))
            if name in COLOR_NAMES and category != "미분류":
                mentions.add((category, color_group_keyword(name, category)))
    return mentions


def extract_rule_mentions(video):
    """영상 한 개에서 규칙 기반으로 (카테고리, 키워드) 집합을 추출합니다."""
    title = video.get("title", "") or ""
    description = video.get("description", "") or ""
    tags = video.get("tags", []) or []

    hashtags = HASHTAG.findall(title) + HASHTAG.findall(description)
    title_text = HASHTAG.sub(" ", title)
    description_text = HASHTAG.sub(" ", URL.sub(" ", description))[:RULE_DESCRIPTION_CHARS]

    raw = []
    for tag in list(tags) + hashtags:
        raw += _parse_tag(tag)
    raw += _parse_prose(title_text)
    raw += _parse_prose(description_text)
    return to_item_bound(raw)


def default_csv_path(now=None):
    stamp = (now or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y%m%d")
    return SAVE_DIR / "keyword_candidates" / f"keyword_candidates_{stamp}.csv"


# =========================================================
# 출력
# =========================================================

def _print_collection(videos, stats):
    print(f"[수집] 검색 {stats['pages']}페이지, 검색 결과 {stats['searched']}건, "
          f"중복 {stats['duplicates']}건 제외")
    if stats["excluded"]:
        reasons = ", ".join(f"{k} {v}건" for k, v in stats["excluded"].items())
        print(f"[필터] {reasons} 제외")
    print(f"[수집] 최종 {len(videos)}개 영상")
    if len(videos) < MAX_VIDEOS:
        print(f"[알림] 목표 {MAX_VIDEOS}개를 채우지 못했습니다. 실제 수집 {len(videos)}개로 집계합니다.")


def _print_candidates(candidates, limit):
    print(f"\n[{BASIS_TEXT}] 후보 {len(candidates)}개 (등장 채널 {MIN_CHANNELS}곳 이상)")
    for c in candidates[:limit]:
        print(f"  {c['category']:<6} {c['keyword']:<20} 채널 {c['channels']}  {c['links'][0]}")
    if len(candidates) > limit:
        print(f"  … 외 {len(candidates) - limit}개")


def main(argv=None):
    parser = argparse.ArgumentParser(description="YouTube 패션 키워드 후보 수집 (규칙 기반)")
    parser.add_argument("--preview", action="store_true", help="저장하지 않고 결과 일부만 출력")
    args = parser.parse_args(argv)

    load_env()
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env(루트 또는 env/)에 YOUTUBE_API_KEY를 설정해 주세요.")

    now = datetime.now(timezone.utc)
    videos, stats = collect_videos(make_api_getter(api_key), now=now)
    _print_collection(videos, stats)

    print("\n[추출] 규칙 기반")
    mentions_by_video = {video["video_id"]: extract_rule_mentions(video) for video in videos}
    candidates = build_candidates(videos, mentions_by_video)

    if args.preview:
        print("\n[미리보기] 수집 영상 상위 10개 (조회수순)")
        for v in videos[:10]:
            print(f"  {v['rank']:>3}. 조회 {v['view_count']:>10,}  {v['channel_title'][:15]:<15}  {v['title'][:45]}")
        _print_candidates(candidates, limit=20)
        print("\n미리보기 모드라 CSV를 저장하지 않았습니다.")
        return

    path = save_csv(candidates, default_csv_path(now), now.astimezone(KST).strftime("%Y-%m-%d"))
    _print_candidates(candidates, limit=10)
    print(f"\n[저장] {path}")


if __name__ == "__main__":
    from _collector_lock import hold  # 예약·수동 실행이 겹치지 않도록 (이미 실행 중이면 종료 코드 75로 건너뜀)
    hold("youtube_candidates")
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
