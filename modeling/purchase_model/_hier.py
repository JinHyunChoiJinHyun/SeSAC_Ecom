"""구매 예측 모델(계층형 로지스틱 회귀) 수치 계산 — 목적함수·기울기·Schur 뉴턴법

점수   s_ci = a_c + x_ci · β_c,   β_c = β0 + Γ z_c + u_c
목적   J = Σ_i w_i·logloss(y_i, s_i) + (λ/2) Σ_c ||u_c||² + (1/2)(||β0||² + ||Γ||²)
       (w = 원래 사례 수, 손실은 사례 수 기준 합. 절편 a_c에는 벌점 없음)
풀이   뉴턴법. 헤시안을 조합 블록 (a_c, u_c)와 공유 블록 (β0, Γ)로 나눠 Schur 보수로 푼다(역행렬 미계산).
종료   뉴턴 감소량 gᵀH⁻¹g/2 ≤ dec_tol (원래 J 기준), Armijo 선 탐색(c=1e-4, 최대 60회 반감)

저장소 밖 실험(pooled_v2/hier_lr.py, hier_schur.py)에서 조밀 뉴턴과 같은 해임을 확인한 계산을 옮겼다.
"""
from __future__ import annotations

import time

import numpy as np
from scipy.linalg import LinAlgError, cho_factor, cho_solve
from scipy.special import expit, logit


def row_loss(s, y):
    """로그손실의 안정적 계산: y=1 → log(1+e^{-s}), y=0 → log(1+e^{s})"""
    return np.where(y > 0.5, np.logaddexp(0.0, -s), np.logaddexp(0.0, s))


def row_resid(s, y):
    """∂ℓ/∂s = σ(s) − y 의 안정적 계산"""
    return np.where(y > 0.5, -expit(-s), expit(s))


class Problem:
    def __init__(self, g, X, y, w, Z, lam):
        self.g = np.asarray(g, np.int64)
        self.X = np.asarray(X, np.float64)
        self.y = np.asarray(y, np.float64)
        self.w = np.asarray(w, np.float64)
        if not np.isin(self.y, (0.0, 1.0)).all():
            raise ValueError("y는 0/1이어야 합니다")
        Z = np.asarray(Z, np.float64)
        self.Z = Z.reshape(Z.shape[0], -1)
        self.C, self.p, self.q = self.Z.shape[0], self.X.shape[1], self.Z.shape[1]
        self.lam = float(lam)
        self.W = self.w.sum()
        self.Wc = np.bincount(self.g, self.w, self.C)
        if (self.Wc <= 0).any():
            raise ValueError("사례가 없는 조합이 있습니다")
        pos = np.bincount(self.g, self.w * self.y, self.C)
        if ((pos <= 0) | (pos >= self.Wc)).any():
            raise ValueError("양성 또는 음성이 0인 조합이 있습니다 (벌점 없는 절편의 유한 최적값 없음)")
        self.n = self.C + self.p + self.p * self.q + self.C * self.p

    def unpack(self, th):
        C, p, q = self.C, self.p, self.q
        return th[:C], th[C:C + p], th[C + p:C + p + p * q].reshape(p, q), th[C + p + p * q:].reshape(C, p)

    def pack(self, a, b0, G, U):
        return np.concatenate([a, b0, G.ravel(), U.ravel()])

    def betas(self, b0, G, U):
        return b0[None, :] + self.Z @ G.T + U

    def scores(self, th):
        a, b0, G, U = self.unpack(th)
        return a[self.g] + np.einsum("ij,ij->i", self.X, self.betas(b0, G, U)[self.g])

    def value_grad(self, th):
        a, b0, G, U = self.unpack(th)
        s = a[self.g] + np.einsum("ij,ij->i", self.X, self.betas(b0, G, U)[self.g])
        loss = float(np.sum(self.w * row_loss(s, self.y)))
        r = self.w * row_resid(s, self.y)
        ga = np.bincount(self.g, r, self.C)
        GB = np.column_stack([np.bincount(self.g, r * self.X[:, j], self.C) for j in range(self.p)])
        gb0 = GB.sum(0) + b0
        gG = GB.T @ self.Z + G
        gU = GB + self.lam * U
        J = loss + 0.5 * self.lam * np.sum(U * U) + 0.5 * (np.sum(b0 * b0) + np.sum(G * G))
        return J, self.pack(ga, gb0, gG, gU)


def init_theta(P: Problem) -> np.ndarray:
    """시작값: 절편 = logit(조합 학습 구매율), 나머지 0"""
    pos = np.bincount(P.g, P.w * P.y, P.C)
    th = np.zeros(P.n)
    th[:P.C] = logit(pos / P.Wc)
    return th


