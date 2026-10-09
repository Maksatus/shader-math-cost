"""malioc (Mali Offline Compiler) wrapper shared by bench/run.py and paretogpu.

compile() runs malioc on GLSL source with a cache keyed by the source text,
parse() turns malioc JSON into a flat record: every variant (Main for fragment
shaders, Position and Varying for IDVS vertex shaders), cycles per pipe on the
longest / shortest path and in total, bound pipes, registers, spilling, fp16 %
and uniform computation. Longest path cycles are None when malioc reports N/A
(dynamic loops, e.g. the URP additional lights loop).

malioc: the newest installed Arm Performance Studio (find_malioc(); MALIOC overrides it). The cache key holds the
malioc path, which holds the Studio version, so a new version never reads results of an old one.

The cache is one SQLite file, <cache>/malioc.sqlite (WAL: many threads and processes read and write it at once),
the entries compressed. Entries of the older cache, one JSON file per key in the same folder, are moved into it the
first time they are asked for. Entries written by the old bench/run.py (v2) hold only the parsed main variant; they
are still served to callers that need just that (run.py), and recompiled when the raw JSON is needed.
"""
import atexit
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zlib

from paretogpu.model import cores as core_names
from paretogpu.model.errors import ParetoError
from paretogpu.model.measurement import Measurement


def find_malioc():
    """$MALIOC, else malioc.exe of the newest "Arm Performance Studio <version>" in Program Files, else malioc on PATH."""
    if os.environ.get("MALIOC"):
        return os.environ["MALIOC"]
    found = []
    for d in glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Arm", "Arm Performance Studio *")):
        exe = os.path.join(d, "mali_offline_compiler", "malioc.exe")
        m = re.search(r"(\d+(?:\.\d+)*)$", d)
        if m and os.path.exists(exe):
            found.append((tuple(int(x) for x in m.group(1).split(".")), exe))
    if found:
        return max(found)[1]
    return shutil.which("malioc") or "malioc"


MALIOC = find_malioc()
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "bench", ".cache")
CACHE_VERSION = 3        # v3 = v2 fields + "raw" (malioc JSON without descriptions)
LEGACY_VERSIONS = (2, 3)  # versions that carry the v2 fields
STAGES = {".vert": "vertex", ".frag": "fragment", ".comp": "compute"}
EXT = {v: k for k, v in STAGES.items()}
SPIRV_EXT = ".spv"  # binary SPIR-V: shader.frag.spv (Vulkan only)
PIPE_NAMES = {  # malioc pipeline name -> short name
    "arith_total": "arith", "arithmetic": "arith", "arith_fma": "fma", "arith_cvt": "cvt",
    "arith_sfu": "sfu", "load_store": "ls", "varying": "v", "texture": "t",
}

_tls = threading.local()


class MaliocError(ParetoError):
    code = "malioc"


