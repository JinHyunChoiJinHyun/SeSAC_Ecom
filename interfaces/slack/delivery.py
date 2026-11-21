"""Slack 출력 창구 — Agent 결과를 Slack 메시지·PNG·CSV로 전달합니다 (이전 agent_system/delivery/delivery_agent.py).

에이전트가 아니라 출력 계층입니다. LLM 판단 없이, 이미 만들어진 결과(final_answer·그래프·표)를
Slack 형식에 맞춰 보냅니다. FastAPI 쪽 출력은 backend/services/chat_service.py가 맡습니다.

- 결과 CSV 만들기: 공통 Tool agent_system/common_tools/execution_tools/output_tools.py
- 메시지 정리: interfaces/slack/message_policy.py · 표 서식: interfaces/slack/table_formatter.py
"""

import logging
import re

from pathlib import Path


from agent_system.common_tools.execution_tools.output_tools import prepare_result_csv
from interfaces.slack.table_formatter import split_text_and_tables


# =========================================================
# 기본 설정
# =========================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)

LOGGER = logging.getLogger(
    "ecom.delivery"
)


# =========================================================
# Slack 전달 정책
# =========================================================

# Slack 본문에서 DataFrame 미리보기로 보여줄 최대 행 수
SLACK_PREVIEW_ROWS = 10

# 결과 CSV 생성 기준(MAX_AUTO_CSV_ROWS)은 output_tools.py에 있습니다.


# =========================================================
# 프로젝트 내부 파일 확인
# =========================================================

def resolve_project_file(
    raw_path,
    base_dir,
):
    """
    전달받은 파일 경로가
    현재 프로젝트 내부의 실제 파일인지 확인합니다.

    프로젝트 외부의 임의 파일이
    Slack에 업로드되는 것을 방지합니다.
    """

    if not raw_path:
        return None


    root = (
        Path(base_dir)
        .resolve()
    )

    path = Path(
        raw_path
    )


    if not path.is_absolute():
        path = (
            root
            / path
        )


    path = (
        path.resolve()
    )


    if not path.is_relative_to(
        root
    ):
        raise ValueError(
            "프로젝트 외부 파일은 "
            "Slack으로 전달할 수 없습니다."
        )


    if not path.is_file():
        raise FileNotFoundError(
            f"파일을 찾지 못했습니다: {path}"
        )


    return path


# =========================================================
# Visualization 파일 확인
# =========================================================

def get_visualization_file(
    result: dict,
    base_dir,
):
    """
    이번 실행에서 생성된 Visualization PNG 파일을 찾습니다.

    우선순위
    --------
    1. result['visualization_path']
    2. result['visualization_result']['file_path']

    visualization_result['success'] 값만 믿지 않고,
    이번 실행 State가 가리키는 실제 PNG 파일이
    존재하는지를 최종 기준으로 사용합니다.

    visualization_output 폴더를 검색하지 않으므로
    이전 실행에서 생성된 다른 PNG가 첨부되지 않습니다.
    """

    visualization_result = (
        result.get(
            "visualization_result"
        )
        or {}
    )


    raw_path = result.get(
        "visualization_path"
    )


    if (
        not raw_path
        and isinstance(
            visualization_result,
            dict,
        )
    ):
        raw_path = (
            visualization_result.get(
                "file_path"
            )
        )


    if not raw_path:
        print(
            "[Delivery Visualization] "
            "이번 실행의 visualization_path 없음"
        )
        return None


    root = (
        Path(base_dir)
        .resolve()
    )

    path = Path(
        raw_path
    )


    if not path.is_absolute():
        path = (
            root
            / path
        )


    path = (
        path.resolve()
    )


    visualization_dir = (
        root
        / "visualization_output"
    ).resolve()


    print(
        "[Delivery Visualization 경로 확인]"
    )
    print(
        f"raw_path = {raw_path}"
    )
    print(
        f"resolved_path = {path}"
    )


    if not path.is_relative_to(
        visualization_dir
    ):
        raise ValueError(
            "허용된 Visualization 경로가 아닙니다."
        )


    if (
        path.suffix.lower()
        != ".png"
    ):
        raise ValueError(
            "Visualization 결과는 "
            "PNG 파일만 허용합니다."
        )


    if not path.is_file():
        raise FileNotFoundError(
            "생성된 Visualization 파일을 "
            f"찾지 못했습니다: {path}"
        )


    print(
        "[Delivery Visualization 파일 확인 완료]"
    )
    print(
        path
    )


    return path


