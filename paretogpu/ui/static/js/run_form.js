"use strict";
function projects() {
  const open = OPT.unity_projects.filter(p => p.open), rest = OPT.unity_projects.filter(p => !p.open);
  const o = p => `<option value="${esc(p.path)}">${esc(p.title)} — ${esc(p.path)}${p.version ? " · " + esc(p.version) : ""}${p.snapshots ? ` · снимков: ${p.snapshots}` : ""}</option>`;
  $("#project").innerHTML = (open.length ? `<optgroup label="Открыты в Unity">${open.map(o).join("")}</optgroup>` : "") +
    (rest.length ? `<optgroup label="Остальные проекты (Unity Hub)">${rest.map(o).join("")}</optgroup>` : "") +
    `<option value="__other">Другая папка…</option>`;
  const saved = store.get("project");
  if (saved && OPT.unity_projects.some(p => p.path === saved)) $("#project").value = saved;
  else if (saved) { $("#project").value = "__other"; $("#projectPath").value = saved; }
  else if (open.length) $("#project").value = open[0].path;
  $("#projectPath").style.display = $("#project").value === "__other" ? "" : "none";
}
const projectValue = () => recalc ? recalc.project : ($("#project").value === "__other" ? $("#projectPath").value.trim() : $("#project").value);

$("#project").onchange = () => {
  $("#projectPath").style.display = $("#project").value === "__other" ? "" : "none";
  if ($("#project").value !== "__other") store.set("project", $("#project").value);
  checkEditor();
};
$("#projectPath").onchange = () => { store.set("project", $("#projectPath").value.trim()); checkEditor(); };

let edSeq = 0;
async function checkEditor(quiet) {
  const el = $("#edStatus"), v = projectValue();
  if (recalc) { edReady = true; updateRun(); return; }
  if (!v) { el.textContent = ""; edReady = false; updateRun(); return; }
  const seq = ++edSeq;
  if (!quiet) { el.className = "status wait"; el.textContent = "проверяю Unity…"; }
  const s = await api("/api/editor?project=" + encodeURIComponent(v));
  if (seq !== edSeq) return;
  edReady = !!s.ready;
  if (!s.project) { el.className = "status bad"; el.textContent = "В этой папке нет Unity-проекта."; }
  else if (s.ready) { el.className = "status ok"; el.textContent = `✓ Unity ${s.version || ""} открыт и готов`; }
  else if (!s.open) { el.className = "status bad"; el.textContent = `Проект не открыт. Откройте его в Unity ${s.version || ""} — кадр снимается из открытого редактора.`; }
  else if (!s.cli) { el.className = "status bad"; el.textContent = "Не найден Unity CLI (команда unity): запустите start.bat, он покажет, что поставить."; }
  else { el.className = "status bad"; el.textContent = "Unity открыт, но занят (компиляция, импорт) или Unity CLI не отвечает. Подождите — статус обновится сам."; }
  updateRun();
}
setInterval(() => { if (document.visibilityState === "visible" && $("#tab-run").classList.contains("on") && !running()) checkEditor(true); }, 8000);

const radio = n => document.querySelector(`input[name=${n}]:checked`).value;
document.querySelectorAll("input[type=radio]").forEach(r => r.addEventListener("change", saveSimple));
$("#customCores").addEventListener("focus", () => { document.querySelector("input[name=devices][value=custom]").checked = true; });
$("#customCores").addEventListener("change", saveSimple);
function saveSimple() { store.set("simple", {devices: radio("devices"), scope: radio("scope"), cores: $("#customCores").value}); }
function loadSimple() {
  const s = store.get("simple") || {};
  for (const n of ["devices", "scope"]) {
    const el = s[n] && document.querySelector(`input[name=${n}][value=${s[n]}]`);
    if (el) el.checked = true;
  }
  $("#customCores").value = s.cores || "";
}

