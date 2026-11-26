"""확정 색상 표시와 검색 별칭을 한곳에서 관리한다."""
import re

# 기존 선택 사전의 표준 이름과 동일하게 유지한다.
COLOR_ALIASES = {'블랙':'Black','검정':'Black','라이트베이지':'Light Beige','밝은베이지':'Light Beige','다크베이지':'Dark Beige','화이트':'White','네이비':'Dark Blue','그레이':'Grey','회색':'Grey','라이트블루':'Light Blue','그린카키':'Greenish Khaki','카키':'Greenish Khaki','베이지':'Beige','다크블루':'Dark Blue','옐로위시브라운':'Yellowish Brown','노란빛브라운':'Yellowish Brown'}
DISPLAY = {'Black':'블랙','Greenish Khaki':'그린 카키','Beige':'베이지','White':'화이트','Dark Blue':'다크 블루','Light Beige':'라이트 베이지','Dark Beige':'다크 베이지','Grey':'그레이','Light Blue':'라이트 블루','Yellowish Brown':'옐로위시 브라운'}


def korean_color(color):
    return DISPLAY.get(color, color)


def mentioned_color(text, colors):
    normalized = re.sub(r'\s+','',str(text)).casefold()
    hits = []
    for color in colors:
        aliases = [color, korean_color(color), *[k for k,v in COLOR_ALIASES.items() if v==color]]
        for alias in aliases:
            key = re.sub(r'\s+','',alias).casefold()
            if not key: continue
            for match in re.finditer(re.escape(key),normalized):
                hits.append((match.start(),match.end(),color))
    # 라이트 베이지 안의 베이지처럼 포함된 짧은 별칭만 제외한다.
    winners = {c for a,b,c in hits if not any(x<=a and b<=y and (x<a or b<y) for x,y,_ in hits)}
    return next(iter(winners)) if len(winners)==1 else None