# =========================================================
# Slack DataFrame 미리보기 제한
# =========================================================

def limit_dataframe_preview(
    text: str,
    max_rows: int = SLACK_PREVIEW_ROWS,
):
    """
    Data Agent의 최종 응답에 포함된
    최대 100행 DataFrame 미리보기를
    Slack에서는 max_rows행으로 줄입니다.

    main.py의 로컬 출력은 건드리지 않고
    Slack으로 전달되는 메시지만 줄입니다.
    """

    if not text:
        return text


    lines = text.splitlines()


    # Data-only 최종 응답에 들어가는
    # "전체 N행 중 상위 M행을 표시했습니다." 문구를 찾습니다.
    footer_index = None

    for index, line in enumerate(
        lines
    ):
        stripped = line.strip()

        if (
            stripped.startswith(
                "전체 "
            )
            and "행 중 상위 "
            in stripped
            and "행을 표시했습니다."
            in stripped
        ):
            footer_index = index
            break


    # 해당 형식이 아니면 원문 유지
    if footer_index is None:
        return text


    # "조회 결과:" 문구 위치 확인
    result_index = None

    for index, line in enumerate(
        lines[:footer_index]
    ):
        if (
            line.strip()
            .startswith(
                "조회 결과:"
            )
        ):
            result_index = index
            break


    if result_index is None:
        return text


    # 조회 결과 이후 첫 번째 비어 있지 않은 줄을
    # DataFrame 헤더로 판단합니다.
    header_index = None

    for index in range(
        result_index + 1,
        footer_index,
    ):
        if lines[index].strip():
            header_index = index
            break


    if header_index is None:
        return text


    # 헤더 다음부터 footer 전까지의
    # 실제 데이터 행을 수집합니다.
    data_rows = [
        line

        for line
        in lines[
            header_index + 1:
            footer_index
        ]

        if line.strip()
    ]


    if len(
        data_rows
    ) <= max_rows:
        return text


    limited_rows = (
        data_rows[
            :max_rows
        ]
    )


    original_footer = (
        lines[
            footer_index
        ]
    )


    # 기존 "상위 100행" 등의 숫자를
    # 실제 Slack 표시 행 수로 바꿉니다.
    new_footer = re.sub(
        r"상위\s+\d+행",
        f"상위 {max_rows}행",
        original_footer,
    )


    # footer 뒤에는 SQL 검증 정보 등 중요한 내용이
    # 이어질 수 있으므로 그대로 보존합니다.
    suffix_lines = (
        lines[
            footer_index + 1:
        ]
    )


    new_lines = (
        lines[
            :header_index + 1
        ]
        + limited_rows
        + [
            "",
            new_footer,
        ]
        + suffix_lines
    )


    return "\n".join(
        new_lines
    )


# =========================================================
# Slack용 final_answer 정리
# =========================================================

def build_slack_message(
    result: dict,
    csv_skipped_reason: str = "",
    csv_available: bool = False,
):
    """
    final_answer를 Slack 메시지로 변환합니다.

    처리 내용
    --------
    1. SQL 검증 정보는 final_answer에 이미 있으므로 유지
    2. 로컬 파일 경로는 Slack 첨부 안내로 변경
    3. DataFrame 미리보기는 최대 10행으로 제한
    4. 대용량 CSV 자동 첨부를 생략한 경우 안내 추가
    """

    final_answer = (
        result.get(
            "final_answer",
            "",
        )
        or ""
    )


    if not isinstance(
        final_answer,
        str,
    ):
        raise ValueError(
            "final_answer가 문자열이 아닙니다."
        )


    if not final_answer.strip():
        raise ValueError(
            "최종 답변이 없습니다."
        )


    final_answer = (
        limit_dataframe_preview(
            final_answer,
            max_rows=
                SLACK_PREVIEW_ROWS,
        )
    )


    converted_lines = []


    for line in (
        final_answer
        .splitlines()
    ):

        # Mac 로컬 PNG 경로는
        # 다른 Slack 사용자가 접근할 수 없으므로
        # 첨부파일 안내로 변경합니다.
        if (
            line
            .strip()
            .startswith(
                "- 저장 위치:"
            )
        ):
            # 그래프 파일은 이 메시지에 별도로 첨부되므로 로컬 경로는 생략합니다.
            continue

        else:
            converted_lines.append(
                line
            )


    # 내부 테이블명·재추론 여부 등 전달 계층에 불필요한 반복 문구만 정리합니다.
    # 질문/고객 수에 따른 분기는 두지 않으며 분석 기준·한계·SQL 검증은 유지합니다.
    from interfaces.slack.message_policy import simplify_slack_lines

    slack_message = simplify_slack_lines(converted_lines)

    # 업무 진행 번호는 내부 history/step_index에 그대로 두고 Slack에서만 숨깁니다.
    # 일반 분석 질문에서 단계 번호를 설명할 때는 변경하지 않습니다.
    if result.get("workflow_run") or result.get("turn_type") in ("workflow", "approval"):
        from interfaces.slack.message_policy import hide_workflow_stage_labels
        slack_message = hide_workflow_stage_labels(slack_message)

    # CSV 파일이 실제로 생성된 경우에만 상세 데이터 안내를 추가합니다.
    if csv_available and "첨부 CSV" not in slack_message and "첨부된 CSV" not in slack_message:
        slack_message += "\n\n상세 데이터: 첨부 CSV에서 확인할 수 있습니다."


    if csv_skipped_reason:

        slack_message += (
            "\n\n[CSV 자동 첨부]\n"
            f"- {csv_skipped_reason}"
        )


    return slack_message


