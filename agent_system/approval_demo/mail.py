"""팀원 한 명에게만 테스트 승인 메일 전송 및 IMAP 답장 검사.

고객 메일 발송 API는 존재하지 않는다. IMAP From 검사는 데모용이며
독립적인 신원 인증/전자서명과 동등하지 않다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime
import imaplib
import os
import re
import smtplib
import ssl

from .workflow import _validate_address, _utc, check_review, load_review, update_review


def _credentials(prefix):
    user = os.environ.get(f'CART_APPROVAL_{prefix}_USER', '').strip()
    password = os.environ.get(f'CART_APPROVAL_{prefix}_PASSWORD', '')
    if prefix == 'IMAP':
        user = os.getenv('CART_APPROVAL_SMTP_USER', '').strip() or user
        password = os.getenv('CART_APPROVAL_SMTP_PASSWORD', '') or password
    if not user or not password:
        raise RuntimeError(f'CART_APPROVAL_{prefix}_USER/PASSWORD 환경변수가 필요합니다.')
    return user, password


def _message_body(review: dict) -> str:
    if review.get('r39_report_delivery') and review.get('mail_body'):
        return review['mail_body']
    if review.get('r39_report_delivery'):
        groups = '\n'.join(f"- {g['name']}: {g['count']:,}명" for g in review['target_groups'])
        return ("안녕하세요.\n기획전 운영 제안서를 전달드립니다.\n\n"
                f"분석 대상: {review['scope_label']}\n"
                f"선정 고객: {review['candidate_count']:,}명 (중복 제거)\n{groups}\n\n"
                "분류별 메시지 전략과 페이지 시안은 첨부된 보고서에서 확인하실 수 있습니다.\n감사합니다.")
    return (f"CART SIGNAL — 팀장 역할 승인 테스트\n\n"
            f"승인 요청 ID: {review['request_id']}\n"
            f"기준일: {review['source_date']} (과거 데이터 시뮬레이션)\n"
            f"범위: {review['scope_label']}\n"
            f"선정 근거: {review['selection_basis']}\n"
            f"전체 점수 대상: {review['total_scored_customers']:,}명\n"
            f"검토용 미리보기: {review['preview_count']}명\n"
            f"타깃 후보(미리보기): {review['candidate_count']}명\n"
            f"비교용 그룹(미리보기): {review['control_count']}명\n"
            "이 구분은 무작위 실험 대조군이 아니며 실제 발송 명단도 아닙니다.\n\n"
            f"검토할 마케팅 문구(발송하지 않음):\n{review['message_draft']}\n\n"
            "승인하려면 아래 명령 중 하나만 답장 본문의 첫 줄에 적어 주세요.\n"
            f"APPROVE {review['approval_token']}\n"
            f"REJECT {review['approval_token']}\n\n"
            "※ 과거 구매 자료이며 수신 동의·연령·제외 대상은 확인되지 않았습니다.\n"
            "※ 메일은 승인 시연만 수행하며 고객에게 메시지를 보내지 않습니다.\n")


def send_team_approval(path, *, confirm: bool = False):
    if not confirm:
        raise PermissionError('실제 이메일 전송에는 --confirm-send 옵션이 필요합니다.')
    review = load_review(path)
    if review.get('status') != 'DRAFT' or check_review(review)['status'] != 'PASS':
        raise ValueError('DRAFT 상태와 검증 PASS가 필요합니다.')
    approver = _validate_address(review['approver'])
    user, password = _credentials('SMTP')
    message = EmailMessage()
    message['From'] = user
    message['To'] = approver
    message['Subject'] = (review.get("mail_subject") or "[CART SIGNAL] 기획전 운영 제안서") if review.get("r39_report_delivery") else f"[CART APPROVAL DEMO {review['request_id']}] 승인 또는 반려 요청"
    message.set_content(_message_body(review))
    # 보고서 승인 시연에서만 최종 PDF 첨부. 승인자 허용 목록은 기존 함수가 확인한다.
    if review.get('report_pdf_path'):
        from pathlib import Path as _Path
        import hashlib as _hashlib
        attachment = _Path(review['report_pdf_path']).resolve()
        allowed_dir = (_Path(__file__).resolve().parents[2] / 'backup/approval_demo_private/final_reports').resolve()
        if (attachment.parent != allowed_dir or attachment.suffix.lower() != '.pdf' or
                not attachment.is_file() or not 0 < attachment.stat().st_size <= 15*1024*1024):
            raise ValueError('승인 요청 PDF 경로 또는 크기가 올바르지 않습니다.')
        content = attachment.read_bytes()
        if not content.startswith(b'%PDF-') or _hashlib.sha256(content).hexdigest() != review.get('report_pdf_sha256'):
            raise ValueError('승인 요청 PDF 무결성 검사 실패')
        message.add_attachment(content, maintype='application', subtype='pdf',
                               filename='CART_SIGNAL_최종_보고서.pdf')
    if review.get('report_png_path'):
        from .delivery_r42 import validate_png, MAX_PNG, MAX_TOTAL
        png = _Path(review['report_png_path']).resolve()
        if not review.get('report_pdf_path') or png.parent != allowed_dir or png.suffix != '.png' or not png.is_file() or png.stat().st_size > MAX_PNG:
            raise ValueError('최종 PNG 경로 또는 크기 오류')
        png_content = validate_png(png.read_bytes())
        if _hashlib.sha256(png_content).hexdigest() != review.get('report_png_sha256') or len(content)+len(png_content)>MAX_TOTAL:
            raise ValueError('최종 첨부 무결성 또는 크기 오류')
        message.add_attachment(png_content, maintype='image', subtype='png', filename='CART_SIGNAL_최종_기획전.png')
    host = os.getenv('CART_APPROVAL_SMTP_HOST', 'smtp.gmail.com')
    port = int(os.getenv('CART_APPROVAL_SMTP_PORT', '587'))
    with smtplib.SMTP(host, port, timeout=25) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(message)
    review['status'] = 'PENDING'
    review['sent_at'] = _utc()
    review['history'].append({'at': _utc(), 'event': 'TEAM_EMAIL_SENT', 'recipient': approver})
    update_review(path, review)
    return {'status': 'PENDING', 'request_id': review['request_id'], 'recipient': approver}


def _decoded(value: str) -> str:
    return str(make_header(decode_header(value or '')))


def _first_plain_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain' and part.get_content_disposition() != 'attachment':
                try:
                    return part.get_content()
                except Exception:
                    return ''
        return ''
    if msg.get_content_type() == 'text/plain':
        try:
            return msg.get_content()
        except Exception:
            return ''
    return ''


def _decision_from_message(message, review):
    """발신자·전송 이후·요청 ID·토큰·명시적 결정이 일치해야 한다."""
    sender = parseaddr(_decoded(message.get('From', '')))[1].strip().lower()
    if sender != review['approver'].lower():
        return None
    subject = _decoded(message.get('Subject', ''))
    if f"CART APPROVAL DEMO {review['request_id']}" not in subject:
        return None
    try:
        when = parsedate_to_datetime(message.get('Date', ''))
        if when.tzinfo is None:
            return None
        if when.astimezone(timezone.utc) < datetime.fromisoformat(review['sent_at']):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    # 답장 인용부의 오래된 명령은 검사하지 않고 본문 첫 줄만 처리한다.
    lines = _first_plain_text(message).splitlines()
    first = next((line.strip() for line in lines if line.strip()), '')
    expected = re.fullmatch(r'(APPROVE|REJECT)\s+([A-Za-z0-9_\-]+)', first)
    if not expected or expected.group(2) != review['approval_token']:
        return None
    return 'APPROVED' if expected.group(1) == 'APPROVE' else 'REJECTED'


def check_team_reply(path):
    """IMAP 답장에서 승인/반려 확인 후 저장. 없으면 PENDING 유지."""
    review = load_review(path)
    if review.get('status') != 'PENDING':
        return {'status': review.get('status', 'UNKNOWN'), 'request_id': review.get('request_id')}
    _validate_address(review['approver'])
    user, password = _credentials('IMAP')
    host = os.getenv('CART_APPROVAL_IMAP_HOST', 'imap.gmail.com')
    port = int(os.getenv('CART_APPROVAL_IMAP_PORT', '993'))
    with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context()) as box:
        box.login(user, password)
        status, _ = box.select('INBOX', readonly=True)
        if status != 'OK':
            raise RuntimeError('IMAP 받은편지함을 열 수 없습니다.')
        status, ids = box.search(None, 'FROM', f'"{review["approver"]}"')
        if status != 'OK':
            raise RuntimeError('IMAP 검색 실패')
        found = []
        from email import message_from_bytes
        for msg_id in (ids[0].split() if ids and ids[0] else [])[-100:]:
            status, content = box.fetch(msg_id, '(RFC822)')
            if status != 'OK' or not content:
                continue
            payload = next((item[1] for item in content if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
            if not payload:
                continue
            message = message_from_bytes(payload, policy=default_policy)
            decision = _decision_from_message(message, review)
            if decision:
                found.append((msg_id, decision))
    if found:
        # 첫 유효 응답 채택: 이후의 다른 답장으로 이미 기록된 결정을 자동 변경하지 않는다.
        review['status'] = found[0][1]
        review['decided_at'] = _utc()
        review['history'].append({'at': _utc(), 'event': 'TEAM_REPLY_VERIFIED', 'status': review['status']})
        update_review(path, review)
    return {'status': review['status'], 'request_id': review['request_id']}
