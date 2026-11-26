"""Slack 수정 DOCX → 최종 PDF. 일반/특화 Agent 보고서 공통 후처리.

Slack Bot의 message(file_share 포함) 이벤트에서만 실행합니다.
"""
from __future__ import annotations

import os
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MAX_DOCX_BYTES = 20 * 1024 * 1024
SLACK_FILE_HOSTS = {"files.slack.com", "files-pri.slack.com", "slack-files.com"}


def _report_file(event: dict) -> dict | None:
    """Slack 메시지에 첨부된 DOCX 하나를 선택합니다 (다른 파일은 건드리지 않음)."""
    if event.get("bot_id") or not event.get("user") or not event.get("channel"):
        return None
    for f in event.get("files") or []:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "")
        if name.lower().endswith(".docx") and not name.startswith("~$"):
            return f
    return None


# PDF 변환 본체는 agent_system/common_tools/report_export/pdf_convert.py (11/20 이동)
# 아래 이름들은 예전 코드·테스트와 맞추려고 그대로 다시 내보냅니다.
from agent_system.common_tools.report_export.pdf_convert import (  # noqa: E402,F401
    KOREAN_FONTS, docx_has_korean, has_korean_font, korean_font_candidates, pdf_font_names)


def convert_docx_to_pdf(docx: Path, out_dir: Path) -> Path:
    """수정본 DOCX → 최종 PDF. ⑥ Reporting Agent의 export_report_pdf Tool로 변환합니다 (Tool 호출 로그 포함).

    실패하면 예외를 냅니다 (process_uploaded_docx가 Slack에 안내).
    """
    from agent_system.agents.reporting_agent import export_uploaded_docx
    return export_uploaded_docx(Path(docx), Path(out_dir))


