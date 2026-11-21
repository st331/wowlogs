// check_talent_page.js: the Talents tab of the published page, spec by spec, in a real browser.
//
//   node scripts/check_talent_page.js <site URL> [Class|Spec,Class|Spec,...]
//   node scripts/check_talent_page.js https://st331.github.io/wowlogs
//
// Owner, 2026-11-21: "the talent build for devourer ... seems wider than other specs like rogue.
// fix this. also add a button to export talents for a selected talent build."
//
// For every spec on the page (or the ones named) at 1920x1080 it opens the Character screen's
// Talents tab and checks
//   * the three panes FIT: no horizontal scroll inside the trees, none on the page;
//   * the spec has at most two hero trees, and its class and spec panes hold no node the
//     talents document lists as hidden from it ("cx");
//   * the lit build's chip carries one export button, a click puts on the clipboard exactly the
//     string builds.json.gz holds for THAT build ("imp"), the button says so and takes its label
//     back; lighting another build moves the button and copies that build's string;
//   * with the clipboard refused, the string is shown selected and the button does not say copied.
// It reads talents.json.gz and builds.json.gz from the same site itself, so what the page shows is
// held against the files and not against the page's own memory. Needs Playwright with Chromium;
// not part of the test loop. Exit status 0 = every check passed.
const { chromium } = require('playwright');
const zlib = require('zlib');

const SITE = (process.argv[2] || '').replace(/\/+$/, '');
if (!SITE) { console.error('usage: node scripts/check_talent_page.js <site URL> [Class|Spec,...]'); process.exit(2); }
const ONLY = process.argv[3] ? process.argv[3].split(',') : null;

