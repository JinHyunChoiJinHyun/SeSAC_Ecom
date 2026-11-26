"""승인된 페이지 사양 기반 독립 PNG 생성 및 사내 첨부 연결 (Slack 전용)."""
from __future__ import annotations
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets

from . import campaign_page as page
from . import campaign_page_design_r4282 as design


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def intent(text, previous='', reviewing=False):
    q=str(text or '').strip().rstrip('.!?')
    if re.fullmatch(r'기획전\s*페이지\s*완성본\s*승인\s+[0-9a-f]{12}',q): return 'approve'
    if re.fullmatch(r'기획전\s*페이지\s*완성본\s*승인',q): return 'approval_help'
    if re.search(r'보고서|워드|word|pdf',q,re.I) or (re.search(r'메일.*(?:보내|발송|전송)',q) and not re.search(r'발송용\s*png',q,re.I)): return None
    if re.search(r'고객군|세그먼트|핵심\s*타깃|교차판매|재활성화|고객\s*선정|카피\s*[123]안',q): return None
    if re.search(r'완성본|최종\s*(?:이미지|디자인|페이지)|페이지\s*완성',q) and not re.search(r'페이지\s*틀',q) and re.search(r'수정|바꿔|변경|강조|줄여|늘려|배치|유지|복원',q) and not re.search(r'이미지.*넣|완성해',q): return 'edit'
    if reviewing and re.search(r'제목|본문|전체\s*톤|배경|모델\s*컷|착장|상품\s*(?:컷|카드|을|를)|CTA|버튼',q,re.I) and re.search(r'수정|바꿔|변경|강조|줄여|늘려|배치|복원|다듬|간결|따뜻|세련',q) and not re.search(r'메일|보고서|페이지\s*틀',q): return 'edit'
    if re.search(r'페이지\s*틀.*(?:만들|생성|수정)|페이지.*(?:되돌|수정|변경)',q) and not re.search('완성',q): return None
    if re.search(r'(?:기획전\s*)?페이지|최종\s*(?:이미지|디자인|png)|(?:기획전|페이지)\s*완성본|메일\s*발송용\s*png|승인한\s*시안',q,re.I):
        if re.search(r'완성|최종|이미지.*넣|발송용\s*png',q,re.I) and re.search(r'만들|생성|완성해|넣|보여|재생성',q):
            if re.search(r'다시\s*(?:보여|확인)|보여\s*줘',q): return 'show'
            return 'regenerate' if re.search(r'다시\s*(?:만들|생성)|재생성|새로',q) else 'generate'
    if re.fullmatch(r'(?:그냥\s*|이대로\s*|이미지를?\s*넣어서\s*)?완성(?:본)?\s*(?:해|만들어)(?:\s*줘|\s*주세요)',q):
        if re.search(r'페이지|시안|배너',str(previous)) and not re.search(r'보고서|워드|PDF',str(previous),re.I): return 'generate'
        if reviewing and not re.search(r'보고서|워드|PDF|고객군|메일',str(previous),re.I): return 'generate'
        return 'ambiguous'
    return None


