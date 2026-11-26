"""재학습한 구매 예측 모델(대기 폴더)의 적용 전 자동 점검입니다.

대시보드 서버가 별도 프로세스로 실행합니다. 모델 코드(modeling.purchase_model)는 읽기만 합니다.

python -m admin_dashboard.retrain_check --candidate <대기 모델 폴더> --models-dir <사용 중 모델 루트> --out <결과 JSON>
종료 코드: 0 점검 완료(결과는 JSON의 can_apply), 1 점검 자체 실패
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

COMBO_CHANGE_WARN = 0.10   # 학습 포함 조합 수가 현재 모델 대비 10% 넘게 바뀌면 경고
OVERLAP_WARN = 0.80        # 대표 조합 상위 K명 겹침이 80% 미만이면 경고
TOP_K = 10_000
N_COMBOS = 3


def _item(key, title, level, detail):
    """level: ok(통과) · warn(경고, 적용 가능) · block(적용 불가) · info(비교 불가 등 안내)"""
    return {"key": key, "title": title, "level": level, "detail": detail}


def _summary(model) -> dict:
    meta = model.meta
    return {"as_of": meta["as_of"], "run_id": model.run_id, "combos": int(meta["counts"]["combos"]),
            "complete_through": meta.get("complete_through"), "data_max": meta.get("data_range", {}).get("max_t_dat"),
            "fit_status": meta.get("fit", {}).get("status"), "iters": meta.get("fit", {}).get("iters")}


def run_checks(candidate_dir, models_dir, data=None, top_k: int = TOP_K, n_combos: int = N_COMBOS) -> dict:
    """대기 모델을 점검합니다. data(HMData)가 없으면 순위 비교는 건너뜁니다."""
    from modeling.purchase_model.errors import TargetingError
    from modeling.purchase_model.features import build_features
    from modeling.purchase_model.model import ARRAY_KEYS, load_model, score_model, select_model

    candidate_dir = Path(candidate_dir)
    items = []
    # 1) 다시 불러오기와 폴더 이름 일치 (실패하면 이후 점검 불가)
    try:
        cand = load_model(candidate_dir)
        if candidate_dir.name != cand.run_id or candidate_dir.parent.name != f"as_of={cand.meta['as_of']}":
            raise ValueError("폴더 이름과 모델 내용(as_of, run_id)이 다릅니다.")
        items.append(_item("load", "저장 모델 다시 불러오기", "ok", f"as_of {cand.meta['as_of']}, run_id {cand.run_id}"))
    except Exception as exc:  # 형식 오류·파일 손상 모두 적용 불가로 처리
        items.append(_item("load", "저장 모델 다시 불러오기", "block", f"{type(exc).__name__}: {exc}"))
        return {"can_apply": False, "items": items, "candidate": None, "current": None}

    # 2) 수렴
    fit = cand.meta.get("fit", {})
    ok = fit.get("status") == "decrement<=tol"
    items.append(_item("converged", "학습 수렴", "ok" if ok else "block",
                       f"{fit.get('status')} · {fit.get('iters')}회 · 감소량 {fit.get('final_decrement')}"))

    # 3) 계수 이상값
    bad = [k for k in ARRAY_KEYS if not np.isfinite(np.asarray(cand.params[k], dtype=float)).all()]
    items.append(_item("finite", "계수 이상값(무한대·NaN)", "block" if bad else "ok",
                       f"이상값이 있는 배열: {', '.join(bad)}" if bad else "모든 계수가 유한한 값입니다."))

    # 현재 사용 중인 모델: 대기 모델 기준일에 지금 선택되는 모델
    try:
        current = select_model(cand.as_of, models_dir)
    except TargetingError as exc:
        current = None
        items.append(_item("current", "비교할 현재 모델", "info", f"비교 대상 없음 ({type(exc).__name__}: {exc})"))

    # 4) 학습 포함 조합 수 변화
    if current is not None:
        new, old = set(cand.index()), set(current.index())
        change = (len(new) - len(old)) / max(1, len(old))
        level = "warn" if abs(change) > COMBO_CHANGE_WARN else "ok"
        items.append(_item("combos", "학습 포함 조합 수", level,
                           f"{len(old):,}개 → {len(new):,}개 ({change:+.1%}) · 추가 {len(new - old):,} · 빠짐 {len(old - new):,}"
                           + (f" · 경고 기준 ±{COMBO_CHANGE_WARN:.0%}" if level == "warn" else "")))

    # 5) 대표 조합 고객 순위 비교 (두 모델 모두에 있는 조합 중 학습 구매 수 상위)
    comparisons = []
    if current is not None and data is not None:
        old_idx = current.index()
        pos = cand.params["train_positives"]
        ranked = sorted(((pos[i], pt, cg) for (pt, cg), i in cand.index().items() if (pt, cg) in old_idx),
                        key=lambda r: (-r[0], r[1], r[2]))[:n_combos]
        complete = cand.meta.get("complete_through")
        for _, pt, cg in ranked:
            try:
                feats = build_features(data, cand.as_of, pt, complete_through=complete)
                a = score_model(cand, feats, cg)
                b = score_model(current, feats, cg)
                k = min(top_k, len(a))
                overlap = len(set(a.customer_id[:k]) & set(b.customer_id[:k])) / k if k else float("nan")
                comparisons.append({"product_type": pt, "colour": cg, "k": k, "overlap": overlap})
            except Exception as exc:
                comparisons.append({"product_type": pt, "colour": cg, "error": f"{type(exc).__name__}: {exc}"})
        if not comparisons:
            items.append(_item("rank", "대표 조합 순위 비교", "info", "두 모델에 함께 있는 조합이 없습니다."))
        for c in comparisons:
            name = f"{c['product_type']} × {c['colour']}"
            if "error" in c:
                items.append(_item("rank", f"순위 비교 · {name}", "info", f"비교하지 못했습니다: {c['error']}"))
            else:
                level = "warn" if not c["overlap"] >= OVERLAP_WARN else "ok"
                items.append(_item("rank", f"순위 비교 · {name}", level,
                                   f"상위 {c['k']:,}명 중 {c['overlap']:.1%}가 현재 모델과 같습니다"
                                   + (f" · 경고 기준 {OVERLAP_WARN:.0%} 미만" if level == "warn" else "")))
    elif current is not None:
        items.append(_item("rank", "대표 조합 순위 비교", "info", "데이터 연결 없이 점검해 순위 비교를 건너뛰었습니다."))

    return {"can_apply": not any(i["level"] == "block" for i in items), "items": items,
            "candidate": _summary(cand), "current": _summary(current) if current is not None else None,
            "rank_comparisons": comparisons}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m admin_dashboard.retrain_check")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--models-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--parquet-dir", default=None, help="테스트용. 지정하면 팀 연결 대신 이 폴더의 Parquet을 읽음")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from modeling.purchase_model.data import connect_hm, connect_parquet

    code = 0
    try:
        with (connect_parquet(args.parquet_dir) if args.parquet_dir else connect_hm()) as data:
            result = run_checks(args.candidate, args.models_dir, data, top_k=args.top_k)
    except Exception as exc:
        code = 1
        result = {"can_apply": False, "error": f"{type(exc).__name__}: {exc}",
                  "items": [_item("check", "자동 점검 실행", "block", f"{type(exc).__name__}: {exc}")]}
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
