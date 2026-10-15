# Shader Math Cost (Mali)

Цена функций в шейдерах на GPU Mali (121 функция, 26 GPU, GLES и Vulkan), в FMA: «sin = 8» — как 8 обычных операций.
Сайт: https://maksatus.github.io/shader-math-cost/.

## ParetoGPU — цена кадра на Mali

Нужно: Python 3.10+ с numpy, [Arm Performance Studio](https://developer.arm.com/Tools%20and%20Software/Arm%20Performance%20Studio) (malioc), RenderDoc, Unity с открытым проектом на Vulkan.
Для кадра с телефона ещё adb и RenderDoc for Arm GPUs (входит в Arm Performance Studio).
Запуск: `start.bat` или `python -m paretogpu ui`; проверка окружения — `python -m paretogpu.doctor`.

- **Проверка кадра** — Unity на Vulkan (`-force-vulkan`, RenderDoc загружен при запуске) в Play Mode → «Проверить кадр»: снимок, цена на Mali, самые дорогие шейдеры.
  С телефона (USB, development build, Vulkan): выберите телефон → «Проверить кадр» запустит игру → «Снять кадр».
  ПК и телефон идут одним конвейером Vulkan / SPIR-V: варианты из «Compile and show code», пиксели — квады по геометрии захвата, повторы циклов — подменённым шейдером-счётчиком.
- **Отчёты**, **Сравнение** (кадры, материалы), **Материал**, **Цена функций**, **Справочник**.

Снимки и отчёты — в `paretogpu/out` (не в git). Консоль: `python -m paretogpu -h`.

## Данные сайта

- `python -m paretogpu bench` — таблица цен (`docs/`), функции в `paretogpu/model/functions.py`.
- Аппроксимации: `bench/approx/build_approx.py` → `docs/approx.js` (numpy, scipy); проверка полным перебором — `bench/approx/check.py`.

План: `plans/shader-optimizer-plan.md`.
