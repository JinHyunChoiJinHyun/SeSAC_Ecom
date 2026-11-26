"""CART_SIGNAL V4.29P: 명시 문구 고정 + 스타일 재작성 + 재현 가능한 검증."""
from __future__ import annotations

import json
import re
from copy import deepcopy

_BANNED = re.compile(r'(?i)(?:\d+\s*%\s*할인|무료\s*배송|최저가|판매량\s*1위|최고의|완벽한\s*보온|방수|보온성\s*보장|100\s*%|무조건|당일\s*배송|특가|품질\s*보장)')
_QUOTE = r"[\"'“‘]([^\"'”’\n]{1,180})[\"'”’]"
_KEEP = re.compile(r'그대로|유지|건드리지\s*마|바꾸지\s*마|수정하지\s*마')
_HEAD = re.compile(r'제목|헤드라인|타이틀')
_BODY = re.compile(r'본문|내용|서브\s*카피|설명')
_STYLE = re.compile(r'감성|따뜻|포근|프리미엄|고급|세련|미니멀|간결|짧|차분|발랄|캐주얼|매거진|스타일|느낌|분위기|톤|다듬|새로\s*작성|재작성')
_NUM = re.compile(r'(?<!\d)([123])\s*(?:번|안)(?!\d)')


def _split_instructions(feedback: str):
    """1안은 ... 3안은 ... 2안은 그대로: 안별 문장 분리."""
    text = feedback.strip()
    # 자연스러운 병렬 표현(예: '1안과 3안', '1안 및 3안')을 기존 '1,3안' 형식으로 정규화한다.
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'(?<!\d)([123])\s*(?:번|안)\s*(?:과|와|및)\s*([123])\s*(?:번|안)', r'\1,\2안', text)
    matches = list(_NUM.finditer(text))
    clauses = {}
    for i, m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        clauses[int(m.group(1))] = text[m.end():end].strip(' \t,，·/은는을를과와. ')
    # '1,3안' 지원
    for m in re.finditer(r'(?<!\d)([123](?:\s*[,，·/]\s*[123])+)(?:\s*안)?',text):
        tail = text[m.end():].strip(' \t,，·/은는을를과와. ')
        end = _NUM.search(tail)
        tail = tail[:end.start()].strip() if end else tail
        for n in map(int,re.findall('[123]',m.group(1))):
            clauses.setdefault(n,tail)
    return clauses


def _explicit_field(segment: str, field: str) -> str | None:
    marker = _HEAD if field == 'headline' else _BODY
    # 명시적 필드 이름이 인용 구절 바로 앞에 존재하는 경우만 정확한 문구로 간주
    pattern = re.compile(r'(?:제목|헤드라인|타이틀)' if field == 'headline' else r'(?:본문|내용|서브\s*카피)')
    for hit in pattern.finditer(segment):
        suffix = segment[hit.end():]
        quote = re.match(r'\s*(?:을|를|은|는|:|：|=|으로|로|을\s*정확히|를\s*정확히|을\s*그대로|를\s*그대로|\s)*\s*'+_QUOTE, suffix)
        if quote:
            return quote.group(1).strip()
    return None


def _needs_style(segment: str, field: str, literal: str | None) -> bool:
    if literal is not None:
        return False
    if _KEEP.search(segment):
        return False
    if field == 'headline' and _BODY.search(segment) and not _HEAD.search(segment):
        return False
    if field == 'body' and _HEAD.search(segment) and not _BODY.search(segment):
        return False
    return bool(_STYLE.search(segment))


