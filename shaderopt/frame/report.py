"""Frame report (plan item K1.6, decision D-09): frame_report.html from frame_cost.json (frame/cost.py).

One main table sorted by the event total — object · shader · pass · keywords · pixels · vertices · pixel price ·
vertex price · total · % of frame — with the summary by stage above it. The main core (D-17) is shown first,
the other cores are a switch; the table can be grouped by shader, variant, object or render target.
Registers, spilling and fp16 are hints under the name, not columns. One self-contained file, stays local.
"""
import html
import json

from shaderopt.profile import report as variant_report

STYLE = variant_report.TEMPLATE[variant_report.TEMPLATE.index("<style>"):variant_report.TEMPLATE.index("</style>")]


def write(cost, path, title):
    blob = json.dumps(cost, ensure_ascii=False).replace("</", "<\\/")
    page = (TEMPLATE.replace("__STYLE__", STYLE).replace("__TITLE__", html.escape(title))
            .replace("__DATA__", blob))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)


TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__STYLE__
.stages { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 12px 0; }
.stage { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; cursor: pointer; }
.stage.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.stage b { font-size: 20px; font-variant-numeric: tabular-nums; }
.stage .n { color: var(--muted); font-size: 12px; }
.stagebar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: var(--chip); margin: 4px 0 0; }
.stagebar span { display: block; height: 100%; }
td.bar { min-width: 120px; }
#main td.name { max-width: 300px; }
.controls .seg { flex-wrap: wrap; max-width: 100%; }
.pct { display: flex; align-items: center; gap: 6px; justify-content: flex-end; }
.pct i { display: inline-block; height: 8px; background: var(--heavy); border-radius: 2px; opacity: .75; }
.m { display: inline-block; font-size: 10px; border-radius: 4px; padding: 0 4px; margin-left: 4px; background: var(--chip); color: var(--muted); }
.m.renderdoc { color: var(--cheap); } .m.frustum, .m.diff { color: var(--medium); } .m.none { color: var(--heavy); }
.hint { display: block; font-size: 11px; color: var(--muted); }
.hint .flag { font-size: 10px; padding: 0 5px; }
.note { font-size: 11px; color: var(--medium); display: block; }
.st { font-size: 11px; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <p class="sub" id="meta"></p>
  <details class="help"><summary>Как читать</summary>
    <p><b>Итог</b> события = пиксели × цена пикселя + вершины × цена вершины, в циклах malioc выбранного ядра (млн).
    <b>Цена</b> — циклы самого загруженного конвейера варианта шейдера (тот же вариант, что рисовал в кадре: шейдер, пасс, keywords),
    на longest path; если longest = N/A (цикл по источникам света) — total ≈ одна итерация. Под ценой — узкий конвейер
    (fma, cvt, sfu — арифметика; ls — load/store; v — varying; t — текстуры). Цена вершины — Position + Varying для каждой вершины
    (Varying на Mali считается только для видимых, так что вершинная часть — верхняя граница).</p>
    <p><b>Пиксели</b> — сколько раз выполнился пиксельный шейдер. Источник: <span class="m renderdoc">renderdoc</span> PSInvocations (эталон);
    <span class="m fullscreen">fullscreen</span> площадь RT для полноэкранного прохода; <span class="m frustum">frustum</span> оценка
    по растру объектов (без RenderDoc; альфа-тест занижен); <span class="m diff">diff</span> изменившиеся пиксели RT (запасной вариант).
    Кадр отрисован на GPU ПК: отбраковка закрытых фрагментов на Mali (early-ZS, FPK) сильнее, овердро opaque на устройстве меньше.</p>
    <p><b>Вершины</b> — <span class="m renderdoc">renderdoc</span> VSInvocations (реальные запуски вертексного шейдера, с кэшем вершин)
    или <span class="m mesh">mesh</span> число вершин из Frame Debugger. <b>Compute</b> — потоки × цена потока ядра:
    CSInvocations RenderDoc или группы × размер группы из Frame Debugger; вариант ядра — без keywords (Frame Debugger их не показывает).</p>
    <p><b>Накоплено</b> — сумма «% кадра» строки и всех строк выше: «первые 5 шейдеров — 57% кадра» (только при сортировке по итогу).
    <b>Этапы</b> — части кадра, где рисуется шейдер или объект (opaque, тени, пост…); их цена в строке сложена.</p>
    <p>Циклы ядер одного поколения сравнимы, между поколениями ширина конвейеров разная — сравнивайте доли и порядок.
    Это статическая оценка malioc, а не замер на устройстве (K3.1).</p>
  </details>
</header>
<main>
  <div class="controls">
    <div class="group"><label>Ядро</label><div class="seg" id="core"></div></div>
    <div class="group"><label>Группировать</label><div class="seg" id="by"></div></div>
    <div class="group"><label>Поиск</label><input type="search" id="q" placeholder="объект, шейдер, keyword, RT"></div>
    <div class="group"><button class="btn" id="all"></button></div>
  </div>
  <div class="stagebar" id="stagebar"></div>
  <div class="stages" id="stages"></div>
  <section id="main"></section>
  <section id="missing"></section>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const TOP = 50;
const CUM_HINT = "Сумма «% кадра» этой строки и всех выше: «первые N строк — это X% кадра». Только при сортировке по итогу.";
const STAGE_NAMES = {post: "пост", opaque: "opaque", transparent: "transparent", shadow: "тени", prepass: "prepass",
                     ui: "UI", compute: "compute", other: "прочее"};
const STAGE_COLORS = {post: "#7a5cff", opaque: "#2f6fde", transparent: "#16a3a3", shadow: "#8a8f98", prepass: "#c98a00",
                      ui: "#d0402b", compute: "#1f9d55", other: "#999"};
const BY = [["event", "события"], ["shader", "шейдер"], ["variant", "вариант"], ["object", "объект"], ["rt", "RT"]];
const st = { core: D.main_core, by: "event", q: "", stage: "", all: false, sort: ["total", -1] };
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const int = v => v == null ? "—" : Math.round(v).toLocaleString("ru-RU");
const cyc = v => v == null ? "—" : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);
const mc = v => v == null ? "—" : (v / 1e6).toFixed(v >= 1e8 ? 0 : v >= 1e7 ? 1 : 2);
const pc = v => v == null ? "—" : (100 * v).toFixed(v >= 0.1 ? 1 : 2) + "%";
const short = c => c.replace("Mali-", "").replace("Immortalis-", "");
const plural = (n, one, few, many) =>
  `${n} ${n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many}`;
