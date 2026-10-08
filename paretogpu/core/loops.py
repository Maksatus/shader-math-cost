"""Price of shaders with dynamic loops as a function of the loop trip count n (review of 2026-10-06, plan K1.5).

malioc reports the longest path of a shader with a dynamic loop (light / probe loops of Forward+, ray marching) as
N/A; its total cycles count every instruction once (both sides of every branch, every loop body once), which is
neither a lower nor an upper bound. Instead every dynamic loop is forced to run exactly n times and the shader is
measured at n = 0, 1, 2:
  price(0) = measured, price(n >= 1) = price(1) + (n - 1) * (price(2) - price(1))     (per pipe, longest path)
Outer dynamic loops get n iterations, loops nested in them 1 (e.g. one word of a cluster mask per light), so n is
"iterations of every outer dynamic loop" (lights, probes, ray steps). Loops with a constant bound are left alone.

Forcing keeps the loop body and its breaks: a counter (phi in SPIR-V, an int in GLSL) is added and the loop's exit
test is replaced by `counter < n`, so malioc's longest path is n full iterations.
  force_spirv(bytes, n)  binary SPIR-V (glslang structured loops: OpLoopMerge, exit test in the header or the block
                         right after it), returns bytes or None if a loop has another shape;
  force_glsl(text, n)    GLSL text (`while(true){` and `for(init; cond; step){`), returns text or None.
The forced sources are measured by app/variants.py (loops_of); cycles_at() is the price at any n.
"""
import re
import struct

NS = (0, 1, 2)  # measured trip counts

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


def force_spirv(data, n):
    """Binary SPIR-V with every dynamic loop forced to n (nested: min(n, 1)) iterations, or None."""
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
    forced = 0
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
        for n_, m_, lm, exit_block in loops:
            nested = any(a < n_ < b for a, b, _, _ in loops if (a, b) != (n_, m_))
            count = min(n, 1) if nested else n
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


def force_glsl(src, n):
    """GLSL with every dynamic loop forced to n (nested: min(n, 1)) iterations, or None."""
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
    for start, brace, end, kind, io in sorted(loops, key=lambda t: -t[0]):
        nested = any(s < start and start < e for s, _, e, _, _ in loops)
        count = min(n, 1) if nested else n
        var = f"_so_loop{start}"
        if kind == "while":
            head = f"for(int {var}=0;{var}<{count};{var}++){{"
        else:
            head = f"int {var}=0; for({io[0]};{var}<{count};{io[1]}){{ {var}++;"
        src = src[:start] + head + src[brace + 1:]
    return src


def force(src, n):
    return force_spirv(src, n) if isinstance(src, bytes) else force_glsl(src, n)


def cycles_at(p, n):
    """Cycles per pipe at trip count n from parametric() data of one core."""
    c0, c1, c2 = p["c"]
    if n == 0:
        return dict(c0)
    return {k: max(0.0, (c1.get(k) or 0) + (n - 1) * ((c2.get(k) or 0) - (c1.get(k) or 0))) for k in set(c1) | set(c2)}
