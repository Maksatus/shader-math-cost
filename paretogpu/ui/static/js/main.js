"use strict";
watchTips();
(async () => {
  SCH = await api("/api/schema"); SCHEMA = SCH.commands;
  OPT = await api("/api/options");
  $("#dl-cores").innerHTML = OPT.cores.filter(c => !c.startsWith("preset:")).map(c => `<option value="${esc(c)}">`).join("");
  projects(); loadSimple(); renderAdv(); checkEditor(); loadHistory(); poll(); loadEnv();
  tab(store.get("tab") || "run");
})();
