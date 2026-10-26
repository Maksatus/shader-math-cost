"""Android formats of the project's textures by name (cs/ParetoGpuTextures.cs)."""
from paretogpu.adapters.unity import bridge
from paretogpu.adapters.unity.cli import UnityError

SCRIPT = "ParetoGpuTextures.cs"
ANSWER = "android_formats"  # <out>/android_formats.json: the editor's answer, kept as the snapshot's cache


class TexturesError(UnityError):
    code = "textures"


def android_formats(project, names, out, timeout=300):
    """-> {name: [{"path", "format"}]}, [] for a name with no texture asset (render targets, built-ins)."""
    return bridge.call(project, SCRIPT, "Formats", out, ANSWER, {"names": sorted(set(names))},
                       TexturesError, timeout)["textures"]
