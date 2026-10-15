"""Compiled variants folder of a project:
  <shader>/ SPIR-V .vert.spv/.frag.spv with manifest.json
  variants.json: every variant ever seen (files, errors, fingerprint); _compiled/: raw output of the last Unity run
"""
import json
import os

from paretogpu.model.variant import EXT, SHORT, STAGE, VariantKey

RAW = "_compiled"
INDEX = "variants.json"


def raw_dir(root):
    return os.path.join(root, RAW)


def load_manifests(root, fingerprints=None):
    """-> {key: {stage: relative file}}. Fills `fingerprints` {key: fp, None if files disagree}."""
    have = {}
    if not os.path.isdir(root):
        return have
    for d in os.listdir(root):
        path = os.path.join(root, d, "manifest.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for m in json.load(f):
                if "subshader" not in m or not os.path.exists(os.path.join(root, d, m["file"])):
                    continue
                k = VariantKey(m["shader"], m["subshader"], m["pass_index"], m["pass"], tuple(m["keywords"]))
                have.setdefault(k, {})[SHORT[m["stage"]]] = f"{d}/{m['file']}"
                if fingerprints is not None:
                    fp = m.get("fingerprint")
                    fingerprints[k] = fp if fingerprints.get(k, fp) == fp else None
    return have


def place(root, raw, keys, result):
    """-> {key: [errors]}."""
    manifests, errors = {}, {}
    for v in result["variants"]:
        k = keys[v["id"]]
        errors[k] = list(v["errors"])
        for x in v["files"]:
            stage = x["stage"]
            with open(os.path.join(raw, x["file"]), "rb") as f:
                data = f.read()
            sub = k.folder
            fn = k.base_name + EXT[stage]
            os.makedirs(os.path.join(root, sub), exist_ok=True)
            with open(os.path.join(root, sub, fn), "wb") as f:
                f.write(data)
            manifests.setdefault(sub, []).append({
                "file": fn, "shader": k.shader, "pass": k.pass_name, "subshader": k.subshader,
                "pass_index": k.pass_index, "keywords": list(k.keywords), "stage": STAGE[stage],
                "fingerprint": v.get("fingerprint")})
    for sub, entries in manifests.items():
        path = os.path.join(root, sub, "manifest.json")
        old = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        names = {e["file"] for e in entries}
        _write_json(path, [e for e in old if e["file"] not in names] + entries)
    return errors


def _write_json(path, data):
    """Atomic: a run stopped while writing leaves the old file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def load_index(root):
    index = {}
    path = os.path.join(root, INDEX)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for x in json.load(f)["variants"]:
                index[VariantKey.from_list(x["key"])] = x
    return index


def save_index(root, index):
    _write_json(os.path.join(root, INDEX), {"variants": list(index.values())})