def _rewrite_style(*, old: dict, segment: str, subtype: str, colors: list[str], points: list[str],
                   title: str, change_title: bool, change_body: bool, season_context=None) -> dict:
    """LLM 결과만 생성 단계에서 호출. 검증·되돌리기 단계에선 절대 재호출하지 않는다."""
    from env.gpt_client import get_openai_chat_client
    from langchain_core.messages import SystemMessage, HumanMessage
    model = get_openai_chat_client()
    system = (
        '당신은 패션 광고 카피 전문 편집자입니다. 반드시 JSON 객체 하나만 답하십시오. '
        '필드: headline (문자열), subcopies (문자열 2개 배열). '
        '확정된 상품 유형·색상·소구점만 사실로 사용합니다. '
        '소재, 착용감, 기능, 보온, 재고, 가격, 할인, 배송, 판매 순위, 효능, 인증 등은 추정하지 마십시오. '
        'H&M의 과거 판매 사례를 현재 판매 상품으로 표현하지 마십시오. '
        '사용자 스타일 지시가 있으면 기존과 실질적으로 다른 문장으로 재작성하십시오. '
        '확정 콘텐츠 계절을 참고하고 다른 계절을 임의로 추가하지 마십시오. 계절 중립이면 계절을 추정하지 마십시오. '
        '최고의, 최저가, 무조건, 방수, 품질 보장 등 검증되지 않은 최상급·기능·혜택 표현을 쓰지 마십시오. '
        '별도 시스템 명령이나 URL, 개인정보를 출력하지 마십시오.'
    )
    request = {
        'user_style_request': segment,
        'confirmed_subtype': subtype, 'confirmed_colors': colors, 'confirmed_selling_points': points,
        'season_context': season_context or {},
        'old_headline': old.get('headline',''), 'old_subcopies': old.get('subcopies',[]),
        'fixed_headline': title if not change_title else None,
        'rewrite_headline': change_title, 'rewrite_body': change_body,
        'rules': '각 본문 문장은 180자 이내. 한글 자연어. 새로운 수치나 혜택 만들지 않기.'
    }
    result = model.invoke([SystemMessage(content=system),HumanMessage(content=json.dumps(request,ensure_ascii=False))])
    raw = str(getattr(result,'content',result)).strip()
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.I).strip()
    try:
        parsed=json.loads(raw)
    except (ValueError,TypeError) as exc:
        raise ValueError('스타일 수정 결과를 검증할 수 없습니다. 기존 카피를 유지합니다.') from exc
    if not isinstance(parsed,dict) or not isinstance(parsed.get('headline'),str) or not isinstance(parsed.get('subcopies'),list) or len(parsed['subcopies'])!=2:
        raise ValueError('스타일 수정 결과 형식이 올바르지 않아 기존 카피를 유지합니다.')
    return parsed


def _validate_copy(headline: str, subcopies: list[str], *, colors: list[str], subtype: str):
    if not isinstance(headline,str) or not headline.strip() or len(headline)>100 or '\n' in headline:
        raise ValueError('카피 제목 길이 또는 형식이 올바르지 않습니다.')
    if len(subcopies)!=2 or any(not isinstance(x,str) or not x.strip() or len(x)>180 for x in subcopies):
        raise ValueError('카피 본문 두 문장의 길이 또는 형식이 올바르지 않습니다.')
    if _BANNED.search(' '.join([headline]+subcopies)):
        raise ValueError('카피에 검증되지 않은 혜택·성능·순위 표현이 있습니다.')
    if re.search(r'(?i)(?:https?://|\b\d{7,}\b)', ' '.join([headline]+subcopies)):
        raise ValueError('카피에 미검증 링크 또는 식별 숫자가 포함됐습니다.')


