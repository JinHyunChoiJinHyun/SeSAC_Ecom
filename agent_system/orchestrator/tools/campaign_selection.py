"""12단계 근거 정리와 13단계 확정값 검증 (LLM 추정 없음)."""
import json
import re
from datetime import date


def ids_from(campaign):
    return re.findall(r"(?<!\d)\d{10}(?!\d)", str((campaign.get("values") or {}).get("products", "")))


def build_summary(run, campaign, details):
    values = campaign.get("values") or {}
    ids = ids_from(campaign)
    market = run.get("category_market") or {}
    if not ids or market.get("category") != values.get("category"):
        raise ValueError("과거 참고 상품 ID 또는 동일 카테고리 시장 근거 없음")
    rows = {str(r.get("article_id")): r for r in (details.get("data") or {}).get("rows", []) if isinstance(r, dict)}
    if not details.get("available") or not set(ids).issubset(rows):
        raise ValueError("확정 상품 상세정보 조회 실패 또는 누락")
    aspects = sorted([r for r in market.get("saved_rows", []) if r.get("source") == "musinsa" and r.get("aspect") and isinstance(r.get("mention_reviews"), (int,float)) and r["mention_reviews"] > 0], key=lambda r: -r["mention_reviews"])
    if not aspects:
        raise ValueError("11단계 리뷰 항목 근거 없음")
    # 소재와 디자인은 실제 문구로만 확인한다. 단독 down은 방향 표현일 수 있다.
    review = {r['aspect']: r for r in aspects}
    feature_specs = [
        ('색상별 스타일링 선택', '색감', None),
        ('파일 원단 아우터', '보온성', r'\b(?:soft pile|pile jacket|pile coat|pile fabric|in pile)\b'),
        ('부드러운 파일 원단', '퀄리티', r'\bsoft pile\b'),
        ('안감이 명시된 아우터', '보온성', r'\blined\b|\b(?:with|has) (?:an? )?lining\b'),
        ('울 혼방 소재', '보온성', r'\bwool[- ]blend\b|\bblend of wool\b'),
        ('다운 충전재 명시 상품', '보온성', r'\bdown[- ]filled\b|\bfilled with (?:[a-z]+ )?down\b|\bdown (?:filling|insulation)\b'),
        ('기장별 아우터 선택', '사이즈', r'\b(?:knee-length|long coat|short coat|cropped jacket)\b'),
        ('핏 선택을 위한 디자인 안내', '사이즈', r'\b(?:oversized|loose fit|regular fit|slim fit|relaxed fit)\b'),
        ('벨트·밑단 조절 디자인', '사이즈', r'\b(?:detachable tie belt|drawstring at the hem)\b'),
        ('포켓이 있는 아우터', None, r'\b(?:side|front|zipped|concealed|diagonal side) pockets\b'),
        ('패딩 구조 명시 상품', '보온성', r'\bpadded (?:jacket|coat)\b'),
    ]
    candidates = []
    for label, aspect, pattern in feature_specs:
        if values.get('category') != '아우터':
            label = label.replace('아우터', '상품')
        matched = {}
        for i in ids:
            row = rows[i]
            if pattern is None:
                value = str(row.get('colour_group_name') or '').strip()
                if value: matched[i] = value
            else:
                hits = re.findall(pattern, str(row.get('detail_desc') or ''), re.I)
                if hits: matched[i] = ', '.join(dict.fromkeys(hits))
        if not matched: continue
        r = review.get(aspect, {})
        scope = 'common' if len(matched) == len(ids) else 'partial'
        candidates.append({'label': label, 'aspect': aspect, 'mentions': int(r.get('mention_reviews', 0)), 'rate': r.get('mention_rate'), 'verified': True, 'scope': scope, 'matched': matched})
    candidates.sort(key=lambda c: (c['scope'] != 'common', -len(c['matched']), -c['mentions']))
    # 일부 상품의 특징은 전체 기획전 공통 주장으로 선택할 수 없다.
    common = [c for c in candidates if c['scope'] == 'common']
    partial = [c for c in candidates if c['scope'] == 'partial']
    # 상품별 디자인 비교는 공통 기능·소재 주장이 아니라 상품 카드의 콘텐츠 구성이다.
    # 다른 공통점이 충분하더라도 비교 근거가 있으면 항상 후보로 제공한다.
    if all(str(rows[i].get('detail_desc') or '').strip() for i in ids):
        common.append({'label': '상품별 디자인 상세 비교', 'aspect': None,
                       'mentions': 0, 'rate': None, 'verified': True,
                       'scope': 'common', 'evidence_type': 'content_comparison',
                       'matched': {i: str(rows[i]['detail_desc']) for i in ids}})
    if not common and not partial:
        raise ValueError('상품 설명에서 검증 가능한 소구점 근거가 없습니다.')
    unknown = [{'aspect': a, 'mentions': int(r['mention_reviews']), 'rate': r.get('mention_rate'), 'required': need} for a, need in [('사이즈', '실측·착용감'), ('보온성', '보온 성능·충전재 상세'), ('두께감', '실제 두께'), ('퀄리티', '품질 평가·내구성')] if (r := review.get(a))]
    # 부분 일치 특성도 선택 가능하지만 이름부터 반드시 '상품별'이라고 한정한다.
    # 본문/광고에서 상품 전체의 공통 기능으로 확대 해석하지 않도록 증거 범위를 저장한다.
    scoped_partial = [dict(c, label='상품별 · ' + c['label'], evidence_type='product_only') for c in partial]
    candidates = common + scoped_partial
    # 제품별 검증 항목 수만큼 제시한다. 임의의 상위 3개 제한은 두지 않는다.
    purchases = {str(r.get("article_id")): r.get("winter_online_purchases") for r in run.get("verified_candidate_rows", [])}
    has_purchases = all(isinstance(purchases.get(i), (int, float)) for i in ids)
    order = sorted(ids, key=lambda i: -purchases[i]) if has_purchases else ids
    category = values['category']
    from agent_system.orchestrator.tools.category_planning.period_context import purchase_label
    plan = run.get('period_plan')
    from .campaign_season import from_campaign
    season_context = from_campaign(run, campaign)
    season_prefix = '' if season_context['source']=='neutral' else season_context['season']+' '
    themes = [f'{season_prefix}{category} 컬렉션', f'나에게 맞는 {category} 스타일']
    # 참고 사례 Run에서는 이미 승인된 세부 유형만 기획전 주제로 제공한다.
    if run.get('reference_product_role') == 'historical_reference':
        themes = [str(values.get('subtype') or run.get('selected_subtype') or '').strip()]
        if not themes[0]:
            raise ValueError('승인된 세부 유형이 없어 기획전 주제를 정할 수 없습니다.')
    summary = {'schema_version': 2, 'evidence_role': 'historical_reference' if run.get('reference_product_role') == 'historical_reference' else 'legacy_product_selection', 'subtype': values.get('subtype') or run.get('selected_subtype'), 'selected_colors': values.get('colors') or run.get('selected_colors') or [], 'category': category, 'ids': ids, 'themes': themes, 'candidates': candidates, 'partial_candidates': partial, 'candidate_count': len(candidates), 'unknown_aspects': unknown, 'order': order, 'order_basis': purchase_label(plan) + ' 건수 (과거 참고 사례)' if has_purchases else '참고 순서 (구매 건수 미확인)' if run.get('reference_product_role') == 'historical_reference' else '확정 순서 (구매 건수 미확인)'}
    # 확인된 과거 H&M 참고 사례의 디자인 특징만 연결한다.
    design_patterns = (
        ('스탠드칼라', r'\bstand[- ]up collar\b'),
        ('셔츠형 칼라', r'\b(?:with a collar|shirt collar)\b'),
        ('봄버형', r'\bbomber\b'),
        ('후드형', r'\bhood(?:ed)?\b'),
        ('스냅 버튼', r'\bpress[- ]studs?\b'),
        ('지퍼', r'\bzip(?:per|ped)?\b'),
        ('포켓', r'\bpockets?\b'),
        ('슬림한 실루엣 표현', r'\b(?:fitted|slim fit)\b'),
    )
    verified_designs = []
    for label, pattern in design_patterns:
        matched_ids = [item for item in ids
                       if re.search(pattern, str(rows[item].get('detail_desc') or ''), flags=re.I)]
        if matched_ids:
            verified_designs.append({'label': label, 'matched_ids': matched_ids, 'products': len(matched_ids)})
    # 상위 랭킹 관련 상품의 근거를 저장하되 기존 공통 소구점의 검증 조건은 유지한다.
    ranked = market.get('ranked_evidence') or {}
    summary['season_context'] = season_context
    summary['verified_designs'] = verified_designs
    if ranked:
        summary['ranked_evidence'] = ranked
    # 동일 관심사에 대한 교차 근거: H&M 직접 확인과 무신사 리뷰 언급을 분리한다.
    # 무신사 언급은 H&M의 성능/만족도 보증 또는 상품 일치를 뜻하지 않는다.
    aspect_counts = {str(a.get('label')): int(a.get('mentions') or 0)
                     for a in ranked.get('aspects', []) if isinstance(a, dict)}
    cross = []
    for candidate in candidates:
        aspect = candidate.get('aspect')
        if not aspect or aspect not in aspect_counts or aspect_counts[aspect] <= 0:
            continue
        cross.append({'label': candidate['label'], 'hm_matched_ids': list(candidate['matched']),
                      'hm_products': len(candidate['matched']), 'hm_total': len(ids),
                      'musinsa_aspect': aspect, 'musinsa_mentions': aspect_counts[aspect],
                      'musinsa_related_reviews': int(ranked.get('related_reviews') or 0),
                      'scope': candidate['scope'], 'cross_verified': True,
                      'limit': '무신사 리뷰는 긍정·부정 미구분; H&M 직접 후기 아님'})
    summary['cross_evidence'] = cross
    # 사용자에게는 결정에 필요한 짧은 설명만 제공한다. 세부 제품 원문과 근거는
    # campaign_summary에 그대로 보존하여 13~14단계 검증에 재사용한다.
    subtype = str(values.get('subtype') or run.get('selected_subtype') or category).strip()
    selected_colors = values.get('colors') or run.get('selected_colors') or []
    if isinstance(selected_colors, str):
        selected_colors = [c.strip() for c in selected_colors.split(',') if c.strip()]
    color_text = ', '.join(selected_colors) if selected_colors else '상품별 확인된 색상'
    market_scope = f"무신사 {category} 전체 리뷰 {market.get('sample_reviews', '미확인')}건"
    most = aspects[:4]
    interest = ', '.join(f"{r['aspect']} {int(r['mention_reviews']):,}건" for r in most)
    # 소분류 관련 리뷰의 분모를 전체 카테고리와 혼동하지 않도록 표기한다.
    ranked_lines = []
    if ranked:
        related_count = ranked.get('related_reviews', 0)
        ranking_count = ranked.get('related_products', 0)
        ranked_lines.append(
            f"무신사 랭킹에서 {ranked.get('gender_scope') or '성별 범위 미확인'} 기준으로 비교한 '{subtype}' 관련 잠정 후보 {ranking_count}개와 "
            f"그 상품에 연결된 리뷰 {related_count}건을 별도로 분석했어요. "
            f"상품명 규칙 분류이므로 실제 {subtype} 제품인지 최종 인증한 결과는 아닙니다.")
        shapes = [x for x in ranked.get('related_shapes', [])
                  if x.get('products') and x.get('label') not in ('패딩·다운 표현', '경량 표현', '코트 표현', '플리스 표현')]
        if shapes:
            ranked_lines.append('관련 상품의 디자인 표현(중복 허용): ' + ', '.join(
                f"{x['label']} {x['products']}개" for x in sorted(shapes, key=lambda x: -x['products'])[:4]) + '.')
        aspects_ranked = [x for x in ranked.get('aspects', []) if x['mentions'] > 0]
        if aspects_ranked:
            ranked_lines.append('관련 리뷰에서 언급된 관심사(긍정·부정 미구분): ' + ', '.join(
                f"{x['label']} {x['mentions']}건/{related_count}건" for x in aspects_ranked[:4]) + '.')
    if verified_designs:
        ranked_lines.append(('H&M 과거 참고 사례 설명에서 확인한 디자인: ' if summary['evidence_role'] == 'historical_reference' else 'H&M 확정 상품 설명에서 확인한 디자인: ') + ', '.join(
            f"{x['label']} {x['products']}/{len(ids)}개" for x in verified_designs[:7]) + '.')
    if ranked:
        hood = next((x['products'] for x in ranked.get('related_shapes', []) if x['label'] == '후드형'), 0)
        hm_hood = next((x['products'] for x in verified_designs if x['label'] == '후드형'), 0)
        if hood and not hm_hood:
            ranked_lines.append('무신사 관련 상품에는 후드형이 확인되지만 확정 H&M 상품 설명에는 후드 근거가 없어 후드형 소구점은 제외합니다.')
    lines = [
        f"■ {subtype} 기획전에 적용할 소구점 제안",
        f"{market_scope}에서는 {interest} 등이 자주 언급됐어요.",
        f"{category} 전체 통계는 비교 참고용이며, H&M 직접 후기나 소분류만의 평가가 아닙니다.",
        *ranked_lines,
        "",
        f"■ 향후 {subtype} 기획전 상품 구성에 적용할 때",
        f"과거 참고 사례 {len(ids)}개의 색상({color_text})과 디자인을 비교하여, 향후 판매할 상품의 편성 조건으로 검토합니다. "
        "칼라·여밈·포켓·실루엣 차이는 과거 디자인 사례이지 실제 판매 상품의 확정 특징이 아닙니다.",
        "리뷰에서 관심이 높았던 요소와 실제 상품 정보가 모두 뒷받침되는 범위에서만 표현하는 방식입니다.",
        "",
        "■ 확인이 필요한 표현",
        "사이즈 만족도·핏, 보온 성능, 실제 두께·착용감·내구성은 경쟁 리뷰만으로 보증할 수 없어요. "
        "상품별 실측이나 검증 자료가 확보되기 전에는 광고의 확정적 표현에서 제외하는 것이 좋아요.",
        "",
        "■ 현재 정리 — 검증된 소구점 후보 (공통 / 상품별 한정)",
    ]
    for i, c in enumerate(candidates, 1):
        scope_note = '상품별 한정, 전체 상품의 공통 특성 아님' if c['scope'] == 'partial' else '공통 콘텐츠 구성' if c.get('evidence_type') == 'content_comparison' else '공통 확인'
        lines.append(f"{i}. {c['label']} — H&M 과거 참고 사례 {len(c['matched'])}/{len(ids)}개에서 확인 ({scope_note}); 향후 편성 조건 후보" if summary["evidence_role"] == "historical_reference" else f"{i}. {c['label']} — H&M 확정 상품 {len(c['matched'])}/{len(ids)}개에서 확인 ({scope_note})")
    if cross:
        lines.extend(['', '■ H&M × 무신사 교차 소구점 근거'])
        for item in cross:
            lines.append(f"{item['label']}: H&M {item['hm_products']}/{item['hm_total']}개 "
                         f"(ID: {', '.join(item['hm_matched_ids'])}) / "
                         f"무신사 관련 리뷰 '{item['musinsa_aspect']}' "
                         f"{item['musinsa_mentions']}/{item['musinsa_related_reviews']}건")
        lines.append('교차 근거는 두 데이터에서 같은 관심사를 관찰했다는 뜻이며, 품질·만족도 검증이나 상품 동일성을 뜻하지 않습니다.')
    if summary["evidence_role"] == "historical_reference":
        lines.append("이 단계에서 선택하는 소구점은 향후 상품 편성을 위한 검토 특징입니다. 과거 상품의 광고 문구 확정이 아닙니다.")
    if partial:
        lines.append('상품별 한정 후보는 해당 상품 카드에만 적용하며, 공통 광고 문구로 사용할 수 없습니다.')
    lines.extend([
        ("확정 기획전 주제(변경 없음): " if summary['evidence_role'] == 'historical_reference' else "추천 기획전 주제: ") + ' / '.join(f"{n+1}. {t}" for n, t in enumerate(themes)),
        "과거 상품 ID는 실제 판매 상품이 아닙니다. 현재 판매 SKU·가격·할인·재고는 확인되지 않았으므로 문구에 넣지 않습니다.",
        "원하는 소구점을 번호로 선택해 주세요 (2~3개 권장, 개수 제한 없음). 상품별 한정 후보는 해당 상품에만 적용합니다.",
    ])
    summary['public_report'] = '\n'.join(lines)
    return {"final_answer": summary['public_report'], "campaign_summary": summary, "data_sources": ["musinsa", "hm"], "data_purpose": "12단계 추천 근거"}


