"""Split Unity "Compile and show code" output into per-variant shader files.

GLES3x export -> .vert / .frag (GLSL); Vulkan export -> .vert.spv / .frag.spv
(the SPIR-V disassembly is assembled back into a binary, see spirv.py).
One file per pass x keyword set x stage, plus manifest.json with the shader,
pass, keywords, stage and platform of each file. A variant Unity could not compile
("// Compile errors generating this shader.") is reported as an error. Files of an
earlier split of the same shader and platform (listed in its manifest.json) are
removed first, so a smaller export leaves no stale variants behind. Output goes to
corpus/real/<shader file name>/ (corpus/real/<name>_vulkan/ for Vulkan; an export with both
platforms gives both folders) by default
(not in git: project shaders must not reach the public repository).

Usage: python -m paretogpu.adapters.unity.split "Compiled-MyShader.shader" [...] [--out real]
"""
import argparse
import json
import os
import re

from paretogpu.adapters.unity import spirv
from paretogpu.model.variant import slug

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "corpus", "real")

STAGES = {"VERTEX": "vert", "FRAGMENT": "frag"}
VK_STAGES = {"Vertex": "vert", "Fragment": "frag"}
_VK_END = re.compile(r"^(Keywords:|Global Keywords:|Local Keywords:|/{10,}|\s*\}\s*$|\s*Pass \{)")


def blocks(lines, start):
    """Top-level #ifdef VERTEX / FRAGMENT blocks after 'Shader Disassembly:' -> [(stage, text)], end line."""
    out, i = [], start
    while i < len(lines):
        line = lines[i].strip()
        m = re.fullmatch(r"#ifdef (VERTEX|FRAGMENT|HULL|DOMAIN|GEOMETRY)", line)
        if m:
            depth, body = 1, []
            i += 1
            while depth:
                s = lines[i].strip()
                if re.match(r"#if(def|ndef)?\b", s):
                    depth += 1
                elif s.startswith("#endif"):
                    depth -= 1
                if depth:
                    body.append(lines[i])
                i += 1
            out.append((m.group(1), "\n".join(body).strip("\n") + "\n"))
            continue
        if line.startswith(("Keywords:", "Global Keywords:", "Local Keywords:", "Pass {", "}")) and out:
            break
        i += 1
    return out, i


def vk_blocks(lines, start):
    """'Disassembly for Vertex:' ... sections of a Vulkan export -> [(stage, text)], end line."""
    out, stage, body, i = [], None, [], start
    while i < len(lines) and not _VK_END.match(lines[i]):
        if m := re.match(r"Disassembly for (\w+):", lines[i].strip()):
            if stage:
                out.append((stage, "\n".join(body)))
            stage, body = m.group(1), []
        elif stage:
            body.append(lines[i])
        i += 1
    if stage:
        out.append((stage, "\n".join(body)))
    return [(s, t) for s, t in out if t.strip() and t.strip() != "Not present."], i


def es31(text):
    """Projects with minimum GLES 3.0 get '#version 300 es' with UNITY_BINDING(x) = layout(binding = x),
    which 3.00 does not allow and malioc rejects. Every target Mali core runs ES 3.1+, so such
    shaders are compiled as '#version 310 es' (what Unity does on an ES 3.1+ context: проверить)."""
    if text.startswith("#version 300 es") and "layout(binding" in text:
        return "#version 310 es" + text[len("#version 300 es"):], "300 es -> 310 es"
    return text, None


def clear(out_dir):
    """Remove the files an earlier split wrote into out_dir (its manifest.json and the files listed there)."""
    man = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(man):
        return
    with open(man, encoding="utf-8") as f:
        old = json.load(f)
    for m in old:
        p = os.path.join(out_dir, m["file"])
        if os.path.isfile(p):
            os.remove(p)
    os.remove(man)


def split(path, out_root):
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    shader = next((m.group(1) for l in lines if (m := re.match(r'Shader "(.+)" \{', l))), "?")
    name = os.path.splitext(os.path.basename(path))[0]
    files, manifest, errors, used = [], [], [], set()
    pass_name, pass_idx, keywords, platform, tier, stage_name = None, -1, [], None, None, None
    i = 0
    while i < len(lines):
        l = lines[i].strip()
        if l == "Pass {":
            pass_idx += 1
            pass_name = f"pass{pass_idx}"
        elif l.startswith("Name ") and pass_idx >= 0:
            pass_name = l[5:].strip('"')
        elif l.startswith("Keywords:"):
            kw = l[len("Keywords:"):].split()
            keywords = [] if kw == ["<none>"] else kw
            tier = None
        elif m := re.match(r"-- Hardware tier variant: Tier (\d+)", l):
            tier = int(m.group(1))  # built-in pipeline GLES exports: one variant per tier
        elif m := re.match(r'-- (\w+) shader for "(\w+)"', l):
            stage_name, platform = m.group(1).lower(), m.group(2)
        elif l.startswith("// Compile errors generating this shader"):
            errors.append(f"pass {pass_idx} {pass_name} [{' '.join(keywords) or 'no keywords'}] {stage_name} "
                          f"{platform}: Unity could not compile this variant")
        elif l == "Shader Disassembly:":
            vk = platform == "vulkan"
            found, i = vk_blocks(lines, i + 1) if vk else blocks(lines, i + 1)
            for stage, text in found:
                ext = (VK_STAGES if vk else STAGES).get(stage)
                if not ext:
                    continue
                base = f"{pass_idx:02d}_{slug(pass_name)}__{slug('_'.join(keywords))}" + (f"__tier{tier}" if tier else "")
                fn, n = f"{base}.{ext}", 1
                while (platform, fn) in used:  # never overwrite another variant
                    n += 1
                    fn = f"{base}__{n}.{ext}"
                used.add((platform, fn))
                patched = None
                if vk:
                    fn += ".spv"
                    try:
                        data = spirv.assemble(text)
                    except spirv.SpirvTextError as e:
                        errors.append(f"{fn}: {e}")
                        continue
                else:
                    text, patched = es31(text)
                    data = text.encode()
                files.append((platform, fn, data))
                manifest.append({"file": fn, "shader": shader, "pass": pass_name, "pass_index": pass_idx,
                                 "keywords": keywords, "tier": tier,
                                 "stage": {"vert": "vertex", "frag": "fragment"}[ext],
                                 "platform": platform, "source": os.path.basename(path),
                                 **({"patched": patched} if patched else {})})
            continue
        i += 1
    # one folder per platform: <name>/ for gles3, <name>_<platform>/ for the others
    for plat in dict.fromkeys(p for p, _, _ in files):
        clear(os.path.join(out_root, slug(name) + ("" if plat == "gles3" else f"_{plat}")))
    outputs = {}
    for (plat, fn, data), m in zip(files, manifest):
        out_dir = os.path.join(out_root, slug(name) + ("" if plat == "gles3" else f"_{plat}"))
        if out_dir not in outputs:
            os.makedirs(out_dir, exist_ok=True)
            outputs[out_dir] = []
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(data)
        outputs[out_dir].append(m)
    for out_dir, man in outputs.items():
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(man, f, indent=1, ensure_ascii=False)
    return shader, outputs, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=os.path.normpath(REAL))
    args = ap.parse_args()
    for p in args.files:
        shader, outputs, errors = split(p, args.out)
        for out_dir, man in outputs.items():
            passes = list(dict.fromkeys(m["pass"] for m in man))
            print(f"{shader} ({man[0]['platform']}): {len(man)} files, passes {', '.join(passes)} -> {out_dir}")
        for e in errors:
            print(f"  FAIL {e}")


if __name__ == "__main__":
    main()
