"""Price of shaders with dynamic loops as a function of the trip count of each loop (plan items K1.5, K1.10).

malioc reports the longest path of a shader with a dynamic loop (light / probe loops of Forward+, ray marching) as
N/A; its total cycles count every instruction once (both sides of every branch, every loop body once), which is
neither a lower nor an upper bound. Instead the dynamic loops are forced to run an exact number of times and measured.

The outer dynamic loops of a shader are numbered in code order (k = 0, 1, ...); a loop nested in one belongs to it.
One model for every shader, whatever its loops are for (lights, probes, ray steps):
  base     every outer loop forced to 1 iteration, the nested loops once per outer iteration
  zero_k   loop k forced to 0, the others to 1
  two_k    loop k forced to 2, the others to 1
  word_k   loop k at 1 with its nested loops run twice (measured only when loop k has nested loops)
  price(n_0, n_1, ...) = base + sum_k f_k(n_k)                                   (per pipe, longest path)
  f_k(n < 1) = (1 - n) * (zero_k - base),   f_k(n >= 1) = (n - 1) * slope_k,   slope_k = two_k - base - max(0, word_k - base)
(n may be fractional: a mean over pixels)
One n for every loop is the same formula with all n_k equal. A nested loop is charged once per outer loop (in base)
and taken out of the slope: in Forward+ it is the scan for the next word of the cluster mask, which the real shader
does once per 32 lights / probes (~2-3 LS cycles per light / probe iteration on Mali; the terrain probe loop on G78:
3 LS per probe instead of 5, as with the loop peeled by hand). Other ways overshoot: nested loops run 0 times leave
the mask empty and the compiler drops the outer body; peeling the first iteration off in the source doubles a light
loop body, which then spills. Loops with a constant bound are left alone.

Forcing keeps the loop body and its breaks: a counter (phi in SPIR-V, an int in GLSL) is added and the loop's exit
test is replaced by `counter < count`, so malioc's longest path is that many full iterations.
  force_spirv(bytes, n, nested)  binary SPIR-V (glslang structured loops: OpLoopMerge, exit test in the header or the
                                 block right after it), returns bytes or None if a loop has another shape;
  force_glsl(text, n, nested)    GLSL text (`while(true){` and `for(init; cond; step){`), returns text or None;
  n, nested: one number for every outer loop or a list, one per outer loop (a nested loop runs min(n_k, 1) * nested_k
  times per iteration of its outer loop k).
outer_loops(src) -> [{"nested"}] of the outer loops or None; plan(src) -> the measurements of the model, forced(src)
-> their sources. They are measured by app/rules.py (LoopRule) and app/ablation.py; cycles_at(p, n) is the price at
any n (one number or one per outer loop).
"""
import bisect
import re
import struct

MODEL = "per-loop/1"  # the measurements and formula of plan() / cycles_at(); a new model is a new name

# SPIR-V opcodes
OP_TYPE_BOOL, OP_TYPE_INT, OP_CONSTANT_TRUE, OP_CONSTANT = 20, 21, 41, 43
OP_FUNCTION, OP_FUNCTION_END, OP_LINE, OP_NO_LINE = 54, 56, 8, 317
OP_IADD, OP_SLESS = 128, 177
OP_PHI, OP_LOOP_MERGE, OP_LABEL, OP_BRANCH, OP_BRANCH_COND, OP_SWITCH = 245, 246, 248, 249, 250, 251
TERMINATORS = {249, 250, 251, 252, 253, 254, 255, 4416}
COMPARISONS = range(170, 192)  # OpIEqual .. OpFUnordGreaterThanEqual


def _targets(inst):
    op = inst[0] & 0xFFFF
    if op == OP_BRANCH:
        return [inst[1]]
    if op == OP_BRANCH_COND:
        return [inst[2], inst[3]]
    if op == OP_SWITCH:
        return [inst[2]] + inst[4::2]
    return []


