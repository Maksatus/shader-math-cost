from paretogpu.features.bench import BENCH
from paretogpu.features.compare import COMPARE
from paretogpu.features.cost import COST
from paretogpu.features.device import DEVICE
from paretogpu.features.frame import FRAME
from paretogpu.features.hotspots import HOTSPOTS
from paretogpu.features.matcompare import MATCOMPARE
from paretogpu.features.matshader import MATSHADER

COMMANDS = [FRAME, COST, DEVICE, COMPARE, MATCOMPARE, MATSHADER, HOTSPOTS, BENCH]
BY_NAME = {c.name: c for c in COMMANDS}
RESULT_KINDS = [c.result for c in COMMANDS if c.result]