function advFields() {
  const out = [], seen = new Set();
  for (const cmd of ["frame", "cost"]) for (const a of SCHEMA[cmd].args) {
    if (SIMPLE.has(a.dest) || seen.has(a.dest)) continue;
    if (recalc && cmd === "frame") continue;
    seen.add(a.dest); out.push(a);
  }
  return out;
}
function renderAdv() {
  $("#adv").innerHTML = `<p class="muted small">Обычно не нужны: значения по умолчанию подобраны под проверку кадра.</p>` + advFields().map(a => {
    const id = "a-" + a.dest, lbl = esc(ADV_LABEL[a.dest] || a.flag);
    if (a.kind === "flag") return `<div class="row2"><label class="chk"><input type="checkbox" id="${id}"> ${lbl}<code>${esc(a.flag)}</code></label><div class="help">${esc(a.help)}</div></div>`;
    const ph = a.dest === "variants" ? "своя папка на проект (paretogpu/out/variants_<проект>)" : a.default != null && a.default !== "" ? `по умолчанию: ${a.default}` : "";
    const ctl = a.choices ? `<select id="${id}"><option value="">по умолчанию (${esc(a.default)})</option>${a.choices.map(c => `<option>${esc(c)}</option>`).join("")}</select>`
      : `<input type="text" id="${id}" placeholder="${esc(ph)}">`;
    return `<div class="row2"><label for="${id}">${lbl}<code>${esc(a.flag)}</code></label>${ctl}<div class="help">${esc(a.help)}</div></div>`;
  }).join("");
}

function values() {
  const v = {};
  const p = projectValue();
  if (p) v.project = p;
  const dev = radio("devices");
  v.cores = dev === "mobile" ? "preset:mobile" : dev === "g78" ? "Mali-G78" : $("#customCores").value.trim();
  if (radio("scope") === "materials") v.materials = true;
  if (!recalc && $("#suffix").value.trim()) v.suffix = $("#suffix").value.trim();
  for (const a of advFields()) {
    const el = $("#a-" + a.dest);
    if (!el) continue;
    if (a.kind === "flag") { if (el.checked) v[a.dest] = true; }
    else if (a.kind === "list") { const l = el.value.split(";").map(s => s.trim()).filter(Boolean); if (l.length) v[a.dest] = l; }
    else if (el.value.trim()) v[a.dest] = el.value.trim();
  }
  if (recalc) v.frame = recalc.path;
  return v;
}

function updateRun() {
  const b = $("#runBtn"), why = $("#runWhy");
  b.textContent = recalc ? "▶ Пересчитать снимок" : "▶ Проверить кадр";
  if (running()) { b.disabled = true; why.textContent = "Идёт проверка — новую можно начать после неё."; return; }
  if (!recalc && !projectValue()) { b.disabled = true; why.textContent = "Выберите проект."; return; }
  if (!recalc && edReady === false) { b.disabled = true; why.textContent = "Кнопка станет активной, когда Unity с проектом откроется и будет готов."; return; }
  if (radio("devices") === "custom" && !$("#customCores").value.trim()) { b.disabled = true; why.textContent = "Впишите ядра Mali или выберите вариант выше."; return; }
  b.disabled = false; why.textContent = recalc ? "Unity нужен, только если шейдеры поменялись с прошлого расчёта." : "";
}
document.querySelectorAll("input[name=devices]").forEach(r => r.addEventListener("change", updateRun));
$("#customCores").addEventListener("input", updateRun);

async function start(preset, vals) {
  $("#formErr").textContent = "";
  const r = await api("/api/run", {preset, values: vals});
  if (r.error) { $("#formErr").textContent = r.error; tab("run"); return false; }
  viewId = null; job = null; logNext = 0; logEl = null; detailsOpen = null;
  if ($("#backCur")) $("#backCur").remove();
  tab("run");
  poll();
  return true;
}
$("#runBtn").onclick = async () => { if (await start(recalc ? "cost" : "frame_cost", values())) setRecalc(null); };

function setRecalc(x) {
  recalc = x;
  $("#recalcBanner").style.display = x ? "" : "none";
  if (x) $("#recalcBanner").innerHTML = `<div style="flex:1">Пересчёт снимка <b>${esc(x.name)}</b>${x.project ? ` (${esc(x.project)})` : ""}: кадр не снимается заново, только цена. Поправили шейдер — пересчитайте и сразу увидите разницу с прошлым расчётом.</div><button class="btn sm" id="recalcCancel">Отмена</button>`;
  if (x) $("#recalcCancel").onclick = () => setRecalc(null);
  $("#formTitle").textContent = x ? "Пересчёт снимка" : "Новая проверка";
  for (const id of ["#howto", "#projField", "#suffixField"]) $(id).style.display = x ? "none" : "";
  renderAdv(); checkEditor(); updateRun();
}

/* ---------- run panel ---------- */