def per_loop(v, k):
    """One number for every outer loop, or the list given (k: how many outer loops)."""
    return list(v) if isinstance(v, (list, tuple)) else [v] * k


def _owners(spans):
    """For every (start, end) span of a loop: the index of the outer loop it belongs to (itself when outer), the outer
    loops numbered in the order of their start."""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    outer = [i for i in order if not any(spans[j][0] < spans[i][0] < spans[j][1] for j in range(len(spans)) if j != i)]
    index = {i: k for k, i in enumerate(outer)}
    return [index[i] if i in index else next(index[j] for j in outer if spans[j][0] < spans[i][0] < spans[j][1])
            for i in range(len(spans))], outer


def force_spirv(data, n, nested=1, describe=False):
    """Binary SPIR-V with every dynamic loop forced (module docstring: n, nested), or None. describe: the outer loops
    [{"nested"}] instead (None if a loop cannot be forced)."""
    if len(data) % 4 or len(data) < 20:
        return None
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    if words[0] != 0x07230203:
        return None
    header, insts, i = words[:5], [], 5
    while i < len(words):
        wc = words[i] >> 16
        if wc == 0:
            return None
        insts.append(words[i:i + wc])
        i += wc
    bound = header[3]

    def new_id():
        nonlocal bound
        bound += 1
        return bound - 1

    int_t = next((x[1] for x in insts if x[0] & 0xFFFF == OP_TYPE_INT and x[2:4] == [32, 1]), None)
    bool_t = next((x[1] for x in insts if x[0] & 0xFFFF == OP_TYPE_BOOL), None)
    const_ids = {x[2] for x in insts if x[0] & 0xFFFF == OP_CONSTANT}
    true_ids = {x[2] for x in insts if x[0] & 0xFFFF == OP_CONSTANT_TRUE}
    first_fn = next((k for k, x in enumerate(insts) if x[0] & 0xFFFF == OP_FUNCTION), None)
    if first_fn is None:
        return None
    decls = []
    if int_t is None:
        int_t = new_id()
        decls.append([(4 << 16) | OP_TYPE_INT, int_t, 32, 1])
    if bool_t is None:
        bool_t = new_id()
        decls.append([(2 << 16) | OP_TYPE_BOOL, bool_t])
    consts = {v: x[2] for x in insts if x[0] & 0xFFFF == OP_CONSTANT and len(x) == 4 and x[1] == int_t
              for v in [x[3]]}

    def const(v):
        if v not in consts:
            consts[v] = new_id()
            decls.append([(4 << 16) | OP_CONSTANT, int_t, consts[v], v])
        return consts[v]

    # blocks of every function: [label id, index of OpLabel, index of the terminator]
    blocks, fn_blocks, cur = [], [], None
    defs = {}
    for k, x in enumerate(insts):
        op = x[0] & 0xFFFF
        if op == OP_LABEL:
            cur = [x[1], k, None]
        elif cur and op in TERMINATORS:
            cur[2] = k
            fn_blocks.append(cur)
            cur = None
        elif op == OP_FUNCTION_END:
            if fn_blocks:
                blocks.append(fn_blocks)
            fn_blocks = []
        if op in COMPARISONS and len(x) >= 5:
            defs[x[2]] = x

    inserts_after, inserts_before, replace = {}, {}, {}
    found = []  # (fb, pos, loops) of every function
    for fb in blocks:
        pos = {b[0]: n_ for n_, b in enumerate(fb)}
        loops = []
        for n_, (lab, start, term) in enumerate(fb):
            lm = next((k for k in range(start, term) if insts[k][0] & 0xFFFF == OP_LOOP_MERGE), None)
            if lm is None:
                continue
            merge = insts[lm][1]
            t = insts[term]
            if t[0] & 0xFFFF == OP_BRANCH_COND and merge in t[2:4]:
                exit_block = fb[n_]
            elif t[0] & 0xFFFF == OP_BRANCH and t[1] in pos:
                exit_block = fb[pos[t[1]]]
                et = insts[exit_block[2]]
                if et[0] & 0xFFFF != OP_BRANCH_COND or merge not in et[2:4]:
                    return None
            else:
                return None
            cond = insts[exit_block[2]][1]
            cmp = defs.get(cond)
            static = cond not in true_ids and cmp is not None and (cmp[3] in const_ids or cmp[4] in const_ids)
            if static:
                continue
            if merge not in pos:
                return None
            loops.append((n_, pos[merge], lm, exit_block))
        found.append((fb, pos, loops))
    # the outer loops of every function, numbered in module order
    plans, base = [], 0
    for fb, pos, loops in found:
        owner, outer = _owners([(a, b) for a, b, _, _ in loops])
        plans.append((fb, pos, loops, [base + o for o in owner], set(outer)))
        base += len(outer)
    if describe:
        with_nested = {owner[i] for _, _, loops, owner, outer in plans for i in range(len(loops)) if i not in outer}
        return [{"nested": k in with_nested} for k in range(base)] if base else None
    ns, nests = per_loop(n, base), per_loop(nested, base)
    forced = 0
    for fb, pos, loops, owner, outer in plans:
        for i, (n_, m_, lm, exit_block) in enumerate(loops):
            k = owner[i]
            count = ns[k] if i in outer else min(ns[k], 1) * nests[k]
            lab, start, _ = fb[n_]
            preds = [b for b in fb if lab in _targets(insts[b[2]])]
            cnt, nxt, cond = new_id(), new_id(), new_id()
            phi = [((3 + 2 * len(preds)) << 16) | OP_PHI, int_t, cnt]
            for b in preds:
                phi += [nxt if pos[b[0]] >= n_ else const(0), b[0]]
            k = start
            while k + 1 < len(insts) and insts[k + 1][0] & 0xFFFF in (OP_PHI, OP_LINE, OP_NO_LINE):
                k += 1
            inserts_after.setdefault(k, []).append(phi)
            inserts_before.setdefault(lm, []).extend([
                [(5 << 16) | OP_IADD, int_t, nxt, cnt, const(1)],
                [(5 << 16) | OP_SLESS, bool_t, cond, cnt, const(count)]])
            et = insts[exit_block[2]]
            merge = insts[lm][1]
            stay = et[3] if et[2] == merge else et[2]
            if stay == merge:
                return None
            replace[exit_block[2]] = [(4 << 16) | OP_BRANCH_COND, cond, stay, merge]
            forced += 1
    if not forced:
        return None
    out = []
    for k, x in enumerate(insts):
        if k == first_fn:
            out += decls
        out += inserts_before.get(k, [])
        out.append(replace.get(k, x))
        out += inserts_after.get(k, [])
    header[3] = bound
    flat = header + [w for x in out for w in x]
    return struct.pack(f"<{len(flat)}I", *flat)


