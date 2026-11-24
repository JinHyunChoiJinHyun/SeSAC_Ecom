"""대표 문구 재표시 명령: 저장된 후보를 변경하지 않는 읽기 전용 경로."""
import re


def is_copy_replay_request(question):
    """명시적인 대표 문구/카피 재표시만 인식하고 수정·선택 명령과 분리한다."""
    text = re.sub(r"\s+", "", str(question or "").strip()).rstrip(".!！。")
    return bool(re.fullmatch(
        r"(?:대표문구|캠페인문구|기획전문구|문구|카피)(?:3안|세안|후보|분석)?"
        r"(?:다시보여줘|다시보여주세요|다시알려줘|재표시|다시출력해줘)", text, re.IGNORECASE
    ) or re.fullmatch(
        r"(?:현재|지금)(?:대표문구|캠페인문구|문구|카피)(?:3안|후보)?(?:보여줘|보여주세요|알려줘)",
        text, re.IGNORECASE
    ))


def copy_replay_notice(run, campaign, pending):
    """(문구, 성공 여부). 승인·워크플로 상태를 절대 변경하지 않는다."""
    if pending:
        return '진행 중인 승인 요청을 먼저 처리하거나 취소해 주세요. 대표 문구와 확정값은 변경하지 않았습니다.', False
    if run.get('workflow') != 'trend_planning':
        return '현재 기획전 분석 세션이 없습니다. 기존 기획전 Slack 대화에서 요청해 주세요.', False
    package = run.get('copy_package') or {}
    if package.get('status') != 'verified' or not package.get('candidates'):
        return '현재 세션에 검증·저장된 대표 문구 3안이 없습니다. 소구점 승인 후 다시 요청해 주세요.', False
    from agent_system.orchestrator.tools.campaign_copy import render_options
    selected = (campaign.get('values') or {}).get('page')
    if selected:
        intro = ('■ 현재 대표 문구 3안 (선택 완료)\n'
                 '이미 대표 문구가 확정된 상태입니다. 다른 안을 선택하려면 '
                 '"대표 문구 단계로 되돌리기"를 입력하세요. 이 조회로 확정값은 바뀌지 않습니다.\n\n')
    else:
        intro = ('■ 현재 대표 문구 3안 (미확정)\n'
                 '저장된 최신 수정본입니다. 이 조회로 수정·선택 이력은 변경되지 않습니다.\n\n')
    return intro + render_options(package), True
