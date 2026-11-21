"""Conservative, opt-in topic change confirmation for CART_SIGNAL Slack."""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / 'runtime' / 'analysis_topic_guard.sqlite'
_LOCK = threading.RLock()

PRODUCT_CATEGORIES = {
    '니트': ('니트', '스웨터', 'knitwear', 'knit', 'sweater'),
    '아우터': ('아우터', '재킷', '자켓', '점퍼', '코트', '패딩', 'outerwear', 'jacket', 'coat', 'blazer'),
    '바지': ('바지', '팬츠', '청바지', '데님', '트라우저', 'trousers', 'pants', 'jeans'),
    '치마': ('치마', '스커트', 'skirt'),
    '원피스': ('원피스', '드레스', 'dress'),
    '셔츠': ('셔츠', '블라우스', 'shirt', 'blouse'),
    '티셔츠': ('티셔츠', '티 샤츠', 't-shirt', 'tshirt', 'tee'),
    '신발': ('신발', '슈즈', '운동화', '스니커즈', 'shoes', 'sneakers'),
    '가방': ('가방', '백팩', '핸드백', 'bag', 'backpack'),
    '액세서리': ('액세서리', '악세서리', '모자', '스카프', 'accessories'),
}

FOLLOWUP = re.compile(r'방금|직전|이전|그것|그거|이거|해당|위의|위에|같은\s*조건|그대로|이어서|추가로|계속|비교|차이|함께|동시에', re.I)
NON_ANALYSIS = re.compile(r'보고서|리포트|워드|word|pdf|승인|반려|취소|새\s*분석\s*시작', re.I)
ANALYSIS = re.compile(r'분석|구매|매출|판매|순위|top\s*\d+|상위\s*\d+|추천|집계|통계', re.I)
YES = {'예', '네', '넵', '응', 'ㅇㅇ', '좋아', '좋아요', '확인', '전환', '새 세션', '새 분석', '진행'}
NO = {'아니', '아니요', '아뇨', '유지', '기존', '그대로', '취소', '현재 세션'}


def _connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=10)
    con.execute('CREATE TABLE IF NOT EXISTS topics (base_thread_id TEXT PRIMARY KEY, question TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS pending (base_thread_id TEXT PRIMARY KEY, question TEXT NOT NULL)')
    return con


def _labels(text):
    low = text.lower()
    genders = set()
    if re.search(r'여성|여자|ladieswear|womenswear|women\b', low):
        genders.add('여성')
    if re.search(r'남성|남자|menswear|men\b', low):
        genders.add('남성')
    cats = set()
    for group, words in PRODUCT_CATEGORIES.items():
        if any(word in low for word in words):
            cats.add(group)
    return genders, cats


def distinct_topic(previous, incoming):
    """Only flag unambiguous independent subject changes; no LLM guessing."""
    if not previous or not ANALYSIS.search(incoming) or NON_ANALYSIS.search(incoming):
        return False
    if FOLLOWUP.search(incoming):
        return False
    old_gender, old_cats = _labels(previous)
    new_gender, new_cats = _labels(incoming)
    if len(new_gender) > 1 or len(new_cats) > 1:
        return False  # multi-subject comparison is not a new independent subject
    if old_gender and new_gender and old_gender.isdisjoint(new_gender):
        return True
    if old_cats and new_cats and old_cats.isdisjoint(new_cats):
        return True
    return False


def before_turn(base_thread_id, question):
    """Return (action, text): normal / ask / switch / stay / cancel."""
    clean = (question or '').strip()
    with _LOCK, _connect() as con:
        pending = con.execute('SELECT question FROM pending WHERE base_thread_id=?', (base_thread_id,)).fetchone()
        if pending:
            if clean.lower() in YES:
                con.execute('DELETE FROM pending WHERE base_thread_id=?', (base_thread_id,))
                return 'switch', pending[0]
            if clean.lower() in NO:
                con.execute('DELETE FROM pending WHERE base_thread_id=?', (base_thread_id,))
                return 'stay', pending[0]  # execute original request in current session
            return 'ask', pending[0]
        row = con.execute('SELECT question FROM topics WHERE base_thread_id=?', (base_thread_id,)).fetchone()
        if row and distinct_topic(row[0], clean):
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?)', (base_thread_id, clean))
            return 'ask', clean
        return 'normal', clean


def remember(base_thread_id, question):
    """Record only completed analysis inputs; report and small follow-ups never replace anchor."""
    if not ANALYSIS.search(question) or NON_ANALYSIS.search(question) or FOLLOWUP.search(question):
        return
    if not (any(_labels(question))):
        return
    with _LOCK, _connect() as con:
        con.execute('INSERT OR REPLACE INTO topics VALUES (?, ?)', (base_thread_id, question))


def reset(base_thread_id):
    with _LOCK, _connect() as con:
        con.execute('DELETE FROM topics WHERE base_thread_id=?', (base_thread_id,))
        con.execute('DELETE FROM pending WHERE base_thread_id=?', (base_thread_id,))


def abandon_pending(base_thread_id):
    with _LOCK, _connect() as con:
        con.execute('DELETE FROM pending WHERE base_thread_id=?', (base_thread_id,))