def _run(cmd, env=None):
    """Run malioc; output decoded as UTF-8 whatever the console code page. A missing malioc is a MaliocError."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    except OSError as e:
        why = "not found" if isinstance(e, FileNotFoundError) else e
        raise MaliocError(f"cannot run malioc ({cmd[0]}): {why}. Install Arm Performance Studio or set MALIOC") from None


_tmp_dirs = []


@atexit.register
def _remove_tmp_dirs():
    for d in _tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)


def _thread_tmp(tmp_root):
    """(TEMP folder of this thread, environment for malioc). malioc writes intermediate files with fixed names to
    %TEMP%/moc-temp, so every worker thread needs its own TEMP to run in parallel; the folders are made in the
    system temp folder (or tmp_root) and removed at exit."""
    dirs = _tls.__dict__.setdefault("dirs", {})
    if tmp_root not in dirs:
        if tmp_root:
            os.makedirs(tmp_root, exist_ok=True)
        d = tempfile.mkdtemp(prefix="paretogpu_malioc_", dir=tmp_root)
        _tmp_dirs.append(d)
        dirs[tmp_root] = (d, dict(os.environ, TEMP=d, TMP=d, TMPDIR=d))
    return dirs[tmp_root]


def list_cores(malioc=MALIOC):
    """[(core, architecture, [apis])] as reported by `malioc --list`."""
    out = _run([malioc, "--list"]).stdout
    cores, arch = [], None
    for line in out.splitlines():
        line = line.strip()
        if line.endswith("architecture"):
            arch = line.replace(" architecture", "")
        elif "(" in line and arch:
            name, apis = line.split(" (", 1)
            cores.append((name, arch, [a.strip() for a in apis.rstrip(")").split(",")]))
    return cores


def parse_cores(spec, malioc=MALIOC):
    """--cores ('preset:mobile', 'Mali-G78,Mali-G52') -> [core], checked against the cores malioc lists."""
    def known():
        names = {c for c, _, _ in list_cores(malioc)}
        if not names:
            raise ValueError(f"malioc ({malioc}) listed no cores: is it Mali Offline Compiler?")
        return names
    return core_names.parse(spec, known)


def version(malioc=MALIOC):
    out = _run([malioc, "--version"]).stdout
    for tok in out.split():
        if tok.startswith("v") and tok[1:2].isdigit():
            return tok[1:]
    return ""


def stage_of(path):
    """Shader stage from a file name (shader.frag, shader.vert.spv, ...), or None."""
    base = path[:-len(SPIRV_EXT)] if path.endswith(SPIRV_EXT) else path
    return STAGES.get(os.path.splitext(base)[1])


def read_source(path):
    """GLSL text, or bytes for a binary SPIR-V file."""
    if path.endswith(SPIRV_EXT):
        with open(path, "rb") as f:
            return f.read()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def cache_key(src, core, api, stage="fragment", malioc=MALIOC):
    """src: GLSL text or SPIR-V bytes."""
    if isinstance(src, bytes):
        return hashlib.sha1(f"{malioc}|{api}|{core}|spirv|{stage}|".encode() + src).hexdigest()
    # fragment keys match the old bench/run.py keys, so its cache stays valid
    tail = "" if stage == "fragment" else f"|{stage}"
    return hashlib.sha1(f"{malioc}|{api}|{core}|{src}{tail}".encode()).hexdigest()


def _strip(o):
    """malioc JSON without descriptions and file names (they bloat the cache)."""
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in ("description", "filename")}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def run_malioc(src, core, api, stage="fragment", malioc=MALIOC, tmp_root=None):
    """Compile once, no cache. src: GLSL text or SPIR-V bytes. Returns malioc JSON; raises MaliocError."""
    spv = isinstance(src, bytes)
    if spv and api != "vulkan":
        raise MaliocError("SPIR-V input needs the Vulkan API (--api vulkan)")
    tmp, env = _thread_tmp(tmp_root)
    fn = os.path.join(tmp, "shader" + EXT[stage] + (SPIRV_EXT if spv else ""))
    with open(fn, "wb") as f:
        f.write(src if spv else src.encode("utf-8"))
    r = _run([malioc, "--opengles" if api == "gles" else "--vulkan", "-c", core, "--format", "json", fn], env)
    try:
        j = json.loads(r.stdout)
    except Exception:
        raise MaliocError((r.stdout + r.stderr)[-1500:])
    try:
        j["shaders"][0]["variants"][0]["performance"]
    except Exception:
        # compile errors come as JSON with schema "error": keep just the messages
        errs = [e for s in j.get("shaders", []) for e in s.get("errors", [])]
        raise MaliocError("\n".join(errs) if errs else (r.stdout + r.stderr)[-1500:])
    return j


def _legacy(j):
    """Fields of the old bench/run.py cache entry (main / first variant)."""
    v = j["shaders"][0]["variants"][0]
    perf = v["performance"]
    return {
        "cycles": dict(zip(perf["pipelines"], perf["longest_path_cycles"]["cycle_count"])),
        "short": dict(zip(perf["pipelines"], perf["shortest_path_cycles"]["cycle_count"])),
        "bound": perf["longest_path_cycles"]["bound_pipelines"],
        "props": {p["name"]: p["value"] for p in v["properties"]},
        "driver": j["shaders"][0].get("driver", ""),
    }


_key_locks = {}
_key_locks_guard = threading.Lock()


def _key_lock(key):
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


class _Cache:
    """<folder>/malioc.sqlite: key -> zlib-compressed JSON entry; a miss looks for the older <folder>/<key>.json."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "malioc.sqlite")
        self.tls = threading.local()
        self.conns = []
        self.lock = threading.Lock()
        os.makedirs(folder, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=60)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value BLOB NOT NULL)")
            db.commit()
        finally:
            db.close()

    def _db(self):
        db = getattr(self.tls, "db", None)
        if db is None:
            db = self.tls.db = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
            db.execute("PRAGMA synchronous=NORMAL")
            with self.lock:
                self.conns.append(db)
        return db

    def close(self):
        with self.lock:
            for db in self.conns:
                db.close()
            self.conns = []

    def get(self, key):
        row = self._db().execute("SELECT value FROM entries WHERE key = ?", (key,)).fetchone()
        if row:
            return json.loads(zlib.decompress(row[0]))
        old = self._read_file(os.path.join(self.folder, key + ".json"))
        if old is not None:
            self.put(key, old)
        return old

    def put(self, key, entry):
        db = self._db()
        db.execute("INSERT OR REPLACE INTO entries (key, value) VALUES (?, ?)",
                   (key, zlib.compress(json.dumps(entry).encode("utf-8"), 6)))
        db.commit()

    @staticmethod
    def _read_file(path):
        """An entry of the older file cache, or None. On Windows a file being replaced by another process cannot be
        opened for a moment: retry, then treat it as a miss."""
        for attempt in range(5):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except FileNotFoundError:
                return None
            except (PermissionError, json.JSONDecodeError):
                time.sleep(0.05 * (attempt + 1))
        return None


