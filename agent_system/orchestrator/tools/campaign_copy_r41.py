"""R41: 확정 R40 고객군에 대한 메시지 초안·수정·승인. 고객 발송 없음."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
import secrets

from . import campaign_target_r39 as target39
from .campaign_copy import _colors

KEYS = {'핵심 타깃':'core', '교차판매':'cross_sell', '재활성화':'reactivation'}
FIELDS = {'제목':'headline', '본문':'body', 'CTA':'cta'}
# 확인되지 않은 판매 조건 및 개인화 주장을 보수적으로 제한한다.
BANNED = re.compile(r'할인|쿠폰|무료|배송|최저가|특가|재고|품절|마감임박|한정수량|보온|방수|발수|소재|착용감|경량성|품질보장|인증|판매량|구매하셨|구매하신|구매하신지|오랜만|돌아오세요|휴면|고객님|당신의\s*구매|https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]+|\d|[<>]', re.I)
GROUP = r'(핵심\s*타깃|교차\s*판매|재활성화)'
PREFIX = '__cart_segment_copy__:'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def source(run, campaign, *, reuse_approved=False):
    t = target39.confirmed_for_report(run, campaign)
    # 완료 Run에서는 기존 승인 문구의 읽기 전용 검증만 허용한다.
    eligible = target39.delivery_allowed(run) if reuse_approved else target39.active(run)
    if not t or t.get('policy') != 'r40_v1' or not eligible:
        raise ValueError('먼저 R40 고객 타깃 선정과 승인을 완료해 주세요.')
    values = campaign.get('values') or {}
    subtype = str(values.get('subtype') or values.get('category') or '').strip()
    colors = _colors(campaign)
    if not subtype or not colors:
        raise ValueError('확정된 기획전 유형과 색상이 필요합니다.')
    basis = _digest({'run':run.get('run_id'), 'fingerprint':t['fingerprint'], 'selection':t.get('output_dir'), 'groups':t['groups']})
    return t, subtype, colors, basis


def _direction_particle(word):
    # 받침이 없거나 ㄹ 받침이면 로, 그 외 한글 받침은 으로.
    last = word.rstrip()[-1:]
    if last and '가' <= last <= '힣':
        return '로' if (ord(last) - ord('가')) % 28 in (0, 8) else '으로'
    return '로'


def _template(key, subtype, colors, style='기본'):
    palette = '·'.join(colors)
    rows = {
        'core': (f'취향을 담은 {subtype} 컬렉션', f'{palette} 컬러의 {subtype} 기획전에서 나다운 스타일을 골라보세요.', '기획전 상품 만나보기', f'나다운 선택, {subtype}'),
        'cross_sell': (f'{subtype}{_direction_particle(subtype)} 만나는 새로운 스타일', f'익숙한 일상에 새로운 컬러를 더해보세요. {palette} 컬러의 {subtype} 스타일을 비교해 보세요.', '새로운 스타일 둘러보기', '새로운 스타일의 시작'),
        'reactivation': (f'새로운 계절의 {subtype} 스타일', f'{palette} 컬러의 {subtype} 기획전에서 지금의 취향에 맞는 스타일을 살펴보세요.', '관심 스타일 살펴보기', '취향을 발견하는 순간'),
    }
    h,b,c,short = rows[key]
    if style == '간결':
        h, b = short, {'core':f'{palette} {subtype}, 나다운 선택을 만나보세요.', 'cross_sell':f'{palette} {subtype}{_direction_particle(subtype)} 새로운 스타일을 살펴보세요.', 'reactivation':f'{palette} {subtype}, 오늘의 취향을 찾아보세요.'}[key]
    elif style == '감성':
        h = {'core':f'나를 닮은 컬러, {subtype}', 'cross_sell':'일상에 더하는 새로운 무드', 'reactivation':'취향이 머무는 순간'}[key]
        b = {'core':f'나만의 분위기를 담아보세요. {palette} {subtype} 컬렉션.', 'cross_sell':f'익숙한 하루에도 새로운 분위기를. {palette} {subtype} 스타일을 만나보세요.', 'reactivation':f'지금의 나에게 어울리는 무드. {palette} {subtype}에서 찾아보세요.'}[key]
    return dict(headline=h, body=b, cta=c)


def _validate_text(text, limit):
    if not isinstance(text,str) or not text.strip() or len(text)>limit or any(ord(ch)<32 for ch in text) or BANNED.search(text):
        raise ValueError('문구 길이 또는 미확인 혜택·기능·개인정보 표현을 확인해 주세요. 기존 문구를 유지합니다.')


def validate(package, run, campaign, *, reuse_approved=False):
    if reuse_approved and (not isinstance(package, dict) or package.get('status') != 'approved'):
        raise ValueError('승인된 고객군별 문구만 재사용할 수 있습니다.')
    t, _, _, basis = source(run,campaign,reuse_approved=reuse_approved)
    if not isinstance(package,dict) or package.get('basis') != basis or package.get('status') not in ('review','rejected','approved') or type(package.get('revision')) is not int or not 1 <= package['revision'] <= 30:
        raise ValueError('현재 타깃과 문구 검토 상태가 일치하지 않습니다.')
    expected = {g['key']:g for g in t['groups']}
    rows = package.get('rows')
    if not isinstance(rows,dict) or set(rows)!=set(expected):
        raise ValueError('고객군 문구 구성이 확정 타깃과 일치하지 않습니다.')
    for key,row in rows.items():
        g = expected[key]
        if set(row) != {'name','count','strategy','headline','body','cta'} or any(row[f]!=g[f] for f in ('name','count','strategy')):
            raise ValueError('고객군 집계 또는 전략이 변경되었습니다.')
        for field,limit in [('headline',100),('body',180),('cta',50)]:
            _validate_text(row[field],limit)
    if len({row['headline'] for row in rows.values()})!=len(rows):
        raise ValueError('고객군별 제목을 구분해 주세요.')
    if package.get('content_hash')!=_digest(rows):
        raise ValueError('저장 문구 무결성 확인 실패')
    if package['status']=='approved' and package.get('approved_hash')!=_digest({'basis':basis,'revision':package['revision'],'rows':rows}):
        raise ValueError('승인된 문구가 검토 내용과 일치하지 않습니다.')
    return package


def _seal(package):
    package['content_hash']=_digest(package['rows'])
    package['token']=secrets.token_hex(16)
    return package


def generate(run,campaign,revision=1):
    t, subtype, colors, basis = source(run,campaign)
    rows={g['key']:dict(name=g['name'],count=g['count'],strategy=g['strategy'],**_template(g['key'],subtype,colors)) for g in t['groups']}
    return validate(_seal(dict(basis=basis,status='review',revision=revision,rows=rows)),run,campaign)


def approved(run,campaign):
    try:
        p=validate(run.get('segment_copy_r41'),run,campaign,reuse_approved=True)
        return p if p['status']=='approved' else None
    except (ValueError,TypeError,KeyError):
        return None


def action_valid(run, action):
    p=run.get('segment_copy_r41') or {}
    return bool(run.get('status')=='paused' and p.get('status')=='review' and p.get('token') and action.get('token')==p['token'] and action.get('run_id')==run.get('run_id') and action.get('command') in ('approve','reject'))


def is_request(text):
    text=str(text).strip()
    return bool(text.startswith(PREFIX) or re.search(r'(?:세그먼트별|고객군별|그룹별|'+GROUP+r').{0,35}(?:문구|카피|제목|본문|CTA)',text,re.I))


def render(p):
    lines=[f"고객군별 마케팅 문구 · {p['revision']}차 · {'승인 완료' if p['status']=='approved' else '검토 대기' if p['status']=='review' else '반려'}", '공통 페이지 유지 · 실제 판매 상품/가격/혜택 미확정 · 고객 실제 발송 없음']
    for row in p['rows'].values():
        lines += ['',f"■ {row['name']} ({row['count']:,}명)",f"전략: {row['strategy']}",f"제목: {row['headline']}",f"본문: {row['body']}",f"CTA: {row['cta']}"]
    if p['status']=='approved':
        lines += ['', '기획전 보고서 만들어줘 — 승인 문구를 반영합니다.']
    else:
        lines += ['', '수정 예: 교차판매 제목을 "새로운 스타일의 시작"으로 바꿔줘 / 재활성화 문구를 간결하게 수정해줘', '검토 버튼으로 승인 또는 반려해 주세요.' if p['status']=='review' else '고객군별 문구 다시 생성해줘 — 새 검토안을 생성합니다.']
    return '\n'.join(lines)


def handle(text,run,campaign,pending=None):
    """검토 상태만 반환한다. 잘못된 요청은 호출자가 원본 Run을 유지한다."""
    if pending:
        raise ValueError('다른 승인 요청을 먼저 처리해 주세요.')
    source(run,campaign)
    text=str(text).strip()
    p=run.get('segment_copy_r41')
    if p:
        p=validate(p,run,campaign)
    if text.startswith(PREFIX):
        command=text[len(PREFIX):]
        if not p or p['status']!='review' or command not in ('approve','reject'):
            raise ValueError('현재 검토 중인 문구가 없습니다.')
        new=deepcopy(p)
        new['status']='approved' if command=='approve' else 'rejected'
        new.pop('token',None)
        if command=='approve':
            new['approved_hash']=_digest({'basis':new['basis'],'revision':new['revision'],'rows':new['rows']})
        return validate(new,run,campaign)
    if p and p['status']=='approved':
        # 승인 후 수정은 기존 보고서/메일의 내용과 불일치할 수 있어 이번 단계에서는 잠근다.
        if re.search(r'수정|변경|바꿔|생성|다시\s*작성|반려',text):
            raise ValueError('이미 승인된 문구입니다. 보고서와의 일관성을 위해 현재 Run에서는 수정하지 않습니다.')
        return p
    if not p or p['status']=='rejected':
        if re.search(r'생성|만들|작성',text) and not re.search(GROUP,text):
            return generate(run,campaign,1 if not p else p['revision']+1)
        if p:
            return p
        raise ValueError('먼저 “고객군별 마케팅 문구 생성해줘”라고 요청해 주세요.')
    if re.search(r'승인|확정|반려',text) and not re.search(r'수정|바꿔|변경',text):
        return p  # 텍스트 승인 금지: 현재 토큰의 버튼 사용
    match=re.fullmatch(GROUP+r'\s*(제목|본문|CTA)\s*(?:을|를|은|는|:)?\s*["“”\'‘’]([^\n]+?)["“”\'‘’]\s*(?:으로|로)?\s*(?:바꿔\s*줘|수정\s*해줘|변경\s*해줘)?[.!]?',text,re.I)
    style=re.fullmatch(GROUP+r'\s*(문구|카피|제목|본문|CTA)\s*(?:을|를|은|는)?\s*(더\s*)?(간결하게|짧게|감성적으로|감성적인\s*느낌으로)\s*(?:수정해줘|바꿔줘|해줘)[.!]?',text,re.I)
    # 스타일 요청이 아닌 경우에만, 따옴표 없는 명시적 치환을 허용한다.
    if not match and not style:
        match=re.fullmatch(GROUP+r'\s*(제목|본문|CTA)\s*(?:을|를|은|는|:)\s*([^"“”\'‘’\n]+?)\s*(?:으로|로)\s*(?:바꿔\s*줘|수정\s*해줘|변경\s*해줘)[.!]?',text,re.I)
    if not match and not style:
        if re.search(r'수정|변경|바꿔|느낌',text):
            raise ValueError('그룹과 수정할 항목을 지정해 주세요. 예: 교차판매 제목을 "새로운 스타일"로 바꿔줘')
        return p
    m=match or style
    key=KEYS[next(k for k in KEYS if k.replace(' ','')==re.sub(r'\s+','',m[1]))]
    if key not in p['rows']:
        raise ValueError('해당 고객군은 선정 인원이 없어 문구를 생성하지 않습니다.')
    new=deepcopy(p)
    if match:
        field=FIELDS[match[2].upper() if match[2].lower()=='cta' else match[2]]
        new['rows'][key][field]=match[3]
    else:
        _,subtype,colors,_=source(run,campaign)
        values=_template(key,subtype,colors,'감성' if '감성' in style[4] else '간결')
        fields=('headline','body') if style[2] in ('문구','카피') else (FIELDS[style[2].upper() if style[2].lower()=='cta' else style[2]],)
        for f in fields: new['rows'][key][f]=values[f]
    if new['rows']==p['rows']:
        return p
    new['revision']+=1
    return validate(_seal(new),run,campaign)
