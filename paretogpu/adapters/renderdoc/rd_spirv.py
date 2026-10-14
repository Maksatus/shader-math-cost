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
