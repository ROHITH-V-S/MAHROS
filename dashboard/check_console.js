/**
 * Structural check for the live console page.
 *
 * The console cannot be fully exercised without a running server, so this
 * checks the things that break silently on their own: a script that does not
 * parse, a selector the JS depends on that has no matching element, a CSS
 * token used but never defined, a colour that only exists in one theme.
 *
 *     node dashboard/check_console.js
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { JSDOM, VirtualConsole } = require("jsdom");

const FILE = path.join(__dirname, "..", "mahros", "server", "static", "console.html");
const html = fs.readFileSync(FILE, "utf8");

let failures = 0;
const ok  = (m) => console.log(`  \x1b[32mPASS\x1b[0m  ${m}`);
const bad = (m) => { failures++; console.log(`  \x1b[31mFAIL\x1b[0m  ${m}`); };
const head = (m) => console.log(`\n\x1b[1m${m}\x1b[0m`);

/* ------------------------------------------------------------ script */
head("Script");

const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
try {
  new vm.Script(script);
  ok(`page script parses (${(script.length / 1024).toFixed(0)} KB)`);
} catch (e) {
  bad(`syntax error: ${e.message}`);
  process.exit(1);
}

/* ------------------------------------------------------------ DOM */
head("DOM contract");

const vc = new VirtualConsole();
const dom = new JSDOM(html, { virtualConsole: vc });   // scripts NOT run: no server
const doc = dom.window.document;

// Every #id the script reaches for must exist in the markup, or be created by
// the script itself. Anything else is a silent null-dereference at runtime.
const CREATED = new Set(["beams", "bids", "cfp-line", "rankphase", "outcome"]);
const ids = new Set([...script.matchAll(/\$\("#([\w-]+)"\)/g)].map((m) => m[1]));
const missing = [...ids].filter((id) => !CREATED.has(id) && !doc.getElementById(id));
if (missing.length) bad(`script targets missing #id -> ${missing.join(", ")}`);
else ok(`all ${ids.size} script-referenced ids resolve`);

const required = [
  ["#map", "network map"],
  ["#theatre", "negotiation theatre"],
  ["#f-origin", "origin selector"],
  ["#f-resource", "resource selector"],
  ["#f-specialty", "specialty selector"],
  ["#f-acuity button", "acuity buttons"],
  ["#go", "request button"],
  ["#tamper", "forge-ledger button"],
  ["#f-fair", "fairness toggle"],
  ["#blocks", "ledger block list"],
  ["#burden", "burden bars"],
  ["#log", "event log"],
];
required.forEach(([sel, label]) => {
  const n = doc.querySelectorAll(sel).length;
  if (!n) bad(`missing ${label} (${sel})`);
});
ok(`all ${required.length} required controls present`);

const acuity = doc.querySelectorAll("#f-acuity button");
if (acuity.length !== 4) bad(`expected 4 acuity buttons, found ${acuity.length}`);
else ok("4 acuity levels offered");
if ([...acuity].filter((b) => b.getAttribute("aria-pressed") === "true").length !== 1)
  bad("acuity buttons must have exactly one pressed by default");
else ok("exactly one acuity preselected");

/* ------------------------------------------------------------ CSS */
head("CSS tokens");

