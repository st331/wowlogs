#!/usr/bin/env node
"use strict";
/* tests/js_oracle.js -- run the REAL client script under node.
 *
 *   node tests/js_oracle.js <site/index.html> <payload.json> <states.json> <now_ms> [out.json]
 *
 * The main <script> of index.html is evaluated verbatim inside a vm context
 * whose DOM is a recording stub (every element accepts any property and
 * remembers what was written to it). Date is frozen at <now_ms>, fetch never
 * resolves (so loadSeason() idles) and initData() is called by hand with the
 * payload. The DOM-only functions (buildControls, renderLabCards, ...) are
 * replaced AFTER evaluation by no-ops: they are global function bindings, so
 * the reassignment is what the client's own code then calls. Nothing that
 * computes a number is replaced -- render() itself runs for every state, and
 * the outputs are read back from the globals the client publishes
 * (CHART_KEYS, lastEffMin, FRAME_A/FRAME_B, __compRows, __compPresence,
 * __pulse, __setRows) plus one capture hook on renderTrendGrid.
 *
 * test_sitecalc_matches_js.py compares the JSON this prints against
 * scripts/sitecalc.py for the same states. */
const fs = require("fs");
const vm = require("vm");

const [, , htmlPath, payloadPath, statesPath, nowArg, outPath] = process.argv;
if (!htmlPath || !payloadPath || !statesPath || !nowArg) {
  console.error("usage: js_oracle.js index.html payload.json states.json now_ms [out.json]");
  process.exit(2);
}
const NOW = Number(nowArg);

function extractMainScript(html) {
  const re = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[1].includes("const RESET_RULES") && m[1].includes("function aggregate(")) return m[1];
  }
  throw new Error("main script (RESET_RULES + aggregate) not found in " + htmlPath);
}

