"""Shader price vs dynamic-loop trip counts: malioc's longest path is N/A for dynamic loops, so each outer loop's exit
test is forced to `counter < n` (body and breaks kept) and the forced variants are measured."""
import base64
import bisect
import re
import struct

MODEL = "per-loop/2"  # rename whenever plan() / cycles_at() change

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
    return list(v) if isinstance(v, (list, tuple)) else [v] * k


def _owners(spans):
    """(outer-loop number of every span, [span indices of the outer loops ordered by start])."""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    outer = [i for i in order if not any(spans[j][0] < spans[i][0] < spans[j][1] for j in range(len(spans)) if j != i)]
    index = {i: k for k, i in enumerate(outer)}
    return [index[i] if i in index else next(index[j] for j in outer if spans[j][0] < spans[i][0] < spans[j][1])
            for i in range(len(spans))], outer


def _parse_spirv(data):
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
    return header, insts


def _loop_plans(insts):
    """([(blocks [label, OpLabel idx, terminator idx], {label: pos}, [(header pos, merge pos, OpLoopMerge idx, exit
    block)], outer number per loop, {loop indices that are outer})] per function, n outer loops), or None."""
    const_ids = {x[2] for x in insts if x[0] & 0xFFFF == OP_CONSTANT}
    true_ids = {x[2] for x in insts if x[0] & 0xFFFF == OP_CONSTANT_TRUE}
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
    found = []
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
    plans, base = [], 0
    for fb, pos, loops in found:
        owner, outer = _owners([(a, b) for a, b, _, _ in loops])
        plans.append((fb, pos, loops, [base + o for o in owner], set(outer)))
        base += len(outer)
    return plans, base


def force_spirv(data, n, nested=1, describe=False):
    """n, nested: scalar or one per outer loop; a nested loop runs min(n_k, 1) * nested_k times. None if unforceable."""
    parsed = _parse_spirv(data)
    if parsed is None:
        return None
    header, insts = parsed
    bound = header[3]

    def new_id():
        nonlocal bound
        bound += 1
        return bound - 1

    int_t = next((x[1] for x in insts if x[0] & 0xFFFF == OP_TYPE_INT and x[2:4] == [32, 1]), None)
    bool_t = next((x[1] for x in insts if x[0] & 0xFFFF == OP_TYPE_BOOL), None)
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

    lp = _loop_plans(insts)
    if lp is None:
        return None
    plans, base = lp
    inserts_after, inserts_before, replace = {}, {}, {}
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


OP_EXT_INST, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_ARRAY, OP_TYPE_STRUCT, OP_TYPE_POINTER = 12, 22, 23, 28, 30, 32
OP_VARIABLE, OP_LOAD, OP_STORE, OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN = 59, 61, 62, 65, 66
OP_DECORATE, OP_MEMBER_DECORATE, OP_COMPOSITE_EXTRACT, OP_COPY_OBJECT, OP_BITCAST = 71, 72, 81, 83, 124
CONVERT_TO_INT, CONVERT_KEEP = (109, 110), (111, 112, 113, 114)  # OpConvertFToU/S; OpConvertS/UToF, OpU/SConvert
ADDS, SUBS = (128, 129), (130, 131)  # OpIAdd, OpFAdd; OpISub, OpFSub
GLSL_MIN, GLSL_MAX = (37, 38, 39), (40, 41, 42)  # GLSL.std.450 FMin UMin SMin; FMax UMax SMax
DEC_BINDING, DEC_SET, DEC_OFFSET, DEC_ARRAY_STRIDE = 33, 34, 35, 6
STORAGE_UNIFORM, STORAGE_PRIVATE, STORAGE_FUNCTION = 2, 6, 7
LESS, LESS_EQ, GREATER, GREATER_EQ = (176, 177), (178, 179), (172, 173), (174, 175)  # (unsigned, signed)
EQUAL, NOT_EQUAL = 170, 171
FLIP = {**dict(zip(LESS, GREATER)), **dict(zip(GREATER, LESS)), **dict(zip(LESS_EQ, GREATER_EQ)),
        **dict(zip(GREATER_EQ, LESS_EQ))}
