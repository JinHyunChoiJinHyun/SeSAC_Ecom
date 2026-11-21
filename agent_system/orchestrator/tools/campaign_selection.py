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
        raise ValueError("확정 상품 또는 동일 카테고리 시장 근거 없음")
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
    # 공통 소재가 부족해도 사실에 근거한 상품별 안내를 선택할 수 있다.
    if len(common) < 2 and all(str(rows[i].get('detail_desc') or '').strip() for i in ids):
        common.append({'label': '상품별 디자인 상세 비교', 'aspect': None, 'mentions': 0, 'rate': None, 'verified': True, 'scope': 'common', 'matched': {i: str(rows[i]['detail_desc']) for i in ids}})
    if len(common) < 2: raise ValueError('공통 소구점 근거 부족: 상품 정보 추가 확인 필요')
    unknown = [{'aspect': a, 'mentions': int(r['mention_reviews']), 'rate': r.get('mention_rate'), 'required': need} for a, need in [('사이즈', '실측·착용감'), ('보온성', '보온 성능·충전재 상세'), ('두께감', '실제 두께'), ('퀄리티', '품질 평가·내구성')] if (r := review.get(a))]
    candidates = common
    purchases = {str(r.get("article_id")): r.get("winter_online_purchases") for r in run.get("verified_candidate_rows", [])}
    has_purchases = all(isinstance(purchases.get(i), (int, float)) for i in ids)
    order = sorted(ids, key=lambda i: -purchases[i]) if has_purchases else ids
    category = values['category']
    themes = [f'겨울 {category} 컬렉션', f'나에게 맞는 {category} 스타일']
    summary = {'schema_version': 2, 'category': category, 'ids': ids, 'themes': themes, 'candidates': candidates, 'partial_candidates': partial, 'unknown_aspects': unknown, 'order': order, 'order_basis': '과거 겨울 온라인 구매 건수' if has_purchases else '확정 순서 (구매 건수 미확인)'}
    # 실제 확정 H&M 상품 설명에서 확인되는 디자인 특징만 연결한다.
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
    summary['verified_designs'] = verified_designs
    if ranked:
        summary['ranked_evidence'] = ranked
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
            f"무신사 랭킹에서 여성·공용 우선 비교한 '{subtype}' 관련 잠정 후보 {ranking_count}개와 "
            f"그 상품에 연결된 리뷰 {related_count}건을 별도로 분석했어요. "
            "상품명 규칙 분류이므로 실제 경량패딩 제품인지 최종 인증한 결과는 아닙니다.")
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
        ranked_lines.append('H&M 확정 상품 설명에서 확인한 디자인: ' + ', '.join(
            f"{x['label']} {x['products']}/{len(ids)}개" for x in verified_designs[:7]) + '.')
    if ranked:
        hood = next((x['products'] for x in ranked.get('related_shapes', []) if x['label'] == '후드형'), 0)
        hm_hood = next((x['products'] for x in verified_designs if x['label'] == '후드형'), 0)
        if hood and not hm_hood:
            ranked_lines.append('무신사 관련 상품에는 후드형이 확인되지만 확정 H&M 상품 설명에는 후드 근거가 없어 후드형 소구점은 제외합니다.')
    lines = [
        f"■ {subtype} 기획전에 적용할 소구점 제안",
        f"{market_scope}에서는 {interest} 등이 자주 언급됐어요.",
        "아우터 전체 통계는 비교 참고용이며, H&M 직접 후기나 소분류만의 평가가 아닙니다.",
        *ranked_lines,
        "",
        f"■ 선택한 {subtype} 상품에 적용할 때",
        f"선택한 {len(ids)}개 상품의 색상({color_text})을 나란히 보여 주고, "
        "각 상품 설명에서 확인된 칼라·여밈·포켓·실루엣 차이를 상품 카드에 안내하는 것을 우선 추천해요.",
        "리뷰에서 관심이 높았던 요소와 실제 상품 정보가 모두 뒷받침되는 범위에서만 표현하는 방식입니다.",
        "",
        "■ 확인이 필요한 표현",
        "사이즈 만족도·핏, 보온 성능, 실제 두께·착용감·내구성은 경쟁 리뷰만으로 보증할 수 없어요. "
        "상품별 실측이나 검증 자료가 확보되기 전에는 광고의 확정적 표현에서 제외하는 것이 좋아요.",
        "",
        "■ 현재 정리 — 공통 소구점 후보",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(f"{i}. {c['label']} — H&M 확정 상품 {len(c['matched'])}/{len(ids)}개에서 확인")
    if partial:
        lines.append("일부 상품만 확인된 차별화 요소는 해당 상품 카드에만 활용할 수 있어요: "
                     + ', '.join(c['label'] for c in partial[:3]) + '.')
    lines.extend([
        "추천 기획전 주제: " + ' / '.join(f"{n+1}. {t}" for n, t in enumerate(themes)),
        "현재 가격·할인·재고는 확인되지 않았으므로 문구에 넣지 않습니다.",
        "공통 소구점 2~3개를 번호로 선택해 주세요.",
    ])
    return {"final_answer": '\n'.join(lines), "campaign_summary": summary, "data_sources": ["musinsa", "hm"], "data_purpose": "12단계 추천 근거"}


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
            "공통 소구점 2~3개를 선택해 주세요. 예: 소구점: 1, 2\n"
            "가격·할인·기간을 따로 알려주지 않으면 미정으로 저장하며 광고에 사용하지 않습니다. "
            "기획전 주제는 1번, 상품 노출은 검증된 구매 기록 순서가 기본값입니다. "
            "다른 주제·혜택·노출 순서를 지정하려면 기존 6개 항목 양식을 사용하세요.")


def parse_selection(text, summary):
    if summary.get("schema_version") != 2:
        raise ValueError("워크플로 12단계 재실행으로 추천 결과를 갱신하세요.")
    # 선택값만 보낸 경우, 확인되지 않은 상품 혜택은 미정으로 안전하게 둔다.
    # 정형 양식은 아래 기존 검증을 그대로 적용한다.
    compact = re.fullmatch(r'\s*소구점\s*[:：]\s*([1-9][0-9]*(?:\s*[,，]\s*[1-9][0-9]*){1,2})\s*', text)
    if compact:
        text = ('주제: 1\n소구점: ' + compact.group(1).replace('，', ',')
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
    theme=choose(fields['주제'],summary['themes'])
    points=[choose(v.strip(),summary['candidates'])['label'] for v in fields['소구점'].split(',')]
    if not 2 <= len(points) <= 3 or len(set(points)) != len(points): raise ValueError("서로 다른 소구점 2~3개를 선택하세요.")
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
    return {"theme":theme,"points":points,"prices_krw":price,"discount_percent":discount,"period":period,"display_order":order,"claim_policy":"미정 값 및 검증되지 않은 기능·품질 표현은 광고에 사용 금지"}


def updates(text, summary):
    return {"selling_points":json.dumps(parse_selection(text,summary),ensure_ascii=False,sort_keys=True)}
