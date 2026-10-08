"""Ablation (plan A3.2): what every statement of a shader costs.

A statement `x.xyz = <expression>;` of main() is ablated by putting a new varying (a vertex attribute in a vertex
shader) of the same type in place of the expression: the compiler then drops the expression and everything that
was computed only for it. The shader is measured again with malioc; the drop of every pipe is the statement's
inclusive cost ("with its tail"). Dynamic loops are forced to n iterations (frame/loops.py) in the base and in every
ablated copy, so statements in loops are priced at n.

Which statements feed only one other statement comes from a def-use analysis of main() per vector component
(reaching definitions over if / else and loops, conservative); P is a child of L when every use of P's value is
in L. The self cost of L is its inclusive cost minus the inclusive costs of its children, per pipe. Parts are the
statements with no parent: a part's cost is its inclusive cost, its lines are the statement and all its children.

Costs of pipes are additive only roughly (the compiler schedules them together), and the price of a shader is the
maximum over pipes: the price drop of a part is not the sum of its pipe drops, and the parts' price drops do not add
up to the price. The new varying itself costs a little in V.

The text is Unity's GLSL (HLSLcc: one assignment per line, real names of textures and uniforms); its prices are
GLES ones. Vulkan's SPIR-V of the same variant has no names left to show.

  parse(src) -> {"statements": [...], "lines": [...]}
  run(src, stage, cores, n, jobs) -> per core: base pipes and price, every statement's inclusive and self cost
"""
import concurrent.futures as cf
import os
import re

from paretogpu import mali
from paretogpu import progress as progress_ui
from paretogpu.frame import loops
from paretogpu.profile import score as heavy

PIPES = ("arith", "fma", "cvt", "sfu", "ls", "v", "t")
TYPES = {"float": ("f", 1), "int": ("i", 1), "uint": ("u", 1), "bool": ("b", 1)}
for _n in (2, 3, 4):
    TYPES.update({f"vec{_n}": ("f", _n), f"ivec{_n}": ("i", _n), f"uvec{_n}": ("u", _n), f"bvec{_n}": ("b", _n)})
COMP = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}
TYPE_RE = r"(float|int|uint|bool|[iub]?vec[234])"
DECL = re.compile(r"^\s*(?:layout\s*\([^)]*\)\s*)?(?:(?:flat|smooth|noperspective)\s+)?(in|out|uniform)?\s*"
                  r"(?:(highp|mediump|lowp)\s+)?" + TYPE_RE + r"\s+([A-Za-z_]\w*)\s*(?:=\s*(.+?))?\s*;\s*$")
ASSIGN = re.compile(r"^(\s*)([A-Za-z_]\w*)(?:\.([xyzwrgba]{1,4}))?\s*=(?!=)\s*(.+?)\s*;\s*$")
LOCAL = re.compile(r"^(\s*)(?:(?:highp|mediump|lowp)\s+)?" + TYPE_RE + r"\s+([A-Za-z_]\w*)\s*=\s*(.+?)\s*;\s*$")
IDENT = re.compile(r"\b([A-Za-z_]\w*)\b(?:\s*\.\s*([xyzwrgba]{1,4})\b)?")
LOCATION = re.compile(r"layout\s*\(\s*location\s*=\s*(\d+)\s*\)\s*in\b")
OUTPUT_NAMES = re.compile(r"^(gl_Position|gl_PointSize|gl_FragDepth)$")
NAME = "_paretogpu_ablated"
END = -1  # the shader's outputs


# --- parsing ---------------------------------------------------------------------------------------------------
def _main_span(lines):
    """(first, last) line indices of main()'s body, without its braces."""
    start = next(i for i, l in enumerate(lines) if re.match(r"^\s*void\s+main\s*\(", l))
    depth, first = 0, None
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                if first is None:
                    first = i + 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return first, i - 1
    raise ValueError("main() has no closing brace")


