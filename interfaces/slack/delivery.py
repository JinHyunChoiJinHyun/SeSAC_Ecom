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

def post_slack_message(
    client,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
):
    """
    final_answer를 Slack에 전송합니다.

    긴 메시지는 여러 개로 나눠 전송합니다.
    """

    responses = []

    # pandas의 공백 정렬 출력은 Slack에서 흐트러지므로 표 블록으로 전달합니다.
    # 원문에 표가 없다면 기존 텍스트 전송 동작을 유지합니다.
    for kind, payload in split_text_and_tables(text):
        if kind == "text":
            for chunk in split_slack_message(payload):
                responses.append(client.chat_postMessage(
                    channel=channel_id, text=chunk, thread_ts=thread_ts,
                ))
            continue

        # Slack table 블록은 메시지당 1개만 허용되므로 각각 전송합니다.
        # API에서 표를 지원하지 않는 환경에서는 내용 손실 없이 텍스트로 대체합니다.
        try:
            responses.append(client.chat_postMessage(
                channel=channel_id,
                text="분석 결과 표",
                blocks=[payload],
                thread_ts=thread_ts,
            ))
        except Exception as exc:
            LOGGER.warning("Slack 표 블록 전송 실패, 텍스트로 대체 (%s)", type(exc).__name__)
            plain = "\n".join(" | ".join(c["text"] for c in row) for row in payload["rows"])
            for chunk in split_slack_message("```\n" + plain + "\n```"):
                responses.append(client.chat_postMessage(
                    channel=channel_id, text=chunk, thread_ts=thread_ts,
                ))


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


    for file_path in (
        file_paths
    ):

        try:

            upload_slack_file(
                client=
                    client,

                channel_id=
                    channel_id,

                file_path=
                    file_path,

                thread_ts=
                    thread_ts,
            )


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