# ops whose x[2] is a result id (not OpName & co.)
VALUES = {OP_EXT_INST, OP_CONSTANT_TRUE, OP_CONSTANT, OP_VARIABLE, OP_LOAD, OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN,
          OP_COMPOSITE_EXTRACT, OP_COPY_OBJECT, OP_BITCAST, OP_PHI, *CONVERT_TO_INT, *CONVERT_KEEP, *ADDS, *SUBS,
          *COMPARISONS}


class _Spirv:
    def __init__(self, insts):
        self.insts, self.made, self.where = insts, {}, {}
        self.set, self.binding, self.offset, self.stride = {}, {}, {}, {}
        self.block_of, self.stores, self.idom = {}, [], {}
        blocks, cur, preds = [], None, {}
        for k, x in enumerate(insts):
            op = x[0] & 0xFFFF
            if op in (OP_TYPE_BOOL, OP_TYPE_INT, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_ARRAY, OP_TYPE_STRUCT,
                      OP_TYPE_POINTER):
                self.made[x[1]] = x
            elif op == OP_DECORATE and len(x) >= 4:
                target = {DEC_SET: self.set, DEC_BINDING: self.binding, DEC_ARRAY_STRIDE: self.stride}.get(x[2])
                if target is not None:
                    target[x[1]] = x[3]
            elif op == OP_MEMBER_DECORATE and len(x) >= 5 and x[3] == DEC_OFFSET:
                self.offset[(x[1], x[2])] = x[4]
            elif op == OP_LABEL:
                cur = x[1]
                blocks.append(cur)
            elif op == OP_FUNCTION_END:
                self._dominators(blocks, preds)
                blocks, cur, preds = [], None, {}
            elif op == OP_STORE:
                self.stores.append((k, x[1], x[2]))
            elif op in VALUES and len(x) >= 3:
                self.made[x[2]] = x
                self.where[x[2]] = k
            if cur is not None:
                self.block_of[k] = cur
                for t in _targets(x):
                    preds.setdefault(t, []).append(cur)
                if op in TERMINATORS:
                    cur = None

    def _dominators(self, blocks, preds):
        """Immediate dominators; meet() relies on SPIR-V block order (dominators come first)."""
        if not blocks:
            return
        order = {b: i for i, b in enumerate(blocks)}
        idom = {blocks[0]: blocks[0]}

        def meet(a, b):
            while a != b:
                while order[a] > order[b]:
                    a = idom[a]
                while order[b] > order[a]:
                    b = idom[b]
            return a
        changed = True
        while changed:
            changed = False
            for b in blocks[1:]:
                ps = [p for p in preds.get(b, []) if p in idom]
                if not ps:
                    continue
                new = ps[0]
                for p in ps[1:]:
                    new = meet(p, new)
                if idom.get(b) != new:
                    idom[b], changed = new, True
        self.idom.update({b: (None if b == blocks[0] else d) for b, d in idom.items()})

    def dominates(self, a, b):
        while b is not None:
            if a == b:
                return True
            b = self.idom.get(b)
        return False

    def chain(self, ptr):
        """(root variable instruction, [constant index values]) of a pointer, or (None, None)."""
        idx = []
        x = self.made.get(ptr)
        while x is not None and x[0] & 0xFFFF in (OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN):
            vals = []
            for i in x[4:]:
                c = self.made.get(i)
                if c is None or c[0] & 0xFFFF != OP_CONSTANT:
                    return None, None
                vals.append(c[3])
            idx = vals + idx
            x = self.made.get(x[3])
        if x is None or x[0] & 0xFFFF != OP_VARIABLE:
            return None, None
        return x, idx

    def scalar(self, t):
        x = self.made.get(t)
        if x is None:
            return None
        if x[0] & 0xFFFF == OP_TYPE_FLOAT and x[2] == 32:
            return "f"
        if x[0] & 0xFFFF == OP_TYPE_INT and x[2] == 32:
            return "i" if x[3] else "u"
        return None

    def uniform(self, var, idx):
        ptr_t = self.made.get(var[1])
        if ptr_t is None or ptr_t[0] & 0xFFFF != OP_TYPE_POINTER or var[2] not in self.binding:
            return None
        t, off = ptr_t[3], 0
        for i in idx:
            x = self.made.get(t)
            if x is None:
                return None
            op = x[0] & 0xFFFF
            if op == OP_TYPE_STRUCT and (t, i) in self.offset and i + 2 < len(x):
                off, t = off + self.offset[(t, i)], x[2 + i]
            elif op == OP_TYPE_VECTOR and i < x[3]:
                off, t = off + 4 * i, x[2]
            elif op == OP_TYPE_ARRAY and t in self.stride:
                off, t = off + self.stride[t] * i, x[2]
            else:
                return None
        kind = self.scalar(t)
        return ["b", self.set.get(var[2], 0), self.binding[var[2]], off, kind] if kind else None

    def stored(self, var, idx, at, region=None):
        """Id last stored to exactly (var, idx) before `at` by a dominating store; None if any store to it is in region
        (a loop-variant value is no bound)."""
        hits = []
        for k, ptr, val in self.stores:
            v, i = self.chain(ptr)
            if v is None or v[2] != var[2]:
                continue
            n = min(len(i), len(idx))
            if i[:n] != idx[:n]:
                continue
            if region and region[0] <= k < region[1]:
                return None
            if k < at:
                hits.append((k, i, val))
        if not hits:
            return None
        k, i, val = hits[-1]
        if i != idx or not self.dominates(self.block_of.get(k), self.block_of.get(at)):
            return None
        return val

    def value(self, vid, at, region=None, depth=0):
        x = self.made.get(vid)
        if x is None or depth > 16:
            return None
        op = x[0] & 0xFFFF
        at = self.where.get(vid, at)
        if op == OP_CONSTANT and len(x) == 4:
            kind = self.scalar(x[1])
            if kind == "f":
                return ["c", struct.unpack("<f", struct.pack("<I", x[3]))[0]]
            if kind:
                return ["c", float(x[3] - (1 << 32) if kind == "i" and x[3] >= 1 << 31 else x[3])]
            return None
        if op == OP_LOAD:
            return self.load(x[3], [], at, region, depth)
        if op == OP_COMPOSITE_EXTRACT and len(x) == 5:
            src = self.made.get(x[3])
            if src is not None and src[0] & 0xFFFF == OP_LOAD:
                return self.load(src[3], [x[4]], self.where.get(x[3], at), region, depth)
            return None
        if op in CONVERT_TO_INT:
            a = self.value(x[3], at, region, depth + 1)
            return None if a is None else (["c", float(int(a[1]))] if a[0] == "c" else ["int", a])
        if op in CONVERT_KEEP or op in (OP_BITCAST, OP_COPY_OBJECT):
            return self.value(x[3], at, region, depth + 1)
        if op in ADDS + SUBS and len(x) == 5:
            a, b = (self.value(i, at, region, depth + 1) for i in x[3:5])
            return [("add" if op in ADDS else "sub"), a, b] if a and b else None
        if op == OP_EXT_INST and len(x) == 7 and x[4] in GLSL_MIN + GLSL_MAX:
            a, b = (self.value(i, at, region, depth + 1) for i in x[5:7])
            return [("min" if x[4] in GLSL_MIN else "max"), a, b] if a and b else None
        return None

    def load(self, ptr, extra, at, region, depth):
        var, idx = self.chain(ptr)
        if var is None:
            return None
        idx = idx + extra
        if var[3] == STORAGE_UNIFORM:
            return self.uniform(var, idx)
        if var[3] in (STORAGE_PRIVATE, STORAGE_FUNCTION):
            val = self.stored(var, idx, at, region)
            return None if val is None else self.value(val, at, None, depth + 1)
        return None

    def counter(self, vid, region):
        """Constant start value if vid loads a local whose only store in the loop is itself + 1, else None."""
        x = self.made.get(vid)
        if x is None or x[0] & 0xFFFF != OP_LOAD:
            return None
        var, idx = self.chain(x[3])
        if var is None or var[3] not in (STORAGE_PRIVATE, STORAGE_FUNCTION):
            return None
        inside = [val for k, ptr, val in self.stores
                  if region[0] <= k < region[1] and (self.chain(ptr)[0] or [None] * 3)[2] == var[2]]
        if len(inside) != 1:
            return None
        inc = self.made.get(inside[0])
        if inc is None or inc[0] & 0xFFFF != OP_IADD or len(inc) != 5:
            return None
        ones = [i for i in inc[3:5] if self.value(i, region[0]) == ["c", 1.0]]
        others = [i for i in inc[3:5] if i not in ones]
        if len(ones) != 1 or len(others) != 1:
            return None
        src = self.made.get(others[0])
        if src is None or src[0] & 0xFFFF != OP_LOAD or self.chain(src[3]) != (var, idx):
            return None
        init = self.stored(var, idx, region[0])
        first = self.value(init, region[0]) if init is not None else None
        return first[1] if first and first[0] == "c" else None


