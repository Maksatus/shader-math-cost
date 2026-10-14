"use strict";
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : {method: "POST", body: JSON.stringify(body),
    headers: {"Content-Type": "application/json", "X-ParetoGPU": "1"}});
  const d = await r.json().catch(() => ({error: `HTTP ${r.status}`}));
  if (!r.ok && !d.error) d.error = `HTTP ${r.status}`;
  return d;
};
// the server exits once every window is gone
const WINDOW_ID = Math.random().toString(36).slice(2) + Date.now().toString(36);
const alive = () => fetch("/api/alive?id=" + WINDOW_ID).catch(() => {});
alive(); setInterval(alive, 20000);
addEventListener("pagehide", e => { if (!e.persisted) navigator.sendBeacon("/api/bye", WINDOW_ID); });
const store = {get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }};

function dur(s) {
  if (s == null) return "—";
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s} с`;
  if (s < 3600) return `${Math.floor(s / 60)} мин ${String(s % 60).padStart(2, "0")} с`;
  return `${Math.floor(s / 3600)} ч ${String(Math.floor(s / 60) % 60).padStart(2, "0")} мин`;
}
const approx = s => s == null ? "?" : s < 5 ? "несколько секунд" : "≈ " + dur(s);
const pct = x => x == null ? "—" : (100 * x).toFixed(x < 0.1 ? 1 : 0) + "%";

const presetText = id => ((SCH.presets || []).find(p => p.id === id) || {}).title || id;
const stepText = cmd => (SCHEMA[cmd] || {}).title || cmd;
const phaseText = id => (SCH.phases[id] || {}).label || id;
const phaseUnit = id => (SCH.phases[id] || {}).unit || "";
const NOTE_TEXT = {"capturing": "захват кадра…", "done": "готово", "nothing to compile": "всё уже скомпилировано",
  "the editor does not answer": "редактор не отвечает", "--no-compile": "по уже скомпилированным"};
const SIMPLE = new Set(["project", "frame", "out", "renderdoc", "cores", "core", "materials", "suffix"]);
let SCH = {phases: {}}, SCHEMA, OPT, recalc = null, edReady = null;

function tab(name) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("section").forEach(s => s.classList.toggle("on", s.id === "tab-" + name));
  if (name === "site" && !$("#siteFrame").src) $("#siteFrame").src = "/site/index.html";
  if (name === "ref" && !$("#refFrame").src) $("#refFrame").src = "/site/reference.html";
  if (name === "reports") loadReports();
  if (name === "compare") cmpMode === "materials" ? loadMaterials() : loadCompare();
  if (name === "mat") loadMs();
  store.set("tab", name);
}
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => tab(b.dataset.tab));
document.addEventListener("click", e => {
  const a = e.target.closest("a[data-ref]");
  if (!a) return;
  e.preventDefault();
  $("#refFrame").src = "/site/reference.html#" + a.dataset.ref;
  tab("ref");
});

async function loadEnv() {
  const d = await api("/api/doctor");
  const short = {"malioc (Arm Performance Studio)": "malioc", "RenderDoc": "RenderDoc", "Unity CLI (unity)": "Unity CLI",
                 "Unity Editor (Hub)": "Unity"};
  $("#env").innerHTML = d.checks.map(c => `<span class="envc ${c.status}">${esc(short[c.what] || c.what)}</span>`).join("");
  $("#siteInfo").textContent = d.site.updated ? `Данные замерены ${when(d.site.updated)} · таблица та же, что на сайте` : "";
}