def _vars(lines, first):
    """{name: {"kind", "width", "prec", "io"}} of the declarations before main() (globals, inputs, outputs)."""
    out = {}
    for line in lines[:first]:
        m = DECL.match(line)
        if m:
            io, prec, typ, name = m.group(1), m.group(2), m.group(3), m.group(4)
            kind, width = TYPES[typ]
            out[name] = {"kind": kind, "width": width, "prec": prec, "io": io}
    return out


def _uses(expr, vars_):
    """{(var, component)} read by an expression."""
    out = set()
    for m in IDENT.finditer(expr):
        v = vars_.get(m.group(1))
        if not v or v["io"] == "uniform":
            continue
        comps = [COMP[c] for c in m.group(2)] if m.group(2) else range(v["width"])
        out.update((m.group(1), c) for c in comps if c < v["width"])
    return out


def parse(src, stage="fragment"):
    """Statements of main() and the tree of its blocks.
    statements: [{"id", "line" (0-based in src), "text", "var", "comps", "kind", "width", "prec", "local",
                  "ablate" (bool), "uses", "loop" (inside a loop)}];
    tree: nested [("stmt", id) | ("block", kind, [children])] for the def-use analysis."""
    lines = src.split("\n")
    first, last = _main_span(lines)
    vars_ = _vars(lines, first)
    stmts, root = [], []
    stack = [("plain", root, [])]  # (kind, children, ids of every statement inside)
    pending = None  # a loop / if header waiting for its "{" on the next line
    pending_owner = None  # the if header of a block waiting for its "{"
    last_if = None  # the header of the if block just closed: an else after it is controlled by it too
    loop_depth = 0

    def add(kind_, line_no, text, **kw):
        s = {"id": len(stmts), "line": line_no, "text": text.strip(), "type": kind_, "loop": loop_depth > 0,
             "depth": len(stack) - 1, "controls": [], **kw}
        stmts.append(s)
        stack[-1][1].append(("stmt", s["id"]))
        for entry in stack[1:]:
            entry[2].append(s["id"])
        return s

    def open_block(kind, owner=None):
        nonlocal loop_depth
        children, ids = [], []
        stack[-1][1].append(("block", kind, children))
        stack.append((kind, children, ids))
        if kind == "loop":
            loop_depth += 1
        if owner is not None:
            owner["controls"].append(ids)
        return ids

    def close_block():
        nonlocal loop_depth, last_if
        kind, _, ids = stack.pop()
        if kind == "loop":
            loop_depth -= 1
        return kind

    def nearest_loop():
        return next((e[2] for e in reversed(stack) if e[0] == "loop"), None)

    if_owner = []  # the header of every open if block

    loop_header = None  # (line, text, uses) of a for / while whose "{" is on the next line
    for i in range(first, last + 1):
        raw = lines[i]
        t = raw.strip()
        if not t or t.startswith("//") or t == ";":
            continue
        if t == "{":
            owner = pending_owner if pending in ("if", "else") else None
            ids = open_block(pending or "plain", owner)
            if pending in ("if", "else"):
                if_owner.append(owner)
            if pending == "loop" and loop_header:
                h = add("other", loop_header[0], loop_header[1], uses=loop_header[2])
                h["controls"].append(ids)
            pending, loop_header, pending_owner = None, None, None
            continue
        if re.match(r"^}\s*else\s*{$", t):
            close_block()
            owner = if_owner.pop() if if_owner else None
            open_block("else", owner)
            if_owner.append(owner)
            continue
        if re.match(r"^}\s*else\s*$", t):
            close_block()
            pending, pending_owner = "else", (if_owner.pop() if if_owner else None)
            continue
        if t == "}":
            if close_block() in ("if", "else") and if_owner:
                if_owner.pop()
            continue
        head = re.match(r"^(while|for|if)\s*\((.*)\)\s*(\{?)\s*(.*)$", t)
        if head and not ASSIGN.match(t):
            kind = "loop" if head.group(1) in ("while", "for") else "if"
            cond, brace, rest = head.group(2), head.group(3), head.group(4)
            if kind == "loop":
                # the header runs with the loop: its uses are uses inside the loop; it controls the loop
                if brace:
                    ids = open_block("loop")
                    add("other", i, t, uses=_uses(cond, vars_))["controls"].append(ids)
                else:
                    pending, loop_header = "loop", (i, t, _uses(cond, vars_))
                continue
            h = add("other", i, t, uses=_uses(cond, vars_))
            if brace and rest.rstrip().endswith("}"):  # if(c){break;} on one line: it controls its loop
                loop_ids = nearest_loop()
                if loop_ids is not None and re.search(r"\b(break|continue)\b", rest):
                    h["controls"].append(loop_ids)
                continue
            if brace:
                open_block("if", h)
                if_owner.append(h)
            else:
                pending, pending_owner = "if", h
            continue
        m = LOCAL.match(raw)
        if m:
            typ, name, expr = m.group(2), m.group(3), m.group(4)
            kind, width = TYPES[typ]
            prec = re.match(r"^\s*(highp|mediump|lowp)\s", raw)
            vars_[name] = {"kind": kind, "width": width, "prec": prec and prec.group(1), "io": None}
            add("assign", i, raw, var=name, comps=list(range(width)), kind=kind, width=width,
                prec=prec and prec.group(1), local=True, swizzle=None, expr=expr, uses=_uses(expr, vars_))
            continue
        m = ASSIGN.match(raw)
        if m and m.group(2) in vars_ or (m and OUTPUT_NAMES.match(m.group(2) or "")):
            name, swz, expr = m.group(2), m.group(3), m.group(4)
            v = vars_.get(name) or {"kind": "f", "width": 4, "prec": "highp", "io": "out"}
            comps = [COMP[c] for c in swz] if swz else list(range(v["width"]))
            add("assign", i, raw, var=name, comps=comps, kind=v["kind"], width=len(comps), prec=v["prec"],
                local=False, swizzle=swz, expr=expr, uses=_uses(expr, vars_), output=v["io"] == "out" or
                bool(OUTPUT_NAMES.match(name)))
            continue
        add("other", i, t, uses=_uses(t, vars_), ret=t.startswith("return"))
    outputs = {(n, c) for n, v in vars_.items() if v["io"] == "out" for c in range(v["width"])}
    if stage == "vertex":
        outputs |= {("gl_Position", c) for c in range(4)}
    for s in stmts:
        s["ablate"] = s["type"] == "assign" and not s["expr"].strip().startswith(NAME)
    return {"statements": stmts, "tree": root, "vars": vars_, "outputs": outputs, "lines": lines,
            "main": (first, last)}