def spirv_bounds(data):
    """Bound per outer dynamic loop (force_spirv numbering), None where not from uniform blocks; None if unforceable.
    Unity's SPIR-V has no member names, so uniforms are ["b", ...] read from the call's block bytes; HLSLcc counters
    are stored/loaded locals, not SSA."""
    parsed = _parse_spirv(data)
    lp = _loop_plans(parsed[1]) if parsed else None
    if not lp or not lp[1]:
        return None
    insts = parsed[1]
    sp = _Spirv(insts)
    out = [None] * lp[1]
    for fb, pos, loops, owner, outer in lp[0]:
        for i, (n_, m_, lm, exit_block) in enumerate(loops):
            if i not in outer:
                continue
            region = (fb[n_][1], fb[m_][1])
            term = insts[exit_block[2]]
            cmp = sp.made.get(term[1])
            if cmp is None or len(cmp) != 5:
                continue
            op, a, b = cmp[0] & 0xFFFF, cmp[3], cmp[4]
            first = sp.counter(a, region)
            if first is None:
                first = sp.counter(b, region)
                if first is None:
                    continue
                a, b, op = b, a, FLIP.get(op, op)
            # term[3] is the false label: if it exits, cmp true means stay in the loop
            if term[3] == insts[lm][1]:
                rel = "<" if op in LESS or op == NOT_EQUAL else "<=" if op in LESS_EQ else None
            else:
                rel = "<" if op in GREATER_EQ or op == EQUAL else "<=" if op in GREATER else None
            bound = sp.value(b, exit_block[2], region) if rel else None
            if bound is None:
                continue
            bound = ["add", bound, ["c", 1.0]] if rel == "<=" else bound
            out[owner[i]] = ["sub", bound, ["c", float(first)]] if first else bound
    return out


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
    """force_spirv() for GLSL: only `while(true){` and dynamic `for(;;){` loops, else None."""
    loops = []
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
    """[{"nested": bool}] per outer dynamic loop in code order; None if none or unforceable."""
    return force_spirv(src, 0, describe=True) if isinstance(src, bytes) else force_glsl(src, 0, describe=True)


