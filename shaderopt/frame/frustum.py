"""Screen coverage of everything the game camera draws (plan item K1.3).

Runs unity/ShaderoptFrustum.cs in the open editor (Unity CLI run_script) and reads <out>/frustum.json:
one record per renderer x submesh x material with the shader, material keywords, render queue, vertices and
two pixel counts — "raster" (no depth test: everything in the frustum, with overdraw and self-overlap) and
"visible" (depth test against the depth of the opaque renderers).
"""
import json
import os

from shaderopt.unity import export as unity

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "unity", "ShaderoptFrustum.cs")


class FrustumError(RuntimeError):
    pass


def run(project, out, camera=""):
    project, out = os.path.abspath(project), os.path.abspath(out)
    if not unity.editor_ready(project, allow_play=True):
        raise FrustumError(f"the editor of {project} does not answer Unity CLI or is busy (compiling, importing)")
    os.makedirs(out, exist_ok=True)
    for f in ("frustum.json", "frustum.error"):
        if os.path.exists(os.path.join(out, f)):
            os.remove(os.path.join(out, f))
    config = os.path.join(out, "frustum_config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"out": out, "camera": camera}, f)
    d = unity._cli_json(["command", "run_script", "--project-path", project, "--timeout", "600", "--timeout_ms", "600000",
                         "--file", os.path.abspath(SCRIPT), "--entry", "ShaderoptFrustum.Run",
                         "--args", json.dumps([config])], 700)
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success") or not str(res.get("result", "")).startswith("ok"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise FrustumError(f"{res.get('result') or diag or res.get('errorDetails') or unity._errors(d)}"[:3000])
    with open(os.path.join(out, "frustum.json"), encoding="utf-8") as f:
        return json.load(f)
