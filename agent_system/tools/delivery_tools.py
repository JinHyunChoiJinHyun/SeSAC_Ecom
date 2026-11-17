"""
전송용 Tool 함수 뼈대
현재는 함수 이름만 정의합니다.
"""


def prepare_delivery_payload(
    final_answer=None,
    dataframe=None,
    processed_dataframe=None,
):
    """전송할 결과 데이터를 준비합니다."""
    pass


def send_to_slack(
    message=None,
    dataframe=None,
    channel=None,
):
    """Slack으로 결과를 전송합니다."""
    pass


def send_email(
    subject=None,
    message=None,
    dataframe=None,
    recipient=None,
):
    """Email로 결과를 전송합니다."""
    pass


def export_dataframe_attachment(
    dataframe=None,
    file_format="csv",
):
    """DataFrame을 첨부파일 형태로 변환합니다."""
    pass


def select_delivery_channel(
    channel=None,
):
    """전송 채널을 선택합니다."""
    pass


def check_delivery_status(
    result=None,
):
    """전송 성공/실패 여부를 확인합니다."""
    pass