# =========================================================
# 긴 Slack 메시지 분리
# =========================================================

def split_slack_message(
    text: str,
    max_length: int = 3000,
):
    """
    Slack 메시지가 너무 길 경우
    여러 개의 메시지로 나눕니다.

    기존 팀원의 3000자 기준을 유지합니다.
    """

    if len(
        text
    ) <= max_length:
        return [
            text
        ]


    chunks = []

    current_lines = []

    current_length = 0


    for line in text.splitlines(
        keepends=True
    ):

        line_length = len(
            line
        )


        # 한 줄 자체가 너무 긴 경우
        if (
            line_length
            > max_length
        ):

            if current_lines:
                chunks.append(
                    "".join(
                        current_lines
                    )
                )

                current_lines = []

                current_length = 0


            for start in range(
                0,
                line_length,
                max_length,
            ):
                chunks.append(
                    line[
                        start:
                        start + max_length
                    ]
                )


            continue


        # 현재 Chunk에 추가하면 제한 초과
        if (
            current_length
            + line_length
            > max_length
        ):

            chunks.append(
                "".join(
                    current_lines
                )
            )

            current_lines = [
                line
            ]

            current_length = (
                line_length
            )

        else:

            current_lines.append(
                line
            )

            current_length += (
                line_length
            )


    if current_lines:
        chunks.append(
            "".join(
                current_lines
            )
        )


    return chunks


# =========================================================
# Slack 메시지 전송
# =========================================================



# CART_SIGNAL_STRUCTURED_TABLE_V1
def _structured_table_payload(result):
    """Agent의 원본 DataFrame을 Slack 표로 표시. CSV 원본은 변경하지 않습니다."""
    from interfaces.slack.table_formatter import _table_payload, MAX_TABLE_ROWS
    import pandas as pd

    frame = result.get("dataframe")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    frame = frame.head(MAX_TABLE_ROWS).copy()
    # 열 이름은 출력 포맷터의 라벨 매핑에 전달합니다.
    columns = [str(col) for col in frame.columns]
    rows = [["" if pd.isna(value) else str(value) for value in row]
            for row in frame.itertuples(index=False, name=None)]
    if not columns or not rows:
        return None
    return _table_payload(columns, rows)




