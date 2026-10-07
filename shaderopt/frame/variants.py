"""Frame events -> measured shader variants (plan item K1.4).

Every draw of a Frame Debugger snapshot (frame_events.json) names its exact variant: shader, subshader,
pass index and the keywords it ran with. Those variants are compiled in the open editor with
ShaderData.Pass.CompileVariant for Android (unity/ShaderoptVariants.cs: the same files as
"Compile and show code", including shader_feature variants it skips), written as
  <variants>/<shader>/          GLSL (.vert / .frag) for GLES3
  <variants>/<shader>_vulkan/   SPIR-V (.vert.spv / .frag.spv) for Vulkan
with manifest.json, and measured with malioc (profile/measure.py) on the chosen cores. Variants already in
the folder are not compiled again while they are current: every compiled variant keeps the fingerprint of its
shader (Unity version, Android platform defines, color space, the asset's dependency hash: the file, its includes
and subgraphs), and when the editor is open the current fingerprints are asked from it and a variant whose
shader changed is compiled again (--recompile: all of the frame's variants). Without the editor (--no-compile)
the files are used as they are and the result says they were not checked. malioc results come from its cache.
A failed variant is not compiled again until its shader changes (or --retry-failed); a run that did not finish
is not a failure.

Compute dispatches are keyed per kernel but not compiled (COMPUTE_NOT_COMPILED): they are listed without a price.
A Unity run that did not finish leaves its files in <variants>/_compiled; the next run places them (recover()).

Result: <variants>/variants.json and, per draw event, the measurement records of its fragment and vertex
shader (per compute event, of its kernel) on every core (match()), or the reason there is none.
"""
import hashlib
import json
import os
import re

from shaderopt.corpus import split_unity
from shaderopt.profile import measure
from shaderopt.unity import export as unity

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "unity", "ShaderoptVariants.cs")
EXT = {("gles3", "vert"): ".vert", ("gles3", "frag"): ".frag", ("gles3", "comp"): ".comp",
       ("vulkan", "vert"): ".vert.spv", ("vulkan", "frag"): ".frag.spv", ("vulkan", "comp"): ".comp.spv"}
API = {"gles3": "gles", "vulkan": "vulkan"}  # export platform -> malioc API
STAGE = {"vert": "vertex", "frag": "fragment", "comp": "compute"}
SHORT = {v: k for k, v in STAGE.items()}
COMPUTE = -1  # subshader of a compute kernel key
# ComputeShader.FindKernel / HasKernel and ShaderUtil.CompileComputeShaderVariant called from a run_script crashed
# the 6000.3.18f1 editor three times (2026-10-06), on built-in and package compute shaders alike
COMPUTE_NOT_COMPILED = "compute kernel not compiled: compiling compute shaders from a script crashes Unity 6000.3"
NOT_FINISHED = "no output: the Unity run did not finish"  # transient: compiled again next time


class VariantsError(RuntimeError):
    pass


def key_of(ev):
    """Variant of a draw event: (shader, subshader, pass index, pass name, keywords);
    of a compute event: (compute shader, -1, 0, kernel, ())."""
    if ev["kind"] == "compute":
        c = ev.get("compute") or {}
        return (c.get("shader") or "", COMPUTE, 0, c.get("kernel") or "", ())
    return (ev["shader"], ev["subshader"] or 0, ev["pass_index"] or 0, ev["pass"] or "", tuple(sorted(ev["keywords"])))


def stages_of(k):
    return ("comp",) if k[1] == COMPUTE else ("vert", "frag")


def key_str(k):
    if k[1] == COMPUTE:
        return f"{k[0]} | kernel {k[3]}"
    return f"{k[0]} | {k[1]}.{k[2]} {k[3]} | {' '.join(k[4]) or '-'}"