def _match(s, i, open_, close):
    d = 0
    for j in range(i, len(s)):
        if s[j] == open_:
            d += 1
        elif s[j] == close:
            d -= 1
            if d == 0:
                return j
    return None


_STATIC_COND = re.compile(r"^\s*[\w.]+\s*(<|<=|>|>=|!=)\s*-?\d+u?\s*$|^\s*-?\d+u?\s*(<|<=|>|>=|!=)\s*[\w.]+\s*$")


def force_glsl(src, n, nested=1, describe=False):
    """GLSL with every dynamic loop forced (module docstring: n, nested), or None. describe: the outer loops
    [{"nested"}] instead (None if a loop cannot be forced)."""
    loops = []  # (start, header end, body open brace, body close brace, kind, (init, step))
    for m in re.finditer(r"\b(while|for)\s*\(", src):
        close = _match(src, m.end() - 1, "(", ")")
        if close is None:
            return None
        brace = close + 1
        while brace < len(src) and src[brace].isspace():
            brace += 1
        if brace >= len(src) or src[brace] != "{":
            return None
        inner = src[m.end():close]
        if m.group(1) == "while":
            if inner.strip() != "true":
                return None
            loops.append((m.start(), brace, _match(src, brace, "{", "}"), "while", None))
        else:
            parts = inner.split(";")
            if len(parts) != 3:
                return None
            if _STATIC_COND.match(parts[1]):
                continue
            loops.append((m.start(), brace, _match(src, brace, "{", "}"), "for", (parts[0], parts[2])))
    if not loops:
        return None
    owner, outer = _owners([(t[0], t[2]) for t in loops])
    if describe:
        return [{"nested": any(owner[i] == k and i != j for i in range(len(loops)))} for k, j in enumerate(outer)]
    ns, nests = per_loop(n, len(outer)), per_loop(nested, len(outer))
    for i in sorted(range(len(loops)), key=lambda i: -loops[i][0]):
        start, brace, end, kind, io = loops[i]
        k = owner[i]
        count = ns[k] if i in outer else min(ns[k], 1) * nests[k]
        var = f"_so_loop{start}"
        if kind == "while":
            head = f"for(int {var}=0;{var}<{count};{var}++){{"
        else:
            head = f"int {var}=0; for({io[0]};{var}<{count};{io[1]}){{ {var}++;"
        src = src[:start] + head + src[brace + 1:]
    return src