def post_slack_message(
    client,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
    dataframe_result: dict | None = None,
):
    """실제 DataFrame 표를 본문 표 위치에 배치합니다."""
    import re

    # CART_SIGNAL_TABLE_LAYOUT_V4
    responses = []

    def send_table(payload):
        try:
            responses.append(client.chat_postMessage(
                channel=channel_id,
                text="분석 결과 표",
                blocks=[payload],
                thread_ts=thread_ts,
            ))
        except Exception as exc:
            LOGGER.warning(
                "Slack 표 전송 실패: %s", type(exc).__name__
            )
            plain = "\n".join(
                " | ".join(cell["text"] for cell in row)
                for row in payload["rows"]
            )
            send_plain("```\n" + plain + "\n```")

    def send_plain(value):
        for chunk in split_slack_message(value):
            if chunk.strip():
                responses.append(client.chat_postMessage(
                    channel=channel_id,
                    text=chunk,
                    thread_ts=thread_ts,
                ))

    def send_text(value):
        if not value.strip():
            return
        for kind, payload in split_text_and_tables(value):
            if kind == "table":
                send_table(payload)
            else:
                send_plain(payload)

    table = None

    if dataframe_result is not None:
        try:
            table = _structured_table_payload(dataframe_result)
        except Exception as exc:
            LOGGER.warning(
                "DataFrame 표 생성 실패: %s",
                type(exc).__name__,
            )

    if table is None:
        send_text(text)
        return responses

    is_product = bool(
        dataframe_result.get("product_result")
    )

    if not is_product:
        # 상품 분석 이외의 기존 메시지 처리는 유지
        send_text(text)
        send_table(table)
        return responses

    # 상품 ID로 만들어낸 H&M 이미지 URL만 제거
    image_url = re.compile(
        r"https?://www2\.hm\.com/content/dam/[^\s)]+"
    )
    markdown_image = re.compile(
        r"!\[[^\]]*\]\(" + image_url.pattern + r"\)"
    )

    text = markdown_image.sub("", text)

    lines = text.splitlines()
    cleaned = []

    for line in lines:
        if image_url.search(line):
            continue
        if line.strip() in ("- ![이미지]", "![이미지]"):
            continue
        cleaned.append(line)

    lines = cleaned

    # Markdown 파이프 표 판별
    def is_pipe_row(line):
        value = line.strip()
        return (
            value.startswith("|")
            and value.endswith("|")
            and value.count("|") >= 3
        )

    def is_separator(line):
        if not is_pipe_row(line):
            return False
        cells = line.strip().strip("|").split("|")
        return all(
            re.fullmatch(r"\s*:?-{3,}:?\s*", cell)
            for cell in cells
        )

    # Markdown 표를 실제 Slack 표로 교체
    found = None

    for i in range(len(lines) - 2):
        if not is_pipe_row(lines[i]):
            continue
        if not is_separator(lines[i + 1]):
            continue

        j = i + 2
        while j < len(lines) and is_pipe_row(lines[j]):
            j += 1

        if j > i + 2:
            found = (i, j)
            break

    if found:
        first, last = found

        prefix = "\n".join(lines[:first]).strip()
        suffix = "\n".join(lines[last:]).strip()

        send_text(prefix)
        send_table(table)
        send_text(suffix)

        return responses

    # 상품별 단순 나열도 표로 대체
    field_pattern = re.compile(
        r"^\s*[-*]\s*"
        r"(?:\*\*)?"
        r"(?:상품\s*ID|상품명|색상)"
        r"(?:\*\*)?\s*[:：]"
    )

    first = next(
        (
            i for i, line in enumerate(lines)
            if field_pattern.match(line)
        ),
        None,
    )

    if first is not None:
        j = first

        while j < len(lines):
            line = lines[j]
            if not line.strip() or field_pattern.match(line):
                j += 1
            else:
                break

        prefix = "\n".join(lines[:first]).strip()
        suffix = "\n".join(lines[j:]).strip()

        send_text(prefix)
        send_table(table)
        send_text(suffix)

        return responses

    # 중복 가능한 표가 없으면 출처·CSV 안내 전에 삽입
    separator = re.search(
        r"(?m)^---\s*$|"
        r"^근거 출처[·ㆍ]데이터 상태|"
        r"^상세 데이터:",
        "\n".join(lines),
    )

    cleaned_text = "\n".join(lines)

    if separator:
        prefix = cleaned_text[:separator.start()].strip()
        suffix = cleaned_text[separator.start():].strip()

        send_text(prefix)
        send_table(table)
        send_text(suffix)
    else:
        send_text(cleaned_text)
        send_table(table)

    return responses


# =========================================================
# Slack 파일 업로드
# =========================================================

def upload_slack_file(
    client,
    channel_id: str,
    file_path: Path,
    thread_ts: str | None = None,
):
    """
    CSV / PNG 결과 파일을
    Slack에 업로드합니다.

    Slack App에는 files:write 권한이 필요합니다.
    """

    return (
        client.files_upload_v2(
            channel=
                channel_id,

            file=
                str(
                    file_path
                ),

            filename=
                file_path.name,

            title=
                file_path.name,

            thread_ts=
                thread_ts,
        )
    )


