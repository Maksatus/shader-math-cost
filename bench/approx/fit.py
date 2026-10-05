"""Minimax (L-inf) coefficient fitting and GPU-like error evaluation.

minimax(): coefficients c minimizing max |sum c_i * basis_i(x) - f(x)| over sample
points, solved exactly as a linear program.
horner_err(): max abs error of an odd polynomial x * P(x^2) evaluated by Horner with
rounding to float32 / float16 after every operation (fma = one rounding is ignored,
which only makes the estimate slightly pessimistic).
"""
import numpy as np
from scipy.optimize import linprog


def minimax(f, basis, x):
    A = np.stack([b(x) for b in basis], axis=1)
    y = f(x)
    n = A.shape[1]
    # variables: c (n), t; minimize t; A c - y <= t, y - A c <= t
    ones = np.ones((len(x), 1))
    A_ub = np.vstack([np.hstack([A, -ones]), np.hstack([-A, -ones])])
    b_ub = np.concatenate([y, -y])
    res = linprog(np.r_[np.zeros(n), 1.0], A_ub=A_ub, b_ub=b_ub,
                  bounds=[(None, None)] * n + [(0, None)], method="highs")
    return res.x[:n], res.x[n]


def odd_poly(f, lo, hi, terms, n=20001):
    """f ~ x * (c0 + c1 x^2 + c2 x^4 + ...), 'terms' coefficients."""
    x = np.linspace(lo, hi, n)
    basis = [lambda x, k=k: x ** (2 * k + 1) for k in range(terms)]
    return minimax(f, basis, x)


def eval_odd(c, x, dt):
    x = x.astype(dt)
    x2 = (x * x).astype(dt)
    p = dt(c[-1])
    for ck in c[-2::-1]:
        p = (p * x2 + dt(ck)).astype(dt)
    return (p * x).astype(dt)


def horner_err(f, c, lo, hi, dt, n=200001):
    x = np.linspace(lo, hi, n).astype(dt)
    ref = f(x.astype(np.float64))
    return float(np.max(np.abs(eval_odd(c, x, dt).astype(np.float64) - ref)))


def all_halfs(lo, hi):
    h = np.arange(65536, dtype=np.uint16).view(np.float16)
    h = h[np.isfinite(h)]
    return np.unique(h[(h >= lo) & (h <= hi)])