def force(src, n, nested=1):
    return force_spirv(src, n, nested) if isinstance(src, bytes) else force_glsl(src, n, nested)


def outer_loops(src):
    """[{"nested": bool}] of the outer dynamic loops of a source in code order, or None (none, or one cannot be
    forced)."""
    return force_spirv(src, 0, describe=True) if isinstance(src, bytes) else force_glsl(src, 0, describe=True)


def plan(src):
    """The measurements of the model (module docstring) of a source: [(name, loop k or None, n per outer loop,
    nested per outer loop)], base first; None when its loops cannot be forced."""
    outer = outer_loops(src)
    if not outer:
        return None
    k_all = len(outer)
    ones = [1] * k_all
    out = [("base", None, ones, ones)]
    for k, lp in enumerate(outer):
        out.append(("zero", k, ones[:k] + [0] + ones[k + 1:], ones))
        out.append(("two", k, ones[:k] + [2] + ones[k + 1:], ones))
        if lp["nested"]:
            out.append(("word", k, ones, ones[:k] + [2] + ones[k + 1:]))
    return out


def forced(src):
    """(plan(src), the forced source of every measurement), or None."""
    p = plan(src)
    if p is None:
        return None
    srcs = [force(src, ns, nests) for _, _, ns, nests in p]
    return None if any(x is None for x in srcs) else (p, srcs)


def profile(p, cycles):
    """The loop data of one core from the plan and the cycles per pipe of every measurement in its order:
    {"model", "base", "loops": [{"zero", "two", "word"}]}."""
    k_all = sum(1 for name, _, _, _ in p if name == "zero")
    out = {"model": MODEL, "base": cycles[0], "loops": [{"zero": None, "two": None, "word": None} for _ in range(k_all)]}
    for (name, k, _, _), c in zip(p, cycles):
        if k is not None:
            out["loops"][k][name] = c
    return out


def _f(lp, base, n, pipe):
    """f_k(n) of one pipe (module docstring)."""
    b = base.get(pipe) or 0
    if n < 1:
        return (1 - n) * ((lp["zero"].get(pipe) or 0) - b)
    word = lp["word"] or base
    slope = (lp["two"].get(pipe) or 0) - b - max(0.0, (word.get(pipe) or 0) - b)
    return (n - 1) * slope


def cycles_at(p, n):
    """Cycles per pipe of the profile p (profile()) with outer loop k at n_k iterations (n: one number for every
    loop or one per loop)."""
    base, lps = p["base"], p["loops"]
    ns = per_loop(n, len(lps))
    pipes = set(base).union(*(set(c) for lp in lps for c in lp.values() if c))
    return {x: max(0.0, (base.get(x) or 0) + sum(_f(lp, base, m, x) for lp, m in zip(lps, ns))) for x in pipes}


