"""마케터가 4·10단계에서 분석할 카테고리를 지정할 때 사용하는 순수 파서."""
from __future__ import annotations
import re

CATEGORIES = ('긴소매 티셔츠','니트·스웨터','반소매 티셔츠','셔츠·블라우스','아우터')
_ALIASES = {
 '아우터':'아우터', '니트':'니트·스웨터','스웨터':'니트·스웨터','니트스웨터':'니트·스웨터',
 '셔츠':'셔츠·블라우스','블라우스':'셔츠·블라우스','셔츠블라우스':'셔츠·블라우스',
 '긴소매티셔츠':'긴소매 티셔츠','긴팔티셔츠':'긴소매 티셔츠',
 '반소매티셔츠':'반소매 티셔츠','반팔티셔츠':'반소매 티셔츠'
}

def parse_category_review(message: str) -> str | None:
    """명시적 카테고리 검토 요청만 허용. 확정·승인·복수 비교 요청은 여기서 처리하지 않음."""
    s=str(message or '').strip().rstrip('.!')
    # 카테고리 선택과 세부 유형 질문이 같이 들어오는 대표 대화 경로.
    natural = re.fullmatch(r"(?:좋아[,.]?\s*)?(아우터|니트|스웨터|니트·스웨터|셔츠·블라우스|긴소매 티셔츠|반소매 티셔츠)\s*(?:으?로)\s*(?:할게|갈게|결정할게|선택할게)(?:(?:[.!]\s*)?(?:어떤|무슨)\s*.*(?:종류|유형).*)?", s)
    if natural:
        return _ALIASES.get(re.sub(r'[\s·/\-]', '', natural.group(1)))
    if re.search(r'확정|승인|선택완료|상품\s*ID',s):
        return None
    s=re.sub(r'\s*(?:부터|도|로|을|를|는|은)?\s*(?:분석|비교|검토|보여줘|해줘|해\s*줘|해주세요|해\s*주세요|보고싶어|볼게|골라볼게|선택할게).*$', '', s)
    s=re.sub(r'^\s*(?:카테고리(?:는|를|로)?\s*|이번에는\s*|다른\s*|다음은\s*)','',s)
    key=re.sub(r'[\s·/\-]', '', s)
    return _ALIASES.get(key)


def parse_category_confirmation(message: str) -> str | None:
    """'분석해줘'와 구분되는 대분류 결정 표현만 인식한다."""
    s = str(message or '').strip().rstrip('.!?~ ')
    match = re.fullmatch(
        r"(?:좋아[,.]?\s*)?(아우터|니트|스웨터|니트·스웨터|셔츠|블라우스|셔츠·블라우스|긴소매 티셔츠|반소매 티셔츠)"
        r"\s*(?:으로|로)\s*(?:할게|갈게|결정할게|선택할게|확정할게|진행할게)"
        r"(?:요)?(?:[.!]\s*(?:어떤|무슨)\s*.*(?:종류|유형).*)?",
        s,
    )
    if not match:
        return None
    return _ALIASES.get(re.sub(r'[\s·/\-]', '', match.group(1)))
