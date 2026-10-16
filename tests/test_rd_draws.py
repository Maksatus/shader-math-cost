"""rd_draws: the depth texels a discarding draw changed (renderdoc stubbed)."""
import struct
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

with mock.patch.dict(sys.modules, {"renderdoc": mock.MagicMock()}):
    from paretogpu.adapters.renderdoc import rd_draws


def tex(w, h, name):
    return SimpleNamespace(width=w, height=h, format=SimpleNamespace(Name=lambda: name))


class WrittenTest(unittest.TestCase):
    def test_d32s8_compares_depth_not_stencil(self):
        before = b"".join(struct.pack("<fI", 0.0, 0) for _ in range(4))
        after = struct.pack("<fI", 0.5, 0) + struct.pack("<fI", 0.0, 7) + before[16:]
        self.assertEqual(rd_draws.written(before, after, tex(2, 2, "D32S8")), bytes([0b0001]))

    def test_d16(self):
        before, after = struct.pack("<4H", 0, 0, 0, 0), struct.pack("<4H", 0, 0, 9, 0)
        self.assertEqual(rd_draws.written(before, after, tex(4, 1, "D16")), bytes([0b0100]))

    def test_bits_past_a_byte(self):
        before = struct.pack("<10H", *[0] * 10)
        after = struct.pack("<10H", *([0] * 9 + [5]))
        self.assertEqual(rd_draws.written(before, after, tex(10, 1, "D16")), bytes([0, 0b10]))

    def test_unknown_layout(self):
        self.assertIsNone(rd_draws.written(b"\0" * 12, b"\0" * 12, tex(2, 2, "D24")))


if __name__ == "__main__":
    unittest.main()
