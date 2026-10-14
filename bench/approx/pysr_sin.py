"""PySR search for sin(x) on [-pi, pi] with FMA-pipe operators only (builtin sin is SFU-bound on Valhall / 5th Gen).

Usage: python pysr_sin.py   (prints the Pareto front)
"""
import numpy as np
from pysr import PySRRegressor

x = np.linspace(-np.pi, np.pi, 2001)
y = np.sin(x)

model = PySRRegressor(
    niterations=200,
    binary_operators=["+", "-", "*"],
    unary_operators=[],
    maxsize=25,
    populations=32,
    procs=0,
    parallelism="multithreading",
    deterministic=False,
    random_state=0,
    progress=False,
    verbosity=0,
    temp_equation_file=True,
)
model.fit(x.reshape(-1, 1), y, variable_names=["x"])

for _, r in model.equations_.iterrows():
    f = r["lambda_format"]
    err = np.max(np.abs(f(x.reshape(-1, 1)) - y))
    print(f"{r['complexity']:3d}  maxerr={err:.2e}  {r['equation']}")
