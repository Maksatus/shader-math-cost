"""SPIR-V words (stdlib only: imported inside RenderDoc's python too)."""
import struct

MAGIC = 0x07230203


def parse(data):
    """-> (header words, [instruction words]) of little-endian SPIR-V bytes, None if it is not."""
    if len(data) % 4 or len(data) < 20:
        return None
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    if words[0] != MAGIC:
        return None
    insts, i = [], 5
    while i < len(words):
        wc = words[i] >> 16
        if wc == 0:
            return None
        insts.append(words[i:i + wc])
        i += wc
    return words[:5], insts
