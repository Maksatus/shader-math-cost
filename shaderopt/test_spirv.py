"""spirv.assemble() on `glslang -H` text of corpus/synthetic shaders (testdata/spirv/).

Each .txt is what `glslang -V -H` printed, the .spv is the binary glslang wrote:
assembling the text must give exactly the same bytes.
Run: python -m unittest shaderopt.test_spirv   (from the repository root)
"""
import glob
import os
import unittest

from shaderopt import spirv

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata", "spirv")


def grammar_found():
    try:
        spirv.find_grammar()
        return True
    except spirv.SpirvTextError:
        return False


@unittest.skipUnless(grammar_found(), "spirv.core.grammar.json not found")
class AssembleTest(unittest.TestCase):
    def test_same_bytes_as_glslang(self):
        files = sorted(glob.glob(os.path.join(DATA, "*.txt")))
        self.assertTrue(files)
        for txt in files:
            with self.subTest(os.path.basename(txt)):
                with open(txt, encoding="utf-8") as f:
                    got = spirv.assemble(f.read())
                with open(txt[:-4] + ".spv", "rb") as f:
                    self.assertEqual(got, f.read())

    def test_errors(self):
        with self.assertRaisesRegex(spirv.SpirvTextError, "unknown instruction"):
            spirv.assemble("   Capability Shader\n   NoSuchOp 1 2\n")
        with self.assertRaisesRegex(spirv.SpirvTextError, "no SPIR-V instructions"):
            spirv.assemble("// Module Version 10000\n")


if __name__ == "__main__":
    unittest.main()