const objName = s => String(s || "").replace(/^\(RP \d+:\d+\) /, "");

const f = D.frame, cov = D.coverage;
document.title = document.getElementById("title").textContent =
  `Стоимость кадра: ${f.project ? f.project.split(/[\\/]/).pop() : ""} ${D.frame_size || ""}`.trim();
document.getElementById("meta").textContent =
  `Unity ${f.unity} · ${f.graphics_api} в редакторе, quality ${f.quality}, ${f.play_mode ? "Play Mode" : "Edit Mode"} · ` +
  `цены: malioc ${D.malioc || ""}, ${D.api} · draw с ценой ${cov.draws_priced} из ${cov.draws} · пиксели: ` +
  Object.entries(D.pixel_methods).map(([k, v]) => `${k} ${v}`).join(", ") + ` · ${D.frame_dir || ""}`;

const coreSeg = document.getElementById("core");
D.cores.forEach(c => { const b = document.createElement("button"); b.dataset.v = c.name;
  b.textContent = short(c.name) + (c.name === D.main_core ? " ★" : ""); b.title = c.arch;
  b.onclick = () => { st.core = c.name; render(); }; coreSeg.appendChild(b); });
const bySeg = document.getElementById("by");
BY.forEach(([k, l]) => { const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
  b.onclick = () => { st.by = k; st.sort = ["total", -1]; render(); }; bySeg.appendChild(b); });
document.getElementById("q").oninput = e => { st.q = e.target.value.toLowerCase(); render(); };
document.getElementById("all").onclick = () => { st.all = !st.all; render(); };

