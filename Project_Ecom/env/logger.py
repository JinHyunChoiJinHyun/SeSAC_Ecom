"""
프로젝트 공통 로그 관리

기능
----
1. 기존 print() 내용을 터미널에 그대로 출력
2. 동시에 logs/app.log 파일에 저장
3. 사용자 질문마다 run_id 생성
4. 날짜별 로그 파일 자동 분리
5. 오래된 로그 자동 삭제
"""

from __future__ import annotations

import logging
import sys
import time
import uuid

from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


# =========================================================
# 프로젝트 경로
# =========================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


# =========================================================
# 로그 경로
# =========================================================

LOG_DIR = (
    PROJECT_ROOT
    / "logs"
)


LOG_FILE = (
    LOG_DIR
    / "app.log"
)


# =========================================================
# run_id 저장 공간
# =========================================================

_run_id_context: ContextVar[str] = (
    ContextVar(
        "run_id",
        default="-",
    )
)


# =========================================================
# 원래 stdout / stderr 보관
# =========================================================

_ORIGINAL_STDOUT = (
    sys.stdout
)

_ORIGINAL_STDERR = (
    sys.stderr
)


# =========================================================
# Logger 설정
# =========================================================

LOGGER_NAME = (
    "agent_system"
)

_configured = (
    False
)


# =========================================================
# run_id Filter
# =========================================================

class RunIdFilter(
    logging.Filter
):

    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:

        record.run_id = (
            _run_id_context.get()
        )

        return True


# =========================================================
# print() → logger 연결
# =========================================================

class StreamToLogger:
    """
    기존 print() 출력을 Logger로 전달합니다.

    Logger는 다시

    - 터미널
    - 로그 파일

    두 곳에 출력합니다.
    """

    def __init__(
        self,
        logger: logging.Logger,
        level: int,
        original_stream,
    ):

        self.logger = (
            logger
        )

        self.level = (
            level
        )

        self.original_stream = (
            original_stream
        )

        self.buffer_text = (
            ""
        )


    def write(
        self,
        message: str,
    ) -> int:

        if not message:

            return 0


        self.buffer_text += (
            message
        )


        # print()는 내부적으로 여러 번 write할 수 있으므로
        # 줄바꿈 단위로 묶어서 로그에 기록
        while "\n" in self.buffer_text:

            line, self.buffer_text = (
                self.buffer_text.split(
                    "\n",
                    1,
                )
            )


            line = (
                line.rstrip()
            )


            if line:

                self.logger.log(
                    self.level,
                    line,
                )


        return len(
            message
        )


    def flush(
        self,
    ):

        line = (
            self.buffer_text
            .rstrip()
        )


        if line:

            self.logger.log(
                self.level,
                line,
            )


        self.buffer_text = (
            ""
        )


        try:

            self.original_stream.flush()

        except Exception:

            pass


    def isatty(
        self,
    ):

        try:

            return (
                self.original_stream
                .isatty()
            )

        except Exception:

            return False


    def fileno(
        self,
    ):

        return (
            self.original_stream
            .fileno()
        )


    @property
    def encoding(
        self,
    ):

        return getattr(
            self.original_stream,
            "encoding",
            "utf-8",
        )


    def __getattr__(
        self,
        name: str,
    ):

        return getattr(
            self.original_stream,
            name,
        )


# =========================================================
# Logger 가져오기
# =========================================================

def get_logger(
) -> logging.Logger:

    return logging.getLogger(
        LOGGER_NAME
    )


# =========================================================
# Logging 초기화
# =========================================================

