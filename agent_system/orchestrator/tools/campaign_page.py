"""기획전 페이지 틀 미리보기 생성/수정/되돌리기.

실제 판매 상품 이미지가 미확정인 단계에서도 Slack에서 볼 수 있는 페이지 목업 PNG를
재현 가능하게 생성한다. 상품 카드는 자리표시자 형태로 그리며, 확정된 카피·색상·소구점만 반영한다.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import shutil
from typing import Iterable

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - Pillow 미설치 환경 보호
    Image = ImageDraw = ImageFont = None


PREVIEW_DIR = Path("visualization_output/page_previews")
LEGACY_PREVIEW_DIR = Path("audit_output/page_previews")


# ------------------------ 요청 판별
_REPLAY_RE = re.compile(
    r"(?:페이지|기획전\s*페이지|페이지\s*틀).*(?:다시\s*(?:보여\s*줘|보여주세요|보여줘요)|보여\s*줘|재표시|현재.*보여)",
    re.I,
)
_REGENERATE_RE = re.compile(
    r"(?:페이지|기획전\s*페이지|페이지\s*틀).*(?:다시\s*(?:만들어\s*줘|생성해\s*줘)|새로\s*만들어\s*줘|재생성)",
    re.I,
)
_UNDO_RE = re.compile(
    r"(?:이전\s*(?:이미지|시안|페이지)|직전\s*(?:수정|변경)|되돌리기|돌아가(?:기)?)",
    re.I,
)
_EDIT_RE = re.compile(
    r"(?:페이지|기획전\s*페이지|페이지\s*틀|배너|히어로|제목|상품\s*카드|CTA|버튼|상단|하단|전체\s*톤|배경|디자인).*(?:수정|바꿔|변경|복원|다듬|줄여|늘려|강조|키워|넣어|빼줘)",
    re.I,
)


def is_page_preview_replay_request(text: str) -> bool:
    return bool(_REPLAY_RE.search(str(text or "")))


def is_page_preview_regenerate_request(text: str) -> bool:
    return bool(_REGENERATE_RE.search(str(text or "")))


def is_page_preview_undo_request(text: str) -> bool:
    return bool(_UNDO_RE.search(str(text or "")))


def parse_page_preview_edit_request(text: str) -> str:
    text = str(text or "").strip()
    if text and _EDIT_RE.search(text):
        return text
    return ""


# ------------------------ 데이터 생성

def _colors(package: dict, campaign: dict) -> list[str]:
    colors = package.get("colors") or (campaign.get("values") or {}).get("colors") or []
    if isinstance(colors, str):
        colors = [x.strip() for x in re.split(r"[,，]", colors) if x.strip()]
    return [str(x).strip() for x in colors if str(x).strip()]



def _base_spec(choice: dict, package: dict, campaign: dict) -> dict:
    colors = _colors(package, campaign)
    color_text = " · ".join(colors) if colors else "Selected Colors"
    points = list(package.get("points") or [])
    subtype = str(package.get("subtype") or (campaign.get("values") or {}).get("subtype")
                  or (campaign.get("values") or {}).get("category") or "경량패딩")
    keywords = list(package.get("keywords") or [])
    subs = [str(x).strip() for x in (choice.get("subcopies") or []) if str(x).strip()]
    hero_body = subs[0] if subs else str(choice.get("description") or "").strip()
    detail_body = subs[1] if len(subs) > 1 else ("상품별 디자인 차이를 비교해 보세요." if points else "")
    return {
        "theme": "modern",
        "layout_variant": 1,
        "headline": str(choice.get("headline") or "").strip(),
        "hero_badge": f"WINTER EDITION · {subtype}",
        "hero_title": str(choice.get("headline") or "").strip(),
        "hero_body": hero_body,
        "hero_note": detail_body,
        "color_text": color_text,
        "points": points,
        "keywords": keywords,
        "cta": "지금 기획전 보기",
        "section_titles": {
            "styling": "색상별 스타일링",
            "cards": "상품 카드 미리보기",
            "compare": "디자인 비교 포인트",
            "cta": "기획전 CTA",
        },
        "feedback_note": "",
    }



def _shorten(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"



def _apply_feedback(spec: dict, feedback: str) -> dict:
    updated = deepcopy(spec)
    text = str(feedback or "").strip()
    lower = text.lower()
    updated["feedback_note"] = text

    # 제목 유지 조건을 스타일 규칙보다 우선한다.
    keep_title = bool(re.search(r"제목(?:은|을|이|만)?\s*(?:그대로|유지|보존|변경하지|바꾸지|수정하지)", text))
    keep_rest = bool(re.search(r"(?:나머지|그\s*외).*?(?:그대로|유지|보존)", text))
    explicit = re.search(r"""(?:배너|히어로|제목)(?:\s*(?:제목|문구))?(?:\s*[을를])?\s*["“‘']([^"”’']{1,60})["”’']\s*(?:으로|로)""", text)
    # 이미 선택한 원래 제목을 지칭한 유지 요청은 잘못 변경된 시안에서도 복구한다.
    preserved_title = spec.get("hero_title", "")
    if keep_title and spec.get("headline") and spec["headline"] in text:
        preserved_title = spec["headline"]
    if explicit:
        text = text[:explicit.start()] + text[explicit.end():]
        lower = text.lower()
    elif keep_title:
        # 제목 자체에 포함된 스타일 단어를 수정 지시로 오인하지 않는다.
        if preserved_title:
            text = text.replace(preserved_title, "")
        lower = text.lower()
    # 명시적 제목 교체 + 나머지 유지 요청은 다른 요소를 건드리지 않는다.
    if explicit and keep_rest and not keep_title:
        updated["hero_title"] = explicit.group(1)
        return updated
    if re.search(r"따뜻|감성|포근|부드럽", text):
        updated["theme"] = "warm"
        updated["hero_badge"] = "WINTER MOOD"
        updated["hero_body"] = _shorten(updated["hero_body"] or "", 55)
        if not keep_title and re.search(r"제목(?:을|도|만)?\s*.*(?:따뜻|감성|포근|부드럽)", text) and "겨울" not in updated["hero_title"]:
            updated["hero_title"] = "포근한 겨울의 스타일링"
    if re.search(r"세련|프리미엄|고급|미니멀", text):
        updated["theme"] = "premium"
        updated["hero_badge"] = "PREMIUM OUTER CURATION"
        updated["cta"] = "컬렉션 보기"
        updated["hero_body"] = _shorten(updated["hero_body"] or "", 42)
    if re.search(r"어둡|다크|검정\s*배경|블랙\s*배경", text):
        updated["theme"] = "premium"
    if re.search(r"밝게|화이트\s*배경|깔끔|심플", text):
        updated["theme"] = "modern"
    if re.search(r"짧게|간결|한줄|짧은", text):
        if not keep_title:
            updated["hero_title"] = _shorten(updated["hero_title"], 20)
        updated["hero_body"] = _shorten(updated["hero_body"], 42)
        updated["hero_note"] = _shorten(updated["hero_note"], 42)
    if re.search(r"상단|히어로|배너", text):
        updated["section_titles"]["styling"] = "상단 스타일링 포인트"
    if re.search(r"상품\s*카드", text):
        updated["section_titles"]["cards"] = "상품 카드 강조 구간"
    if re.search(r"비교", text):
        updated["section_titles"]["compare"] = "디자인 상세 비교"
    if re.search(r"버튼|cta|콜투액션", lower):
        updated["cta"] = "상품 보러 가기"
    exact_cta = re.search(r"""(?:CTA|버튼)(?:\s*(?:문구|텍스트))?\s*["“‘']([^"”’']{1,36})["”’']\s*(?:로|으로)""", text, re.I)
    if exact_cta:
        updated["cta"] = exact_cta.group(1)
    if re.search(r"크게|강조", text):
        updated["layout_variant"] = 2
    if re.search(r"작게|간단히", text):
        updated["layout_variant"] = 3
    if keep_title:
        updated["hero_title"] = preserved_title
    elif explicit:
        updated["hero_title"] = explicit.group(1)
    if updated == spec or all(updated[k] == spec[k] for k in ("theme", "layout_variant", "hero_title", "hero_badge", "hero_body", "hero_note", "cta", "section_titles")):
        raise ValueError("어떤 페이지 요소를 바꿔야 할지 확인되지 않았습니다. 예: '상단 배너를 프리미엄 톤으로 바꿔줘', '배너 제목 \"겨울의 새로운 스타일\"로 바꿔줘'")
    return updated



def _next_output_path(run_id: str, revision: int) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run_id or "preview"))
    directory = PREVIEW_DIR / safe
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"page_preview_r{revision}.png"



def migrate_preview_paths(preview: dict, run_id: str) -> dict:
    """기존 시안/이력의 경로만 이전하고 원본 PNG와 승인 정보를 보존한다."""
    migrated = deepcopy(preview)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run_id or "preview"))
    old_root = LEGACY_PREVIEW_DIR.resolve()
    new_root = PREVIEW_DIR.resolve()
    entries = [migrated.get("current") or {}, *(migrated.get("history") or [])]
    for entry in entries:
        raw = entry.get("image_path")
        if not raw:
            continue
        revision = entry.get("revision")
        if type(revision) is not int or revision < 1:
            raise ValueError("페이지 시안 버전이 올바르지 않습니다.")
        filename = f"page_preview_r{revision}.png"
        source = Path(raw).resolve()
        legacy = old_root / safe / filename
        destination = new_root / safe / filename
        if source == destination and source.is_relative_to(new_root):
            continue
        if source != legacy or not source.is_relative_to(old_root):
            raise ValueError("페이지 시안 경로가 올바르지 않습니다.")
        if destination.resolve() != destination:
            raise ValueError("페이지 시안 저장 경로가 올바르지 않습니다.")
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif not destination.is_file():
            render_preview_image(entry["spec"], destination)
        entry["image_path"] = str(PREVIEW_DIR / safe / filename)
    return migrated


def _safe_font(size: int, bold: bool = False):
    if ImageFont is None:  # pragma: no cover
        return None
    candidates = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/Library/Fonts/AppleGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()



def _palette(theme: str) -> dict[str, tuple[int, int, int]]:
    if theme == "warm":
        return {
            "bg": (248, 243, 238), "panel": (255, 251, 247), "ink": (62, 49, 44),
            "accent": (170, 127, 95), "muted": (122, 105, 95), "chip": (238, 224, 212),
            "card": (242, 235, 228),
        }
    if theme == "premium":
        return {
            "bg": (245, 245, 245), "panel": (255, 255, 255), "ink": (28, 28, 28),
            "accent": (77, 77, 77), "muted": (100, 100, 100), "chip": (233, 233, 233),
            "card": (243, 243, 243),
        }
    return {
        "bg": (246, 248, 250), "panel": (255, 255, 255), "ink": (34, 39, 46),
        "accent": (72, 101, 129), "muted": (102, 112, 122), "chip": (231, 237, 243),
        "card": (240, 244, 247),
    }



def _wrap(draw, text: str, font, width: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        trial = current + " " + word
        if draw.textbbox((0, 0), trial, font=font)[2] <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines



def _paste_asset(canvas, asset, box, radius, *, cover):
    from PIL import ImageOps
    x,y,x2,y2 = box
    size=(x2-x,y2-y)
    if cover:
        fitted=ImageOps.fit(asset.convert('RGB'),size,method=Image.Resampling.LANCZOS)
    else:
        fitted=Image.new('RGB',size,(245,241,236))
        contained=ImageOps.contain(asset.convert('RGB'),size,method=Image.Resampling.LANCZOS)
        fitted.paste(contained,((size[0]-contained.width)//2,(size[1]-contained.height)//2))
    mask=Image.new('L',size,0)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,size[0]-1,size[1]-1),radius=radius,fill=255)
    canvas.paste(fitted,(x,y),mask)


def render_preview_image(spec: dict, path: Path, *, final_assets=None) -> str:
    """간단한 페이지 목업 PNG를 생성한다."""
    if Image is None:
        raise RuntimeError("Pillow가 설치되어 있지 않아 페이지 이미지를 생성할 수 없습니다. pip install Pillow 실행 후 재시도하세요.")

    pal = _palette(spec.get("theme") or "modern")
    width, height = 1400, 2200
    img = Image.new("RGB", (width, height), pal["bg"])
    draw = ImageDraw.Draw(img)
    title_f = _safe_font(58, bold=True)
    h1_f = _safe_font(44, bold=True)
    h2_f = _safe_font(30, bold=True)
    body_f = _safe_font(24)
    small_f = _safe_font(20)
    tiny_f = _safe_font(17)

    # Outer panel
    draw.rounded_rectangle((60, 40, width - 60, height - 40), radius=30, fill=pal["panel"])
    if not final_assets:
        draw.text((95, 75), "CART SIGNAL — 기획전 페이지 틀 미리보기", font=h2_f, fill=pal["ink"])
        draw.text((95, 120), f"선택 카피 기반 시안 · revision {spec.get('revision', 1)} · layout {spec.get('layout_variant', 1)}", font=small_f, fill=pal["muted"])

    # Hero block
    hero = (95, 180, width - 95, 780)
    draw.rounded_rectangle(hero, radius=34, fill=pal["card"])
    draw.rounded_rectangle((hero[0] + 34, hero[1] + 34, hero[0] + 320, hero[1] + 82), radius=20, fill=pal["chip"])
    draw.text((hero[0] + 52, hero[1] + 45), spec.get("hero_badge") or "WINTER EDITION", font=small_f, fill=pal["accent"])
    title_y = hero[1] + 120
    for line in _wrap(draw, spec.get("hero_title") or "", title_f, 680):
        draw.text((hero[0] + 52, title_y), line, font=title_f, fill=pal["ink"])
        title_y += 68
    body_y = title_y + 12
    for line in _wrap(draw, spec.get("hero_body") or "", body_f, 660):
        draw.text((hero[0] + 52, body_y), line, font=body_f, fill=pal["muted"])
        body_y += 38
    note_y = body_y + 18
    for line in _wrap(draw, spec.get("hero_note") or "", small_f, 660):
        draw.text((hero[0] + 52, note_y), line, font=small_f, fill=pal["muted"])
        note_y += 32

    if final_assets and note_y > hero[3]-20:
        raise ValueError("승인 문구가 배너 영역을 초과합니다. 시안을 수정해 주세요.")

    # hero visual placeholder
    image_box = (hero[0] + 760, hero[1] + 54, hero[2] - 50, hero[3] - 54)
    if final_assets:
        _paste_asset(img, final_assets['hero'], image_box, 28, cover=True)
    else:
        draw.rounded_rectangle(image_box, radius=28, outline=pal["accent"], width=4, fill=pal["panel"])
        draw.line((image_box[0] + 20, image_box[1] + 20, image_box[2] - 20, image_box[3] - 20), fill=pal["accent"], width=3)
        draw.line((image_box[2] - 20, image_box[1] + 20, image_box[0] + 20, image_box[3] - 20), fill=pal["accent"], width=3)
        draw.text((image_box[0] + 42, image_box[1] + 38), "HERO VISUAL\n(실제 상품 이미지 확정 전 자리표시자)", font=small_f, fill=pal["muted"])

    # Color chips
    y = 835
    draw.text((95, y), spec.get("section_titles", {}).get("styling", "색상별 스타일링"), font=h2_f, fill=pal["ink"])
    chip_y = y + 55
    chip_x = 95
    for name in [x.strip() for x in str(spec.get("color_text") or "").split("·") if x.strip()]:
        w = max(150, 30 + draw.textbbox((0, 0), name, font=small_f)[2])
        draw.rounded_rectangle((chip_x, chip_y, chip_x + w, chip_y + 48), radius=24, fill=pal["chip"])
        draw.text((chip_x + 18, chip_y + 12), name, font=small_f, fill=pal["ink"])
        chip_x += w + 16

    # Product cards
    y = 980
    draw.text((95, y), "상품 컬렉션" if final_assets else spec.get("section_titles", {}).get("cards", "상품 카드 미리보기"), font=h2_f, fill=pal["ink"])
    card_y = y + 58
    gap = 24
    card_w = (width - 190 - gap * 3) // 4
    for i in range(4):
        x1 = 95 + i * (card_w + gap)
        x2 = x1 + card_w
        draw.rounded_rectangle((x1, card_y, x2, card_y + 360), radius=24, fill=pal["chip"] if spec.get("layout_variant") == 2 else pal["panel"], outline=pal["accent"] if spec.get("layout_variant") == 2 else pal["chip"], width=4 if spec.get("layout_variant") == 2 else 3)
        if final_assets:
            card = final_assets['cards'][i]
            _paste_asset(img, card['image'], (x1+18,card_y+18,x2-18,card_y+245), 18, cover=False)
            cy = card_y + 258
            for line in _wrap(draw, card['name'], small_f, card_w-48):
                draw.text((x1+24,cy),line,font=small_f,fill=pal['ink']); cy+=27
            if cy > card_y + 325:
                raise ValueError('상품명 길이가 카드 배치 범위를 초과합니다.')
            draw.text((x1+24,cy+5),card['color'],font=tiny_f,fill=pal['muted'])
        else:
            draw.rounded_rectangle((x1 + 18, card_y + 18, x2 - 18, card_y + 210), radius=18, fill=pal["card"])
            draw.text((x1 + 36, card_y + 42), f"PRODUCT {i + 1}", font=small_f, fill=pal["accent"])
            draw.text((x1 + 26, card_y + 240), "상품명 자리", font=small_f, fill=pal["ink"])
            draw.text((x1 + 26, card_y + 277), "디자인 포인트 비교", font=tiny_f, fill=pal["muted"])
            draw.text((x1 + 26, card_y + 304), "색상/디테일/실루엣", font=tiny_f, fill=pal["muted"])

    # Compare block
    y = 1415
    draw.text((95, y), spec.get("section_titles", {}).get("compare", "디자인 비교 포인트"), font=h2_f, fill=pal["ink"])
    block_y = y + 58
    draw.rounded_rectangle((95, block_y, width - 95, block_y + 260), radius=24, fill=pal["card"])
    points = list(spec.get("points") or [])[:4]
    if not points:
        points = ["색상별 스타일링", "상품별 디자인 상세 비교"]
    py = block_y + 34
    for idx, point in enumerate(points, start=1):
        draw.rounded_rectangle((125, py + 2, 163, py + 38), radius=12, fill=pal["chip"])
        draw.text((136, py + 8), str(idx), font=tiny_f, fill=pal["accent"])
        draw.text((185, py + 7), point, font=body_f, fill=pal["ink"])
        py += 52

    # CTA
    y = 1715
    if not final_assets:
        draw.text((95, y), spec.get("section_titles", {}).get("cta", "기획전 CTA"), font=h2_f, fill=pal["ink"])
    cta_y = y + 60
    draw.rounded_rectangle((95, cta_y, width - 95, cta_y + 180), radius=28, fill=pal["ink"])
    if final_assets:
        draw.text((130, cta_y + 66), "기획전 컬렉션", font=h2_f, fill=(255,255,255))
    else:
        draw.text((130, cta_y + 34), "선택한 카피와 상품 비교 포인트를 반영한 최종 진입 영역", font=small_f, fill=(255, 255, 255))
    draw.rounded_rectangle((95 + 900, cta_y + 54, width - 140, cta_y + 126), radius=26, fill=pal["panel"])
    draw.text((95 + 938, cta_y + 77), spec.get("cta") or "기획전 보기", font=small_f, fill=pal["ink"])

    # Footer note
    footer = spec.get("feedback_note") or ""
    if footer and not final_assets:
        draw.text((95, 2045), "마지막 반영 요청: " + _shorten(footer, 70), font=tiny_f, fill=pal["muted"])
    if final_assets:
        draw.text((95, 2085), "AI 생성 콘셉트 이미지 · 실제 판매 상품 아님", font=tiny_f, fill=pal["muted"])
    else:
        draw.text((95, 2085), "※ 실제 판매 상품 이미지/가격/혜택 미확정. 본 시안은 레이아웃 검토용입니다.", font=tiny_f, fill=pal["muted"])

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return str(path)



def _entry(spec: dict, run_id: str, revision: int, choice: dict) -> dict:
    path = _next_output_path(run_id, revision)
    with_revision = {**deepcopy(spec), "revision": revision}
    image_path = render_preview_image(with_revision, path)
    return {
        "revision": revision,
        "spec": with_revision,
        "image_path": image_path,
        "selected_copy_number": choice.get("number"),
        "selected_headline": choice.get("headline"),
    }



def create_initial_preview(run_id: str, choice: dict, package: dict, campaign: dict) -> dict:
    spec = _base_spec(choice, package, campaign)
    current = _entry(spec, run_id, 1, choice)
    return {
        "status": "review",
        "selected_copy_number": choice.get("number"),
        "selected_copy": deepcopy(choice),
        "current": current,
        "history": [deepcopy(current)],
        "revision": 1,
    }



def revise_preview(preview: dict, run_id: str, package: dict, campaign: dict, feedback: str) -> dict:
    preview = deepcopy(preview or {})
    choice = preview.get("selected_copy") or {}
    current = (preview.get("current") or {}).get("spec") or _base_spec(choice, package, campaign)
    spec = _apply_feedback(current, feedback)
    revision = int(preview.get("revision_seq") or preview.get("revision") or 0) + 1
    current_entry = _entry(spec, run_id, revision, choice)
    history = list(preview.get("history") or []) + [deepcopy(current_entry)]
    return {**preview, "status": "review", "current": current_entry, "history": history, "revision": revision, "button_epoch": int(preview.get("button_epoch") or 0) + 1, "revision_seq": revision}



def regenerate_preview(preview: dict, run_id: str, package: dict, campaign: dict) -> dict:
    preview = deepcopy(preview or {})
    choice = preview.get("selected_copy") or {}
    current = deepcopy((preview.get("current") or {}).get("spec") or _base_spec(choice, package, campaign))
    current["layout_variant"] = 1 + (int(current.get("layout_variant") or 1) % 3)
    if current.get("theme") == "modern":
        current["theme"] = "premium"
    elif current.get("theme") == "premium":
        current["theme"] = "warm"
    else:
        current["theme"] = "modern"
    current["feedback_note"] = "페이지 틀 재생성"
    revision = int(preview.get("revision_seq") or preview.get("revision") or 0) + 1
    current_entry = _entry(current, run_id, revision, choice)
    history = list(preview.get("history") or []) + [deepcopy(current_entry)]
    return {**preview, "status": "review", "current": current_entry, "history": history, "revision": revision, "button_epoch": int(preview.get("button_epoch") or 0) + 1, "revision_seq": revision}



def undo_preview(preview: dict) -> dict:
    preview = deepcopy(preview or {})
    history = list(preview.get("history") or [])
    if len(history) <= 1:
        raise ValueError("되돌릴 페이지 수정 이력이 없습니다.")
    history = history[:-1]
    current = deepcopy(history[-1])
    return {**preview, "status": "review", "current": current, "history": history, "revision": current["revision"], "button_epoch": int(preview.get("button_epoch") or 0) + 1}



def preview_message(preview: dict, intro: str = "기획전 페이지 틀 시안을 만들었습니다.") -> str:
    current = (preview or {}).get("current") or {}
    spec = current.get("spec") or {}
    copy_no = (preview or {}).get("selected_copy_number") or current.get("selected_copy_number") or "?"
    sections = [
        "히어로 배너",
        spec.get("section_titles", {}).get("styling", "색상별 스타일링"),
        spec.get("section_titles", {}).get("cards", "상품 카드 미리보기"),
        spec.get("section_titles", {}).get("compare", "디자인 비교 포인트"),
        spec.get("section_titles", {}).get("cta", "기획전 CTA"),
    ]
    rows = [
        intro,
        "",
        "■ 기획전 페이지 틀 미리보기",
        f"선택 카피: {copy_no}안 · {current.get('selected_headline') or spec.get('headline') or ''}",
        f"현재 시안: {current.get('revision', 1)}차 버전",
        f"현재 배너 제목: {spec.get('hero_title') or ''}",
        f"구성: {' / '.join(sections)}",
        "첨부된 이미지로 페이지 목업을 확인해 주세요.",
        "",
        "수정 예시: '상단 배너 문구 더 크게', '상품 카드 구간 강조해줘', '전체 톤을 더 따뜻하게 바꿔줘'",
        "재표시: '페이지 틀 다시 보여줘' / 재생성: '페이지 틀 다시 만들어줘' / 복원: '이전 이미지로 돌아가'",
        "시안을 승인한 뒤 고객 타깃 분석을 요청할 수 있습니다.",
    ]
    if spec.get("feedback_note"):
        rows.insert(6, f"마지막 반영 요청: {spec['feedback_note']}")
    return "\n".join(rows)
