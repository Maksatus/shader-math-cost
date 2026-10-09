"use strict";
let msJobId = null, msTimer = null;
async function loadMs() {
  if (!$("#msProject").options.length) {
    const ps = OPT.unity_projects;
    $("#msProject").innerHTML = ps.map(p => `<option value="${esc(p.path)}">${esc(p.title)}</option>`).join("");
    const s = store.get("matshader") || {};
    const saved = s.project || store.get("project");
    if (ps.some(p => p.path === saved)) $("#msProject").value = saved;
    else { const open = ps.find(p => p.open); if (open) $("#msProject").value = open.path; }
    if (s.project === $("#msProject").value) $("#msMat").value = s.mat || "";
  }
  msSuggest();
  await loadMsHist();
}
async function msSuggest() {
  const list = await materialsOf($("#msProject").value);
  const saved = MATS; MATS = list;  // suggest() reads MATS
  suggest($("#msMat"), $("#dl-ms"));
  MATS = saved;
  const ok = list.some(m => m.path === $("#msMat").value.trim());
  const busy = !!(job && job.status === "running");
  $("#msRun").disabled = !ok || busy;
  $("#msRun").title = busy ? "Уже идёт запуск" : ok ? "" : "Выберите материал из списка";
  store.set("matshader", {project: $("#msProject").value, mat: $("#msMat").value.trim()});
}
$("#msMat").oninput = msSuggest;
$("#msProject").onchange = () => { $("#msMat").value = ""; msSuggest(); };
$("#msMat").ondragover = e => { e.preventDefault(); $("#msMat").classList.add("drop"); };
$("#msMat").ondragleave = () => $("#msMat").classList.remove("drop");
$("#msMat").ondrop = async e => {
  e.preventDefault(); $("#msMat").classList.remove("drop");
  const f = e.dataTransfer.files[0];
  if (!f || !f.name.endsWith(".mat")) { msMsg(`Перетащите файл материала (.mat).`); return; }
  const r = await api("/api/material_match", {project: $("#msProject").value, name: f.name, text: await f.text()});
  if (!r.paths || !r.paths.length) { msMsg(`Материал ${esc(f.name)} не найден в проекте.`); return; }
  $("#msMat").value = r.paths[0];
  if (!(MATS_OF.get($("#msProject").value) || []).some(m => m.path === r.paths[0])) forgetMats($("#msProject").value);
  msSuggest();
};
function msMsg(html) {
  $("#msFrame").style.display = "none"; $("#msFrame").src = "about:blank"; $("#msFrame").dataset.q = "";
  $("#msEmpty").style.display = "";
  $("#msEmpty").innerHTML = `<div class="card" style="max-width:800px;margin:0 auto">${html}</div>`;
}
async function loadMsHist(select) {
  const items = (await api("/api/matshaders")).items || [];
  $("#msHist").innerHTML = (items.length ? "" : `<option value="">прошлых разборов нет</option>`) + items.map(x =>
    `<option value="${esc(x.url)}">${when(x.computed_at)} · ${esc(x.m.material)}</option>`).join("");
  const want = select || store.get("msHist");
  if (items.some(x => x.url === want)) $("#msHist").value = want;
  if (msJobId && job && job.id === msJobId && job.status === "running") return msProgress();
  showMs();
}
function showMs() {
  const url = $("#msHist").value;
  $("#msOpen").style.visibility = url ? "" : "hidden";
  if (!url) {
    msMsg(`<h2>Разбор материала</h2><p class="muted">Выберите проект и материал вверху (поиск или перетащите .mat) и нажмите «Разобрать».
      Нужен открытый редактор Unity. <a href="#" data-ref="usage" style="color:var(--accent)">Подробнее ↗</a></p>`);
    return;
  }
  store.set("msHist", url);
  $("#msEmpty").style.display = "none";
  $("#msFrame").style.display = "";
  if ($("#msFrame").dataset.q !== url) { $("#msFrame").src = url; $("#msFrame").dataset.q = url; }
  $("#msOpen").href = url;
}
$("#msHist").onchange = showMs;
$("#msRun").onclick = async () => {
  const mat = $("#msMat").value.trim();
  const r = await api("/api/run", {preset: "matshader", values: {project: $("#msProject").value, material: mat}});
  if (r.error) { msMsg(`<div class="err">${esc(r.error)}</div>`); return; }
  msJobId = r.id;
  poll();
  msProgress();
};
function msProgress() {
  clearTimeout(msTimer);
  if (!$("#tab-mat").classList.contains("on")) { msTimer = setTimeout(msProgress, 1500); return; }
  const j = job && job.id === msJobId ? job : null;
  if (!j || j.status === "running") {
    const ph = j && flatPhases(j).find(x => x.status === "running");
    const {pct: p} = j ? overall(j, j.now || Date.now() / 1000) : {pct: 0};
    msMsg(`<h2>Разбираю материал <span class="spin"></span></h2>
      <div class="now">${ph ? esc((PHASE_TEXT[ph.id] || [ph.id])[0]) + ` <span class="muted">· ${esc(phaseInfo(ph, j.now || Date.now() / 1000))}</span>` : "Подготовка…"}</div>
      <div class="bar"><i style="width:${p.toFixed(1)}%"></i></div><div class="small muted">Подробный ход и лог — во вкладке «Проверка кадра».</div>`);
    msTimer = setTimeout(msProgress, 700);
    return;
  }
  msJobId = null;
  msSuggest();
  if (j.status === "ok") return loadMsHist(j.report);
  msMsg(`<h2 class="bad">Разбор не получился</h2>${j.hint ? `<div class="hint">${esc(j.hint.text || j.hint)}</div>` : ""}
    <pre class="log">${esc((j.log || []).slice(-12).join("\n"))}</pre><div class="small muted">Весь лог — во вкладке «Проверка кадра».</div>`);
}
