"use strict";
function openCompare(a, b) {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x.dataset.tab === "compare"));
  document.querySelectorAll("section").forEach(x => x.classList.toggle("on", x.id === "tab-compare"));
  store.set("tab", "compare");
  cmpMode = "frames"; store.set("compareMode", "frames");
  document.querySelectorAll("#cmpMode button").forEach(b => b.classList.toggle("on", b.dataset.m === "frames"));
  $("#tab-compare").classList.remove("matmode");
  loadCompare(a, b);
}

function openReport(x, which) {
  curRep = x;
  tab("reports");
  $("#repList").style.display = "none";
  $("#repView").style.display = "flex";
  $("#repName").textContent = x.name;
  showRep(which || "cost");
}
function showRep(which) {
  const x = curRep;
  document.querySelectorAll("#repWhich button").forEach(b => b.classList.toggle("on", b.dataset.w === which));
  const url = (x.pages || {})[which];
  if (url) {
    $("#repFrame").src = url;
    $("#repOpen").href = url;
    return;
  }
  if (which === "hotspots") {
    if (confirm("Разобрать 10 самых дорогих шейдеров этого кадра по строкам?\n\nКаждая строка их кода — отдельный замер malioc; обычно до минуты. Ход будет виден на вкладке «Проверка кадра»."))
      start("hotspots", {frame: x.path || (OPT.snapshots.find(s => s.name === x.name) || {}).path});
    else showRep("cost");
  }
}
document.querySelectorAll("#repWhich button").forEach(b => b.onclick = () => curRep && showRep(b.dataset.w));
$("#repBack").onclick = () => { $("#repView").style.display = "none"; $("#repList").style.display = ""; $("#repFrame").src = "about:blank"; loadReports(); };
$("#repFolder").onclick = () => curRep && api("/api/reveal", curRep.path ? {path: curRep.path} : {name: curRep.name});
$("#repRecalc").onclick = () => {
  if (!curRep) return;
  const s = OPT.snapshots.find(x => x.name === curRep.name) || {};
  setRecalc({name: curRep.name, path: curRep.path || s.path, project: curRep.project || s.project});
  tab("run");
};
