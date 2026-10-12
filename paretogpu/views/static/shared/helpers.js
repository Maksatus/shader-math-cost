"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const num = x => x == null ? "—" : Math.round(x).toLocaleString("ru-RU");
const M = (x, digits) => x == null ? "—" : (x / 1e6).toFixed(digits ?? (Math.abs(x) < 1e7 ? 2 : 1));
const when = (t, year = false) => t ? new Date(t * 1000).toLocaleString("ru-RU",
  {day: "2-digit", month: "2-digit", ...(year ? {year: "2-digit"} : {}), hour: "2-digit", minute: "2-digit"}) : "—";
