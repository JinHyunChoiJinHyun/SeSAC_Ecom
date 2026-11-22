"""CART SIGNAL Slack 본문 표시 정책.

분석·검증 결과는 그대로 두고, 사용자용 본문에 중복 출력되는 내부
구현 메타데이터만 보수적으로 축약합니다. 요청 문구별 분기는 없습니다.
"""

import re

# 단독 줄에 있는 기술적 메타데이터만 간소화합니다.
# '조회 소스'라는 단어가 설명 문장 중간에 등장하면 유지합니다.
_TECH_METADATA = re.compile(r"^\s*[-•]?\s*(?:조회\s*소스|원본\s*테이블)\s*:\s*(?:[\w]+\.)?[\w.]+\s*$")
_REINFERENCE_NOTICE = re.compile(
    r"^\s*[-•]?\s*저장된\s+(?:최신\s+)?예측\s+결과를\s+사용했으며\s+이번\s+요청에서\s+모델\s+재추론[은을]?\s+실행하지\s+않았습니다\.?\s*$"
)
_COLUMN_NOTICE = re.compile(r"^\s*(?:표에는\s+)?주요\s+컬럼만\s+표시했습니다\.\s*\(생략된\s+컬럼\s+\d+개\)\s*$")


_MD_BOLD = re.compile(r"\*{2,3}(.+?)\*{2,3}")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def to_slack_mrkdwn(lines):
    """LLM이 쓴 Markdown을 Slack 표기로 바꿉니다: **굵게**·***굵게*** → *굵게*, '## 제목' → *제목*.

    코드 블록(``` … ```) 안은 바꾸지 않습니다 (SQL의 COUNT(*) 등 보존).
    """
    out, in_code = [], False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if not in_code:
            heading = _MD_HEADING.match(line)
            if heading:
                line = f"*{heading.group(1).strip('*').strip()}*"
            # 백틱으로 감싼 인라인 코드는 Markdown 변환에서 제외
            parts = re.split(r"(`+[^`\\n]+`+)", line)
            line = "".join(
                part if part.startswith("`") else
                _MD_BOLD.sub(lambda m: f"*{m.group(1).strip()}*", part)
                for part in parts
            )
        out.append(line)
    return out


def simplify_slack_lines(lines):
    """설명·해석 주의·검증 정보는 삭제하지 않는 표시 정책입니다."""
    cleaned = []
    for line in to_slack_mrkdwn(list(lines)):
        if _TECH_METADATA.fullmatch(line) or _REINFERENCE_NOTICE.fullmatch(line) or _COLUMN_NOTICE.fullmatch(line):
            continue
        cleaned.append(line)
    result = "\n".join(cleaned)
    # 기술 문구를 생략하면서 빈 줄만 과도하게 남지 않도록 정리합니다.
    return re.sub(r"\n{3,}", "\n\n", result).strip()


# 업무 흐름의 단계 번호는 실행 이력에 남기되 Slack 사용자 본문에서는 숨깁니다.
_WORKFLOW_STAGE_LABEL = re.compile(
    r"(?<![\w\d])(?:\d{1,2}\s*[~～–-]\s*)?\d{1,2}\s*단계"
)

