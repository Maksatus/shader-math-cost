"use strict";
let RUNS = [];
async function loadCompare(a, b) {
  RUNS = (await api("/api/costs")).runs;
  const groups = {};
  for (const r of RUNS) (groups[r.snapshot] = groups[r.snapshot] || []).push(r);
  const opts = Object.entries(groups).map(([snap, rs]) => `<optgroup label="${esc(snap)}${rs[0].project ? " · " + esc(rs[0].project) : ""}">` +
    rs.map(r => `<option value="${esc(r.id)}">${when(r.computed_at)} · ${M(r.total)} млн · ${esc(r.main_core || "")}${r.api ? " · " + esc(r.api) : ""}</option>`).join("") + `</optgroup>`).join("");
  $("#cmpA").innerHTML = `<option value="">— выберите —</option>` + opts;
  $("#cmpB").innerHTML = `<option value="">— выберите —</option>` + opts;
  const saved = store.get("compare") || {};
  a = a !== undefined ? a : saved.a; b = b !== undefined ? b : saved.b;
  const has = id => id && RUNS.some(r => r.id === id);
  if (!has(b)) b = RUNS[0] && RUNS[0].id;
  if (!has(a) || a === b) {
    const rb = RUNS.find(r => r.id === b);
    const same = rb && RUNS.filter(r => r.snapshot === rb.snapshot && r.id !== b && r.computed_at < rb.computed_at)[0];
    const proj = rb && RUNS.filter(r => r.snapshot !== rb.snapshot && r.project === rb.project && r.snapshot_time < rb.snapshot_time)[0];
    a = (same || proj || RUNS.find(r => r.id !== b) || {}).id;
  }
  $("#cmpA").value = a || ""; $("#cmpB").value = b || "";
  document.querySelectorAll("#cmpMode button").forEach(x => x.classList.toggle("on", x.dataset.m === "frames"));
  $("#tab-compare").classList.remove("matmode");
  showCompare();
}
function showCompare() {
  const a = $("#cmpA").value, b = $("#cmpB").value;
  store.set("compare", {a, b});
  const ok = a && b;
  $("#cmpEmpty").style.display = ok ? "none" : "";
  $("#cmpFrame").style.display = ok ? "" : "none";
  for (const id of ["#cmpSave", "#cmpOpen"]) $(id).style.visibility = ok ? "" : "hidden";
  if (!ok) {
    $("#cmpEmpty").innerHTML = `<div class="card empty" style="max-width:800px;margin:0 auto">${RUNS.length < 2
      ? "Для сравнения нужно хотя бы два расчёта.<br><br>Поправили шейдер — откройте «Отчёты» и нажмите «Пересчитать» у снимка: тот же кадр посчитается с новыми ценами, и разница будет только от шейдеров.<br>Или снимите новый кадр во вкладке «Проверка кадра»."
      : "Выберите расчёты A (было) и B (стало) вверху."}</div>`;
    $("#cmpFrame").src = "about:blank";
    return;
  }
  const q = `a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`;
  if ($("#cmpFrame").dataset.q !== q) { $("#cmpFrame").src = "/compare?" + q; $("#cmpFrame").dataset.q = q; }
  $("#cmpSave").href = "/compare?" + q + "&download=1";
  $("#cmpOpen").href = "/compare?" + q;
}
$("#cmpA").onchange = showCompare;
$("#cmpB").onchange = showCompare;
$("#cmpSwap").onclick = () => { const a = $("#cmpA").value; $("#cmpA").value = $("#cmpB").value; $("#cmpB").value = a; showCompare(); };
/* ---------- compare: two materials ---------- */
let cmpMode = store.get("compareMode") === "materials" ? "materials" : "frames";
let MATS = [], matsOf = null, matJobId = null, matTimer = null;
function setMode(m) {
  cmpMode = m;
  store.set("compareMode", m);
  document.querySelectorAll("#cmpMode button").forEach(b => b.classList.toggle("on", b.dataset.m === m));
  $("#tab-compare").classList.toggle("matmode", m === "materials");
  $("#cmpFrame").dataset.q = "";
  m === "materials" ? loadMaterials() : loadCompare();
}
document.querySelectorAll("#cmpMode button").forEach(b => b.onclick = () => setMode(b.dataset.m));
const matProject = () => $("#matProject").value;
async function loadMaterials() {
  document.querySelectorAll("#cmpMode button").forEach(b => b.classList.toggle("on", b.dataset.m === "materials"));
  $("#tab-compare").classList.add("matmode");
  if (!$("#matProject").options.length) {
    const ps = OPT.unity_projects;
    $("#matProject").innerHTML = ps.map(p => `<option value="${esc(p.path)}">${esc(p.title)}</option>`).join("");
    const saved = (store.get("matcompare") || {}).project || store.get("project");
    if (ps.some(p => p.path === saved)) $("#matProject").value = saved;
    else { const open = ps.find(p => p.open); if (open) $("#matProject").value = open.path; }
    const s = store.get("matcompare") || {};
    if (s.project === matProject()) { $("#matA").value = s.a || ""; $("#matB").value = s.b || ""; }
  }
  await Promise.all([loadMatList(), loadMatHist()]);
}
async function loadMatList() {
  const pr = matProject();
  if (!pr || matsOf === pr) return;
  matsOf = pr; MATS = [];
  const d = await api("/api/materials?project=" + encodeURIComponent(pr));
  if (matsOf !== pr) return;
  MATS = d.materials || [];
  suggest($("#matA"), $("#dl-matA")); suggest($("#matB"), $("#dl-matB"));
  matStatus();
}
function suggest(inp, dl) {
  // the datalist holds the 60 best matches of what is typed: thousands of options make the browser slow
  const q = inp.value.trim().toLowerCase();
  const hits = [];
  for (const m of MATS) {
    if (m.error) continue;
    const name = m.name.toLowerCase(), path = m.path.toLowerCase();
    const rank = !q ? 2 : name === q ? 0 : name.startsWith(q) ? 1 : path.includes(q) || (m.shader || "").toLowerCase().includes(q) ? 2 : -1;
    if (rank >= 0) hits.push([rank, m]);
    if (!q && hits.length >= 60) break;
  }
  hits.sort((x, y) => x[0] - y[0]);
  dl.innerHTML = hits.slice(0, 60).map(([, m]) => `<option value="${esc(m.path)}">${esc(m.name)} · ${esc(m.shader || "")}</option>`).join("");
}
const matOf = v => MATS.find(m => m.path === v.trim());
function matStatus() {
  const a = matOf($("#matA").value), b = matOf($("#matB").value);
  store.set("matcompare", {project: matProject(), a: $("#matA").value.trim(), b: $("#matB").value.trim()});
  const busy = !!(job && job.status === "running");
  $("#matRun").disabled = !(a && b) || busy;
  $("#matRun").title = busy ? "Уже идёт запуск" : !(a && b) ? "Выберите оба материала из списка" : "";
  return {a, b};
}
for (const [inp, dl] of [["#matA", "#dl-matA"], ["#matB", "#dl-matB"]]) {
  const el = $(inp);
  el.oninput = () => { suggest(el, $(dl)); matStatus(); };
  el.ondragover = e => { e.preventDefault(); el.classList.add("drop"); };
  el.ondragleave = () => el.classList.remove("drop");
  el.ondrop = async e => {
    e.preventDefault(); el.classList.remove("drop");
    const f = e.dataTransfer.files[0];
    const text = e.dataTransfer.getData("text/plain");
    if (!f) { if (text) { el.value = text.replace(/\\/g, "/").replace(/^.*?(?=Assets\/)/, ""); el.oninput(); } return; }
    if (!f.name.endsWith(".mat")) { matMsg(`${esc(f.name)}: это не материал (.mat).`); return; }
    const r = await api("/api/material_match", {project: matProject(), name: f.name, text: await f.text()});
    if (!r.paths || !r.paths.length) { matMsg(`Материал ${esc(f.name)} не найден в проекте ${esc(matProject())}.`); return; }
    el.value = r.paths[0];
    if (!matOf(r.paths[0])) { forgetMats(matProject()); await loadMatList(); }
    el.oninput();
    if (r.paths.length > 1) matMsg(`В проекте несколько ${esc(f.name)}, выбран ${esc(r.paths[0])}. Другие — в списке поля.`);
  };
}
$("#matProject").onchange = () => { $("#matA").value = ""; $("#matB").value = ""; loadMatList(); matStatus(); };
$("#matSwap").onclick = () => { const a = $("#matA").value; $("#matA").value = $("#matB").value; $("#matB").value = a; matStatus(); };
function matMsg(html) {
  $("#cmpFrame").style.display = "none"; $("#cmpFrame").src = "about:blank"; $("#cmpFrame").dataset.q = "";
  $("#cmpEmpty").style.display = "";
  $("#cmpEmpty").innerHTML = `<div class="card" style="max-width:800px;margin:0 auto">${html}</div>`;
}
async function loadMatHist(select) {
  const items = (await api("/api/matcompares")).items || [];
  $("#matHist").innerHTML = (items.length ? "" : `<option value="">прошлых сравнений нет</option>`) + items.map(x =>
    `<option value="${esc(x.url)}">${when(x.computed_at)} · ${esc(x.a.material)} → ${esc(x.b.material)}</option>`).join("");
  const want = select || store.get("matHist");
  if (items.some(x => x.url === want)) $("#matHist").value = want;
  if (matJobId && job && job.id === matJobId && job.status === "running") return matProgress();
  showMat();
}
function showMat() {
  const url = $("#matHist").value;
  $("#cmpOpen").style.visibility = url ? "" : "hidden";
  if (!url) {
    matMsg(`<h2>Сравнение двух материалов</h2><p class="muted">Выберите проект и материалы A и B вверху (поиск по имени, пути, шейдеру или перетащите .mat) и нажмите «Сравнить». Нужен открытый редактор Unity.
      <a href="#" data-ref="usage" style="color:var(--accent)">Подробнее ↗</a></p>`);
    return;
  }
  store.set("matHist", url);
  $("#cmpEmpty").style.display = "none";
  $("#cmpFrame").style.display = "";
  if ($("#cmpFrame").dataset.q !== url) { $("#cmpFrame").src = url; $("#cmpFrame").dataset.q = url; }
  $("#cmpOpen").href = url;
}
$("#matHist").onchange = showMat;
$("#matRun").onclick = async () => {
  const {a, b} = matStatus();
  if (!a || !b) return;
  const r = await api("/api/run", {preset: "matcompare", values: {project: matProject(), a: a.path, b: b.path}});
  if (r.error) { matMsg(`<div class="err">${esc(r.error)}</div>`); return; }
  matJobId = r.id;
  poll();
  matProgress();
};
function matProgress() {
  clearTimeout(matTimer);
  if (cmpMode !== "materials") return;
  const j = job && job.id === matJobId ? job : null;
  if (!j || j.status === "running") {
    const ph = j && flatPhases(j).find(x => x.status === "running");
    const {pct: p} = j ? overall(j, j.now || Date.now() / 1000) : {pct: 0};
    matMsg(`<h2>Сравниваю материалы <span class="spin"></span></h2>
      <div class="now">${ph ? esc((PHASE_TEXT[ph.id] || [ph.id])[0]) + ` <span class="muted">· ${esc(phaseInfo(ph, j.now || Date.now() / 1000))}</span>` : "Подготовка…"}</div>
      <div class="bar"><i style="width:${p.toFixed(1)}%"></i></div><div class="small muted">Подробный ход и лог — во вкладке «Проверка кадра».</div>`);
    matStatus();
    matTimer = setTimeout(matProgress, 700);
    return;
  }
  matJobId = null;
  matStatus();
  if (j.status === "ok") return loadMatHist(j.report);
  const tail = (j.log || []).slice(-12).join("\n");
  matMsg(`<h2 class="bad">Сравнение не получилось</h2>${j.hint ? `<div class="hint">${esc(j.hint.text || j.hint)}</div>` : ""}
    <pre class="log">${esc(tail)}</pre><div class="small muted">Весь лог — во вкладке «Проверка кадра».</div>`);
}

/* ---------- one material ---------- */
const MATS_OF = new Map();  // project -> its materials (for the pickers)
async function materialsOf(project) {
  if (!MATS_OF.has(project)) MATS_OF.set(project, (await api("/api/materials?project=" + encodeURIComponent(project))).materials || []);
  return MATS_OF.get(project);
}
// a dropped material the cached list does not have (added by a branch switch): load the list again
function forgetMats(project) {
  MATS_OF.delete(project);
  if (matsOf === project) matsOf = null;
}