def _rel_grad(P: Problem, grad) -> tuple[float, float]:
    ga, gb0, gG, gU = P.unpack(grad)
    blk = np.column_stack([ga, gU]) ** 2
    return float((np.sqrt(blk.sum(1)) / P.Wc).max()), float(np.sqrt(np.sum(gb0 ** 2) + np.sum(gG ** 2)) / P.W)


def _moments(P: Problem, th):
    s = P.scores(th)
    v = P.w * expit(s) * expit(-s)
    C, p = P.C, P.p
    M = np.empty((C, p + 1, p + 1))
    M[:, 0, 0] = np.bincount(P.g, v, C)
    for j in range(p):
        M[:, 0, j + 1] = M[:, j + 1, 0] = np.bincount(P.g, v * P.X[:, j], C)
        for k in range(j, p):
            M[:, j + 1, k + 1] = M[:, k + 1, j + 1] = np.bincount(P.g, v * P.X[:, j] * P.X[:, k], C)
    return M


def _dmaps(P: Problem):
    C, p, q = P.C, P.p, P.q
    D = np.zeros((C, p, p + p * q))
    for j in range(p):
        D[:, j, j] = 1.0
        D[:, j, p + j * q: p + (j + 1) * q] = P.Z
    return D


def _blocks(P: Problem, th, D):
    M = _moments(P, th)
    Hcc = M.copy()
    Hcc[:, 1:, 1:] += P.lam * np.eye(P.p)
    Hcs = np.einsum("cij,cjk->cik", M[:, :, 1:], D)
    Hss = np.einsum("cji,cjl,clk->ik", D, M[:, 1:, 1:], D) + np.eye(D.shape[2])
    return Hcc, Hcs, Hss


def _schur_solve(P: Problem, g, Hcc, Hcs, Hss):
    a, b0, G, U = P.unpack(g)
    gc, gs = np.column_stack([a, U]), np.concatenate([b0, G.reshape(-1)])
    sol = np.linalg.solve(Hcc, np.concatenate([gc[:, :, None], Hcs], axis=2))
    yc, Yc = sol[:, :, 0], sol[:, :, 1:]
    S = Hss - np.einsum("cis,cit->st", Hcs, Yc)
    r = gs - np.einsum("cis,ci->s", Hcs, yc)
    jit = 0
    try:
        cf = cho_factor(S, lower=True)
    except LinAlgError:
        jit = 1
        cf = cho_factor(S + 1e-10 * np.abs(np.diag(S)).max() * np.eye(len(S)), lower=True)
    ds = cho_solve(cf, r)
    dc = yc - np.einsum("cis,s->ci", Yc, ds)
    p, q = P.p, P.q
    return P.pack(dc[:, 0], ds[:p], ds[p:].reshape(p, q), dc[:, 1:]), jit


def fit(P: Problem, theta0=None, max_iter: int = 100, dec_tol: float = 1e-9) -> dict:
    t0 = time.perf_counter()
    D = _dmaps(P)
    th = init_theta(P) if theta0 is None else np.asarray(theta0, np.float64).copy()
    J, g = P.value_grad(th)
    hist, status, jitter = [], "max_iter", 0
    for it in range(max_iter + 1):
        step, jit = _schur_solve(P, g, *_blocks(P, th, D))
        jitter += jit
        dec = float(g @ step) / 2.0
        cmax, sh = _rel_grad(P, g)
        rec = dict(iter=it, J=J, newton_decrement=dec, combo_rel_grad=cmax, shared_rel_grad=sh)
        if dec <= dec_tol:
            status = "decrement<=tol"; hist.append(rec); break
        if it == max_iter:
            hist.append(rec); break
        t, ok = 1.0, False
        for _ in range(60):
            Jt, gt = P.value_grad(th - t * step)
            if Jt <= J - 1e-4 * t * 2 * dec:
                ok = True; break
            t *= 0.5
        rec.update(t=t, actual_decrease=(J - Jt) if ok else 0.0)
        hist.append(rec)
        if not ok:
            status = "line_search_no_decrease"; break
        th, J, g = th - t * step, Jt, gt
    a, b0, G, U = P.unpack(th)
    return dict(theta=th, a=a.copy(), b0=b0.copy(), G=G.copy(), U=U.copy(), J=J, status=status, iters=hist[-1]["iter"],
                history=hist, cholesky_jitter_used=jitter, seconds=time.perf_counter() - t0)