# --- def-use ------------------------------------------------------------------------------------------------------
def _union(a, b):
    out = {k: set(v) for k, v in a.items()}
    for k, v in b.items():
        out.setdefault(k, set()).update(v)
    return out


def _flow(nodes, state, p, edges):
    i = 0
    while i < len(nodes):
        node = nodes[i]
        if node[0] == "stmt":
            s = p["statements"][node[1]]
            for key in s["uses"]:
                for d in state.get(key, ()):
                    edges.add((d, s["id"]))
            if s.get("ret"):
                for key in p["outputs"]:
                    for d in state.get(key, ()):
                        edges.add((d, END))
            if s["type"] == "assign":
                for c in s["comps"]:
                    state[(s["var"], c)] = {s["id"]}
                    if s.get("output"):
                        p["outputs"].add((s["var"], c))
        else:
            kind, children = node[1], node[2]
            if kind == "loop":
                once = _flow(children, {k: set(v) for k, v in state.items()}, p, edges)
                twice = _flow(children, _union(state, once), p, edges)  # values carried around the loop
                state = _union(state, twice)
            elif kind == "if" and i + 1 < len(nodes) and nodes[i + 1][0] == "block" and nodes[i + 1][1] == "else":
                a = _flow(children, {k: set(v) for k, v in state.items()}, p, edges)
                b = _flow(nodes[i + 1][2], {k: set(v) for k, v in state.items()}, p, edges)
                state = _union(a, b)
                i += 1
            elif kind in ("if", "else"):
                state = _union(state, _flow(children, {k: set(v) for k, v in state.items()}, p, edges))
            else:
                state = _flow(children, state, p, edges)
        i += 1
    return state


