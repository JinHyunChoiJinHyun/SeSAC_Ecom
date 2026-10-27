"""
Visualization Agent에서 사용하는
실제 차트 생성 Tool 모듈

Visualization Agent:
    어떤 차트를 만들지 판단

Visualization Tools:
    실제 matplotlib 차트 생성
"""

from __future__ import annotations

import platform
import re

from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from matplotlib import (
    font_manager,
)


# =========================================================
# 프로젝트 경로
# =========================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)


# =========================================================
# 시각화 출력 폴더
# =========================================================

OUTPUT_DIR = (
    PROJECT_ROOT
    / "visualization_output"
)


OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# 의미상 식별자 컬럼
# =========================================================

SEMANTIC_ID_COLUMNS = {
    "client_id",
    "sku",
    "category",
    "url",
}


# =========================================================
# 한글 폰트 설정
# =========================================================

def configure_korean_font():
    """
    운영체제에 설치된 한글 폰트를 찾아
    matplotlib 기본 폰트로 설정합니다.
    """

    system = (
        platform.system()
    )


    if system == "Darwin":

        candidates = [
            "AppleGothic",
            "Arial Unicode MS",
        ]


    elif system == "Windows":

        candidates = [
            "Malgun Gothic",
        ]


    else:

        candidates = [
            "NanumGothic",
            "Noto Sans CJK KR",
        ]


    available_fonts = {
        font.name

        for font
        in font_manager.fontManager.ttflist
    }


    for candidate in candidates:

        if candidate in available_fonts:

            plt.rcParams[
                "font.family"
            ] = candidate

            break


    # 음수 기호 깨짐 방지
    plt.rcParams[
        "axes.unicode_minus"
    ] = False


# =========================================================
# 공통 차트 테두리
# =========================================================

def apply_chart_border(
    ax,
):
    """
    차트 영역의 바깥 테두리를 선명하게 표시합니다.
    """

    for spine in ax.spines.values():

        spine.set_visible(
            True
        )

        spine.set_linewidth(
            1.2
        )

        spine.set_edgecolor(
            "black"
        )


# =========================================================
# 파일 이름 생성
# =========================================================

def sanitize_file_name(
    title: str,
    chart_type: str,
) -> str:
    """
    제목을 안전한 이미지 파일명으로 변환합니다.
    """

    safe_title = re.sub(
        r"[^0-9A-Za-z가-힣_-]+",
        "_",
        title.strip(),
    ).strip(
        "_"
    )


    if not safe_title:

        safe_title = (
            chart_type
        )


    timestamp = (
        datetime
        .now()
        .strftime(
            "%Y%m%d_%H%M%S_%f"
        )
    )


    return (
        f"{timestamp}_"
        f"{chart_type}_"
        f"{safe_title}.png"
    )


# =========================================================
# 의미상 ID 컬럼 판별
# =========================================================

def is_semantic_identifier(
    column_name: str,
) -> bool:
    """
    숫자로 저장되어 있더라도
    의미상 식별자인 컬럼인지 판단합니다.
    """

    lower_name = (
        column_name
        .lower()
    )


    return (
        lower_name
        in SEMANTIC_ID_COLUMNS

        or lower_name.endswith(
            "_id"
        )
    )


# =========================================================
# DataFrame 검증
# =========================================================

def validate_dataframe(
    df: pd.DataFrame,
):
    """
    DataFrame 기본 검증.
    """

    if df is None:

        return (
            False,
            "DataFrame이 None입니다."
        )


    if not isinstance(
        df,
        pd.DataFrame,
    ):

        return (
            False,
            "입력 데이터가 pandas DataFrame이 아닙니다."
        )


    if df.empty:

        return (
            False,
            "DataFrame이 비어 있습니다."
        )


    return (
        True,
        ""
    )


# =========================================================
# 컬럼 존재 확인
# =========================================================

def validate_columns(
    df: pd.DataFrame,
    columns: list[str],
):
    """
    요청한 컬럼이 실제 DataFrame에 존재하는지 확인.
    """

    missing_columns = [

        column

        for column
        in columns

        if column
        not in df.columns
    ]


    if missing_columns:

        return (
            False,
            (
                "DataFrame에 없는 컬럼입니다: "
                f"{missing_columns}"
            )
        )


    return (
        True,
        ""
    )


