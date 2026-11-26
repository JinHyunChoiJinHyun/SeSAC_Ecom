"""구매 예측 모델 — 기준일별 하나의 계층형 로지스틱 회귀로 학습 가능한 모든 품목·색상의 고객 점수를 계산

모델   조합 c, 고객 입력 x(같은 품목 90일 구매 수(log1p), 마지막 구매 후 일수(log), 90일 구매일 수(log)):
       score_logit = a_c + x̃·β_c,  β_c = β0 + Γ z_c + u_c,  x̃ = (x − 평균) / 표준편차 (학습 집계의 사례 가중)
       z_c = [조합 학습 구매율(log, 표준화), 품목 학습 구매율(log, 표준화), 상품군 더미(학습 조합 15개 이상 상품군, 나머지 '기타')]
       상품군 = 그 품목×색상 상품 중 가장 많은 상품군(DuckDB MODE). 동점이면 MODE가 하나를 고르며 어느 쪽인지 보장되지 않음
       목적함수·풀이는 _hier.py (λ=10, 뉴턴 감소량 ≤ 1e-9, 최대 100회)
학습   as_of 기준 12개 학습 월요일(U+30일 ≤ as_of, U+30일 ≤ complete_through+1일, U−90일 ≥ 첫 거래일), 라벨 = [U, U+30)에 그 조합 구매.
       학습 대상 = Unknown이 아닌 조합 중 학습 양성 > 0 이고 음성 > 0 인 전체 조합
지원   점수는 학습에 포함된 조합만 낸다. 양성 0 조합·Unknown·카탈로그 밖 조합은 UnsupportedCombo
점수   prob_uncalibrated = σ(score_logit). 보정되지 않은 값이며, 순위는 score_logit 내림차순·동점 customer_id 오름차순
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit

from . import _hier
from .data import UNKNOWN_VALUES, HMData, combo_article_count, to_date
from .errors import FutureModel, ModelMismatch, ModelNotReady, NotConverged, SaveFailed, SingleClass, TargetingError, UnsupportedCombo
from .features import (FEATURES, HISTORY_DAYS, LABEL_DAYS, TRANSFORMS, FeatureSet, _check_window, _resolve_complete,
                       _transform, _validate_frame, _versions, build_features, training_mondays)

FORMAT_VERSION = "purchase-1"
MODELS_DIR = Path(__file__).resolve().parent / "models"
LAMBDA = 10.0
DEC_TOL = 1e-9
MAX_ITER = 100
GROUP_MIN_COMBOS = 15
ARRAY_KEYS = ("a", "b0", "G", "U", "Z", "x_mean", "x_sd")


@dataclass
class PurchaseModel:
    params: dict
    meta: dict
    path: Path | None = None

    @property
    def as_of(self) -> date:
        return date.fromisoformat(self.meta["as_of"])

    @property
    def run_id(self) -> str | None:
        return self.meta.get("run_id")

    def index(self) -> dict:
        if "_index" not in self.__dict__:
            self.__dict__["_index"] = {(p, c): i for i, (p, c) in enumerate(zip(self.params["product_type"], self.params["colour"]))}
        return self.__dict__["_index"]


def supported_combos(model: PurchaseModel) -> pd.DataFrame:
    """모델에 학습으로 포함된(점수를 낼 수 있는) 조합"""
    return pd.DataFrame({"product_type": model.params["product_type"], "colour": model.params["colour"],
                         "train_positives": model.params["train_positives"], "train_unique_buyers": model.params["train_unique_buyers"]})


# ---------------- 학습 데이터 (임시 테이블, 외부 연결은 닫지 않음) ----------------
class _Tables:
    def __init__(self, con):
        self.con = con
        self.p = f"_purchase_{uuid.uuid4().hex[:10]}_"
        self.made: list[str] = []

    def t(self, name: str) -> str:
        return self.p + name

    def create(self, name: str, sql: str, params=None) -> None:
        self.con.execute(f"CREATE TEMP TABLE {self.t(name)} AS {sql}", params) if params is not None else \
            self.con.execute(f"CREATE TEMP TABLE {self.t(name)} AS {sql}")
        self.made.append(self.t(name))

    def drop_all(self) -> None:
        for name in reversed(self.made):
            try:
                self.con.execute(f"DROP TABLE IF EXISTS {name}")
            except Exception:
                pass
        self.made.clear()


def _type_cells(T: _Tables, pt: str) -> pd.DataFrame:
    return T.con.execute(f"""SELECT COALESCE(tc.n, 0) cnt, c.days, c.pdays, COUNT(*) w FROM {T.t('common')} c
    LEFT JOIN {T.t('tcnt')} tc ON tc.u = c.u AND tc.cid = c.cid AND tc.pt = $pt GROUP BY ALL""", dict(pt=pt)).df()


def _type_pos(T: _Tables, pt: str) -> pd.DataFrame:
    return T.con.execute(f"""SELECT l.cg, COALESCE(tc.n, 0) cnt, c.days, c.pdays, COUNT(*) pos FROM {T.t('labc')} l
    JOIN {T.t('common')} c ON c.u = l.u AND c.cid = l.cid
    LEFT JOIN {T.t('tcnt')} tc ON tc.u = l.u AND tc.cid = l.cid AND tc.pt = $pt
    WHERE l.pt = $pt GROUP BY ALL""", dict(pt=pt)).df()


def _split(cells: pd.DataFrame, pos_c: pd.DataFrame) -> pd.DataFrame:
    """품목 셀 사례 수에서 조합 양성을 떼어 y=1 / y=0 행 (가중치 0 행 제거, 셀·y 순 정렬)"""
    m = cells.merge(pos_c[["cnt", "days", "pdays", "pos"]], on=["cnt", "days", "pdays"], how="left")
    m["pos"] = m["pos"].fillna(0).astype(np.int64)
    if (m.pos > m.w).any() or len(m) != len(cells) or int(m.pos.sum()) != int(pos_c.pos.sum()):
        raise TargetingError("학습 집계 검사 실패: 조합 양성 셀이 품목 셀과 맞지 않습니다.")
    y1 = m[m.pos > 0].assign(y=1, w=lambda d: d.pos)
    y0 = m[m.w - m.pos > 0].assign(y=0, w=lambda d: d.w - d.pos)
    out = pd.concat([y0, y1], ignore_index=True)[["cnt", "days", "pdays", "y", "w"]].astype(np.int64)
    return out.sort_values(["cnt", "days", "pdays", "y"]).reset_index(drop=True)


def _training_data(data: HMData, as_of: date, complete: date):
    mondays = training_mondays(as_of, data.data_start, complete)
    for u in mondays:
        assert u + timedelta(days=LABEL_DAYS) <= as_of and u + timedelta(days=LABEL_DAYS) <= complete + timedelta(days=1)
        assert u - timedelta(days=HISTORY_DAYS) >= data.data_start
    end = min(as_of, complete + timedelta(days=1))
    T = _Tables(data.con)
    try:
        T.create("tx", f"""SELECT DENSE_RANK() OVER (ORDER BY t.customer_id)::INTEGER cid, t.t_dat::DATE d,
            a.product_type_name pt, a.colour_group_name cg
            FROM hm.transactions t JOIN hm.articles a ON a.article_id = t.article_id
            WHERE t.t_dat >= $s AND t.t_dat < $e""", dict(s=mondays[0] - timedelta(days=HISTORY_DAYS), e=end))
        mx = T.con.execute(f"SELECT MAX(d) FROM {T.t('tx')}").fetchall()[0][0]
        if mx is not None and mx >= end:
            raise TargetingError(f"학습 테이블에 기준 범위 밖 거래가 있습니다: {mx} >= {end}")
        T.create("mon", "SELECT UNNEST($m::DATE[]) u", dict(m=mondays))
        T.create("common", f"""SELECT m.u, x.cid, (m.u - MAX(x.d))::INTEGER AS days, COUNT(DISTINCT x.d)::INTEGER AS pdays
            FROM {T.t('mon')} m JOIN {T.t('tx')} x ON x.d >= m.u - {HISTORY_DAYS} AND x.d < m.u GROUP BY m.u, x.cid""")
        T.create("labc", f"""SELECT DISTINCT m.u, x.cid, x.pt, x.cg FROM {T.t('mon')} m
            JOIN {T.t('tx')} x ON x.d >= m.u AND x.d < m.u + {LABEL_DAYS}
            JOIN {T.t('common')} c ON c.u = m.u AND c.cid = x.cid""")
        N = int(T.con.execute(f"SELECT COUNT(*) FROM {T.t('common')}").fetchall()[0][0])
        cpos = T.con.execute(f"SELECT pt, cg, COUNT(*) pos, COUNT(DISTINCT cid) ubuy FROM {T.t('labc')} GROUP BY 1, 2").df()
        tpos = T.con.execute(f"SELECT pt, COUNT(*) pos_t FROM (SELECT DISTINCT u, cid, pt FROM {T.t('labc')}) GROUP BY 1").df()
        art_cols = set(T.con.execute("SELECT * FROM hm.articles LIMIT 0").df().columns)
        pg_sql = "MODE(product_group_name)" if "product_group_name" in art_cols else "NULL::VARCHAR"
        art = T.con.execute(f"SELECT product_type_name pt, colour_group_name cg, {pg_sql} pg FROM hm.articles GROUP BY 1, 2").df()
        art["pg"] = art["pg"].fillna("기타")
        unknown = cpos.pt.isin(UNKNOWN_VALUES) | cpos.cg.isin(UNKNOWN_VALUES)
        tr = cpos[~unknown & (cpos.pos > 0) & (cpos.pos < N)].merge(art, on=["pt", "cg"], how="left")
        tr = tr.sort_values(["pt", "cg"]).reset_index(drop=True)
        if tr.empty:
            raise SingleClass("학습 양성·음성이 모두 있는 조합이 없습니다.")
        T.create("tcnt", f"""SELECT m.u, x.cid, x.pt, COUNT(*)::INTEGER n FROM {T.t('mon')} m
            JOIN {T.t('tx')} x ON x.d >= m.u - {HISTORY_DAYS} AND x.d < m.u
            WHERE x.pt IN (SELECT UNNEST($p::VARCHAR[])) GROUP BY ALL""", dict(p=sorted(tr.pt.unique())))
        parts = []
        for pt, idx in tr.groupby("pt", sort=False).groups.items():
            cells = _type_cells(T, pt)
            pos = _type_pos(T, pt)
            for i in idx:
                a = _split(cells, pos[pos.cg == tr.cg[i]])
                if int(a.w.sum()) != N or int(a.loc[a.y == 1, "w"].sum()) != int(tr.pos[i]):
                    raise TargetingError(f"학습 집계 검사 실패: {pt}×{tr.cg[i]}")
                parts.append(a.assign(combo=int(i)))
        agg = pd.concat(parts, ignore_index=True).sort_values("combo", kind="stable").reset_index(drop=True)
        info = dict(train_mondays=[m.isoformat() for m in mondays], N=N,
                    excluded=dict(catalog_combos=int(len(art)),
                                  unknown=int((art.pt.isin(UNKNOWN_VALUES) | art.cg.isin(UNKNOWN_VALUES)).sum()),
                                  no_positive_or_no_negative=int(len(art) - (art.pt.isin(UNKNOWN_VALUES) | art.cg.isin(UNKNOWN_VALUES)).sum() - len(tr))),
                    type_positives=tpos.set_index("pt").pos_t.to_dict())
        return tr, agg, info
    finally:
        T.drop_all()


def train_model(data: HMData, as_of, complete_through=None, lam: float = LAMBDA) -> PurchaseModel:
    as_of = to_date(as_of)
    complete, basis = _resolve_complete(data, complete_through)
    _check_window(data, as_of, complete, HISTORY_DAYS)
    tr, agg, info = _training_data(data, as_of, complete)
    N = info["N"]
    tr["grp"] = tr.pg.where(tr.pg.map(tr.pg.value_counts()) >= GROUP_MIN_COMBOS, "기타")
    levels = sorted(tr.grp.unique())
    tp = pd.Series(info["type_positives"])
    z1 = np.log((tr.pos.to_numpy(float) + 0.5) / (N + 1))
    z2 = np.log((tr.pt.map(tp).fillna(0).to_numpy(float) + 0.5) / (N + 1))
    zst = [[float(v.mean()), float(v.std()) if float(v.std()) > 0 else 1.0] for v in (z1, z2)]  # 표준편차 0(조합 1개 등)이면 1로 둠
    Z = np.column_stack([(z1 - zst[0][0]) / zst[0][1], (z2 - zst[1][0]) / zst[1][1]]
                        + [(tr.grp == lv).to_numpy(float) for lv in levels[1:]])
    X = _transform(agg.rename(columns={"cnt": FEATURES[0], "days": FEATURES[1], "pdays": FEATURES[2]}))
    w = agg.w.to_numpy(np.float64)
    mu = np.average(X, axis=0, weights=w)
    sd = np.sqrt(np.average((X - mu) ** 2, axis=0, weights=w))
    P = _hier.Problem(agg.combo.to_numpy(), (X - mu) / sd, agg.y.to_numpy(np.float64), w, Z, lam)
    del X
    f = _hier.fit(P, max_iter=MAX_ITER, dec_tol=DEC_TOL)
    if f["status"] != "decrement<=tol":
        raise NotConverged(f"구매 예측 모델 학습이 수렴하지 않았습니다: {f['status']} ({f['iters']}회, "
                           f"감소량 {f['history'][-1]['newton_decrement']:.3e})")
    params = dict(product_type=tr.pt.tolist(), colour=tr.cg.tolist(), product_group=tr.pg.tolist(), group_used=tr.grp.tolist(),
                  train_positives=tr.pos.astype(int).tolist(), train_unique_buyers=tr.ubuy.astype(int).tolist(),
                  a=f["a"], b0=f["b0"], G=f["G"], U=f["U"], Z=Z, x_mean=mu, x_sd=sd)
    last = f["history"][-1]
    meta = {
        "format_version": FORMAT_VERSION,
        "model": "purchase model (hierarchical logistic regression)",
        "as_of": as_of.isoformat(),
        "as_of_meaning": "예측 시작일. 학습에는 as_of 전날까지의 거래만 사용",
        "usable_for_score_dates": f"{as_of.isoformat()} 이후(포함) 기준일에만 사용 가능",
        "train_mondays": info["train_mondays"], "history_days": HISTORY_DAYS, "label_days": LABEL_DAYS,
        "label": f"[U, U+{LABEL_DAYS}일)에 그 품목×색상을 한 번 이상 구매하면 1",
        "data_source": data.source,
        "data_range": {"min_t_dat": data.data_start.isoformat(), "max_t_dat": data.data_end.isoformat()},
        "complete_through": complete.isoformat(), "complete_through_basis": basis,
        "counts": {"combos": int(len(tr)), "train_cases": N, "agg_rows": int(len(agg)), "excluded_combos": info["excluded"]},
        "support_scope": "학습에 포함된 조합만 점수를 낸다. 학습 양성 0 조합·Unknown·카탈로그 밖 조합은 지원하지 않음(자동 대체 없음)",
        "features": FEATURES, "transforms": TRANSFORMS,
        "x_standardize": "학습 집계의 사례 가중 평균·표준편차",
        "z": {"columns": ["z1 log((조합 양성+0.5)/(N+1)) 표준화", "z2 log((품목 양성+0.5)/(N+1)) 표준화"] + [f"상품군={lv}" for lv in levels[1:]],
              "standardize_mean_sd": zst, "group_levels": levels, "group_reference": levels[0],
              "group_rule": f"학습 조합 {GROUP_MIN_COMBOS}개 이상 상품군만 수준, 나머지 '기타'. 조합의 상품군은 상품 수 최다 상품군(MODE), 동점이면 선택 보장 없음"},
        "objective": "Σ w·logloss + (λ/2)Σ||u_c||² + ½(||β0||² + ||Γ||²), 절편 벌점 없음",
        "lambda": lam, "solver": "Schur 뉴턴 + Armijo", "stop": f"뉴턴 감소량 ≤ {DEC_TOL}, 최대 {MAX_ITER}회",
        "fit": {"status": f["status"], "iters": int(f["iters"]), "final_decrement": last["newton_decrement"], "J": f["J"],
                "seconds": round(f["seconds"], 2), "cholesky_jitter_used": f["cholesky_jitter_used"]},
        "n_params": int(P.n),
        "score_output": "score_logit = a_c + x̃·β_c, prob_uncalibrated = σ(score_logit) (보정되지 않은 값)",
        "rank_rule": "score_logit 내림차순, 동점은 customer_id 오름차순",
        "versions": _versions(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return PurchaseModel(params=params, meta=meta)


# ---------------- 저장·로딩·선택 ----------------
def _bundle(model: PurchaseModel) -> dict:
    out = {"format_version": FORMAT_VERSION, "features": list(FEATURES), "transforms": dict(TRANSFORMS), "as_of": model.meta["as_of"]}
    for k, v in model.params.items():
        out[k] = np.asarray(v, np.float64) if k in ARRAY_KEYS else list(v)
    return out


def _same(a: PurchaseModel, b: PurchaseModel) -> bool:
    for k in model_keys(a):
        x, y = a.params[k], b.params[k]
        if k in ARRAY_KEYS:
            if not (np.asarray(x).shape == np.asarray(y).shape and np.array_equal(x, y)):
                return False
        elif list(x) != list(y):
            return False
    return True


def model_keys(model: PurchaseModel) -> list[str]:
    return list(model.params)


def save_model(model: PurchaseModel, out_dir=MODELS_DIR, run_id: str | None = None) -> Path:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    base = Path(out_dir) / f"as_of={model.meta['as_of']}"
    final, tmp = base / run_id, base / f".tmp-{run_id}"
    if final.exists():
        raise SaveFailed(f"이미 존재하는 모델 폴더입니다: {final}")
    created = False
    try:
        base.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(exist_ok=False)
        created = True
        meta = dict(model.meta, run_id=run_id)
        joblib.dump(_bundle(model), tmp / "model.joblib")
        (tmp / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if not _same(load_model(tmp, check_run_id=False), model):
            raise SaveFailed("저장 후 다시 불러온 구매 예측 모델이 원래 모델과 다릅니다.")
        os.rename(tmp, final)
    except Exception as exc:
        if created:
            shutil.rmtree(tmp, ignore_errors=True)
        if isinstance(exc, TargetingError):
            raise
        raise SaveFailed(f"{type(exc).__name__}: {exc}") from exc
    model.meta["run_id"] = run_id
    model.path = final
    return final


def load_model(path, check_run_id: bool = True) -> PurchaseModel:
    path = Path(path)
    bundle = joblib.load(path / "model.joblib")
    meta = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    if bundle.get("format_version") != FORMAT_VERSION or meta.get("format_version") != FORMAT_VERSION:
        raise ModelMismatch(f"지원하지 않는 구매 예측 모델 형식입니다: {path}")
    if bundle["features"] != FEATURES or bundle["transforms"] != TRANSFORMS:
        raise ModelMismatch("구매 예측 모델의 변수 순서나 변환이 현재 코드와 다릅니다.")
    if bundle["as_of"] != meta["as_of"]:
        raise ModelMismatch("model.joblib와 metadata.json의 as_of가 다릅니다.")
    if check_run_id and not meta.get("run_id"):
        raise ModelMismatch(f"metadata.json에 run_id가 없습니다: {path}")
    params = {k: v for k, v in bundle.items() if k not in ("format_version", "features", "transforms", "as_of")}
    C = len(params["product_type"])
    if any(len(params[k]) != C for k in ("colour", "train_positives", "a", "U", "Z")) or meta["counts"]["combos"] != C:
        raise ModelMismatch(f"구매 예측 모델의 조합 수가 서로 다릅니다: {path}")
    return PurchaseModel(params=params, meta=meta, path=path)


def list_models(models_dir=MODELS_DIR) -> list[dict]:
    """저장된 구매 예측 모델 폴더 목록 (models_dir/as_of=YYYY-MM-DD/<run_id>/). 저장 중 임시 폴더(.tmp-)는 제외"""
    root = Path(models_dir)
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("as_of=")):
        try:
            as_of = date.fromisoformat(d.name[len("as_of="):])
        except ValueError:
            continue
        for r in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            out.append({"as_of": as_of.isoformat(), "run_id": r.name, "path": r})
    return out


def select_model(score_as_of, models_dir=MODELS_DIR) -> PurchaseModel:
    """score_as_of 이하 as_of 중 최신(같은 as_of면 최신 run_id) 구매 예측 모델. 폴더 이름과 모델 내용이 다르면 ModelMismatch"""
    score_as_of = to_date(score_as_of)
    entries = list_models(models_dir)
    if not entries:
        raise ModelNotReady(f"저장된 구매 예측 모델이 없습니다: {Path(models_dir)}. 먼저 학습해야 합니다.")
    usable = [e for e in entries if date.fromisoformat(e["as_of"]) <= score_as_of]
    if not usable:
        raise ModelNotReady(f"구매 예측 모델은 있지만 모두 기준일 {score_as_of}보다 늦은 as_of로 학습됐습니다: "
                            f"{sorted({e['as_of'] for e in entries})}")
    best = max(usable, key=lambda e: (e["as_of"], e["run_id"]))
    try:
        model = load_model(best["path"])
    except (OSError, ValueError, KeyError) as exc:
        raise ModelMismatch(f"구매 예측 모델 폴더를 읽을 수 없습니다: {best['path']} ({type(exc).__name__}: {exc})") from exc
    if (model.meta["as_of"], model.run_id) != (best["as_of"], best["run_id"]):
        raise ModelMismatch(f"폴더 이름과 모델 내용(as_of, run_id)이 다릅니다: {best['path']}")
    return model


def valid_score_dates(data: HMData, model: PurchaseModel, complete_through=None):
    complete, _ = _resolve_complete(data, complete_through)
    first = max(model.as_of, data.data_start + timedelta(days=HISTORY_DAYS))
    last = complete + timedelta(days=1)
    return (first, last) if first <= last else None


# ---------------- 점수 ----------------
def _empty_scores() -> pd.DataFrame:
    return pd.DataFrame({"customer_id": pd.Series([], dtype=str), "score_logit": pd.Series([], dtype=float),
                         "prob_uncalibrated": pd.Series([], dtype=float), "rank": pd.Series([], dtype=np.int64)})


def score_model(model: PurchaseModel, features: FeatureSet, colour: str) -> pd.DataFrame:
    if not isinstance(features, FeatureSet):
        raise TypeError("features는 FeatureSet이어야 합니다 (as_of와 product_type을 명시).")
    if to_date(features.as_of) < model.as_of:
        raise FutureModel(f"기준일 {features.as_of}보다 늦은 as_of({model.as_of})로 학습된 구매 예측 모델은 사용할 수 없습니다.")
    c = model.index().get((features.product_type, colour))
    if c is None:
        raise UnsupportedCombo(f"{features.product_type}×{colour}는 구매 예측 모델(as_of {model.as_of})의 학습에 포함되지 않았습니다.",
                               "not_in_model")
    frame = features.frame
    _validate_frame(frame)
    if frame.empty:
        return _empty_scores()
    p = model.params
    Xs = (_transform(frame) - p["x_mean"]) / p["x_sd"]
    beta = p["b0"] + p["Z"][c] @ p["G"].T + p["U"][c]
    logit = p["a"][c] + Xs @ beta
    cid = frame["customer_id"].astype(str).to_numpy()
    order = np.lexsort((cid, -logit))
    return pd.DataFrame({"customer_id": cid[order], "score_logit": logit[order], "prob_uncalibrated": expit(logit[order]),
                         "rank": np.arange(1, len(order) + 1, dtype=np.int64)})


@dataclass
class ComboScores:
    scores: pd.DataFrame       # customer_id, score_logit, prob_uncalibrated, rank
    excluded: pd.DataFrame     # customer_ids로 요청했지만 최근 90일 구매 이력이 없어 점수를 내지 않은 고객
    product_type: str
    colour: str
    score_as_of: date
    model_as_of: date
    model_run_id: str | None


def _check_request(data: HMData, product_type: str, colour: str) -> None:
    if product_type in UNKNOWN_VALUES or colour in UNKNOWN_VALUES:
        raise UnsupportedCombo(f"Unknown 속성으로는 점수를 낼 수 없습니다: {product_type}×{colour}", "unknown")
    if combo_article_count(data, product_type, colour) == 0:
        raise UnsupportedCombo(f"articles에 없는 조합입니다: {product_type}×{colour}", "not_in_catalog")


def score_combo(data: HMData, product_type: str, colour: str, score_as_of, customer_ids=None,
                complete_through=None, models_dir=MODELS_DIR) -> ComboScores:
    """품목·색상의 구매 예측 모델 고객 점수·순위. 지원하지 않는 조합은 UnsupportedCombo이며 다른 모델로 대체하지 않는다.

    customer_ids를 주면 기준일 입력을 만든 뒤 그 고객만 남기고, 순위는 남은 고객 안에서 매긴다.
    최근 90일 구매 이력이 없는 고객은 점수를 만들지 않고 excluded로 돌려준다.
    """
    _check_request(data, product_type, colour)
    score_as_of = to_date(score_as_of)
    model = select_model(score_as_of, models_dir)
    if (product_type, colour) not in model.index():
        raise UnsupportedCombo(f"{product_type}×{colour}는 구매 예측 모델(as_of {model.as_of})의 학습에 포함되지 않았습니다.",
                               "not_in_model")
    feats = build_features(data, score_as_of, product_type, customer_ids, complete_through)
    scores = score_model(model, feats, colour)
    return ComboScores(scores=scores, excluded=feats.excluded, product_type=product_type, colour=colour,
                       score_as_of=score_as_of, model_as_of=model.as_of, model_run_id=model.run_id)


def score_article(data: HMData, article_id: str, score_as_of, customer_ids=None, complete_through=None,
                  models_dir=MODELS_DIR) -> ComboScores:
    """상품 ID의 품목·색상으로 score_combo. 없는 상품 ID는 ArticleNotFound"""
    from .taste import target_from_article

    t = target_from_article(data, article_id)
    return score_combo(data, t["product_type"], t["colour"], score_as_of, customer_ids, complete_through, models_dir)
