"""Candidates for moving to the vertex shader (plan A3.4).

A value of the fragment shader that is an affine function of varyings with coefficients from uniforms (material
properties, time) can be computed exactly in the vertex shader and interpolated instead: interpolation is a
weighted average with weights summing to 1, so it commutes with an affine map. Tiling/Offset (uv * _ST.xy + _ST.zw),
Rotate with an angle from a property (sin/cos of a uniform times uv), Flipbook (uv scaled and offset by a tile index
from time) are such values; anything that goes through a texture, a non-linear function of a varying, a product of
two varyings or gl_FragCoord is not.

Every statement of main() at the top level is classified in order, per vector component:
  C constant  <  U uniform only  <  A affine in varyings  <  X anything else
Statements inside if / loops are X (their value depends on the control flow). A candidate is an A statement whose
value is read by something that is not A (the frontier of an affine chain): it would become a new interpolator. How
much it saves comes from the ablation of the same statement (project/ablation.py), which replaces it with a new
varying — just what the move does in the fragment shader. Only a list: nothing is rewritten.

  candidates(src, parsed=None) -> [{"id", "line", "text", "chain" (ids), "width", "label", "varyings"}]
"""
import re

from paretogpu.project import ablation

C, U, A, X = 0, 1, 2, 3
KIND_NAME = {C: "const", U: "uniform", A: "affine", X: "other"}
TOKEN = re.compile(r"\s*(?:(\d+\.\d*(?:[eE][-+]?\d+)?[uUfF]?|\.\d+(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?[uU]?)"
                   r"|([A-Za-z_]\w*)|(<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%()\[\],.?:!<>&|^~]))")
CONSTRUCTORS = re.compile(r"^(float|int|uint|bool|[iub]?vec[234]|mat[234](x[234])?)$")
LINEAR_1 = {"float", "vec2", "vec3", "vec4"}
NONLINEAR_OK_ON_UNIFORM = True  # any function of uniforms only is uniform
UNIFORM_DECL = re.compile(r"^\s*(?:UNITY_LOCATION\(\d+\)\s*)?uniform\s+(?:(?:highp|mediump|lowp)\s+)?\w+\s+(\w+)")
BLOCK_MEMBER = re.compile(r"^\s*(?:(?:highp|mediump|lowp)\s+)?(?:float|int|uint|bool|[iub]?vec[234]|mat[234])\s+(\w+)")
TIME = re.compile(r"_Time|_TimeParameters|_SinTime|_CosTime")


def uniforms_of(lines, first):
    """Names of uniforms (loose ones and the members of uniform blocks) declared before main()."""
    out, in_block = set(), False
    for line in lines[:first]:
        t = line.strip()
        if in_block:
            if t.startswith("}"):
                in_block = False
                continue
            m = BLOCK_MEMBER.match(line)
            if m:
                out.add(m.group(1))
            continue
        if re.match(r"^\s*(?:UNITY_BINDING\(\d+\)|layout\s*\([^)]*\))?\s*uniform\s+\w+\s*\{?\s*$", line) or (
                "uniform" in line and t.endswith("{")):
            in_block = True
            continue
        m = UNIFORM_DECL.match(line)
        if m:
            out.add(m.group(1))
    return out


