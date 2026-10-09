"""A result: what one run of a command left (a page and its data), described the same way for every command.

<folder>/<kind>.result.json holds a ResultMeta; the kinds (what their title and summary are) are declared by the
commands (features/: ResultKind), the store lists them (store/results.py), the UI shows them all the same way.
"""
from typing import TypedDict

SUFFIX = ".result.json"


class ResultMeta(TypedDict, total=False):
    kind: str         # cost | compare | matcompare | matshader | hotspots ...
    id: str           # unique among the results of its kind: <folder name> or <snapshot>/<kind>
    title: str
    created: float    # when it was computed (the data's computed_at)
    project: str      # the Unity project, if any
    snapshot: str     # the snapshot it is about, if any
    folder: str       # absolute folder of its files (filled in when listed)
    data: str         # its JSON file, relative to the folder
    page: str         # its HTML page, relative to the folder
    summary: dict     # what a list of results shows about it (kind-specific)
