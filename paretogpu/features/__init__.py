"""The commands of `python -m paretogpu`: one module per feature, each declares its Command (features/spec.py)."""
from paretogpu.features.compare import COMPARE
from paretogpu.features.cost import COST
from paretogpu.features.frame import FRAME
from paretogpu.features.hotspots import HOTSPOTS
from paretogpu.features.matcompare import MATCOMPARE
from paretogpu.features.matshader import MATSHADER
from paretogpu.features.measure import EXPORT, MEASURE, REPORT

COMMANDS = [MEASURE, REPORT, EXPORT, FRAME, COST, COMPARE, MATCOMPARE, MATSHADER, HOTSPOTS]
BY_NAME = {c.name: c for c in COMMANDS}
RESULT_KINDS = [c.result for c in COMMANDS if c.result]
