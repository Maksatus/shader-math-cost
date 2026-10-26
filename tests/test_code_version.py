"""app/code_version.py and compare's code_differs warning."""
import os
import tempfile
import unittest

from paretogpu.app import code_version
from paretogpu.core import compare


class CodeVersionTest(unittest.TestCase):
    def version(self, text):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.py"), "w", encoding="utf-8") as f:
                f.write(text)
            return code_version.of(["m.py"], d)

    def test_comments_and_docstrings_do_not_count(self):
        a = self.version('def f(x):\n    return x * 2\n')
        self.assertEqual(a, self.version('def f(x):\n    """Twice."""\n    # doubled\n    return x * 2\n'))
        self.assertNotEqual(a, self.version('def f(x):\n    return x * 3\n'))

    def test_the_package_has_one(self):
        self.assertRegex(code_version.current(), r"^[0-9a-f]{12}$")

    def test_compare_warns_about_two_versions(self):
        def run(v):
            return {"code_version": v, "cores": [{"name": "Mali-G78"}], "main_core": "Mali-G78", "totals": {},
                    "events": []}
        codes = lambda a, b: [w["code"] for w in compare.compare(run(a), run(b))["warnings"]]
        self.assertIn("code_differs", codes("aaa", "bbb"))
        self.assertIn("code_differs", codes(None, "bbb"))
        self.assertNotIn("code_differs", codes("aaa", "aaa"))


if __name__ == "__main__":
    unittest.main()
