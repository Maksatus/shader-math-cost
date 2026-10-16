"""Fragment SPIR-V with discards removed (for rd_device.py). Stdlib only: imported inside RenderDoc's python.
Phone pixels are Samples Passed (past the depth test), so a draw that discards counts too few; the replay swaps in this
shader (kill -> return, demote -> nop) and counts again. without_discard() -> None if nothing changed or a kill is in a
non-void function."""
from array import array

MAGIC = 0x07230203
OP_NOP, OP_TYPE_VOID, OP_FUNCTION, OP_KILL, OP_RETURN = 0, 19, 54, 252, 253
OP_TERMINATE, OP_DEMOTE = 4416, 5380
KILLS = (OP_KILL, OP_TERMINATE)


def _words(raw):
    w = array("I")
    w.frombytes(bytes(raw)[:len(raw) - len(raw) % 4])
    if not w or w[0] != MAGIC:
        w.byteswap()
        if not w or w[0] != MAGIC:
            raise ValueError("not SPIR-V")
        return w, True
    return w, False


def _instructions(w):
    i = 5
    while i < len(w):
        n, op = w[i] >> 16, w[i] & 0xFFFF
        if n == 0:
            raise ValueError(f"SPIR-V instruction of no words at {i}")
        yield i, op
        i += n


OP_ENTRY_POINT, OP_EXECUTION_MODE, OP_EXECUTION_MODE_ID = 15, 16, 331
OP_TYPE_BOOL, OP_TYPE_INT, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_MATRIX = 20, 21, 22, 23, 24
OP_TYPE_ARRAY, OP_TYPE_RUNTIME_ARRAY, OP_TYPE_STRUCT, OP_TYPE_POINTER = 28, 29, 30, 32
OP_CONSTANT, OP_CONSTANT_COMPOSITE, OP_SPEC_CONSTANT_COMPOSITE = 43, 44, 51
OP_VARIABLE, OP_DECORATE, OP_MEMBER_DECORATE, OP_FUNCTION_END, OP_FUNCTION_CALL = 59, 71, 72, 56, 57
DEC_ROW_MAJOR, DEC_ARRAY_STRIDE, DEC_MATRIX_STRIDE, DEC_BLOCK, DEC_BUILTIN = 4, 6, 7, 2, 11
DEC_BINDING, DEC_SET, DEC_OFFSET = 33, 34, 35
STORAGE_UNIFORM, MODE_LOCAL_SIZE, MODE_LOCAL_SIZE_ID, BUILTIN_WORKGROUP_SIZE = 2, 17, 38, 25


def _string(w, i):
    raw = w[i:].tobytes()
    s = raw[:raw.index(b"\0")]
    return s.decode("utf-8", "replace"), (len(s) + 4) // 4


