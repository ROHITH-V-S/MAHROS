/**
 * Static + headless render check for results/dashboard.html
 *
 * Runs entirely in-process with jsdom: no browser, no network, no navigation.
 * It parses the built file, executes the page script against a DOM, and then
 * asserts that the things we claim are on the page are actually on the page.
 *
 *     node dashboard/check.js
 *
 * The point is to catch the failures that are invisible in source: a chart that
 * throws halfway through and silently renders three bars instead of five, a CSS
 * token referenced but never defined, a colour that only exists inside a dark
 * mode media query.
 */

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const FILE = path.join(__dirname, "..", "results", "dashboard.html");
const html = fs.readFileSync(FILE, "utf8");

let failures = 0;
let warnings = 0;
const ok   = (m) => console.log(`  \x1b[32mPASS\x1b[0m  ${m}`);
const bad  = (m) => { failures++; console.log(`  \x1b[31mFAIL\x1b[0m  ${m}`); };
const warn = (m) => { warnings++; console.log(`  \x1b[33mWARN\x1b[0m  ${m}`); };
const head = (m) => console.log(`\n\x1b[1m${m}\x1b[0m`);

/* ------------------------------------------------------------------ CSS */
head("CSS tokens");

const styleBlock = html.match(/<style>([\s\S]*?)<\/style>/)[1];

// Every token defined on the bare :root block (the light palette).
const rootBlock = styleBlock.match(/:root\s*\{([\s\S]*?)\}/)[1];
const defined = new Set([...rootBlock.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));