def plan(src):
    """[(name, loop k or None, n per outer loop, nested per outer loop)], base first; None if unforceable."""
    outer = outer_loops(src)
    if not outer:
        return None
    k_all = len(outer)
    ones = [1] * k_all
    out = [("base", None, ones, ones)]
    for k, lp in enumerate(outer):
        out.append(("zero", k, ones[:k] + [0] + ones[k + 1:], ones))
        out.append(("two", k, ones[:k] + [2] + ones[k + 1:], ones))
        if lp["nested"]:  # nested x2, not x0: an empty cluster mask lets the compiler drop the outer body
            out.append(("word", k, ones, ones[:k] + [2] + ones[k + 1:]))
    if k_all > 1:
        out.append(("zero", None, [0] * k_all, ones))
    return out


def forced(src):
    p = plan(src)
    if p is None:
        return None
    srcs = [force(src, ns, nests) for _, _, ns, nests in p]
    return None if any(x is None for x in srcs) else (p, srcs)


def profile(p, cycles):
    """cycles: per-pipe dict of every plan() measurement, in plan order."""
    k_all = sum(1 for name, k, _, _ in p if name == "zero" and k is not None)
    out = {"model": MODEL, "base": cycles[0], "loops": [{"zero": None, "two": None, "word": None} for _ in range(k_all)]}
    for (name, k, _, _), c in zip(p, cycles):
        if k is not None:
            out["loops"][k][name] = c
        elif name == "zero":
            out["zero"] = c
    return out


def _f(lp, base, n, pipe):
    """n < 1: (1 - n) * (zero - base); else (n - 1) * slope. The nested loop (Forward+ cluster-mask word scan, once per
    32 lights in the real shader) is charged once in base and taken out of the slope."""
    b = base.get(pipe) or 0
    if n < 1:
        return (1 - n) * ((lp["zero"].get(pipe) or 0) - b)
    word = lp["word"] or base
    slope = (lp["two"].get(pipe) or 0) - b - max(0.0, (word.get(pipe) or 0) - b)
    return (n - 1) * slope