def review_brief(market, subtype=None):
    """11단계 경쟁 리뷰의 핵심만 요약한다. 원본 통계/근거 데이터는 바꾸지 않는다."""
    category = str(market.get('category') or '선택 카테고리')
    reviews = market.get('sample_reviews', '미확인')
    items = sorted(
        [r for r in (market.get('saved_rows') or [])
         if r.get('source') == 'musinsa' and r.get('aspect')
         and isinstance(r.get('mention_reviews'), (int, float))],
        key=lambda r: -r['mention_reviews'],
    )[:5]
    if items:
        names = ', '.join(f"{r['aspect']} {int(r['mention_reviews']):,}건" for r in items)
    else:
        names = '리뷰 항목 집계 확인 필요'
    target = subtype or category
    return ('■ 무신사 고객 관심사 요약\n'
            f'{category} 전체 수집 리뷰 {reviews}건에서 {names} 등이 확인됐어요.\n'
            f'이 결과는 {target}만 선별한 리뷰가 아니므로 관심사 참고용입니다. '
            '실제 광고 소구점은 다음 단계에서 확정 H&M 상품 설명과 대조해 제안합니다.\n'
            '상세 빈도와 항목별 비율은 첨부 CSV에서 확인할 수 있어요.')


def guide(summary):
    """13단계 선택 안내. 미정은 실제 가격이나 할인을 지어내지 않는다는 뜻."""
    labels = ' / '.join(f'{i+1}. {x["label"]}' for i,x in enumerate(summary['candidates']))
    return (f"소구점 후보: {labels}\n"
            "소구점을 자유롭게 선택해 주세요 (2~3개 권장). 예: 소구점: 1, 2\n"
            "가격·할인 미확정 값은 광고에 사용하지 않습니다. 운영 예정 일정은 기존 기획전 정보를 참고합니다. "
            "승인된 세부 유형을 기획전 주제로 유지하며, 과거 참고 사례는 구매 기록순으로 표시합니다. "
            "새로운 기획전의 실제 판매 상품·가격·혜택·노출 순서는 아직 확정하지 않습니다.")


