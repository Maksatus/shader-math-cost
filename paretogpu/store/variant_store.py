"""Compiled variants folder of a project:
  <shader>/ GLSL .vert/.frag (GLES3); <shader>_vulkan/ SPIR-V .vert.spv/.frag.spv; each with manifest.json
  variants.json: every variant ever seen (files, errors, fingerprint); _compiled/: raw output of the last Unity run
"""
import json
import os

from paretogpu.model.variant import EXT, SHORT, STAGE, VariantKey

RAW = "_compiled"
INDEX = "variants.json"


def raw_dir(root):
    return os.path.join(root, RAW)


def load_manifests(root, fingerprints=None, platforms=None):
    """-> {key: {(platform, stage): relative file}}. Fills `fingerprints` {key: fp, None if files disagree} from
    `platforms` files only (other platforms' files keep an older fingerprint)."""
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
    """-> {key: [errors]}. patch(platform, data) -> (data, note or None) is applied to every file before writing."""
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


def gles_file(root, file):
    """<shader>_vulkan/<name>.frag.spv -> <root>/<shader>/<name>.frag, or None when not compiled."""
    folder, name = os.path.split(file.replace("\\", "/"))
    if folder.endswith("_vulkan"):
        folder = folder[:-len("_vulkan")]
    if name.endswith(".spv"):
        name = name[:-len(".spv")]
    path = os.path.join(root, folder, name)
    return path if os.path.isfile(path) else None
