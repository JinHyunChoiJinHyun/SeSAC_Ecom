"""최종 기획전 전용 콘텐츠 구성 및 이미지 렌더링. 미리보기 렌더러와 분리."""
from . import campaign_page as page


def designs(subtype):
    # 실제 제품 스펙이 아니라 생성 사진의 디자인 콘셉트만 정의한다.
    if any(k in subtype for k in ('패딩','다운','퍼퍼')):
        return [('숏 실루엣','short silhouette with horizontal quilting','간결한 길이와 가로 퀼팅으로 구성한 데일리 스타일'),
                ('퀼팅 디자인','collarless diamond quilting with snap front','다이아몬드 퀼팅을 활용한 단정한 레이어드 스타일'),
                ('스탠드 칼라','stand collar and clean horizontal quilting','깔끔한 칼라와 단정한 실루엣의 컬러 스타일'),
                ('후드 디자인','hooded silhouette with horizontal quilting','후드 디테일을 중심으로 한 캐주얼 스타일')]
    key = subtype.casefold()
    if any(k in key for k in ('니트','스웨터','knit','sweater','cardigan','가디건')):
        return [('간결한 스타일','clean knitwear silhouette concept','간결한 실루엣을 중심으로 한 니트 스타일 제안'),
                ('레이어드 스타일','layering-friendly knitwear styling concept','레이어드 코디 방향 제안'),
                ('텍스처 스타일','visually distinct knit surface concept','표면의 시각적 차이를 비교하는 디자인 제안'),
                ('데일리 스타일','relaxed knitwear design concept','일상 코디에 어울리는 디자인 제안')]
    if any(k in key for k in ('셔츠','블라우스','blouse','shirt')) and not any(k in key for k in ('티셔츠','t-shirt','tee','tshirt')):
        return [('클린 디자인','clean shirt or blouse silhouette concept','간결한 실루엣 중심의 코디 제안'),
                ('칼라 포인트','distinct neckline or collar styling concept','목선과 칼라 표현의 비교 제안'),
                ('디테일 디자인','subtle placket or garment detail concept','상품 디테일 비교 제안'),
                ('캐주얼 스타일','relaxed shirt or blouse styling concept','일상 코디 방향 제안')]
    if any(k in key for k in ('티셔츠','t-shirt','tshirt','tee','긴소매','반소매')):
        return [('베이직 스타일','clean T-shirt silhouette, preserve selected sleeve length','기본 실루엣의 디자인 제안'),
                ('넥라인 스타일','neckline variation appropriate to selected garment','목선 디자인 비교 제안'),
                ('레이어드 스타일','layering-friendly T-shirt design, preserve sleeve length','레이어드 코디 방향 제안'),
                ('캐주얼 스타일','casual T-shirt styling, preserve selected sleeve length','일상 스타일링 제안')]
    # 기타 상품 유형은 확인되지 않은 소재·구조를 강제하지 않는다.
    return [('미니멀 스타일','minimal clean styling, simple silhouette','간결한 디자인을 중심으로 한 데일리 스타일'),
            ('디테일 스타일','subtle decorative design details appropriate to this garment','디테일에 포인트를 준 스타일링 제안'),
            ('클래식 스타일','classic silhouette appropriate to this garment','단정한 실루엣을 중심으로 한 스타일링 제안'),
            ('캐주얼 스타일','relaxed casual styling appropriate to this garment','일상 코디에 맞춘 캐주얼 스타일링 제안')]


def hero_prompt(context,constraint,variant='', *, product_gender='legacy'):
    from .campaign_scope_r43 import image_terms
    _, models = image_terms(product_gender)
    return (context+' ONE single coherent editorial fashion photograph, not a collage, not a grid, no split screen, no inset or montage. '
            f'One or two {models} ONLY, wearing the selected garment and selected colors, natural pose, cohesive scene, '
            'medium or three-quarter framing, clear garment silhouette, realistic fashion photography. Match the specified season and theme. '
            +variant+constraint)