def dead_sets(p, users):
    """{statement id: the statements that become dead code when its expression is replaced}: what the compiler
    drops with it. Liveness from the shader's outputs, as a compiler does it: a statement is live when its value is
    an output or is read by a live statement; a condition or loop header is live when a live statement is in a block
    it controls. Everything else is dead (cycles of a loop's counter and its exit test die together)."""
    sources = {}
    for d, us in users.items():
        for u in us:
            sources.setdefault(u, set()).add(d)
    st = {s["id"]: s for s in p["statements"]}
    tracked = {i for i, s in st.items() if s["type"] == "assign" or (s["controls"] and not s.get("ret"))}
    controlled_by = {}
    for i, s in st.items():
        if s["type"] == "other" and s["controls"] and not s.get("ret"):
            for ids in s["controls"]:
                for x in ids:
                    if x != i:
                        controlled_by.setdefault(x, set()).add(i)
    # always live: values that reach an output, and the returns (with the conditions they are under)
    essential = [d for d, us in users.items() if END in us] + [i for i, s in st.items() if s.get("ret")]
    out = {}
    for s in p["statements"]:
        if not s["ablate"]:
            continue
        cut = s["id"]  # its expression is replaced: it reads nothing any more
        live, todo = set(), list(essential)
        while todo:
            x = todo.pop()
            if x in live:
                continue
            live.add(x)
            if x != cut:
                todo += sources.get(x, ())
            todo += controlled_by.get(x, ())
        out[cut] = {x for x in tracked if x not in live} | {cut}
    return out


def tree(p):
    """({statement id: parent id or None}, users): a statement's parent is the closest statement whose removal makes
    it dead code (its immediate post-dominator towards the outputs); users: {statement: statements reading it}."""
    edges = set()
    final = _flow(p["tree"], {}, p, edges)
    for key in p["outputs"]:
        for d in final.get(key, ()):
            edges.add((d, END))
    users = {}
    for d, u in edges:
        users.setdefault(d, set()).add(u)
    dead = dead_sets(p, users)
    parent = {}
    for s in p["statements"]:
        if s["type"] != "assign":
            continue
        doms = [d for d, ds in dead.items() if d != s["id"] and s["id"] in ds]
        parent[s["id"]] = min(doms, key=lambda d: len(dead[d])) if doms else None
    return parent, users


# --- ablated sources -----------------------------------------------------------------------------------------------
def ablated(p, sid, stage):
    """The source with statement sid's expression replaced by a new input of its type."""
    s = p["statements"][sid]
    lines = list(p["lines"])
    n, kind = s["width"], s["kind"]
    sw = "xyzw"[:n]
    if kind == "f":
        decl_t, expr = "vec4", f"{NAME}.{sw}"
    elif kind == "i":
        decl_t, expr = "ivec4", f"{NAME}.{sw}"
    elif kind == "u":
        decl_t, expr = "uvec4", f"{NAME}.{sw}"
    else:
        decl_t = "ivec4"
        expr = f"({NAME}.x != 0)" if n == 1 else f"notEqual({NAME}.{sw}, ivec{n}(0))"
    prec = s["prec"] or "highp"
    flat = "flat " if kind != "f" and stage == "fragment" else ""
    lhs = s["text"].split("=", 1)[0].rstrip()
    indent = re.match(r"^\s*", lines[s["line"]]).group(0)
    lines[s["line"]] = f"{indent}{lhs} = {expr};"
    first = p["main"][0]
    locs = [int(m.group(1)) for l in lines[:first] for m in [LOCATION.search(l)] if m]
    layout = f"layout(location = {max(locs) + 1}) " if stage == "fragment" and locs else ""
    decl = f"{layout}{flat}in {prec} {decl_t} {NAME};"
    at = next(i for i, l in enumerate(lines) if re.match(r"^\s*void\s+main\s*\(", l))
    lines.insert(at, decl)
    return "\n".join(lines)


# --- what a part does (plan A3.5) -------------------------------------------------------------------------------
def _plural(n, one, few, many):
    k = n % 100
    w = many if 11 <= k <= 14 else one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many
    return f"{n} {w}"


