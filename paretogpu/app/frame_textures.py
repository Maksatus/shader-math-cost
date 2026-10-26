import json
import os

from paretogpu.adapters.unity import cli as unity
from paretogpu.adapters.unity.textures import ANSWER, android_formats
from paretogpu.core import textures
from paretogpu.views.reporter import CONSOLE

FILE = ANSWER + ".json"


def formats(frame_dir, frame, capture, project, ask_editor=True, rep=CONSOLE):
    """-> {texture name: [{"path", "format"}]} of a PC snapshot's textures from the importers (kept in the
    snapshot), None when the editor cannot answer; {} for a device frame (its formats are the phone's own)."""
    if frame.get("device"):
        return {}
    kept = os.path.join(frame_dir, FILE)
    if os.path.exists(kept):
        with open(kept, encoding="utf-8") as f:
            return json.load(f)["textures"]
    if not (ask_editor and project and unity.editor_ready(project, allow_play=True)):
        return None
    rep.log("  Android formats of the frame's textures from the editor")
    return android_formats(project, textures.asset_names(capture), frame_dir)