_caches = {}
_caches_guard = threading.Lock()


def _cache(folder):
    folder = os.path.abspath(folder)
    with _caches_guard:
        if folder not in _caches:
            _caches[folder] = _Cache(folder)
            atexit.register(_caches[folder].close)
        return _caches[folder]


def compile(src, core, api, stage="fragment", malioc=MALIOC, cache=CACHE, need_raw=False):
    """Cached malioc run. Returns {"ok": True, cycles, short, bound, props, driver[, raw][, cached]}
    or {"ok": False, "error": text}; failures are not cached.
    need_raw: recompile old cache entries that lack the raw JSON."""
    store = _cache(cache)
    key = cache_key(src, core, api, stage, malioc)
    # variants with identical sources share the key: one thread compiles, the others wait and read
    with _key_lock(key):
        res = store.get(key)
        if res and res.get("v") in LEGACY_VERSIONS and (res.get("raw") or not need_raw):
            res["cached"] = True
            return res
        try:
            j = _strip(run_malioc(src, core, api, stage, malioc))
        except MaliocError as e:
            return {"ok": False, "error": str(e)}
        res = {"v": CACHE_VERSION, "ok": True, **_legacy(j), "raw": j}
        store.put(key, res)
        return res


def _cycles(perf, key):
    c = perf.get(key)
    # malioc reports N/A (null) for the longest path of shaders with dynamic loops
    if not c or all(v is None for v in c["cycle_count"]):
        return None, []
    return ({PIPE_NAMES.get(p, p): v for p, v in zip(perf["pipelines"], c["cycle_count"])},
            [PIPE_NAMES.get(p, p) for p in c["bound_pipelines"]])


def parse(j):
    """Flat record from malioc JSON (raw or stripped)."""
    sh = j["shaders"][0]
    sprops = {p["name"]: p["value"] for p in sh.get("properties", [])}
    variants = {}
    for v in sh["variants"]:
        perf = v["performance"]
        props = {p["name"]: p["value"] for p in v["properties"]}
        longest, bound = _cycles(perf, "longest_path_cycles")
        shortest, _ = _cycles(perf, "shortest_path_cycles")
        total, _ = _cycles(perf, "total_cycles")
        variants[v["name"].lower()] = {
            "longest": longest, "shortest": shortest, "total": total, "bound": bound,
            "work_regs": props.get("work_registers_used"),
            "uniform_regs": props.get("uniform_registers_used"),
            "occupancy": props.get("thread_occupancy"),
            "spilling": props.get("has_stack_spilling"),
            "spill_bytes": props.get("stack_spill_bytes"),
            "fp16_pct": props.get("fp16_arithmetic"),
        }
    hw = sh["hardware"]
    return {
        "core": hw["core"], "arch": hw["architecture"],
        "api": "gles" if sh["shader"]["api"] == "OpenGL ES" else "vulkan",
        "stage": sh["shader"]["type"].lower(),
        "driver": sh.get("driver", ""),
        "malioc": ".".join(map(str, j["producer"]["version"])),
        "uniform_computation": sprops.get("has_uniform_computation"),
        "variants": variants,
        "notes": sh.get("notes", []),
    }


def measure(src, core, api, stage="fragment", malioc=MALIOC, cache=CACHE) -> Measurement:
    """compile() + parse(): the full record, or {"ok": False, "error": ...}."""
    r = compile(src, core, api, stage, malioc, cache, need_raw=True)
    if not r["ok"]:
        return r
    return {"ok": True, "cached": r.get("cached", False),
            "source_sha1": hashlib.sha1(src if isinstance(src, bytes) else src.encode()).hexdigest(),
            **parse(r["raw"])}


def spills(rec):
    """True if any variant spills (parsed record) or the v2 entry spills."""
    if "variants" in rec:
        return any(v["spilling"] for v in rec["variants"].values())
    return bool(rec["props"].get("has_stack_spilling"))
