"""Frame report (plan item K1.6, decision D-09): frame_report.html from frame_cost.json (frame/cost.py).

One main table sorted by the event total — object · shader · pass · keywords · pixels · vertices · pixel price ·
vertex price · total · % of frame — with the summary by stage above it. The main core (D-17) is shown first,
the other cores are a switch; the table can be grouped by shader, variant, object or render target.
Registers, spilling and fp16 are hints under the name, not columns. One self-contained file, stays local.
"""
import html
import json

STYLE = r"""<style>
:root {
  color-scheme: dark;
  --bg: #121417; --panel: #1a1e23; --text: #eceef0; --muted: #8b939c; --line: #2a3038;
  --accent: #34d399; --on-accent: #062a1d; --chip: #232830; --hover: #20252b;
  --cheap: #8bd99b; --medium: #e8c27a; --heavy: #f2877b; --info: #7fb2e5; --bar: #55606c;
  --sans: Manrope, system-ui, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 var(--sans); font-variant-numeric: tabular-nums; }
header, main { max-width: 1800px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 18px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 22px 0 6px; }
h2 small { color: var(--muted); font-weight: 400; font-size: 13px; }
.sub { color: var(--muted); margin: 0 0 4px; }
details.help { color: var(--muted); font-size: 13px; margin: 6px 0; }
details.help summary { cursor: pointer; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin: 12px 0 8px; }
.group { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.group > label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { border: 0; background: transparent; color: var(--text); padding: 5px 11px; cursor: pointer; font: inherit; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button.on { background: var(--accent); color: var(--on-accent); }
input[type=search], select { background: var(--panel); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 5px 9px; font: inherit; }
input[type=search] { width: 220px; max-width: 100%; }
.chipbtn { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 999px;
  padding: 3px 10px; cursor: pointer; font: inherit; font-size: 12px; }
.chipbtn.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.btn { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 8px;
  padding: 5px 10px; cursor: pointer; font: inherit; }
.wrap { overflow: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; max-height: 75vh; }
table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
thead th { position: sticky; top: 0; background: var(--panel); z-index: 2; font-weight: 600; font-size: 12px;
  vertical-align: bottom; cursor: pointer; user-select: none; }
thead th small { display: block; font-weight: 400; color: var(--muted); font-size: 10px; }
thead th.sorted { color: var(--accent); }
th.l, td.l { text-align: left; }
td.name { position: sticky; left: 0; background: var(--panel); z-index: 1; max-width: 420px; white-space: normal; }
thead th.name { left: 0; z-index: 3; }
tbody tr:hover td { background: var(--hover); }
.sh { font-weight: 600; }
.pass { color: var(--muted); }
.kw { display: inline-block; font-family: var(--mono); font-size: 11px; background: var(--chip);
  border-radius: 4px; padding: 0 4px; margin: 1px 2px 1px 0; color: var(--muted); }
.num { font-variant-numeric: tabular-nums; }
td.big { font-weight: 700; }
.bd { display: block; font-size: 10px; color: var(--muted); }
.flag { display: inline-block; font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 999px; margin: 1px 2px; }
.flag.regs_gt32, .flag.spilling { color: var(--heavy); background: color-mix(in srgb, var(--heavy) 14%, transparent); }
.flag.sfu_bound, .flag.low_fp16 { color: var(--medium); background: color-mix(in srgb, var(--medium) 14%, transparent); }
.flag.dynamic_loop { color: var(--info); background: color-mix(in srgb, var(--info) 14%, transparent); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.file { font-family: var(--mono); font-size: 11px; }
.empty { color: var(--muted); padding: 14px; }
.fail { color: var(--heavy); white-space: pre-wrap; font-family: var(--mono); font-size: 12px; }
.gl { border-left: 2px solid var(--line); }
"""


