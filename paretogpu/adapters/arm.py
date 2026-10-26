"""Arm Performance Studio: files of its newest install (malioc, RenderDoc for Arm GPUs, Streamline)."""
import glob
import os
import re


def studio_file(*rel):
    """-> path of rel inside the newest "Arm Performance Studio <version>" that has it, None if none does."""
    found = []
    for d in glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Arm", "Arm Performance Studio *")):
        m = re.search(r"(\d+(?:\.\d+)*)$", d)
        path = os.path.join(d, *rel)
        if m and os.path.exists(path):
            found.append((tuple(int(x) for x in m.group(1).split(".")), path))
    return max(found)[1] if found else None