/* ---- recording DOM stub ------------------------------------------------ */
const ELEMENTS = new Map();
const NOOP = () => {};
function styleObj() { return new Proxy({}, { get: (t, p) => (p in t ? t[p] : ""), set: (t, p, v) => { t[p] = v; return true; } }); }
function makeEl(id) {
  const store = { id: id || "", dataset: {}, style: styleObj(), children: [],
                  classList: { add: NOOP, remove: NOOP, toggle: NOOP, contains: () => false },
                  innerHTML: "", textContent: "", value: "", title: "", className: "",
                  checked: false, disabled: false, hidden: false, max: "", min: "",
                  clientWidth: 900, clientHeight: 400, offsetWidth: 900, offsetHeight: 400,
                  scrollWidth: 900, scrollHeight: 400, scrollTop: 0, scrollLeft: 0 };
  const methods = {
    querySelectorAll: () => [], querySelector: () => makeEl(),
    getBoundingClientRect: () => ({ width: 900, height: 400, left: 0, top: 0, right: 900, bottom: 400, x: 0, y: 0 }),
    addEventListener: NOOP, removeEventListener: NOOP, setAttribute: NOOP, removeAttribute: NOOP,
    getAttribute: () => null, hasAttribute: () => false, insertAdjacentHTML: NOOP, append: NOOP,
    appendChild: NOOP, prepend: NOOP, removeChild: NOOP, replaceChildren: NOOP, focus: NOOP, blur: NOOP,
    scrollIntoView: NOOP, remove: NOOP, closest: () => null, contains: () => false, matches: () => false,
    click: NOOP, select: NOOP, dispatchEvent: NOOP, animate: () => ({ cancel: NOOP }),
    getContext: () => null, toggleAttribute: NOOP, cloneNode: () => makeEl(),
  };
  return new Proxy(store, {
    get(t, p) {
      if (p === Symbol.toPrimitive) return (hint) => (hint === "number" ? 0 : "");
      if (p in t) return t[p];
      if (p in methods) return methods[p];
      if (p === "parentElement" || p === "parentNode" || p === "firstElementChild" ||
          p === "lastElementChild" || p === "nextElementSibling" || p === "previousElementSibling" ||
          p === "offsetParent") return null;
      return undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}
const document = {
  getElementById(id) { if (!ELEMENTS.has(id)) ELEMENTS.set(id, makeEl(id)); return ELEMENTS.get(id); },
  querySelectorAll: () => [], querySelector: () => makeEl(), createElement: () => makeEl(),
  createTextNode: () => makeEl(), addEventListener: NOOP, removeEventListener: NOOP,
  body: makeEl("body"), documentElement: makeEl("html"), head: makeEl("head"),
  title: "", readyState: "complete", activeElement: null, hidden: false, visibilityState: "visible",
  createDocumentFragment: () => makeEl(), fonts: { ready: new Promise(() => {}) },
};
class FrozenDate extends Date {
  constructor(...a) { if (a.length === 0) super(NOW); else super(...a); }
  static now() { return NOW; }
}
class Obs { constructor() {} observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
const storage = new Map();
const sandbox = {
  document, console, Date: FrozenDate,
  localStorage: { getItem: (k) => (storage.has(k) ? storage.get(k) : null),
                  setItem: (k, v) => storage.set(k, String(v)), removeItem: (k) => storage.delete(k), clear: () => storage.clear() },
  sessionStorage: { getItem: () => null, setItem: NOOP, removeItem: NOOP },
  location: { hash: "", search: "", href: "http://localhost/", pathname: "/", origin: "http://localhost", host: "localhost", reload: NOOP },
  history: { replaceState: NOOP, pushState: NOOP },
  navigator: { userAgent: "node", clipboard: { writeText: () => Promise.resolve() }, language: "en-US" },
  fetch: () => new Promise(() => {}),
  setTimeout: () => 0, clearTimeout: NOOP, setInterval: () => 0, clearInterval: NOOP,
  requestAnimationFrame: () => 0, cancelAnimationFrame: NOOP, requestIdleCallback: () => 0,
  matchMedia: () => ({ matches: false, addEventListener: NOOP, removeEventListener: NOOP, addListener: NOOP }),
  IntersectionObserver: Obs, ResizeObserver: Obs, MutationObserver: Obs,
  Image: class { constructor() { this.style = styleObj(); } },
  URL, URLSearchParams, TextEncoder, TextDecoder, performance, structuredClone,
  Intl, Math, JSON, Map, Set, Number, String, Array, Object, Promise, Symbol, Error, TypeError, RangeError,
  Int8Array, Uint8Array, Int16Array, Uint16Array, Int32Array, Uint32Array, Float32Array, Float64Array,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, encodeURI, decodeURI,
  getComputedStyle: () => styleObj(), scrollTo: NOOP, scrollBy: NOOP, alert: NOOP, confirm: () => false,
  innerWidth: 1920, innerHeight: 1080, devicePixelRatio: 1, screen: { width: 1920, height: 1080 },
  addEventListener: NOOP, removeEventListener: NOOP, dispatchEvent: NOOP, open: NOOP,
  CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } },
  Event: class { constructor(t) { this.type = t; } },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

const html = fs.readFileSync(htmlPath, "utf8");
const script = extractMainScript(html);
vm.runInContext(script, sandbox, { filename: "index.html<main>" });

/* ---- neutralise the DOM-only functions; capture the trend grid --------- */
vm.runInContext(`
  buildControls = function(){};
  renderLabCards = function(){};
  labScopeRefresh = function(){};
  archonTitle = function(){ return ""; };
  setCompare = function(on){ state.compare = on; };
  csRestoreFromHash = function(){};
  syncCompSpecUI = function(){};
  globalThis.__trendCapture = null;
  renderTrendGrid = function(box, series, weeks, ymin, ymax, mt, vfmt, inv){
    globalThis.__trendCapture = {series, weeks, ymin, ymax};
  };
  globalThis.__effCalls = [];
  const __eff0 = effMinFor;
  effMinFor = function(pool){ const v = __eff0(pool); globalThis.__effCalls.push([pool, v]); return v; };
`, sandbox, { filename: "oracle-stubs" });

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
const states = JSON.parse(fs.readFileSync(statesPath, "utf8"));
sandbox.__payload = payload;
vm.runInContext(`initData(__payload, {file:"data.json", sub:"", foot:"", minchars:250, maxchars:1200});
  // the per-source defaults initData just set: every state starts from them
  globalThis.__baseState = {};
  for (const k of Object.keys(state)) __baseState[k] = state[k] instanceof Set ? new Set(state[k]) : state[k];`,
  sandbox, { filename: "oracle-init" });

/* ---- per-state driver, inside the context ------------------------------ */
const driver = vm.runInContext(`(function(spec){
  const SETS = ["cls","spec","hero","dun","role","reg","weeksA","weeksB"];
  for (const k of Object.keys(__baseState)) state[k] = __baseState[k] instanceof Set ? new Set(__baseState[k]) : __baseState[k];
  for (const k of Object.keys(spec)) {
    state[k] = SETS.includes(k) ? new Set(spec[k]) : spec[k];
  }
  refMemo = new Map();
  window.__setRows = []; window.__compRows = []; window.__compPresence = {den:0, map:new Map()};
  globalThis.__trendCapture = null; globalThis.__effCalls = [];
  __pulse = new Map();
  render();
  const grp = A => A ? [...A.groups.entries()].map(([k,g]) => [k, {
      n:g.n, avg:g.avg, med:g.med, q30:g.q30, q85:g.q85, qb:g.qb,
      qdA:("qdA" in g)?g.qdA:null, qdB:("qdB" in g)?g.qdB:null,
      adeaths:g.adeaths, deathless:g.deathless, chars:g.chars, runs:g.runs,
      arating:g.arating, mrating:g.mrating, rn:g.rn, ravg:g.ravg, rmed:g.rmed,
      cls:g.cls, spec:g.spec, hero:g.hero, floorK:("floorK" in g)?g.floorK:null}]) : null;
  const agg = A => A ? {parses:A.parses, runs:A.runs, chars:A.chars, dmin:A.dmin, dmax:A.dmax, groups:grp(A)} : null;
  const tc = globalThis.__trendCapture;
  const trendMin = globalThis.__effCalls.length ? globalThis.__effCalls[globalThis.__effCalls.length-1] : null;
  return {
    A: agg(FRAME_A), B: agg(FRAME_B),
    effMin: lastEffMin, CHART_KEYS: [...CHART_KEYS], eliteHidden,
    weekCounts: [...weekCounts.entries()], availWeeks:[...availWeeks], usB0, curMinDay, curMaxDay, runCount,
    comps: {rowsAll: window.__compRows.map(r => ({strength:r.strength, best:r.best, median:r.median,
              avgkey:r.avgkey, n:r.n, kdur:r.kdur, dun:r.dun, key:r.key, deaths:r.deaths, day:r.day,
              comp:r.comp.map(c => [c.cls, c.spec, c.role])})),
            presence: {den: window.__compPresence.den, map: [...window.__compPresence.map.entries()]}},
    pulse: [...__pulse.values()].map(e => ({key:e.key, dps:e.dps, prevMed:e.prevMed, thin:e.thin,
              nNow:e.nNow, nPrev:e.nPrev, adeaths:e.adeaths, delta:e.delta, isNew:e.isNew,
              pres:e.pres, spark:e.spark, rn:e.rn, rp:e.rp===undefined?null:e.rp})),
    trend: tc ? {buckets: tc.weeks, ymin: tc.ymin, ymax: tc.ymax, trendMin: trendMin,
              series: tc.series.map(s => ({key:s.key, name:s.name, pts:s.pts.map(p => ({xi:p.xi, b:p.b, v:p.v, n:p.n}))}))}
             : {buckets: [], series: [], trendMin: trendMin},
    setbonus: window.__setRows.map(r => ({cls:r.cls, spec:r.spec, hero:r.hero, n0:r.n0, n2:r.n2, n4:r.n4,
              tot:r.tot, s0:r.s0, s2:r.s2, s4:r.s4, m0:r.m0, m2:r.m2, m4:r.m4, p2:r.p2, p4:r.p4, pt:r.pt, cells:r.cells})),
  };
})`, sandbox, { filename: "oracle-driver" });

const results = [];
for (let i = 0; i < states.length; i++) {
  try {
    results.push(driver(states[i]));
  } catch (e) {
    results.push({ error: String(e && e.stack || e), state: states[i] });
  }
}
const out = JSON.stringify({ now: NOW, n: results.length, results });
if (outPath) fs.writeFileSync(outPath, out); else process.stdout.write(out);