def write(cost, path, title):
    # < > & as JSON escapes: no name can close the <script> or open a comment in it
    blob = json.dumps(cost, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
__STYLE__
.stages { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 12px 0; }
.stage { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; cursor: pointer; }
.stage.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.stage b { font-size: 20px; font-variant-numeric: tabular-nums; }
.stage .n { color: var(--muted); font-size: 12px; }
.stagebar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: var(--chip); margin: 4px 0 0; }
.stagebar span { display: block; height: 100%; }
td.bar { min-width: 120px; }
#loops .wrap { width: fit-content; max-width: 100%; }
#loops table { min-width: 0; }
#loops th, #loops td { padding: 5px 14px; }
.wrap.ev { max-width: 1400px; }
.ev table { table-layout: fixed; width: 100%; }
.ev th, .ev td { overflow: hidden; text-overflow: ellipsis; padding: 8px 10px; }
.ev tbody td { font-size: 13px; }
.ev tr.r { cursor: pointer; }
.ev tr.open > td { background: color-mix(in srgb, var(--accent) 7%, var(--panel)); }
.ev td.num { font-family: var(--mono); font-size: 12.5px; }
.ev td.big { color: var(--text); font-weight: 600; }
.ev .pct { justify-content: flex-start; gap: 8px; }
.ev .pct span { flex: none; width: 46px; text-align: right; font-family: var(--mono); font-size: 12.5px; }
.ev .pct i { flex: none; max-width: calc(100% - 54px); }
.dots { display: inline-flex; gap: 3px; margin-right: 5px; } .dots .dot { margin-right: 0; }
.kw .dot { width: 6px; height: 6px; margin-right: 4px; }
.warnc { color: var(--medium); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; vertical-align: 1px; }
.shn { color: var(--text); } .ev .pass { color: var(--muted); }
.fc { display: inline-block; font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 6px; padding: 0 6px; margin-left: 6px; cursor: help; }
.fc.bad { color: var(--heavy); border-color: color-mix(in srgb, var(--heavy) 40%, transparent); }
.lp { color: var(--info); cursor: help; } .pw { color: var(--medium); font-weight: 700; cursor: help; }
.ev tr.det > td { background: var(--bg); white-space: normal; padding: 10px 14px 14px 34px; text-align: left; }
.dg { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px 18px; font-size: 12.5px; }
.dg span { display: block; color: var(--muted); font-size: 11px; font-family: var(--sans); }
.dl { margin-top: 8px; font-size: 12px; color: var(--muted); }
.controls .seg { flex-wrap: wrap; max-width: 100%; }
.pct { display: flex; align-items: center; gap: 6px; justify-content: flex-end; }
.pct i { display: inline-block; height: 8px; background: var(--bar); border-radius: 2px; }
.m { display: inline-block; font-size: 10px; border-radius: 4px; padding: 0 4px; margin-left: 4px; background: var(--chip); color: var(--muted); }
.m.renderdoc { color: var(--cheap); } .m.diff { color: var(--medium); } .m.none { color: var(--heavy); }
.hint { display: block; font-size: 11px; color: var(--muted); }
.hint .flag { font-size: 10px; padding: 0 5px; }
.note { font-size: 11px; color: var(--medium); display: block; }
.st { font-size: 11px; color: var(--muted); }
.tabs { display: flex; gap: 4px; margin: 0 0 10px; border-bottom: 1px solid var(--line); }
.tabs button { background: none; border: 0; border-bottom: 2px solid transparent; padding: 6px 12px; cursor: pointer;
               color: var(--muted); font: inherit; font-weight: 600; }
.tabs button.on { color: var(--fg, inherit); border-bottom-color: var(--accent); }
.hide { display: none !important; }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <p class="sub" id="meta"></p>
  <details class="help"><summary>Как читать</summary>
    <p><b>Итог</b> = пиксели × цена пикселя + вершины × цена вершины, млн циклов на выбранном ядре.
    <b>Цена</b> — циклы шейдера на один пиксель или вершину по malioc.</p>
    <p><b>Пиксели</b> и <b>вершины</b> — сколько раз реально выполнились пиксельный и вершинный шейдер (счётчики RenderDoc).</p>
    <p><b>↻ цикл n</b> — у шейдера цикл неизвестной длины (свет, шаги луча); цена посчитана для n итераций,
    таблица «Кадр при n итераций» показывает, как от n зависит итог.</p>
    <p>Наведите на метку или число — появится подсказка. Это оценка malioc, а не замер на устройстве:
    сравнивайте доли и порядок, а не циклы разных поколений GPU.</p>
  </details>
</header>
<main>
  <div class="tabs" id="tabs"></div>
  <div class="controls">
    <div class="group"><label>Ядро</label><div class="seg" id="core"></div></div>
    <div class="group"><label>Группировать</label><div class="seg" id="by"></div></div>
    <div class="group"><label>Поиск</label><input type="search" id="q" placeholder="объект, шейдер, keyword, RT"></div>
    <div class="group project-only"><label>Показать</label><div class="seg" id="pfilter"></div></div>
    <div class="group"><button class="btn" id="all"></button></div>
  </div>
  <div class="stagebar" id="stagebar"></div>
  <div class="stages" id="stages"></div>
  <section id="loops"></section>
  <section id="main"></section>
  <section id="missing"></section>
  <section id="project"></section>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const TOP = 50;
