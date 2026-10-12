"""Every phase id the code reports (views/reporter.phase / skip, app/engine.Rule.phase, features: Command.phases) is
declared in model/phases.py, so the UI has a name for it.

Run: python -m unittest discover tests   (from the repository root)
"""
import ast
import os
import unittest

from paretogpu.features import COMMANDS
from paretogpu.model.phases import PHASES

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "paretogpu")
SKIP = ("out", "real", "__pycache__", "static")


def reported():
    """{(module, line, id)} of every literal phase id in rep.phase("..."), rep.skip("...") and `phase = "..."`."""
    out = set()
    for d, dirs, files in os.walk(PKG):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(d, fn)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            rel = os.path.relpath(path, PKG).replace(os.sep, "/")
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("phase", "skip") and node.args
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    out.add((rel, node.lineno, node.args[0].value))
                if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == "phase" and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    out.add((rel, node.lineno, node.value.value))
    return out


class PhasesTest(unittest.TestCase):
    def test_reported_phases_are_declared(self):
        bad = sorted(f"{m}:{line} reports phase {pid!r}" for m, line, pid in reported() if pid not in PHASES)
        self.assertEqual(bad, [])

    def test_command_phases_are_declared(self):
        for c in COMMANDS:
            for values in ({}, {"materials": True, "measure": True, "site_only": True}):
                for pid in c.phases_of(values):
                    self.assertIn(pid, PHASES, f"{c.name}: phase {pid!r}")

    def test_every_phase_has_a_label(self):
        for pid, (label, unit) in PHASES.items():
            self.assertTrue(label, pid)
            self.assertTrue(unit is None or isinstance(unit, str), pid)


if __name__ == "__main__":
    unittest.main()