def cycles_at(p, n):
    """n: scalar or one per outer loop, may be fractional (a mean over pixels)."""
    base, lps, zero = p["base"], p["loops"], p.get("zero")
    ns = per_loop(n, len(lps))
    pipes = set(base).union(*(set(c) for lp in lps for c in lp.values() if c), set(zero or {}))
    w = 1.0
    for m in ns:
        w *= max(0.0, 1 - m)
    out = {}
    for x in pipes:
        v = (base.get(x) or 0) + sum(_f(lp, base, m, x) for lp, m in zip(lps, ns))
        if zero and w:  # no loop running: the compiler drops what they all fed, so blend toward measured "zero"
            v += w * ((zero.get(x) or 0) - (base.get(x) or 0) - sum(_f(lp, base, 0, x) for lp in lps))
        out[x] = max(0.0, v)
    return out


# Bound expression (a loop's trip count from the event's uniforms): ["u", name, component 0..3 or None] | ["c", number]
# | ["min" | "max" | "add" | "sub", a, b] | ["int", a] (towards zero) | ["b", set, binding, byte offset, "f"|"i"|"u"]

_DECL = re.compile(r"^\s*(?:UNITY_UNIFORM\s+|uniform\s+)(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)\s*;", re.M)
_MEMBER = re.compile(r"^\s*(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)\s*;", re.M)
_CAST = re.compile(r"^(u?int|float|u?vec[234]|ivec[234]|bool)\((.*)\)$", re.S)
_CALL2 = re.compile(r"^(min|max)\((.*)\)$", re.S)
_ATOM = re.compile(r"^([A-Za-z_]\w*)(?:\.([xyzw]+))?$")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?u?$")
COMPONENTS = "xyzw"


def _uniforms(src):
    names = set(_DECL.findall(src))
    for block in re.finditer(r"uniform\s+\w+\s*\{(.*?)\};", src, re.S):
        names |= set(_MEMBER.findall(block.group(1)))
    return names


def _split_args(s):
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
    i = e.find("(")
    return i > 0 and _match(e, i, "(", ")") == len(e) - 1


def _scopes(src):
    """Returns scope_at(pos) -> positions of the braces open at pos, outermost first."""
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
    e = _strip_parens(expr)
    if depth > 8 or not e:
        return None
    if _NUM.match(e):
        v = float(e.rstrip("u"))
        return ["c", v - 2.0 ** 32 if e.endswith("u") and v >= 2.0 ** 31 else v]
    m = _CAST.match(e)
    if m and _whole_call(e):
        a = _trace(src, m.group(2), pos, uniforms, scope_at, depth + 1)
        return (["int", a] if m.group(1) in ("int", "uint") and a[0] not in ("int", "c") else a) if a else None
    m = _CALL2.match(e)
    if m and _whole_call(e):
        args = _split_args(m.group(2))
        if len(args) != 2:
            return None
        a, b = (_trace(src, x, pos, uniforms, scope_at, depth + 1) for x in args)
        return [m.group(1), a, b] if a and b else None
    for op, name in (("+", "add"), ("-", "sub")):
        d, k = 0, None
        for i, ch in enumerate(e):  # last top-level binary +/- (not a sign or exponent)
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
    best = None
    for a in re.finditer(r"(?<![\w.])" + re.escape(var) + r"(?:\.([xyzw]+))?\s*=(?!=)\s*([^;]*);", src[:pos]):
        if a.group(1) is None or not swz or swz in a.group(1):
            best = a
    if best is None:
        return None
    there = scope_at(best.start())
    if scope_at(pos)[:len(there)] != there:
        return None  # assigned in a block closed before pos: may not have run
    if _writes(src[best.end():pos], var, swz):
        return None
    rhs, mask = best.group(2), best.group(1)
    if mask and swz and len(mask) > 1:
        rhs = _strip_parens(rhs)
        m = _CAST.match(rhs)
        inner = _strip_parens(m.group(2)) if m and _whole_call(rhs) else rhs
        a = _ATOM.match(inner)
        if not a or not a.group(2) or len(a.group(2)) != len(mask):
            return None
        rhs = f"{a.group(1)}.{a.group(2)[mask.index(swz)]}"
    return _trace(src, rhs, best.start(), uniforms, scope_at, depth + 1)