# --- what bounds a loop (plan K1.10, stage B) ---------------------------------------------------------------------
# The bound of an outer `for` of Unity's GLES GLSL (HLSLcc) traced back to the shader's uniforms, so its trip count is
# the value the frame's event used (Frame Debugger parameters). A bound is a small expression:
#   ["u", name, component or None]  a uniform (a vector's component 0..3)
#   ["c", number]                   a constant
#   ["min" | "max" | "add" | "sub", a, b]
# A loop whose bound is not that (a mask scan, a value read from a buffer, an assignment in a branch closed before
# the loop) has the bound None: its n stays the default.

_DECL = re.compile(r"^\s*(?:UNITY_UNIFORM\s+|uniform\s+)(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)\s*;", re.M)
_MEMBER = re.compile(r"^\s*(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)\s*;", re.M)
_CAST = re.compile(r"^(?:u?int|float|u?vec[234]|ivec[234]|bool)\((.*)\)$", re.S)
_CALL2 = re.compile(r"^(min|max)\((.*)\)$", re.S)
_ATOM = re.compile(r"^([A-Za-z_]\w*)(?:\.([xyzw]+))?$")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?u?$")
COMPONENTS = "xyzw"


def _uniforms(src):
    """Names of the plain (not array) uniforms of a GLES source, in uniform blocks or not."""
    names = set(_DECL.findall(src))
    for block in re.finditer(r"uniform\s+\w+\s*\{(.*?)\};", src, re.S):
        names |= set(_MEMBER.findall(block.group(1)))
    return names