def hide_workflow_stage_labels(text: str) -> str:
    """워크플로 응답의 기술적 단계 번호를 제거하고 의미 있는 제목과 근거는 유지합니다."""
    if not isinstance(text, str):
        return text
    transformed = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code or line.strip().startswith("```"):
            transformed.append(line)
            continue

        def replace_stage(match):
            # 실제 업무 단계(1~29)만 숨깁니다. 일반 숫자·상품 ID·통계는 보존합니다.
            numbers = [int(v) for v in re.findall(r"\d+", match.group(0))]
            return "" if numbers and all(1 <= n <= 29 for n in numbers) else match.group(0)

        # 재실행 명령은 실제 입력 문법이므로 단계 번호를 보존한다.
        commands = []
        def protect_command(match):
            commands.append(match.group(0))
            return f"@@CART_COMMAND_{len(commands)-1}@@"
        protected = re.sub(r"워크플로\s+\d{1,2}\s*단계\s+재실행", protect_command, line)
        cleaned = _WORKFLOW_STAGE_LABEL.sub(replace_stage, protected)
        for index, command in enumerate(commands):
            cleaned = cleaned.replace(f"@@CART_COMMAND_{index}@@", command)
        # 문장 중간 단계 번호가 생략되더라도 자연스럽게 읽히도록 조사만 정리합니다.
        cleaned = re.sub(r"(?<=\s)에서(?=\s)", "이후", cleaned) if cleaned != line else cleaned
        cleaned = re.sub(r"^([ \t]*[■*-]?[ \t]*)[—–:-][ \t]*", r"\1", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        if cleaned.strip() in {"", "■", "-"} and line.strip():
            continue
        transformed.append(cleaned)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(transformed)).strip()


_PERIOD_PREFIXES = ("운영 일정:", "H&M 핵심 분석 월:", "H&M 사전 분석 월:", "H&M 분석 기준일:")

def format_workflow_text(text: str) -> str:
    """사용자 화면에서만 제목·문단을 구분하고 동일한 기간 문장을 한 번 표시한다."""
    out, seen = [], set()
    in_code = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            out.append(raw)
            continue
        if in_code:
            out.append(raw)
            continue
        if line.startswith(_PERIOD_PREFIXES):
            if line in seen:
                continue
            seen.add(line)
        # 5·6단계처럼 기존 필터를 통과한 잔여 단계 표기만 제거
        raw = re.sub(r"^(\s*■\s*)\d{1,2}·\s*", r"\1", raw)
        raw = re.sub(r"(/\s*)\d{1,2}·\s*(?=저장 결과 정리)", r"\1", raw)
        line = raw.strip()
        if line.startswith("■ "):
            out.extend(["", "*" + line[2:].strip() + "*", ""])
        elif line.startswith(("[시장·내부 근거 검증]", "[내부 구매 참고]")):
            out.extend(["", "*" + line + "*", ""])
        elif re.match(r"^(?:추천:|선택:|주의:|확정 소구점:|선택 소구점:)", line):
            label, rest = line.split(":", 1)
            out.extend(["", f"*{label}:*{rest}"])
        elif re.match(r"^\d+안\.\s", line):
            out.extend(["", f"*{line}*"])
        elif re.match(r"^\d+\.\s", line):
            out.extend(["", raw])
        else:
            out.append(raw)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()



def use_csv_only_preview(result, message, csv_available):
    """본문에서 설명된 기획전 결과에 한해 긴 중복 표를 생략한다."""
    if not csv_available:
        return False
    if result.get("workflow_display"):
        return "세부 유형 추천" in message
    run = result.get("workflow_run") or {}
    return run.get("workflow") == "trend_planning" and any(title in message for title in (
        "카테고리 비교", "상품 분석", "상품 후보", "상품 속성", "경쟁 유사 상품", "소구점 후보"))



def workflow_section_blocks(text):
    """굵은 단독 제목을 Slack 섹션으로 분리한다. 코드가 있는 본문은 원문 전송으로 유지."""
    if "```" in text:
        return []
    groups, current = [], []
    for line in text.splitlines():
        # 숫자 후보 자체와 짧은 강조 라벨은 독립 구분선으로 분리하지 않는다.
        heading = re.fullmatch(r"\*([^*]+)\*", line.strip())
        if heading and not re.match(r"\d+안\.", heading.group(1)):
            if current and any(part.strip() for part in current):
                groups.append("\n".join(current).strip())
                current = []
        current.append(line)
    if current and any(part.strip() for part in current):
        groups.append("\n".join(current).strip())
    if len(groups) < 2 or len(groups) > 25 or any(len(g) > 3000 for g in groups):
        return []
    blocks = []
    for group in groups:
        if blocks:
            blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": group}})
    return blocks
