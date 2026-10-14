"""core/ and app/ emit warnings as {"code", **params}; rendered here for the console and, via export() embedded as
MSG (views/html.py), in the browser."""

WARNINGS = {
    "no_common_cores": "У расчётов нет общих ядер: сравнить нечего.",
    "cores_differ": "Разные наборы ядер: сравниваются только общие ({cores}).",
    "api_differs": "Разный API цен: A {a}, B {b}. Цены Vulkan и GLES отличаются сами по себе.",
    "code_differs": "Расчёты сделаны разными версиями ParetoGPU (A {a}, B {b}): часть разницы может дать сам "
                    "инструмент, а не шейдеры или сцена. Пересчитайте оба снимка («Пересчитать» в списке снимков).",
    "malioc_differs": "Разные версии malioc (A {a}, B {b}): часть разницы даёт компилятор, а не шейдеры.",
    "render_msaa": "Разный MSAA основной цели (A {a}×, B {b}×).",
    "render_color": "Разный формат основной цели (A {a}, B {b}).",
    "render_shadow_maps": "Разные карты теней (A {a}, B {b}): другой профиль качества или каскады.",
    "render_passes": "Разный набор проходов: только в A — {a}; только в B — {b}. Кадры сняты с разными настройками "
                     "рендера (профиль качества, эффекты), их цена несопоставима.",
    "pixel_methods_differ": "Пиксели посчитаны по-разному (A: {a}; B: {b}): «renderdoc» — запуски пиксельного "
                            "шейдера на GPU ПК, «samples» — фрагменты телефона после теста глубины. Часть разницы "
                            "«объём работы» — разница способов, а не сцены.",
    "vertex_methods_differ": "Вершины посчитаны по-разному (A: {a}; B: {b}): «renderdoc» — запуски вершинного шейдера "
                             "на GPU ПК (его кэш вершин меньше, чем у Mali: в среднем +14%, у плотных сеток до +50%), "
                             "«indices» — уникальные вершины draw на телефоне. Часть разницы «объём работы» — разница "
                             "способов, а не сцены.",
    "resolution_differs": "Разное разрешение кадра (A {a}, B {b}): пиксели несопоставимы.",
    "quality_differs": "Разный уровень качества Unity (A {a}, B {b}).",
    "pipeline_differs": "Разный ассет пайплайна (A {a}, B {b}): проходы и эффекты кадров могут отличаться.",
    "scale_differs": "Разный масштаб рендера (A {a}, B {b} от Game view).",
    "projects_differ": "Расчёты из разных проектов.",
    "unpriced_draws": "Draw без цены: в A {a}, в B {b}. Их стоимость не входит в сравнение.",
    "unchecked": "шейдеры не перекомпилировались перед расчётом, цены могут быть устаревшими.",
    "build_target_not_mobile": "Платформа сборки в редакторе — {target}, а не Android: качество, keywords и варианты "
                               "кадра могут быть не те, что на устройстве.",
    "rd_count_mismatch": "У {n} draw вызовы RenderDoc не сходятся с событием Frame Debugger (индексы, вершины или "
                         "пиксели больше цели): их пиксели и вершины могут быть от другого draw — отмечены «!».",
    "device_discard_unshaded": "У {n} draw с discard (alpha clip) пиксели не пересчитаны без discard ({error}): они "
                               "занижены, считаны только выжившие фрагменты.",
    "pass_unpriced": "проход {pass}: нет цены ({error})",
    "variants_unchecked": "Варианты не сверены с шейдерами (редактор не отвечал или --no-compile): цены могут быть "
                          "устаревшими.",
    "no_snapshots": "В проекте нет снимков кадра: глобальные keywords пайплайна (свет, тени, Forward+) не добавлены, "
                    "а проходы взяты все, кроме служебных.",
}

# (core/ablation.OPS key, Russian plural forms one/few/many); heaviest first: explain_text keeps the first 4
OPS = [
    ("buffers", "чтение буфера по индексу", "чтения буферов по индексу", "чтений буферов по индексу"),
    ("tex", "выборка текстуры", "выборки текстур", "выборок текстур"),
    ("hash", "хэш на sin", "хэша на sin", "хэшей на sin"),
    ("trig", "sin/cos/atan", "sin/cos/atan", "sin/cos/atan"),
    ("powexp", "pow/exp/log", "pow/exp/log", "pow/exp/log"),
    ("roots", "корень или нормализация", "корня или нормализации", "корней или нормализаций"),
    ("div", "деление", "деления", "делений"),
    ("deriv", "производная", "производные", "производных"),
    ("ints", "целочисленная операция", "целочисленные операции", "целочисленных операций"),
    ("matrix", "строка матрицы", "строки матриц", "строк матриц"),
    ("select", "сравнение / выбор", "сравнения / выбора", "сравнений / выборов"),
]
PLAIN = "простая арифметика"
IN_LOOP = "в цикле (×{n}): "

# core/vertexmove.py candidate kinds
VERTEXMOVE = {
    "rotate": "поворот (Rotate с углом из свойства)",
    "flipbook": "флипбук / анимация по времени",
    "time_offset": "смещение по времени",
    "tiling_offset": "Tiling / Offset",
    "uv_affine": "масштаб и сдвиг uv (смещения выборок)",
    "affine": "аффинное преобразование varying",
}


class _Params(dict):
    def __missing__(self, key):
        return "?"


def text(w):
    """Plain strings come from older results and pass through."""
    if isinstance(w, str):
        return w
    body = WARNINGS.get(w.get("code"), w.get("code") or "").format_map(_Params(w))
    return f"{w['side']}: {body}" if w.get("side") else body


def plural(n, one, few, many):
    k = n % 100
    word = many if 11 <= k <= 14 else one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many
    return f"{n} {word}"


def explain_text(ex):
    """core/ablation.explain() -> "в цикле (×2): 3 выборки текстур, 2 деления"."""
    if isinstance(ex, str):
        return ex
    ops = ex.get("ops") or {}
    words = [plural(ops[key], one, few, many) if one != few else f"{ops[key]} {one}"
             for key, one, few, many in OPS if ops.get(key)]
    out = ", ".join(words[:4]) or PLAIN
    if ex.get("loop"):
        out = IN_LOOP.format(n=ex["n"] if ex.get("n") is not None else "n") + out
    return out


def export():
    return {"warnings": WARNINGS, "ops": OPS, "plain": PLAIN, "in_loop": IN_LOOP, "vertexmove": VERTEXMOVE}
