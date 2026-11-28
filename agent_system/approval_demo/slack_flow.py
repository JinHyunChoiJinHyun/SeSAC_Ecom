"""Slack에서 팀원 승인 시연을 명시적으로 실행하는 안전한 명령 라우터.

승인 수신자는 사전에 설정한 허용 목록만 가능하며 고객에게 메일을 보내지 않는다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .workflow import prepare_review, store_review, load_review, check_review

_PREFIX = '승인시연'
_ID = re.compile(r'^[0-9a-f]{14}$')


def _reply(message: str) -> dict:
    return {'turn_type': 'chitchat', 'final_answer': message, 'completed_steps': []}


def _records_root(repo_root: Path) -> Path:
    # 승인자 토큰을 포함하는 파일이므로 Git에 포함하지 않는 전용 폴더에만 저장한다.
    return repo_root / 'backup' / 'approval_demo_private' / 'slack_records'


def _record_path(repo_root: Path, request_id: str) -> Path:
    if not _ID.fullmatch(request_id):
        raise ValueError('승인 요청 ID 형식이 올바르지 않습니다.')
    return _records_root(repo_root) / (request_id + '.json')


def _owner_record(repo_root: Path, request_id: str, slack_user: str) -> tuple[Path, dict]:
    path = _record_path(repo_root, request_id)
    review = load_review(path)
    if not slack_user or review.get('slack_requester') != slack_user:
        raise PermissionError('해당 승인 요청을 생성한 Slack 사용자만 확인·발송할 수 있습니다.')
    return path, review


def run_approval_demo_command(question: str, event: dict, *, repo_root: Path | None = None) -> dict | None:
    """명시적인 승인시연 명령만 처리하고, 다른 질문은 기존 Orchestrator에 넘긴다.

    사용법:
      승인시연 준비 | 아우터 구매 고객 선정 | 검토할 광고 문구 | 팀원이메일@example.com
      승인시연 발송확인 <요청ID>
      승인시연 확인 <요청ID>
      승인시연 상태 <요청ID>
    """
    text = str(question or '').strip()
    if not (text == _PREFIX or text.startswith(_PREFIX + ' ') or text.startswith(_PREFIX + '|')):
        return None
    root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    slack_user = str(event.get('user') or '').strip()
    if not slack_user:
        return _reply('Slack 사용자 정보가 없어 승인 시연을 수행할 수 없습니다.')
    try:
        if re.match(r'^승인시연\s+준비\s*\|', text):
            pieces = [part.strip() for part in text.split('|')]
            if len(pieces) != 4 or pieces[0] != '승인시연 준비':
                raise ValueError('입력: 승인시연 준비 | 고객 선정 요청 | 광고 문구 | 승인자 이메일')
            _, target_question, message_draft, approver = pieces
            if not target_question or not message_draft or not approver:
                raise ValueError('고객 선정 요청·광고 문구·승인자 이메일이 모두 필요합니다.')
            if len(target_question) > 300 or len(message_draft) > 3000:
                raise ValueError('요청 또는 메시지 길이를 확인해 주세요.')
            from agent_system.agents.dynamic_targeting import calculate_requested_target
            from agent_system.agents.validation_agent import check_selection
            result = calculate_requested_target(target_question, {}, root)
            verdict = check_selection('targeting', '고객 선정', result)
            if verdict['status'] != 'PASS':
                raise ValueError('타깃 근거 검증 실패: ' + ', '.join(verdict.get('errors') or []))
            review = prepare_review(result, message_draft=message_draft, approver=approver)
            review['slack_requester'] = slack_user
            path = _record_path(root, review['request_id'])
            store_review(review, path)
            return _reply(
                '승인 시연 초안이 준비됐습니다 (이메일 미발송).\n'
                f"요청 ID: {review['request_id']}\n"
                f"기준일: {review['source_date']} / 범위: {review['scope_label']}\n"
                f"전체 점수 대상: {review['total_scored_customers']:,}명 / 미리보기: {review['preview_count']}명\n"
                f"시연용 타깃: {review['candidate_count']}명 / 비교용: {review['control_count']}명\n"
                '※ 2020년 과거 데이터 기반이며, 실제 고객 발송 명단이 아닙니다.\n'
                f"발송하려면 `승인시연 발송확인 {review['request_id']}`를 명시적으로 입력하세요."
            )
        match = re.fullmatch(r'승인시연\s+(발송확인|확인|상태)\s+([0-9a-f]{14})', text)
        if not match:
            return _reply('사용법: `승인시연 준비 | 고객 선정 요청 | 광고 문구 | 승인자 이메일` 또는 `승인시연 상태/발송확인/확인 요청ID`')
        action, request_id = match.groups()
        path, review = _owner_record(root, request_id, slack_user)
        if action == '상태':
            verdict = check_review(review)
            return _reply(f"요청 ID: {request_id} / 상태: {review['status']} / 검증: {verdict['status']}")
        if action == '발송확인':
            from .mail import send_team_approval
            sent = send_team_approval(path, confirm=True)
            return _reply(f"팀원 승인 요청 이메일 발송 처리: {sent['status']} / 요청 ID: {request_id}. 고객에게 발송하지 않았습니다.")
        from .mail import check_team_reply
        checked = check_team_reply(path)
        return _reply(f"팀원 승인 확인: {checked['status']} / 요청 ID: {request_id}. 고객 발송은 실행되지 않습니다.")
    except (ValueError, PermissionError, FileNotFoundError, RuntimeError, OSError) as exc:
        # SMTP/IMAP 인증 예외의 전체 문자열에는 환경 정보를 포함할 수 있어 타입만 로그에 노출한다.
        if isinstance(exc, (ValueError, PermissionError)):
            return _reply('승인 시연 요청을 처리하지 못했습니다: ' + str(exc))
        return _reply(f'승인 시연 요청 실패 ({type(exc).__name__}). 설정·대상 모델·메일 연결을 확인하세요.')
    except Exception as exc:
        return _reply(f'승인 시연 요청 실패 ({type(exc).__name__}). 서버 로그를 확인하세요.')