def parse_selection(text, summary):
    if summary.get("schema_version") != 2:
        raise ValueError("워크플로 12단계 재실행으로 추천 결과를 갱신하세요.")
    # 선택값만 보낸 경우, 확인되지 않은 상품 혜택은 미정으로 안전하게 둔다.
    # 정형 양식은 아래 기존 검증을 그대로 적용한다.
    # 번호만 전달하는 짧은 입력: 1, 2 / 소구점 1번이랑 2번 / 1,2,3,4.
    # 최대 개수는 후보 전체 수로 제한하고, 중복이나 확인되지 않은 범위는 거부한다.
    compact = re.fullmatch(
        r'\s*(?:소구점\s*[:：]?\s*)?\d+(?:번)?'
        r'(?:\s*(?:[,，]|이랑|랑|하고|와|과|및)\s*\d+(?:번)?)*'
        r'\s*(?:으로|로)?\s*(?:할게|선택할게|선택해줘)?[.!]?\s*', text)
    if compact:
        numbers = re.findall(r'\d+', text)
        text = ('주제: 1\n소구점: ' + ','.join(numbers)
                + '\n가격: 미정\n할인: 미정\n기간: 미정\n노출순서: 추천')
    fields = {}
    for line in text.strip().splitlines():
        k, sep, v = line.partition(':'); k=k.strip(); v=v.strip()
        if not sep or k not in ('주제','소구점','가격','할인','기간','노출순서') or k in fields or not v:
            raise ValueError("항목별 입력 형식을 확인하세요.")
        fields[k]=v
    missing = [k for k in ('주제','소구점','가격','할인','기간','노출순서') if k not in fields]
    if missing: raise ValueError("누락: " + ', '.join(missing))
    def choose(value, options):
        if not re.fullmatch(r'[0-9]+',value): raise ValueError("추천 번호를 입력하세요.")
        n=int(value)
        if not 1 <= n <= len(options): raise ValueError("추천 번호 범위 오류")
        return options[n-1]
    # 콘텐츠 제목 후보를 선택하더라도 이미 승인된 상품 세부 유형·테마를 덮어쓰지 않는다.
    content_concept = choose(fields['주제'], summary['themes'])
    reference_mode = summary.get('evidence_role') == 'historical_reference'
    theme = str(summary.get('subtype') or '').strip() if reference_mode else content_concept
    if reference_mode and not theme:
        raise ValueError('확정된 세부 유형·기획전 주제가 없어 선택을 진행할 수 없습니다.')
    points=[choose(v.strip(),summary['candidates'])['label'] for v in fields['소구점'].split(',')]
    if not points or len(points) > len(summary["candidates"]) or len(set(points)) != len(points):
        raise ValueError("서로 다른 소구점을 1개 이상 선택하세요 (후보 전체 수 이내).")
    ids=summary['ids']
    def offers(text, discount=False):
        if text=='미정': return {i:None for i in ids}
        out={}
        for item in text.split(','):
            m=re.fullmatch(r'\s*(\d{10})\s*=\s*(\d+(?:\.\d+)?)\s*',item)
            if not m or m[1] not in ids or m[1] in out: raise ValueError("상품별 금액/할인 형식 또는 ID 오류")
            v=float(m[2])
            if (discount and not 0<=v<=100) or (not discount and (not v.is_integer() or v<=0 or v>10**9)): raise ValueError("가격/할인 범위 오류")
            out[m[1]]=v if discount else int(v)
        if set(out)!=set(ids): raise ValueError("모든 확정 상품의 값을 입력하세요.")
        return out
    price=offers(fields['가격']); discount=offers(fields['할인'],True)
    period=None
    if fields['기간']!='미정':
        try:
            a,b=fields['기간'].split('~'); start,end=date.fromisoformat(a.strip()),date.fromisoformat(b.strip())
            if start>end: raise ValueError()
            period=[start.isoformat(),end.isoformat()]
        except ValueError: raise ValueError("기간 형식 또는 순서 오류") from None
    order=summary['order'] if fields['노출순서']=='추천' else [x.strip() for x in fields['노출순서'].split(',')]
    if len(order)!=len(ids) or set(order)!=set(ids): raise ValueError("확정 상품 ID 전체를 중복 없이 입력하세요.")
    result = {"theme":theme,"points":points,"prices_krw":price,"discount_percent":discount,"period":period,"display_order":order,"claim_policy":"미정 값 및 검증되지 않은 기능·품질 표현은 광고에 사용 금지"}
    if reference_mode:
        # 과거 추천 주제의 표현 방향은 확정 주제와 분리한다.
        # 짧은 자연어 입력에서는 불필요한 별도 콘텐츠 콘셉트를 만들지 않는다.
        if not compact and content_concept != theme:
            result['content_concept'] = content_concept
        from agent_system.orchestrator.tools.merchandising_direction import build_direction
        if fields['노출순서'] != '추천':
            raise ValueError('과거 참고 상품 ID를 실제 판매 상품의 노출 순서로 확정할 수 없습니다.')
        if fields['가격'] != '미정' or fields['할인'] != '미정':
            raise ValueError('과거 참고 상품의 가격·할인을 미래 기획전 판매 조건으로 확정할 수 없습니다.')
        result['merchandising_direction'] = build_direction(summary, result,
            subtype=summary.get('subtype') or summary.get('category') or '', colors=summary.get('selected_colors'))
    return result


