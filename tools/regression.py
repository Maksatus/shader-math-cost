"""Regression check of every paretogpu command on copies of real local data (it stays local: paretogpu/out).

  python tools/regression.py baseline [--name base] [--snapshots A,B] [--materials A.mat,B.mat]
  python tools/regression.py check [--name base]          run again and compare with the baseline
  python tools/regression.py diff <run A> <run B>

A run copies two snapshots of one project and the project's variants folder from paretogpu/out into
paretogpu/out/_regression/work (the real snapshots are never written), runs cost (several option sets), compare,
matcompare, matshader, hotspots, measure / report on corpus/synthetic, the bench table and the UI's JSON endpoints,
and keeps every output in paretogpu/out/_regression/<run>. --no-compile everywhere: no Unity needed. Two runs are
compared ignoring times, dict key order and timings; the data embedded in HTML pages is compared as JSON.
By default the two newest snapshots of a project and two materials whose shaders they draw are taken; the baseline
remembers them, so `check` runs on the same inputs.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
from paretogpu.store import workspace  # noqa: E402

ROOT = os.path.join(workspace.OUT, "_regression")
WORK = os.path.join(ROOT, "work")
UI_PORT = 8799
SNAP_FILES = ("frame_events.json", "frame.json")


def W(*p):
    return os.path.join(WORK, *p)


# --- inputs ------------------------------------------------------------------------------------------------------
def pick_inputs(snapshots=None, mats=None):
    snaps = sorted(workspace.snapshot_dirs(), key=lambda s: -os.path.getmtime(os.path.join(s[1], "frame_events.json")))
    if snapshots:
        names = snapshots.split(",")
    else:
        by_project = {}
        for name, path in snaps:
            by_project.setdefault(workspace.snapshot_project(path), []).append(name)
        names = next((v[:2] for v in by_project.values() if len(v) >= 2), [s[0] for s in snaps[:2]])
    if len(names) < 2:
        sys.exit("needs two snapshots of a project in paretogpu/out")
    project = workspace.snapshot_project(os.path.join(workspace.OUT, names[0]))
    if mats:
        mat_paths = mats.split(",")
    else:
        from paretogpu.adapters.unity import assets
        with open(os.path.join(workspace.OUT, names[0], "frame_events.json"), encoding="utf-8") as f:
            drawn = [e["shader"] for e in json.load(f)["events"] if e["kind"] == "draw" and e["stage"] == "opaque"]
        by_shader = {}
        for m in assets.scan(project):
            if m.get("shader") and not m.get("error"):
                by_shader.setdefault(m["shader"], m["path"])
        mat_paths = list(dict.fromkeys(by_shader[s] for s in drawn if s in by_shader))[:2]
    return {"snapshots": names, "project": project, "variants": workspace.variants_dir(project),
            "materials": mat_paths}


def prepare(inputs):
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    shutil.copytree(inputs["variants"], W("variants"), ignore=shutil.ignore_patterns("_compiled"))
    for s in inputs["snapshots"]:
        os.makedirs(W("snaps", s))
        for fn in SNAP_FILES:
            src = os.path.join(workspace.OUT, s, fn)
            if os.path.exists(src):
                shutil.copy2(src, W("snaps", s, fn))
    os.makedirs(W("out"))


def steps(inputs):
    a, b = inputs["snapshots"][:2]
    v, project, mats = W("variants"), inputs["project"], inputs["materials"]
    out = [
        ("cost_a", ["cost", W("snaps", a), "--variants", v, "--no-compile", "--cores", "preset:mobile",
                    "--out", W("out", "cost_a")]),
        ("cost_b", ["cost", W("snaps", b), "--variants", v, "--no-compile", "--cores", "preset:mobile",
                    "--materials", "--project", project, "--out", W("out", "cost_b")]),
        ("cost_gles", ["cost", W("snaps", a), "--variants", v, "--no-compile", "--core", "Mali-G78", "--api", "gles",
                       "--loop-iters", "4", "--loop-iters-shader", "Lit=1", "--main-core", "Mali-G78",
                       "--out", W("out", "cost_gles")]),
        ("compare", ["compare", W("out", "cost_a"), W("out", "cost_b"), "--out", W("out", "compare")]),
        ("hotspots", ["hotspots", W("out", "cost_a"), "--top", "4", "--out", W("out", "hotspots")]),
        ("measure", ["measure", os.path.join(REPO, "paretogpu", "corpus", "synthetic"), "--cores", "preset:mobile",
                     "--out", W("out", "measure")]),
        ("report", ["report", W("out", "measure"), "--out", W("out", "report")]),
    ]
    if len(mats) >= 2:
        out.insert(4, ("matcompare", ["matcompare", "--project", project, mats[0], mats[1], "--variants", v,
                                      "--no-compile", "--out", W("out", "matcompare")]))
    if mats:
        out.insert(5, ("matshader", ["matshader", "--project", project, mats[0], "--variants", v, "--no-compile",
                                     "--out", W("out", "matshader")]))
    return out


# --- a run -------------------------------------------------------------------------------------------------------
def env():
    e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONHASHSEED="0")
    e.pop("PARETOGPU_PROGRESS", None)
    return e


def run(name, inputs, bench=True):
    res = os.path.join(ROOT, name)
    if os.path.isdir(res):
        shutil.rmtree(res)
    os.makedirs(res)
    prepare(inputs)
    timing = {}
    for sid, argv in steps(inputs):
        t = time.time()
        p = subprocess.run([sys.executable, "-m", "paretogpu"] + argv, cwd=REPO, env=env(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        timing[sid] = round(time.time() - t, 1)
        with open(os.path.join(res, f"{sid}.stdout.txt"), "w", encoding="utf-8") as f:
            f.write(f"rc={p.returncode}\n{p.stdout}\n--- stderr\n{p.stderr}")
        print(f"{sid}: exit {p.returncode}, {timing[sid]} s")
    if bench:
        t = time.time()
        b = W("out", "bench")
        os.makedirs(b)
        p = subprocess.run([sys.executable, "-m", "paretogpu", "bench", "--out", b], cwd=REPO, env=env(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if "invalid choice" in p.stderr:  # before bench became a command
            p = subprocess.run([sys.executable, os.path.join(REPO, "bench", "run.py"), "--out", b], cwd=REPO,
                               env=env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
            subprocess.run([sys.executable, os.path.join(REPO, "bench", "build_site.py"), b], cwd=REPO, env=env(),
                           capture_output=True)
        timing["bench"] = round(time.time() - t, 1)
        print(f"bench: exit {p.returncode}, {timing['bench']} s")
    ui(res, inputs)
    shutil.copytree(W("out"), os.path.join(res, "out"))
    with open(os.path.join(res, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"inputs": inputs, "timing": timing, "time": time.time()}, f, indent=1, ensure_ascii=False)


def ui(res, inputs):
    gets = {"schema": "/api/schema", "options": "/api/options", "reports": "/api/reports", "costs": "/api/costs",
            "matcompares": "/api/matcompares", "matshaders": "/api/matshaders", "doctor": "/api/doctor",
            "materials": "/api/materials?project=" + urllib.request.quote(inputs["project"]), "ping": "/api/ping"}
    d = os.path.join(res, "ui")
    os.makedirs(d)
    p = subprocess.Popen([sys.executable, "-m", "paretogpu", "ui", "--no-window", "--port", str(UI_PORT)], cwd=REPO,
                         env=env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{UI_PORT}/api/ping", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        for name, path in gets.items():
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{UI_PORT}{path}", timeout=120) as r:
                    body = r.read().decode("utf-8")
            except Exception as e:
                body = f"ERROR {e}"
            with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as f:
                f.write(body)
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


# --- comparing ---------------------------------------------------------------------------------------------------
VOLATILE = {"computed_at", "made_at", "cost_computed_at", "started", "finished", "seconds", "time", "cost_time",
            "snapshot_time", "updated"}
TIME_RE = re.compile(r"\d{8}_\d{6}b*")
BLOBS = [re.compile(r"^const D = (.*);$", re.M),
         re.compile(r'(?s)<script type="application/json" id="data">(.*?)</script>')]


def norm_json(o):
    if isinstance(o, dict):
        return {k: ("<t>" if k in VOLATILE else norm_json(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [norm_json(v) for v in o]
    if isinstance(o, str):
        return TIME_RE.sub("<time>", o)
    return o


def norm_text(t):
    t = re.sub(r'"(computed_at|made_at|cost_computed_at)":\s*[0-9.e+]+', r'"\1":0', t)
    t = TIME_RE.sub("<time>", t)
    return re.sub(r"\b\d+(\.\d+)? (s|с)\b", r"<sec> \2", t)


def load(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if path.endswith(".html"):
        for rx in BLOBS:
            m = rx.search(text)
            if m and m.group(1).strip() not in ("", "null", "/*COMPARE_DATA*/null"):
                try:
                    data = norm_json(json.loads(m.group(1).replace("<\\/", "</")))
                except ValueError:
                    continue
                return norm_text(text[:m.start(1)] + "<DATA>" + text[m.end(1):]), data
        return norm_text(text), None
    if path.endswith(".jsonl"):
        return sorted(json.dumps(norm_json(json.loads(l)), sort_keys=True) for l in text.splitlines() if l.strip())
    if path.endswith(".json"):
        try:
            return norm_json(json.loads(text))
        except ValueError:
            return norm_text(text)
    return norm_text(text)


def files(root):
    out = {}
    for d, _, fs in os.walk(root):
        for fn in fs:
            if fn == "run.json" or fn.endswith(".rdc"):
                continue
            p = os.path.join(d, fn)
            out.setdefault(TIME_RE.sub("<time>", os.path.relpath(p, root).replace("\\", "/")), []).append(p)
    return out


def first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in list(dict.fromkeys(list(a) + list(b))):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'B' if k not in a else 'A'}"
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: {len(a)} items vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {str(a)[:200]!r} vs {str(b)[:200]!r}"


def diff(na, nb, ignore=()):
    fa, fb = files(os.path.join(ROOT, na)), files(os.path.join(ROOT, nb))
    bad = 0
    for rel in sorted(set(fa) | set(fb)):
        if any(re.search(p, rel) for p in ignore):
            continue
        if rel not in fa or rel not in fb:
            print(f"only in {'B' if rel not in fa else 'A'}: {rel}")
            bad += 1
            continue
        for pa, pb in zip(sorted(fa[rel]), sorted(fb[rel])):
            a, b = load(pa), load(pb)
            if a == b:
                continue
            bad += 1
            if isinstance(a, str) and isinstance(b, str):
                lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=1))
                print(f"differs: {rel}\n" + "\n".join(x[:300] for x in lines[:30]))
            else:
                print(f"differs: {rel}: {first_diff(a, b)}")
    print(f"{bad} differences")
    return bad


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline", help="run on the chosen inputs and keep the result as the baseline")
    b.add_argument("--name", default="base")
    b.add_argument("--snapshots", help="two snapshot folders of paretogpu/out, comma separated (default: the newest)")
    b.add_argument("--materials", help="two .mat paths of the project, comma separated (default: drawn in the frame)")
    b.add_argument("--no-bench", action="store_true")
    c = sub.add_parser("check", help="run again on the baseline's inputs and compare")
    c.add_argument("--name", default="base")
    c.add_argument("--no-bench", action="store_true")
    c.add_argument("--ignore", action="append", default=[], help="regex of output paths not to compare")
    d = sub.add_parser("diff", help="compare two runs in paretogpu/out/_regression")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--ignore", action="append", default=[])
    a = ap.parse_args()
    if a.cmd == "baseline":
        inputs = pick_inputs(a.snapshots, a.materials)
        print("inputs:", json.dumps(inputs, ensure_ascii=False))
        run(a.name, inputs, not a.no_bench)
        return 0
    if a.cmd == "check":
        with open(os.path.join(ROOT, a.name, "run.json"), encoding="utf-8") as f:
            inputs = json.load(f)["inputs"]
        run(a.name + "_check", inputs, not a.no_bench)
        return 1 if diff(a.name, a.name + "_check", a.ignore + (["^out/bench/"] if a.no_bench else [])) else 0
    return 1 if diff(a.a, a.b, a.ignore) else 0


if __name__ == "__main__":
    sys.exit(main())
