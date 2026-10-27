import logging
import re

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd


# =========================================================
# 기본 설정
# =========================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
)

DELIVERY_OUTPUT_DIR = (
    BASE_DIR
    / "delivery_output"
)

LOGGER = logging.getLogger(
    "ecom.delivery"
)


# =========================================================
# Slack 전달 정책
# =========================================================

# Slack 본문에서 DataFrame 미리보기로 보여줄 최대 행 수
SLACK_PREVIEW_ROWS = 10

# 이 행 수를 초과하는 DataFrame은
# 전체 CSV 자동 생성/업로드를 생략합니다.
#
# 2천만 행 이상의 DataFrame을 CSV로 만들면
# 파일 생성과 Slack 업로드가 오래 걸리기 때문에
# 서비스 응답성을 위해 제한합니다.
MAX_AUTO_CSV_ROWS = 100_000


# =========================================================
# Delivery Output 폴더 생성
# =========================================================

def ensure_delivery_output_dir():
    """
    Slack에 첨부할 CSV 파일을 저장할
    delivery_output 폴더를 생성합니다.
    """

    DELIVERY_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


# =========================================================
# 최종 전달 DataFrame 선택
# =========================================================

def select_delivery_dataframe(
    result: dict,
):
    """
    최종적으로 Slack에 전달할
    DataFrame을 선택합니다.

    우선순위
    --------
    1. Experiment Agent 결과
    2. Model Agent 결과
    3. Prep Agent 처리 결과
    4. Data Agent 조회 결과

    Model / Experiment Agent는
    아직 구현되지 않았으므로
    현재는 대부분 processed_dataframe 또는
    dataframe이 선택됩니다.
    """

    experiment_df = result.get(
        "experiment_result_dataframe"
    )

    if isinstance(
        experiment_df,
        pd.DataFrame,
    ):
        return (
            experiment_df,
            "experiment",
        )


    model_df = result.get(
        "model_result_dataframe"
    )

    if isinstance(
        model_df,
        pd.DataFrame,
    ):
        return (
            model_df,
            "model",
        )


    processed_df = result.get(
        "processed_dataframe"
    )

    if isinstance(
        processed_df,
        pd.DataFrame,
    ):
        return (
            processed_df,
            "processed",
        )


    dataframe = result.get(
        "dataframe"
    )

    if isinstance(
        dataframe,
        pd.DataFrame,
    ):
        return (
            dataframe,
            "data",
        )


    return (
        None,
        None,
    )


# =========================================================
# DataFrame → CSV
# =========================================================

