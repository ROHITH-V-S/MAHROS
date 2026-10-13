/**
 * Drive the Hospital data tab in a headless DOM against a running MAHROS with
 * its database, the way a person would: open the tab, edit an occupancy,
 * Save, check the database changed, open History, try an over-capacity value,
 * restore everything.
 *
 *     docker compose up -d --build
 *     npm install jsdom            (once, anywhere; or set NODE_PATH)
 *     node tests/ui_hospital_tab.js http://localhost:8090
 *
 * Changes data and restores the real defaults at the end.
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const BASE = process.argv[2] || "http://localhost:8090";
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const html = await (await fetch(BASE + "/")).text();
  const vc = new VirtualConsole(); const errors = [];
  vc.on("jsdomError", e => errors.push(String(e.message || e)));
  vc.on("error", e => errors.push(String(e)));
  const dom = new JSDOM(html, { url: BASE + "/", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.WebSocket = class { constructor(){ setTimeout(()=>this.onopen&&this.onopen(),0);} send(){} close(){} }; } });
  const d = dom.window.document, $ = s => d.querySelector(s);
  await sleep(300);
  $("#actor").value = "ui-test"; $("#actor").dispatchEvent(new dom.window.Event("change"));
  [...d.querySelectorAll("#tabs button")].find(b => b.dataset.view === "data").click();
  await sleep(2500);
  const rows = d.querySelectorAll("#hd-table tr[data-id]");
  console.log("view visible:", !$("#view-data").hidden, "| hospital rows:", rows.length, "| offline card hidden:", $("#hd-offline").hidden);
  console.log("audit pill:", $("#hd-audit").textContent, "| ledger pill:", $("#hd-ledger").textContent);
  console.log("audit rows shown:", d.querySelectorAll("#au-table tr").length - 1, "| quality box has table:", !!$("#dq-box table"));
  const row = $('#hd-table tr[data-id="450068"]');
  const inputs = row.querySelectorAll("input[type=number]");
  console.log("Memorial Hermann pool inputs:", inputs.length, "| locked icons:", row.querySelectorAll(".lock").length);
  const icuInput = inputs[0]; const before = icuInput.value;
  icuInput.value = String(+before - 7); icuInput.dispatchEvent(new dom.window.Event("input"));
  console.log("row marked dirty:", row.className, "| save enabled:", !row.querySelector(".btn.primary").disabled);
  row.querySelector(".btn.primary").click(); await sleep(1500);
  const row2 = $('#hd-table tr[data-id="450068"]');
  console.log("after save msg:", row2.querySelector(".row-msg").textContent, "| edited badge:", !!row2.querySelector(".edited"));
  const api = await (await fetch(BASE + "/api/hospitals")).json();
  const icu = api.hospitals.find(h => h.hospital_id === "450068").pools.find(p => p.kind === "icu_bed");
  console.log("DB now:", icu.occupied, "(was", before + ")");
  const hist = [...row2.querySelectorAll("button")].find(b => b.textContent === "History"); hist.click(); await sleep(1500);
  const hrow = d.querySelector("#hd-table tr.hist");
  console.log("history panel:", !!hrow, "| first change:", hrow && hrow.querySelector(".hrow") && hrow.querySelector(".hrow").textContent.slice(0, 140));
  // over-capacity via UI
  const row3 = $('#hd-table tr[data-id="450068"]'); const inp3 = row3.querySelector("input[type=number]");
  inp3.value = "999"; inp3.dispatchEvent(new dom.window.Event("input")); row3.querySelector(".btn.primary").click(); await sleep(1200);
  console.log("over-capacity message:", $('#hd-table tr[data-id="450068"]').querySelector(".row-msg").textContent);
  // restore all (two clicks)
  $("#hd-restore-all").click(); console.log("armed label:", $("#hd-restore-all").textContent); $("#hd-restore-all").click(); await sleep(2000);
  console.log("restore msg:", $("#hd-msg").textContent.slice(0, 160));
  console.log("JS errors:", errors.length ? errors : "none");
  dom.window.close();
})();
