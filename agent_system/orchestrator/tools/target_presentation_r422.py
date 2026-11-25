"""R40 집계만 사용한 시각화와 정책 선정 근거. 고객 행·LLM·모델 재실행 없음."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import logging

LOG = logging.getLogger(__name__)

# R40_target_diagnostic_results_20261125_011031.zip의 두 quadrants CSV에서
# window=180, shrink_k=20, weights=.3/.7, prediction_top=.03,
# taste_top_positive=.1, combo_policy=max_taste_any_combo인 행만 추출한 정책 선정 자료.
BACKTEST = (
    ('2020-07-01', 535271, 2613, 5822, 174, 10887, 171, 38753, 444, 479526, 1824),
    ('2020-08-01', 538393, 4061, 5520, 330, 11122, 329, 39033, 727, 482337, 2675),
)


def aggregate(target):
    if target.get('policy') != 'r40_v1':
        raise ValueError('R40 집계가 아닙니다.')
    p = target if target.get('status') == 'confirmed' else target.get('proposal') or {}
    population = p['population']
    rows = p['all_groups']
    groups = {g['key']: g['count'] for g in rows}
    if (type(population) is not int or population <= 0 or len(rows) != 5
            or set(groups) != {'core','cross_sell','reactivation','excluded','unknown_taste'}
            or any(type(n) is not int or n < 0 for n in groups.values())
            or sum(groups.values()) != population
            or sum(groups[k] for k in ('core','cross_sell','reactivation')) != p['total']):
        raise ValueError('R40 집계 불일치')
    return p, groups


def evidence_lines(target):
    p, _ = aggregate(target)
    combos = ' / '.join(f"{c['product_type']} × {c['colour']}" for c in target.get('combos', []))
    cutoff = p.get('taste_cutoff')
    cutoff_text = f'{cutoff:.6f}' if cutoff is not None else '양수 취향 없음'
    from .audience_scope_r426 import audience_text, population_text
    lines = [
        '분석 대상: ' + (combos or '확정 기획전 조합'),
        'H&M 과거 구매 데이터 · 기준일 2020-09-23 · 예측 기간 30일 · 취향 집계 180일',
        '구매 예측: 조합별 평균 동점 백분위의 최대값 ≥ 0.97. 여러 조합의 합집합과 동점 때문에 전체 후보는 정확히 3%가 아닐 수 있습니다.',
        '취향: 상품 유형 30% + 색상 70%. 속성별 보정 점수 = 구매 일치 비율 r × 유효 거래 건수 n / (n + 20). 조합별 보정 취향 최대값을 사용합니다.',
        f'취향 기준: 양수 취향 점수의 90분위 이상(동점 포함), 0점 제외. 현재 경계값: {cutoff_text}.',
        '핵심=구매·취향 높음 / 교차판매=구매 높음·취향 낮음 / 재활성화=구매 낮음·취향 높음 / 제외=두 기준 미충족 / 미확인=취향 결측.',
        '정책 선정 근거: Jacket × Black·Greenish Khaki의 과거 두 기준일에서 각 4,000개 정책을 비교했습니다. 구매율과 고객군 규모를 함께 고려한 기본 정책입니다.',
    ]
    if p.get('audience'):
        lines[1:1] = [audience_text(p), population_text(p), '순위·취향 경계는 연령 제한 모집단 내에서 산출합니다.']
    lines.append('다음 백테스트는 연령을 제한하지 않은 모집단의 정책 선정 결과입니다. 현재 연령 제한 모집단의 Lift는 미측정입니다.')
    for day,n,b,cn,cb,xn,xb,rn,rb,en,eb in BACKTEST:
        lines.append(f'{day} 정책 선정용 검증: 전체 {b/n*100:.2f}% / 핵심 {cb/cn*100:.2f}% / 교차판매 {xb/xn*100:.2f}% / 재활성화 {rb/rn*100:.2f}% / 제외 {eb/en*100:.2f}% / 핵심 Lift {(cb/cn)/(b/n):.2f}배.')
    lines += ['한계: 위 수치는 현재 실행 성능이나 독립 테스트, 실제 캠페인 전환 개선율이 아닙니다. 다른 상품 조합의 최적성은 미검증이며 재활성화 고객의 휴면 여부는 확인하지 않았습니다. 실제 고객 발송은 하지 않습니다.']
    return lines


def render(target, path):
    from PIL import Image, ImageDraw, ImageFont
    p, groups = aggregate(target)
    def font(size, bold=False):
        paths = ['/System/Library/Fonts/AppleSDGothicNeo.ttc', '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
                 '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if bold else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                 '/usr/share/fonts/truetype/unfonts-core/UnDotumBold.ttf' if bold else '/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf']
        for candidate in paths:
            try: return ImageFont.truetype(candidate, size=size)
            except OSError: continue
        raise OSError('한글 글꼴이 없습니다.')
    img=Image.new('RGB',(1200,1000),'#FFFFFF'); d=ImageDraw.Draw(img)
    ink='#172B36'; muted='#526775'
    def center(x,y,text,size,color=ink,bold=False):
        d.text((x,y),text,font=font(size,bold),fill=color,anchor='mm')
    center(600,48,'고객 타깃 선정 결과',46,bold=True)
    center(600,106,f"전체 {p['population']:,}명   /   마케팅 후보 {p['total']:,}명",34,bold=True)
    from .audience_scope_r426 import label
    age_label = label(p['audience'])+' · ' if p.get('audience') else ''
    center(600,155,age_label+'R40 · 구매 예측 상위 3% × 양수 취향 상위 10%',25,muted)
    tiles=[('cross_sell','교차판매','새로운 스타일 제안','#DBEAFE','#1E40AF',150,235),
           ('core','핵심 타깃','구매 결정 촉진','#DCFCE7','#166534',665,235),
           ('excluded','제외','마케팅 대상 제외','#F1F5F9','#475569',150,485),
           ('reactivation','재활성화','관심 및 구매 동기 강화','#FEF3C7','#92400E',665,485)]
    for key,label,strategy,bg,fg,x,y in tiles:
        d.rounded_rectangle((x,y,x+495,y+230),radius=18,fill=bg)
        center(x+247,y+40,label,34,fg,True)
        center(x+247,y+103,f'{groups[key]:,}명',57,fg,True)
        center(x+247,y+153,f"{groups[key]/p['population']*100:.2f}%",29,fg)
        center(x+247,y+198,strategy,25,fg)
    center(85,350,'높음',26); center(85,600,'낮음',26)
    center(395,770,'낮음',26); center(912,770,'높음',26)
    center(655,810,'고객 취향 점수 →',30,bold=True)
    center(360,204,'구매 예측 백분위 ↑',30,bold=True)
    d.rounded_rectangle((150,850,1160,935),radius=16,fill='#F3E8FF')
    center(655,879,f"취향 미확인  {groups['unknown_taste']:,}명 ({groups['unknown_taste']/p['population']*100:.2f}%)",30,'#6B21A8',True)
    center(655,912,'취향 판단 불가 · 마케팅 보류',24,'#6B21A8')
    center(600,973,'비율은 전체 점수화 고객 기준 · 실제 발송 가능 인원 아님',22,muted)
    img.save(path,format='PNG')


def chart_path(target, run, root=None):
    """승인 결과와 같은 비공개 폴더에 집계 PNG만 원자적으로 저장."""
    from . import campaign_target_r39 as legacy
    root = Path(root or legacy.ROOT).resolve()
    p, _ = aggregate(target)
    folder = Path(target['folder'])
    if folder.resolve() != folder or folder.parent != legacy._folder(run,root) or not folder.is_dir():
        raise ValueError('R40 집계 저장 경로 오류')
    digest = hashlib.sha256(json.dumps(p['all_groups'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    dst = folder / f'r40_quadrants_{digest}.png'
    fd, name = tempfile.mkstemp(suffix='.png',dir=folder)
    os.close(fd)
    try:
        render(target,name)
        os.replace(name,dst)
    finally:
        Path(name).unlink(missing_ok=True)
    return str(dst)


def optional_chart(target,run,root=None):
    try: return chart_path(target,run,root)
    except Exception as exc:
        LOG.warning('R40 집계 PNG 생성 실패 (%s); 텍스트 집계 유지',type(exc).__name__)
        return None
