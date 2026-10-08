"""The folder of compiled shader variants of a project (shared by its frames and materials).

  <variants>/<shader>/          GLSL (.vert / .frag) for GLES3
  <variants>/<shader>_vulkan/   SPIR-V (.vert.spv / .frag.spv) for Vulkan
  <variants>/<shader>/manifest.json   every file: shader, subshader, pass, keywords, stage, platform, fingerprint
  <variants>/variants.json      every variant the folder has seen, of any frame: its files, errors, fingerprint
  <variants>/_compiled/         the raw output of the last Unity run (placed into the folders by place())
"""
import json
import os

from paretogpu.model.variant import EXT, SHORT, STAGE, VariantKey

RAW = "_compiled"
INDEX = "variants.json"


def raw_dir(root):
    return os.path.join(root, RAW)


def load_manifests(root, fingerprints=None, platforms=None):
    """{key: {(platform, stage): relative file}} of the variants already in the folder (entries whose file is gone
    are skipped). fingerprints: a dict filled with {key: fingerprint the files were compiled with (None if unknown)},
    from the files of `platforms` only (files of a platform not compiled now keep an older fingerprint)."""
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
                have.setdefault(k, {})[(m["platform"], SHORT[m["stage"]])] = f"{d}/{m['file']}"
                if fingerprints is not None and (platforms is None or m["platform"] in platforms):
                    fp = m.get("fingerprint")
                    fingerprints[k] = fp if fingerprints.get(k, fp) == fp else None
    return have


def place(root, raw, keys, result, patch=None):
    """Compiled files of a run -> <root>/<shader>[_vulkan]/ + manifest.json; returns {key: [errors]}.
    patch(platform, data) -> (data, note or None): a fix applied to every file before it is written."""
    manifests, errors = {}, {}
    for v in result["variants"]:
        k = keys[v["id"]]
        errors[k] = list(v["errors"])
        for x in v["files"]:
            plat, stage = x["platform"], x["stage"]
            with open(os.path.join(raw, x["file"]), "rb") as f:
                data = f.read()
            patched = None
            if patch:
                data, patched = patch(plat, data)
            sub = k.folder(plat)
            fn = k.base_name + EXT[(plat, stage)]
            os.makedirs(os.path.join(root, sub), exist_ok=True)
            with open(os.path.join(root, sub, fn), "wb") as f:
                f.write(data)
            manifests.setdefault(sub, []).append({
                "file": fn, "shader": k.shader, "pass": k.pass_name, "subshader": k.subshader,
                "pass_index": k.pass_index, "keywords": list(k.keywords), "stage": STAGE[stage], "platform": plat, "source": "CompileVariant",
                "fingerprint": v.get("fingerprint"), **({"patched": patched} if patched else {})})
    for sub, entries in manifests.items():
        path = os.path.join(root, sub, "manifest.json")
        old = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        names = {e["file"] for e in entries}
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump([e for e in old if e["file"] not in names] + entries, f, indent=1, ensure_ascii=False)
    return errors


def load_index(root):
    """{key: entry} of variants.json: every variant this folder has seen, of any frame."""
    index = {}
    path = os.path.join(root, INDEX)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for x in json.load(f)["variants"]:
                index[VariantKey.from_list(x["key"])] = x
    return index


def save_index(root, index):
    with open(os.path.join(root, INDEX), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"variants": list(index.values())}, f, indent=1, ensure_ascii=False)
