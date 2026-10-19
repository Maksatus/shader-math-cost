"use strict";
const FP16_TIP = "Доля вычислений в half: half2/half4 в FMA идут вдвое быстрее float, скалярный half — как float";
const METHOD_TIPS = {
  quads: "Квады 2×2 геометрии draw: потоки фрагментного шейдера, включая вспомогательные",
  renderdoc: "Счётчик RenderDoc (PSInvocations / VSInvocations / CSInvocations)",
  samples: "Счётчик сэмплов с телефона (Samples Passed): пиксели, прошедшие тест глубины, без вспомогательных потоков квадов (оценка)",
  none: "Число не получено: цены у этой части нет",
  indices: "Уникальные vertices draw (по буферу индексов) × instances, с телефона",
  calls: "Индексы вызова × instances, с телефона: каждый индекс как vertex (оценка сверху)",
  mesh: "Число vertices меша из Frame Debugger: RenderDoc vertices не дал (оценка)",
  dispatch: "Группы × размер группы dispatch: RenderDoc потоков не дал",
};
const methodTag = m => m ? `<span class="m ${esc(m)}"${METHOD_TIPS[m] ? ` title="${esc(METHOD_TIPS[m])}" style="cursor:help"` : ""}>${esc(m)}</span>` : "";
// tooltips of column headers
const AUTO_TIPS = {
  "цена": "Часть разницы от цены шейдера на пиксель / vertex: эффект правки шейдера",
  "объём": "Часть разницы от числа пикселей / vertices: ракурс, сцена, LOD, overdraw",
  "состав": "Варианты, которые есть только в A или только в B: поменялись keywords, шейдер добавили или убрали",
  "Разница": "B − A, млн циклов: минус и зелёный — стало дешевле",
  "Registers": "Рабочие регистры на поток: больше 32 — вдвое меньше потоков на ядре",
  "регистры пикселя": "Рабочие регистры фрагментного шейдера: больше 32 — вдвое меньше потоков",
  "регистры vertex": "Рабочие регистры vertex шейдера",
  "fp16, %": FP16_TIP, "fp16 пикселя, %": FP16_TIP,
  "Bottleneck (самый загруженный unit)": "Unit ядра, который загружен сильнее всех; цена — сумма всех units",
  "bottleneck пикселя": "Unit ядра, который загружен сильнее всех; цена — сумма всех units",
  "путь пикселя": "longest — самый длинный путь malioc; loop n=… — динамические циклы прогнаны n раз; total — длинный путь не посчитан",
  "Динамические циклы": "Есть ли у шейдера циклы неизвестной длины: тогда цена зависит от n",
  "Флаги": "Проблемы шейдера: spilling, много регистров, мало half, bottleneck в SFU, динамический цикл",
  "Keywords варианта": "Keywords материала + глобальные keywords пайплайна для pass (свет, тени, туман, Forward+)",
  "n циклов": "Сколько раз выполняются динамические циклы шейдера: источники света и пробы на пиксель, шаги луча",
  "Кто дешевле": "На сколько дешевле — в % от цены более дорогого",
  "Пиксель": "Цена фрагментного шейдера: циклы на один пиксель", "Vertex": "Цена vertex шейдера: циклы на один vertex (Position + Varying)",
  "Pixels": "Потоки фрагментного шейдера (метка рядом — откуда число)", "Vertices": "Сколько раз выполнился vertex шейдер (метка рядом — откуда число)",
  "Threads": "Сколько потоков compute-шейдера запущено",
  "Cycles / pixel": "Цена одного пикселя и bottleneck (самый загруженный unit)", "Cycles / vertex": "Цена одного vertex (Position + Varying) и bottleneck",
  "Cycles / thread": "Цена одного потока compute-шейдера и bottleneck",
  "Cycles / pixel, avg": "Средняя цена пикселя по events группы", "Cycles / vertex, avg": "Средняя цена vertex по events группы",
  "Total: pixels / vertices": "Пиксели × цена пикселя и vertices × цена vertex, млн циклов",
  "В frame": "Доля frame, которую дают events с тем же скомпилированным кодом",
  "B − A": "Разница B − A в циклах: минус и зелёный — B дешевле",
  "Pipe": "Unit ядра Mali: A — арифметика (FMA, CVT, SFU), LS — память, V — varyings, T — текстуры",
};
function autoTips(root) {
  for (const el of root.querySelectorAll("th")) {
    if (el.title || el.children.length > 2) continue;
    const t = el.textContent.trim().replace(/^[+−] /, "").replace(/:$/, "").replace(/ ★$/, "").replace(/[ ↑↓]+$/, "");
    if (AUTO_TIPS[t]) { el.title = AUTO_TIPS[t]; el.style.cursor = "help"; }
  }
}
function watchTips() {
  new MutationObserver(() => autoTips(document.body)).observe(document.body, {childList: true, subtree: true});
  autoTips(document.body);
}
