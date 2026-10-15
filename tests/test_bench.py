"""app/bench.py arithmetic per core class; adapters/malioc.py cache entry check."""
import unittest
from unittest import mock

from paretogpu.adapters import malioc
from paretogpu.app import bench


class AluTest(unittest.TestCase):
    S = {"arith_fma": 3.0, "arith_cvt": 0.05, "arith_sfu": 3.0, "texture": 9.0}

    def test_max_of_pipes_on_g78(self):
        self.assertEqual(bench._alu(self.S, "Mali-G78"), 3.0)

    def test_shared_issue_on_g710(self):
        self.assertAlmostEqual(bench._alu(self.S, "Mali-G710"), 3.0 + 0.05 + 0.75)
        self.assertEqual(bench._alu({"arith_fma": 0.1, "arith_cvt": 0.0, "arith_sfu": 2.0}, "Mali-G610"), 2.0)

    def test_bifrost(self):
        self.assertEqual(bench._alu({"arithmetic": 1.5, "texture": 4.0}, "Mali-G52"), 1.5)


class CacheEntryTest(unittest.TestCase):
    def raw(self, version=(2026, 5, 0), core="Mali-G78", api="Vulkan"):
        return {"producer": {"version": list(version)},
                "shaders": [{"hardware": {"core": core}, "shader": {"api": api}}]}

    def setUp(self):
        p = mock.patch.dict(malioc._versions, {"m": "2026.5.0"})
        p.start()
        self.addCleanup(p.stop)

    def test_same_version_core_and_api(self):
        self.assertTrue(malioc.entry_matches({"raw": self.raw()}, "Mali-G78", "vulkan", "m"))
        self.assertTrue(malioc.entry_matches({"malioc": "2026.5.0"}, "Mali-G78", "gles", "m"))
        self.assertTrue(malioc.entry_matches({"v": 2}, "Mali-G78", "gles", "m"))  # v2: nothing to check

    def test_other_version_core_or_api_is_a_miss(self):
        self.assertFalse(malioc.entry_matches({"raw": self.raw((8, 4, 0))}, "Mali-G78", "vulkan", "m"))
        self.assertFalse(malioc.entry_matches({"malioc": "2026.4.0"}, "Mali-G78", "vulkan", "m"))
        self.assertFalse(malioc.entry_matches({"raw": self.raw(core="Mali-G57")}, "Mali-G78", "vulkan", "m"))
        self.assertFalse(malioc.entry_matches({"raw": self.raw(api="OpenGL ES")}, "Mali-G78", "vulkan", "m"))


if __name__ == "__main__":
    unittest.main()