def source(run,campaign):
    from .audience_scope_r426 import require_scope
    if run.get('workflow')!='trend_planning' or run.get('status') in (None,'cancelled') or not run.get('run_id'):
        raise ValueError('현재 기획전을 먼저 시작해 주세요.')
    preview=run.get('page_preview') or {}; current=preview.get('current') or {}; spec=current.get('spec') or {}
    if preview.get('status')!='approved' or type(preview.get('revision')) is not int or current.get('revision')!=preview['revision'] or spec.get('revision')!=preview['revision']:
        raise ValueError('현재 기획전의 페이지 틀을 먼저 승인해 주세요.')
    values=campaign.get('values') or {}; subtype=str(values.get('subtype') or '').strip()
    try:
        selected=values.get('page'); selected=json.loads(selected) if isinstance(selected,str) else selected
        choice=preview.get('selected_copy') or {}
        number=selected['number']
        if (type(number) is not int or number not in (1,2,3) or preview.get('selected_copy_number')!=number
                or current.get('selected_copy_number')!=number or choice.get('number')!=number
                or choice.get('headline')!=selected.get('headline') or current.get('selected_headline')!=selected.get('headline')
                or not selected.get('headline') or choice.get('subcopies')!=selected.get('subcopies') or not values.get('products')):
            raise ValueError('승인된 카피와 페이지 시안이 일치하지 않습니다.')
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError('승인된 카피와 페이지 시안을 확인할 수 없습니다.') from exc
    colors=page._colors({},campaign)
    if not subtype or len(colors) not in (1,2,3) or len(set(colors))!=len(colors) or spec.get('color_text')!=' · '.join(colors):
        raise ValueError('승인된 상품 유형과 색상을 확인할 수 없습니다.')
    if not all(isinstance(spec.get(k),str) and spec[k].strip() for k in ('hero_title','hero_body','cta')):
        raise ValueError('승인된 제목·본문·CTA가 필요합니다.')
    scope=require_scope(run)
    # 고객 개인 정보, 원문 사용자 요청, 이메일은 이미지 API에 전달하지 않는다.
    if 'season_context' in spec:
        season=spec['season_context']['season']
    else:
        # 구형 승인 시안의 원래 source fingerprint를 유지한다.
        raw=str(run.get('request') or '')
        season=next((x for x in ('겨울','여름','봄','가을') if x in raw),'계절 중립')
    data={'run_id':str(run['run_id']),'revision':preview['revision'],'spec':deepcopy(spec),'subtype':subtype,'colors':colors,'scope':scope,'season':season,'products':values.get('products'),'selling_points':values.get('selling_points'),'schedule':values.get('schedule')}
    if 'campaign_scope' in run:
        from .campaign_scope_r43 import run_scope
        data['campaign_scope'] = run_scope(run)
        if any(r.get('analysis_product_gender') != data['campaign_scope']['product_gender'] or r.get('analysis_channel') != data['campaign_scope']['channel'] for r in run.get('verified_candidate_rows') or []):
            raise ValueError('페이지 상품군·채널과 검증 후보가 일치하지 않습니다.')
    return data,_digest(data)


def _folder(root,event,run):
    owner=str(event.get('user') or ''); channel=str(event.get('channel') or '')
    if not owner or not channel or not run.get('run_id'): raise ValueError('기획전 요청자 및 채널을 확인해 주세요.')
    key=_digest([owner,channel,str(run['run_id'])])[:32]
    return Path(root)/'backup'/'approval_demo_private'/'page_final_r428'/key