# what makes code heavy, the heaviest first: (key, pattern, one, few, many)
OPS = [
    ("buffers", r"\b_\w+\[(?!\d+\])", "чтение буфера по индексу", "чтения буферов по индексу", "чтений буферов по индексу"),
    ("tex", r"\b(?:texture(?:Lod|Grad|Offset|LodOffset|Proj|ProjLod)?|texelFetch)\s*\(", "выборка текстуры",
     "выборки текстур", "выборок текстур"),
    ("hash", None, "хэш на sin", "хэша на sin", "хэшей на sin"),
    ("trig", r"\b(?:sin|cos|tan|asin|acos|atan)\s*\(", "sin/cos/atan", "sin/cos/atan", "sin/cos/atan"),
    ("powexp", r"\b(?:pow|exp|exp2|log|log2)\s*\(", "pow/exp/log", "pow/exp/log", "pow/exp/log"),
    ("roots", r"\b(?:sqrt|inversesqrt|normalize|length|distance)\s*\(", "корень или нормализация",
     "корня или нормализации", "корней или нормализаций"),
    ("div", r"(?<![/*])/(?![/*])", "деление", "деления", "делений"),
    ("deriv", r"\b(?:dFdx|dFdy|fwidth)\s*\(", "производная", "производные", "производных"),
    ("ints", r"\b(?:u?int_?bitfieldExtract|bitfieldExtract|findLSB|findMSB|bitCount)\s*\(|<<|>>|(?<![&])&(?![&])|"
             r"(?<![|])\|(?![|])", "целочисленная операция", "целочисленные операции", "целочисленных операций"),
    ("matrix", r"\bhlslcc_mtx\w+\[\d\]", "строка матрицы", "строки матриц", "строк матриц"),
    ("select", r"\?|\b(?:lessThan|greaterThan|lessThanEqual|greaterThanEqual|equal|notEqual)\s*\(",
     "сравнение / выбор", "сравнения / выбора", "сравнений / выборов"),
]
HASH = re.compile(r"\bfract\s*\(.*\bsin\s*\(|\bsin\s*\(.*\d{4,}\.\d")


def explain(texts, loop=False, n=None):
    """{"ops": {key: count}, "text": short Russian summary} of the code of a part."""
    ops = {}
    for t in texts:
        rhs = t.split("=", 1)[1] if "=" in t else t
        if HASH.search(rhs):
            ops["hash"] = ops.get("hash", 0) + 1
            rhs = re.sub(r"\bsin\s*\(", "(", rhs)
        for key, pat, *_ in OPS:
            if pat:
                k = len(re.findall(pat, rhs))
                if k:
                    ops[key] = ops.get(key, 0) + k
    words = []
    for key, _, one, few, many in OPS:
        if ops.get(key):
            words.append(_plural(ops[key], one, few, many) if one != few else f"{ops[key]} {one}")
    text = ", ".join(words[:4]) or "простая арифметика"
    if loop:
        text = f"в цикле (×{n if n is not None else 'n'}): " + text
    return {"ops": ops, "text": text}


# --- measuring --------------------------------------------------------------------------------------------------
def measure(src, core, stage, n, forced_loops):
    """{"pipes", "price", "bound", "regs"} of a GLSL source on a core, dynamic loops forced to n; or {"error"}."""
    text = src
    if forced_loops:
        f = loops.force_glsl(src, n)
        if f is None:
            return {"error": "циклы не удалось прогнать n раз"}
        text = f
    r = mali.measure(text, core, "gles", stage)
    if not r["ok"]:
        return {"error": (r.get("error") or "malioc failed").strip().splitlines()[-1][:300]}
    c = heavy.combined(r)
    pipes = c["longest"] if c["longest"] is not None else heavy.fallback(c)
    price, bound = heavy.bottleneck(pipes)
    regs = max((v["work_regs"] or 0) for v in r["variants"].values())
    return {"pipes": {k: round(pipes.get(k) or 0.0, 4) for k in PIPES if pipes.get(k) is not None},
            "price": round(price, 4), "bound": bound, "regs": regs, "na": c["longest"] is None}