def _writes(code, var, swz=""):
    for w in re.finditer(r"(?<![\w.])" + re.escape(var) + r"(?:\.([xyzw]+))?\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|"
                         r"(?<![\w.])" + re.escape(var) + r"(?:\.([xyzw]+))?\s*(?:\+\+|--)|"
                         r"(?:\+\+|--)\s*" + re.escape(var) + r"(?:\.([xyzw]+))?(?!\w)", code):
        mask = w.group(1) or w.group(2) or w.group(3)
        if not mask or not swz or swz in mask:
            return True
    return False


def _step_is_one(step, var):
    s = re.sub(r"\s+", "", step)
    return s in (f"{var}++", f"++{var}", f"{var}+=1", f"{var}+=1u", f"{var}={var}+1", f"{var}={var}+1u",
                 f"{var}=1+{var}", f"{var}=1u+{var}")


def glsl_bounds(src):
    """Bound per outer dynamic loop (outer_loops order), None where not from uniforms; None if unforceable."""
    outer = outer_loops(src)
    if outer is None:
        return None
    uniforms, scope_at = _uniforms(src), _scopes(src)
    spans = []  # must match force_glsl()'s loop detection so outer indices agree
    for m in re.finditer(r"\b(while|for)\s*\(", src):
        close = _match(src, m.end() - 1, "(", ")")
        inner = src[m.end():close]
        if m.group(1) == "for" and _STATIC_COND.match(inner.split(";")[1]):
            continue
        spans.append((m.start(), _match(src, src.index("{", close), "{", "}"), m.group(1), inner))
    _, outer_i = _owners([(s, e) for s, e, _, _ in spans])
    out = []
    for i in outer_i:
        start, end, kind, inner = spans[i]
        bound = None
        if kind == "for":
            init, cond, step = inner.split(";")
            var = re.match(r"^\s*(?:(?:lowp|mediump|highp)\s+)?(?:u?int\s+)?(\w+)\s*=\s*(.+?)\s*$", init, re.S)
            m = re.match(r"^\s*(\w+)\s*(<=|<)\s*(.+?)\s*$", cond, re.S)
            body = src[src.index("{", start):end]
            if var and m and m.group(1) == var.group(1) and _step_is_one(step, var.group(1)) \
                    and not _writes(body, var.group(1)):
                first = bound_value(_trace(src, var.group(2), start, uniforms, scope_at), {})
                b = _trace(src, m.group(3), start, uniforms, scope_at)
                written = any(_writes(body, a.group(1), a.group(2) or "")
                              for a in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)(?:\.([xyzw]))?(?![\w(])", m.group(3)))
                if b and first is not None and not written:
                    b = ["add", b, ["c", 1.0]] if m.group(2) == "<=" else b
                    bound = ["sub", b, ["c", first]] if first else b
        out.append(bound)
    return out if len(out) == len(outer) else None


def bound_value(bound, params):
    """params: {name: number or [x, y, z, w], "@set.binding": base64 of the call's uniform-block bytes}."""
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
    if op == "b":
        raw = (params or {}).get(f"@{bound[1]}.{bound[2]}")
        if not isinstance(raw, str):
            return None
        data = base64.b64decode(raw)
        if bound[3] + 4 > len(data):
            return None
        return float(struct.unpack_from({"f": "<f", "i": "<i", "u": "<I"}[bound[4]], data, bound[3])[0])
    if op == "int":
        a = bound_value(bound[1], params)
        return None if a is None else float(int(a))
    a, b = bound_value(bound[1], params), bound_value(bound[2], params)
    if a is None or b is None:
        return None
    return {"min": min, "max": max, "add": lambda x, y: x + y, "sub": lambda x, y: x - y}[op](a, b)


def bound_text(bound):
    if not bound:
        return None
    if bound[0] == "c":
        return f"{bound[1]:g}"
    if bound[0] == "u":
        return bound[1] + (f".{COMPONENTS[bound[2]]}" if bound[2] is not None else "")
    if bound[0] == "b":
        return f"block {bound[1]}.{bound[2]} +{bound[3]} ({bound[4]})"
    if bound[0] == "int":
        return f"int({bound_text(bound[1])})"
    a, b = bound_text(bound[1]), bound_text(bound[2])
    return f"{bound[0]}({a}, {b})" if bound[0] in ("min", "max") else f"{a} {'+' if bound[0] == 'add' else '-'} {b}"