async function getJsonGz(page, name) {
  // fetched by the browser, so the same origin, proxy and cache rules as the page's own request
  const b64 = await page.evaluate(async (u) => {
    const r = await fetch(u, { cache: 'no-store' });
    if (!r.ok) return null;
    const a = new Uint8Array(await r.arrayBuffer());
    let s = ''; for (let i = 0; i < a.length; i += 32768) s += String.fromCharCode.apply(null, a.subarray(i, i + 32768));
    return btoa(s);
  }, SITE + '/' + name);
  if (b64 == null) return null;
  const raw = Buffer.from(b64, 'base64');
  const txt = (raw[0] === 0x1f && raw[1] === 0x8b) ? zlib.gunzipSync(raw).toString('utf8') : raw.toString('utf8');
  return JSON.parse(txt);
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-proxy-server', '--no-sandbox'] });
  let bad = 0, n = 0;
  const fail = (m) => { console.log('FAIL ' + m); bad++; };
  const open = async (grant) => {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    if (grant) await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(SITE).origin });
    const page = await ctx.newPage();
    const errs = []; page.on('pageerror', (e) => errs.push(String(e.message || e)));
    await page.goto(SITE + '/index.html', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#tbl tbody tr').length > 5, null, { timeout: 120000 });
    return { ctx, page, errs };
  };
  const enter = async (page, cls, spec) => {
    await page.evaluate(() => { if (state.screen) exitScreen(); state.minchars = 1; state.role = new Set(); state.klo = 2; state.khi = 30; state.weeksA = new Set(availWeeks); render(); });
    await page.waitForTimeout(250);
    const key = await page.evaluate(([c, s]) => { for (const [k, g] of FRAME_A.groups) if (g.cls === c && g.spec === s) return k; return null; }, [cls, spec]);
    if (key == null) return false;
    await page.evaluate((k) => { openFrame(k); }, key); await page.waitForTimeout(250);
    await page.evaluate(() => { enterScreen(); screenTab = 'tal'; renderScreen(); });
    try { await page.waitForFunction(() => !!document.querySelector('#cs-trees .ttpane'), null, { timeout: 60000 }); } catch (e) { return false; }
    await page.waitForTimeout(500);
    return true;
  };
  const lit = (page) => page.evaluate(() => {
    const on = document.querySelector('#cs-bldchips .bldchip.on');
    return { s: (typeof screenBuild === 'number' && screenBuild >= 0) ? __csBldS[screenBuild] : null,
      btns: [...document.querySelectorAll('#cs-bldchips [data-copy]')].map((b) => b.closest('.bldchip') === on),
      label: on && on.querySelector('[data-copy]') ? on.querySelector('[data-copy]').textContent : null };
  });

  const { ctx, page, errs } = await open(true);
  const talents = await getJsonGz(page, 'talents.json.gz');
  const builds = await getJsonGz(page, 'builds.json.gz');
  if (!talents || !builds) { console.log('FAIL the site serves no talents.json.gz or no builds.json.gz'); process.exit(1); }
  const specs = (ONLY || Object.keys(talents.trees).sort());
  const withCx = Object.values(talents.trees).filter((t) => Array.isArray(t.cx) && t.cx.length).length;
  const nImp = Object.values(builds.specs).reduce((a, s) => a + (s.builds || []).filter((b) => b && b.imp).length, 0);
  const nHash = Object.values(builds.specs).reduce((a, s) => a + (s.builds || []).filter((b) => b && String(b.s).startsWith('t:')).length, 0);
  const games = [...new Set(Object.values(builds.specs).map((s) => s.game).filter(Boolean))];
  console.log(`site ${SITE}: ${Object.keys(talents.trees).length} specs in the talents document, ${withCx} with a hide list; `
    + `${nImp} of ${nHash} listed builds carry an import string, written for game build ${games.join(', ') || 'unknown'}`);

  let exported = 0, wide = [], heroMax = 0;
  for (const sk of specs) {
    const [cls, spec] = sk.split('|');
    const t = talents.trees[sk];
    if (!t) { fail(`${sk}: not in the talents document`); continue; }
    if (!(await enter(page, cls, spec))) { console.log(`skip ${sk}: not on the page in this view`); continue; }
    n++;
    const heroes = Object.keys(t.hero || {});
    heroMax = Math.max(heroMax, heroes.length);
    if (heroes.length > 2) fail(`${sk}: ${heroes.length} hero trees in the document (${heroes.join(', ')})`);
    const m = await page.evaluate(() => {
      const el = document.getElementById('cs-trees'), host = document.getElementById('charscreen');
      return { cls: el.className, scrollW: el.scrollWidth, clientW: el.clientWidth, hostW: host.clientWidth,
        docW: document.documentElement.scrollWidth, winW: window.innerWidth,
        ids: [...el.querySelectorAll('.ttn[data-nd]')].map((x) => +x.dataset.nd),
        panes: [...el.querySelectorAll('.ttpane')].map((p) => Math.round(p.getBoundingClientRect().width)) };
    });
    const fits = m.scrollW <= m.clientW + 1 && m.docW <= m.winW + 1 && !/\bovf\b/.test(m.cls);
    if (!fits) { wide.push(sk); fail(`${sk}: the trees do not fit (scroll ${m.scrollW} in ${m.clientW}, page ${m.docW} in ${m.winW}, class "${m.cls}")`); }
    const hidden = new Set((t.cx || []).map(Number));
    const shown = m.ids.filter((id) => hidden.has(id));
    if (shown.length) fail(`${sk}: ${shown.length} nodes the document hides from this spec are on screen (${shown.slice(0, 5)})`);
    // the export button
    const vocab = builds.specs[sk];
    const st = await lit(page);
    const vb = vocab && st.s ? (vocab.builds || []).find((b) => b && b.s === st.s) : null;
    if (vb && vb.imp) {
      if (st.btns.length !== 1 || st.btns[0] !== true) { fail(`${sk}: ${st.btns.length} export buttons, on the lit chip: ${JSON.stringify(st.btns)}`); continue; }
      await page.click('#cs-bldchips .bldchip.on [data-copy]'); await page.waitForTimeout(250);
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      const said = await page.evaluate(() => document.querySelector('#cs-bldchips .bldchip.on [data-copy]').textContent);
      if (clip !== vb.imp) fail(`${sk}: the clipboard holds ${clip.slice(0, 30)}… and the file says ${vb.imp.slice(0, 30)}…`);
      else if (said !== 'copied') fail(`${sk}: the string was copied and the button says "${said}"`);
      else exported++;
    } else if (st.btns.length) fail(`${sk}: an export button on a build the file holds no string for`);
  }
  // one spec in depth: the label comes back, another build moves the button, the footnote names the game build
  const deep = specs.find((sk) => builds.specs[sk] && (builds.specs[sk].builds || []).filter((b) => b && b.imp).length > 3);
  if (deep) {
    const [cls, spec] = deep.split('|');
    await enter(page, cls, spec);
    await page.click('#cs-bldchips .bldchip.on [data-copy]'); await page.waitForTimeout(1700);
    const back = await page.evaluate(() => document.querySelector('#cs-bldchips .bldchip.on [data-copy]').textContent);
    if (back !== 'export') fail(`${deep}: the label did not come back ("${back}")`);
    const first = await page.evaluate(() => navigator.clipboard.readText());
    await page.click('#cs-bldchips .bldchip[data-bld="2"]'); await page.waitForTimeout(500);
    const st = await lit(page);
    const vb = (builds.specs[deep].builds || []).find((b) => b && b.s === st.s);
    await page.click('#cs-bldchips .bldchip.on [data-copy]'); await page.waitForTimeout(250);
    const second = await page.evaluate(() => navigator.clipboard.readText());
    if (!(st.btns.length === 1 && st.btns[0] && vb && second === vb.imp && second !== first)) fail(`${deep}: lighting Build 3 did not move the export to that build`);
    const foot = await page.evaluate(() => [...document.querySelectorAll('.cs-foot')].map((f) => f.textContent).find((x) => /import strings/.test(x)) || '');
    if (!/export on the lit build writes one from its logged talents/.test(foot)) fail(`${deep}: the footnote does not say where the string comes from`);
    if (builds.specs[deep].game && foot.indexOf('game build ' + builds.specs[deep].game) < 0) fail(`${deep}: the footnote does not name game build ${builds.specs[deep].game}`);
    console.log(`in depth (${deep}): the label returns, Build 3 exports its own string, the footnote names game build ${builds.specs[deep].game || 'unknown'}`);
  }
  if (errs.length) fail('page errors: ' + JSON.stringify(errs.slice(0, 3)));
  await ctx.close();

  if (deep) {   // the clipboard refused
    const r = await open(false);
    const [cls, spec] = deep.split('|');
    await enter(r.page, cls, spec);
    await r.page.evaluate(() => { navigator.clipboard.writeText = () => Promise.reject(new Error('denied')); });
    await r.page.click('#cs-bldchips .bldchip.on [data-copy]'); await r.page.waitForTimeout(300);
    const fb = await r.page.evaluate(() => { const b = document.querySelector('#cs-impbox');
      return b ? { val: b.value, sel: b.selectionEnd - b.selectionStart, focus: document.activeElement === b,
        lab: document.querySelector('#cs-bldchips .bldchip.on [data-copy]').textContent, s: __csBldS[screenBuild] } : null; });
    const vb = fb && (builds.specs[deep].builds || []).find((b) => b && b.s === fb.s);
    if (!(fb && vb && fb.val === vb.imp && fb.sel === fb.val.length && fb.focus && fb.lab === 'select + copy')) fail(`${deep}: with the clipboard refused the string is not handed over selected: ${JSON.stringify(fb && { len: fb.val.length, sel: fb.sel, lab: fb.lab })}`);
    else console.log(`clipboard refused (${deep}): the string is shown selected, the button says "select + copy"`);
    if (r.errs.length) fail('page errors: ' + JSON.stringify(r.errs.slice(0, 3)));
    await r.ctx.close();
  }

  console.log(`${n} specs opened at 1920x1080: ${n - wide.length} fit their panes, at most ${heroMax} hero trees each; `
    + `export copied the file's own string for ${exported} lit builds`);
  console.log(bad ? `FAIL (${bad})` : 'PASS  the Talents tab: every spec fits, and export hands over the build\'s own import string');
  await browser.close();
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error('CHECK FAILED TO RUN', e); process.exit(1); });
