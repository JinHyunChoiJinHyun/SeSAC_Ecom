"""R38: 승인과 고객 타깃 분석 시작을 별도 의사결정으로 관리한다."""
from copy import deepcopy
import re

WAIT_NOTICE = '기획전 페이지 틀이 확정되었습니다. 준비되시면 “고객 타깃 선정해줘”라고 말씀해 주세요.'


def target_start_intent(question: str) -> str:
    """명시적인 실행 요청만 인식한다. 결과 조회·질문·부정 요청은 제외한다."""
    text = re.sub(r'\s+', '', str(question or '')).strip('.!。！')
    if re.fullmatch(r'(?:이제|그럼|좋아[,，]?)?(?:다음단계(?:로)?|계속|워크플로계속)(?:진행(?:해줘|해주세요|하자|할게)|넘어가(?:자|줘|주세요)|해줘|해주세요)?', text):
        return 'continue'
    if re.fullmatch(r'(?:이제|그럼)?(?:(?:이번|현재)?기획전(?:에맞는|에적합한|의)?|이번)?(?:구매가능성이높은)?(?:고객(?:타[깃겟])?|타[깃겟])(?:을|를)?(?:분석|선정|추천|찾아|정해)(?:을|를)?(?:시작)?(?:해줘|해주세요|해보자|하자|하겠습니다|할게|보자|줘|주세요|자)?', text):
        return 'target'
    if re.fullmatch(r'(?:이제)?누구한테마케팅할지정해(?:보자|줘|주세요)', text):
        return 'target'
    return ''


def waiting_for_target_start(run: dict) -> bool:
    return (run.get('workflow') == 'trend_planning'
            and run.get('status') == 'paused'
            and run.get('target_start_pending') is True
            and (run.get('page_preview') or {}).get('status') == 'approved')


def approve_preview(run: dict, preview: dict, step_index: int) -> dict:
    new_run = deepcopy(run)
    new_run['page_preview'] = deepcopy(preview)
    new_run['page_preview']['status'] = 'approved'
    new_run.update(step_index=step_index, status='paused', waiting_key='',
                   target_start_pending=True, report_draft={})
    return new_run


def start_targeting(run: dict, campaign: dict, pending: dict, question: str):
    """실행 여부·새 상태·안내를 반환. 기존 승인값과 시안은 변경하지 않는다."""
    if not waiting_for_target_start(run) or not target_start_intent(question):
        return None, ''
    if pending:
        return None, '진행 중인 다른 승인 요청을 먼저 처리해 주세요.'
    values = campaign.get('values') or {}
    if not all(values.get(key) for key in ('products', 'page')):
        return None, '확정 상품과 카피를 확인할 수 없습니다. 기획전 정보를 먼저 확인해 주세요.'
    new_run = deepcopy(run)
    new_run.update(status='running', waiting_key='', target_start_pending=False)
    return new_run, ''



def migrate_legacy_review(run, campaign, pending, question, steps):
    """미승인 R39 제안만 명시적 신규 선정 요청에서 R40으로 전환한다."""
    from . import campaign_target_r39 as legacy
    target = run.get('campaign_target_r39') or {}
    if not (legacy.active(run) and run.get('status') == 'paused'
            and target.get('status') == 'review' and not target.get('policy')
            and legacy.target_request(question)):
        return None, ''
    if pending:
        return None, '진행 중인 다른 승인 요청을 먼저 처리해 주세요.'
    if run.get('report_draft') or run.get('segment_copy_r41') or run.get('report_sent_r39'):
        return None, '후속 산출물이 존재하여 기존 고객 선정 상태를 자동 변경하지 않습니다.'
    if not all((campaign.get('values') or {}).get(k) for k in ('products', 'page')):
        return None, '확정 상품과 페이지 정보를 먼저 확인해 주세요.'
    index = next((i for i, (_, step) in enumerate(steps) if step.no == 17), None)
    if index is None:
        return None, '고객 분석 시작 단계를 찾지 못했습니다.'
    new = deepcopy(run)
    new.update(campaign_target_r39={}, segment_copy_r41={}, report_draft={},
               status='running', waiting_key='', target_start_pending=False,
               step_index=index)
    return new, ''
