"""기획전 승인값에 대한 구매 모델 순위 및 배타적 고객군 CSV (고객 발송 없음)."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

DATE = '2020-09-23'
MAX_K = 10000
ROOT = Path(__file__).resolve().parents[3]


def in_campaign(run):
    return run.get('workflow') == 'trend_planning' and run.get('status') not in ('done', 'cancelled', None)


def approved(run):
    return run.get('workflow') == 'trend_planning' and (run.get('page_preview') or {}).get('status') == 'approved'


def active(run):
    return in_campaign(run) and approved(run)


def delivery_allowed(run):
    # 완료된 기획전도 동일 Run의 확정 산출물로 추가 사내 발송할 수 있다.
    return active(run) or bool(approved(run) and run.get('status') == 'done' and run.get('report_sent_r39'))

def complete_after_report(run, campaign, saved, nonce):
    target = confirmed_for_report(run, campaign)
    pending = saved.get('pending') or {}
    registered = saved.get('target') or {}
    sent_nonce = (pending.get('status') == 'SENT' and pending.get('nonce') == nonce) or (
        bool(nonce) and any(item.get('status') == 'SENT' and item.get('nonce_hash') == hashlib.sha256(str(nonce).encode()).hexdigest()
                            for item in saved.get('delivery_history', [])))
    if (not active(run) or not target or not saved.get('report_sent') or not sent_nonce
            or registered.get('kind') != 'r39'
            or registered.get('run_id') != run.get('run_id') or registered.get('fingerprint') != target['fingerprint']
            or registered.get('segment_copy_hash') != email_summary(run, target).get('segment_copy_hash')):
        return None
    return dict(run, status='done', waiting_key='', target_start_pending=False, report_sent_r39=saved['report_sent'])


def fingerprint(campaign, run):
    values = campaign.get('values') or {}
    data = {k: values.get(k) for k in ('category', 'subtype', 'colors', 'products', 'selling_points', 'page', 'schedule')}
    data['revision'] = (run.get('page_preview') or {}).get('revision')
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_k(text):
    match = re.fullmatch(r'\s*(?:색상별\s*)?(?:상위\s*)?([0-9][0-9,]*)\s*명\s*(?:씩\s*)?(?:선정|확정|으로\s*할게|으로\s*해줘|선정해줘|확정해줘|으로\s*확정)?[.!]?\s*', str(text))
    if not match:
        return None
    n = int(match[1].replace(',', ''))
    if not 1 <= n <= MAX_K:
        raise ValueError('색상별 1~10,000명을 입력해 주세요.')
    return n


def target_request(text):
    from .target_start_r38 import target_start_intent
    if target_start_intent(text) == 'target':
        return True
    text = re.sub(r'\s+', '', str(text)).strip('.!')
    return bool(re.fullmatch(r'(?:이제)?(?:구매예측모델(?:을)?(?:활용해서|사용해서|기반으로))?(?:이번기획전(?:의상품을)?(?:에맞는)?)?(?:구매할가능성이높은|구매가능성이높은)?(?:고객(?:타[깃겟])?|타[깃겟])(?:을|를)?(?:선정|분석|추천)(?:해줘|해주세요|하자|해보자)', text))


def target_result_request(text):
    from agent_system.approval_demo.recipient_flow import is_selected_customer_analysis_request
    return is_selected_customer_analysis_request(text) or bool(re.fullmatch(r'(?:고객\s*)?타[깃겟]\s*(?:선정\s*|분석\s*)?결과\s*(?:다시\s*)?보여\s*(?:줘|주세요)[.!]?', str(text).strip()))


def campaign_combos(values, attrs, supported):
    """한국어 유형을 임의로 영문 품목으로 치환하지 않고 승인 참고 상품의 DB 분류를 사용."""
    colors = values.get('colors') or []
    if isinstance(colors, str):
        colors = colors.split(',')
    colors = list(dict.fromkeys(str(c).strip() for c in colors if str(c).strip()))
    if not colors:
        raise ValueError('확정된 기획전 색상이 없습니다.')
    pairs = set(zip(supported['product_type'], supported['colour']))
    combos = []
    for color in colors:
        types = set(attrs.loc[attrs['colour_group_name'] == color, 'product_type_name'].dropna())
        if len(types) != 1 or 'Unknown' in types:
            raise ValueError(f'{color}: 참고 상품에서 단일 모델 품목을 확인할 수 없습니다.')
        pt = str(next(iter(types)))
        if (pt, color) not in pairs:
            raise ValueError(f'{pt} × {color}: 모델 미지원 조합입니다. 다른 모델로 대체하지 않습니다.')
        combos.append((pt, color))
    return combos


def _folder(run, root):
    token = str(run.get('run_id') or '')
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
        raise ValueError('기획전 식별자가 올바르지 않습니다.')
    base = Path(root).resolve() / 'backup' / 'approval_demo_private' / 'campaign_targets'
    folder = base / token
    if folder.resolve() != folder:
        raise ValueError('타깃 저장 경로가 올바르지 않습니다.')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def _write_csv(frame, path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as out:
        frame.to_csv(out, index=False)


def score_campaign(campaign, run, root=ROOT):
    from modeling.purchase_model import connect_team, article_attributes, select_model, supported_combos, score_combo, taste_scores
    from agent_system.agents.model_targeting_agent import validate_ranked_frame
    values = campaign.get('values') or {}
    ids = list(dict.fromkeys(re.findall(r'(?<!\d)\d{10}(?!\d)', str(values.get('products') or ''))))
    if not ids:
        raise ValueError('확정 참고 상품 ID가 없습니다.')
    folder = _folder(run, root) / secrets.token_hex(8)
    folder.mkdir(mode=0o700)
    model = select_model(DATE)
    info = []
    with connect_team(Path(root)) as data:
        combos = campaign_combos(values, article_attributes(data, ids), supported_combos(model))
        for i, (pt, color) in enumerate(combos, 1):
            scored = score_combo(data, pt, color, DATE)
            validate_ranked_frame(scored.scores)
            if str(scored.score_as_of) != DATE or (scored.product_type, scored.colour) != (pt, color):
                raise ValueError('모델 결과의 조합 또는 기준일이 다릅니다.')
            frame = scored.scores.head(MAX_K)[['customer_id', 'score_logit', 'rank']].copy()
            frame['customer_id'] = frame.customer_id.astype(str)
            # 서비스 정책: 동일 90일 창의 원시 취향 비율만 별도 보관 (가중치/합산 없음).
            taste = taste_scores(data, DATE, window_days=90, target={'product_type': pt, 'colour': color}, customer_ids=frame.customer_id.tolist())
            taste['customer_id'] = taste.customer_id.astype(str)
            frame = frame.merge(taste[['customer_id', 'product_type_ratio', 'colour_ratio']], on='customer_id', how='left', validate='one_to_one')
            name = f'rank_{i}.csv'
            _write_csv(frame, folder / name)
            info.append({'product_type': pt, 'colour': color, 'total': len(scored.scores), 'cached': len(frame), 'file': name, 'sha256': hashlib.sha256((folder / name).read_bytes()).hexdigest(), 'model_run_id': str(scored.model_run_id)})
    return {'status': 'review', 'fingerprint': fingerprint(campaign, run), 'folder': str(folder), 'combos': info, 'date': DATE}


def scoring_notice(target):
    if target.get("policy") == "r40_v1":
        from .campaign_target_r40 import notice
        return notice(target)
    lines = ['고객 구매 예측 분석 결과', f'분석 기준: H&M 과거 구매 데이터 기반 시뮬레이션 ({DATE})', '예측 기간: 30일 · 색상별 구매 예측 순위']
    for c in target['combos']:
        lines.append(f"• {c['product_type']} × {c['colour']}: 점수 산출 {c['total']:,}명")
    cap = min(c['cached'] for c in target['combos'])
    lines += ['색상별 상위 고객을 선정하고 중복 고객은 별도 그룹으로 분리합니다.', f'선정 규모를 입력해 주세요. 예: 색상별 상위 {min(1000, cap):,}명 선정 (1~{cap:,}명)']
    return '\n'.join(lines)


def select_groups(target, k, campaign, run, root=ROOT, *, preview_only=False, expected=None):
    import pandas as pd
    if target.get('status') != 'review' or target.get('fingerprint') != fingerprint(campaign, run):
        raise ValueError('현재 기획전과 일치하는 고객 분석 결과가 없습니다.')
    if type(k) is not int or not 1 <= k <= MAX_K:
        raise ValueError('선정 규모 범위 오류')
    if k > min(c['cached'] for c in target['combos']):
        raise ValueError('선정 규모가 일부 색상의 점수 산출 고객 수보다 많습니다.')
    folder = Path(target['folder']).resolve()
    if folder.parent != _folder(run, root) or not folder.is_dir():
        raise ValueError('타깃 결과 경로가 올바르지 않습니다.')
    frames, memberships = [], {}
    for i, c in enumerate(target['combos'], 1):
        path = folder / f'rank_{i}.csv'
        if path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != c.get('sha256'):
            raise ValueError('저장 점수 파일 경로 또는 무결성 오류')
        frame = pd.read_csv(path, dtype={'customer_id': str}).head(k).copy()
        if len(frame) != min(k, c['cached']) or frame.customer_id.isna().any() or frame.customer_id.duplicated().any() or frame['rank'].tolist() != list(range(1, len(frame)+1)):
            raise ValueError('저장 순위 자료 검증 실패')
        frame['product_type'] = c['product_type']
        frame['colour'] = c['colour']
        for cid in frame.customer_id:
            memberships.setdefault(cid, []).append(i)
        frames.append(frame)
    all_rows = pd.concat(frames, ignore_index=True)
    groups = {}
    for cid, indices in memberships.items():
        groups.setdefault(tuple(indices), []).append(cid)
    out = folder / ('selection_' + secrets.token_hex(6))
    if not preview_only and expected is None:
        raise ValueError('고객 타깃 승인 후 CSV를 생성할 수 있습니다.')
    summary = []
    group_frames = []
    subtype = str((campaign.get('values') or {}).get('subtype') or '기획전 상품')
    # 조합별 점수/순위를 별도 열 또는 행으로 유지. CSV 고객군은 배타적이며 공통 그룹 내부는 조합별 행.
    for number, (indices, customers) in enumerate(sorted(groups.items()), 1):
        labels = [target['combos'][i-1]['colour'] for i in indices]
        label = ' · '.join(labels) + (' 공통' if len(indices)>1 else ' 단독')
        # 하나의 고객당 한 행, 색상별 점수와 취향 비율을 별도 열로 저장
        sub = all_rows[all_rows.customer_id.isin(customers)]
        wide = pd.DataFrame({'customer_id': customers})
        for i in indices:
            c = target['combos'][i-1]
            cols = ['customer_id', 'score_logit', 'rank', 'product_type_ratio', 'colour_ratio']
            selected = sub[sub.colour == c['colour']][cols].rename(columns={x: f'combo_{i}_{x}' for x in cols if x != 'customer_id'})
            wide = wide.merge(selected, on='customer_id', validate='one_to_one')
        wide.insert(1, 'target_segment', label)
        name = f'target_group_{number:02d}.csv'
        # 승인 전에는 파일을 생성하지 않는다.
        group_frames.append(wide)
        strategy = ('선정 색상의 스타일을 비교하고 선택지를 안내합니다.' if len(indices)>1 else f'{labels[0]} 색상의 스타일링과 디자인을 중심으로 안내합니다.')
        message = (' · '.join(labels) + f' 컬러의 {subtype} 스타일을 비교해 보세요.' if len(indices)>1 else f'{labels[0]} 컬러로 만나는 {subtype} 스타일을 살펴보세요.')
        summary.append({'name': label, 'count': len(customers), 'share_percent': round(100*len(customers)/len(memberships), 2), 'csv': name, 'strategy': strategy, 'message': message, 'combos': list(indices)})
    proposal = {'k': k, 'total': len(memberships), 'groups': summary}
    if preview_only:
        return proposal
    if proposal != expected:
        raise ValueError('검토한 선정 결과가 변경되었습니다. 다시 선정해 주세요.')
    out.mkdir(mode=0o700)
    for group, frame in zip(summary, group_frames):
        _write_csv(frame, out / group['csv'])
    _write_csv(pd.concat(group_frames, ignore_index=True), out / 'target_all.csv')
    _write_csv(pd.DataFrame(summary).drop(columns=['combos']), out / 'target_summary.csv')
    _write_csv(pd.DataFrame([{'column_prefix': f'combo_{i}', 'product_type': c['product_type'], 'colour': c['colour'], 'as_of': DATE, 'taste_window_days': 90} for i, c in enumerate(target['combos'], 1)]), out / 'target_combos.csv')
    return {**target, 'status': 'confirmed', 'k': k, 'total': len(memberships), 'groups': summary, 'output_dir': str(out)}



REVIEW_COMMANDS = {'approve', 'reject', 'resize', 'evidence'}


def propose_selection(target, k, campaign, run, root=ROOT):
    proposal = select_groups(target, k, campaign, run, root=root, preview_only=True)
    return {**target, 'proposal': proposal, 'review_token': secrets.token_hex(16)}


def review_action_valid(run, action):
    target = run.get('campaign_target_r39') or {}
    return (active(run) and run.get('status') == 'paused'
            and target.get('status') == 'review' and bool(target.get('proposal'))
            and bool(target.get('review_token')) and action.get('token') == target['review_token']
            and action.get('run_id') == run.get('run_id') and action.get('command') in REVIEW_COMMANDS
            and not (target.get('policy') == 'r40_v1' and action.get('command') == 'resize'))


def review_selection(target, command, campaign, run, root=ROOT):
    if target.get("policy") == "r40_v1":
        from .campaign_target_r40 import review
        return review(target, command, campaign, run, root)
    if target.get('status') != 'review' or not target.get('proposal') or target.get('fingerprint') != fingerprint(campaign, run):
        raise ValueError('현재 기획전의 선정 제안을 다시 확인해 주세요.')
    if command == 'approve':
        result = select_groups(target, target['proposal']['k'], campaign, run, root=root, expected=target['proposal'])
    elif command in ('reject', 'resize'):
        result = dict(target)
    elif command == 'evidence':
        return target
    else:
        raise ValueError('지원하지 않는 검토 요청입니다.')
    result.pop('proposal', None)
    result.pop('review_token', None)
    return result


def proposal_notice(target, evidence=False):
    if target.get("policy") == "r40_v1":
        from .campaign_target_r40 import notice
        return notice(target, evidence=evidence)
    proposal = target['proposal']
    lines = ['고객 타깃 선정 제안', f"선정 기준: 색상별 상위 {proposal['k']:,}명", f"전체 고객: {proposal['total']:,}명 (중복 제거)"]
    for group in proposal['groups']:
        lines.append(f"• {group['name']}: {group['count']:,}명 ({group['share_percent']}%)")
        if evidence:
            lines.append('  선정 근거: 해당 색상 조합의 모델 상위 순위 포함 여부. ' + group['strategy'])
    if evidence:
        lines.append('구매 점수는 색상별로 독립적으로 사용하며 취향 비율은 별도 참고 지표입니다.')
    lines.append('선정 결과를 검토해 주세요. 승인 후 분류별 CSV를 저장합니다.')
    return '\n'.join(lines)

def confirmed_notice(target):
    if target.get("policy") == "r40_v1":
        from .campaign_target_r40 import notice
        return notice(target, confirmed=True)
    lines = [f"고객 타깃을 확정했습니다. 중복 제거 {target['total']:,}명", f"선정 기준: 색상별 상위 {target['k']:,}명"]
    for group in target['groups']:
        lines.append(f"• {group['name']}: {group['count']:,}명 ({group['share_percent']}%) — {group['strategy']}")
    lines += ['분류별 CSV와 집계 CSV를 로컬에 저장했습니다.', f"저장 위치: {Path(target['output_dir']).name} (backup/approval_demo_private/campaign_targets 아래)", '준비되시면 “기획전 보고서 만들어줘”라고 말씀해 주세요.']
    return '\n'.join(lines)


def confirmed_for_report(run, campaign):
    target = run.get('campaign_target_r39') or {}
    if ((run.get('page_preview') or {}).get('status') != 'approved'
            or target.get('status') != 'confirmed' or target.get('fingerprint') != fingerprint(campaign, run)):
        return None
    groups = target.get('groups') or []
    if (not groups or not target.get('combos') or type(target.get('total')) is not int
            or target['total'] <= 0 or any(type(g.get('count')) is not int or g['count'] <= 0 for g in groups)
            or sum(g['count'] for g in groups) != target['total']):
        return None
    if target.get('policy') == 'r40_v1':
        all_groups = target.get('all_groups') or []
        if (len(all_groups) != 5 or {g.get('key') for g in all_groups} != {'core','cross_sell','reactivation','excluded','unknown_taste'}
                or any(type(g.get('count')) is not int or g['count'] < 0 for g in all_groups)
                or sum(g['count'] for g in all_groups) != target.get('population')
                or {g.get('key'):g['count'] for g in groups} != {g['key']:g['count'] for g in all_groups if g['key'] in ('core','cross_sell','reactivation') and g['count'] > 0}
                or len({g.get('key') for g in groups}) != len(groups)
                or sum(g['count'] for g in all_groups if g['key'] in ('core','cross_sell','reactivation')) != target['total']):
            return None
    return target


def selection_basis(target):
    if target.get('policy') == 'r40_v1':
        from .campaign_target_r40 import BASIS
        return BASIS
    return f"색상별 상위 {target['k']:,}명"


def email_summary(run, target):
    # 사내 보고서 전달용 집계만 별도 승인 상태에 저장한다. 고객 식별자는 전달하지 않는다.
    return {'kind': 'r39', 'run_id': run['run_id'], 'fingerprint': target['fingerprint'],
            **({'segment_copy_hash': (run.get('segment_copy_r41') or {}).get('approved_hash')} if target.get('policy') == 'r40_v1' and (run.get('segment_copy_r41') or {}).get('approved_hash') else {}),
            'source_date': DATE, 'selection_basis': selection_basis(target) if target.get('policy') == 'r40_v1' else '품목·색상별 모델 순위와 중복 제거',
            'scope_label': ' / '.join(f"{c['product_type']} × {c['colour']}" for c in target['combos']),
            'candidate_count': target['total'], 'preview_count': target['total'], 'control_count': 0,
            'total_scored_customers': max(c['total'] for c in target['combos']),
            'groups': [{'name': g['name'], 'count': g['count']} for g in target['groups']]}