def interface(raw, entry):
    """-> {"blocks": [(set, binding, byte size)] of the uniform blocks the entry point's functions use (byte size 0: it
    ends in a runtime array), "local_size": [x, y, z] or None}."""
    w, _ = _words(raw)
    insts = [(i, op, w[i:i + (w[i] >> 16)]) for i, op in _instructions(w)]
    dec, mdec, types, consts, ptrs, uvars = {}, {}, {}, {}, {}, []
    entry_id, local, local_ids, fn_of, fn, calls, uses = None, None, None, {}, None, {}, {}
    for i, op, x in insts:
        if op == OP_ENTRY_POINT:
            name, _ = _string(w, i + 3)
            if name == entry:
                entry_id = x[2]
        elif op == OP_EXECUTION_MODE and x[2] == MODE_LOCAL_SIZE:
            local = (x[1], list(x[3:6]))
        elif op == OP_EXECUTION_MODE_ID and x[2] == MODE_LOCAL_SIZE_ID:
            local_ids = (x[1], list(x[3:6]))
        elif op == OP_DECORATE:
            dec.setdefault(x[1], {})[x[2]] = list(x[3:])
        elif op == OP_MEMBER_DECORATE:
            mdec.setdefault((x[1], x[2]), {})[x[3]] = list(x[4:])
        elif op in (OP_TYPE_BOOL, OP_TYPE_INT, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_MATRIX, OP_TYPE_ARRAY,
                    OP_TYPE_RUNTIME_ARRAY, OP_TYPE_STRUCT):
            types[x[1]] = (op, list(x[2:]))
        elif op == OP_TYPE_POINTER:
            ptrs[x[1]] = (x[2], x[3])
        elif op == OP_CONSTANT:
            consts[x[2]] = x[3]
        elif op in (OP_CONSTANT_COMPOSITE, OP_SPEC_CONSTANT_COMPOSITE):
            consts[x[2]] = list(x[3:])
        elif op == OP_VARIABLE and x[3] == STORAGE_UNIFORM:
            uvars.append((x[2], ptrs[x[1]][1]))
        elif op == OP_FUNCTION:
            fn = x[2]
            calls[fn], uses[fn] = set(), set()
        elif op == OP_FUNCTION_END:
            fn = None
        elif fn is not None:
            if op == OP_FUNCTION_CALL:
                calls[fn].add(x[3])
            uses[fn].update(x[1:])
    if entry_id is None:
        raise ValueError(f"SPIR-V has no entry point {entry}")
    reach, todo = set(), [entry_id]
    while todo:
        f = todo.pop()
        if f not in reach:
            reach.add(f)
            todo += calls.get(f, ())
    used = set().union(*(uses.get(f, set()) for f in reach))

    def size(t, matrix_stride=None, row_major=False):
        op, a = types[t]
        if op in (OP_TYPE_INT, OP_TYPE_FLOAT):
            return a[0] // 8
        if op == OP_TYPE_BOOL:
            return 4
        if op == OP_TYPE_VECTOR:
            return size(a[0]) * a[1]
        if op == OP_TYPE_MATRIX:
            rows = types[a[0]][1][1]
            return (rows if row_major else a[1]) * matrix_stride
        if op == OP_TYPE_ARRAY:
            return consts[a[1]] * dec[t][DEC_ARRAY_STRIDE][0]
        if op == OP_TYPE_RUNTIME_ARRAY:
            return 0
        end = 0
        for m, mt in enumerate(a):
            d = mdec.get((t, m), {})
            ms = size(mt, (d.get(DEC_MATRIX_STRIDE) or [None])[0], DEC_ROW_MAJOR in d)
            if ms == 0:
                return 0
            end = max(end, d[DEC_OFFSET][0] + ms)
        return end
    blocks = []
    for var, t in uvars:
        if var not in used:
            continue
        while types[t][0] in (OP_TYPE_ARRAY, OP_TYPE_RUNTIME_ARRAY):
            t = types[t][1][0]
        if DEC_BLOCK not in dec.get(t, {}):
            continue
        d = dec.get(var, {})
        blocks.append((d.get(DEC_SET, [0])[0], d.get(DEC_BINDING, [0])[0], size(t)))
    size3 = None
    if local and local[0] == entry_id:
        size3 = local[1]
    elif local_ids and local_ids[0] == entry_id:
        size3 = [consts[c] for c in local_ids[1]]
    builtin = next((v for v, d in dec.items() if d.get(DEC_BUILTIN) == [BUILTIN_WORKGROUP_SIZE]), None)
    if builtin is not None and isinstance(consts.get(builtin), list):
        size3 = [consts[c] for c in consts[builtin]]
    return {"blocks": blocks, "local_size": size3}


def discards(raw):
    w, _ = _words(raw)
    return any(op in KILLS or op == OP_DEMOTE for _, op in _instructions(w))


def without_discard(raw):
    w, swapped = _words(raw)
    voids, void_function, changed = set(), True, False
    for i, op in list(_instructions(w)):
        if op == OP_TYPE_VOID:
            voids.add(w[i + 1])
        elif op == OP_FUNCTION:
            void_function = w[i + 1] in voids
        elif op in KILLS:
            if not void_function:
                return None
            w[i] = (1 << 16) | OP_RETURN
            changed = True
        elif op == OP_DEMOTE:
            w[i] = (1 << 16) | OP_NOP
            changed = True
    if not changed:
        return None
    if swapped:
        w.byteswap()
    return w.tobytes()


OP_STORE, OP_CONSTANT_NULL, OP_ACCESS_CHAIN = 62, 46, 65
STORAGE_OUTPUT, BUILTIN_POSITION = 3, 0


