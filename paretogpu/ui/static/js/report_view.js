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
  const url = (x.pages || {})[which || "cost"];
  if (url) {
    $("#repFrame").src = url;
    $("#repOpen").href = url;
  }
}
$("#repBack").onclick = () => { $("#repView").style.display = "none"; $("#repList").style.display = ""; $("#repFrame").src = "about:blank"; loadReports(); };
$("#repFolder").onclick = () => curRep && api("/api/reveal", curRep.path ? {path: curRep.path} : {name: curRep.name});
$("#repRecalc").onclick = () => {
  if (!curRep) return;
  const s = OPT.snapshots.find(x => x.name === curRep.name) || {};
  setRecalc({name: curRep.name, path: curRep.path || s.path, project: curRep.project || s.project});
  tab("run");
};
