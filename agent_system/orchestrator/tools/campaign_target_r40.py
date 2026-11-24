"""R40: 진단과 동일한 전체 모집단 기준 구매 순위·취향 분류. 실제 발송 없음."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import secrets
import shutil

import numpy as np
import pandas as pd

POLICY = 'r40_v1'
WINDOW = 180
SHRINK_K = 20
PRODUCT_WEIGHT = .3
COLOUR_WEIGHT = .7
PREDICTION_TOP = .03
TASTE_TOP = .10
SEGMENTS = ('excluded', 'reactivation', 'cross_sell', 'core', 'unknown_taste')
LABELS = dict(zip(SEGMENTS, ('제외', '재활성화', '교차판매', '핵심 타깃', '취향 미확인')))
CANDIDATES = ('core', 'cross_sell', 'reactivation')
BASIS = 'R40 · 구매 순위 상위 3% · 양수 취향 상위 10% · 180일 · K=20 · 품목 0.3/색상 0.7 · 조합별 최대값'
STRATEGIES = {
    'core': ('구매 순위와 취향이 모두 높아 기획전 주요 상품을 우선 소개합니다.', '기획전의 주요 스타일을 만나보세요.'),
    'cross_sell': ('구매 순위는 높지만 취향 기준 미충족으로 다양한 스타일을 소개합니다.', '새로운 스타일과 컬러 조합을 살펴보세요.'),
    'reactivation': ('취향은 높지만 구매 순위 기준 미충족으로 관심을 다시 유도합니다. 휴면 여부는 미확인입니다.', '관심 스타일의 새로운 구성을 만나보세요.'),
}


def classify(predictions, tastes):
    """진단과 동일: 조합 내 평균 동점 백분위, 조합별 최대, 양수 취향 분위수."""
    logits = np.asarray(predictions, dtype=float)
    taste = np.asarray(tastes, dtype=float)
    if logits.ndim != 2 or logits.shape != taste.shape or not logits.size or not np.isfinite(logits).all():
        raise ValueError('R40 점수 배열 검증 실패')
    if np.isinf(taste).any() or ((taste[np.isfinite(taste)] < 0) | (taste[np.isfinite(taste)] > 1)).any():
        raise ValueError('R40 취향 점수 범위 오류')
    ranks = np.stack([pd.Series(x).rank(method='average', pct=True).to_numpy() for x in logits])
    p = ranks.max(axis=0)
    t = np.max(np.where(np.isfinite(taste), taste, -np.inf), axis=0)
    t[~np.isfinite(t)] = np.nan
    valid = np.isfinite(t)
    positive = t[valid & (t > 0)]
    cutoff = float(np.quantile(positive, 1-TASTE_TOP)) if positive.size else None
    high_t = valid & (t > 0) & (t >= cutoff) if cutoff is not None else np.zeros(len(t), bool)
    high_p = p >= 1-PREDICTION_TOP
    codes = np.where(valid, 2*high_p.astype(int)+high_t.astype(int), 4)
    return p, t, np.array(SEGMENTS)[codes], cutoff, ranks


def weighted_taste(frame):
    parts = []
    for attr, w in [('product_type', PRODUCT_WEIGHT), ('colour', COLOUR_WEIGHT)]:
        ratio = frame[f'{attr}_ratio'].to_numpy(float)
        n = frame[f'{attr}_valid'].to_numpy(float)
        if not np.isfinite(n).all() or (n < 0).any() or np.isinf(ratio).any() or ((ratio[np.isfinite(ratio)] < 0) | (ratio[np.isfinite(ratio)] > 1)).any():
            raise ValueError('R40 취향 원시 자료 범위 오류')
        parts.append(w * (ratio*n/(n+SHRINK_K)))
    return np.sum(parts, axis=0)


def score_campaign(campaign, run, root):
    from . import campaign_target_r39 as legacy
    from modeling.purchase_model import connect_team, article_attributes, select_model, supported_combos, score_combo, taste_scores
    from agent_system.agents.model_targeting_agent import validate_ranked_frame
    if not legacy.active(run):
        raise ValueError('먼저 기획전 페이지 틀을 승인해 주세요.')
    values = campaign.get('values') or {}
    ids = list(dict.fromkeys(re.findall(r'(?<!\d)\d{10}(?!\d)', str(values.get('products') or ''))))
    if not ids:
        raise ValueError('확정 참고 상품 ID가 없습니다.')
    folder = legacy._folder(run, root) / secrets.token_hex(8)
    folder.mkdir(mode=0o700)
    try:
        model = select_model(legacy.DATE)
        info, logits, tastes = [], [], []
        population = None
        with connect_team(Path(root)) as data:
            combos = legacy.campaign_combos(values, article_attributes(data, ids), supported_combos(model))
            # 진단은 동일 품목 내 색상 조합만 평가했으므로 이 범위를 벗어나면 중단한다.
            if len({pt for pt, _ in combos}) != 1:
                raise ValueError('R40은 동일 품목의 색상 조합만 지원합니다.')
            for pt, color in combos:
                scored = score_combo(data, pt, color, legacy.DATE)
                validate_ranked_frame(scored.scores)
                if str(scored.score_as_of) != legacy.DATE or (scored.product_type, scored.colour) != (pt, color) or str(scored.model_run_id) != str(model.run_id):
                    raise ValueError('모델 결과의 조합·기준일·실행 ID가 다릅니다.')
                frame = scored.scores[['customer_id', 'score_logit']].copy()
                frame['customer_id'] = frame.customer_id.astype(str)
                frame = frame.set_index('customer_id')
                if population is None:
                    population = frame.index.sort_values()
                if len(frame) != len(population) or not frame.index.difference(population).empty:
                    raise ValueError('R40 조합 간 점수화 고객 집합 불일치')
                frame = frame.reindex(population)
                taste = taste_scores(data, legacy.DATE, window_days=WINDOW, target={'product_type':pt, 'colour':color}, customer_ids=population.tolist())
                taste['customer_id'] = taste.customer_id.astype(str)
                if taste.customer_id.duplicated().any() or len(taste) != len(population) or not pd.Index(taste.customer_id).difference(population).empty:
                    raise ValueError('R40 취향 고객 집합 불일치')
                taste = taste.set_index('customer_id').reindex(population)
                logits.append(frame.score_logit.to_numpy(float))
                tastes.append(weighted_taste(taste))
                info.append({'product_type':pt, 'colour':color, 'total':len(population), 'model_run_id':str(scored.model_run_id)})
        p, t, segments, cutoff, ranks = classify(logits, tastes)
        cache = pd.DataFrame({'customer_id':population, 'target_segment':segments, 'target_percentile':p, 'taste_weighted':t})
        for i, (logit, rank, taste) in enumerate(zip(logits, ranks, tastes), 1):
            cache[f'combo_{i}_score_logit'] = logit
            cache[f'combo_{i}_percentile'] = rank
            cache[f'combo_{i}_taste_weighted'] = taste
        path = folder/'r40_scores.csv'
        legacy._write_csv(cache, path)
        target = {'status':'review', 'policy':POLICY, 'fingerprint':legacy.fingerprint(campaign, run), 'folder':str(folder), 'combos':info, 'date':legacy.DATE,
                  'cache_sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'taste_cutoff':cutoff}
        return propose(target, campaign, run, root)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _cache(target, campaign, run, root):
    from . import campaign_target_r39 as legacy
    if not legacy.active(run) or target.get('status') != 'review' or target.get('policy') != POLICY or target.get('fingerprint') != legacy.fingerprint(campaign, run):
        raise ValueError('현재 기획전과 일치하는 R40 분석 결과가 없습니다.')
    folder = Path(target['folder']).resolve()
    if folder.parent != legacy._folder(run, root) or not folder.is_dir():
        raise ValueError('R40 저장 경로 오류')
    path = folder/'r40_scores.csv'
    if path.resolve() != path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != target.get('cache_sha256'):
        raise ValueError('R40 점수 파일 무결성 오류')
    df = pd.read_csv(path, dtype={'customer_id':str})
    if df.empty or df.customer_id.isna().any() or df.customer_id.duplicated().any() or not df.target_segment.isin(SEGMENTS).all() or any(c['total'] != len(df) for c in target['combos']):
        raise ValueError('R40 저장 점수 자료 검증 실패')
    return folder, df


def _summary(df, cutoff):
    counts = df.target_segment.value_counts()
    all_groups = [{'key':key,'name':LABELS[key],'count':int(counts.get(key,0)), 'share_percent':round(100*int(counts.get(key,0))/len(df),2)} for key in ('core','cross_sell','reactivation','excluded','unknown_taste')]
    groups = []
    for g in all_groups:
        if g['key'] in CANDIDATES and g['count']:
            strategy, message = STRATEGIES[g['key']]
            groups.append({**g, 'csv':f"target_{g['key']}.csv", 'strategy':strategy, 'message':message})
    return {'policy':POLICY,'total':sum(g['count'] for g in groups), 'population':len(df), 'groups':groups,'all_groups':all_groups,'taste_cutoff':cutoff}


def propose(target, campaign, run, root):
    _, df = _cache(target, campaign, run, root)
    proposal = _summary(df, target['taste_cutoff'])
    return {**target, 'proposal':proposal, 'review_token':secrets.token_hex(16)}


def review(target, command, campaign, run, root):
    from . import campaign_target_r39 as legacy
    if not target.get('proposal') or not target.get('review_token'):
        raise ValueError('R40 선정 제안을 다시 확인해 주세요.')
    folder, df = _cache(target, campaign, run, root)
    proposal = _summary(df, target['taste_cutoff'])
    if proposal != target['proposal']:
        raise ValueError('검토한 R40 결과가 변경되었습니다. 다시 선정해 주세요.')
    if command == 'evidence':
        return target
    result = {k:v for k,v in target.items() if k not in ('proposal','review_token')}
    if command == 'reject':
        return result
    if command != 'approve':
        raise ValueError('R40 기본 정책은 고정 비율입니다. 임의 규모 변경은 지원하지 않습니다.')
    if not proposal['total']:
        raise ValueError('선정 가능한 후보가 없어 승인할 수 없습니다.')
    out = folder/('selection_'+secrets.token_hex(6))
    out.mkdir(mode=0o700)
    try:
        for g in proposal['groups']:
            legacy._write_csv(df[df.target_segment == g['key']], out/g['csv'])
        legacy._write_csv(df[df.target_segment.isin(CANDIDATES)], out/'target_all.csv')
        legacy._write_csv(pd.DataFrame(proposal['all_groups']), out/'target_summary.csv')
        legacy._write_csv(pd.DataFrame([{'column_prefix':f'combo_{i}','product_type':c['product_type'],'colour':c['colour'],'as_of':legacy.DATE,'taste_window_days':WINDOW} for i,c in enumerate(target['combos'],1)]), out/'target_combos.csv')
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return {**result, **proposal, 'status':'confirmed','output_dir':str(out)}


def notice(target, *, evidence=False, confirmed=False):
    p = target if confirmed else target.get('proposal')
    if not p:
        return 'R40 분석 결과를 유지합니다. 다시 검토하려면 “고객 타깃 선정해줘”라고 말씀해 주세요.'
    lines = ['고객 타깃 확정' if confirmed else '고객 타깃 선정 제안', '구매 예측 상위 3% · 양수 취향 상위 10%',
             'H&M 2020-09-23 기준 30일 예측 · 실제 구매확률 및 발송 가능 인원 아님',
             f"점수화 고객 {p['population']:,}명 / 마케팅 후보 {p['total']:,}명 (중복 제거)"]
    lines += [f"• {g['name']}: {g['count']:,}명 ({g['share_percent']:.2f}%, 전체 기준)" for g in p['all_groups']]
    if evidence:
        cutoff = p['taste_cutoff']
        lines += ['동일 조합 내 평균 동점 백분위 및 조합별 최대 구매 순위·취향 사용. 취향 0점은 높은 취향에서 제외.',
                  f"취향 경계값: {cutoff:.6f}" if cutoff is not None else '양수 취향 없음',
                  '핵심=높은 순위·높은 취향, 교차판매=높은 순위·낮은 취향, 재활성화=낮은 순위·높은 취향. 결측은 미확인.',
                  'Jacket 두 색상의 과거 검증으로 정한 기본 정책이며 다른 조합에 대한 최적성은 미검증.']
    if confirmed:
        lines += ['승인된 마케팅 후보 3개 고객군의 CSV를 로컬에 저장했습니다. 제외·미확인은 집계만 저장합니다.', '준비되시면 “고객군별 마케팅 문구 생성해줘”라고 말씀해 주세요.']
    else:
        lines += ['승인 후 마케팅 후보 CSV를 저장합니다. 실제 고객 발송은 하지 않습니다.']
    return '\n'.join(lines)
