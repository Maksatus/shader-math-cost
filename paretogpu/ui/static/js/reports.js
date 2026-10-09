"use strict";
let curRep = null;
async function loadReports() {
  const reps = (await api("/api/reports")).reports;
  if (!reps.length) { $("#repList").innerHTML = `<div class="card empty">Снимков ещё нет. Откройте «Проверка кадра» и нажмите «Проверить кадр».</div>`; return; }
  $("#repList").innerHTML = `<div class="card" style="max-width:1500px;margin:0 auto"><h2>Проверенные кадры</h2>
    <p class="muted small">Хранятся только на этом компьютере (${esc(OPT.out)}), в git и на сайт не попадают. Отметьте два снимка, чтобы сравнить их.</p>
    <div class="selbar" id="selBar"></div>
    <table class="rep"><tr><th></th><th>Когда</th><th>Проект</th><th class="n">Draw</th><th class="n">Кадр, млн циклов</th><th>Пиксели / вершины</th><th></th></tr>` +
    reps.map((x, i) => {
      const t = x.total || 0, f = t ? x.fragment / t : 0, v = t ? x.vertex / t : 0;
      return `<tr><td>${x.runs.length ? `<input type="checkbox" class="pick" data-i="${i}">` : ""}</td><td><b>${when(x.time)}</b><div class="muted small">${esc(x.name)}${x.renderdoc ? " · RenderDoc" : ""}${x.runs.length > 1 ? ` · расчётов: ${x.runs.length}` : ""}</div></td>
      <td>${esc(x.project || "—")}</td><td class="n">${x.draws}</td>
      <td class="n">${x.total ? `<b>${M(x.total)}</b><div class="muted small">${esc(x.main_core)}</div>` : `<span class="muted">не посчитан</span>`}</td>
      <td>${x.total ? `<div class="split" style="width:140px"><i style="width:${100 * f}%;background:var(--part-px)"></i><i style="width:${100 * v}%;background:var(--part-vx)"></i></div><span class="muted small">${pct(f)} / ${pct(v)}</span>` : ""}</td>
      <td style="white-space:nowrap;text-align:right">${x.report ? `<button class="btn sm primary" data-a="open" data-i="${i}">Открыть</button> ` : ""}
        <button class="btn sm" data-a="recalc" data-i="${i}">${x.report ? "Пересчитать" : "Посчитать"}</button>
        ${x.runs.length ? `<button class="btn sm" data-a="cmp" data-i="${i}">Сравнить</button>` : ""}
        <button class="btn sm" data-a="folder" data-i="${i}">Папка</button>
        <button class="btn sm danger" data-a="del" data-i="${i}" title="Удалить">✕</button></td></tr>`;
    }).join("") + `</table></div>`;
  document.querySelectorAll("#repList [data-a]").forEach(b => b.onclick = async () => {
    const x = reps[+b.dataset.i];
    if (b.dataset.a === "open") openReport(x);
    if (b.dataset.a === "recalc") { setRecalc(x); tab("run"); }
    if (b.dataset.a === "cmp") compareSnapshot(x, reps);
    if (b.dataset.a === "folder") api("/api/reveal", {name: x.name});
    if (b.dataset.a === "del" && confirm(`Удалить снимок ${x.name} со всеми файлами (отчёт, захват RenderDoc)?`)) {
      const d = await api("/api/delete", {name: x.name});
      if (d.error) alert(d.error);
      OPT = await api("/api/options"); loadReports();
    }
  });
  const picks = () => [...document.querySelectorAll(".pick:checked")].map(c => reps[+c.dataset.i]);
  const sel = () => {
    const p = picks();
    $("#selBar").innerHTML = p.length === 2
      ? `<button class="btn primary sm" id="cmpPicked">Сравнить выбранные</button><span class="muted">A — более ранний снимок, B — более поздний (последние расчёты)</span>`
      : p.length ? `<span class="muted">Отметьте ещё один снимок.</span>` : "";
    if (p.length === 2) $("#cmpPicked").onclick = () => {
      const [a, b] = p.sort((u, v) => u.time - v.time);
      openCompare(`${a.name}/${a.runs.at(-1)}`, `${b.name}/${b.runs.at(-1)}`);
    };
  };
  document.querySelectorAll(".pick").forEach(c => c.onchange = () => {
    const p = picks();
    if (p.length > 2) c.checked = false;
    sel();
  });
}

function compareSnapshot(x, reps) {
  // the snapshot priced again: its last two runs; else its last run against the previous snapshot of the project
  const b = `${x.name}/${x.runs.at(-1)}`;
  if (x.runs.length > 1) return openCompare(`${x.name}/${x.runs.at(-2)}`, b);
  const prev = reps.filter(r => r.runs.length && r.name !== x.name && r.time < x.time && (!x.project || r.project === x.project))
    .sort((u, v) => v.time - u.time)[0];
  if (prev) return openCompare(`${prev.name}/${prev.runs.at(-1)}`, b);
  openCompare(null, b);
}

/* ---------- compare ---------- */