const CUM_HINT = "Сумма «% кадра» этой строки и всех выше: «первые N строк — это X% кадра». Только при сортировке по итогу.";
const STAGE_NAMES = {post: "пост", opaque: "opaque", transparent: "transparent", shadow: "тени", prepass: "prepass",
                     ui: "UI", compute: "compute", other: "прочее"};
const STAGE_COLORS = {post: "#a594f0", opaque: "#7fb2e5", transparent: "#6cc4c4", shadow: "#8b939c", prepass: "#d9b36e",
                      ui: "#e3a1c4", compute: "#b9a3e0", other: "#5f6873"};
const BY = [["event", "события"], ["shader", "шейдер"], ["variant", "вариант"], ["object", "объект"], ["rt", "RT"]];
const st = { core: D.main_core, by: "event", q: "", stage: "", all: false, sort: ["total", -1],
             tab: "frame", pf: "all", psort: ["px", -1], open: new Set() };
const P = D.project_shaders;
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
  `цены: malioc ${D.malioc || ""}, ${D.api}` + (D.variants_checked === false ? " (варианты не сверены с шейдерами)" : "") + ` · draw с ценой ${cov.draws_priced} из ${cov.draws} · пиксели: ` +
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
const pathNote = p => p === "total" ? " · total" : String(p || "").startsWith("loop") ? " · цикл " + esc(p.slice(5)) : "";
// a dynamic loop (lights per pixel, ray steps): its price at 0, 1, 2, 4, 8 repeats; the used one is in px_path "loop n=2"
const loopN = c => (String(c.px_path || "").match(/n=(\d+)/) || [])[1];
const byN = c => c.px_price_by_n ? "цена пикселя при числе повторов цикла: " + Object.entries(c.px_price_by_n)
  .map(([n, v]) => `${n} → ${cyc(v)}${n === loopN(c) ? " (в итоге)" : ""}`).join(" · ") : "";
const loopTip = c => `В шейдере цикл, число повторов которого заранее неизвестно (например, по источникам света на пиксель). `
  + `Цена посчитана для ${loopN(c) ?? "n"} повторов. ` + (byN(c) ? byN(c)[0].toUpperCase() + byN(c).slice(1) : "");
