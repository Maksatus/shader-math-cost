"""Results in paretogpu/out: every <folder>/<kind>.result.json (model/result.py) of a snapshot folder or of an
analysis folder (out/_<kind>/<id>/).
"""
import json
import os

from paretogpu.model.result import SUFFIX, ResultMeta
from paretogpu.store import workspace

SKIP = ("_ui", "_regression")


def meta_path(folder, kind):
    return os.path.join(folder, kind + SUFFIX)


def write_meta(folder, meta: ResultMeta):
    os.makedirs(folder, exist_ok=True)
    tmp = meta_path(folder, meta["kind"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({k: v for k, v in meta.items() if k != "folder"}, f, indent=1, ensure_ascii=False)
    os.replace(tmp, meta_path(folder, meta["kind"]))


def read_meta(folder, kind):
    try:
        with open(meta_path(folder, kind), encoding="utf-8") as f:
            return {**json.load(f), "folder": os.path.abspath(folder)}
    except (OSError, ValueError):
        return None


def folders():
    """The folders of out/ results can be in: out/<name>/ and out/_<kind>/<id>/."""
    if not os.path.isdir(workspace.OUT):
        return
    for name in sorted(os.listdir(workspace.OUT)):
        d = os.path.join(workspace.OUT, name)
        if not os.path.isdir(d) or name in SKIP or name.startswith("variants"):
            continue
        if name.startswith("_"):
            for sub in sorted(os.listdir(d)):
                if os.path.isdir(os.path.join(d, sub)):
                    yield os.path.join(d, sub)
        else:
            yield d


def scan(kind=None):
    """[ResultMeta] of the results in out/ (of one kind), newest first."""
    out = []
    for d in folders():
        for fn in os.listdir(d):
            if fn.endswith(SUFFIX) and (kind is None or fn == kind + SUFFIX):
                m = read_meta(d, fn[:-len(SUFFIX)])
                if m:
                    out.append(m)
    return sorted(out, key=lambda m: -(m.get("created") or 0))