const style = html.match(/<style>([\s\S]*?)<\/style>/)[1];
const rootBlock = style.match(/:root\s*\{([\s\S]*?)\n\}/)[1];
const defined = new Set([...rootBlock.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
const referenced = new Set([...html.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
const undef = [...referenced].filter((t) => !defined.has(t));
if (undef.length) bad(`tokens referenced but never defined -> ${undef.join(", ")}`);
else ok(`all ${referenced.size} referenced tokens defined on :root`);

const media = style.match(/@media \(prefers-color-scheme: dark\)\s*\{([\s\S]*?)\n\}/);
const stamp = style.match(/:root\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/);
if (!media) bad("no prefers-color-scheme dark block");
else if (!/:root:not\(\[data-theme="light"\]\)/.test(media[1]))
  bad("dark media query not guarded against an explicit light choice");
else ok("dark media query guarded");
if (!stamp) bad("no :root[data-theme='dark'] block");
else ok("explicit dark stamp present");

if (media && stamp) {
  const a = new Set([...media[1].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const b = new Set([...stamp[1].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const drift = [...a].filter((t) => !b.has(t)).concat([...b].filter((t) => !a.has(t)));
  if (drift.length) bad(`dark blocks disagree -> ${drift.join(", ")}`);
  else ok(`both dark blocks redefine the same ${a.size} tokens`);
}
if (/body\s*\{[^}]*background:var\(--/.test(style.replace(/\s/g, (m) => m === "\n" ? "\n" : m)))
  ok("body paints a token background");
else if (/body\{[^}]*background:var\(--/.test(style.replace(/\s+/g, "")))
  ok("body paints a token background");
else bad("body has no token background");

/* ------------------------------------------------------------ self-containment */
head("Self-containment");
const ext = [...html.matchAll(/(?:src|href)\s*=\s*["'](https?:)?\/\//g)];
if (ext.length) bad(`${ext.length} external resource reference(s)`);
else ok("no external resources — serves offline");

/* ------------------------------------------------------------ protocol contract */
head("Server contract");

const serverSrc = fs.readFileSync(
  path.join(__dirname, "..", "mahros", "server", "app.py"), "utf8");

// Event kinds the UI branches on must match what cnp.py actually emits.
const emitted = fs.readFileSync(
  path.join(__dirname, "..", "mahros", "negotiation", "cnp.py"), "utf8");
const emits = new Set([...emitted.matchAll(/_emit\("(\w+)"/g)].map((m) => m[1]));
const handled = new Set([...script.matchAll(/m\.kind\s*===\s*"(\w+)"/g)].map((m) => m[1]));

// The server consumes the terminal steps and re-sends them as dedicated
// `agreement` / `failed` messages, so the console does not branch on them.
// That filter must actually exist in app.py, or the outcome renders twice.
const TERMINAL = ["awarded", "failed"];
if (/if step\["kind"\] in \("awarded", "failed"\)/.test(serverSrc))
  ok("server filters terminal steps out of the raw stream");
else bad("server does not filter terminal steps — outcome will render twice");

const unhandled = [...emits].filter((k) => !handled.has(k) && !TERMINAL.includes(k));
if (unhandled.length) bad(`engine emits unhandled step kinds -> ${unhandled.join(", ")}`);
else ok(`console handles every non-terminal step kind (${[...emits].filter(k => !TERMINAL.includes(k)).join(", ")})`);

// The console must still handle the dedicated terminal message types.
const msgTypes = new Set([...script.matchAll(/case "(\w+)":/g)].map((m) => m[1]));
const missingMsg = ["state", "negotiation_start", "step", "agreement", "failed", "tamper"]
  .filter((t) => !msgTypes.has(t));
if (missingMsg.length) bad(`console ignores message types -> ${missingMsg.join(", ")}`);
else ok("console handles every message type the server sends");

const wsActions = new Set([...script.matchAll(/action:\s*"(\w+)"/g)].map((m) => m[1]));
const serverActions = new Set(
  [...serverSrc.matchAll(/action ==\s*"(\w+)"/g)].map((m) => m[1]));
const orphan = [...wsActions].filter((a) => !serverActions.has(a));
if (orphan.length) bad(`console sends actions the server ignores -> ${orphan.join(", ")}`);
else ok(`all ${wsActions.size} console actions handled by the server`);

console.log(`\n${failures === 0 ? "\x1b[32mALL CHECKS PASS" : "\x1b[31m" + failures + " FAILURE(S)"}\x1b[0m\n`);
process.exit(failures ? 1 : 0);