def card_prompt(context,constraint,subtype,color,design,*,theme='modern',product_gender='legacy'):
    from .campaign_scope_r43 import image_terms
    apparel, _ = image_terms(product_gender)
    backdrop={'warm':'warm ivory','modern':'neutral white','premium':'muted charcoal'}.get(theme,'neutral white')
    return (context+f' Single isolated {subtype} ({apparel}) in exactly {color}, design concept: {design}. '
            'One garment only, no person, no mannequin, no hands, no collage, no grid, front-facing studio product photography, '
            f'entire garment visible with generous margins, same {backdrop} seamless backdrop, soft diffused light, consistent scale and centered composition.'+constraint)


def compare_details(data):
    colors=' · '.join(data['colors']); subtype=data['subtype']
    result=[]
    for point in (data['spec'].get('points') or ['색상별 스타일링','상품별 디자인 상세 비교'])[:4]:
        if '색상' in point or '컬러' in point:
            detail=f'{colors} 컬러를 비교하고, 평소 즐겨 입는 색상과 어울리는 {subtype} 스타일을 찾아보세요.'
        elif any(k in point for k in ('디자인','실루엣','디테일','상품별')):
            detail='상품별 실루엣과 디자인 디테일을 살펴보고, 원하는 코디 분위기에 맞는 스타일을 비교해 보세요.'
        else:
            detail=f'{subtype} 컬렉션에서 {point} 관점으로 디자인과 스타일링을 살펴보세요.'
        result.append((str(point),detail))
    return result