function loopsTable() {
  const L = D.loops, el = document.getElementById("loops");
  if (!L || !L.events) { el.innerHTML = ""; return; }
  const by = L.by_n[st.core] || {}, ns = Object.keys(by);
  const names = [...new Set(ns.flatMap(n => Object.keys(by[n].stages)))];
  const cell = (n, k) => `<td class="num ${String(n) === String(L.n) ? "gl" : ""}">${k}</td>`;
  el.innerHTML = `<h2 title="Весь кадр, если в каждом шейдере с ↻ цикл повторится n раз (например, n источников света на пиксель). В итоге кадра сейчас n = ${L.n}">`
    + `↻ Кадр при разном числе повторов циклов <small>${L.events} событий с циклами · в итоге n = ${L.n}`
    + (Object.keys(L.overrides).length ? " · " + Object.entries(L.overrides).map(([k, v]) => esc(k) + " = " + v).join(", ") : "")
    + (L.unforced.length ? ` · ${L.unforced.length} файлов не удалось — цена по total` : "") + `</small></h2>`
    + `<div class="wrap"><table><thead><tr><th class="l">повторов цикла, n</th>${ns.map(n => `<th class="num">${n}</th>`).join("")}</tr></thead><tbody>`
    + `<tr><td class="l">кадр, млн циклов</td>${ns.map(n => cell(n, mc(by[n].total))).join("")}</tr>`
    + `<tr><td class="l" title="Доля кадра, которую дают события с ↻">доля событий с циклами</td>${ns.map(n => cell(n, pc(by[n].loop_share))).join("")}</tr>`
    + names.map(k => `<tr><td class="l">${esc(STAGE_NAMES[k] || k)}</td>${ns.map(n => cell(n, pc(by[n].stages[k] || 0))).join("")}</tr>`).join("")
    + `</tbody></table></div>`;
}
function stages() {
  const g = (D.groups[st.core] || {}).stage || [];
  document.getElementById("stagebar").innerHTML = g.map(a =>
    `<span title="${esc(STAGE_NAMES[a.key] || a.key)} ${pc(a.share)}" style="width:${100 * a.share}%;background:${STAGE_COLORS[a.key] || STAGE_COLORS.other}"></span>`).join("");
  const t = D.totals[st.core] || {};
  document.getElementById("stages").innerHTML =
    `<div class="stage ${st.stage ? "" : "on"}" data-s=""><div class="n">весь кадр · ${short(st.core)}</div><b>${mc(t.total)}</b> <span class="n">млн циклов</span>` +
    `<div class="n">пиксели ${pc(t.total ? t.fragment / t.total : null)} · вершины ${pc(t.total ? t.vertex / t.total : null)}` +
    (t.compute ? ` · compute ${pc(t.compute / t.total)}` : "") + `</div></div>` +
    g.map(a => `<div class="stage ${st.stage === a.key ? "on" : ""}" data-s="${esc(a.key)}">` +
      `<div class="n"><span style="color:${STAGE_COLORS[a.key] || STAGE_COLORS.other}">■</span> ${esc(STAGE_NAMES[a.key] || a.key)} · ${a.events} соб.</div>` +
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
const BAD_FLAGS = new Set(["spilling", "regs_gt32"]);
const TIPS = {
  dynamic_loop: "Цикл с заранее неизвестным числом повторов (свет на пиксель, шаги луча): цена зависит от числа повторов, см. ↻",
  low_fp16: "Мало вычислений в half (fp16). На Mali half вдвое дешевле float — кандидат на перевод в half",
  regs_gt32: "Больше 32 регистров: GPU держит меньше потоков сразу и хуже прячет задержки текстур",
  spilling: "Регистров не хватило, данные уходят в память — заметно дороже. Упростите шейдер",
  sfu_bound: "Упирается в спецфункции (sin, exp, pow, rcp, sqrt…) — замените их приближениями",
  renderdoc: "Точно: сколько раз реально выполнился шейдер (счётчик RenderDoc)",
  fullscreen: "Площадь экрана: полноэкранный проход посчитан без RenderDoc",
  diff: "Оценка: сколько пикселей поменялось в Frame Debugger, обычно занижено",
  mesh: "Число вершин меша из Frame Debugger",
  none: "Не посчитано",
};
const tip = k => esc(TIPS[k] || "");
const flagTag = x => `<span class="flag ${x}" title="${tip(x)}">${x}</span>`;
const methodTag = m => m ? `<span class="m ${esc(m)}" title="${tip(m)}">${esc(m)}</span>` : "";
function flagCount(flags) {
  if (!flags.length) return "";
  const bad = flags.some(x => BAD_FLAGS.has(x));
  return ` <span class="fc ${bad ? "bad" : ""}" title="${esc(flags.map(x => x + ": " + (TIPS[x] || "")).join(" · "))}">${bad ? "⚠ " : ""}${flags.length}</span>`;
}
function detailRow(r, c, cum) {
  const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
  const range = r.pixels_low != null || r.pixels_high != null ? ` (${int(r.pixels_low)} … ${int(r.pixels_high)})` : "";
  const items = r.kind === "compute"
    ? [item("Потоки", `${int(r.threads)} ${methodTag(r.thread_method)}`),
       item("Цикл / поток", `${cyc(c.cs_price)} · ${esc(c.cs_bound.join("+"))}${pathNote(c.cs_path)}`)]
    : [item("Пиксели", `${int(r.pixels)} ${methodTag(r.pixel_method)}${range}`),
       item("Цикл / пиксель", `${cyc(c.px_price)} · ${esc(c.px_bound.join("+"))}${pathNote(c.px_path)}`),
       item("Вершины", `${int(r.vertices)} ${methodTag(r.vertex_method)}`),
       item("Цикл / вершина", `${cyc(c.vtx_price)} · ${esc(c.vtx_bound.join("+"))}`),
       item("Итог: пиксели / вершины", `${mc(c.fragment)} / ${mc(c.vertex)} млн`)];
  items.push(item("Регистры", `${c.px_regs ?? "—"}${c.vtx_regs != null ? " / " + c.vtx_regs : ""}${c.px_fp16 != null ? ` · fp16 ${c.px_fp16}%` : ""}`));
  if (cum != null) items.push(item(`<span title="${CUM_HINT}">Накоплено</span>`, pc(cum)));
  const tags = [`событие ${r.index + 1}`, STAGE_NAMES[r.stage] || r.stage];
  if (r.kind === "compute") tags.push(`kernel ${r.kernel}`);
  else {
    tags.push(`RT ${String(r.rt || "").replace(/_\d+x\d+_.*$/, "")} ${r.rt_size[0]}×${r.rt_size[1]}`);
    if (r.meshes.length > 1) tags.push(`батч: ${plural(r.meshes.length, "меш", "меша", "мешей")}`);
  }
  const kws = r.kind === "compute" ? "" : r.keywords.length ? r.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">без keywords</span>';
  return `<tr class="det"><td colspan="6"><div class="dg num">${items.join("")}</div>`
    + (byN(c) ? `<div class="dl" title="${esc(loopTip(c))}">↻ ${esc(byN(c))}</div>` : "")
    + (r.pixel_note ? `<div class="dl note">${esc(r.pixel_note)}</div>` : "")
    + (c.flags.length ? `<div class="dl">${c.flags.map(flagTag).join(" ")}</div>` : "")
    + `<div class="dl">${tags.map(t => `<span class="kw">${esc(t)}</span>`).join("")}${kws}</div></td></tr>`;
}
function eventsTable() {
  let rows = D.events.filter(r => cost(r) && match(r));
  const n = rows.length;
  const get = (r, k) => { const c = cost(r);
    return k === "name" ? objName(r.object).toLowerCase() : k === "shader" ? (r.shader + r.pass).toLowerCase()
      : k === "pixels" ? (r.kind === "compute" ? r.threads : r.pixels) : k === "price" ? (r.kind === "compute" ? c.cs_price : c.px_price) ?? -1
      : c[k] ?? -1; };
  sortRows(rows, get);
  const max = Math.max(...rows.map(r => cost(r).share), 0);
  const byTotal = (st.sort[0] === "total" || st.sort[0] === "share") && st.sort[1] < 0;
  let cum = 0;
  const shown = st.all ? rows : rows.slice(0, TOP);
  let h = `<h2>События кадра <small>${shown.length} из ${n} · ${short(st.core)} · клик по строке — подробности</small></h2>`;
  h += `<div class="wrap ev"><table><colgroup><col style="width:15%"><col style="width:24%"><col style="width:29%">`
     + `<col style="width:11%"><col style="width:9%"><col style="width:12%"></colgroup><thead><tr>`
     + th("share", "% кадра", "l") + th("name", "Объект", "l") + th("shader", "Шейдер · пасс", "l")
     + th("pixels", "Пиксели") + th("price", "Цикл / пикс") + th("total", "Итог, млн") + `</tr></thead><tbody>`;
  for (const r of shown) {
    const c = cost(r); cum += c.share;
    const open = st.open.has(r.index), compute = r.kind === "compute", names = objName(r.object).split(", ");
    const loop = String(c.px_path || "").startsWith("loop") ? ` <span class="lp" title="${esc(loopTip(c))}">↻</span>` : "";
    const warn = r.pixel_method === "none" || r.pixel_note ? ` <span class="pw" title="${esc(r.pixel_note || "пиксели не посчитаны")}">!</span>` : "";
    h += `<tr class="r ${open ? "open" : ""}" data-i="${r.index}">`
       + `<td class="l"><div class="pct"><span>${pc(c.share)}</span><i style="width:${Math.max(1, 100 * c.share / (max || 1))}%"></i></div></td>`
       + `<td class="l"><span class="dot" style="background:${STAGE_COLORS[r.stage] || STAGE_COLORS.other}" title="${esc(STAGE_NAMES[r.stage] || r.stage)}"></span>`
       + `<span class="sh" title="${esc(objName(r.object))}">${esc(names[0])}</span>${names.length > 1 ? ` <span class="st">+${names.length - 1}</span>` : ""}${flagCount(c.flags)}</td>`
       + `<td class="l" title="${esc(r.shader)} · ${esc(compute ? "kernel " + r.kernel : r.pass)}"><span class="shn">${esc(r.shader)}</span> <span class="pass">· ${esc(compute ? r.kernel : r.pass)}</span></td>`
       + `<td class="num">${int(compute ? r.threads : r.pixels)}${warn}</td>`
       + `<td class="num">${cyc(compute ? c.cs_price : c.px_price)}${loop}</td>`
       + `<td class="num big">${mc(c.total)}</td></tr>`;
    if (open) h += detailRow(r, c, byTotal ? cum : null);
  }
  return h + "</tbody></table></div>";
}
function groupLabel(a) {
  if (st.by === "variant") {
    const [sh, pass, kw] = String(a.key).split(" | ");
    return `<span class="shn">${esc(sh)}</span> <span class="pass">· ${esc(pass || "")}${kw ? " · " + esc(kw) : ""}</span>`;
  }
  if (st.by === "rt") return `<span class="shn">${esc(String(a.key).replace(/_\d+x\d+_.*$/, ""))}</span>`;
  return `<span class="shn">${esc(st.by === "object" ? objName(a.key) : a.key)}</span>`;
}
function groupDetail(a, cum) {
  const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
  const range = p => { if (p.length < 2) return ""; const lo = Math.min(...p), hi = Math.max(...p);
    return hi - lo > 1e-6 ? ` (${cyc(lo)} … ${cyc(hi)})` : ""; };
  const items = [item("Пиксели", int(a.pixels)), item("Цикл / пиксель, среднее", cyc(a.px_price) + range(a.pxP)),
    item("Вершины", int(a.vertices)), item("Цикл / вершина, среднее", cyc(a.vtx_price) + range(a.vtxP)),
    item("Итог: пиксели / вершины", `${mc(a.fragment)} / ${mc(a.vertex)} млн`)];
  if (a.threads) items.push(item("Compute", `${int(a.threads)} потоков · ${mc(a.compute)} млн`));
  if (cum != null) items.push(item(`<span title="${CUM_HINT}">Накоплено</span>`, pc(cum)));
  const stagesTag = [...a.stages].map(s => `<span class="kw"><span class="dot" style="background:${STAGE_COLORS[s] || STAGE_COLORS.other}"></span>${esc(STAGE_NAMES[s] || s)}</span>`).join("");
  const shaders = st.by !== "shader" && st.by !== "variant" ? [...a.shaders].map(s => `<span class="kw">${esc(s)}</span>`).join("") : "";
  return `<tr class="det"><td colspan="6"><div class="dg num">${items.join("")}</div>`
    + `<div class="dl">${stagesTag}${shaders}</div>`
    + (st.by === "rt" || st.by === "variant" ? `<div class="dl">${esc(a.key)}</div>` : "") + `</td></tr>`;
}
function groupTable() {
  const rows = groupRows();
  sortRows(rows, (a, k) => k === "name" ? String(a.key).toLowerCase() : a[k] ?? -1);
  const max = Math.max(...rows.map(a => a.share), 0);
  const shown = st.all ? rows : rows.slice(0, TOP);
  const byTotal = (st.sort[0] === "total" || st.sort[0] === "share") && st.sort[1] < 0;
  const label = BY.find(b => b[0] === st.by)[1];
  let cum = 0;
  let h = `<h2>По ${{shader: "шейдерам", variant: "вариантам", object: "объектам", rt: "render target"}[st.by]} <small>${shown.length} из ${rows.length} · ${short(st.core)} · клик по строке — подробности</small></h2>`;
  h += `<div class="wrap ev"><table><colgroup><col style="width:15%"><col style="width:44%"><col style="width:8%">`
     + `<col style="width:12%"><col style="width:9%"><col style="width:12%"></colgroup><thead><tr>`
     + th("share", "% кадра", "l") + th("name", label[0].toUpperCase() + label.slice(1), "l") + th("events", "Событий")
     + th("pixels", "Пиксели") + th("px_price", "Цикл / пикс") + th("total", "Итог, млн") + `</tr></thead><tbody>`;
  for (const a of shown) {
    cum += a.share;
    const open = st.open.has("g:" + st.by + ":" + a.key);
    const dots = [...a.stages].map(s => `<span class="dot" style="background:${STAGE_COLORS[s] || STAGE_COLORS.other}" title="${esc(STAGE_NAMES[s] || s)}"></span>`).join("");
    const sub = st.by === "object" || st.by === "rt" ? ` <span class="st">${esc([...a.shaders].join(", "))}</span>` : "";
    h += `<tr class="r ${open ? "open" : ""}" data-g="${esc("g:" + st.by + ":" + a.key)}">`
       + `<td class="l"><div class="pct"><span>${pc(a.share)}</span><i style="width:${Math.max(1, 100 * a.share / (max || 1))}%"></i></div></td>`
       + `<td class="l" title="${esc(a.key)}"><span class="dots">${dots}</span>${groupLabel(a)}${sub}</td>`
       + `<td class="num">${a.events}</td><td class="num">${int(a.pixels + a.threads)}</td>`
       + `<td class="num">${cyc(a.px_price)}</td><td class="num big">${mc(a.total)}</td></tr>`;
    if (open) h += groupDetail(a, byTotal ? cum : null);
  }
  return h + "</tbody></table></div>";
}
const tabs = document.getElementById("tabs");
[["frame", "Кадр"], ["project", P ? `Шейдеры проекта · ${P.rows.length}` : ""]].forEach(([k, l]) => { if (!l) return;
  const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
  b.onclick = () => { st.tab = k; render(); }; tabs.appendChild(b); });
if (!P) tabs.classList.add("hide");
const pfSeg = document.getElementById("pfilter");
[["all", "все"], ["frame", "в кадре"], ["notframe", "не в кадре"]].forEach(([k, l]) => {
  const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
  b.onclick = () => { st.pf = k; render(); }; pfSeg.appendChild(b); });

function projectTable() {
  const main = st.core;
  let rows = P.rows.map((r, i) => Object.assign({_id: i}, r)).filter(r => {
    if (st.pf === "frame" && !r.frame_events) return false;
    if (st.pf === "notframe" && r.frame_events) return false;
    if (!st.q) return true;
    return [r.shader, r.pass, r.keyword_sets.flat().join(" "), r.materials.join(" ")].join(" ").toLowerCase().includes(st.q);
  });
  const n = rows.length;
  const get = (r, k) => { const p = r.prices[main] || {};
    return k === "name" ? (r.shader + r.pass).toLowerCase() : k === "mats" ? r.materials.length
      : k === "frame" ? ((r.in_frame || {})[main] || 0) : k === "px" ? (p.px_price ?? -1) : k === "vtx" ? (p.vtx_price ?? -1)
      : k === "regs" ? (p.regs ?? -1) : k.startsWith("core:") ? ((r.prices[k.slice(5)] || {}).px_price ?? -1) : 0; };
  const [sk, sd] = st.psort;
  rows.sort((a, b) => { const x = get(a, sk), y = get(b, sk); return (x < y ? -1 : x > y ? 1 : 0) * sd; });
  const shown = st.all ? rows : rows.slice(0, TOP);
  const others = D.cores.filter(c => c.name !== main);
  const pth = (k, label, cls = "") => `<th class="${cls} ${sk === k ? "sorted" : ""}" data-pk="${k}">${label}`
    + `${sk === k ? (sd < 0 ? " ↓" : " ↑") : ""}</th>`;
  const fixed = 9 + 9 + 8 + 9;
  const coreW = others.length ? Math.min(7, 24 / others.length) : 0;
  const nameW = 100 - fixed - 7 - coreW * others.length;
  let h = `<h2>Шейдеры проекта <small>${shown.length} из ${n} · ${P.materials} материалов → ${P.variants} вариантов · ${short(main)} · `
    + `один ряд — один скомпилированный шейдер · клик по строке — подробности</small></h2>`;
  h += `<div class="wrap ev"><table><colgroup><col style="width:${nameW}%"><col style="width:7%"><col style="width:9%"><col style="width:9%">`
     + others.map(() => `<col style="width:${coreW}%">`).join("") + `<col style="width:8%"><col style="width:9%"></colgroup><thead><tr>`
     + pth("name", "Шейдер · пасс", "l") + pth("mats", "Материалы") + pth("px", "Цикл / пикс") + pth("vtx", "Цикл / верш")
     + others.map(c => pth("core:" + c.name, short(c.name))).join("")
     + pth("regs", "Регистры") + pth("frame", "% кадра") + `</tr></thead><tbody>`;
  for (const r of shown) {
    const p = r.prices[main] || {}, f = (r.in_frame || {})[main];
    const open = st.open.has("p:" + r._id);
    h += `<tr class="r ${open ? "open" : ""}" data-g="p:${r._id}">`
       + `<td class="l" title="${esc(r.shader)} · ${esc(r.pass)}"><span class="dot" style="background:${r.frame_events ? "var(--accent)" : "var(--line)"}" title="${r.frame_events ? "есть в кадре" : "нет в кадре"}"></span>`
       + `<span class="shn">${esc(r.shader)}</span> <span class="pass">· ${esc(r.pass)}</span>${flagCount(p.flags || [])}</td>`
       + `<td class="num">${r.materials.length}</td>`
       + `<td class="num big">${cyc(p.px_price)}${String(p.px_path || "").startsWith("loop") ? ' <span class="lp">↻</span>' : ""}</td>`
       + `<td class="num">${cyc(p.vtx_price)}</td>`
       + others.map(c => `<td class="num st">${cyc((r.prices[c.name] || {}).px_price)}</td>`).join("")
       + `<td class="num ${p.regs > 32 ? "warnc" : ""}">${p.regs ?? "—"}</td>`
       + `<td class="num">${r.frame_events ? pc(f || 0) : "—"}</td></tr>`;
    if (open) {
      const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
      const kw = r.keyword_sets[0] || [];
      h += `<tr class="det"><td colspan="${6 + others.length}"><div class="dg num">`
         + item("Цикл / пиксель", `${cyc(p.px_price)} · ${esc((p.px_bound || []).join("+"))}${pathNote(p.px_path)}`)
         + item("Цикл / вершина", `${cyc(p.vtx_price)} · ${esc((p.vtx_bound || []).join("+"))}`)
         + item("Регистры", `${p.regs ?? "—"}${p.fp16 != null ? ` · fp16 ${p.fp16}%` : ""}`)
         + item("В кадре", r.frame_events ? `${pc(f || 0)} · ${plural(r.frame_events, "событие", "события", "событий")}` : "нет")
         + `</div>`
         + ((p.flags || []).length ? `<div class="dl">${p.flags.map(x => `<span class="flag ${x}">${x}</span>`).join(" ")}</div>` : "")
         + `<div class="dl">${kw.length ? kw.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">без keywords</span>'}`
         + (r.keyword_sets.length > 1 ? ` · ещё ${r.keyword_sets.length - 1} наборов keywords с тем же кодом` : "") + `</div>`
         + `<div class="dl">Материалы: ${r.materials.map(esc).join(", ")}</div></td></tr>`;
    }
  }
  h += "</tbody></table></div>";
  const list = (title, items, line) => items.length ? `<details style="margin-top:12px"><summary class="st">${title} · ${items.length}</summary>`
    + `<div class="wrap"><table><tbody>${items.map(line).join("")}</tbody></table></div></details>` : "";
  h += list("Варианты без цены (не скомпилированы: нужен открытый редактор)", P.unpriced, u =>
    `<tr><td class="l">${esc(u.shader)} · ${esc(u.pass)}</td><td class="l">${u.keywords.map(esc).join(" ")}</td>`
    + `<td class="num">${u.materials.length}</td><td class="l fail">${esc(u.reason)}</td></tr>`);
  h += list("Материалы, которые не попали в таблицу", P.skipped, x =>
    `<tr><td class="l">${esc(x.material)}</td><td class="l">${esc(x.shader || "")}</td><td class="l fail">${esc(x.reason)}</td></tr>`);
  return h;
}

function bindOpen() {
  document.querySelectorAll("#main tr.r, #project tr.r").forEach(el => el.onclick = () => {
    const k = el.dataset.g ?? +el.dataset.i; st.open.has(k) ? st.open.delete(k) : st.open.add(k); render(); });
}
function render() {
  tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.tab));
  pfSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.pf));
  const proj = st.tab === "project";
  document.querySelectorAll(".project-only").forEach(el => el.classList.toggle("hide", !proj));
  for (const id of ["stagebar", "stages", "loops", "main", "missing"]) document.getElementById(id).classList.toggle("hide", proj);
  bySeg.closest(".group").classList.toggle("hide", proj);
  document.getElementById("project").classList.toggle("hide", !proj);
  if (proj) {
    coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
    document.getElementById("all").textContent = st.all ? `Только топ-${TOP}` : "Показать все";
    document.getElementById("project").innerHTML = projectTable();
    document.querySelectorAll("thead th[data-pk]").forEach(el => el.onclick = () => {
      const k = el.dataset.pk; st.psort = [k, st.psort[0] === k ? -st.psort[1] : (k === "name" ? 1 : -1)]; render(); });
    bindOpen();
    return;
  }
  coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
  bySeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.by));
  document.getElementById("all").textContent = st.all ? `Только топ-${TOP}` : "Показать все";
  stages();
  loopsTable();
  document.getElementById("main").innerHTML = st.by === "event" ? eventsTable() : groupTable();
  document.querySelectorAll("thead th[data-k]").forEach(el => el.onclick = () => {
    const k = el.dataset.k; st.sort = [k, st.sort[0] === k ? -st.sort[1] : (k === "name" || k === "shader" || k === "index" ? 1 : -1)]; render(); });
  bindOpen();
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