# =========================================================
# 공통 결과 구조
# =========================================================

def make_result(
    success: bool,
    chart_type: str,
    title: str,
    file_path: str | None = None,
    error: str | None = None,
    **kwargs,
):
    """
    모든 Visualization Tool이 동일한 형태로
    결과를 반환하도록 합니다.
    """

    result = {

        "success":
            success,

        "chart_type":
            chart_type,

        "title":
            title,

        "file_path":
            file_path,

        "error":
            error,
    }


    result.update(
        kwargs
    )


    return result


# =========================================================
# 1. 막대그래프
# =========================================================

def create_bar_chart(
    df: pd.DataFrame,
    x_column: str,
    y_column: str,
    title: str,
    x_label: str | None = None,
    y_label: str | None = None,
    top_n: int | None = 20,
    sort_descending: bool = True,
):
    """
    범주별 값 비교 및 Top-N 시각화.

    예:
    - 구매 상위 고객
    - 구매 상위 SKU
    - category별 구매량
    """

    chart_type = (
        "bar"
    )


    valid, error = (
        validate_dataframe(
            df
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    valid, error = (
        validate_columns(
            df,
            [
                x_column,
                y_column,
            ]
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    if not pd.api.types.is_numeric_dtype(
        df[
            y_column
        ]
    ):

        return make_result(
            False,
            chart_type,
            title,
            error=(
                f"막대그래프의 y축 "
                f"'{y_column}'은 숫자형이어야 합니다."
            ),
        )


    plot_df = (
        df[
            [
                x_column,
                y_column,
            ]
        ]
        .dropna()
        .copy()
    )


    plot_df = (
        plot_df
        .sort_values(
            y_column,
            ascending=(
                not sort_descending
            ),
        )
    )


    if (
        top_n is not None
        and top_n > 0
    ):

        plot_df = (
            plot_df
            .head(
                int(
                    top_n
                )
            )
        )


    if plot_df.empty:

        return make_result(
            False,
            chart_type,
            title,
            error=(
                "시각화할 유효한 데이터가 없습니다."
            ),
        )


    # client_id, sku 등의 숫자를
    # 연속형 숫자가 아닌 범주 라벨로 표시
    plot_df[
        x_column
    ] = (
        plot_df[
            x_column
        ]
        .astype(
            str
        )
    )


    configure_korean_font()


    fig, ax = (
        plt.subplots(
            figsize=(
                10,
                6,
            )
        )
    )


    ax.bar(
        plot_df[
            x_column
        ],
        plot_df[
            y_column
        ],
        color="steelblue",
        edgecolor="black",
        linewidth=0.8,
    )


    ax.set_title(
        title
    )


    ax.set_xlabel(
        x_label
        or x_column
    )


    ax.set_ylabel(
        y_label
        or y_column
    )


    if len(
        plot_df
    ) >= 8:

        plt.xticks(
            rotation=45,
            ha="right",
        )


    apply_chart_border(
        ax
    )


    fig.tight_layout()


    file_path = (
        OUTPUT_DIR
        / sanitize_file_name(
            title,
            chart_type,
        )
    )


    fig.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight",
    )


    plt.close(
        fig
    )


    return make_result(

        True,

        chart_type,

        title,

        file_path=str(
            file_path
        ),

        x_column=
            x_column,

        y_column=
            y_column,

        rows_used=
            len(
                plot_df
            ),
    )


# =========================================================
# 2. 선그래프
# =========================================================

def create_line_chart(
    df: pd.DataFrame,
    x_column: str,
    y_column: str,
    title: str,
    x_label: str | None = None,
    y_label: str | None = None,
):
    """
    시간 흐름이나 순서에 따른 추세 시각화.

    예:
    - 일별 구매량
    - 주별 방문량
    - 월별 검색량
    """

    chart_type = (
        "line"
    )


    valid, error = (
        validate_dataframe(
            df
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    valid, error = (
        validate_columns(
            df,
            [
                x_column,
                y_column,
            ]
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    if not pd.api.types.is_numeric_dtype(
        df[
            y_column
        ]
    ):

        return make_result(
            False,
            chart_type,
            title,
            error=(
                f"선그래프의 y축 "
                f"'{y_column}'은 숫자형이어야 합니다."
            ),
        )


    plot_df = (
        df[
            [
                x_column,
                y_column,
            ]
        ]
        .dropna()
        .copy()
    )


    # 문자열 날짜라면 datetime 변환 시도
    if (
        not pd.api.types.is_datetime64_any_dtype(
            plot_df[
                x_column
            ]
        )

        and (

            pd.api.types.is_object_dtype(
                plot_df[
                    x_column
                ]
            )

            or

            pd.api.types.is_string_dtype(
                plot_df[
                    x_column
                ]
            )
        )
    ):

        converted = (
            pd.to_datetime(
                plot_df[
                    x_column
                ],
                errors="coerce",
            )
        )


        # 대부분 날짜로 변환 가능한 경우에만 적용
        if (
            converted
            .notna()
            .mean()
            >= 0.9
        ):

            plot_df[
                x_column
            ] = converted


    plot_df = (
        plot_df
        .sort_values(
            x_column
        )
    )


    if plot_df.empty:

        return make_result(
            False,
            chart_type,
            title,
            error=(
                "시각화할 유효한 데이터가 없습니다."
            ),
        )


    configure_korean_font()


    fig, ax = (
        plt.subplots(
            figsize=(
                10,
                6,
            )
        )
    )


    ax.plot(
        plot_df[
            x_column
        ],
        plot_df[
            y_column
        ],
        color="crimson",
        marker="o",
    )


    ax.set_title(
        title
    )


    ax.set_xlabel(
        x_label
        or x_column
    )


    ax.set_ylabel(
        y_label
        or y_column
    )


    fig.autofmt_xdate()


    apply_chart_border(
        ax
    )


    fig.tight_layout()


    file_path = (
        OUTPUT_DIR
        / sanitize_file_name(
            title,
            chart_type,
        )
    )


    fig.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight",
    )


    plt.close(
        fig
    )


    return make_result(

        True,

        chart_type,

        title,

        file_path=str(
            file_path
        ),

        x_column=
            x_column,

        y_column=
            y_column,

        rows_used=
            len(
                plot_df
            ),
    )


# =========================================================
# 3. 히스토그램
# =========================================================

def create_histogram(
    df: pd.DataFrame,
    column: str,
    title: str,
    x_label: str | None = None,
    y_label: str | None = "빈도",
    bins: int = 30,
    auto_log_scale: bool = True,
):
    """
    숫자형 Feature의 분포를 확인합니다.

    데이터가 심한 long-tail 형태라면
    y축을 자동으로 log scale로 변경할 수 있습니다.
    """

    chart_type = (
        "histogram"
    )


    valid, error = (
        validate_dataframe(
            df
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    valid, error = (
        validate_columns(
            df,
            [
                column
            ]
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    if is_semantic_identifier(
        column
    ):

        return make_result(
            False,
            chart_type,
            title,
            error=(
                f"'{column}'은 숫자로 저장되어 있지만 "
                "의미상 식별자이므로 "
                "히스토그램의 연속형 변수로 "
                "사용하지 않습니다."
            ),
        )


    if not pd.api.types.is_numeric_dtype(
        df[
            column
        ]
    ):

        return make_result(
            False,
            chart_type,
            title,
            error=(
                f"히스토그램 컬럼 "
                f"'{column}'은 숫자형이어야 합니다."
            ),
        )


    values = (
        df[
            column
        ]
        .replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )
        .dropna()
    )


    if values.empty:

        return make_result(
            False,
            chart_type,
            title,
            error=(
                "히스토그램으로 표현할 "
                "값이 없습니다."
            ),
        )


    configure_korean_font()


    fig, ax = (
        plt.subplots(
            figsize=(
                10,
                6,
            )
        )
    )


    ax.hist(
        values,
        bins=int(
            bins
        ),
        color="mediumpurple",
        edgecolor="black",
        linewidth=0.8,
    )


    # -----------------------------------------------------
    # Long-tail 자동 감지
    # -----------------------------------------------------

    log_scale_applied = (
        False
    )


    if (
        auto_log_scale
        and len(
            values
        ) >= 20
    ):

        skewness = (
            values.skew()
        )


        median_value = (
            values.median()
        )


        max_value = (
            values.max()
        )


        # 중앙값이 0이면 비율 계산이 어렵기 때문에
        # skewness를 중심으로 판단
        if median_value > 0:

            max_median_ratio = (
                max_value
                / median_value
            )

        else:

            max_median_ratio = (
                np.inf
            )


        # 심하게 오른쪽으로 치우친 분포인 경우
        if (
            pd.notna(
                skewness
            )
            and skewness > 2
            and max_median_ratio > 20
        ):

            ax.set_yscale(
                "log"
            )


            log_scale_applied = (
                True
            )


    ax.set_title(
        title
    )


    ax.set_xlabel(
        x_label
        or column
    )


    if log_scale_applied:

        ax.set_ylabel(
            (
                y_label
                or "빈도"
            )
            + " (log scale)"
        )

    else:

        ax.set_ylabel(
            y_label
            or "빈도"
        )


    apply_chart_border(
        ax
    )


    fig.tight_layout()


    file_path = (
        OUTPUT_DIR
        / sanitize_file_name(
            title,
            chart_type,
        )
    )


    fig.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight",
    )


    plt.close(
        fig
    )


    return make_result(

        True,

        chart_type,

        title,

        file_path=str(
            file_path
        ),

        column=
            column,

        rows_used=
            len(
                values
            ),

        bins=
            int(
                bins
            ),

        log_scale_applied=
            log_scale_applied,
    )


# =========================================================
# 4. 박스플롯
# =========================================================

def create_boxplot(
    df: pd.DataFrame,
    value_column: str,
    title: str,
    category_column: str | None = None,
    x_label: str | None = None,
    y_label: str | None = None,
    top_n_categories: int = 10,
):
    """
    숫자형 값의 분포와 이상치를 확인합니다.

    category_column을 지정하면
    범주별 분포를 비교합니다.
    """

    chart_type = (
        "boxplot"
    )


    valid, error = (
        validate_dataframe(
            df
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    required_columns = [
        value_column
    ]


    if category_column:

        required_columns.append(
            category_column
        )


    valid, error = (
        validate_columns(
            df,
            required_columns,
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    if is_semantic_identifier(
        value_column
    ):

        return make_result(
            False,
            chart_type,
            title,
            error=(
                f"'{value_column}'은 식별자이므로 "
                "박스플롯의 분포 값으로 "
                "사용하지 않습니다."
            ),
        )


    if not pd.api.types.is_numeric_dtype(
        df[
            value_column
        ]
    ):

        return make_result(
            False,
            chart_type,
            title,
            error=(
                f"박스플롯 값 컬럼 "
                f"'{value_column}'은 숫자형이어야 합니다."
            ),
        )


    configure_korean_font()


    fig, ax = (
        plt.subplots(
            figsize=(
                10,
                6,
            )
        )
    )


    # -----------------------------------------------------
    # 범주별 Boxplot
    # -----------------------------------------------------

    if category_column:

        plot_df = (
            df[
                [
                    category_column,
                    value_column,
                ]
            ]
            .dropna()
            .copy()
        )


        top_categories = (
            plot_df[
                category_column
            ]
            .value_counts()
            .head(
                int(
                    top_n_categories
                )
            )
            .index
        )


        plot_df = (
            plot_df[
                plot_df[
                    category_column
                ]
                .isin(
                    top_categories
                )
            ]
        )


        groups = []

        labels = []


        for (
            category_value,
            group_df,
        ) in plot_df.groupby(
            category_column,
            sort=False,
        ):

            groups.append(
                group_df[
                    value_column
                ]
                .values
            )


            labels.append(
                str(
                    category_value
                )
            )


        if not groups:

            plt.close(
                fig
            )


            return make_result(
                False,
                chart_type,
                title,
                error=(
                    "박스플롯을 만들 수 있는 "
                    "유효한 범주 데이터가 없습니다."
                ),
            )


        ax.boxplot(
            groups,
            tick_labels=
                labels,
            patch_artist=True,
            boxprops={
                "facecolor": "lightgreen",
                "edgecolor": "black",
            },
        )


        ax.set_xlabel(
            x_label
            or category_column
        )


        if len(
            labels
        ) >= 6:

            plt.xticks(
                rotation=45,
                ha="right",
            )


        rows_used = (
            len(
                plot_df
            )
        )


    # -----------------------------------------------------
    # 단일 변수 Boxplot
    # -----------------------------------------------------

    else:

        values = (
            df[
                value_column
            ]
            .replace(
                [
                    np.inf,
                    -np.inf,
                ],
                np.nan,
            )
            .dropna()
        )


        if values.empty:

            plt.close(
                fig
            )


            return make_result(
                False,
                chart_type,
                title,
                error=(
                    "박스플롯으로 표현할 "
                    "값이 없습니다."
                ),
            )


        ax.boxplot(
            values,
            patch_artist=True,
            boxprops={
                "facecolor": "lightgreen",
                "edgecolor": "black",
            },
        )


        ax.set_xlabel(
            x_label
            or value_column
        )


        rows_used = (
            len(
                values
            )
        )


    ax.set_title(
        title
    )


    ax.set_ylabel(
        y_label
        or value_column
    )


    apply_chart_border(
        ax
    )


    fig.tight_layout()


    file_path = (
        OUTPUT_DIR
        / sanitize_file_name(
            title,
            chart_type,
        )
    )


    fig.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight",
    )


    plt.close(
        fig
    )


    return make_result(

        True,

        chart_type,

        title,

        file_path=str(
            file_path
        ),

        value_column=
            value_column,

        category_column=
            category_column,

        rows_used=
            rows_used,
    )


# =========================================================
# 5. 산점도
# =========================================================

def create_scatterplot(
    df: pd.DataFrame,
    x_column: str,
    y_column: str,
    title: str,
    x_label: str | None = None,
    y_label: str | None = None,
):
    """
    두 숫자형 Feature의 관계를 확인합니다.
    """

    chart_type = (
        "scatter"
    )


    valid, error = (
        validate_dataframe(
            df
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    valid, error = (
        validate_columns(
            df,
            [
                x_column,
                y_column,
            ]
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    semantic_ids = [

        column

        for column
        in [
            x_column,
            y_column,
        ]

        if is_semantic_identifier(
            column
        )
    ]


    if semantic_ids:

        return make_result(
            False,
            chart_type,
            title,
            error=(
                "산점도 관계 분석에는 "
                "의미상 식별자를 사용하지 않습니다: "
                f"{semantic_ids}"
            ),
        )


    for column in [
        x_column,
        y_column,
    ]:

        if not pd.api.types.is_numeric_dtype(
            df[
                column
            ]
        ):

            return make_result(
                False,
                chart_type,
                title,
                error=(
                    f"산점도 컬럼 "
                    f"'{column}'은 숫자형이어야 합니다."
                ),
            )


    plot_df = (
        df[
            [
                x_column,
                y_column,
            ]
        ]
        .replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )
        .dropna()
    )


    if plot_df.empty:

        return make_result(
            False,
            chart_type,
            title,
            error=(
                "산점도로 표현할 "
                "유효한 값이 없습니다."
            ),
        )


    configure_korean_font()


    fig, ax = (
        plt.subplots(
            figsize=(
                10,
                6,
            )
        )
    )


    ax.scatter(
        plot_df[
            x_column
        ],
        plot_df[
            y_column
        ],
        color="darkorange",
        edgecolors="black",
        linewidths=0.4,
        alpha=0.5,
    )


    ax.set_title(
        title
    )


    ax.set_xlabel(
        x_label
        or x_column
    )


    ax.set_ylabel(
        y_label
        or y_column
    )


    apply_chart_border(
        ax
    )


    fig.tight_layout()


    file_path = (
        OUTPUT_DIR
        / sanitize_file_name(
            title,
            chart_type,
        )
    )


    fig.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight",
    )


    plt.close(
        fig
    )


    return make_result(

        True,

        chart_type,

        title,

        file_path=str(
            file_path
        ),

        x_column=
            x_column,

        y_column=
            y_column,

        rows_used=
            len(
                plot_df
            ),
    )


# =========================================================
# 6. 상관계수 히트맵
# =========================================================

def create_correlation_heatmap(
    df: pd.DataFrame,
    title: str,
    columns: list[str] | None = None,
):
    """
    여러 숫자형 Feature 간
    Pearson 상관계수 행렬을 시각화합니다.

    의미상 식별자는 자동 제외합니다.
    """

    chart_type = (
        "heatmap"
    )


    valid, error = (
        validate_dataframe(
            df
        )
    )


    if not valid:

        return make_result(
            False,
            chart_type,
            title,
            error=error,
        )


    # -----------------------------------------------------
    # 사용할 컬럼 선택
    # -----------------------------------------------------

    if columns:

        valid, error = (
            validate_columns(
                df,
                columns,
            )
        )


        if not valid:

            return make_result(
                False,
                chart_type,
                title,
                error=error,
            )


        candidate_columns = (
            columns
        )


    else:

        candidate_columns = (
            list(
                df
                .select_dtypes(
                    include=[
                        np.number
                    ]
                )
                .columns
            )
        )


    # -----------------------------------------------------
    # 실제 숫자형 Feature만 사용
    # -----------------------------------------------------

    numeric_columns = [

        column

        for column
        in candidate_columns

        if (
            column
            in df.columns

            and

            pd.api.types.is_numeric_dtype(
                df[
                    column
                ]
            )

            and

            not is_semantic_identifier(
                column
            )
        )
    ]


    if len(
        numeric_columns
    ) < 2:

        return make_result(
            False,
            chart_type,
            title,
            error=(
                "상관계수 히트맵에는 "
                "식별자가 아닌 숫자형 Feature가 "
                "최소 2개 필요합니다."
            ),
        )


    corr = (
        df[
            numeric_columns
        ]
        .replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )
        .corr()
    )


    configure_korean_font()


    fig, ax = (
        plt.subplots(
            figsize=(
                9,
                7,
            )
        )
    )


    image = (
        ax.imshow(
            corr.values,
            aspect="auto",
            cmap="YlGnBu",
            vmin=-1,
            vmax=1,
        )
    )


    ax.set_title(
        title
    )


    ax.set_xticks(
        range(
            len(
                numeric_columns
            )
        )
    )


    ax.set_yticks(
        range(
            len(
                numeric_columns
            )
        )
    )


    ax.set_xticklabels(
        numeric_columns,
        rotation=45,
        ha="right",
    )


    ax.set_yticklabels(
        numeric_columns
    )


    # -----------------------------------------------------
    # 상관계수 숫자 표시
    # -----------------------------------------------------

    for row_index in range(
        len(
            numeric_columns
        )
    ):

        for column_index in range(
            len(
                numeric_columns
            )
        ):

            value = (
                corr.iloc[
                    row_index,
                    column_index
                ]
            )


            if pd.notna(
                value
            ):

                ax.text(

                    column_index,

                    row_index,

                    f"{value:.2f}",

                    ha="center",

                    va="center",
                )


    fig.colorbar(
        image,
        ax=ax,
    )


    apply_chart_border(
        ax
    )


    fig.tight_layout()


    file_path = (
        OUTPUT_DIR
        / sanitize_file_name(
            title,
            chart_type,
        )
    )


    fig.savefig(
        file_path,
        dpi=150,
        bbox_inches="tight",
    )


    plt.close(
        fig
    )


    return make_result(

        True,

        chart_type,

        title,

        file_path=str(
            file_path
        ),

        columns=
            numeric_columns,

        rows_used=
            len(
                df
            ),
    )