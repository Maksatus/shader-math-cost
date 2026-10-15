"""Unity materials read as text, without Unity. Global keywords (lights, fog, shadows) are set by the pipeline, not
materials: they come from the frame snapshots."""
import json
import os
import re

BUILTIN_GUID = "0000000000000000f000000000000000"
SKIP_DIRS = {".git", "Library", "Temp", "Logs", "obj", "UserSettings"}

_SHADER_RE = re.compile(r'^\s*Shader\s+"([^"]+)"', re.M)
_MAT_SHADER = re.compile(r"^\s*m_Shader:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-f]+))?", re.M)
_META_GUID = re.compile(r"^guid:\s*([0-9a-f]{32})", re.M)


def _walk(root, ext):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for fn in files:
            if fn.endswith(ext):
                yield os.path.join(d, fn)


def material_roots(project):
    """Assets/ + embedded Packages/ (registry packages are not the project's)."""
    roots = [os.path.join(project, "Assets")]
    pk = os.path.join(project, "Packages")
    if os.path.isdir(pk):
        roots += [os.path.join(pk, d) for d in sorted(os.listdir(pk)) if os.path.isdir(os.path.join(pk, d))]
    return [r for r in roots if os.path.isdir(r)]


def shader_roots(project):
    roots = material_roots(project)
    cache = os.path.join(project, "Library", "PackageCache")
    if os.path.isdir(cache):
        roots += [os.path.join(cache, d) for d in sorted(os.listdir(cache))]
    return roots


def shader_name(path):
    """.shader: its Shader "..." line; .shadergraph: <graph category path>/<file name>."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    if path.endswith(".shader"):
        m = _SHADER_RE.search(text)
        return m.group(1) if m else None
    stem = os.path.splitext(os.path.basename(path))[0]
    category = "Shader Graphs"
    for block in text.split("\n\n"):  # a .shadergraph is several JSON objects one after another
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        if isinstance(obj, dict) and "m_Path" in obj and obj.get("m_Type", "").endswith("GraphData"):
            category = obj["m_Path"] or ""
            break
    return f"{category}/{stem}" if category else stem


def shader_guids(project):
    out = {}
    for root in shader_roots(project):
        for ext in (".shader.meta", ".shadergraph.meta"):
            for meta in _walk(root, ext):
                try:
                    with open(meta, encoding="utf-8", errors="replace") as f:
                        m = _META_GUID.search(f.read())
                except OSError:
                    continue
                if m:
                    name = shader_name(meta[:-len(".meta")])
                    if name:
                        out[m.group(1)] = name
    return out


def _list_after(text, key):
    """YAML block list or inline [] list under `key:`; None if the key is absent."""
    m = re.search(rf"^(\s*){key}:(.*)$", text, re.M)
    if not m:
        return None
    inline = m.group(2).strip()
    if inline:
        return [x.strip() for x in inline.strip("[]").split(",") if x.strip()]
    items, indent = [], m.group(1)
    for line in text[m.end():].splitlines()[1:]:
        s = line.strip()
        if not s.startswith("- ") or not line.startswith(indent):
            break
        items.append(s[2:].strip())
    return items


def parse_material(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    name = re.search(r"^\s*m_Name:\s*(.*)$", text, re.M)
    shader = _MAT_SHADER.search(text)
    keywords = _list_after(text, "m_ValidKeywords")
    if keywords is None:  # before Unity 2021.2: one space separated string
        m = re.search(r"^\s*m_ShaderKeywords:\s*(.*)$", text, re.M)
        keywords = (m.group(1).split() if m else [])
    return {"name": name.group(1).strip() if name else os.path.basename(path),
            "file_id": int(shader.group(1)) if shader else 0, "guid": shader.group(2) if shader else None,
            "keywords": sorted(set(keywords)), "disabled_passes": _list_after(text, "disabledShaderPasses") or []}


def scan(project):
    """-> [{"path", "name", "shader", "guid", "keywords", "disabled_passes", "file_id"[, "error"]}]."""
    project = os.path.abspath(project)
    guids = shader_guids(project)
    out = []
    for root in material_roots(project):
        for path in _walk(root, ".mat"):
            rel = os.path.relpath(path, project).replace("\\", "/")
            try:
                m = parse_material(path)
            except OSError as e:
                out.append({"path": rel, "error": str(e)})
                continue
            m["path"] = rel
            if not m["guid"]:
                m["error"] = "no shader"
            elif m["guid"] == BUILTIN_GUID:
                m["error"] = f"built-in shader (fileID {m['file_id']})"
            elif m["guid"] not in guids:
                m["error"] = f"shader {m['guid']} not found in the project"
            m["shader"] = guids.get(m["guid"])
            out.append(m)
    return sorted(out, key=lambda m: m["path"])


_RP_REF = r"\{fileID:\s*-?\d+(?:,\s*guid:\s*([0-9a-f]{32}))?"


def build_pipeline(project):
    """The pipeline asset an Android build starts with (its default quality level's, else Graphics settings'), read
    from ProjectSettings: {"name", "path", "hash": None, "quality"}; None if the build has no pipeline asset. A scene
    may still swap it at run time."""
    def read(name):
        with open(os.path.join(project, "ProjectSettings", name), encoding="utf-8", errors="replace") as f:
            return f.read()
    quality = read("QualitySettings.asset")
    levels = re.split(r"^  - serializedVersion:", quality.split("m_QualitySettings:", 1)[1], flags=re.M)[1:]
    m = re.search(r"^  m_PerPlatformDefaultQuality:\s*\n(?:    \w+: \d+\n)*?    Android: (\d+)", quality, re.M)
    level = levels[int(m.group(1))] if m and int(m.group(1)) < len(levels) else None
    guid = None
    if level:
        g = re.search(r"^    customRenderPipeline: " + _RP_REF, level, re.M)
        guid = g and g.group(1)
    if not guid:
        g = re.search(r"^  m_CustomRenderPipeline: " + _RP_REF, read("GraphicsSettings.asset"), re.M)
        guid = g and g.group(1)
    if not guid:
        return None
    path = None
    for root in material_roots(project):
        for meta in _walk(root, ".asset.meta"):
            with open(meta, encoding="utf-8", errors="replace") as f:
                found = _META_GUID.search(f.read())
            if found and found.group(1) == guid:
                path = os.path.relpath(meta[:-len(".meta")], project).replace(os.sep, "/")
                break
        if path:
            break
    qname = re.search(r"^    name: (.*)$", level, re.M) if level else None
    return {"name": path and os.path.splitext(os.path.basename(path))[0], "path": path,
            "hash": None, "quality": qname and qname.group(1).strip()}