# =========================================================
# Delivery Agent
# =========================================================

# CART_SIGNAL_DELIVERY_ORDER_V1

def _file_ids(response):
    """Slack SDK 업로드 응답에서 실제 파일 ID를 추출합니다."""
    files = response.get("files") or []
    if not files and response.get("file"):
        files = [response.get("file")]
    return list(dict.fromkeys(f["id"] for f in files
                             if isinstance(f, dict) and f.get("id")))


def _wait_for_file_shares(client, channel_id, thread_ts, file_ids, timeout=6):
    """업로드 API 반환과 공유 게시를 구분합니다. 제한 시간 내 확인만 수행합니다."""
    import time
    pending = set(file_ids)
    deadline = time.monotonic() + timeout
    while pending:
        for file_id in sorted(pending):
            try:
                info = client.files_info(file=file_id).get("file") or {}
                shares = info.get("shares") or {}
                entries = [entry for group in shares.values()
                           if isinstance(group, dict)
                           for entry in group.get(channel_id, [])
                           if isinstance(entry, dict)]
                # 스레드 정보가 반환되는 경우 반드시 일치해야 합니다.
                if any(entry.get("ts") and
                       (not thread_ts or not entry.get("thread_ts") or
                        entry.get("thread_ts") == thread_ts) for entry in entries):
                    pending.discard(file_id)
            except Exception as exc:
                # files:read 미설정, API 오류 등을 무한 재시도하지 않습니다.
                LOGGER.warning("[Delivery 공유 확인 불가] %s", type(exc).__name__)
                return False
        if not pending:
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    return not pending


def _ordered_workflow_files(paths):
    """단계별 결과 PNG부터 보내고 CSV는 마지막에 배치합니다."""
    def priority(path):
        name = path.name.lower()
        if name.endswith("_evidence.png"):
            return 0
        if name.endswith("_winter_purchases.png"):
            return 1
        if name.endswith("_products.png"):
            return 2
        if name.endswith(".csv"):
            return 4
        return 3
    return sorted(paths, key=priority)


