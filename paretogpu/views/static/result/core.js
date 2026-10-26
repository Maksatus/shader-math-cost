// frame deltas in cycles below half the last shown digit (0.00 млн) are float noise, not a change
const z = d => Math.abs(d) < 5e3 ? 0 : d;
const sM = x => (x = z(x), x > 0 ? "+" : x < 0 ? "−" : "±") + M(Math.abs(x));
const pct = (d, a) => !a ? (d ? "нов." : "0%") : (d > 0 ? "+" : d < 0 ? "−" : "±") + Math.abs(100 * d / a).toFixed(Math.abs(d / a) < 0.1 ? 1 : 0) + "%";
const cls = d => d < 0 ? "good" : d > 0 ? "bad" : "";
const pr = x => x == null ? "—" : (+x).toFixed(2);
const STAGE = {opaque: "opaque", transparent: "transparent", post: "post", shadow: "shadows", prepass: "prepass",
  ui: "UI", compute: "compute", tile: "Mali tile", other: "other"};
const stageColor = s => `var(--stage-${s in STAGE ? s : "other"})`;
const PART = {fragment: "pixels", vertex: "vertices", compute: "compute"};
const UNIT = {fragment: "пикс.", vertex: "vert.", compute: "потоков"};
const FLAG = {spilling: "spilling", regs_gt32: "регистров > 32", low_fp16: "мало fp16", dynamic_loop: "динамический цикл", sfu_bound: "bottleneck в SFU",
  uncompressed: "несжатая", no_mips: "без мипов"};
const PIPE = {
  arith: ["A", "арифметика"],
  fma: ["FMA", "умножение/сложение"],
  cvt: ["CVT", "сравнения/типы"],
  sfu: ["SFU", "спецфункции"],
  ls: ["LS", "память"],
  v: ["V", "varyings"],
  t: ["T", "текстуры"],
};
const SITE_REF = location.protocol.startsWith("http") && /^(127\.0\.0\.1|localhost)$/.test(location.hostname)
  ? "/site/reference.html" : "https://maksatus.github.io/shader-math-cost/reference.html";
const SITE_COST = SITE_REF + "#cost";
const boundTag = b => b && b.length ? b.map(x => { const p = PIPE[x] || [x, ""];
  return `<span class="bnd" onclick="window.open(SITE_REF + '#fix', '_blank')">${esc(p[0])}${p[1] ? ` <i>${esc(p[1])}</i>` : ""}</span>`; }).join(" + ") : "—";
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
    <div class="small muted">расчёт ${when(i.computed_at, true)} · ${esc(i.project || "")}</div>
    <div class="small muted">malioc ${esc(i.malioc || "?")}${i.resolution ? " · " + i.resolution.join("×") : ""}${i.quality ? " · " + esc(i.quality) : ""}</div></div>`;
}

const fill = (t, p) => t.replace(/\{(\w+)\}/g, (_, k) => p[k] ?? "?");
const msg = w => (w.side ? w.side + ": " : "") + fill(MSG.warnings[w.code] || w.code || "", w);

const VIEWS = {};
function render() {
  if (!D) { $("#app").innerHTML = `<div class="empty">Нет данных сравнения.</div>`; return; }
  (VIEWS[D.kind] || VIEWS.compare)();
}
