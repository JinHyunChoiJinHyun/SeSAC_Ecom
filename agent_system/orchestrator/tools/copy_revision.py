"""사용자 표현 피드백을 반영한 재현 가능한 문구 3안과 근거 검사.

외부 LLM 사용 없이 승인된 상품과 소구점만 사용하는 안전한 폴백.
독립 생성 모델 기반 의미 검증(Validation Agent)은 연결 전까지 주장하지 않는다.
"""
from __future__ import annotations

import re

_BANNED = re.compile(r'(?i)(?:\d+\s*%\s*할인|무료\s*배송|최저가|1\s*위|판매량\s*1위|최고의|완벽한\s*보온|방수|보온성\s*보장|100\s*%|무조건|당일\s*배송|특가|품질\s*보장)')


def is_copy_reset_request(message):
    """명시적인 카피 수정 이력 초기화만 허용한다. 전체 워크플로 초기화와 구별."""
    text = re.sub(r'\s+', ' ', str(message or '')).strip()
    return bool(re.fullmatch(
        r'(?:카피|문구|대표 문구)\s*(?:수정\s*)?(?:이력\s*)?(?:초기화|리셋|원본으로 복구)(?:해\s*줘|해주세요|할게)?[.!]?',
        text,
    ))


def parse_revision_request(message):
    """선택보다 재작성 의도를 우선 확인한다. 숫자만 있는 선택은 보존한다."""
    text = str(message or '').strip()
    if not text or len(text) > 400:
        return None
    if re.search(r'(다시\s*(?:3|세)\s*안|다시\s*추천|다른\s*문구|재생성|수정|고쳐|바꿔|다듬|반영|느낌|분위기|문구.*(?:만들|추천)|글자\s*이내|영어\s*없이|한글로|짧게|간결|감성|세련|차분|발랄|톤|메모.*분리|카피.*빼)', text):
        return text
    return None


def _numbered_instructions(text):
    """'1안은 따뜻하게, 3안은 짧게, 2안은 유지'의 독립 지시를 추출."""
    matches = list(re.finditer(r'(?<!\d)([123])\s*(?:번|안)(?!\d)', text))
    result = {}
    for i, match in enumerate(matches):
        # 다음 번호까지의 구간에서 대상별 지시를 분리한다.
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        clause = text[match.end():end].strip(' ,，·/과랑은를을 ')
        result[int(match.group(1))] = clause
    return result


def parse_revision_targets(feedback):
    """명시적인 '유지' 대상은 수정하지 않으며, 숫자가 없으면 전체 수정."""
    text = str(feedback or '')
    instructions = _numbered_instructions(text)
    if re.search(r'전체|모두|전부|세\s*개\s*다|세\s*안\s*다|3\s*개\s*다', text) and not instructions:
        return [1, 2, 3]
    # 쉼표로 묶인 '1,3안' 및 '1안과 3안' 지원
    hits = set(instructions)
    for match in re.finditer(r'(?<!\d)([123](?:\s*[,，·/]\s*[123])+)(?:\s*안)?', text):
        if re.search(r'서브\s*카피\s*$', text[max(0, match.start()-9):match.start()]):
            continue
        hits.update(int(x) for x in re.findall(r'[123]', match.group(1)))
    for phrase, number in [(r'첫\s*번째', 1), (r'두\s*번째', 2), (r'세\s*번째', 3)]:
        if re.search(phrase, text):
            hits.add(number)
    # 구체적으로 보존하라는 번호를 제외한다.
    preserved = {n for n, clause in instructions.items()
                 if re.search(r'(그대로|유지|건드리지|바꾸지\s*마|수정하지\s*마)', clause)}
    return sorted(hits - preserved) if hits else [1, 2, 3]


def _feedback_profile(feedback):
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 400:
        raise ValueError('문구 수정 요청은 400자 이내로 알려주세요.')
    if _BANNED.search(feedback):
        raise ValueError('검증되지 않은 할인·성능·순위 표현은 문구에 사용할 수 없습니다.')
    short = bool(re.search(r'짧게|짧고|간결|20\s*자\s*이내|15\s*자\s*이내', feedback))
    emotional = bool(re.search(r'감성|세련|따뜻|일상|나다운|나만의', feedback))
    korean = bool(re.search(r'영어\s*없이|한글로|영문\s*빼|영문\s*없이', feedback))
    # 인용 문구는 상품 성능 등 확인 불가 사실을 새로 주장하지 않는 경우에만 참조한다.
    quoted = re.search(r'["“‘\']([^"”’\']{3,40})["”’\']', feedback)
    phrase = quoted.group(1).strip() if quoted else ''
    if phrase and (_BANNED.search(phrase) or re.search(r'\d+\s*%', phrase)):
        raise ValueError('참고 문구에 확인되지 않은 혜택·성능 표현이 있습니다.')
    return short, emotional, korean, phrase


