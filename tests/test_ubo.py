"""core/ubo.py: dynamically indexed uniform blocks read through a storage-buffer twin."""
import os
import subprocess
import tempfile
import unittest

from paretogpu.core import spirv, ubo
from tests.test_loops import CLUSTER_SPV, LIGHTS_SPV, read, spirv_val


def chains(data):
    header, insts = spirv.parse(data)
    op = lambda x: x[0] & 0xFFFF
    consts = {x[2] for x in insts if op(x) == ubo.OP_CONSTANT}
    blocks = {x[1]: x[2] for x in insts if op(x) == ubo.OP_DECORATE and x[2] in (ubo.BLOCK, ubo.BUFFER_BLOCK)}
    pointers = {x[1]: x[3] for x in insts if op(x) == ubo.OP_TYPE_POINTER}
    var = {x[2]: blocks.get(pointers.get(x[1])) for x in insts if op(x) == ubo.OP_VARIABLE}
    return [(var.get(x[3]), all(v in consts for v in x[4:])) for x in insts if op(x) == ubo.OP_ACCESS_CHAIN
            and var.get(x[3]) is not None]


class DynamicUniforms(unittest.TestCase):
    def test_dynamic_chains_read_the_twin(self):
        src = read(CLUSTER_SPV, True)
        out = ubo.dynamic_as_buffer(src)
        before, after = chains(src), chains(out)
        self.assertIn((ubo.BLOCK, False), before)  # _Waves[i]
        self.assertNotIn((ubo.BLOCK, False), after)
        self.assertIn((ubo.BUFFER_BLOCK, False), after)
        self.assertEqual(sum(1 for c in before if c == (ubo.BLOCK, True)),
                         sum(1 for c in after if c == (ubo.BLOCK, True)))

    def test_valid_module(self):
        val = spirv_val()
        if not val:
            self.skipTest("no spirv-val")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.spv")
            with open(p, "wb") as f:
                f.write(ubo.dynamic_as_buffer(read(CLUSTER_SPV, True)))
            r = subprocess.run([val, p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_constant_indices_unchanged(self):
        src = read(LIGHTS_SPV, True)
        self.assertEqual(ubo.dynamic_as_buffer(src), src)

    def test_not_spirv(self):
        self.assertEqual(ubo.dynamic_as_buffer(b"void main() {}"), b"void main() {}")


if __name__ == "__main__":
    unittest.main()
