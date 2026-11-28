"""신규 기획전의 상품군/과거 거래 채널. 고객 성별과 분리하고 구형 Run은 보존한다."""
from __future__ import annotations
import re

GROUPS = {'women': ('Ladieswear',), 'men': ('Menswear',),
          'both': ('Ladieswear', 'Menswear'),
          'all': ('Ladieswear', 'Menswear', 'Divided'),
          'legacy': ('Ladieswear', 'Divided')}
LABELS = {'women': '여성용(Ladieswear) 상품', 'men': '남성용(Menswear) 상품',
          'both': '여성용·남성용(Ladieswear·Menswear) 상품',
          'all': '지원 상품군 전체(Ladieswear·Menswear·Divided)',
          'legacy': '여성용(Ladieswear·Divided) 상품(구형 분석 범위)'}
CHANNELS = {'online': (2,), 'offline': (1,), 'all': (1, 2)}
CHANNEL_LABELS = {'online': '온라인', 'offline': '오프라인', 'all': '온라인·오프라인'}
LEGACY = {'product_gender': 'legacy', 'channel': 'online'}
MODEL_NOTE = '구매 예측은 상품 유형×색상 기준이며 상품 성별별·판매 채널별 구매 예측이 아닙니다. 기존 모델의 전체 채널 구매 이력을 사용합니다.'


def parse_scope(request):
    text = str(request or '')
    # 고객 성별은 상품 성별과 별개이며 원천 고객 테이블에 확인값이 없다.
    if re.search(r'(?:남성|여성|남자|여자)\s*(?:고객|회원|소비자|타[깃겟])|(?:고객|회원|타[깃겟])\s*(?:성별|은|는|을|를|이|가)\s*(?:남성|여성|남자|여자)', text):
        raise ValueError('고객 성별은 H&M 고객 데이터에서 확인할 수 없습니다. 상품 성별만 지정하거나 고객 성별 조건을 제외해 주세요.')
    if re.search(r'공용|유니섹스|unisex|아동|키즈|유아|어린이', text, re.I):
        raise ValueError('공용·아동 상품의 분류는 아직 검증되지 않았습니다. 여성용·남성용 또는 지원 상품군 전체 범위를 지정해 주세요.')
    if re.search(r'제외|아닌|아니라|말고|이외', text) and re.search(r'여성|남성|여자|남자|온라인|오프라인|매장', text):
        raise ValueError('부정·제외 조건이 포함되어 상품군 또는 채널을 안전하게 확정할 수 없습니다. 원하는 조건을 직접 지정해 주세요.')
    # 성별 단어가 상품을 수식하는 경우만 상품군으로 확정한다.
    merchandise = r'(?:의류|옷|상품|패션|아우터|셔츠|니트|경량\s*패딩|패딩|코트|팬츠|바지|재킷|자켓|티셔츠|컬렉션|기획전)'
    women = bool(re.search(r'(?:여성|여자)(?:용)?\s*'+merchandise+r'|여성복|여자옷|\bwomen(?:’s|\x27s)?\s+apparel|womenswear', text, re.I))
    men = bool(re.search(r'(?:남성|남자)(?:용)?\s*'+merchandise+r'|남성복|남자옷|\bmen(?:’s|\x27s)?\s+apparel|menswear', text, re.I))
    if re.search(r'(?:남성|여성|남자|여자)\s*(?:에게|(?:을|를)?\s*대상|위한|만\s*대상)|(?:남성|여성|남자|여자)\s*\d+\s*대', text):
        raise ValueError('고객 성별 조건을 적용할 수 없습니다. 상품 성별과 고객 조건을 구분해 주세요.')
    if re.search(r'(?:여성|여자)\s*(?:및|과|와|·|/|,|&|이랑)\s*(?:남성|남자)|(?:남성|남자)\s*(?:및|과|와|·|/|,|&|이랑)\s*(?:여성|여자)', text):
        women = men = True
    if not (women or men) and re.search(r'여성|남성|여자|남자', text):
        raise ValueError('상품 성별 조건을 명확히 지정해 주세요. 예: 여성 의류 또는 남성 의류.')
    if re.search(r'남녀|남여', text):
        women = men = True
    gender = 'women' if women and not men else 'men' if men and not women else 'both' if women and men else 'all'
    offline = bool(re.search(r'오프\s*라인|매장|팝업\s*스토어', text))
    online = bool(re.search(r'온\s*라인|비대면', text))
    if re.search(r'온\s*[·ㆍ/＆&-]?\s*오프라인|전체\s*채널|모든\s*채널', text):
        online = offline = True
    channel = 'online' if online and not offline else 'offline' if offline and not online else 'all'
    return {'product_gender': gender, 'channel': channel}


def normalize(scope):
    if not isinstance(scope, dict) or set(scope) != {'product_gender', 'channel'} or scope['product_gender'] not in GROUPS or scope['channel'] not in CHANNELS:
        raise ValueError('기획전 상품군·채널 조건이 올바르지 않습니다.')
    return dict(scope)


def plan_scope(plan=None):
    return normalize((plan or {}).get('campaign_scope', LEGACY))


def bind_plan(plan, scope):
    scope = normalize(scope)
    if 'campaign_scope' in plan and plan_scope(plan) != scope:
        raise ValueError('이미 확정된 상품군·채널 조건과 다릅니다. 새 분석을 시작하세요.')
    return {**plan, 'campaign_scope': scope}


def run_scope(run):
    scope = normalize(run.get('campaign_scope', LEGACY))
    if run.get('period_plan') and plan_scope(run['period_plan']) != scope:
        raise ValueError('상품군·채널과 공통 분석 계획이 다릅니다. 새 분석을 시작하세요.')
    return scope


def labels(plan=None):
    scope = plan_scope(plan)
    return LABELS[scope['product_gender']], CHANNEL_LABELS[scope['channel']]


def eligible_articles(articles, scope):
    scope = normalize(scope)
    if 'index_group_name' not in articles:
        raise ValueError('H&M 상품군 분류 컬럼이 없습니다.')
    out = articles.loc[articles['index_group_name'].isin(GROUPS[scope['product_gender']])].copy()
    if out.empty and scope['product_gender'] != 'legacy':
        raise ValueError('지정 상품군의 H&M 상품을 확인할 수 없습니다. 다른 상품군으로 대체하지 않습니다.')
    return out


def channel_sql(plan=None):
    # 기존 SQL의 연령/날짜 파라미터 순서를 유지. 값은 위 허용 목록에서만 생성한다.
    ids = CHANNELS[plan_scope(plan)['channel']]
    return 't.sales_channel_id = 2' if ids == (2,) else 't.sales_channel_id = 1' if ids == (1,) else 't.sales_channel_id IN (1, 2)'


def unsupported_scope(request):
    try: parse_scope(request)
    except ValueError as exc: return str(exc)
    return ''


def image_terms(gender):
    return {'women': ("women's apparel", 'adult female fashion models'),
            'men': ("men's apparel", 'adult male fashion models'),
            'both': ("women's and men's apparel", 'adult female and/or male fashion models'),
            'all': ('adult apparel, no unisex classification implied', 'adult fashion models'),
            'legacy': ("women's apparel", 'adult female fashion models')}[gender]