function cost(r) { return r.cost[st.core]; }
function match(r) {
  if (st.stage && r.stage !== st.stage) return false;
  if (!st.q) return true;
  return [r.object, r.shader, r.pass, (r.keywords || []).join(" "), r.rt, r.variant, r.path].join(" ").toLowerCase().includes(st.q);
}
function stages() {
  const g = (D.groups[st.core] || {}).stage || [];
  document.getElementById("stagebar").innerHTML = g.map(a =>
    `<span title="${esc(STAGE_NAMES[a.key] || a.key)} ${pc(a.share)}" style="width:${100 * a.share}%;background:${STAGE_COLORS[a.key] || "#999"}"></span>`).join("");
  const t = D.totals[st.core] || {};
  document.getElementById("stages").innerHTML =
    `<div class="stage ${st.stage ? "" : "on"}" data-s=""><div class="n">весь кадр · ${short(st.core)}</div><b>${mc(t.total)}</b> <span class="n">млн циклов</span>` +
    `<div class="n">пиксели ${pc(t.total ? t.fragment / t.total : null)} · вершины ${pc(t.total ? t.vertex / t.total : null)}` +
    (t.compute ? ` · compute ${pc(t.compute / t.total)}` : "") + `</div></div>` +
    g.map(a => `<div class="stage ${st.stage === a.key ? "on" : ""}" data-s="${esc(a.key)}">` +
      `<div class="n"><span style="color:${STAGE_COLORS[a.key] || "#999"}">■</span> ${esc(STAGE_NAMES[a.key] || a.key)} · ${a.events} соб.</div>` +
      `<b>${pc(a.share)}</b><div class="n">${mc(a.total)} млн · ${int(a.pixels)} пикс · ${int(a.vertices)} верш</div></div>`).join("");
  document.querySelectorAll(".stage").forEach(el => el.onclick = () => { st.stage = el.dataset.s; render(); });
}
function groupRows() {
  const key = r => st.by === "object" ? objName(r.object) : st.by === "rt" ? r.rt : r[st.by];
  const m = new Map();
  for (const r of D.events) {
    const c = cost(r); if (!c || !match(r)) continue;
    const k = key(r) || "—";
    if (!m.has(k)) m.set(k, {key: k, events: 0, pixels: 0, vertices: 0, fragment: 0, vertex: 0, total: 0, share: 0,
                             stages: new Set(), shaders: new Set(), pxP: [], vtxP: [], threads: 0, compute: 0});
    const a = m.get(k); a.events++; a.pixels += r.pixels; a.vertices += r.vertices;
    a.threads += r.threads || 0; a.compute += c.compute || 0;
    a.fragment += c.fragment; a.vertex += c.vertex; a.total += c.total; a.share += c.share;
    a.stages.add(r.stage); a.shaders.add(r.shader);
    // prices of the events that did any work (a draw with 0 pixels, e.g. depth-only shadows, says nothing)
    if (r.kind === "compute") { if (r.threads) a.pxP.push(c.cs_price); }
    else { if (r.pixels) a.pxP.push(c.px_price); if (r.vertices) a.vtxP.push(c.vtx_price); }
  }
  // the group's price: cycles per pixel / per vertex weighted by the work (compute: per thread)
  for (const a of m.values()) {
    a.px_price = a.pixels + a.threads ? (a.fragment + a.compute) / (a.pixels + a.threads) : null;
    a.vtx_price = a.vertices ? a.vertex / a.vertices : null;
  }
  return [...m.values()];
}
function th(k, label, cls = "", small = "") {
  const [sk, sd] = st.sort;
  return `<th class="${cls} ${sk === k ? "sorted" : ""}" data-k="${k}">${label}${sk === k ? (sd < 0 ? " ↓" : " ↑") : ""}${small ? `<small>${small}</small>` : ""}</th>`;
}
function sortRows(rows, get) {
  const [k, d] = st.sort;
  rows.sort((a, b) => { const x = get(a, k), y = get(b, k); return (x < y ? -1 : x > y ? 1 : 0) * d; });
}
function pctCell(share, max) {
  return `<td class="num bar"><div class="pct">${pc(share)}<i style="width:${Math.max(1, 80 * share / (max || 1))}px"></i></div></td>`;
}
function eventsTable() {
  let rows = D.events.filter(r => cost(r) && match(r));
  const n = rows.length;
  const get = (r, k) => { const c = cost(r);
    return k === "name" ? objName(r.object).toLowerCase() : k === "shader" ? (r.shader + r.pass).toLowerCase()
      : k === "pixels" ? r.pixels : k === "vertices" ? r.vertices : k === "index" ? r.index : c[k] ?? -1; };
  sortRows(rows, get);
  const max = Math.max(...rows.map(r => cost(r).share), 0);
  let cum = 0;
  const shown = st.all ? rows : rows.slice(0, TOP);
  let h = `<h2>События кадра <small>${shown.length} из ${n} · ${short(st.core)} · сортировка по ${st.sort[0]}</small></h2>`;
  h += `<div class="wrap"><table><thead><tr>${th("index", "#")}${th("name", "Объект", "l name")}${th("shader", "Шейдер · пасс · keywords", "l")}`
     + th("pixels", "Пиксели", "gl") + th("vertices", "Вершины") + th("px_price", "Цена пикселя", "gl", "циклы")
     + th("vtx_price", "Цена вершины", "", "циклы") + th("total", "Итог", "gl", "млн циклов") + th("share", "% кадра")
     + `<th class="num" title="${CUM_HINT}">Накоплено<small>% кадра</small></th></tr></thead><tbody>`;
  for (const r of shown) {
    const c = cost(r); cum += c.share;
    const hints = [...c.flags.map(x => `<span class="flag ${x}">${x}</span>`),
      `регистры ${c.px_regs}${c.vtx_regs != null ? " / " + c.vtx_regs : ""}`, c.px_fp16 != null ? `fp16 ${c.px_fp16}%` : ""].filter(Boolean).join(" ");
    const range = r.pixels_low != null || r.pixels_high != null ? `диапазон ${int(r.pixels_low)} … ${int(r.pixels_high)}` : "";
    h += `<tr><td class="num">${r.index + 1}<span class="bd">${esc(STAGE_NAMES[r.stage] || r.stage)}</span></td>`
       + `<td class="l name"><span class="sh">${esc(objName(r.object))}</span>`
       + (r.kind === "compute" ? "" : `<span class="hint">${r.meshes.length > 1 ? `батч: ${plural(r.meshes.length, "меш", "меша", "мешей")} · ` : ""}<span title="${esc(r.rt)}">RT ${esc(String(r.rt || "").replace(/_\d+x\d+_.*$/, ""))} ${r.rt_size[0]}×${r.rt_size[1]}</span></span>`)
       + `<span class="hint">${hints}</span></td>`
       + (r.kind === "compute"
         ? `<td class="l" style="white-space:normal;max-width:330px"><span class="sh">${esc(r.shader)}</span> <span class="pass">· kernel ${esc(r.kernel)}</span></td>`
           + `<td class="num gl">${int(r.threads)}<span class="m ${esc(r.thread_method)}">потоки · ${esc(r.thread_method)}</span></td><td class="num">—</td>`
           + `<td class="num gl" title="${esc(c.cs_path)}">${cyc(c.cs_price)}<span class="bd">поток · ${esc(c.cs_bound.join("+"))}${c.cs_path === "total" ? " · total" : ""}</span></td><td class="num">—</td>`
           + `<td class="num big gl">${mc(c.total)}<span class="bd">compute</span></td>`
         : `<td class="l" style="white-space:normal;max-width:330px"><span class="sh">${esc(r.shader)}</span> <span class="pass">· ${esc(r.pass)}</span><br>`
           + (r.keywords.length ? r.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">без keywords</span>') + `</td>`
           + `<td class="num gl" title="${esc(range)}">${int(r.pixels)}<span class="m ${esc(r.pixel_method)}">${esc(r.pixel_method)}</span>`
           + (r.pixel_note ? `<span class="note">${esc(r.pixel_note)}</span>` : "") + `</td>`
           + `<td class="num">${int(r.vertices)}<span class="m ${esc(r.vertex_method)}">${esc(r.vertex_method)}</span></td>`
           + `<td class="num gl" title="${esc(c.px_path)}">${cyc(c.px_price)}<span class="bd">${esc(c.px_bound.join("+"))}${c.px_path === "total" ? " · total" : ""}</span></td>`
           + `<td class="num">${cyc(c.vtx_price)}<span class="bd">${esc(c.vtx_bound.join("+"))}</span></td>`
           + `<td class="num big gl">${mc(c.total)}<span class="bd">пикс ${mc(c.fragment)} · верш ${mc(c.vertex)}</span></td>`)
       + pctCell(c.share, max) + `<td class="num st">${st.sort[0] === "total" && st.sort[1] < 0 ? pc(cum) : ""}</td></tr>`;
  }
  return h + "</tbody></table></div>";
}
function groupTable() {
  const rows = groupRows();
  sortRows(rows, (a, k) => k === "name" ? String(a.key).toLowerCase() : a[k] ?? -1);
  const max = Math.max(...rows.map(a => a.share), 0);
  const shown = st.all ? rows : rows.slice(0, TOP);
  let cum = 0;
  let h = `<h2>По ${{shader: "шейдерам", variant: "вариантам", object: "объектам", rt: "render target"}[st.by]} <small>${shown.length} из ${rows.length} · ${short(st.core)}</small></h2>`;
  h += `<div class="wrap"><table><thead><tr>${th("name", BY.find(b => b[0] === st.by)[1], "l name")}${th("events", "Событий")}`
     + th("pixels", "Пиксели", "gl") + th("vertices", "Вершины")
     + th("px_price", "Цена пикселя", "gl", "циклы, среднее") + th("vtx_price", "Цена вершины", "", "циклы, среднее")
     + th("fragment", "Пиксели", "gl", "млн циклов")
     + th("vertex", "Вершины", "", "млн циклов") + th("total", "Итог", "gl", "млн циклов") + th("share", "% кадра")
     + `<th class="num" title="${CUM_HINT}">Накоплено<small>% кадра</small></th>`
     + `<th class="l" title="Части кадра, где рисуется: один шейдер может рисовать и в основной проход, и в тени — в строке они сложены">Этапы</th></tr></thead><tbody>`;
  const range = p => { if (p.length < 2) return ""; const lo = Math.min(...p), hi = Math.max(...p);
    return hi - lo > 1e-6 ? `<span class="bd">${cyc(lo)} … ${cyc(hi)}</span>` : ""; };
  for (const a of shown) {
    cum += a.share;
    h += `<tr><td class="l name"><span class="sh">${esc(st.by === "object" ? objName(a.key) : a.key)}</span>`
       + (st.by !== "shader" && st.by !== "variant" ? `<span class="hint">${esc([...a.shaders].join(", "))}</span>` : "") + `</td>`
       + `<td class="num">${a.events}</td><td class="num gl">${int(a.pixels)}</td><td class="num">${int(a.vertices)}</td>`
       + `<td class="num gl" title="циклы пикселей ÷ пиксели (у compute — на поток); под числом — разброс по событиям">${cyc(a.px_price)}${range(a.pxP)}</td>`
       + `<td class="num" title="циклы вершин ÷ вершины; под числом — разброс по событиям">${cyc(a.vtx_price)}${range(a.vtxP)}</td>`
       + `<td class="num gl">${mc(a.fragment)}</td><td class="num">${mc(a.vertex)}</td><td class="num big gl">${mc(a.total)}</td>`
       + pctCell(a.share, max) + `<td class="num st">${st.sort[0] === "total" && st.sort[1] < 0 ? pc(cum) : ""}</td>`
       + `<td class="l st">${[...a.stages].map(s => esc(STAGE_NAMES[s] || s)).join(", ")}</td></tr>`;
  }
  return h + "</tbody></table></div>";
}
function render() {
  coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
  bySeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.by));
  document.getElementById("all").textContent = st.all ? `Только топ-${TOP}` : "Показать все";
  stages();
  document.getElementById("main").innerHTML = st.by === "event" ? eventsTable() : groupTable();
  document.querySelectorAll("thead th[data-k]").forEach(el => el.onclick = () => {
    const k = el.dataset.k; st.sort = [k, st.sort[0] === k ? -st.sort[1] : (k === "name" || k === "shader" || k === "index" ? 1 : -1)]; render(); });
  document.getElementById("missing").innerHTML = D.missing.length
    ? `<h2>Без цены <small>${D.missing.length} — не входят в итог</small></h2><div class="wrap"><table><tbody>`
      + D.missing.map(m => `<tr><td class="num">${m.index + 1}</td><td class="l">${esc(STAGE_NAMES[m.stage] || m.stage)}</td>`
        + `<td class="l">${esc(objName(m.object))}</td><td class="l">${esc(m.shader)} ${m.pass ? "· " + esc(m.pass) : ""}</td>`
        + `<td class="l fail">${esc(m.reason)}</td></tr>`).join("") + "</tbody></table></div>" : "";
}
render();
</script>
</body>
</html>
"""
