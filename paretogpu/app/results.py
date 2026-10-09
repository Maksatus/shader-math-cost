"""Publishing a result (model/result.py): its data, its page and the description every list of results reads.

  publish(kind, doc, folder, snapshot)  writes <kind>.json + <kind>.html (unless the command writes its own page)
                                        and <kind>.result.json
  adopt(kinds)                          describes the results made before descriptions existed (once)
kind: a ResultKind of the command (features/spec.py): name, title(doc), summary(doc), data, page, own_page.
"""
import json
import os
import time

from paretogpu.model.result import ResultMeta
from paretogpu.store import results, workspace
from paretogpu.views import html


def meta_of(kind, doc, folder, snapshot=None) -> ResultMeta:
    folder = os.path.abspath(folder)
    return {"kind": kind.name, "id": os.path.basename(folder) + (f"/{kind.name}" if snapshot else ""),
            "title": kind.title(doc), "created": doc.get("computed_at") or time.time(),
            "project": doc.get("project") or (doc.get("frame") or {}).get("project"), "snapshot": snapshot,
            "data": kind.data, "page": kind.page, "summary": kind.summary(doc)}


def publish(kind, doc, folder, snapshot=None):
    """Write the result; returns the path of its page."""
    if not kind.own_page:
        html.write_result(doc, folder, kind.name, kind.title(doc))
    results.write_meta(folder, meta_of(kind, doc, folder, snapshot))
    return os.path.join(folder, kind.page)


def adopt(kinds):
    """Results in out/ that have their data but no description yet (made before descriptions existed) get one."""
    for d in results.folders():
        snapshot = os.path.basename(d) if workspace.is_snapshot(d) else None
        for kind in kinds:
            if os.path.exists(os.path.join(d, kind.data)) and not os.path.exists(results.meta_path(d, kind.name)):
                try:
                    with open(os.path.join(d, kind.data), encoding="utf-8") as f:
                        doc = json.load(f)
                    results.write_meta(d, meta_of(kind, doc, d, snapshot))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
