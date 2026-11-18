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
            line = _MD_BOLD.sub(lambda m: f"*{m.group(1).strip()}*", line)
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