def updates(text, summary):
    return {"selling_points":json.dumps(parse_selection(text,summary),ensure_ascii=False,sort_keys=True)}

# R29: 확정 근거에서 직접 생성하는 재표시·전달용 표/그래프. 새로운 분석값을 추정하지 않는다.
def build_selling_point_evidence(summary, output_dir=None):
    """승인 대기 소구점 후보의 공통/상품별 범위와 근거를 CSV·PNG로 출력한다."""
    from pathlib import Path
    import pandas as pd

    if not isinstance(summary, dict) or summary.get('schema_version') != 2:
        raise ValueError('저장된 소구점 검증 근거가 없습니다. 워크플로 12단계 재실행이 필요합니다.')
    candidates = summary.get('candidates') or []
    if not candidates:
        raise ValueError('소구점 후보가 없어 근거표를 생성할 수 없습니다.')
    ids = [str(item) for item in summary.get('ids') or []]
    rows = []
    for index, item in enumerate(candidates, 1):
        matched = item.get('matched') or {}
        rows.append({
            '번호': index,
            '소구점': str(item.get('label') or ''),
            '적용범위': '전체 공통' if item.get('scope') == 'common' else '상품별 한정',
            'H&M근거상품수': len(matched),
            'H&M참고상품전체': len(ids),
            '무신사연관관심사': str(item.get('aspect') or '해당 없음'),
            '무신사관심사언급건수': int(item.get('mentions') or 0),
            '관련H&M상품ID': ', '.join(str(k) for k in matched),
        })
    frame = pd.DataFrame(rows)
    result = {'dataframe': frame, 'data_purpose': '확정 상품의 소구점 후보 근거표'}
    if output_dir is None:
        output_dir = Path.cwd() / 'visualization_output'
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    chart_path = output_dir / f'selling_point_evidence_{uuid4().hex[:10]}.png'
    try:
        from agent_system.orchestrator.tools.category_planning.confirmation_report import _korean_font_context
        import matplotlib.pyplot as plt
        with _korean_font_context():
            fig, ax = plt.subplots(figsize=(11, max(4, 0.58 * len(rows) + 1.2)))
            labels = [f"{r['번호']}. {r['소구점']}" for r in rows]
            y = list(range(len(rows)))
            ax.barh(y, [r['H&M근거상품수'] for r in rows])
            ax.set_yticks(y, labels)
            ax.invert_yaxis()
            ax.set_xlim(0, max(len(ids) + 1, 2))
            ax.set_xlabel('H&M 과거 참고 상품 중 설명 근거 확인 수 (개)')
            ax.set_title('소구점 후보별 H&M 근거 상품 수 (광고 성능 검증 아님)')
            for i, r in enumerate(rows):
                ax.text(r['H&M근거상품수'] + .08, i,
                        f"{r['H&M근거상품수']}/{len(ids)}", va='center', fontsize=9)
            fig.tight_layout()
            fig.savefig(chart_path, dpi=140)
            plt.close(fig)
        result['visualization_path'] = str(chart_path)
    except Exception as exc:
        # 그래프가 실패해도 검증 근거 CSV는 반드시 보낸다.
        import logging
        logging.getLogger(__name__).warning('SELLING_POINT_CHART_FAILED %s', type(exc).__name__)
    return result


