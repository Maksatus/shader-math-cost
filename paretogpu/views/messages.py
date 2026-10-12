"""The words of the results, in one place: core/ and app/ give warnings as {"code", ...params} (and a part's
explanation as counts, a vertex candidate's label as a code), this module turns them into the report's language for
the console, and the HTML pages get the same templates embedded (views/html.py, `MSG`) to do it in the browser.

  text(warning)          "{code}" of WARNINGS filled with the warning's parameters; "side": a prefix ("A: ...")
  explain_text(ex)       core/ablation.explain(): what a part does, as a short phrase
  export()               everything a page needs: the templates, the operations, the labels
"""

WARNINGS = {
    # core/compare.py
    "no_common_cores": "У расчётов нет общих ядер: сравнить нечего.",
    "cores_differ": "Разные наборы ядер: сравниваются только общие ({cores}).",
    "api_differs": "Разный API цен: A {a}, B {b}. Цены Vulkan и GLES отличаются сами по себе.",
    "malioc_differs": "Разные версии malioc (A {a}, B {b}): часть разницы даёт компилятор, а не шейдеры.",
    "loops_n_differs": "Разное число итераций динамических циклов (A n = {a}, B n = {b}).",
    "resolution_differs": "Разное разрешение кадра (A {a}, B {b}): пиксели несопоставимы.",
    "quality_differs": "Разный уровень качества Unity (A {a}, B {b}).",
    "pipeline_differs": "Разный ассет пайплайна (A {a}, B {b}): проходы и эффекты кадров могут отличаться.",
    "scale_differs": "Разный масштаб рендера (A {a}, B {b} от Game view).",
    "projects_differ": "Расчёты из разных проектов.",
    "unpriced_draws": "Draw без цены: в A {a}, в B {b}. Их стоимость не входит в сравнение.",
    "unchecked": "шейдеры не перекомпилировались перед расчётом, цены могут быть устаревшими.",
    # core/profile.py
    "settings_not_recorded": "Снимок сделан до записи настроек кадра: неизвестно, в каком качестве и разрешении он "
                             "снят. Снимите кадр заново.",
    "build_target_not_mobile": "Платформа сборки в редакторе — {target}, а не Android: качество, keywords и варианты "
                               "кадра могут быть не те, что на устройстве.",
    "scale_not_from_asset": "RenderScale в ассете пайплайна {asset}, а кадр нарисован в масштабе {frame} от Game view "
                            "({frame_size} из {screen}): масштаб задаётся не только ассетом, верен масштаб кадра.",
    # app/materials.py
    "pass_unpriced": "проход {pass}: нет цены ({error})",
    "variants_unchecked": "Варианты не сверены с шейдерами (редактор не отвечал или --no-compile): цены могут быть "
                          "устаревшими.",
    "no_snapshots": "В проекте нет снимков кадра: глобальные keywords пайплайна (свет, тени, Forward+) не добавлены, "
                    "а проходы взяты все, кроме служебных.",
}

# what makes code heavy (core/ablation.OPS keys), the heaviest first: one, few, many of the counted thing
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

# core/vertexmove.py: what kind of affine value a candidate is
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
    """The warning as the report says it. A plain string (an older result) is returned as it is."""
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
    """The templates for the pages (views/html.py embeds them as MSG)."""
    return {"warnings": WARNINGS, "ops": OPS, "plain": PLAIN, "in_loop": IN_LOOP, "vertexmove": VERTEXMOVE}