def regenerate_copy(base, feedback, previous_revision=0):
    """지정 안만 재작성한다. 변경 이력은 그대로 재생 가능한 형태로 보관한다."""
    from copy import deepcopy
    # 입력 전체의 안전 검사는 유지하되, 수정 안별 스타일은 독립 적용한다.
    _feedback_profile(feedback)
    if base.get('status') not in ('draft', 'verified') or len(base.get('candidates') or []) != 3:
        raise ValueError('검증된 기존 문구 3안이 필요합니다.')
    if not isinstance(previous_revision, int) or not 0 <= previous_revision <= 30:
        raise ValueError('문구 수정 횟수 정보가 올바르지 않습니다.')
    if previous_revision >= 30:
        raise ValueError('문구 수정은 최대 30회까지 지원합니다.')
    base = deepcopy(base)
    targets = parse_revision_targets(feedback)
    if not targets:
        raise ValueError('모든 안을 유지하도록 요청했습니다. 수정할 안을 지정해 주세요.')
    subtype = base['subtype']
    colors = base['colors']
    instructions = _numbered_instructions(feedback)
    from agent_system.orchestrator.tools.fashion_copy import make_fashion_copy
    for number in targets:
        segment = instructions.get(number) or feedback
        short, emotional, korean, phrase = _feedback_profile(segment)
        premium = bool(re.search(r'프리미엄|고급|럭셔리|미니멀', segment))
        color_text = '·'.join(colors)
        if korean:
            replacements = {'Black':'블랙','Greenish Khaki':'그린 카키', 'Beige':'베이지','White':'화이트'}
            color_text = '·'.join(replacements.get(x,x) for x in colors)
        if short and premium:
            headline = [f'겨울, {subtype}', f'{color_text} 컬렉션', f'{color_text} 무드'][number-1]
        elif short:
            headline = [f'{subtype}, 취향대로', f'{color_text} {subtype}', f'나다운 {subtype}'][number-1]
        elif emotional:
            headline = [f'겨울의 순간마다, 나다운 {subtype}', f'취향으로 완성하는 {subtype}',
                        f'{color_text}, 나만의 겨울 스타일'][number-1]
        else:
            headline = [f'{color_text}로 고르는 {subtype}', f'취향을 담은 {subtype} 컬렉션',
                        f'디자인으로 비교하는 {subtype}'][number-1]
        if phrase and number == targets[0]:
            headline = f'{phrase} — {subtype}'
        length = re.search(r'(\d{1,2})\s*자\s*이내', segment)
        if length and len(headline) > int(length.group(1)):
            raise ValueError(f'{number}안의 요청 글자 수로 문구를 만들 수 없습니다.')
        creative = make_fashion_copy(subtype=subtype, colors=colors, points=base['points'],
                                     mood='감성' if emotional and not short else '간결' if short else '일상', korean=korean)
        candidate = base['candidates'][number-1]
        subcopies = creative['options'][number-1]['subcopies']
        candidate.update(headline=headline, description=subcopies[0], subcopies=subcopies,
                         tone=creative['options'][number-1]['tone'], basis=list(base['points']), verified=True)
    # 공유 키워드는 전체 안의 기준이므로 별도 수정을 요청하지 않으면 보존한다.
    base['copy_style'] = 'fashion_v422a'
    base['revision'] = previous_revision + 1
    base['revision_feedback'] = feedback.strip()
    base['revision_targets'] = targets
    steps = list(base.get('revision_steps') or [])
    steps.append({'feedback': feedback.strip(), 'targets': targets})
    base['revision_steps'] = steps
    base['status'] = 'draft'
    return base


def validate_copy_claims(package, summary):
    """상품별 근거 범위·선정 소구점·허위 성능 표현을 독립적으로 검사."""
    selected = set(package.get('points') or [])
    known = {x['label']: x for x in summary.get('candidates') or []}
    if not selected or not selected.issubset(known):
        raise ValueError('선정 소구점 근거가 누락되었습니다.')
    if len(package.get('candidates') or []) != 3:
        raise ValueError('대표 문구는 3안이어야 합니다.')
    titles = []
    for candidate in package['candidates']:
        title = str(candidate.get('headline') or '')
        desc = str(candidate.get('description') or '')
        if not title or title in titles or _BANNED.search(title + ' ' + desc):
            raise ValueError('중복·미검증 광고 표현이 포함되어 있습니다.')
        titles.append(title)
        subcopies = candidate.get('subcopies') or []
        if len(subcopies) != 2 or not all(isinstance(x, str) and x.strip() and len(x) <= 180 for x in subcopies):
            raise ValueError('서브 카피는 안별로 두 개의 검증 가능한 문구가 필요합니다.')
        if desc != subcopies[0] or any(_BANNED.search(x) for x in subcopies):
            raise ValueError('서브 카피에 미검증 표현 또는 변조가 있습니다.')
        if set(candidate.get('basis') or []) != selected:
            raise ValueError('문구별 확정 소구점 근거가 일치하지 않습니다.')
    keywords = package.get('keywords') or []
    if len(keywords) != 5 or len(set(keywords)) != 5 or any(not isinstance(x, str) or _BANNED.search(x) for x in keywords):
        raise ValueError('검증된 키워드 태그 5개가 필요합니다.')
    cards = {str(x.get('article_id')): x for x in package.get('product_cards') or []}
    if set(cards) != set(package.get('ids') or []):
        raise ValueError('상품 카드가 확정 상품과 일치하지 않습니다.')
    for point in selected:
        row = known[point]
        if row.get('scope') == 'partial':
            for product_id, card in cards.items():
                if point in str(card.get('copy') or '') and product_id not in (row.get('matched') or {}):
                    raise ValueError('상품별 소구점이 근거 없는 상품에 적용됐습니다.')
    return True
