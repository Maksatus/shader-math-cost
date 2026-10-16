"""Dynamically indexed uniform buffers measured as storage buffers. malioc charges a uniform-block load with a
non-constant index as a divergent gather (3x a storage-buffer load of the same data); on G78 such indices (loop
counters, light / probe indices) are warp-coherent and the LS counters show the storage-buffer price (K3.1).
The dynamic access chains get a BufferBlock twin of their block (same layout, unused binding); constant-index
accesses keep the original block and stay in uniform registers."""
import struct

OP_DECORATE, OP_MEMBER_DECORATE, OP_TYPE_STRUCT, OP_TYPE_POINTER, OP_VARIABLE = 71, 72, 30, 32, 59
OP_CONSTANT, OP_SPEC_CONSTANT, OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN, OP_FUNCTION = 43, 50, 65, 66, 54
UNIFORM = 2
BLOCK, BUFFER_BLOCK, NON_WRITABLE, BINDING, DESCRIPTOR_SET = 2, 3, 24, 33, 34


def _parse(data):
    if len(data) % 4 or len(data) < 20:
        return None
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    if words[0] != 0x07230203:
        return None
    insts, i = [], 5
    while i < len(words):
        wc = words[i] >> 16
        if wc == 0:
            return None
        insts.append(words[i:i + wc])
        i += wc
    return words[:5], insts


def _inst(op, *ops):
    return [(len(ops) + 1) << 16 | op, *ops]


def dynamic_as_buffer(data: bytes) -> bytes:
    """-> data with every dynamically indexed uniform block read through a storage-buffer twin; data unchanged when
    there is none (or it is not SPIR-V 1.0-1.2, where BufferBlock exists)."""
    parsed = _parse(data)
    if parsed is None or parsed[0][1] > 0x10200:
        return data
    header, insts = parsed
    op = lambda x: x[0] & 0xFFFF
    consts = {x[2] for x in insts if op(x) in (OP_CONSTANT, OP_SPEC_CONSTANT)}
    blocks = {x[1] for x in insts if op(x) == OP_DECORATE and x[2] == BLOCK}
    pointers = {x[1]: (x[2], x[3]) for x in insts if op(x) == OP_TYPE_POINTER}
    variables = {x[2]: pointers.get(x[1]) for x in insts if op(x) == OP_VARIABLE and x[3] == UNIFORM}
    dynamic = [i for i, x in enumerate(insts) if op(x) in (OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN)
               and (variables.get(x[3]) or (None, None))[1] in blocks and any(v not in consts for v in x[4:])]
    if not dynamic:
        return data
    bound = header[3]
    twins = {}
    for v in dict.fromkeys(insts[i][3] for i in dynamic):
        twins[v] = (bound, bound + 1, bound + 2)  # struct, pointer, variable
        bound += 3
    deco = {}
    for x in insts:
        if op(x) == OP_DECORATE and x[2] in (BINDING, DESCRIPTOR_SET):
            deco.setdefault(x[1], {})[x[2]] = x[3]
    free = 1 + max([b for d in deco.values() for k, b in d.items() if k == BINDING] or [0])
    notes, types = [], {}
    for v, (s, p, nv) in twins.items():
        old = variables[v][1]
        notes += [_inst(OP_MEMBER_DECORATE, s, *x[2:]) for x in insts if op(x) == OP_MEMBER_DECORATE and x[1] == old]
        notes += [_inst(OP_DECORATE, s, BUFFER_BLOCK), _inst(OP_DECORATE, nv, DESCRIPTOR_SET,
                                                             deco.get(v, {}).get(DESCRIPTOR_SET, 0)),
                  _inst(OP_DECORATE, nv, BINDING, free)]
        free += 1
        members = next(x[2:] for x in insts if op(x) == OP_TYPE_STRUCT and x[1] == old)
        notes += [_inst(OP_MEMBER_DECORATE, s, m, NON_WRITABLE) for m in range(len(members))]
        types[v] = [_inst(OP_TYPE_STRUCT, s, *members), _inst(OP_TYPE_POINTER, p, UNIFORM, s),
                    _inst(OP_VARIABLE, p, nv, UNIFORM)]
    for i in dynamic:
        insts[i] = insts[i][:3] + [twins[insts[i][3]][2]] + insts[i][4:]
    last_note = max(i for i, x in enumerate(insts) if op(x) in (OP_DECORATE, OP_MEMBER_DECORATE))
    out = []
    for i, x in enumerate(insts):
        out.append(x)
        if i == last_note:
            out += notes
        if op(x) == OP_VARIABLE and x[2] in types:
            out += types[x[2]]
    header = header[:3] + [bound] + header[4:]
    words = header + [w for x in out for w in x]
    return struct.pack(f"<{len(words)}I", *words)
