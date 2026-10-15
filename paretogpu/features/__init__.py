from paretogpu.features.bench import BENCH
from paretogpu.features.compare import COMPARE
from paretogpu.features.cost import COST
from paretogpu.features.device import DEVICE
from paretogpu.features.frame import FRAME
from paretogpu.features.matcompare import MATCOMPARE

COMMANDS = [FRAME, COST, DEVICE, COMPARE, MATCOMPARE, BENCH]
BY_NAME = {c.name: c for c in COMMANDS}
RESULT_KINDS = [c.result for c in COMMANDS if c.result]