def base_name(k):
    """File name stem of a variant: readable, unique (hash of the whole key), not too long for Windows paths."""
    kw = split_unity.slug("_".join(k[4])) or "none"
    h = hashlib.sha1(json.dumps(k).encode()).hexdigest()[:8]
    stem = (f"{split_unity.slug(k[3])}" if k[1] == COMPUTE else f"{k[1]}_{k[2]:02d}_{split_unity.slug(k[3])}__{kw}")
    return (stem[:72] + "__" + h) if len(stem) > 80 else f"{stem}__{h}"


def folder_of(k, platform):
    return (split_unity.slug(k[0]) + ("_compute" if k[1] == COMPUTE else "")
            + ("" if platform == "gles3" else f"_{platform}"))


def load_manifests(root, fingerprints=None):
    """{key: {(platform, stage): relative file}} of the variants already in the folder (entries whose file is gone
    are skipped). fingerprints: a dict filled with {key: fingerprint the files were compiled with (None if unknown)}."""
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
                k = (m["shader"], m["subshader"], m["pass_index"], m["pass"], tuple(m["keywords"]))
                have.setdefault(k, {})[(m["platform"], SHORT[m["stage"]])] = f"{d}/{m['file']}"
                if fingerprints is not None:
                    fp = m.get("fingerprint")
                    fingerprints[k] = fp if fingerprints.get(k, fp) == fp else None
    return have


def current_fingerprints(project, shaders, root, timeout=600):
    """{shader name: fingerprint (None: not found)} from the open editor (ShaderoptVariants.Fingerprints)."""
    raw = os.path.join(root, "_compiled")
    os.makedirs(raw, exist_ok=True)
    config, out = os.path.join(raw, "fingerprints_config.json"), os.path.join(raw, "fingerprints.json")
    if os.path.exists(out):
        os.remove(out)
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"shaders": sorted(set(shaders)), "out": out}, f, indent=1)
    d = unity._cli_json(["command", "run_script", "--project-path", project, "--timeout", str(timeout),
                         "--timeout_ms", str(timeout * 1000), "--file", os.path.abspath(SCRIPT),
                         "--entry", "ShaderoptVariants.Fingerprints", "--args", json.dumps([config])], timeout + 60)
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success") or not str(res.get("result", "")).startswith("ok"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise VariantsError(f"fingerprints: {res.get('result') or diag or res.get('errorDetails') or unity._errors(d)}"[:3000])
    with open(out, encoding="utf-8") as f:
        return json.load(f)["shaders"]


def compile_variants(project, keys, root, platforms, timeout=1800):
    """Compile `keys` in the open editor; writes the files and manifests; returns {key: [errors]}."""
    if not unity.editor_ready(project, allow_play=True):
        raise VariantsError(f"the editor of {project} does not answer Unity CLI or is busy (compiling, importing): "
                            "it is needed to compile the variants of the frame")
    raw = os.path.join(root, "_compiled")
    os.makedirs(raw, exist_ok=True)
    for fn in os.listdir(raw):
        os.remove(os.path.join(raw, fn))
    config = os.path.join(raw, "config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"shaders": [k[0] for k in keys], "subshaders": [k[1] for k in keys],
                   "pass_indices": [k[2] for k in keys], "passes": [k[3] for k in keys],
                   "keywords": [" ".join(k[4]) for k in keys], "platforms": list(platforms), "out": raw}, f, indent=1)
    d = unity._cli_json(["command", "run_script", "--project-path", project, "--timeout", str(timeout),
                         "--timeout_ms", str(timeout * 1000), "--file", os.path.abspath(SCRIPT),
                         "--entry", "ShaderoptVariants.Run", "--args", json.dumps([config])], timeout + 60)
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success") or not str(res.get("result", "")).startswith("ok"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise VariantsError(f"{res.get('result') or diag or res.get('errorDetails') or unity._errors(d)}"[:3000])
    with open(os.path.join(raw, "variants_result.json"), encoding="utf-8") as f:
        result = json.load(f)
    if len(result["variants"]) != len(keys):
        raise VariantsError(f"Unity compiled {len(result['variants'])} of {len(keys)} variants")
    return place(root, raw, keys, result)


