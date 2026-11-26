"""시연 전용 승인 CLI. 실제 고객 메시지 발송 경로는 제공하지 않는다."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .workflow import prepare_review, store_review, load_review, check_review
from .mail import send_team_approval, check_team_reply


def main():
    parser = argparse.ArgumentParser(description='CART SIGNAL 팀원 이메일 승인 시연(고객 발송 없음)')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare', help='타깃 결과 JSON으로 시연용 승인 요약 생성')
    p.add_argument('--result-json', type=Path)
    p.add_argument('--question', help='실제 R2 타깃 Agent 실행 질문')
    p.add_argument('--campaign-json', type=Path)
    p.add_argument('--message-file', required=True, type=Path)
    p.add_argument('--approver', required=True)
    p.add_argument('--output', required=True, type=Path)
    s = sub.add_parser('send', help='팀원 이메일로 승인 요청 메일 보내기')
    s.add_argument('--record', required=True, type=Path)
    s.add_argument('--confirm-send', action='store_true')
    c = sub.add_parser('check', help='팀원의 명시적 이메일 승인·반려 답장 확인')
    c.add_argument('--record', required=True, type=Path)
    v = sub.add_parser('status', help='승인 기록 상태 조회')
    v.add_argument('--record', required=True, type=Path)
    args = parser.parse_args()
    if args.cmd == 'prepare':
        if bool(args.result_json) == bool(args.question):
            parser.error('--result-json 또는 --question 중 정확히 하나를 지정하세요.')
        if args.result_json:
            result = json.loads(args.result_json.read_text(encoding='utf-8'))
        else:
            from agent_system.agents.dynamic_targeting import calculate_requested_target
            campaign = json.loads(args.campaign_json.read_text(encoding='utf-8')) if args.campaign_json else {}
            result = calculate_requested_target(args.question, campaign, Path.cwd())
        # 기존 타깃 검증 게이트를 실제로 통과한 결과만 허용한다.
        from agent_system.validation.specialized_selection_gate import check_selection
        verdict = check_selection('targeting', '고객 선정', result)
        if verdict['status'] != 'PASS':
            raise SystemExit('고객 선정 검증 실패: ' + ','.join(verdict['errors']))
        review = prepare_review(result, message_draft=args.message_file.read_text(encoding='utf-8'), approver=args.approver)
        store_review(review, args.output)
        print(json.dumps({'status': review['status'], 'request_id': review['request_id'],
                          'preview_count': review['preview_count'], 'candidate_count': review['candidate_count'],
                          'control_count': review['control_count'], 'record': str(args.output)}, ensure_ascii=False, indent=2))
    elif args.cmd == 'send':
        print(json.dumps(send_team_approval(args.record, confirm=args.confirm_send), ensure_ascii=False))
    elif args.cmd == 'check':
        print(json.dumps(check_team_reply(args.record), ensure_ascii=False))
    else:
        review = load_review(args.record)
        print(json.dumps({'status': review.get('status'), 'request_id': review.get('request_id'),
                          'validation': check_review(review)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
