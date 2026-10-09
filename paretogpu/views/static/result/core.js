const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const M = x => x == null ? "—" : (x / 1e6).toFixed(Math.abs(x) < 1e7 ? 2 : 1);
const sM = x => (x > 0 ? "+" : x < 0 ? "−" : "±") + M(Math.abs(x));
const pct = (d, a) => !a ? (d ? "нов." : "0%") : (d > 0 ? "+" : d < 0 ? "−" : "±") + Math.abs(100 * d / a).toFixed(Math.abs(d / a) < 0.1 ? 1 : 0) + "%";
const cls = d => d < 0 ? "good" : d > 0 ? "bad" : "";
const num = x => x == null ? "—" : Math.round(x).toLocaleString("ru-RU");
const pr = x => x == null ? "—" : (+x).toFixed(2);
const when = t => t ? new Date(t * 1000).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit"}) : "—";
const STAGE = {opaque: "непрозрачное", transparent: "прозрачное", post: "постобработка", shadow: "тени", prepass: "препасс",
  ui: "UI", compute: "compute", other: "прочее"};
const PART = {fragment: "пиксели", vertex: "вершины", compute: "compute"};
const UNIT = {fragment: "пикс.", vertex: "верш.", compute: "потоков"};
const FLAG = {spilling: "spilling", regs_gt32: "регистров > 32", low_fp16: "мало fp16", dynamic_loop: "динамический цикл", sfu_bound: "упор в SFU"};
// the pipes of a Mali shader core (malioc): the bound is the busiest one, its cycles are the price
const PIPE = {
  arith: ["A", "арифметика", "Арифметика: FMA, CVT и SFU вместе"],
  fma: ["FMA", "умножение/сложение", "FMA — умножение и сложение (+ * mad lerp dot). Упор: меньше математики, half вместо float"],
  cvt: ["CVT", "сравнения/типы", "CVT — сравнения, выбор и преобразования типов (min max clamp step ?:, half↔float). Упор: меньше сравнений и преобразований"],
  sfu: ["SFU", "спецфункции", "SFU — спецфункции (rcp sqrt exp log sin cos) и деление. Упор: заменить их приближениями"],
  ls: ["LS", "память", "LS — load/store: чтение буферов (свет Forward+, константы), спилл регистров, атомики. Упор: убрать спилл, меньше чтений буферов в циклах"],
  v: ["V", "varyings", "V — интерполяция varyings из вершинного шейдера. Упор: меньше varyings, передавать в half"],
  t: ["T", "текстуры", "T — выборки и фильтрация текстур. Упор: меньше выборок, ASTC, mip-map, проще фильтрация"],
};
const SITE_REF = location.protocol.startsWith("http") && /^(127\.0\.0\.1|localhost)$/.test(location.hostname)
  ? "/site/reference.html" : "https://maksatus.github.io/shader-math-cost/reference.html";
const SITE_COST = SITE_REF + "#cost";
const boundTag = b => b && b.length ? b.map(x => { const p = PIPE[x] || [x, "", ""];
  return `<span class="bnd" title="${esc(p[2] + " · клик — справочник")}" onclick="window.open(SITE_REF + '#fix', '_blank')">${esc(p[0])}${p[1] ? ` <i>${esc(p[1])}</i>` : ""}</span>`; }).join(" + ") : "—";
const costHelp = `<a href="${SITE_COST}" target="_blank" class="costhelp">как считается цена — справочник ↗</a>`;
const refLink = (id, text) => `<a href="${SITE_REF}#${id}" target="_blank" class="costhelp">${text} — справочник ↗</a>`;
let core = D && D.main_core, hideSmall = true, query = "", open = new Set();

function bar(d, max, w = 120) {
  const f = max ? Math.min(1, Math.abs(d) / max) : 0;
  const style = d < 0 ? `right:50%;width:${50 * f}%;background:var(--cheap)` : `left:50%;width:${50 * f}%;background:var(--heavy)`;
  return `<span class="cell-bar" style="width:${w}px"><span class="mid"></span><i style="${style}"></i></span>`;
}

function side(i, tag) {
  return `<div class="side ${tag === "B" ? "b" : ""}"><span class="tag">${tag}</span><b>${esc(i.snapshot || "?")}</b>
    <div class="small muted">расчёт ${when(i.computed_at)} · ${esc(i.project || "")}</div>
    <div class="small muted">${esc(i.api || "")} · malioc ${esc(i.malioc || "?")}${i.resolution ? " · " + i.resolution.join("×") : ""}${i.quality ? " · " + esc(i.quality) : ""}</div></div>`;
}

const VIEWS = {};
function render() {
  if (!D) { $("#app").innerHTML = `<div class="empty">Нет данных сравнения.</div>`; return; }
  (VIEWS[D.kind] || VIEWS.compare)();
}
