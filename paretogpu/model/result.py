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