def _r31_rebuild_detailed_report(summary, market=None):
    """저장된 근거만 사용하여 12단계 상세 보고서를 재구성한다."""
    ids = [str(x) for x in (summary.get('ids') or [])]
    subtype = str(summary.get('subtype') or '선택 상품 유형')
    category = str(summary.get('category') or '선택 상품군')
    colors = summary.get('selected_colors') or []
    if isinstance(colors, str):
        colors = [v.strip() for v in colors.split(',') if v.strip()]
    ranked = summary.get('ranked_evidence') or {}
    if not ranked and isinstance(market, dict):
        ranked = market.get('ranked_evidence') or {}
    n = len(ids)
    lines = [f'■ {subtype} 기획전 소구점 상세 분석 (저장된 근거 재표시)',
             f'상품군: {category} / 상품 유형: {subtype}',
             f'선택 색상: {", ".join(colors) if colors else "저장된 색상 없음"}',
             f'H&M 과거 참고 사례: {n}개 (실제 판매 편성 상품 아님)',
             '', '■ 무신사 관련 상품·리뷰 근거']
    related_p = ranked.get('related_products')
    related_r = ranked.get('related_reviews')
    if related_p is not None and related_r is not None:
        lines.append(f'관련 상품명 잠정 후보 {related_p}개 / 연결 수집 리뷰 {related_r}건 (판매량·긍정 평가 아님)')
    else:
        lines.append('상품·리뷰 표본 수: 저장된 상세 근거가 없어 재확인 필요')
    shapes = [x for x in ranked.get('related_shapes', []) if isinstance(x, dict) and x.get('products')]
    if shapes:
        lines.append('상품명 디자인 표현(중복 허용): ' + ', '.join(
            f"{x.get('label')} {x.get('products')}개" for x in shapes[:6]))
    aspects = [x for x in ranked.get('aspects', []) if isinstance(x, dict) and x.get('mentions') is not None]
    if aspects:
        denominator = f'/{related_r}건' if related_r else ''
        lines.append('관련 리뷰 언급(긍정·부정 미구분): ' + ', '.join(
            f"{x.get('label')} {x.get('mentions')}건{denominator}" for x in aspects[:7]))
    else:
        lines.append('세부 리뷰 관심사: 저장된 자료에서 확인 필요')
    lines.extend(['', '■ H&M 과거 참고 사례의 디자인 근거'])
    designs = summary.get('verified_designs') or []
    if designs:
        lines.append('상품 설명에서 확인: ' + ', '.join(
            f"{x.get('label')} {x.get('products')}/{n}개" for x in designs))
    else:
        lines.append('검증 가능한 디자인 태그가 저장되지 않았습니다.')
    lines.extend(['', '■ 소구점 후보 및 적용 범위'])
    for i, item in enumerate(summary.get('candidates') or [], 1):
        matched = item.get('matched') or {}
        scope = '전체 공통' if item.get('scope') == 'common' else '상품별 한정'
        lines.append(f"{i}. {item.get('label')} / {scope} / H&M 근거 {len(matched)}/{n}개")
        if scope == '상품별 한정':
            lines.append('   해당 상품에만 적용 가능하며 공통 광고 문구로 사용할 수 없습니다.')
    lines.extend(['', '■ H&M × 무신사 교차 검토'])
    cross = summary.get('cross_evidence') or []
    if cross:
        for item in cross:
            lines.append(f"- {item.get('label')}: H&M {item.get('hm_products')}/{item.get('hm_total')}개 "
                         f"(ID: {', '.join(map(str, item.get('hm_matched_ids') or []))}) / "
                         f"무신사 '{item.get('musinsa_aspect')}' 언급 "
                         f"{item.get('musinsa_mentions')}/{item.get('musinsa_related_reviews')}건")
    else:
        lines.append('교차 검토 근거: 저장된 자료에서 확인 필요')
    lines.append('교차 근거는 관심사와 상품 설명의 관련성을 보여줄 뿐 품질·만족도 검증이 아닙니다.')
    unknown = summary.get('unknown_aspects') or []
    if unknown:
        lines.extend(['', '■ 확인이 필요한 표현'])
        for item in unknown:
            lines.append(f"- {item.get('aspect')}: {item.get('required')} 확인 전 광고에서 확정 표현 제외")
    lines.extend(['', '■ 선택 안내',
                  '소구점 번호를 선택해 주세요. 예: 소구점: 1, 2',
                  '실제 판매 SKU·가격·할인·재고는 과거 H&M 데이터로 확정할 수 없습니다.',
                  '이 분석을 다시 표시해도 기존 승인·선택 상태는 변경되지 않습니다.'])
    return '\n'.join(lines)


def replay_selling_point_report(summary, campaign=None, market=None):
    """선택·승인 상태를 변경하지 않고 저장된 소구점 후보를 재표시한다."""
    if not isinstance(summary, dict) or summary.get('schema_version') != 2:
        raise ValueError('저장된 소구점 분석 결과가 없습니다. 워크플로 12단계 재실행이 필요합니다.')
    if (campaign or {}).get('values', {}).get('selling_points'):
        # 확정된 값은 유지하며, 과거 후보임을 구별한다.
        title = '■ 저장된 소구점 후보 다시 보기 (기존 승인값 유지)'
    else:
        title = '■ 소구점 후보 다시 보기 (아직 승인 전)'
    original = str(summary.get('public_report') or '').strip()
    detailed = original or _r31_rebuild_detailed_report(summary, market)
    return title + '\n\n' + detailed