def save_dataframe_to_csv(
    df: pd.DataFrame,
    result_type: str,
):
    """
    최종 DataFrame을 CSV로 저장합니다.

    utf-8-sig를 사용하여
    Excel에서 한글을 열 때 발생할 수 있는
    인코딩 문제를 줄입니다.
    """

    ensure_delivery_output_dir()


    timestamp = (
        datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    unique_id = (
        uuid4()
        .hex[:6]
    )

    file_name = (
        f"{timestamp}_"
        f"{unique_id}_"
        f"{result_type}_result.csv"
    )

    file_path = (
        DELIVERY_OUTPUT_DIR
        / file_name
    )


    df.to_csv(
        file_path,
        index=False,
        encoding="utf-8-sig",
    )


    return file_path


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
# Model 결과 파일
# =========================================================

def get_model_output_file(
    result: dict,
    base_dir,
):
    """
    향후 Model Agent가 파일을 생성했을 때
    Slack 첨부파일로 사용합니다.
    """

    raw_path = result.get(
        "model_output_path"
    )


    if not raw_path:
        return None


    return resolve_project_file(
        raw_path=
            raw_path,

        base_dir=
            base_dir,
    )


# =========================================================
# Experiment 결과 파일
# =========================================================

def get_experiment_output_file(
    result: dict,
    base_dir,
):
    """
    향후 Experiment Agent가 파일을 생성했을 때
    Slack 첨부파일로 사용합니다.
    """

    raw_path = result.get(
        "experiment_output_path"
    )


    if not raw_path:
        return None


    return resolve_project_file(
        raw_path=
            raw_path,

        base_dir=
            base_dir,
    )


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
            converted_lines.append(
                "- 시각화 파일: "
                "Slack 첨부파일을 확인해주세요."
            )

        else:
            converted_lines.append(
                line
            )


    slack_message = (
        "\n".join(
            converted_lines
        )
    )


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

    message_chunks = (
        split_slack_message(
            text
        )
    )


    responses = []


    for chunk in (
        message_chunks
    ):

        response = (
            client.chat_postMessage(
                channel=
                    channel_id,

                text=
                    chunk,

                thread_ts=
                    thread_ts,
            )
        )


        responses.append(
            response
        )


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
    CSV / PNG / 향후 Model·Experiment 파일을
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
    base_dir=BASE_DIR,
):
    """
    Multi Agent 실행 결과를
    Slack에 최종 전달합니다.

    전달 순서
    --------
    1. final_answer
    2. Visualization PNG
    3. Model / Experiment 결과 파일
    4. CSV

    중요한 정책
    ------------
    - PNG를 CSV보다 먼저 전송합니다.
    - DataFrame이 MAX_AUTO_CSV_ROWS를 초과하면
      전체 CSV 자동 생성/업로드를 생략합니다.
    - Slack 본문의 DataFrame 미리보기는
      최대 SLACK_PREVIEW_ROWS행만 표시합니다.
    """

    print(
        "[Delivery Agent]"
    )


    # =====================================================
    # 최종 DataFrame 선택
    # =====================================================

    result_df, result_type = (
        select_delivery_dataframe(
            result
        )
    )


    csv_path = None

    csv_error = ""

    csv_skipped_reason = ""

    attachment_errors = []


    # =====================================================
    # CSV 생성 여부 판단
    # =====================================================

    if isinstance(
        result_df,
        pd.DataFrame,
    ):

        row_count = len(
            result_df
        )


        if (
            row_count
            > MAX_AUTO_CSV_ROWS
        ):

            csv_skipped_reason = (
                f"결과가 {row_count:,}행으로 커서 "
                f"전체 CSV 자동 첨부를 생략했습니다. "
                f"(자동 첨부 기준: {MAX_AUTO_CSV_ROWS:,}행 이하)"
            )


            print(
                "[Delivery CSV 생략]"
            )

            print(
                csv_skipped_reason
            )


        else:

            try:

                csv_path = (
                    save_dataframe_to_csv(
                        df=
                            result_df,

                        result_type=
                            result_type,
                    )
                )


                print(
                    "[Delivery CSV 생성]"
                )

                print(
                    csv_path
                )


            except Exception as exc:

                csv_error = (
                    repr(
                        exc
                    )
                )


                LOGGER.exception(
                    "CSV 생성 실패 (%s)",
                    type(exc).__name__,
                )


                print(
                    "[Delivery CSV 생성 오류]"
                )

                print(
                    csv_error
                )


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
    # Model 결과 파일
    # -----------------------------------------------------

    try:

        model_path = (
            get_model_output_file(
                result=
                    result,

                base_dir=
                    base_dir,
            )
        )


        if (
            model_path
            is not None
        ):

            file_paths.append(
                model_path
            )


    except Exception as exc:

        model_error = (
            repr(
                exc
            )
        )


        LOGGER.exception(
            "Model 결과 파일 확인 실패 (%s)",
            type(exc).__name__,
        )


        attachment_errors.append(
            {
                "type":
                    "model",

                "error":
                    model_error,
            }
        )


    # -----------------------------------------------------
    # Experiment 결과 파일
    # -----------------------------------------------------

    try:

        experiment_path = (
            get_experiment_output_file(
                result=
                    result,

                base_dir=
                    base_dir,
            )
        )


        if (
            experiment_path
            is not None
        ):

            file_paths.append(
                experiment_path
            )


    except Exception as exc:

        experiment_error = (
            repr(
                exc
            )
        )


        LOGGER.exception(
            "Experiment 결과 파일 확인 실패 (%s)",
            type(exc).__name__,
        )


        attachment_errors.append(
            {
                "type":
                    "experiment",

                "error":
                    experiment_error,
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

    slack_message = (
        build_slack_message(
            result=
                result,

            csv_skipped_reason=
                csv_skipped_reason,
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
    # PNG → Model → Experiment → CSV 입니다.
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