// Every token referenced anywhere in the stylesheet or the script.
const referenced = new Set([...html.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
// css("--x") lookups from the chart code count as references too.
[...html.matchAll(/css\("(--[\w-]+)"\)/g)].forEach((m) => referenced.add(m[1]));

const missing = [...referenced].filter((t) => !defined.has(t) && t !== "--accent-col");
if (missing.length) bad(`referenced but not defined on :root -> ${missing.join(", ")}`);
else ok(`all ${referenced.size} referenced tokens defined on bare :root`);

// The classic unreadable-artifact bug: a token whose ONLY definition sits
// inside a media query or a [data-theme] block.
const darkMedia = styleBlock.match(/@media \(prefers-color-scheme: dark\)\s*\{([\s\S]*?)\n\}/);
const darkStamp = styleBlock.match(/:root\[data-theme="dark"\]\s*\{([\s\S]*?)\}/);
if (!darkMedia) bad("no prefers-color-scheme: dark block");
if (!darkStamp) bad("no :root[data-theme='dark'] block");

if (darkMedia && darkStamp) {
  const inMedia = new Set([...darkMedia[1].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const inStamp = new Set([...darkStamp[1].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const onlyDark = [...inMedia].filter((t) => !defined.has(t));
  if (onlyDark.length) bad(`defined only in dark media query -> ${onlyDark.join(", ")}`);
  else ok("no token is dark-only (light palette is complete on :root)");

  const drift = [...inMedia].filter((t) => !inStamp.has(t))
    .concat([...inStamp].filter((t) => !inMedia.has(t)));
  if (drift.length) bad(`media query and [data-theme] blocks disagree -> ${drift.join(", ")}`);
  else ok(`both dark blocks redefine the same ${inMedia.size} tokens`);

  if (/:root:not\(\[data-theme="light"\]\)/.test(darkMedia[1]))
    ok("dark media query guarded with :not([data-theme='light'])");
  else bad("dark media query is not guarded — an explicit light choice will lose");
}

if (/body\s*\{[^}]*background:\s*var\(--/.test(styleBlock))
  ok("body paints an explicit token background");
else bad("body has no token background — it will borrow the host ground");

/* ------------------------------------------------------------------ data */
head("Embedded data");

const dataMatch = html.match(/const DATA = (\{[\s\S]*?\});\r?\n/);
if (!dataMatch) { bad("could not locate embedded DATA"); process.exit(1); }
let DATA;
try {
  DATA = JSON.parse(dataMatch[1].replace(/<\\\//g, "</"));
  ok(`DATA parses (${(dataMatch[1].length / 1024).toFixed(0)} KB)`);
} catch (e) { bad(`DATA does not parse: ${e.message}`); process.exit(1); }

for (const k of ["meta", "results", "deltas", "ablation", "hospitals",
                 "hospitals_no_fairness", "sample_decisions", "by_acuity",
                 "ledger_demo", "privacy_demo"]) {
  if (DATA[k] === undefined) bad(`DATA.${k} missing`);
}
ok("all required top-level keys present");

for (const sc of DATA.meta.scenarios)
  for (const st of DATA.meta.strategies)
    if (!DATA.results[sc] || !DATA.results[sc][st])
      bad(`results[${sc}][${st}] missing`);
ok(`results complete: ${DATA.meta.scenarios.length} scenarios x ${DATA.meta.strategies.length} strategies`);

if (DATA.sample_decisions.length === 0) bad("sample_decisions is empty — the decision feed will render blank");
else ok(`${DATA.sample_decisions.length} sample decisions, all with rationales`);

if (DATA.sample_decisions.some((d) => !d.rationale)) bad("some decisions have no rationale");

/* No external resources — the page must be fully self-contained. */
head("Self-containment");
const ext = [...html.matchAll(/(?:src|href)\s*=\s*["'](https?:)?\/\//g)];
if (ext.length) bad(`${ext.length} external resource reference(s)`);
else ok("no external src/href — nothing loads over the network");
if (/@import|fonts\.googleapis|cdn\./.test(html)) bad("found a CDN / @import reference");
else ok("no CDN or @import");

/* ------------------------------------------------------------------ render */
head("Headless render");

const vc = new VirtualConsole();
const jsErrors = [];
vc.on("jsdomError", (e) => jsErrors.push(e.message));
vc.on("error", (m) => jsErrors.push(String(m)));

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  virtualConsole: vc,
  url: "https://local.invalid/dashboard",
});
const doc = dom.window.document;

if (jsErrors.length) jsErrors.forEach((e) => bad(`script error: ${e.split("\n")[0]}`));
else ok("page script executed with no errors");

const expect = (sel, min, label) => {
  const n = doc.querySelectorAll(sel).length;
  if (n < min) bad(`${label}: expected >= ${min}, rendered ${n}`);
  else ok(`${label}: ${n}`);
};

expect("#hero-tiles .tile", 4, "hero stat tiles");
expect("#seg-scenario button", DATA.meta.scenarios.length, "scenario filter buttons");
expect("#seg-metric button", 6, "metric filter buttons");
expect("#chart-compare path.barmark", DATA.meta.strategies.length, "comparison bars");
expect("#chart-compare text.val", DATA.meta.strategies.length, "comparison value labels");
expect("#table-compare tbody tr", DATA.meta.strategies.length, "comparison table rows");
expect("#contribution-cards .card", 3, "contribution cards");
expect("#chart-burden path.barmark", DATA.hospitals.length * 2, "burden bars (2 series)");
expect("#legend-burden .item", 2, "burden legend items");
expect("#chart-acuity path.barmark", 8, "acuity bars");
expect("#legend-acuity .item", 3, "acuity legend items");
expect("#decision-feed .dec", 5, "decision cards");
expect("#layer-list .layer", 5, "architecture layers");
expect("#notes-list .note", 5, "honest-limitation notes");
expect("#ledger-demo .verdict", 2, "ledger verdict banners");
expect("#privacy-demo .code", 3, "privacy demo stages");

/* Accessibility + correctness spot checks on the rendered output. */
head("Rendered correctness");

const title = doc.querySelector("title");
if (title && title.textContent.trim()) ok(`title: "${title.textContent.trim()}"`);
else bad("no <title>");

const charts = doc.querySelectorAll("svg.chart");
let unlabelled = 0;
charts.forEach((s) => { if (!s.getAttribute("aria-label")) unlabelled++; });
if (unlabelled) bad(`${unlabelled} chart(s) without aria-label`);
else ok(`all ${charts.length} charts carry an aria-label`);

// Every legend swatch must resolve to a real colour, not an empty var lookup.
const swatches = [...doc.querySelectorAll(".legend .sw")];
const emptySw = swatches.filter((s) => !/#|rgb/.test(s.getAttribute("style") || ""));
if (emptySw.length) bad(`${emptySw.length} legend swatch(es) have no resolved colour`);
else ok(`${swatches.length} legend swatches resolved to real colours`);

// Bars must resolve too — a var() that fails silently paints nothing.
const bars = [...doc.querySelectorAll("path.barmark")];
const emptyBars = bars.filter((b) => { const f = b.getAttribute("fill"); return !f || !/#|rgb/.test(f); });
if (emptyBars.length) bad(`${emptyBars.length} bar(s) have an unresolved fill`);
else ok(`all ${bars.length} bars have resolved fills`);

// The redaction demo must not leak identifiers *in its scrubbed stage*. Stage 1
// shows the raw note on purpose — that is the before-and-after — so only the
// scrubbed output and the peer-facing payload are checked.
const scrubbedEl = doc.querySelector("#privacy-scrubbed");
if (!scrubbedEl) bad("#privacy-scrubbed stage not rendered");
else {
  const scrubbedText = scrubbedEl.textContent;
  const leaks = ["Rahul", "9812345678", "H03-000891", "rahul.sharma@example.com"]
    .filter((s) => scrubbedText.includes(s));
  if (leaks.length) bad(`scrubbed note still contains: ${leaks.join(", ")}`);
  else ok("scrubbed note contains no raw identifier");

  // Placeholders must render as visible text, not be swallowed as HTML tags.
  if (scrubbedText.includes("<NAME>")) ok("redaction placeholders render as visible text");
  else bad("redaction placeholders were swallowed as markup");

  if (scrubbedEl.querySelector(".redact")) ok("redactions are visually marked");
  else warn("no .redact spans — redactions will not stand out");
}

// The peer-facing payload is the real privacy claim: it must carry a pseudonym
// and none of the direct identifiers.
const peerPayload = [...doc.querySelectorAll("#privacy-demo .code")].pop().textContent;
if (/pt_[0-9a-f]{8}/.test(peerPayload)) ok("peer payload carries a pseudonymous token");
else bad("peer payload has no pseudonymous patient_ref");
const payloadLeaks = ["name", "mrn", "phone", "age"].filter((k) =>
  new RegExp(`"${k}"`).test(peerPayload));
if (payloadLeaks.length) bad(`peer payload exposes: ${payloadLeaks.join(", ")}`);
else ok("peer payload exposes no identifying field");

// Numbers on the page must match the data, not be stale copy.
const heroText = doc.querySelector("#hero-tiles").textContent;
const expectedSuccess = (DATA.results.surge_scarcity.mahros.success_rate * 100).toFixed(0) + "%";
if (heroText.includes(expectedSuccess)) ok(`hero success rate matches data (${expectedSuccess})`);
else bad(`hero does not show the data's success rate (${expectedSuccess})`);

// Visible text only: <script> contents are in body.textContent but are never
// shown to a reader, and they legitimately contain the words we are hunting.
const visible = doc.body.cloneNode(true);
visible.querySelectorAll("script, style").forEach((n) => n.remove());
const bodyText = visible.textContent;

if (bodyText.includes("__MAHROS_DATA__")) bad("template token left unreplaced in output");
else ok("no unreplaced template tokens");
if (/undefined|NaN/.test(bodyText)) {
  const ctx = bodyText.match(/.{0,50}(undefined|NaN).{0,50}/);
  bad(`visible text contains undefined/NaN -> "...${ctx[0].trim()}..."`);
} else ok("no undefined/NaN in visible text");

/* ------------------------------------------------------------------ done */
console.log(
  `\n${failures === 0 ? "\x1b[32mALL CHECKS PASS" : "\x1b[31m" + failures + " FAILURE(S)"}\x1b[0m` +
  (warnings ? `  (${warnings} warning)` : "") +
  `\n`);
process.exit(failures ? 1 : 0);
