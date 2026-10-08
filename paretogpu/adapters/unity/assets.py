"""Materials of a Unity project and the shader variants they select (the "project shaders" tab of the frame report).

Reads the .mat files as text, without Unity: the shader (by GUID -> .shader / .shadergraph -> shader name), the
material's keywords (m_ValidKeywords; m_ShaderKeywords in old files) and the passes it switches off
(disabledShaderPasses). Global keywords (lights, fog, shadows) are not in a material: the pipeline sets them, so
they are taken from the frame snapshots (variants_of()).

  scan(project)  -> [{"path", "name", "shader", "guid", "keywords", "disabled_passes", "error"}]
"""
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
    """Assets/ and the embedded packages in Packages/ (packages from the registry are not the project's)."""
    roots = [os.path.join(project, "Assets")]
    pk = os.path.join(project, "Packages")
    if os.path.isdir(pk):
        roots += [os.path.join(pk, d) for d in sorted(os.listdir(pk)) if os.path.isdir(os.path.join(pk, d))]
    return [r for r in roots if os.path.isdir(r)]


def shader_roots(project):
    """Where shaders can live: the material roots plus the package cache (URP, pipelines from packages)."""
    roots = material_roots(project)
    cache = os.path.join(project, "Library", "PackageCache")
    if os.path.isdir(cache):
        roots += [os.path.join(cache, d) for d in sorted(os.listdir(cache))]
    return roots


def shader_name(path):
    """Shader name of a .shader (the Shader "..." line) or a .shadergraph (its category path + file name)."""
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
    """{guid: shader name} of every .shader / .shadergraph of the project and its packages."""
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
    """The YAML list under `key:` (one `- item` per line) or an inline [] list."""
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
    """Every material of the project with its shader name, keywords and switched-off passes."""
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