def _split_args(s):
    """'a, min(b, c)' -> ['a', 'min(b, c)'] at the top level."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    return out + [cur.strip()]


def _strip_parens(e):
    e = e.strip()
    while e.startswith("(") and _match(e, 0, "(", ")") == len(e) - 1:
        e = e[1:-1].strip()
    return e


def _whole_call(e):
    """Whether e is one call `f(...)` whose parentheses close at its end."""
    i = e.find("(")
    return i > 0 and _match(e, i, "(", ")") == len(e) - 1


def _scopes(src):
    """scope_at(pos): the positions of the braces open at pos, outermost first."""
    marks, opens = [], []
    for i, ch in enumerate(src):
        if ch == "{":
            opens.append(i)
            marks.append((i, tuple(opens)))
        elif ch == "}" and opens:
            opens.pop()
            marks.append((i, tuple(opens)))
    starts = [m[0] for m in marks]

    def scope_at(pos):
        k = bisect.bisect_right(starts, pos) - 1
        return marks[k][1] if k >= 0 else ()
    return scope_at


def _trace(src, expr, pos, uniforms, scope_at, depth=0):
    """The bound expression of `expr` as it is at position pos, or None."""
    e = _strip_parens(expr)
    if depth > 8 or not e:
        return None
    if _NUM.match(e):
        return ["c", float(e.rstrip("u"))]
    m = _CAST.match(e)
    if m and _whole_call(e):
        return _trace(src, m.group(1), pos, uniforms, scope_at, depth + 1)
    m = _CALL2.match(e)
    if m and _whole_call(e):
        args = _split_args(m.group(2))
        if len(args) != 2:
            return None
        a, b = (_trace(src, x, pos, uniforms, scope_at, depth + 1) for x in args)
        return [m.group(1), a, b] if a and b else None
    for op, name in (("+", "add"), ("-", "sub")):
        d, k = 0, None
        for i, ch in enumerate(e):  # the last top-level + / - that is not a sign
            d += ch == "("
            d -= ch == ")"
            if ch == op and d == 0 and i > 0 and e[i - 1] not in "eE(+-*/,":
                k = i
        if k is not None:
            a = _trace(src, e[:k], pos, uniforms, scope_at, depth + 1)
            b = _trace(src, e[k + 1:], pos, uniforms, scope_at, depth + 1)
            return [name, a, b] if a and b else None
    m = _ATOM.match(e)
    if not m:
        return None
    var, swz = m.group(1), m.group(2) or ""
    if len(swz) > 1:
        return None
    if var in uniforms:
        return ["u", var, COMPONENTS.index(swz) if swz else None]
    # the last assignment to that component before pos, in a block that also holds pos
    best = None
    for a in re.finditer(r"(?<![\w.])" + re.escape(var) + r"(?:\.([xyzw]+))?\s*=(?!=)\s*([^;]*);", src[:pos]):
        if a.group(1) is None or not swz or swz in a.group(1):
            best = a
    if best is None:
        return None
    there = scope_at(best.start())
    if scope_at(pos)[:len(there)] != there:
        return None  # assigned in a block closed before pos: not known to hold
    rhs, mask = best.group(2), best.group(1)
    if mask and swz and len(mask) > 1:
        rhs = _strip_parens(rhs)
        m = _CAST.match(rhs)
        inner = _strip_parens(m.group(1)) if m and _whole_call(rhs) else rhs
        a = _ATOM.match(inner)
        if not a or not a.group(2) or len(a.group(2)) != len(mask):
            return None
        rhs = f"{a.group(1)}.{a.group(2)[mask.index(swz)]}"
    return _trace(src, rhs, best.start(), uniforms, scope_at, depth + 1)


def glsl_bounds(src):
    """The bound (see above) of every outer dynamic loop of a GLES source in code order, None where it is not a
    function of uniforms; None for the whole source when its loops cannot be forced."""
    outer = outer_loops(src)
    if outer is None:
        return None
    uniforms, scope_at = _uniforms(src), _scopes(src)
    spans = []  # the dynamic loops as force_glsl() finds them
    for m in re.finditer(r"\b(while|for)\s*\(", src):
        close = _match(src, m.end() - 1, "(", ")")
        inner = src[m.end():close]
        if m.group(1) == "for" and _STATIC_COND.match(inner.split(";")[1]):
            continue
        spans.append((m.start(), _match(src, src.index("{", close), "{", "}"), m.group(1), inner))
    _, outer_i = _owners([(s, e) for s, e, _, _ in spans])
    out = []
    for i in outer_i:
        start, _, kind, inner = spans[i]
        bound = None
        if kind == "for":
            init, cond, _ = inner.split(";")
            var = re.search(r"(\w+)\s*=", init)
            m = re.match(r"^\s*(\w+)\s*(<=|<)\s*(.+?)\s*$", cond, re.S)
            if var and m and m.group(1) == var.group(1):
                b = _trace(src, m.group(3), start, uniforms, scope_at)
                bound = (["add", b, ["c", 1.0]] if m.group(2) == "<=" else b) if b else None
        out.append(bound)
    return out if len(out) == len(outer) else None


def bound_value(bound, params):
    """The trip count a bound gives with an event's shader parameters ({name: number or [x, y, z, w]}), or None."""
    if not bound:
        return None
    op = bound[0]
    if op == "c":
        return bound[1]
    if op == "u":
        v = (params or {}).get(bound[1])
        if isinstance(v, list):
            if bound[2] is None or bound[2] >= len(v) or isinstance(v[0], list):
                return None
            v = v[bound[2]]
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    a, b = bound_value(bound[1], params), bound_value(bound[2], params)
    if a is None or b is None:
        return None
    return {"min": min, "max": max, "add": lambda x, y: x + y, "sub": lambda x, y: x - y}[op](a, b)


def bound_text(bound):
    """'min(CR_Params0.w, 128)' of a bound."""
    if not bound:
        return None
    if bound[0] == "c":
        return f"{bound[1]:g}"
    if bound[0] == "u":
        return bound[1] + (f".{COMPONENTS[bound[2]]}" if bound[2] is not None else "")
    a, b = bound_text(bound[1]), bound_text(bound[2])
    return f"{bound[0]}({a}, {b})" if bound[0] in ("min", "max") else f"{a} {'+' if bound[0] == 'add' else '-'} {b}"
