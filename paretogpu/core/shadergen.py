"""Shader source generator for the math-cost benchmark.

Each test shader runs a dependency chain of N steps:
    x = <expr(x, a, b, c)> + w_i
Operands a/b/c/w come from varyings (not uniforms): the Mali compiler folds
chains of uniform-only operands into a single op (uniform computation hoisting).

Float vector/scalar shaders must stay byte-identical between versions (results
are cached by source text), so int/matrix/helper additions are emitted only
when the shader needs them.
"""

N_POOL_VARYINGS = 8          # vP0..vP7 -> 32 distinct operands per type
N_INT_VARYINGS = 4           # vI0..vI3 (flat) -> 16 distinct int operands

GLSL_DIM = {"float": 1, "vec2": 2, "vec3": 3, "vec4": 4,
            "int": 1, "ivec2": 2, "ivec4": 4, "uint": 1, "uvec2": 2, "uvec4": 4}
MAT_DIM = {"mat2": 2, "mat3": 3, "mat4": 4}
VEC_OF = {1: "float", 2: "vec2", 3: "vec3", 4: "vec4"}
SWZ = ["xyzw", "yzwx", "zwxy", "wxyz"]
SAMPLERS = {"uT2": "sampler2D", "uT3": "sampler3D", "uTC": "samplerCube",
            "uTA": "sampler2DArray", "uTS": "sampler2DShadow"}


def is_int(t):
    return t.startswith(("int", "ivec", "uint", "uvec"))


def pool(k, t):
    """k-th distinct varying operand of GLSL type t."""
    if t in MAT_DIM:
        d = MAT_DIM[t]
        return f"{t}(" + ", ".join(pool(k + j * 5, VEC_OF[d]) for j in range(d)) + ")"
    n = GLSL_DIM[t]
    if is_int(t):
        k %= N_INT_VARYINGS * 4
        v, r = k % N_INT_VARYINGS, k // N_INT_VARYINGS
        e = f"vI{v}.{'xyzw'[r]}" if n == 1 else f"vI{v}.{SWZ[r][:n]}"
        return e if t.startswith(("int", "ivec")) else f"{t}({e})"
    k %= N_POOL_VARYINGS * 4
    v, r = k % N_POOL_VARYINGS, k // N_POOL_VARYINGS
    if n == 1:
        return f"vP{v}.{'xyzw'[r]}"
    return f"vP{v}.{SWZ[r][:n]}"


def header(api, prec, t="float", need_m2=False, samplers=()):
    s = "#version 310 es\n" if api == "gles" else "#version 450\n"
    s += f"precision {prec} float;\n"
    if is_int(t):
        s += f"precision {prec} int;\n"
    bind = "binding=0" if api == "gles" else "set=0, binding=0"
    s += f"layout(std140, {bind}) uniform U {{ {prec} mat4 uM4; {prec} mat3 uM3; }};\n"
    if need_m2:
        bind1 = "binding=1" if api == "gles" else "set=0, binding=1"
        s += f"layout(std140, {bind1}) uniform U2 {{ {prec} mat2 uM2; }};\n"
    for i, name in enumerate(samplers):
        b = f"binding={2 + i}" if api == "gles" else f"set=0, binding={2 + i}"
        s += f"layout({b}) uniform {prec} {SAMPLERS[name]} {name};\n"
    s += f"layout(location=0) in {prec} vec4 vX;\n"
    for i in range(N_POOL_VARYINGS):
        s += f"layout(location={i+1}) in {prec} vec4 vP{i};\n"
    if is_int(t):
        for i in range(N_INT_VARYINGS):
            s += f"layout(location={N_POOL_VARYINGS + 1 + i}) flat in {prec} ivec4 vI{i};\n"
    s += f"layout(location=0) out {prec} vec4 o;\n"
    return s


def out_expr(t):
    if t in MAT_DIM:
        d = MAT_DIM[t]
        col = " + ".join(f"x[{j}]" for j in range(d))
        return {2: f"vec4({col}, 0.0, 1.0)", 3: f"vec4({col}, 1.0)", 4: col}[d]
    if is_int(t):
        n = GLSL_DIM[t]
        return {1: "vec4(float(x))", 2: f"vec4(vec2(x), 0.0, 1.0)", 4: "vec4(x)"}[n]
    return {"float": "vec4(x)", "vec2": "vec4(x, 0.0, 1.0)",
            "vec3": "vec4(x, 1.0)", "vec4": "x"}[t]


def init_expr(t):
    if t in MAT_DIM:
        d = MAT_DIM[t]
        return {2: "mat2(vX.xy, vX.zw)", 3: "mat3(vX.xyz, vX.yzw, vX.zwx)",
                4: "mat4(vX, vX.yzwx, vX.zwxy, vX.wxyz)"}[d]
    if is_int(t):
        n = GLSL_DIM[t]
        return f"{t}(" + {1: "vX.x", 2: "vX.xy", 4: "vX"}[n] + ")"
    return {"float": "vX.x", "vec2": "vX.xy", "vec3": "vX.xyz", "vec4": "vX"}[t]


def build(api, prec, t, step_expr, n, combine=True, prelude=""):
    """step_expr: format string with {x},{a},{b},{s} ({s} = scalar operand).
    combine: append '+ w_i' after each step (breaks idempotent folding).
    prelude: helper functions placed before main()."""
    s = header(api, prec, t, need_m2="uM2" in step_expr,
               samplers=[n for n in SAMPLERS if n in step_expr])
    s += prelude
    s += "void main() {\n"
    s += f"  {prec} {t} x = {init_expr(t)};\n"
    sc = "int" if is_int(t) else "float"
    for i in range(n):
        e = step_expr.format(x="x", a=pool(i + 1, t), b=pool(i + 7, t),
                             s=pool(i + 13, sc), T=t)
        if combine:
            e = f"({e}) + {pool(i, t)}"
        s += f"  x = {e};\n"
    s += f"  o = {out_expr(t)};\n"
    s += "}\n"
    return s