def deliver_to_slack(
    result: dict,
    client,
    channel_id: str,
    thread_ts: str | None = None,
    base_dir=PROJECT_ROOT,
):
    """
    Multi Agent 실행 결과를
    Slack에 최종 전달합니다.

    전달 순서
    --------
    1. final_answer
    2. Visualization PNG
    3. CSV

    중요한 정책
    ------------
    - PNG를 CSV보다 먼저 전송합니다.
    - DataFrame이 MAX_AUTO_CSV_ROWS를 초과하면
      전체 CSV 자동 생성/업로드를 생략합니다.
    - Slack 본문의 DataFrame 미리보기는
      최대 SLACK_PREVIEW_ROWS행만 표시합니다.
    """

    print(
        "[Slack Delivery]"
    )


    # =====================================================
    # 결과 CSV (공통 Tool: output_tools.prepare_result_csv)
    # =====================================================

    # 그래프만 요청한 후속 질문(delivery_mode="chart_only")은 직전과 같은 데이터라 CSV를 다시 보내지 않습니다.
    if result.get("delivery_mode") == "chart_only":
        csv_status = {"result_type": "chart_only", "path": None, "error": "", "skipped_reason": ""}
    else:
        csv_status = prepare_result_csv(result)
    result_type = csv_status["result_type"]
    csv_path = csv_status["path"]
    csv_error = csv_status["error"]
    csv_skipped_reason = csv_status["skipped_reason"]
    attachment_errors = []
    chart_only = result.get("delivery_mode") == "chart_only"
    full_answer = str(result.get("final_answer") or "")

    if csv_path is not None:
        print("[Slack Delivery CSV]")
        print(csv_path)
    elif csv_skipped_reason:
        print(f"[Slack Delivery CSV 생략] {csv_skipped_reason}")
    elif csv_error:
        print(f"[Slack Delivery CSV 생성 오류] {csv_error}")


    # =====================================================
    # 첨부파일 수집
    #
    # PNG를 먼저 넣고 CSV를 마지막에 넣습니다.
    # =====================================================

    file_paths = []


    # -----------------------------------------------------
    # Visualization PNG
    # -----------------------------------------------------

    try:

        visualization_path = (
            get_visualization_file(
                result=
                    result,

                base_dir=
                    base_dir,
            )
        )


        if (
            visualization_path
            is not None
        ):

            file_paths.append(
                visualization_path
            )


            print(
                "[Delivery 첨부 대상 추가]"
            )

            print(
                "Visualization =",
                visualization_path,
            )


    except Exception as exc:

        visualization_error = (
            repr(
                exc
            )
        )


        LOGGER.exception(
            "Visualization 파일 확인 실패 (%s)",
            type(exc).__name__,
        )


        attachment_errors.append(
            {
                "type":
                    "visualization",

                "error":
                    visualization_error,
            }
        )


    # -----------------------------------------------------
    # CSV
    #
    # 가장 마지막에 첨부하여
    # PNG가 대용량 CSV 때문에 기다리지 않게 합니다.
    # -----------------------------------------------------

    if (
        csv_path
        is not None
    ):
        file_paths.append(
            csv_path
        )


    # =====================================================
    # 추가 첨부 (보고서 PDF·Word 등: result["attachments"])
    # 프로젝트 안의 실제 파일만 올립니다 (resolve_project_file)
    # =====================================================

    for raw_attachment in result.get("attachments") or []:
        try:
            file_paths.append(resolve_project_file(raw_attachment, base_dir))
        except Exception as exc:
            print(f"[Slack Delivery 첨부 제외] {raw_attachment}: {exc}")
            attachment_errors.append({"type": "attachment", "error": repr(exc)})


    # =====================================================
    # 중복 첨부파일 제거
    # =====================================================

    unique_file_paths = []

    seen_paths = set()


    for file_path in (
        file_paths
    ):

        path_string = str(
            file_path
        )


        if (
            path_string
            not in seen_paths
        ):

            unique_file_paths.append(
                file_path
            )

            seen_paths.add(
                path_string
            )


    file_paths = (
        unique_file_paths
    )


    if result.get("turn_type") == "workflow":
        file_paths = _ordered_workflow_files(file_paths)

    print(
        "[Delivery 최종 첨부파일]"
    )


    if file_paths:

        for file_path in (
            file_paths
        ):
            print(
                "-",
                file_path,
            )

    else:
        print(
            "첨부파일 없음"
        )


    # =====================================================
    # 최종 Slack 메시지 생성
    # =====================================================

    # 그래프 파일을 확인하지 못했다면 요약 대신 전체 분석 내용을 전달
    if chart_only and any(
        error.get("type") == "visualization"
        for error in attachment_errors
    ):
        result = {
            **result,
            "final_answer": full_answer,
            "delivery_mode": "text_fallback",
        }

    slack_message = (
        build_slack_message(
            result=
                result,

            csv_skipped_reason=
                csv_skipped_reason,
            csv_available=csv_path is not None,
        )
    )



    # CART_SIGNAL_SEARCH_ONLY_CLEAN_V1
    # 상품 검색만 실행한 경우 LLM의 중복 목록 대신 원본 DataFrame 표 사용
    product_info = result.get("product_result")
    frame = result.get("dataframe")

    if (
        isinstance(product_info, dict)
        and hasattr(frame, "empty")
        and not frame.empty
        and result.get("delivery_mode") != "chart_only"
    ):
        tool_records = product_info.get("tools") or []
        used_tools = {
            item.get("name")
            for item in tool_records
            if isinstance(item, dict)
        }

        search_succeeded = any(
            isinstance(item, dict)
            and item.get("name") == "search_products"
            and item.get("available") is True
            for item in tool_records
        )

        if used_tools == {"search_products"} and search_succeeded:
            count = len(frame)

            slack_message = (
                f"H&M 상품 후보 {count}개를 조회했습니다.\n\n"
                "---\n"
                "근거 출처·데이터 상태\n"
                "- H&M 자사 상품 데이터 (실제 조회 결과)\n\n"
                "상세 데이터: 첨부 CSV에서 확인할 수 있습니다."
            )
            print(
                "[Slack Delivery] 상품 검색 중복 본문 제거, "
                "DataFrame 표 사용"
            )

    # =====================================================
    # 최종 답변 전송
    # =====================================================

    try:

        message_responses = (
            post_slack_message(
                client=
                    client,

                channel_id=
                    channel_id,

                text=
                    slack_message,

                dataframe_result=
                    result if not chart_only else None,

                thread_ts=
                    thread_ts,
            )
        )


    except Exception as exc:

        error = (
            repr(
                exc
            )
        )


        LOGGER.exception(
            "Slack 최종 메시지 전송 실패 (%s)",
            type(exc).__name__,
        )


        return {
            "success":
                False,

            "status":
                "failed",

            "csv_path":
                (
                    str(
                        csv_path
                    )
                    if csv_path
                    else None
                ),

            "csv_skipped_reason":
                csv_skipped_reason,

            "file_paths":
                [
                    str(path)
                    for path
                    in file_paths
                ],

            "uploaded_files":
                [],

            "attachment_errors":
                attachment_errors,

            "error":
                error,
        }


    # =====================================================
    # 첨부파일 전송
    #
    # file_paths 순서 자체가
    # PNG → CSV 입니다.
    # =====================================================

    upload_results = []
    shared_file_ids = []
    share_confirmation = None


    for file_path in (
        file_paths
    ):

        try:

            upload_response = upload_slack_file(
                client=
                    client,

                channel_id=
                    channel_id,

                file_path=
                    file_path,

                thread_ts=
                    thread_ts,
            )


            if result.get("actions"):
                shared_file_ids.extend(_file_ids(upload_response))

            upload_results.append(
                {
                    "success":
                        True,

                    "file_path":
                        str(
                            file_path
                        ),

                    "error":
                        "",
                }
            )


            print(
                "[Slack 파일 업로드 완료]"
            )

            print(
                file_path
            )


        except Exception as exc:

            upload_error = (
                repr(
                    exc
                )
            )


            LOGGER.exception(
                "Slack 파일 업로드 실패 (%s)",
                type(exc).__name__,
            )


            print(
                "[Slack 파일 업로드 실패]"
            )

            print(
                file_path
            )

            print(
                upload_error
            )


            upload_results.append(
                {
                    "success":
                        False,

                    "file_path":
                        str(
                            file_path
                        ),

                    "error":
                        upload_error,
                }
            )


    # 버튼이 있는 응답은 파일 공유 게시를 확인한 후 반환합니다.
    if result.get("actions") and upload_results:
        successful_count = sum(bool(item.get("success")) for item in upload_results)
        if successful_count:
            share_confirmation = (
                len(set(shared_file_ids)) >= successful_count
                and _wait_for_file_shares(client, channel_id, thread_ts, shared_file_ids)
            )
            LOGGER.info("[Delivery 파일 공유 확인] confirmed=%s", share_confirmation)

    # 그래프 업로드에 실패했으면 전체 분석 본문을 추가로 전달
    chart_upload_failed = chart_only and any(
        not item.get("success")
        and str(item.get("file_path", "")).lower().endswith(".png")
        for item in upload_results
    )

    if chart_upload_failed and full_answer.strip():
        try:
            post_slack_message(
                client=client,
                channel_id=channel_id,
                text=full_answer,
                thread_ts=thread_ts,
            )
        except Exception as exc:
            LOGGER.exception(
                "그래프 업로드 실패 후 본문 재전송 실패 (%s)",
                type(exc).__name__,
            )
            attachment_errors.append({
                "type": "text_fallback",
                "error": repr(exc),
            })

    # =====================================================
    # 실패 항목 확인
    # =====================================================

    failed_uploads = [
        upload_result

        for upload_result
        in upload_results

        if not upload_result.get(
            "success"
        )
    ]


    has_partial_error = (
        bool(
            failed_uploads
        )
        or bool(
            csv_error
        )
        or bool(
            attachment_errors
        )
    )


    status = (
        "partial_success"
        if has_partial_error
        else "success"
    )


    print(
        "[Delivery 완료]"
    )

    print(
        "status =",
        status,
    )


    # =====================================================
    # 최종 Delivery 결과
    # =====================================================

    return {
        "success":
            not has_partial_error,

        "status":
            status,

        "channel":
            channel_id,

        "thread_ts":
            thread_ts,

        "message_count":
            len(
                message_responses
            ),

        "result_type":
            result_type,

        "csv_path":
            (
                str(
                    csv_path
                )
                if csv_path
                else None
            ),

        "csv_skipped_reason":
            csv_skipped_reason,

        "file_paths":
            [
                str(path)
                for path
                in file_paths
            ],

        "uploaded_files":
            upload_results,

        "share_confirmation": share_confirmation,

        "attachment_errors":
            attachment_errors,

        "csv_error":
            csv_error,

        "error":
            (
                str(
                    failed_uploads
                )
                if failed_uploads
                else ""
            ),
    }
