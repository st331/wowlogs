#!/usr/bin/env node
// The figures the page PRINTS, checked against the payload: step 1 of 2 (the browser side).
//
//   node scripts/check_dn_oracle.js <site URL> <dump.json>
//   python3 scripts/check_dn_oracle.py <data.json.gz> <dump.json>
//
// Loads the published page, walks 19 views (lens, key range, hero split, time / gear / skill compare, two to
// seven dungeons, all roles, tanks, the Archon replica) and writes down, for every spec in each view, what the
// page computed: adjusted and logged average and percentiles, average key, dungeon mix, the flag, the
// there / elsewhere split, key floor and kept share. check_dn_oracle.py recomputes all of it from the payload
// and the rule in the README and compares. test_dn_math.py covers the factor maths without a browser; this
// pair covers what reads the factors (aggregate, aggregateElite, dnFields) and needs one.
//
// <site URL>: any place that serves the page with its data, e.g. https://st331.github.io/wowlogs or a local
// `python3 -m http.server` over a folder holding index.html and the published *.json.gz files.
// PAGE=<file>: serve this local index.html in place of the site's, to check a page before it is deployed.
// Needs Playwright with Chromium (PLAYWRIGHT=<module path> if it is not resolvable as "playwright").
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const [url, out] = process.argv.slice(2);
if (!url || !out) { console.error('usage: node scripts/check_dn_oracle.js <site URL> <dump.json>'); process.exit(2); }
const VIEWS = [
  ['default', ''],
  ['p85, keys 12-24, all runs, every reset', 'state.pctl=85;state.klo=12;state.khi=24;state.timedOnly=false;state.weeksA=new Set(availWeeks);'],
  ['hero talents split', 'state.merge=false;state.weeksA=new Set(availWeeks);'],
  ['time compare', 'state.weeksA=new Set([0]);state.weeksB=new Set([1]);state.compare=true;'],
  ['gear compare', 'GEAR'],
  ['skill compare', 'state.skill=true;state.pctlB=85;state.weeksA=new Set(availWeeks);'],
  ...[2, 3, 4, 5, 6, 7].map(k => [k + ' dungeons', 'state.dun=DU(' + k + ');state.weeksA=new Set(availWeeks);']),
  ['all roles, keys 2-24', 'state.role=new Set();state.klo=2;state.khi=24;state.timedOnly=false;state.weeksA=new Set(availWeeks);'],
  ['tanks, keys 13-19', 'state.role=new Set(["Tank"]);state.klo=13;state.khi=19;state.weeksA=new Set(availWeeks);'],
  ['p10 lens', 'state.pctl=10;state.weeksA=new Set(availWeeks);'],
  ['Archon replica', 'ARCHON'],
  ['Archon replica, 2 dungeons', 'ARCHON;state.dun=DU(2);'],
  ['Archon replica, 4 dungeons', 'ARCHON;state.dun=DU(4);'],
  ['Archon replica, one region', 'ARCHON;state.reg=new Set([D.regions[0]]);'],
];
(async () => {
  const browser = await chromium.launch({ args: (process.env.CHROMIUM_ARGS || '--no-proxy-server --no-sandbox').split(' ') });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errs = [];
  page.on('pageerror', e => { if (!String(e.stack || e).includes('zamimg')) errs.push(String(e.message || e)); });
  await page.route(/zamimg|googleapis|gstatic/, r => r.abort());
  if (process.env.PAGE) {
    const body = fs.readFileSync(process.env.PAGE);
    await page.route(u => /\/(index\.html)?(\?.*)?$/.test(u.pathname + u.search), r => r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body }));
  }
  await page.goto(url.replace(/\/?$/, '/') + 'index.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#tbl tbody tr').length > 5, null, { timeout: 120000 });
  const run = js => page.evaluate(js => { new Function('DU', js)(k => new Set(D.dungeons.slice(0, k))); }, js);
  const dump = await page.evaluate(() => ({ built: D.built, n: N, outw: [...Array(N).keys()].filter(i => R.rbucket[i] === OUTW),
    elite: { days: ELITE_DAYS, keep: ELITE_KEEP, pctl: ELITE_PCTL, min: ELITE_MIN }, views: [] }));
  for (const [name, js] of VIEWS) {
    if (await page.evaluate(() => state.elite)) { await page.evaluate(() => document.getElementById('archon').click()); await page.waitForTimeout(300); }
    await page.evaluate(() => { if (state.gear) setGear(false); if (state.skill) setSkill(false); if (state.compare) setCompare(false);
      Object.assign(state, { dnorm: true, pctl: 50, klo: DEF.klo, khi: DEF.khi, timedOnly: DEF.timedOnly, minchars: 1, dun: new Set(), reg: new Set(),
        role: new Set(['DPS']), weeksA: new Set(SCOPE_DEF.weeksA), weeksB: new Set([1]), compare: false, skill: false, merge: true, tab: 'med' }); render(); });
    if (js.startsWith('ARCHON')) { await page.evaluate(() => document.getElementById('archon').click()); await page.waitForTimeout(300); await run(js.slice(7) + ';render();'); }
    else if (js === 'GEAR') await page.evaluate(() => { state.weeksA = new Set(availWeeks); render(); setGear(true); });
    else await run(js + ';if(state.compare) setCompare(true); else if(state.skill) setSkill(true); else render();');
    await page.waitForTimeout(300);
    const d = await page.evaluate(name => {
      const o = { name, elite: state.elite, pctl: state.elite ? Math.round(ELITE_PCTL * 100) : state.pctl, pctlB: state.pctlB, merge: state.merge, dnOn: dnOn(),
        dungeons: dnView(), roles: [...state.role], regs: [...state.reg], sides: {} };
      // the rows in view are the page's own selection: this check is about the statistics over them
      const side = (agg, weeks, il) => {
        const s = { rows: [], groups: [] };
        if (!state.elite) { const m = baseMasks(il), cut = periodCut(weeks); for (let i = 0; i < N; i++) if (rowPass(i, m) && periodPass(i, weeks, cut) && !projSkip(i)) s.rows.push(i); }
        for (const [k, g] of agg.groups) s.groups.push({ k, cls: g.cls, spec: g.spec, hero: g.hero, n: g.n, avg: g.avg, med: g.med, qb: g.qb, q30: g.q30, q85: g.q85,
          lavg: g.lavg, lmed: g.lmed, adj: g.adj, akey: g.akey, dtop: g.dtop, dshare: g.dshare, din: g.din, dout: g.dout, nin: g.nin, nout: g.nout, hot: dmixHot(g),
          floorK: g.floorK, kshare: g.kshare, kpool: g.kpool });
        return s;
      };
      o.sides.A = side(FRAME_A, state.weeksA);
      if (FRAME_B) o.sides.B = side(FRAME_B, state.compare ? state.weeksB : state.weeksA, gearOn() ? { lo: state.ilob, hi: state.ihib } : undefined);
      return o;
    }, name);
    console.log(name + ': ' + Object.entries(d.sides).map(([s, v]) => s + ' ' + v.groups.length + ' specs' + (d.elite ? '' : ', ' + v.rows.length + ' rows')).join(' | '));
    dump.views.push(d);
  }
  fs.writeFileSync(out, JSON.stringify(dump));
  console.log('page errors: ' + JSON.stringify(errs) + ' | payload built ' + dump.built + ', ' + dump.n + ' rows -> ' + out);
  await browser.close();
  process.exit(errs.length ? 1 : 0);
})().catch(e => { console.error('CHECK FAILED TO RUN', e); process.exit(1); });
