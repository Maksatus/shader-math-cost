"""app/results.py and store/results.py: a published result is described and listed the same way for every kind;
results made before descriptions existed are adopted. Temporary out/ folder, synthetic documents.

Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from paretogpu.app import results
from paretogpu.features import RESULT_KINDS
from paretogpu.features.matcompare import RESULT as MATCOMPARE
from paretogpu.store import results as store
from paretogpu.store import workspace

MAT = {"material": "M", "path": "Assets/M.mat", "shader": "S", "passes": []}


class ResultsTest(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.patch = mock.patch.object(workspace, "OUT", self.out)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.out, ignore_errors=True)

    def test_publish_and_list(self):
        folder = os.path.join(self.out, "_matcompare", "20261009_120000")
        doc = {"kind": "materials", "project": "P", "computed_at": 5.0, "a": MAT, "b": {**MAT, "material": "N"}}
        page = results.publish(MATCOMPARE, doc, folder)
        self.assertTrue(os.path.exists(page) and os.path.exists(os.path.join(folder, "matcompare.json")))
        [m] = store.scan("matcompare")
        self.assertEqual((m["id"], m["title"], m["created"], m["project"]), ("20261009_120000", "Материалы: M → N", 5.0, "P"))
        self.assertEqual(m["summary"]["b"]["material"], "N")
        self.assertEqual(store.scan("hotspots"), [])

    def test_old_results_are_adopted(self):
        snap = os.path.join(self.out, "frame_x_20261009_120000")
        os.makedirs(snap)
        with open(os.path.join(snap, "frame_events.json"), "w", encoding="utf-8") as f:
            json.dump({"project": "P", "events": []}, f)
        with open(os.path.join(snap, "frame_cost.json"), "w", encoding="utf-8") as f:
            json.dump({"computed_at": 7.0, "main_core": "Mali-G78", "frame_dir": snap, "api": "vulkan",
                       "totals": {"Mali-G78": {"total": 10.0}}, "coverage": {"draws_priced": 3}}, f)
        results.adopt(RESULT_KINDS)
        [m] = store.scan()
        self.assertEqual((m["kind"], m["snapshot"], m["summary"]["total"], m["summary"]["priced"]),
                         ("cost", "frame_x_20261009_120000", 10.0, 3))
        results.adopt(RESULT_KINDS)
        self.assertEqual(len(store.scan()), 1)


if __name__ == "__main__":
    unittest.main()