def configure_logging(
    capture_print: bool = True,
    retention_days: int = 30,
) -> logging.Logger:
    """
    프로젝트 전체 Logging을 초기화합니다.

    capture_print=True이면
    기존 print()도 자동으로 로그 파일에 저장됩니다.

    retention_days=30이면
    최대 30일치 로그를 보관합니다.
    """

    global _configured


    logger = (
        get_logger()
    )


    # 이미 설정했다면
    # Handler 중복 등록 방지
    if _configured:

        return logger


    # =====================================================
    # logs 폴더 생성
    # =====================================================

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


    # =====================================================
    # Logger 기본 설정
    # =====================================================

    logger.setLevel(
        logging.INFO
    )


    # root logger로 중복 전달되는 것 방지
    logger.propagate = (
        False
    )


    # =====================================================
    # 로그 출력 형식
    # =====================================================

    formatter = logging.Formatter(

        fmt=(
            "%(asctime)s | "
            "%(levelname)s | "
            "run_id=%(run_id)s | "
            "%(message)s"
        ),

        datefmt=(
            "%Y-%m-%d %H:%M:%S"
        ),
    )


    run_id_filter = (
        RunIdFilter()
    )


    # =====================================================
    # 터미널 Handler
    # =====================================================

    console_handler = (
        logging.StreamHandler(
            _ORIGINAL_STDOUT
        )
    )


    console_handler.setLevel(
        logging.INFO
    )


    console_handler.setFormatter(
        formatter
    )


    console_handler.addFilter(
        run_id_filter
    )


    # =====================================================
    # 파일 Handler
    # =====================================================

    file_handler = (
        TimedRotatingFileHandler(

            filename=str(
                LOG_FILE
            ),

            # 매일 자정 로그 파일 교체
            when="midnight",

            interval=1,

            # 지정 일수만 보관
            backupCount=max(
                1,
                retention_days,
            ),

            encoding="utf-8",

            # 실제 로그가 기록될 때 파일 생성
            delay=True,
        )
    )


    file_handler.setLevel(
        logging.INFO
    )


    file_handler.setFormatter(
        formatter
    )


    file_handler.addFilter(
        run_id_filter
    )


    # =====================================================
    # Handler 등록
    # =====================================================

    logger.addHandler(
        console_handler
    )


    logger.addHandler(
        file_handler
    )


    # =====================================================
    # 기존 print() 연결
    # =====================================================

    if capture_print:

        sys.stdout = (
            StreamToLogger(

                logger=
                    logger,

                level=
                    logging.INFO,

                original_stream=
                    _ORIGINAL_STDOUT,
            )
        )


        sys.stderr = (
            StreamToLogger(

                logger=
                    logger,

                level=
                    logging.ERROR,

                original_stream=
                    _ORIGINAL_STDERR,
            )
        )


    _configured = (
        True
    )


    logger.info(
        "LOGGING_START | file=%s",
        LOG_FILE,
    )


    return logger


# =========================================================
# run_id 생성
# =========================================================

def create_run_id(
) -> str:

    return (
        uuid.uuid4()
        .hex[:8]
    )


# =========================================================
# run_id 설정
# =========================================================

def set_run_id(
    run_id: str,
):

    return (
        _run_id_context
        .set(
            run_id
        )
    )


# =========================================================
# run_id 초기화
# =========================================================

def reset_run_id(
    token,
):

    _run_id_context.reset(
        token
    )


# =========================================================
# 로그용 문자열 정리
# =========================================================

def normalize_log_text(
    text: str,
    max_length: int = 1000,
) -> str:
    """
    질문 등의 문자열을 한 줄 로그로 정리합니다.
    """

    normalized = (
        str(
            text
        )
        .replace(
            "\r",
            " ",
        )
        .replace(
            "\n",
            " ",
        )
        .strip()
    )


    if (
        len(
            normalized
        )
        > max_length
    ):

        normalized = (
            normalized[
                :max_length
            ]
            + "...(truncated)"
        )


    return normalized


# =========================================================
# 요청 단위 Logging
# =========================================================

@contextmanager
def request_log(
    question: str,
    log_question: bool = True,
):
    """
    사용자 질문 하나를 하나의 run_id로 묶어 관리합니다.

    사용 예:

    with request_log(question):

        result = graph.invoke(...)
    """

    logger = (
        get_logger()
    )


    run_id = (
        create_run_id()
    )


    token = (
        set_run_id(
            run_id
        )
    )


    started_at = (
        time.perf_counter()
    )


    # =====================================================
    # 요청 시작
    # =====================================================

    if log_question:

        logger.info(
            "REQUEST_START | question=%s",
            normalize_log_text(
                question
            ),
        )

    else:

        logger.info(
            "REQUEST_START"
        )


    try:

        yield run_id


    # =====================================================
    # 처리 실패
    # =====================================================

    except Exception:

        elapsed_seconds = (
            time.perf_counter()
            - started_at
        )


        logger.exception(
            "REQUEST_END | "
            "status=error | "
            "elapsed_sec=%.3f",
            elapsed_seconds,
        )


        raise


    # =====================================================
    # 정상 종료
    # =====================================================

    else:

        elapsed_seconds = (
            time.perf_counter()
            - started_at
        )


        logger.info(
            "REQUEST_END | "
            "status=success | "
            "elapsed_sec=%.3f",
            elapsed_seconds,
        )


    finally:

        reset_run_id(
            token
        )