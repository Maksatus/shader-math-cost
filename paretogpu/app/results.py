"""Writes <kind>.json + <kind>.html (unless kind.own_page) + <kind>.result.json; kind: features/spec.py ResultKind."""
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
    """-> path of its page."""
    if not kind.own_page:
        html.write_result(doc, folder, kind.name, kind.title(doc))
    results.write_meta(folder, meta_of(kind, doc, folder, snapshot))
    return os.path.join(folder, kind.page)


def adopt(kinds):
    """Writes the missing <kind>.result.json for results in out/ that have only their data."""
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
