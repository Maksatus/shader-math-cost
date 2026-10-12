"""Minimax (L-inf) coefficient fitting and GPU-like error evaluation.

minimax(): coefficients c minimizing max |sum c_i * basis_i(x) - f(x)| over sample
points, solved exactly as a linear program.
horner_err(): max abs error of an odd polynomial x * P(x^2) evaluated by Horner with
rounding to float32 / float16 after every operation; fma=True rounds a*b+c once.
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


def mad(a, b, c, dt, fma=False):
    """a*b+c in type dt (arrays a, b of type dt): two roundings, or one with fma
    (the float64 product of float32/16 values is exact)."""
    if fma:
        return (a.astype(np.float64) * b + np.float64(dt(c))).astype(dt)
    return ((a * b).astype(dt) + dt(c)).astype(dt)


def eval_odd(c, x, dt, fma=False):
    x = x.astype(dt)
    x2 = (x * x).astype(dt)
    p = np.full_like(x2, c[-1])
    for ck in c[-2::-1]:
        p = mad(p, x2, ck, dt, fma)
    return (p * x).astype(dt)


def horner_err(f, c, lo, hi, dt, n=200001):
    x = np.linspace(lo, hi, n).astype(dt)
    ref = f(x.astype(np.float64))
    return float(np.max(np.abs(eval_odd(c, x, dt).astype(np.float64) - ref)))


def all_halfs(lo, hi):
    h = np.arange(65536, dtype=np.uint16).view(np.float16)
    h = h[np.isfinite(h)]
    return np.unique(h[(h >= lo) & (h <= hi)])