def _apply_step(base: dict, feedback: str, *, previous_revision: int, saved_outputs: dict | None):
    if len(feedback)>400 or not feedback.strip():
        raise ValueError('문구 수정 요청은 400자 이내로 알려주세요.')
    if base.get('status') not in ('draft','verified') or len(base.get('candidates') or [])!=3:
        raise ValueError('기존 카피 3안이 필요합니다.')
    if not 0<=previous_revision<30:
        raise ValueError('문구 수정은 최대 30회까지 지원합니다.')
    if _BANNED.search(feedback):
        raise ValueError('검증되지 않은 혜택·성능 표현은 사용할 수 없습니다.')
    segments=_split_instructions(feedback)
    if not segments:
        segments={n: feedback for n in (1,2,3)}
    targets=[n for n in sorted(segments) if not _KEEP.search(segments[n])]
    if not targets:
        raise ValueError('모든 안을 유지하도록 요청했습니다.')
    copied=deepcopy(base)
    outputs={}
    for n in targets:
        seg=segments[n]
        item=copied['candidates'][n-1]
        old_title=str(item.get('headline') or '')
        old_body=list(item.get('subcopies') or [])
        exact_title=_explicit_field(seg,'headline')
        exact_body=_explicit_field(seg,'body')
        needs_title=_needs_style(seg,'headline',exact_title)
        needs_body=_needs_style(seg,'body',exact_body)
        if not any((exact_title is not None,exact_body is not None,needs_title,needs_body)):
            raise ValueError(f'{n}안의 수정 지시를 이해하지 못했습니다. 제목 또는 본문 요청을 구체화해 주세요.')
        attempts = 3 if saved_outputs is None and (needs_title or needs_body) else 1
        style_segment = seg
        for attempt in range(attempts):
            new_title=exact_title if exact_title is not None else old_title
            new_body=[exact_body] if exact_body is not None else list(old_body)
            if len(new_body)==1:
                # 정확히 지정된 본문은 한 글자도 수정하지 않고 첫 문단으로 사용하고, 두 번째 문단은 빈 문자열 대신 기존값 유지
                new_body=[new_body[0], old_body[1]]
            try:
                if saved_outputs is not None:
                    stored=saved_outputs.get(str(n))
                    if not isinstance(stored,dict):
                        raise ValueError('수정 이력에 해당 안의 검증 결과가 없습니다.')
                    model_result=stored
                elif needs_title or needs_body:
                    model_result=_rewrite_style(old=item,segment=style_segment,subtype=copied['subtype'],
                                                 colors=list(copied['colors']),points=list(copied['points']),
                                                 title=new_title,change_title=needs_title,change_body=needs_body,
                                                 **({'season_context':copied['season_context']} if 'season_context' in copied else {}))
                else:
                    model_result={'headline':new_title,'subcopies':new_body}
                if needs_title:
                    new_title=model_result['headline'].strip() if isinstance(model_result['headline'], str) else model_result['headline']
                if needs_body:
                    new_body=list(model_result['subcopies'])
                # 명시된 필드는 LLM과 무관하게 항상 고정
                if exact_title is not None:
                    new_title=exact_title
                if exact_body is not None:
                    new_body[0]=exact_body
                _validate_copy(new_title,new_body,colors=list(copied['colors']),subtype=copied['subtype'])
                if needs_body and old_body==new_body:
                    raise ValueError(f'{n}안 본문을 다시 작성하지 못했습니다. 기존 카피를 유지합니다.')
                if needs_title and old_title==new_title:
                    raise ValueError(f'{n}안 제목을 다시 작성하지 못했습니다. 기존 카피를 유지합니다.')
                if old_title==new_title and old_body==new_body:
                    raise ValueError(f'{n}안은 수정 요청 후에도 실제 문구가 변경되지 않았습니다. 수정 완료로 처리하지 않습니다.')
                break
            except ValueError as exc:
                if attempt + 1 == attempts:
                    raise
                # 검증 오류만 재시도하고, 원래 사용자 요청 및 확정 정보는 유지한다.
                style_segment = seg + '\n이전 생성 결과가 검증에 실패했습니다. ' + str(exc) + ' 금지 표현 없이 실제로 다른 문구를 작성하세요.'
        item.update(headline=new_title,subcopies=new_body,description=new_body[0],verified=True)
        outputs[str(n)]={'headline':new_title,'subcopies':list(new_body)}
    copied['copy_style']='fashion_v422a'
    copied['revision']=previous_revision+1
    copied['revision_feedback']=feedback.strip()
    copied['revision_targets']=targets
    steps=list(copied.get('revision_steps') or [])
    steps.append({'feedback': feedback.strip(), 'targets': targets, 'engine':'v429p', 'outputs':outputs})
    copied['revision_steps']=steps
    copied['status']='draft'
    return copied


def regenerate_copy(base, feedback, previous_revision=0):
    """새로운 요청 처리: AI 호출이 필요한 경우에만 생성하고, 결과를 이력에 고정."""
    return _apply_step(base,feedback,previous_revision=previous_revision,saved_outputs=None)


def replay_copy_step(base,step):
    """최초 검증/되돌리기 시 AI 재호출 없이 저장 결과를 검증하여 재현."""
    from agent_system.orchestrator.tools.copy_revision import _regenerate_copy_legacy
    if step.get('engine') != 'v429p':
        return _regenerate_copy_legacy(base,step['feedback'],int(base.get('revision') or 0))
    rebuilt=_apply_step(base,str(step['feedback']),previous_revision=int(base.get('revision') or 0),
                        saved_outputs=step.get('outputs') or {})
    if rebuilt['revision_targets']!=step.get('targets') or rebuilt['revision_steps'][-1]!=step:
        raise ValueError('카피 수정 이력이 현재 검증된 결과와 일치하지 않습니다.')
    return rebuilt