def render(data,assets,path):
    from PIL import Image, ImageDraw
    spec=data['spec']; pal=page._palette(spec.get('theme') or 'modern')
    level=max(0,min(4,int(spec.get('palette_adjust',0))))
    if level and spec.get('theme')=='warm':
        for key in ('bg','panel','card','chip'):
            r,g,b=pal[key]; pal[key]=(min(255,r+level),max(0,g-2*level),max(0,b-5*level))
    W=1400; H=3000; im=Image.new('RGB',(W,H),pal['bg']); d=ImageDraw.Draw(im)
    def font(size,bold=False): return page._safe_font(size,bold)
    def wrap(text,f,w):
        # 긴 단어도 영역 밖으로 나가지 않도록 글자 단위 보완.
        lines=[]
        for line in page._wrap(d,str(text),f,w):
            while d.textlength(line,font=f)>w:
                end=1
                while end<len(line) and d.textlength(line[:end+1],font=f)<=w: end+=1
                lines.append(line[:end]); line=line[end:]
            if line: lines.append(line)
        return lines
    def text(x,y,value,size,w,*,bold=False,color=None,step=None):
        f=font(size,bold); step=step or int(size*1.40)
        for line in wrap(value,f,w):
            d.text((x,y),line,font=f,fill=color or pal['ink']); y+=step
        return y
    def roundbox(box,fill,r=24,outline=None):
        d.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=2)
    left=64; right=W-left; hero_top=64; tx=left+44; tw=680; ix=left+760
    # 승인 카피의 길이에 따라 히어로 높이를 확장한다.
    title=spec['hero_title']; body=spec['hero_body']; note=spec.get('hero_note','')
    title_size=54
    title_lines=wrap(title,font(title_size,True),tw)
    hh=max(590,160+len(title_lines)*72+len(wrap(body,font(30),tw))*42+len(wrap(note,font(25),tw))*36+110)
    roundbox((left,hero_top,right,hero_top+hh),pal['card'],34)
    badge=spec.get('hero_badge') or data['subtype']; bw=min(tw,int(d.textlength(badge,font=font(23)))+44)
    roundbox((tx,hero_top+38,tx+bw,hero_top+84),pal['chip'],23)
    text(tx+20,hero_top+47,badge,23,bw-30,color=pal['accent'])
    y=text(tx,hero_top+115,title,title_size,tw,bold=True,step=72)+18
    y=text(tx,y,body,30,tw,step=42)+20
    text(tx,y,note,25,tw,color=pal['muted'],step=36)
    page._paste_asset(im,assets['hero'],(ix,hero_top+25,right-25,hero_top+hh-25),28,cover=True)
    y=hero_top+hh+46
    y=text(left,y,spec.get('section_titles',{}).get('styling','색상별 스타일링'),34,1200,bold=True)+12
    cx=left
    for color in data['colors']:
        bw=max(140,int(d.textlength(color,font=font(23)))+42)
        roundbox((cx,y,cx+bw,y+50),pal['chip'],25);text(cx+20,y+10,color,23,bw-30);cx+=bw+15
    y+=92
    y=text(left,y,'상품 컬렉션',34,1200,bold=True)+14
    cards=assets['cards']; gap=20; cols=2 if spec.get('layout_variant')==2 else 4
    cw=(right-left-gap*(cols-1))//cols; ih=330 if cols==2 else 255
    for start in range(0,len(cards),cols):
        row=cards[start:start+cols]; heights=[]
        for card in row:
            heights.append(len(wrap(card['name'],font(26,True),cw-36))*36+len(wrap(card.get('description') or '상품의 색상과 디자인을 비교해 보세요.',font(22),cw-36))*31)
        ch=ih+max(heights)+98
        for i,card in enumerate(row):
            x=left+i*(cw+gap);roundbox((x,y,x+cw,y+ch),pal['panel'],24,pal['chip'])
            page._paste_asset(im,card['image'],(x+13,y+13,x+cw-13,y+13+ih),18,cover=False)
            cy=text(x+18,y+ih+27,card['name'],26,cw-36,bold=True,step=36)+4
            cy=text(x+18,cy,card['color'],21,cw-36,color=pal['accent'],step=30)+8
            text(x+18,cy,card.get('description') or '상품의 색상과 디자인을 비교해 보세요.',22,cw-36,color=pal['muted'],step=31)
        y+=ch+gap
    y+=26
    y=text(left,y,spec.get('section_titles',{}).get('compare','디자인 비교 포인트'),34,1200,bold=True)+14
    pairs=compare_details(data); heights=[max(len(wrap(a,font(26,True),310))*37,len(wrap(b,font(25),810))*35)+36 for a,b in pairs]
    roundbox((left,y,right,y+sum(heights)+32),pal['card'],24)
    py=y+16
    for i,((label,detail),rh) in enumerate(zip(pairs,heights),1):
        roundbox((left+25,py+8,left+70,py+53),pal['chip'],14);text(left+40,py+15,str(i),23,30,color=pal['accent'])
        text(left+88,py+9,label,26,310,bold=True,step=37)
        d.line((left+414,py+10,left+414,py+rh-10),fill=pal['accent'],width=2)
        text(left+438,py+9,detail,25,790,step=35)
        py+=rh
    y+=sum(heights)+76
    # 승인 CTA 버튼 원문을 유지하고, 별도 보조 문구만 구성.
    cta=spec['cta']; cta_w=max(300,min(530,int(d.textlength(cta,font=font(29,True)))+66))
    lead=f"{' · '.join(data['colors'])} {data['subtype']} 컬렉션"
    lead_w=right-left-cta_w-125
    lead_h=len(wrap(lead,font(34,True),lead_w))*47
    button_h=max(78,len(wrap(cta,font(29,True),cta_w-42))*40+30)
    bh=max(180,lead_h+88,button_h+52)
    roundbox((left,y,right,y+bh),pal['ink'],28)
    cy=text(left+35,y+30,lead,34,lead_w,bold=True,color=(255,255,255),step=47)
    text(left+35,cy+10,'나에게 어울리는 스타일을 만나보세요.',25,lead_w,color=(245,235,228))
    bx=right-30-cta_w; by=y+(bh-button_h)//2
    roundbox((bx,by,right-30,by+button_h),pal['panel'],26)
    text(bx+21,by+16,cta,29,cta_w-42,bold=True,step=40)
    y+=bh+35
    y=text(left,y,'AI 생성 콘셉트 이미지 · 실제 판매 상품 아님',19,1200,color=pal['muted'])+44
    if y>H: raise ValueError('완성 페이지 높이 제한을 초과했습니다. 문구를 줄여 주세요.')
    im.crop((0,0,W,y)).save(path,format='PNG')