def _module(raw):
    w, swapped = _words(raw)
    insts = [(op, list(w[i:i + (w[i] >> 16)])) for i, op in _instructions(w)]
    m = {"w": w, "swapped": swapped, "insts": insts,
         "entry": next(x[2] for op, x in insts if op == OP_ENTRY_POINT),
         "builtin": {x[1]: x[3] for op, x in insts if op == OP_DECORATE and x[2] == DEC_BUILTIN},
         "member_builtin": {(x[1], x[2]): x[4] for op, x in insts if op == OP_MEMBER_DECORATE and x[3] == DEC_BUILTIN},
         "pointee": {x[1]: x[3] for op, x in insts if op == OP_TYPE_POINTER and x[2] == STORAGE_OUTPUT},
         "structs": {x[1]: x[2:] for op, x in insts if op == OP_TYPE_STRUCT}}
    m["outputs"] = [(x[2], m["pointee"][x[1]]) for op, x in insts if op == OP_VARIABLE and x[3] == STORAGE_OUTPUT]
    return m


def _rebuild(m, bound, globals_, at_return):
    """globals_: words placed before the first function; at_return(): words placed before each return of the entry
    point."""
    out, fn, placed = list(m["w"][:5]), None, False
    for op, x in m["insts"]:
        if op == OP_FUNCTION:
            if not placed:
                out += globals_
                placed = True
            fn = x[2]
        elif op == OP_RETURN and fn == m["entry"]:
            out += at_return()
        elif op == OP_FUNCTION_END:
            fn = None
        out += x
    out[3] = bound[0]
    res = array("I", out)
    if m["swapped"]:
        res.byteswap()
    return res.tobytes()


def _plain_outputs(m):
    return [(v, t) for v, t in m["outputs"] if v not in m["builtin"]
            and not any(k[0] == t for k in m["member_builtin"])]


def zero_outputs(raw):
    """-> the SPIR-V with zero stored into every non-builtin output before each return of its entry point: a fragment
    shader's colour math is dropped by the driver (discard and depth stay);
    None if it has no such output."""
    m = _module(raw)
    outs = _plain_outputs(m)
    if not outs:
        return None
    bound, nulls, glob = [m["w"][3]], {}, []
    for _, t in outs:
        if t not in nulls:
            nulls[t] = bound[0]
            glob += [(3 << 16) | OP_CONSTANT_NULL, t, bound[0]]
            bound[0] += 1
    return _rebuild(m, bound, glob, lambda: [w for v, t in outs for w in ((3 << 16) | OP_STORE, v, nulls[t])])



def off_screen(raw):
    """-> the vertex SPIR-V whose position is a constant outside the clip volume (every triangle culled: no position
    math, tiling, varyings or fragments left), None if it writes no position."""
    m = _module(raw)
    types = {x[1]: (op, x) for op, x in m["insts"] if op in (OP_TYPE_INT, OP_TYPE_FLOAT, OP_TYPE_VECTOR)}
    target = None
    for v, t in m["outputs"]:
        if m["builtin"].get(v) == BUILTIN_POSITION:
            target = (v, t, None)
        for (st, k), b in m["member_builtin"].items():
            if st == t and b == BUILTIN_POSITION:
                target = (v, m["structs"][st][k], k)
    if target is None:
        return None
    var, vec, member = target
    if types.get(vec, (None,))[0] != OP_TYPE_VECTOR:
        return None
    flt = types[vec][1][2]
    bound, glob = [m["w"][3]], []

    def new():
        bound[0] += 1
        return bound[0] - 1
    two, one, pos = new(), new(), new()
    glob += [(4 << 16) | OP_CONSTANT, flt, two, 0x40000000, (4 << 16) | OP_CONSTANT, flt, one, 0x3F800000,
             (7 << 16) | OP_CONSTANT_COMPOSITE, vec, pos, two, two, two, one]
    if member is None:
        return _rebuild(m, bound, glob, lambda: [(3 << 16) | OP_STORE, var, pos])
    ptr = next((x[1] for op, x in m["insts"] if op == OP_TYPE_POINTER and x[2] == STORAGE_OUTPUT and x[3] == vec), None)
    if ptr is None:
        ptr = new()
        glob += [(4 << 16) | OP_TYPE_POINTER, ptr, STORAGE_OUTPUT, vec]
    sint = next((x[1] for op, x in m["insts"] if op == OP_TYPE_INT and x[2] == 32 and x[3] == 1), None)
    if sint is None:
        sint = new()
        glob += [(4 << 16) | OP_TYPE_INT, sint, 32, 1]
    idx = new()
    glob += [(4 << 16) | OP_CONSTANT, sint, idx, member]

    def at_return():
        chain = new()
        return [(5 << 16) | OP_ACCESS_CHAIN, ptr, chain, var, idx, (3 << 16) | OP_STORE, chain, pos]
    return _rebuild(m, bound, glob, at_return)