def read(root,event,run):
    path=_folder(root,event,run)/'state.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def _write(root,event,run,record):
    folder=_folder(root,event,run); folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=folder/(secrets.token_hex(12)+'.tmp')
    try:
        with os.fdopen(os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w',encoding='utf-8') as f:
            json.dump(record,f,ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,folder/'state.json')
    finally: tmp.unlink(missing_ok=True)


def _checked(root,record):
    from agent_system.approval_demo.delivery_r42 import validate_png
    path=Path(record['path']).resolve(); base=(Path(root)/'visualization_output'/'page_final_r428').resolve()
    if path.parent!=base or not path.is_file() or path.stat().st_size>3*1024*1024: raise ValueError('최종 PNG 파일을 확인할 수 없습니다.')
    data=path.read_bytes()
    if _hash(data)!=record['sha256']: raise ValueError('최종 PNG 무결성 확인 실패')
    validate_png(data); return data


def _image_api(prompt, size):
    from openai import OpenAI
    from PIL import Image
    if os.getenv('CART_IMAGE_ENABLED','').lower() not in ('1','true','yes') or not os.getenv('OPENAI_API_KEY'):
        raise ValueError('이미지 생성 API 설정이 필요합니다. CART_IMAGE_ENABLED=1 및 OPENAI_API_KEY를 확인해 주세요.')
    client=OpenAI(api_key=os.environ['OPENAI_API_KEY'],base_url='https://api.openai.com/v1',timeout=180.0,max_retries=0)
    model=os.getenv('CART_IMAGE_MODEL','gpt-image-2.5-flare')
    if not re.fullmatch(r'gpt-image-[a-z0-9.\-]+|chatgpt-image-latest',model): raise ValueError('이미지 모델 설정이 올바르지 않습니다.')
    response=client.images.generate(model=model,prompt=prompt,size=size,quality='medium',n=1,output_format='png')
    raw=base64.b64decode(response.data[0].b64_json,validate=True)
    if len(raw)>30*1024*1024: raise ValueError('생성 이미지 크기 제한 초과')
    with Image.open(BytesIO(raw)) as im:
        if im.width*im.height>20_000_000: raise ValueError('생성 이미지 해상도 제한 초과')
        return im.convert('RGB')


def build_assets(data, generate_image=None):
    generate_image=generate_image or _image_api
    colors=data['colors']; subtype=data['subtype']; spec=data['spec']; scope=data['scope']
    # 상품명/색상/스타일만 전달하며 가격 및 성능을 생성하지 않는다.
    from .campaign_scope_r43 import image_terms
    gender = (data.get('campaign_scope') or {}).get('product_gender', 'legacy')
    apparel, models = image_terms(gender)
    context=f"Concept fashion imagery, {apparel}, item: {subtype}; allowed garment colors: {', '.join(colors)}; season: {data['season']}; target styling age range: {scope['age_min']}-{scope['age_max']}; theme: {spec.get('theme','modern')}."
    points='; '.join(str(x)[:80] for x in (spec.get('points') or [])[:4])
    context+=f' Design composition reference only: {points}. Show visual color and design differences, not functional performance.'
    constraint=' Imaginary fashion concept only, no brands, no logos, no typography, no price, no promotional claims. Never depict an actual SKU.'
    hero=generate_image(design.hero_prompt(context,constraint,product_gender=gender),'1024x1536')
    cards=[]
    for i,(name,visual,description) in enumerate(design.designs(subtype)):
        color=colors[i%len(colors)]
        image=generate_image(design.card_prompt(context,constraint,subtype,color,visual,theme=spec.get('theme','modern'),product_gender=gender),'1024x1024')
        cards.append({'image':image,'name':f'{subtype} {name}','color':color,'description':description})
    return {'hero':hero,'cards':cards}


def _save_assets(root,event,run,token,assets):
    folder=_folder(root,event,run)/token
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    manifest=[]
    for i,asset in enumerate([assets['hero']]+[c['image'] for c in assets['cards']]):
        path=folder/f'{i}.png'
        asset.convert('RGB').save(path,format='PNG')
        path.chmod(0o600)
        manifest.append(_hash(path.read_bytes()))
    return {'hashes':manifest,'cards':[{k:c[k] for k in ('name','color','description') if k in c} for c in assets['cards']]}


def _load_assets(root,event,run,record):
    from PIL import Image
    token=record['token']
    if not re.fullmatch('[0-9a-f]{12}',token): raise ValueError('이미지 버전 식별값 오류')
    folder=_folder(root,event,run)/token
    meta=record.get('assets') or {}; images=[]
    if len(meta.get('hashes',[]))!=5 or len(meta.get('cards',[]))!=4: raise ValueError('원본 이미지가 없습니다. 완성본을 다시 생성해 주세요.')
    for i,h in enumerate(meta['hashes']):
        raw=(folder/f'{i}.png').read_bytes()
        if _hash(raw)!=h: raise ValueError('원본 이미지 무결성 확인 실패')
        with Image.open(BytesIO(raw)) as im: images.append(im.convert('RGB'))
    return {'hero':images[0],'cards':[dict(c,image=im) for c,im in zip(meta['cards'],images[1:])]}


def revise(data,assets,question,generate_image=None):
    """지원하는 변경만 적용하고 나머지 필드는 그대로 보존한다."""
    from .campaign_copy_r41 import _validate_text
    q=str(question).strip().translate(str.maketrans({'“':'"','”':'"','‘':"'",'’':"'"})); spec=data['spec']; changed=False
    if len(q)>400: raise ValueError('수정 요청은 400자 이내로 입력해 주세요.')
    explicit=[]
    # 정확히 지정된 문구만 교체. 스타일 요청만으로 제목을 덮어쓰지 않는다.
    for label,field,limit in [('제목','hero_title',60),('본문','hero_body',150),('CTA(?:\\s*버튼)?|버튼','cta',20)]:
        keep=re.search(r'(?:'+label+r')(?:\s*문구)?\s*(?:만|은|는|을|를|이)?\s*(?:유지|그대로|보존|변경하지|바꾸지)',q,re.I)
        exact=re.search(r'(?:'+label+r')(?:\s*문구)?\s*(?:만|을|를|은|는)?\s*["\x27]([^"\x27]+)["\x27]\s*(?:으로|로)',q,re.I)
        if keep and exact: raise ValueError('같은 문구에 유지와 변경 요청이 함께 있습니다.')
        if exact:
            value=exact.group(1); _validate_text(value,limit); explicit.append((field,value))
            changed |= spec[field]!=value; spec[field]=value
        elif field=='hero_body' and not keep and re.search(r'본문(?:\s*문구)?\s*(?:만|은|을|는)?\s*(?:더\s*)?(?:간결|짧게|감성)',q):
            value=f"{' · '.join(data['colors'])} {data['subtype']}, 나만의 스타일을 만나보세요."
            _validate_text(value,limit); changed |= spec[field]!=value; spec[field]=value
        elif field=='hero_title' and not keep and re.search(r'제목(?:\s*문구)?\s*(?:만|은|을|는)?\s*(?:더\s*)?(?:감성적으로|간결하게|세련되게)',q):
            value=f"나를 닮은 컬러, {data['subtype']}"
            _validate_text(value,limit); changed |= spec[field]!=value; spec[field]=value
        elif field=='cta' and not keep and re.search(r'(?:CTA(?:\s*버튼)?|버튼)(?:\s*문구)?\s*(?:만|은|을|는)?\s*(?:더\s*)?(?:직접적|간결|짧게|눈에 띄)',q,re.I):
            value='상품 둘러보기'; changed |= spec[field]!=value; spec[field]=value
    # 색상 및 레이아웃 유지 요청을 우선 적용.
    if not re.search(r'(?:톤|배경|테마|디자인).*?(?:유지|그대로)',q):
        if re.search(r'전체\s*톤|배경|테마|분위기',q):
            theme=('warm' if re.search('따뜻|감성|포근',q) else 'premium' if re.search('고급|세련|다크|어둡',q) else 'modern' if re.search('밝|깔끔|화이트|심플',q) else None)
            if theme:
                relative=bool(re.search(r'조금\s*더|좀\s*더|더\s*(?:따뜻|밝|어둡|진|연)',q))
                previous=spec.get('theme')
                if previous!=theme:
                    spec['theme']=theme; spec.pop('palette_adjust',None); changed=True
                if relative and theme=='warm':
                    old_level=int(spec.get('palette_adjust',0)); level=min(4,old_level+1)
                    changed |= level!=old_level; spec['palette_adjust']=level
    if not re.search(r'(?:상품\s*카드|레이아웃|배치).*?(?:유지|그대로)',q) and re.search(r'상품\s*(?:카드|컷).*?(?:강조|중심|크게)|모델\s*컷.*줄여',q):
        changed |= spec.get('layout_variant')!=2; spec['layout_variant']=2
    # 선택된 색상 안에서만 카드 순서를 변경한다.
    if re.search(r'앞(?:쪽|으로)|먼저',q):
        from .campaign_colors import mentioned_color
        color=mentioned_color(q,data['colors'])
        if color:
            ordered=sorted(assets['cards'],key=lambda c:c['color']!=color)
            changed |= [c['color'] for c in ordered]!=[c['color'] for c in assets['cards']]; assets['cards']=ordered
    if re.search(r'(?:모델\s*컷|착장\s*이미지|히어로\s*이미지).*?(?:다시|교체|재생성)',q):
        gen=generate_image or _image_api
        from .campaign_scope_r43 import image_terms
        gender = (data.get('campaign_scope') or {}).get('product_gender', 'legacy')
        apparel, models = image_terms(gender)
        prompt=f"Editorial concept fashion photo, {models} wearing {data['subtype']}; colors {', '.join(data['colors'])}; {data['season']}; style {spec.get('theme')}; target styling age range {data['scope']['age_min']}-{data['scope']['age_max']}. Distinct alternate composition. No brands, no typography, no claims, no prices."
        assets['hero']=gen(design.hero_prompt(prompt,' no typography, no brands, no price, no claims.',product_gender=gender),'1024x1536'); changed=True
    if not changed and any(spec.get(field)==value for field,value in explicit): raise ValueError('요청한 문구가 이미 적용되어 있습니다. 기존 완성본을 유지합니다.')
    if not changed: raise ValueError('지원되는 변경을 찾지 못했거나 테마 조정 한도에 도달했습니다. 제목·본문·CTA 지정 문구, 전체 톤, 상품 카드 강조·순서, 모델컷 재생성을 요청해 주세요.')
    return data,assets


def _render(data,assets,path):
    from PIL import Image
    from agent_system.approval_demo.delivery_r42 import validate_png
    design.render(data,assets,path)
    # 메일 첨부 3MB 제한: 동일 레이아웃을 유지하며 해상도만 줄인다.
    with Image.open(path) as im:
        image=im.convert('RGB')
    for w in (1400,1200,1050,900):
        if image.width!=w: image=image.resize((w,round(w*image.height/image.width)),Image.Resampling.LANCZOS)
        image.save(path,format='PNG',optimize=True)
        if path.stat().st_size<=3*1024*1024:
            validate_png(path.read_bytes()); return
    raise ValueError('최종 PNG 크기 제한을 충족하지 못했습니다.')


def _reply(text,**extra): return {'turn_type':'chitchat','final_answer':text,'completed_steps':[],**extra}


def handle(kind,question,root,event,run,campaign,*,generate_image=None):
    from agent_system.approval_demo.recipient_flow import campaign_delivery_busy
    if kind=='ambiguous': return _reply('기획전 페이지 완성본과 보고서 완성본 중 어느 것을 만들까요?')
    if kind=='approval_help': kind='show'
    try:
        data,fingerprint=source(run,campaign)
        if campaign_delivery_busy(root,event,run['run_id']): raise ValueError('이메일 발송 진행 중에는 최종 이미지를 변경할 수 없습니다.')
        old=read(root,event,run)
        if kind in ('approve','reject'):
            if not old or old['fingerprint']!=fingerprint or old['token']!=str(question).strip().rstrip('.!?').split()[-1]: raise ValueError('현재 승인 대상과 일치하지 않습니다. 최신 완성본을 확인해 주세요.')
            _checked(root,old)
            if old['status']!='review': raise ValueError('이미 처리된 완성본입니다. 최신 검토 요청을 확인해 주세요.')
            if kind=='reject':
                _write(root,event,run,{**old,'status':'rejected'})
                return _reply('완성본을 반려했습니다. 기존 페이지 틀과 보고서는 유지됩니다. 기획전 페이지 완성본 다시 만들어줘라고 요청해 주세요.')
            _write(root,event,run,{**old,'status':'approved'})
            return _reply('기획전 페이지 완성본을 승인했습니다. 사내 메일 요청 시 현재 기획전의 최종 PDF와 함께 사용합니다. AI 생성 콘셉트 이미지입니다.')
        if old and old['fingerprint']==fingerprint and kind not in ('regenerate','edit') and (old['status']!='rejected' or kind=='show'):
            _checked(root,old); record=old
        elif kind=='show': raise ValueError('현재 승인 시안의 완성본이 없습니다. 기획전 페이지 완성해줘라고 요청해 주세요.')
        else:
            token=secrets.token_hex(6)
            folder=Path(root)/'visualization_output'/'page_final_r428'; folder.mkdir(parents=True,exist_ok=True)
            path=folder/('campaign_final_'+token+'.png')
            try:
                if kind=='edit':
                    if not old or old['fingerprint']!=fingerprint: raise ValueError('현재 승인 시안의 완성본을 먼저 생성해 주세요.')
                    _checked(root,old)
                    data['spec']=deepcopy(old.get('spec') or data['spec'])
                    data,assets=revise(data,_load_assets(root,event,run,old),question,generate_image)
                else:
                    if old and old['fingerprint']==fingerprint: data['spec']=deepcopy(old.get('spec') or data['spec'])
                    assets=build_assets(data,generate_image)
                _render(data,assets,path)
                meta=_save_assets(root,event,run,token,assets)
                record={'revision':(old.get('revision',1)+1 if old else 1),'spec':data['spec'],'assets':meta,'fingerprint':fingerprint,'token':token,'path':str(path.resolve()),'sha256':_hash(path.read_bytes()),'status':'review','design_version':2,'created_at':datetime.now(timezone.utc).isoformat()}
                _write(root,event,run,record)
            except Exception:
                path.unlink(missing_ok=True); raise
        msg=f'기획전 페이지 완성본 {record.get("revision",1)}차입니다. 상품 사진은 AI 생성 콘셉트 이미지이며 실제 판매 상품이 아닙니다.'
        if record['status']=='review':
            msg+='\n수정 예: 제목은 유지하고 전체 톤을 따뜻하게 바꿔줘 / 블랙 상품을 먼저 배치해줘 / CTA를 “컬렉션 보기”로 바꿔줘.\n이미지 검토 후 아래 버튼으로 승인하거나 수정 요청을 선택해 주세요.'
        elif record['status']=='approved': msg+='\n이미 승인된 완성본입니다.'
        else: msg+='\n반려된 완성본입니다. 다시 생성해 주세요.'
        if record.get('design_version')!=2: msg+='\n이전 디자인입니다. 개선된 디자인은 기획전 페이지 완성본 다시 만들어줘로 생성할 수 있습니다.'
        return _reply(msg,visualization_path=record['path'],actions=review_actions(run,record))
    except ValueError as exc: return _reply(str(exc))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('R428 final page generation failed (%s)',type(exc).__name__)
        return _reply('기획전 이미지 생성에 실패했습니다. 기존 승인 시안·고객·보고서·발송 이력은 유지됩니다. 이미지 API 권한 및 설정을 확인해 주세요.')


def review_actions(run,record):
    if record.get('status')!='review': return []
    return [{'type':'final_page_review','run_id':run['run_id'],'token':record['token']}]


def handle_button(action,root,event,run,campaign):
    try:
        if action.get('run_id')!=run.get('run_id'): raise ValueError('현재 기획전의 버튼이 아닙니다.')
        record=read(root,event,run)
        _,fingerprint=source(run,campaign)
        if not record or record['status']!='review' or record['fingerprint']!=fingerprint or record['token']!=action.get('token'):
            raise ValueError('이전 버전 또는 이미 처리된 버튼입니다. 최신 완성본을 확인해 주세요.')
        _checked(root,record)
        command=action.get('command')
        if command=='edit':
            return _reply('수정할 내용을 자연어로 입력해 주세요. 예: 제목은 유지하고 전체 톤을 세련되게 바꿔줘. 변경 전 이미지는 보존합니다.',actions=review_actions(run,record))
        if command not in ('approve','reject'): raise ValueError('지원하지 않는 버튼입니다.')
        return handle(command,'기획전 페이지 완성본 승인 '+record['token'],root,event,run,campaign)
    except (ValueError,OSError,KeyError) as exc:
        return _reply(str(exc) if isinstance(exc,ValueError) else '현재 완성본을 확인할 수 없습니다.')


def sync_delivery(root,event,run,campaign,*,sending=False):
    """등록된 보고서와 현재 승인 이미지의 동일성을 확인. 이전 수신자 승인 재사용 차단."""
    from agent_system.approval_demo import recipient_flow as r, delivery_r42 as d
    record=read(root,event,run)
    if not record: return
    state=r._read_state(root,event); png=state.get('png') or {}
    target=state.get('target') or {}
    if target.get('kind')!='r39' or target.get('run_id')!=run.get('run_id') or target.get('owner')!=event.get('user') or target.get('channel')!=event.get('channel'):
        raise ValueError('현재 기획전의 보고서 및 첨부 상태를 확인해 주세요.')
    # 명시적 사용자 PNG 첨부는 기존 방식대로 허용한다. 새 생성 이후 첨부한 것만 대체 가능.
    if png.get('file_id') and not str(png['file_id']).startswith('r428:') and str(png.get('saved_at',''))>record['created_at']:
        return
    _,fingerprint=source(run,campaign)
    if record['fingerprint']!=fingerprint or record['status']!='approved': raise ValueError('현재 페이지 완성본을 다시 생성하고 승인해 주세요. 이메일은 발송하지 않았습니다.')
    content=_checked(root,record)
    if png.get('sha256')==record['sha256'] and png.get('file_id')=='r428:'+record['token']: return
    if sending: raise ValueError('최종 PNG가 변경됐습니다. 수신자를 다시 지정하고 발송 승인해 주세요.')
    from agent_system.agents.campaign_execution_agent import register_png
    register_png(root,event,content,file_id='r428:'+record['token'])