def _download_slack_docx(client, slack_file: dict, dest: Path, *, png=False) -> None:
    file_id = slack_file.get("id")
    if not file_id:
        raise ValueError("Slack 파일 ID가 없습니다.")
    details = client.files_info(file=file_id)
    if not details.get("ok", True):
        raise ValueError("Slack 파일 정보를 조회할 수 없습니다.")
    info = details.get("file") or {}
    if not str(info.get("name") or "").lower().endswith(".png" if png else ".docx"):
        raise ValueError("DOCX 파일만 지원합니다.")
    size = info.get("size")
    from agent_system.approval_demo.delivery_r42 import MAX_PNG
    limit = MAX_PNG if png else MAX_DOCX_BYTES
    if not isinstance(size, int) or not 0 < size <= limit:
        raise ValueError("PNG는 최대 3MB, DOCX는 최대 20MB까지 지원합니다.")
    url = str(info.get("url_private_download") or info.get("url_private") or "")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (host not in SLACK_FILE_HOSTS and not host.endswith(".slack.com")):
        raise ValueError("Slack 파일 다운로드 주소가 올바르지 않습니다.")
    token = getattr(client, "token", None) or os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Slack Bot 토큰이 없어 파일을 다운로드하지 못합니다.")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    # Redirect가 외부 호스트로 이동하면 Authorization이 넘어가지 않도록 차단
    class SlackRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            p = urllib.parse.urlsplit(newurl)
            h = (p.hostname or "").lower()
            if p.scheme != "https" or (h not in SLACK_FILE_HOSTS and not h.endswith(".slack.com")):
                raise ValueError("외부 호스트로의 리디렉션을 거부했습니다.")
            return super().redirect_request(request, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(SlackRedirect)
    with opener.open(req, timeout=35) as response, dest.open("wb") as output:
        total = 0
        while True:
            block = response.read(65536)
            if not block:
                break
            total += len(block)
            if total > limit:
                raise ValueError("파일 크기가 20MB를 초과했습니다.")
            output.write(block)
    if png:
        from agent_system.approval_demo.delivery_r42 import validate_png
        validate_png(dest.read_bytes())
        return
    if not zipfile.is_zipfile(dest):
        raise ValueError("올바른 DOCX 파일이 아닙니다.")
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise ValueError("Word 문서 구조가 없습니다.")
        if len(names) > 5000 or sum(i.file_size for i in z.infolist()) > 150 * 1024 * 1024:
            raise ValueError("비정상적으로 큰 문서이거나 압축 파일입니다.")


def process_uploaded_docx(client, event: dict, file_info: dict) -> None:
    """Slack 워커에서 실행: 임시 파일만 사용하여 사용자 업로드 문서를 변환 후 전달."""
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event.get("ts")
    # CART_SIGNAL_PDF_LOG_V1
    print("[PDF 업로드] Word 파일 처리 시작", flush=True)
    try:
        # 변환은 수십 초 걸릴 수 있어 먼저 진행 중임을 알림 (첫 변환은 글꼴 목록을 만드느라 더 걸림)
        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                text="Word 수정본을 PDF로 변환하고 있습니다. 잠시만 기다려 주세요.")
    except Exception:
        pass
    try:
        with tempfile.TemporaryDirectory(prefix="cart_signal_final_") as work:
            folder = Path(work)
            docx = folder / "마케터_수정본.docx"
            _download_slack_docx(client, file_info, docx)
            print("[PDF 업로드] Word 다운로드 완료", flush=True)
            pdf = convert_docx_to_pdf(docx, folder)
            print("[PDF 업로드] PDF 변환 함수 완료", flush=True)
            upload_result = client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(pdf),
                filename="CART_SIGNAL_최종_보고서.pdf",
                title="CART SIGNAL 최종 보고서",
                initial_comment="업로드한 Word 수정본을 PDF로 변환했습니다. 최종본을 확인해 주세요.",
            )
            # Slack SDK의 SlackResponse와 테스트용 dict 모두 get()을 제공한다.
            if not hasattr(upload_result, 'get') or upload_result.get('ok') is not True:
                raise RuntimeError('Slack 최종 PDF 공유가 확인되지 않았습니다.')
            # 성공적으로 Slack에 게시된 최종 PDF만 스레드의 승인 첨부 대상으로 등록
            from agent_system.agents.campaign_execution_agent import mark_pdf_ready
            mark_pdf_ready(Path(__file__).resolve().parents[2], event, pdf)
            print("[PDF 업로드] Slack PDF 전송 완료", flush=True)
            return True
    except Exception as exc:
        # 사용자에게 경고하고, 토큰·URL 등 내부 정보는 노출하지 않음
        import logging
        logging.getLogger(__name__).exception("Slack 보고서 최종 변환 실패")
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"⚠️ Word 수정본을 PDF로 변환하지 못했습니다. ({type(exc).__name__}) "
                 "파일이 정상적인 DOCX인지, LibreOffice가 설치돼 있는지 확인해 주세요.",
        )


def process_uploaded_png(client, event, file_info, *, defer=False):
    from agent_system.agents.campaign_execution_agent import register_png, prepare_send, post_buttons
    root = Path(__file__).resolve().parents[2]
    kwargs = dict(channel=event['channel'], thread_ts=event.get('thread_ts') or event.get('ts'))
    try:
        with tempfile.TemporaryDirectory(prefix='cart_png_') as work:
            dest = Path(work)/'final.png'
            _download_slack_docx(client,file_info,dest,png=True)
            register_png(root,event,dest.read_bytes(),file_info.get('id'))
        client.chat_postMessage(**kwargs,text='최종 PNG 등록 완료. 원본 그대로 발송합니다.')
        if defer:
            return True
        pending = prepare_send(root,event)
        if pending:
            post_buttons(client,event,pending)
        else:
            from agent_system.approval_demo.recipient_flow import _read_state
            state = _read_state(root,event)
            status = (state.get('pending') or {}).get('status')
            client.chat_postMessage(**kwargs,text='수신자 확인 버튼을 눌러 주세요.' if status=='AWAITING_CONFIRMATION' else '수신자를 지정해 주세요.' if state.get('pdf') else 'Word 수정본을 첨부해 주세요.')
        return True
    except Exception as exc:
        client.chat_postMessage(**kwargs,text='PNG 등록 실패: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))