def recover(root):
    """A Unity run that did not finish (the editor crashed or the connection broke) leaves its compiled files in
    <root>/_compiled without variants_result.json: place what is there. Returns {key: [errors]} or None."""
    raw = os.path.join(root, "_compiled")
    config = os.path.join(raw, "config.json")
    if not os.path.exists(config) or os.path.exists(os.path.join(raw, "variants_result.json")):
        return None
    with open(config, encoding="utf-8") as f:
        cfg = json.load(f)
    keys = [(s, ss, pi, p, tuple(kw.split())) for s, ss, pi, p, kw in
            zip(cfg["shaders"], cfg["subshaders"], cfg["pass_indices"], cfg["passes"], cfg["keywords"])]
    files = {}
    for fn in os.listdir(raw):
        m = re.fullmatch(r"(\d+)_(gles3|vulkan)_(vert|frag|comp)\.bin", fn)
        if m:
            files.setdefault(int(m.group(1)), []).append({"platform": m.group(2), "stage": m.group(3), "file": fn})
    # variants without any file were not reached (or failed: the messages are lost with the run)
    result = {"variants": [{"id": i, "files": files.get(i, []), "fingerprint": None,
                            "errors": [] if i in files else [NOT_FINISHED]}
                           for i in range(len(keys))]}
    errors = place(root, raw, keys, result)
    with open(os.path.join(raw, "variants_result.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({**result, "recovered": True}, f, indent=1)
    return errors


def place(root, raw, keys, result):
    """Compiled files of a run -> <root>/<shader>[_vulkan]/ + manifest.json; returns {key: [errors]}."""
    manifests, errors = {}, {}
    for v in result["variants"]:
        k = keys[v["id"]]
        errors[k] = list(v["errors"])
        for x in v["files"]:
            plat, stage = x["platform"], x["stage"]
            with open(os.path.join(raw, x["file"]), "rb") as f:
                data = f.read()
            patched = None
            if plat == "gles3":
                text, patched = split_unity.es31(data.decode("utf-8", errors="replace"))
                data = text.encode()
            sub = folder_of(k, plat)
            fn = base_name(k) + EXT[(plat, stage)]
            os.makedirs(os.path.join(root, sub), exist_ok=True)
            with open(os.path.join(root, sub, fn), "wb") as f:
                f.write(data)
            manifests.setdefault(sub, []).append({
                "file": fn, "shader": k[0], "pass": k[3], "subshader": k[1], "pass_index": k[2],
                "keywords": list(k[4]), "stage": STAGE[stage], "platform": plat, "source": "CompileVariant",
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


def load_measurements(root):
    """{relative file: {core: record}} from <root>/measurements.jsonl (failed runs keep their error)."""
    out = {}
    path = os.path.join(root, "measurements.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out.setdefault(r["file"], {})[r["core"]] = r
    return out


def run(events, project, root, cores, platforms=("gles3", "vulkan"), jobs=None, compile_missing=True, progress=print,
        recompile=False, retry_failed=False):
    """Compile and measure the variants of the frame's draws. Returns the state for match():
    {"keys", "files": {key: {(platform, stage): file}}, "errors": {key: [..]}, "measurements",
     "checked": True if the compiled files were checked against the shaders in the open editor}.
    recompile: compile every variant of the frame again; retry_failed: also the ones that failed before."""
    root = os.path.abspath(root)
    project = project and os.path.abspath(project)
    keys = list(dict.fromkeys(key_of(e) for e in events if e["kind"] in ("draw", "compute")))
    recovered = recover(root) or {}
    if recovered:
        progress(f"recovered {sum(1 for e in recovered.values() if not e)} variants of an unfinished Unity run")
    stored = {}
    have = load_manifests(root, stored)
    index_path = os.path.join(root, "variants.json")
    index = {}  # every variant this folder has seen, of any frame: {key: entry}
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            for x in json.load(f)["variants"]:
                index[tuple(x["key"][:4]) + (tuple(x["key"][4]),)] = x
    draw_keys = [k for k in keys if k[1] != COMPUTE]  # compute kernels are not compiled: see COMPUTE_NOT_COMPILED

    def complete(k):
        return all((p, s) in have.get(k, {}) for p in platforms for s in stages_of(k))

    current = None
    if compile_missing and draw_keys:
        if project and unity.editor_ready(project, allow_play=True):
            current = current_fingerprints(project, [k[0] for k in draw_keys], root)
        else:
            progress("the editor does not answer Unity CLI: compiled variants are used without checking "
                     "their shaders for changes")

    def failed_before(k):
        """A variant that failed is not compiled again until its shader changes (or retry_failed)."""
        errs = [e for e in (index.get(k) or {}).get("errors", []) if e != NOT_FINISHED]
        if not errs or retry_failed or recompile:
            return False
        return current is None or (index.get(k) or {}).get("fingerprint") == current.get(k[0])

    stale = [k for k in draw_keys if complete(k) and current is not None
             and (recompile or stored.get(k) is None or stored.get(k) != current.get(k[0]))]
    missing = [k for k in draw_keys if (not complete(k) or k in stale) and not failed_before(k)]
    errors = {k: [e for e in (index.get(k) or {}).get("errors", []) if e != NOT_FINISHED] for k in keys}
    for k in keys:
        if k[1] == COMPUTE and not have.get(k):
            errors[k] = [COMPUTE_NOT_COMPILED]
    if missing and compile_missing:
        if stale:
            progress(f"{len(stale)} compiled variants are out of date (shader, includes, Unity or platform defines "
                     f"changed{', --recompile' if recompile else ''}): compiling them again")
        progress(f"compiling {len(missing)} of {len(keys)} variants in Unity ...")
        errors.update(compile_variants(project, missing, root, platforms))
        stored = {}
        have = load_manifests(root, stored)
    pending = set() if compile_missing else {k for k in missing if not complete(k)}
    if any(have.get(k) for k in keys):
        progress(f"measuring {root} on {', '.join(cores)} ...")
        records, failures = measure.run(root, cores, "gles", root, jobs)
        if failures:
            progress(f"  malioc failed on {len(failures)} files")
    for k in keys:
        index[k] = {"key": [k[0], k[1], k[2], k[3], list(k[4])],
                    "files": {f"{p}/{s}": fn for (p, s), fn in sorted(have.get(k, {}).items())},
                    "errors": [e for e in errors.get(k, []) if e != NOT_FINISHED],
                    "fingerprint": stored.get(k) or (current or {}).get(k[0])}
    with open(index_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"variants": list(index.values())}, f, indent=1, ensure_ascii=False)
    # not compiled in this run: the reason is shown, not stored (the next run compiles them)
    for k in pending:
        errors[k] = errors[k] or ["not compiled yet (--no-compile)"]
    return {"keys": keys, "files": {k: have.get(k, {}) for k in keys}, "errors": errors,
            "measurements": load_measurements(root), "checked": current is not None}


def match(ev, state, api="vulkan"):
    """(records, reason) for one event: records = {"fragment": {core: rec}, "vertex": {core: rec}}
    ({"compute": {core: rec}} for a dispatch) with the measurement records of the variant in `api`;
    reason (None if every stage is measured) says what is missing."""
    if ev["kind"] not in ("draw", "compute"):
        return None, None
    k = key_of(ev)
    plat = {v: p for p, v in API.items()}[api]
    files, errs = state["files"].get(k, {}), state["errors"].get(k, [])
    out, why = {}, []
    for s in stages_of(k):
        stage = STAGE[s]
        fn = files.get((plat, s))
        if not fn:
            why.append(f"{stage}: not exported" + (f" ({'; '.join(errs)})" if errs else ""))
            continue
        recs = state["measurements"].get(fn, {})
        ok = {c: r for c, r in recs.items() if r.get("ok", True)}
        if not ok:
            bad = next((r.get("error", "") for r in recs.values()), "not measured")
            why.append(f"{stage}: malioc failed: {bad.strip().splitlines()[-1] if bad.strip() else bad}")
            continue
        out[stage] = ok
    return out, ("; ".join(why) or None)