def run(src, stage, cores, n=2, jobs=None, progress=print, tick=None):
    """Ablation of every statement of main() on every core. Returns
    {"lines": main()'s lines [{"no", "text", "stmt"}], "statements": {id: {...}}, "by_core": {core: {"base", "stmts"}}}."""
    p = parse(src, stage)
    parent, users = tree(p)
    targets = [s for s in p["statements"] if s["ablate"]]
    # does the base need its loops forced (longest path N/A)? the same for every copy
    probe = {c: mali.measure(src, c, "gles", stage) for c in cores}
    forced = {c: bool(probe[c].get("ok")) and heavy.combined(probe[c])["longest"] is None for c in cores}
    base = {c: measure(src, c, stage, n, forced[c]) for c in cores}
    tasks = [(s["id"], c) for s in targets for c in cores if "error" not in base[c]]
    progress(f"ablation of {len(targets)} statements of the {stage} shader on {', '.join(cores)} ...")
    tick = tick or progress_ui.counter(len(tasks))  # one counter may span several shaders

    def job(t):
        sid, c = t
        try:
            return measure(ablated(p, sid, stage), c, stage, n, forced[c])
        finally:
            tick()

    with cf.ThreadPoolExecutor(jobs or os.cpu_count()) as ex:
        res = dict(zip(tasks, ex.map(job, tasks)))
    children = {}
    for sid, par in parent.items():
        if par is not None:
            children.setdefault(par, []).append(sid)
    by_core = {}
    for c in cores:
        b = base[c]
        rows = {}
        if "error" not in b:
            for s in targets:
                r = res.get((s["id"], c)) or {"error": "не замерено"}
                if "error" in r:
                    rows[s["id"]] = {"error": r["error"]}
                    continue
                rows[s["id"]] = {"incl": {k: round(b["pipes"].get(k, 0) - r["pipes"].get(k, 0), 4) for k in b["pipes"]},
                                 "price": round(b["price"] - r["price"], 4), "regs": b["regs"] - r["regs"]}
            for sid, row in rows.items():
                if "error" in row:
                    continue
                kids = [rows[k] for k in children.get(sid, []) if "error" not in rows.get(k, {"error": 1})]
                row["self"] = {k: round(max(0.0, v - sum(x["incl"].get(k, 0) for x in kids)), 4)
                               for k, v in row["incl"].items()}
                row["self_price"] = round(max(0.0, row["price"] - sum(x["price"] for x in kids)), 4)
        by_core[c] = {"base": b, "forced": forced[c], "stmts": rows}
    first, last = p["main"]
    by_line = {s["line"]: s["id"] for s in p["statements"]}
    sts = {s["id"]: s for s in p["statements"]}

    def subtree(sid):
        out, todo = [], [sid]
        while todo:
            x = todo.pop()
            out.append(x)
            todo += children.get(x, [])
        return out
    stmts = {}
    for s in p["statements"]:
        if s["type"] != "assign":
            continue
        tree_ids = subtree(s["id"])
        stmts[s["id"]] = {"line": s["line"], "text": s["text"], "ablate": s["ablate"], "loop": s["loop"],
                          "parent": parent.get(s["id"]), "children": children.get(s["id"], []),
                          "used_by_many": len(users.get(s["id"], ())) > 1,
                          "explain": explain([sts[x]["text"] for x in tree_ids], any(sts[x]["loop"] for x in tree_ids), n)}
    moves, budget = [], None
    if stage == "fragment":
        from paretogpu.project import vertexmove
        moves = vertexmove.candidates(src)
        budget = vertexmove.budget(src)
    return {"stage": stage, "n": n, "main": [first, last],
            "lines": [{"no": i + 1, "text": p["lines"][i], "stmt": by_line.get(i) if by_line.get(i) in stmts else None}
                      for i in range(first, last + 1)],
            "statements": stmts, "by_core": by_core, "vertex_candidates": moves, "varyings": budget,
            "note": "Цены — GLES-вариант (у Vulkan-варианта Unity нет имён). Максимум по конвейерам не складывается: "
                    "экономия цены у частей не суммируется в цену шейдера."}
