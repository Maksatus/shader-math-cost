# Shader Math Cost (Mali)

Сколько стоят функции в шейдерах на GPU Mali: 120 функций, 32 GPU, OpenGL ES и Vulkan.

**Сайт:** https://maksatus.github.io/shader-math-cost/

Стоимость указана в FMA (одно умножение-сложение): «sin = 8» значит, что sin стоит как 8 обычных операций.

## Локальный интерфейс

```
python -m shaderopt ui
```

Или двойной клик по `start.bat`: сначала проверяет, что установлены Python 3.10+, malioc, RenderDoc и Unity (`python -m shaderopt.doctor`) и пишет, чего не хватает и где это взять (сам ничего не ставит), затем запускает интерфейс. Аргументы передаются в `ui` (например `start.bat --port 8800`).

Открывает окно (Edge в режиме приложения) на `http://127.0.0.1:8765` с тремя вкладками: таблица цен функций (этот сайт), кнопки для команд `shaderopt` с прогрессом каждого шага и оценкой оставшегося времени, отчёты снимков кадров из `shaderopt/out`. Отчёты открываются только локально и в git не попадают.

## Обновить данные

Нужны Python 3 и [Arm Performance Studio](https://developer.arm.com/Tools%20and%20Software/Arm%20Performance%20Studio) (malioc).

```
python bench/run.py
python bench/build_site.py
```

Функции задаются в `bench/functions.py`.

## Аппроксимации

Быстрые замены функций (кнопка «≈ быстрее» в таблице) лежат в `docs/approx.js`. Пересобрать: нужны numpy, scipy и malioc, PySR только для поиска формы формулы (`bench/approx/pysr_*.py`).

```
cd bench/approx
python build_approx.py
```

Формулы, диапазоны и результаты замеров на устройствах задаются в `bench/approx/build_approx.py`.
