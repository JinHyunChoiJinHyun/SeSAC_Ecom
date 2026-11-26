"""무신사 크롤러 - 주간 랭킹 상품과 리뷰를 모아 CSV로 저장합니다.

RANKING_URLS 에 넣은 링크마다 아래 순서로 실행하고, 모든 링크를 종류별 파일 3개에 합쳐 저장합니다.
맨 앞 collected_date 열에 수집일(YYYY-MM-DD), 그다음 category 열에 링크 이름(name)이 들어갑니다.
저장 폴더는 실행한 날짜(한국 시간) 폴더입니다: data/musinsa/YYYYMMDD/
(주 1회 수집할 때 지난 기록이 덮어써지지 않도록. 날짜는 시작할 때 한 번 정하므로 자정을 넘겨도 같은 폴더)
  1) crawl_ranking(url, name) -> data/musinsa/YYYYMMDD/ranking.csv       (순위·브랜드·상품명·가격)
  2) crawl_reviews(name)      -> data/musinsa/YYYYMMDD/review.csv        (작성자 체형·사이즈·만족도·본문)
                              + data/musinsa/YYYYMMDD/product_info.csv  (제품 성별·별점·리뷰 수·이미지)
     product_info 는 리뷰 페이지 안의 상품 정보에서 함께 읽습니다(상품 상세페이지는 봇 차단으로 막힘).
2번은 같은 날짜 폴더의 ranking.csv 에서 category 가 같은 행의 item_id 를 사용합니다.
저장할 때는 그 링크(category)의 기존 행만 지우고 새로 모은 행으로 바꾸므로,
같은 날 링크 하나만 다시 돌려도 다른 링크의 데이터는 그대로 남고 중복으로 쌓이지 않습니다.
※ 실행 중에는 합친 CSV를 엑셀로 열어두지 마세요(저장이 실패합니다).
링크는 랭킹 페이지와 검색 결과 페이지 모두 됩니다.

성별 컬럼은 두 개이고 의미가 다릅니다.
  product_info.product_gender = 제품 성별(남/여/공용), review.reviewer_gender = 리뷰 작성자 성별(남성/여성)

실행 (external_data 폴더에서): python musinsa_crawler.py
종료 코드: 모든 링크 성공이면 0, 실패한 링크가 있으면 1

※ 2026-11 기준 무신사 화면 구조에 맞춤. 무신사는 class 이름이 자주 바뀌므로
   값이 None 으로 나오면 SELECTORS 를 개발자도구(F12)로 다시 확인하세요.
   (가능한 한 class 대신 data-* 속성 / 컴포넌트 이름으로 찾도록 해둠)
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import time

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from urllib3.exceptions import HTTPError as DriverConnectionError
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# 실행 위치와 관계없이 이 파일이 있는 프로젝트 폴더 기준 (작업 스케줄러로 실행해도 같은 곳에 저장)
KST = timezone(timedelta(hours=9))
_STARTED = datetime.now(KST)
RUN_DATE = _STARTED.strftime("%Y%m%d")              # 실행 날짜 = 저장 폴더 이름
COLLECTED_DATE = _STARTED.strftime("%Y-%m-%d")      # 세 파일 맨 앞 collected_date 열 값 (폴더 이름과 같은 날)
OUT_DIR = Path(__file__).resolve().parent / "data" / "musinsa" / RUN_DATE
PAGE_LOAD_TIMEOUT = 110  # 페이지 하나를 여는 데 기다리는 최대 시간(초). Selenium 응답 한계(120초)보다 짧아야 함

# 크롤링할 링크 목록: {이름: 링크}. 이름이 합친 CSV의 category 열 값이 됩니다.
# 이름에는 \ / : * ? " < > | 문자를 넣지 마세요.
RANKING_URLS = {
    "knit_sweater" : "https://www.musinsa.com/main/musinsa/ranking?gf=A&storeCode=musinsa&sectionId=199&contentsId=&categoryCode=001006&ageBand=AGE_BAND_ALL&subPan=product&period=WEEKLY",
    "short_sleeve_tee" : "https://www.musinsa.com/main/musinsa/ranking?gf=A&storeCode=musinsa&sectionId=199&contentsId=&categoryCode=001001&ageBand=AGE_BAND_ALL&subPan=product&period=WEEKLY",
    "long_sleeve_tee" : "https://www.musinsa.com/main/musinsa/ranking?gf=A&storeCode=musinsa&sectionId=199&contentsId=&categoryCode=001010&ageBand=AGE_BAND_ALL&subPan=product&period=WEEKLY",
    "shirt_blouse" : "https://www.musinsa.com/main/musinsa/ranking?gf=A&storeCode=musinsa&sectionId=199&contentsId=&categoryCode=001002&ageBand=AGE_BAND_ALL&subPan=product&period=WEEKLY",
    "outer" : "https://www.musinsa.com/main/musinsa/ranking?gf=A&storeCode=musinsa&sectionId=199&contentsId=&categoryCode=002000&ageBand=AGE_BAND_ALL&subPan=product&period=WEEKLY"
}

INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def out_path(kind, name):
    """kind: 'ranking' / 'product_info' / 'review' 의 합친 파일 경로. name 은 이름 검사에만 씁니다."""
    if not str(name).strip() or INVALID_NAME_CHARS.search(str(name)):
        raise ValueError(f"쓸 수 없는 링크 이름입니다: {name!r}")
    return OUT_DIR / f"{kind}.csv"


def read_category(path: Path, name):
    """합친 CSV에서 category 가 name 인 행만 읽는다. 파일이 없으면 None."""
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"category": str, "item_id": str, "review_id": str})
    return df[df["category"] == str(name).strip()]

RANKING_LIST_ID = "ranking_goods_list"   # 랭킹 페이지에서 '랭킹 목록' 카드의 data-item-list-id 값

SELECTORS = {
    # 상품 카드: 이름/가격/순위는 카드의 data-* 속성에 들어있음
    # (랭킹 페이지는 <div>, 검색 페이지는 <a> 라서 태그는 지정하지 않음)
    "rank_card": ".gtm-view-item-list[data-item-id]",
    # (랭킹 목록 카드만 data-item-list-id="ranking_goods_list" → RANKING_LIST_ID 로 걸러냄)
    "rank_brand": "a[href*='/brand/']",
    # 리뷰 페이지
    "review_item": "[class*='review-list-item__Container']",
    "review_option_row": "[class*='OptionRow__Container']",
    # MeasureBox = 화면 길이 계산용 숨김 영역으로, '더보기' 안 눌러도 리뷰 전문이 들어있음.
    # 첫 번째 span = 본문, 두 번째 span(data-truncate-more) = 숨겨진 '... 더보기' 버튼
    "review_text": "[class*='TruncateRewrite__MeasureBox'] > span:not([data-truncate-more])",
}


def make_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,2000")
    options.add_argument("--log-level=3")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    # Selenium 4.6+ 는 크롬드라이버를 자동으로 받아오므로 webdriver_manager 불필요
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


class Browser:
    """리뷰 수집용 크롬. 페이지가 막히면 건너뛰고, 크롬이 멈추면 새로 띄웁니다."""

    def __init__(self):
        self.driver = make_driver()

    def restart(self):
        self.quit()
        self.driver = make_driver()

    def quit(self):
        try:
            self.driver.quit()
        except (WebDriverException, DriverConnectionError):
            pass

    def open(self, url):
        """페이지를 엽니다. 실패하면 False (호출한 쪽에서 그 상품을 건너뜀)."""
        for _ in range(2):
            try:
                self.driver.get(url)
                return True
            except TimeoutException:
                print(f"[시간 초과] {PAGE_LOAD_TIMEOUT}초 안에 열리지 않음: {url}")
                return False
            except (WebDriverException, DriverConnectionError) as error:
                # 크롬 자체가 응답하지 않음 → 새로 띄우고 한 번 더 시도
                print(f"[크롬 재시작] {type(error).__name__}: {url}")
                self.restart()
        return False


def text_of(parent, selector):
    el = parent.select_one(selector)
    return el.get_text(strip=True) if el else None


def save_csv(rows, path: Path, name):
    """합친 CSV에서 이 링크(category=name)의 기존 행을 지우고 새 행으로 바꿔 저장한다.
    반환값은 이번 링크의 행만 담은 DataFrame."""
    path.parent.mkdir(parents=True, exist_ok=True)
    category = str(name).strip()
    df = pd.DataFrame(rows)
    df.insert(0, "category", category)
    df.insert(0, "collected_date", COLLECTED_DATE)   # 여러 주 파일을 합쳐도 수집일을 알 수 있게
    if path.exists():
        old = pd.read_csv(path, dtype={"category": str, "item_id": str, "review_id": str})
        combined = pd.concat([old[old["category"] != category], df], ignore_index=True)
    else:
        combined = df
    combined.to_csv(path, index=False, encoding="utf-8-sig")  # 엑셀에서 한글 안 깨지게
    print(f"[저장] {path} ({category} {len(df)}행 / 전체 {len(combined)}행)")
    return df


def load_item_ids(name, limit=None):
    ranking_csv = out_path("ranking", name)
    df = read_category(ranking_csv, name)
    if df is None or df.empty:
        raise FileNotFoundError(f"{ranking_csv} 에 '{name}' 행이 없습니다. crawl_ranking(url, '{name}') 을 먼저 실행하세요.")
    ids = df["item_id"].tolist()
    return ids[:limit] if limit else ids


def card_container(card, max_up=6):
    """카드를 감싸는 가장 큰 상자(상품 1개만 들어있는 범위)를 찾는다.
    검색 페이지는 브랜드 이름이 <a> 카드 바깥 형제 요소에 있어서 필요."""
    box = card
    for _ in range(max_up):
        parent = box.parent
        if parent is None or len(parent.select(SELECTORS["rank_card"])) > 1:
            break
        box = parent
    return box


def scroll_step(driver, pause):
    """화면 한 칸 정도 스크롤. 무신사 리뷰/랭킹은 가상 스크롤이라
    맨 아래로 한 번에 점프하면 중간 항목을 건너뛰므로 조금씩 내린다."""
    driver.execute_script("window.scrollBy(0, window.innerHeight * 0.8);")
    time.sleep(pause)


POPUP_SKIP_TEXT = "오늘 그만 보기"   # 행사 소개 화면의 닫기 버튼 문구 (행사마다 바뀌면 여기만 수정)


def close_event_popup(driver, wait=10):
    """행사 소개 화면의 '오늘 그만 보기' 버튼이 보이면 누른다. 눌렀으면 True."""
    try:
        WebDriverWait(driver, wait).until(
            lambda d: any(b.is_displayed() for b in d.find_elements(By.XPATH, f"//button[contains(., '{POPUP_SKIP_TEXT}')]"))
        )
        button = next(b for b in driver.find_elements(By.XPATH, f"//button[contains(., '{POPUP_SKIP_TEXT}')]")
                      if b.is_displayed())
        button.click()
        time.sleep(3)
        return True
    except (TimeoutException, StopIteration):
        return False


# 1) 랭킹 TOP N: item_id, 순위, 브랜드, 상품명, 가격
# 페이지가 안 뜨거나 크롬이 멈추면 None 을 돌려주고 랭킹 파일은 저장하지 않음(예전 결과 보존)
def crawl_ranking(url, name, top_n=100, scroll_pause=1.0, max_idle_scrolls=8, card_wait=60):
    driver = make_driver()
    rows = {}  # item_id -> row (순서 유지 + 중복 제거)
    is_ranking = "/ranking" in url   # 랭킹 링크일 때만 아래 검사(검색 결과 링크는 그대로)
    try:
        driver.get(url)
        # 행사 기간에는 랭킹 링크가 행사 페이지로 넘어가기도 함(예: 2026-11 무진장 블랙프라이데이)
        # → '오늘 그만 보기'를 누르면 그 창에서는 랭킹이 열리므로, 누르고 원래 링크를 다시 엶
        #   (버튼을 누르면 전체 카테고리 랭킹으로 가서 다시 열어야 함)
        if is_ranking and "/ranking" not in driver.current_url and close_event_popup(driver):
            print(f"[팝업] {name}: '{POPUP_SKIP_TEXT}' 누르고 랭킹 다시 열기")
            driver.get(url)
        # 그래도 다른 페이지면 실패로 처리(행사 배너 상품을 랭킹으로 저장하지 않도록)
        if is_ranking and "/ranking" not in driver.current_url:
            print(f"[건너뜀] {name}: 랭킹 페이지가 아닌 곳으로 이동됨 → {driver.current_url}")
            return None
        WebDriverWait(driver, card_wait).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["rank_card"]))
        )

        idle = 0
        while len(rows) < top_n and idle < max_idle_scrolls:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            before = len(rows)

            for card in soup.select(SELECTORS["rank_card"]):
                item_id = card.get("data-item-id")
                if not item_id or item_id in rows:
                    continue
                # 랭킹 페이지 안의 다른 상품 묶음(행사 배너, 추천 영역 등)은 건너뜀
                if is_ranking and card.get("data-item-list-id") != RANKING_LIST_ID:
                    continue
                # 상품명은 data-rosetta-props(JSON)에 있음
                # (랭킹 페이지: 카드 안쪽 요소 / 검색 페이지: 카드 자신)
                item_name = None
                props_el = card if card.has_attr("data-rosetta-props") else card.select_one("[data-rosetta-props]")
                if props_el:
                    try:
                        item_name = json.loads(props_el["data-rosetta-props"])["items"][0]["item_name"]
                    except (KeyError, IndexError, ValueError):
                        pass
                rows[item_id] = {
                    "item_id": item_id,
                    "ranking": card.get("data-item-list-index"),
                    "brand": text_of(card_container(card), SELECTORS["rank_brand"]),
                    "brand_id": card.get("data-item-brand"),
                    "name": item_name,
                    "price": card.get("data-price"),
                    "best_price": card.get("data-best-price"),
                    "original_price": card.get("data-original-price"),
                    "discount_rate": card.get("data-discount-rate"),
                }
                print(rows[item_id])

            idle = 0 if len(rows) > before else idle + 1
            scroll_step(driver, scroll_pause)

        if len(rows) < top_n:
            print(f"[경고] {len(rows)}개까지만 로드됨")
    except (WebDriverException, DriverConnectionError) as error:
        # TimeoutException(카드 대기 / 110초 페이지 제한)도 WebDriverException 의 한 종류
        print(f"[건너뜀] {name}: 랭킹 페이지 로딩 실패 ({type(error).__name__})")
        return None
    finally:
        try:
            driver.quit()
        except (WebDriverException, DriverConnectionError):
            pass

    if not rows:
        # 페이지는 열렸지만 상품을 하나도 못 읽음(차단 화면, 구조 변경 등) → 실패로 처리하고 저장하지 않음
        print(f"[건너뜀] {name}: 랭킹 상품을 하나도 읽지 못함 (기존 데이터는 그대로 둠)")
        return None
    ranked = sorted(rows.values(), key=lambda r: int(r["ranking"] or 10**9))[:top_n]
    return save_csv(ranked, out_path("ranking", name), name)


def parse_review_item(item):
    """리뷰 1개 → dict. 옵션 영역은 '라벨/값' 쌍의 행들로 되어 있음:
    상품명 / 구매옵션('색상 · L') / 체형정보('여성 · 163cm · 58kg') / 만족도"""
    info = {}
    for row in item.select(SELECTORS["review_option_row"]):
        spans = row.find_all("span", recursive=False)
        if len(spans) >= 2:
            info[spans[0].get_text(strip=True)] = spans[1].get_text(strip=True)

    reviewer_gender = height = weight = None
    body = [p.strip() for p in info.get("체형정보", "").split("·") if p.strip()]
    for p in body:
        if p.endswith("cm"):
            height = p[:-2]
        elif p.endswith("kg"):
            weight = p[:-2]
        else:
            reviewer_gender = p

    option = info.get("구매옵션")
    size = option.split("·")[-1].strip() if option else None

    text_el = item.select_one(SELECTORS["review_text"])
    review = text_el.get_text(" ", strip=True) if text_el else None
    if review:
        review = re.sub(r"\s*\.\.\.\s*더보기$", "", review)

    return {
        "review_id": item.get("data-content-id"),
        "reviewer_gender": reviewer_gender,
        "height": height,
        "weight": weight,
        "option": option,
        "size": size,
        "satisfaction": info.get("만족도"),
        "review": review,
    }


# 3) 리뷰: 체형정보/사이즈(사이즈 추천용) + 본문(긍부정 분석용)
NO_REVIEW_TEXT = "등록된 후기가 없습니다"


def load_review_counts(name):
    """product_info.csv 에서 같은 링크(category)의 상품별 리뷰 수를 읽는다. 파일이나 값이 없으면 빈 dict."""
    df = read_category(out_path("product_info", name), name)
    if df is None or "review_count" not in df.columns:
        return {}
    return {i: int(n) for i, n in zip(df["item_id"], df["review_count"]) if pd.notna(n)}


GENDER_TEXT = {"남성": "남", "여성": "여"}  # 제품 성별은 남/여/공용으로 짧게 저장


def goods_info_from_review_page(soup, item_id):
    """리뷰 페이지 안 상품 정보(__NEXT_DATA__ 의 goodsData)에서 product_info 값을 읽는다.
    성별은 '제품' 성별(리뷰 작성자 성별 아님)."""
    goods = {}
    script = soup.select_one("script#__NEXT_DATA__")
    if script:
        try:
            data = json.loads(script.get_text())
            goods_list = data["props"]["pageProps"]["goodsData"]["data"]["list"] or []
            goods = next((g for g in goods_list if str(g.get("goodsNo")) == str(item_id)), {})
        except (ValueError, KeyError, TypeError):
            goods = {}

    product_gender = goods.get("displayGenderText")
    product_gender = GENDER_TEXT.get(product_gender, product_gender)

    # 별점: 화면의 별점 영역 data-score(예: 4.9) → 없으면 reviewScore(100점 환산, 예: 98)를 5점으로 바꿈
    rating = None
    star = soup.select_one("[data-testid='review-star-score']")
    if star and star.get("data-score"):
        rating = star["data-score"]
    elif goods.get("reviewScore") is not None:
        rating = round(goods["reviewScore"] / 20, 1)

    return {
        "item_id": item_id,
        "product_gender": product_gender,
        "rating": rating,
        "review_count": goods.get("reviewCount"),
        "img": goods.get("imageUrl"),
    }


def review_list_or_empty(driver):
    """리뷰 목록이 보이면 'list', '등록된 후기가 없습니다'가 보이면 'empty', 둘 다 아니면 False(계속 기다림)."""
    if driver.find_elements(By.CSS_SELECTOR, SELECTORS["review_item"]):
        return "list"
    if driver.find_elements(By.XPATH, f"//*[contains(text(), '{NO_REVIEW_TEXT}')]"):
        return "empty"
    return False


def crawl_reviews(name, limit=None, max_reviews_per_item=200, scroll_pause=1.0, max_idle_scrolls=6):
    """리뷰를 모으면서, 같은 리뷰 페이지에 있는 상품 정보(제품 성별·별점·리뷰 수·이미지)도 product_info 로 저장한다.
    (상품 상세페이지는 봇 차단으로 막혀서 리뷰 페이지에서 함께 읽음)"""
    # 상품 목록을 먼저 읽음. 여기서 실패하면 아무것도 저장하지 않고 멈춤(기존 product_info·review 보존)
    item_ids = load_item_ids(name, limit)
    # 리뷰 수: 리뷰 페이지에서 읽은 값을 먼저 쓰고, 없으면 기존 product_info 파일의 값을 씀
    # (둘 다 없으면 빈 스크롤 6번 뒤 멈춤)
    review_counts = load_review_counts(name)
    browser = Browser()
    rows = []
    info_rows = []
    try:
        for n, item_id in enumerate(item_ids):
            if n:
                time.sleep(1)   # 상품 사이 1초 쉼 (사이트 부담 줄이기)
            if not browser.open(f"https://www.musinsa.com/review/goods/{item_id}"):
                print(f"[건너뜀] {item_id}: 리뷰 페이지 열기 실패")
                continue
            try:
                state = WebDriverWait(browser.driver, 15).until(review_list_or_empty)
            except TimeoutException:
                print(f"[로딩 실패] {item_id}: 15초 안에 리뷰 목록이 뜨지 않음")
                continue

            try:
                info = goods_info_from_review_page(BeautifulSoup(browser.driver.page_source, "html.parser"), item_id)
            except (WebDriverException, DriverConnectionError) as error:
                print(f"[건너뜀] {item_id}: 페이지 읽기 실패 ({type(error).__name__}) → 크롬 재시작")
                browser.restart()
                continue
            info_rows.append(info)
            print(info)

            if state == "empty":
                print(f"[리뷰 없음] {item_id}")
                continue

            target = max_reviews_per_item
            count = info["review_count"] if info["review_count"] is not None else review_counts.get(item_id)
            if count is not None:
                target = min(target, int(count))

            seen = set()
            idle = 0
            try:
                while len(seen) < target and idle < max_idle_scrolls:
                    soup = BeautifulSoup(browser.driver.page_source, "html.parser")
                    before = len(seen)
                    for item in soup.select(SELECTORS["review_item"]):
                        if len(seen) >= target:
                            break
                        r = parse_review_item(item)
                        if not r["review_id"] or r["review_id"] in seen:
                            continue
                        seen.add(r["review_id"])
                        rows.append({"item_id": item_id, **r})
                        print(item_id, r["reviewer_gender"], r["height"], r["weight"], r["size"], (r["review"] or "")[:30])
                    idle = 0 if len(seen) > before else idle + 1
                    if len(seen) < target:
                        scroll_step(browser.driver, scroll_pause)
            except (WebDriverException, DriverConnectionError) as error:
                # 스크롤 중 크롬이 멈추면 지금까지 모은 리뷰는 두고 다음 상품으로
                print(f"[중단] {item_id}: 스크롤 중 오류 ({type(error).__name__}) → 크롬 재시작")
                browser.restart()
            print(f"[{item_id}] 리뷰 {len(seen)}개 (목표 {target}개)")
    finally:
        browser.quit()
        # 중간에 오류로 멈춰도 그때까지 모은 행은 저장
        save_csv(info_rows, out_path("product_info", name), name)
        df = save_csv(rows, out_path("review", name), name)
    return df


if __name__ == "__main__":
    for name in RANKING_URLS:
        out_path("ranking", name)   # 이름이 잘못됐으면 크롤링 시작 전에 멈춤
    failed = []
    for i, (name, url) in enumerate(RANKING_URLS.items(), start=1):
        print(f"\n===== [{i}/{len(RANKING_URLS)}] {name}: {url}")

        if crawl_ranking(url, name, top_n=100) is None:
            # 랭킹이 없으면 리뷰도 건너뜀 (예전 랭킹 파일로 수집하지 않도록)
            failed.append(name)
            continue
        # product_info.csv 는 리뷰 단계에서 리뷰 페이지의 상품 정보로 함께 저장됨.
        try:
            crawl_reviews(name, max_reviews_per_item=30)
        except Exception as error:   # 한 링크의 리뷰가 실패해도 다음 링크는 계속
            print(f"[오류] {name} 리뷰: {type(error).__name__} {error}")
            failed.append(name)

    total = len(RANKING_URLS)
    print(f"\n[완료] {total}개 중 {total - len(failed)}개 성공" + (f" / 실패: {', '.join(failed)}" if failed else ""))
    raise SystemExit(1 if failed else 0)   # 작업 스케줄러·로그에서 실패를 알 수 있도록