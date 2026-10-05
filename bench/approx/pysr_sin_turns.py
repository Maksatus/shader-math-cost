"""PySR search for sin(2*pi*t) for any t (phase in turns), with round / frac / abs
available, to see whether it finds range reduction by itself.
Prints the Pareto front (complexity, max abs error on t in [-3, 3], expression).
"""
import numpy as np
import sympy
from pysr import PySRRegressor

t = np.linspace(-3, 3, 3001)
y = np.sin(2 * np.pi * t)

model = PySRRegressor(
    niterations=400,
    binary_operators=["+", "-", "*"],
    unary_operators=["round", "frac(x) = x - floor(x)", "abs"],
    extra_sympy_mappings={"frac": lambda x: x - sympy.floor(x), "round": sympy.Function("round")},
    maxsize=30,
    populations=32,
    parallelism="multithreading",
    progress=False,
    verbosity=0,
    temp_equation_file=True,
)
model.fit(t.reshape(-1, 1), y, variable_names=["t"])

for _, r in model.equations_.iterrows():
    err = np.max(np.abs(model.predict(t.reshape(-1, 1), index=_) - y))
    print(f"{r['complexity']:3d}  maxerr={err:.2e}  {r['equation']}")