class _Expr:
    """Recursive descent over a GLSL expression; returns its kind (C, U, A, X) and the varyings it reads."""

    def __init__(self, text, env, varyings, uniforms):
        self.toks = []
        pos = 0
        while pos < len(text):
            m = TOKEN.match(text, pos)
            if not m or m.end() == pos:
                if text[pos:].strip():
                    raise ValueError(f"cannot read {text[pos:pos + 20]!r}")
                break
            self.toks.append(("num", m.group(1)) if m.group(1) else ("id", m.group(2)) if m.group(2) else ("op", m.group(3)))
            pos = m.end()
        self.i, self.env, self.varyings, self.uniforms, self.reads = 0, env, varyings, uniforms, set()

    def peek(self, v=None):
        t = self.toks[self.i] if self.i < len(self.toks) else (None, None)
        return t if v is None else t[1] == v

    def take(self, v=None):
        t = self.toks[self.i]
        if v is not None and t[1] != v:
            raise ValueError(f"expected {v}, got {t[1]}")
        self.i += 1
        return t

    def parse(self):
        k = self.ternary()
        if self.i != len(self.toks):
            raise ValueError("trailing tokens")
        return k

    def ternary(self):
        c = self.binary(0)
        if self.peek("?"):
            self.take("?")
            a = self.ternary()
            self.take(":")
            b = self.ternary()
            return max(a, b, U) if c <= U and max(a, b) <= A else X
        return c

    LEVELS = [("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="), ("<", ">", "<=", ">="), ("<<", ">>"),
              ("+", "-"), ("*", "/", "%")]

    def binary(self, level):
        if level == len(self.LEVELS):
            return self.unary()
        a = self.binary(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in self.LEVELS[level]:
            op = self.take()[1]
            b = self.binary(level + 1)
            a = self.combine(op, a, b)
        return a

    @staticmethod
    def combine(op, a, b):
        if X in (a, b):
            return X
        if op in ("+", "-"):
            return max(a, b)
        if op == "*":
            return X if a == A and b == A else max(a, b)
        if op == "/":
            return X if b == A else max(a, b)
        return max(a, b, U) if max(a, b) <= U else X  # comparisons, logic, bits, %: not affine

    def unary(self):
        if self.peek()[0] == "op" and self.peek()[1] in ("-", "+"):
            self.take()
            return self.unary()
        if self.peek()[0] == "op" and self.peek()[1] in ("!", "~"):
            self.take()
            k = self.unary()
            return k if k <= U else X
        return self.postfix()

    def postfix(self):
        k, name = self.primary()
        while self.i < len(self.toks):
            if self.peek("."):
                self.take(".")
                field = self.take()[1]
                if name is not None and name in self.env:  # a component of a temporary
                    k = max((self.env[name].get(ablation.COMP.get(c, 0), X) for c in field), default=X)
                name = None
            elif self.peek("["):
                self.take("[")
                idx = self.ternary()
                self.take("]")
                if name is not None and name in self.env:
                    k = max(self.env[name].values(), default=X)
                k = k if idx <= U else X
                name = None
            else:
                break
        if name is not None and name in self.env:
            k = max(self.env[name].values(), default=X)
        return k

    def primary(self):
        kind, v = self.take()
        if kind == "num":
            return C, None
        if kind == "op":
            if v != "(":
                raise ValueError(f"unexpected {v}")
            k = self.ternary()
            self.take(")")
            return k, None
        if self.peek("("):
            return self.call(v), None
        if v in ("gl_FragCoord", "gl_FrontFacing", "gl_PointCoord"):
            return X, None
        if v in self.varyings:
            self.reads.add(v)
            return A, None
        if v in self.uniforms:
            return U, None
        if v in self.env:
            return X, v  # resolved per component by postfix()
        if v in ("true", "false"):
            return C, None
        return X, None  # unknown: a local we did not track, a helper's name

    def call(self, fn):
        self.take("(")
        args = []
        if not self.peek(")"):
            args.append(self.ternary())
            while self.peek(","):
                self.take(",")
                args.append(self.ternary())
        self.take(")")
        top = max(args, default=C)
        if top == X:
            return X
        if fn in LINEAR_1:
            return top
        if CONSTRUCTORS.match(fn):  # int(), bool(), mat: truncation or not tracked
            return top if top <= U else X
        if fn == "dot" and len(args) == 2:
            return A if sorted(args)[1] == A and sorted(args)[0] <= U else (top if top <= U else X)
        if fn == "mix" and len(args) == 3:
            return max(args[0], args[1], U) if args[2] <= U else X
        if fn == "fma" and len(args) == 3:
            return self.combine("+", self.combine("*", args[0], args[1]), args[2])
        return top if top <= U else X


def _label(texts, uniforms_read):
    """What kind of affine value it is, by the functions and uniforms of its chain."""
    joined = " ".join(texts)
    if re.search(r"\b(sin|cos)\s*\(", joined):
        return "поворот (Rotate с углом из свойства)"
    if re.search(r"\b(floor|fract|mod|trunc)\s*\(", joined) and TIME.search(joined):
        return "флипбук / анимация по времени"
    if TIME.search(joined):
        return "смещение по времени"
    if re.search(r"_ST\b|_ST\.", joined):
        return "Tiling / Offset"
    if not re.search(r"\b(?!float\b|[iu]?vec[234]\b)[a-z]\w*\s*\(", joined):
        return "масштаб и сдвиг uv (смещения выборок)"
    return "аффинное преобразование varying"


def candidates(src, parsed=None):
    """The frontier statements of affine chains of the fragment shader's main() (see the module doc)."""
    p = parsed or ablation.parse(src, "fragment")
    lines, first = p["lines"], p["main"][0]
    uniforms = uniforms_of(lines, first)
    varyings = {n for n, v in p["vars"].items() if v["io"] == "in"}
    env, kinds = {}, {}
    for s in p["statements"]:
        if s["type"] != "assign":
            continue
        if s["depth"] > 0:
            k = X
        else:
            try:
                e = _Expr(s["expr"], env, varyings, uniforms)
                k = e.parse()
                s["reads_varyings"] = sorted(e.reads)
            except (ValueError, IndexError):
                k = X
        kinds[s["id"]] = k
        env.setdefault(s["var"], {})
        for c in s["comps"]:
            env[s["var"]][c] = k
    _, users = ablation.tree(p)
    sources = {}  # statement -> the statements whose values it reads
    for d, us in users.items():
        for u in us:
            sources.setdefault(u, set()).add(d)
    st = {s["id"]: s for s in p["statements"]}
    out = []
    for sid, k in kinds.items():
        s = st[sid]
        if k != A or s.get("output"):
            continue
        us = users.get(sid, set())
        if us and all(u != ablation.END and kinds.get(u) == A for u in us):
            continue  # inside a chain: its end is the candidate
        chain, todo = set(), [sid]
        while todo:  # everything it is computed from: affine, uniform and constant statements (redone in the vertex)
            x = todo.pop()
            if x not in chain:
                chain.add(x)
                todo += [d for d in sources.get(x, ()) if kinds.get(d) in (A, U, C)]
        texts = [st[x]["text"] for x in chain]
        work = re.sub(r"\b(float|[iu]?vec[234])\s*\(", "(", " ".join(st[x]["expr"] for x in chain))
        if not re.search(r"[*/]|\b[a-z]\w*\s*\(", work):
            continue  # a copy or a shift of a varying: cheaper than one more interpolated varying
        out.append({"id": sid, "line": s["line"], "text": s["text"], "chain": sorted(chain, key=lambda x: st[x]["line"]),
                    "width": len(s["comps"]), "label": _label(texts, None),
                    "varyings": sorted({v for x in chain for v in st[x].get("reads_varyings", [])})})
    return sorted(out, key=lambda c: c["line"])


def budget(src, parsed=None):
    """Varyings of the fragment shader: how many vec4 slots are used and how many components are free in them."""
    p = parsed or ablation.parse(src, "fragment")
    ins = [v for v in p["vars"].values() if v["io"] == "in"]
    return {"varyings": len(ins), "free_components": sum(4 - v["width"] for v in ins if v["kind"] == "f")}
