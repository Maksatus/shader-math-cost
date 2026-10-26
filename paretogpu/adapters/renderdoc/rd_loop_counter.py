"""Fragment SPIR-V that writes loop trip counts instead of its colour (rd_draws.py); imported inside RenderDoc's python
(stdlib and core/loops only). counting(raw, continues, scale) -> SPIR-V whose output at Location 0 is
((n_a + 1) * scale, (n_b + 1) * scale, (n_c + 1) * scale, 1) (alpha 1 keeps blending onto a cleared target exact): n =
runs of a loop's continue block, the only way back to its header. Pixels the draw wrote = non-zero on a cleared
target."""
import struct

from paretogpu.core import loops as model
from paretogpu.core import spirv

OP_ENTRY_POINT, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_POINTER, OP_CONSTANT = 15, 22, 23, 32, 43
OP_FUNCTION, OP_FUNCTION_END, OP_VARIABLE, OP_LOAD, OP_STORE, OP_DECORATE = 54, 56, 59, 61, 62, 71
OP_COMPOSITE_CONSTRUCT, OP_FADD, OP_FMUL, OP_PHI, OP_LABEL, OP_RETURN = 80, 129, 133, 245, 248, 253
OP_LINE, OP_NO_LINE = 8, 317
STORAGE_OUTPUT, STORAGE_PRIVATE, DEC_LOCATION, MODEL_FRAGMENT = 3, 6, 30, 4


def outer_loops(raw):
    """-> ([continue label per outer loop], [holds a nested loop]) in core/loops order (the pricing's)."""
    parsed = spirv.parse(raw)
    plans = model._loop_plans(parsed[1]) if parsed else None
    if not plans or not plans[1]:
        return [], []
    insts, found = parsed[1], {}
    for fb, pos, lps, owner, outer in plans[0]:
        for i, (_, _, lm, _) in enumerate(lps):
            if i in outer:
                found[owner[i]] = (insts[lm][2], any(owner[j] == owner[i] and j not in outer for j in range(len(lps))))
    ks = sorted(found)
    return [found[k][0] for k in ks], [found[k][1] for k in ks]


def _inst(op, *words):
    return [((len(words) + 1) << 16) | op, *words]


def counting(raw, continues, scale=1.0):
    """continues: one to three continue-block label ids. -> SPIR-V bytes; ValueError if the output is not float."""
    parsed = spirv.parse(bytes(raw))
    if parsed is None:
        raise ValueError("not SPIR-V")
    header, insts = parsed
    bound = header[3]

    def new():
        nonlocal bound
        bound += 1
        return bound - 1
    ops = [x[0] & 0xFFFF for x in insts]
    entry = next(x for x, op in zip(insts, ops) if op == OP_ENTRY_POINT and x[1] == MODEL_FRAGMENT)
    main = entry[2]
    location0 = {x[1] for x, op in zip(insts, ops) if op == OP_DECORATE and x[2] == DEC_LOCATION and x[3] == 0}
    pointers = {x[1]: (x[2], x[3]) for x, op in zip(insts, ops) if op == OP_TYPE_POINTER}
    out = next((x for x, op in zip(insts, ops) if op == OP_VARIABLE and x[3] == STORAGE_OUTPUT and x[2] in location0),
               None)
    if out is None:
        raise ValueError("no colour output at Location 0")
    out_type = pointers[out[1]][1]
    types = {x[1]: x for x, op in zip(insts, ops) if op in (OP_TYPE_FLOAT, OP_TYPE_VECTOR)}
    vec = types.get(out_type)
    if vec is None or vec[0] & 0xFFFF != OP_TYPE_VECTOR or types[vec[2]][2] != 32:
        raise ValueError("the colour output is not a 32-bit float vector")
    width, f32 = vec[3], vec[2]

    pf, c0, c1, cs = new(), new(), new(), new()
    counters = [new() for _ in continues]
    fl = lambda v: struct.unpack("<I", struct.pack("<f", v))[0]
    decl = [_inst(OP_TYPE_POINTER, pf, STORAGE_PRIVATE, f32), _inst(OP_CONSTANT, f32, c0, fl(0.0)),
            _inst(OP_CONSTANT, f32, c1, fl(1.0)), _inst(OP_CONSTANT, f32, cs, fl(scale))]
    decl += [_inst(OP_VARIABLE, pf, c, STORAGE_PRIVATE, c1) for c in counters]  # n + 1 from the start

    body, fn, labels = [], None, {}
    first_fn = next(k for k, op in enumerate(ops) if op == OP_FUNCTION)
    for k, (x, op) in enumerate(zip(insts, ops)):
        if k == first_fn:
            body += decl
        if op == OP_FUNCTION:
            fn = x[2]
        if op == OP_RETURN and fn == main:
            parts = []
            for c in counters + [None] * (3 - len(counters)):
                if c is None:
                    parts.append(c1)
                    continue
                v, s = new(), new()
                body += [_inst(OP_LOAD, f32, v, c), _inst(OP_FMUL, f32, s, v, cs)]
                parts.append(s)
            comps = (parts + [c1])[:width]
            res = new()
            body += [_inst(OP_COMPOSITE_CONSTRUCT, out_type, res, *comps), _inst(OP_STORE, out[2], res)]
        body.append(x)
        if op == OP_LABEL and x[1] in continues:
            labels[x[1]] = len(body)
    for lab, c in sorted(zip(continues, counters), key=lambda t: -labels[t[0]]):
        at = labels[lab]
        while at < len(body) and body[at][0] & 0xFFFF in (OP_PHI, OP_LINE, OP_NO_LINE):
            at += 1
        v, s = new(), new()
        body[at:at] = [_inst(OP_LOAD, f32, v, c), _inst(OP_FADD, f32, s, v, c1), _inst(OP_STORE, c, s)]
    if len(labels) != len(continues):
        raise ValueError("a continue block was not found")
    if header[1] >= 0x10400:  # from SPIR-V 1.4 every global the entry point uses is in its interface
        k = next(i for i, x in enumerate(body) if x is entry)
        body[k] = entry + counters
        body[k][0] = (len(body[k]) << 16) | OP_ENTRY_POINT
    header = header[:3] + [bound] + header[4:]
    words = header + [w for x in body for w in x]
    return struct.pack(f"<{len(words)}I", *words)
