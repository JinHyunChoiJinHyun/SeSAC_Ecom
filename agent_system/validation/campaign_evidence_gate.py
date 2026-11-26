"""생성 코드와 별개로 승인된 캠페인 근거를 검사하는 독립 검증 게이트.

외부 API에 의존하지 않는다. PASS는 구조·출처·승인값 정합성만 의미하며
LLM 기반 의미 검증 또는 상품의 실제 성능 검증을 의미하지 않는다.
"""
from __future__ import annotations

import json
import re

_ARTICLE = re.compile(r'(?<!\d)\d{10}(?!\d)')
_UNSUPPORTED = re.compile(
    r'(?i)(?:\d+\s*%\s*할인|무료\s*배송|최저가|판매량\s*1위|\b1\s*위\b|'
    r'완벽한\s*보온|보온성\s*보장|방수\s*보장|품질\s*보장|당일\s*배송|무조건|특가)'
)


def _object(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ids(value):
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return _ARTICLE.findall(str(value or ''))


def validate_copy_evidence(package: dict, summary: dict, campaign: dict) -> dict:
    """생성기 호출 없이 구조와 확인된 상품·소구점 범위만 교차 검사."""
    failures = []
    values = (campaign or {}).get('values') or {}
    approved_ids = _ids(values.get('products'))
    package_ids = _ids(package.get('ids'))
    summary_ids = _ids(summary.get('ids'))
    if not approved_ids or len(set(approved_ids)) != len(approved_ids) or not (approved_ids == package_ids == summary_ids):
        failures.append('PRODUCT_IDS_MISMATCH')

    selected = _object(values.get('selling_points'))
    points = selected.get('points') or []
    if not isinstance(points, list) or not points or len(set(points)) != len(points) or points != package.get('points'):
        failures.append('SELLING_POINTS_MISMATCH')
    known = {c.get('label'): c for c in summary.get('candidates') or [] if isinstance(c, dict)}
    if any(p not in known for p in points):
        failures.append('UNKNOWN_SELLING_POINT')

    colors = values.get('colors') or []
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(',') if c.strip()]
    if colors != package.get('colors') or str(values.get('subtype') or values.get('category') or '').strip() != str(package.get('subtype') or '').strip():
        failures.append('CAMPAIGN_CONTEXT_MISMATCH')

    if summary.get('evidence_role') == 'historical_reference':
        if package.get('reference_role') != 'historical_reference':
            failures.append('REFERENCE_ROLE_MISSING')
        if package.get('saleable_article_ids'):
            failures.append('UNVERIFIED_SALEABLE_PRODUCT_IDS')
        if any(str(card.get('role') or '') == 'saleable_product' for card in package.get('product_cards') or []):
            failures.append('REFERENCE_PROMOTED_TO_SALEABLE')
    options = package.get('candidates') or []
    if len(options) != 3 or [c.get('number') for c in options] != [1, 2, 3]:
        failures.append('COPY_OPTIONS_INVALID')
    titles = []
    for candidate in options:
        title = str(candidate.get('headline') or '').strip()
        subs = candidate.get('subcopies') or []
        if not title or not isinstance(subs, list) or len(subs) != 2 or any(not isinstance(s, str) or not s.strip() for s in subs):
            failures.append('COPY_CONTENT_MISSING')
        if candidate.get('description') != (subs[0] if subs else None):
            failures.append('COPY_DESCRIPTION_MISMATCH')
        if set(candidate.get('basis') or []) != set(points):
            failures.append('COPY_BASIS_MISMATCH')
        if _UNSUPPORTED.search(' '.join([title, *[str(s) for s in subs]])):
            failures.append('UNSUPPORTED_MARKETING_CLAIM')
        titles.append(title)
    if len(set(titles)) != len(titles):
        failures.append('DUPLICATE_HEADLINE')
    keywords = package.get('keywords') or []
    if len(keywords) != 5 or len(set(keywords)) != 5:
        failures.append('KEYWORDS_INVALID')

    cards = package.get('product_cards') or []
    card_ids = [str(c.get('article_id') or '') for c in cards if isinstance(c, dict)]
    if len(card_ids) != len(package_ids) or set(card_ids) != set(package_ids):
        failures.append('PRODUCT_CARDS_MISMATCH')
    card_map = {str(c['article_id']): str(c.get('copy') or '') for c in cards if isinstance(c, dict) and c.get('article_id')}
    for point in points:
        row = known.get(point) or {}
        matched = set((row.get('matched') or {}).keys())
        if not matched.issubset(set(package_ids)):
            failures.append('POINT_EVIDENCE_OTHER_PRODUCT')
        if row.get('scope') == 'partial':
            for aid, copy in card_map.items():
                if aid not in matched and point in copy:
                    failures.append('PARTIAL_POINT_LEAK')

    return {'passed': not failures, 'failures': sorted(set(failures)),
            'checks': 'independent_deterministic_evidence',
            'semantic_llm_validation': 'not_run'}


def validate_approved_copy_for_report(run: dict, campaign: dict) -> dict:
    """현재 Run에 copy_package가 있으면 저장된 승인 카피와 정확하게 비교한다.

    오래된 Run에는 copy_package가 없을 수 있어 기존 보고서 생성과의 호환성을
    유지하되 해당 경우에는 `not_checked_legacy_run`을 반환한다.
    """
    package = (run or {}).get('copy_package') or {}
    if not package:
        return {'passed': True, 'checks': 'not_checked_legacy_run',
                'semantic_llm_validation': 'not_run', 'failures': []}
    result = validate_copy_evidence(package, (run or {}).get('campaign_summary') or {}, campaign)
    failures = list(result['failures'])
    values = (campaign or {}).get('values') or {}
    page = _object(values.get('page'))
    matches = [c for c in package.get('candidates') or []
               if c.get('number') == page.get('number') and c.get('headline') == page.get('headline')]
    if package.get('status') != 'verified' or len(matches) != 1:
        failures.append('COPY_APPROVAL_NOT_VERIFIED')
    else:
        choice = matches[0]
        if (package.get('reference_role') == 'historical_reference'
                and (page.get('reference_role') != 'historical_reference' or page.get('saleable_article_ids'))):
            failures.append('REFERENCE_CONTEXT_MISMATCH')
        if (page.get('full_copy') is not None and page.get('full_copy') != ' '.join([choice['headline']] + list(choice.get('subcopies') or []))):
            failures.append('FULL_COPY_MISMATCH')
        if (page.get('subcopies') != choice.get('subcopies')
                or page.get('keywords') != package.get('keywords')
                or page.get('article_ids') != package.get('ids')
                or page.get('colors') != package.get('colors')
                or page.get('subtype') != package.get('subtype')):
            failures.append('APPROVED_COPY_SET_MISMATCH')
    result.update(passed=not failures, failures=sorted(set(failures)))
    return result


def require_approved_copy_for_report(run: dict, campaign: dict) -> dict:
    result = validate_approved_copy_for_report(run, campaign)
    if not result['passed']:
        raise ValueError('보고서 승인 근거 검증 실패: ' + ', '.join(result['failures']))
    return result
