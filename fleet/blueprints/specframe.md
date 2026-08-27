# BLUEPRINT — Spec Frame "The Ledger Rail" (FINAL, build-ready, 2026-08-27)

Winning docked-rail design with all judge grafts folded in and all vetoes honoured.
Self-contained: a build agent needs only this file plus the live source
`/home/user/wowlogs/site/index.html` (3,392 lines; all line numbers below verified
against the working tree TODAY — the site already contains the shipped
"top comps for this spec" feature: `#compspec` select L615-616, `compSpec` state L754,
`syncCompSpecUI` L2069, `jumpToComps` L2081, `.complink` feeders L3356/L3378 and the
Pulse `csKey` at L2521).

Contract: `fleet/feature_specframe.md` (incl. the flask addition). Prefs:
`fleet/user_prefs.md`, `fleet/feedback_round2.md`. Skin: `fleet/design_language.md`
— **§GG governs** (the live file already carries §GG tokens: `--metal` L35,
`--metal-line` L36, `--gg-shadow` L37, Cinzel as `--font-display` L33).

**One sentence.** Clicking a bar docks a measure-aligned Gilded-Glass rail to the
viewport bottom showing that spec's identity numbers, its top comps, and its
character stats side by side — the first real implementation of design_language
§15.11's docked inspector, click-only, one computation shared with every existing
surface.

---

## 0. Non-negotiable gates (every judge veto, restated as build checks)

A build failing ANY of these fails review:

1. **No Cinzel / `var(--font-display)` anywhere in the frame.** Frame headers and
   block titles are Inter. Cinzel stays wordmark + section titles only.
2. **The frame's comps content is a real sortable mini-table** — all four columns,
   both directions, NaN parked last via the shared `cmpCells` (L2096), cap applied
   AFTER the sort. Never an unsortable "summary list".
3. **Compare must render inline in the frame.** Time compare ⇒ `A · B (Δ)` per row
   via the existing `deltaHTML` (L1980). Skill compare ⇒ p{lens} vs p{pctlB} on
   distribution rows only, honestly captioned. A bare "· period A" tag is not enough.
4. **No focus steal on open.** The rail is not a modal; focus moves only via the
   user's own Tab. `#frame-x` is reachable by Tab, never auto-focused.
5. **Zero layout shift of existing click targets.** Nothing resizes, reflows, or
   scrolls the chart on open/switch/close. The bar the user clicked never moves.
   (The `main` padding-bottom adjustment in §3 is below the fold and moves no
   clickable currently under the cursor.)
6. **No "A > B > C" priority prose.** No `>` arrows, no Archon sentence shape.
   Priority reads through p50-descending row order plus the bias footer only.
7. **Zero hover behavior on the frame.** The 450ms `#tip` (L311, untouched) owns
   hover. The frame adds no hover-preview, no hover chrome (this resolves §15.11's
   hover-preview clause against the standing tooltip contract — binding).
8. **Toasts are never repositioned to dodge the rail.** They stay `position:fixed`
   z-60 and transiently overlay it. Add this code comment at the frame CSS:
   `/* toasts (z:60) transiently overlay the rail by design — never repositioned */`
9. Standing bans: no fake histograms (we ship p25/p50/p75 — draw quantile ranges
   only); no rarity-color tinting, no purple, nothing Archon-shaped; no multi-pin;
   no persistence of `frameKey`/`frameFlask` across reloads; no dormant
   placeholders (absent specstats/flasks ⇒ block/chips simply not rendered);
   flask chips nowhere outside the frame's stats block; no outside-click close;
   no auto-open on load; **no changes to `aggregate()` (L1892), `renderComps`'
   math (L2810), the sidebar, section order, or the tooltip** beyond the one-line
   `__compRows` publish (§6) and the touchpoints in §12.
10. No rotation, radii 6/4, calm hover, centered bounded measure — all of
    design_language §1 and §GG's unchanged rules.

## 1. Gesture reconciliation (explicit decisions)

- Bar/label click (**L2300**: `bar.onclick=()=>jumpToRow(r.key); lspan.onclick=...`)
  is **superseded**: both become `()=>openFrame(r.key)`.
- The Data Table jump is **absorbed as a link**: the identity block's last line is
  `full row → Data Table`, calling the existing `jumpToRow(key)` (L2054; keeps the
  2s `.rowhl` highlight).
- The shipped comps slice (`#compspec` select, `.complink` feeders, `jumpToComps`
  L2081) is **kept untouched as the section's own deep view**; the frame's comps
  block links into it: `all K comps →` calls `jumpToComps(presKeyVal)` (opens the
  full sortable section slice with the select synced — the built feature, reused
  verbatim).
- Frame = glance + launcher; Top Comps section = full sortable table.
- `#overview-sub` copy (static default **L598** and the render-time string at
  **L2326**) becomes: `click any bar for its Spec Frame — comps and stats in one
  place`.
- Tooltip contract untouched: hover still means the 450ms `#tip` and nothing else
  (see gate 7). `openFrame` calls `hideTip()` first (same as `jumpToRow` does at
  L2055) so a pending tooltip never lingers over the rail.

## 2. Placement & markup

Insert after `</main>`, before `#tip` (L648 area):

```html
<div id="frame-pos"><div id="frame" role="region" aria-label="Spec frame" hidden>
  <div class="fhead">
    <span class="classdot" id="frame-dot"></span><b id="frame-name">Arcane Mage</b>
    <span id="frame-hero" class="fh"></span>
    <span id="frame-scope" class="fs"></span>
    <button id="frame-x" title="Close (Esc)">×</button>
  </div>
  <div id="frame-blocks"></div>   <!-- registry renders .fblock[data-block] children -->
</div></div>
```

```css
/* toasts (z:60) transiently overlay the rail by design — never repositioned */
#frame-pos{position:fixed; left:0; right:0; bottom:0; z-index:50; display:flex;
  justify-content:center; padding:0 42px; pointer-events:none}          /* §15.11 verbatim */
#frame{pointer-events:auto; width:100%; max-width:1116px; background:var(--surface2);
  border:1px solid var(--metal-line); border-bottom:0; border-radius:var(--r2) var(--r2) 0 0;
  padding:.7rem 1.1rem .85rem; font-size:.78rem; max-height:38vh; overflow-y:auto;
  box-shadow:0 -1px 2px rgba(0,0,0,.3), 0 -18px 40px -24px rgba(0,0,0,.7),
             inset 0 1px 0 rgba(255,255,255,.06)}                        /* §GG lip, upward */
@supports (backdrop-filter:blur(8px)) or (-webkit-backdrop-filter:blur(8px)){
  #frame{background:rgba(41,37,29,.88); backdrop-filter:blur(8px);
    -webkit-backdrop-filter:blur(8px)}}   /* same pattern as #lensbar L62 / #strip L212 / #tip L314 */
#frame.first{animation:frameIn .16s ease}  /* first-open only; class removed on animationend */
@keyframes frameIn{from{transform:translateY(8px); opacity:0}}
.fhead{display:flex; align-items:baseline; gap:.5rem; margin-bottom:.45rem}
.fhead b{font:600 .84rem var(--font); color:var(--ink)}   /* Inter — NEVER --font-display */
.fh{color:var(--ink3)} .fs{color:var(--ink3); font-size:.72rem; margin-left:auto}
#frame-x{background:none; border:0; color:var(--ink3); padding:.15rem .5rem;
  border-radius:var(--r1); cursor:pointer; font-size:.9rem}
#frame-x:hover{color:var(--ink); background:rgba(234,227,208,.07)}
#frame-blocks{display:flex; flex-wrap:wrap; gap:.9rem 2rem; align-items:flex-start}
.fblock{min-width:230px; flex:1}  .fblock[data-block="comps"]{flex:1.6}
.fblock .bt{font-size:.62rem; font-weight:700; text-transform:uppercase;
  letter-spacing:.15em; color:var(--ink3); margin-bottom:.35rem}  /* Inter mini-caps */
.fblock .foot{font-size:.7rem; color:var(--ink3); line-height:1.5; margin-top:.4rem}
.fblock table.data{min-width:0}          /* mini table: override the 700px table floor */
/* the pinned bar — static, click-driven, re-applied in the chart render loop */
.brow.framed{box-shadow:inset 2px 0 0 var(--metal-line);
  background:rgba(232,188,87,.05)}
.brow.framed .blbl{color:var(--ink)}
/* stat range bars (quantiles drawn as quantiles — never a histogram) */
.frange{position:relative; width:120px; height:8px; display:inline-block;
  vertical-align:middle; background:var(--line1); border-radius:2px}
.frange .fq{position:absolute; top:0; bottom:0; background:rgba(234,227,208,.14)}
.frange .fmed{position:absolute; top:-2px; bottom:-2px; width:2.5px;
  border-radius:1px; background:var(--tickref)}
@media(max-width:900px){#frame-pos{padding:0 18px}}
```

Notes: gold `--metal-line` border = §GG active accent (the frame is by definition
pinned/active). House scrollbar applies automatically. Z-order (verified):
strip 30 < lensbar 40 < popovers 41 < `#tip` 45 < frame 50 < toasts 60.

## 3. Open / switch / close, keyboard

- **Open**: `openFrame(groupKey)` → `hideTip()`; sets `state.frameKey` (new state
  field, display-only like `compSpec`: default `null`, never persisted, cleared on
  reload); `renderFrame()`; unhide. First-open only: add class `first`
  (translateY(8px)→0 + fade, 160ms ease — §15.11's one sanctioned slide) and
  **remove it on `animationend`** so switches can never re-slide even if
  open/close races (J2 graft). Focus is NOT stolen (gate 4).
- **Switch**: clicking any other bar (or arrow-stepping) swaps content
  **instantly, zero motion**. Clicking the same bar while its frame is open
  **closes** (§15.11: clicking the pinned trigger again unpins) — i.e.
  `openFrame(k)` first checks `if(state.frameKey===k){closeFrame(); return}`.
- **Close**: `#frame-x` or **Esc**. Esc handler (first in the file — verified: no
  existing `Escape` listeners) also carries the arrow-stepping graft:

```js
document.addEventListener("keydown",e=>{
  if(state.frameKey==null) return;
  if(e.key==="Escape"){ closeFrame(); return }
  if(e.key!=="ArrowUp"&&e.key!=="ArrowDown") return;
  // arrows guarded: never hijack keys inside form controls
  if(/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)||e.target.isContentEditable) return;
  const i=CHART_KEYS.indexOf(state.frameKey);
  if(i<0) return;                                   // framed spec not in current chart
  e.preventDefault();                               // swallow page scroll, ends included
  const j=Math.min(CHART_KEYS.length-1, Math.max(0, i+(e.key==="ArrowDown"?1:-1)));
  if(j!==i){ state.frameKey=CHART_KEYS[j]; renderFrame(); paintFramed(); }  // instant swap
});
```

  `CHART_KEYS` is a module-level array stashed in `render()` right after `view`
  is final (~L2214): `CHART_KEYS=view.map(r=>r.key)`. `paintFramed()` re-toggles
  `.framed` on the chart rows (or simply toggle in the row loop and on arrow-step
  walk the existing `.brow` nodes — implementation's choice; NO chart re-render).
- **No outside-click close** — popover convention stays with popovers; a pinned
  reading surface must survive sorting/scrolling the page above it.
- **Pinned-bar cue** (graft, both judges): in the chart render loop (~L2244) add
  `row.classList.toggle("framed", r.key===state.frameKey)` — static gilded inset
  edge per §2 CSS. Click-driven and static — calm-compliant.
- **Footer clearance**: while open, `main` gets
  `paddingBottom = (72 + frame.offsetHeight)+"px"`, recomputed once on
  open/switch/close **after** `renderFrame()` (state-driven, never scroll-driven);
  restored to `""` on close.
- `render()` calls `renderFrame()` **last** (append after `renderStrip(rows,A)` at
  L2334) — the compMin slider, sidebar filters, compare, lens, Archon mode, and
  merge toggle all refresh an open frame through that one path. `render()` also
  stashes `FRAME_A=A` and `FRAME_B=B` (module-level, like `COMPS_A` L2653) for
  `renderFrame` to read.

## 4. Block registry (extensibility)

```js
const FRAME_BLOCKS=[   // adding talents/trinkets later = append ONE entry; nothing else changes
  {id:"identity", has:ctx=>true,          html:ctx=>..., wire:(el,ctx)=>...},
  {id:"comps",    has:ctx=>true,          html:ctx=>..., wire:(el,ctx)=>...},
  {id:"stats",    has:ctx=>!!ctx.stats,   html:ctx=>..., wire:(el,ctx)=>...},
];
```

`renderFrame()` builds:

```js
const key=state.frameKey; if(key==null||!$("frame")) return;
const pk = state.merge ? key : Math.floor(key/200);        // ≡ Data Table presKey, L3306
const cls=D.classes[Math.floor(pk/100)], spec=D.specs[pk%100];
const hero=(!state.merge && FRAME_A.groups.get(key)) ? FRAME_A.groups.get(key).hero : "";
const ctx={key, presKey:pk, cls, spec, hero,
  g: FRAME_A ? (FRAME_A.groups.get(key)??null) : null,
  b: (state.compare&&FRAME_B) ? (FRAME_B.groups.get(key)??null) : null,
  stats: (window.D&&D.specstats&&D.specstats.specs)
           ? (D.specstats.specs[cls+"|"+spec]||null) : null,
  flask: state.frameFlask};
```

then concatenates `has(ctx)`-true blocks in order into `#frame-blocks` and runs
each `wire(el,ctx)`. `has()===false` ⇒ **the block does not exist** — flex
reflows, no placeholder, no ghost (feature-detect = the hasTier/hasRating
pattern; a future block's retirement is one array-entry deletion, zero residue).
Names are decoded from `pk`, not from `g`, so the header and comps/stats blocks
survive `g===null` (spec filtered out — §8).

Header: `#frame-dot` background = `CLASS_COLORS[cls]`; `#frame-name` =
`cls+" "+spec` (Inter, gate 1); `#frame-hero` = `· <hero>` only when merge off
and known; `#frame-scope` = the same scope fragments `scopeLine()` (L1688)
builds — `p{pctl} lens · <period> · keys +lo–+hi · timed only · <cohort>`, and
**"Archon replica"** when `state.elite` (identical wording source, no special
casing). Implement as a small `frameScope()` that reuses scopeLine's parts
without the labStamp/extra.

## 5. Block 1 — Identity & key numbers

Title `OVERVIEW`. §15.11 body layout: 2-col rows, key `--ink3` left / value
`--ink` right, `tabular-nums`. Rows (from `ctx.g`):

- `dpsLabel()` (L1714) at the lens → `g.med`
- `p30–p85 spread` → `fmtInt(g.q30)+" – "+fmtInt(g.q85)` plus the Data Table's
  spread % (`100*(q85-q30)/q85`, guard q85>0) — the owner's self-eval read
- `Avg deaths` → `g.adeaths.toFixed(2)`
- `Deathless` → `g.deathless.toFixed(1)+"%"`
- `Med player rating` → `fmtInt(g.mrating)` + `· n=<g.rn> rated` — only if
  `hasRating && g.rn`
- **4pc standing** (graft, both judges): one row, rendered only when
  `hasTier && labHas("tier4pc")` AND `window.__setRows` holds a matching row
  (match on cls/spec/hero names) with finite `s4`/`p4`:
  `4pc <s4.toFixed(0)>% adopted · +<p4.toFixed(1)>% matched gain`. To avoid a
  second O(N) pass, `renderSetBonus()` (L2730) publishes its computed rows once:
  `window.__setRows=rows;` right after `const rows=setBonusRows()` (L2741) —
  render order is fine because `renderFrame()` runs last (§3). Retirement of the
  tier4pc manifest entry ⇒ `labHas` false ⇒ the row simply never renders — zero
  residue (Lab-gated, the owner's "is it gear or me" question at the exact moment
  of vetting a spec).

Footer (always): `<n> parses · <runs> runs · <chars> chars · <fmtDay(FRAME_A.dmin)> – <fmtDay(FRAME_A.dmax)>`
then the `full row → Data Table` link (house accent link style L48, **no caret
glyphs**) wired to `jumpToRow(ctx.key)`.

**Compare inline (gate 3):**
- Time compare on (`state.compare`): the DPS, avg-deaths, and deathless rows
  render `A · B (Δ)` — A from `ctx.g`, B from `ctx.b`, Δ via
  `deltaHTML(a,b,{betterUp})` with betterUp true for DPS/deathless, **false** for
  deaths (same polarity the tabs use). Missing B ⇒ deltaHTML's own "new"/"–".
- Skill compare on (`skillOn()` && !state.compare): distribution rows only —
  DPS row shows `p{pctl} · p{pctlB}`: `g.med` vs `g.qb`; deaths row shows
  `g.qdA` vs `g.qdB` with sub-caption `deaths at the p{pctl}-th vs p{pctlB}-th
  parse — lower is better`; deathless row plain with `(skill compare does not
  apply)` — the chart's honest caption language (L2311-2317), abbreviated.
- Archon mode: numbers already flow through `aggregate()`/`dpsLabel()`, so the
  block automatically states the elite statistic; the header scope fragment says
  "Archon replica" (§4) — no special casing.

## 6. Block 2 — Top comps (absorbing the built comps feature coherently)

**Data — the one-line publish (graft, both judges).** In `renderComps`
(L2810), immediately after `const rows=rowsAll.filter(r=>r.n>=state.compMin);`
(**L2876**) add:

```js
window.__compRows=rowsAll;   // same objects, same single pass as the table/Pulse/__compPresence
```

This single publish sits **before both early returns** (the no-qualifying-comps
return L2878-2885 and the spec-slice zero-state return L2911-2918), so the frame
can never read stale rows after a filter change empties the comp set — it
satisfies both judges' dual-publish graft with one line (state this equivalence
in a code comment). Global ranks land on these same objects at L2892 for
qualifying rows; below-gate rows have `rank===undefined` ⇒ frame shows "–".
Never `aggregate()`, never the sidebar Spec filter (the comps_facts §2 drift
trap: runSeen semantics would silently swap run counts — 415 vs 547 measured).

Title `TOP COMPS`. Sub-line above the table:
`in K of Q qualifying comps (<compMin>+ runs) · M distinct field it`
where `K=__compPresence.map.get(ctx.presKey)||0`, `Q=__compPresence.den`,
`M=members.length` — K/Q ≡ Pulse's numerator/denominator by construction
(published at L2860 from the identical pass).

A genuine mini `<table class="data">`: columns `# / Composition / Runs /
Strength` — **all four sortable both ways** (header click; shared `cmpCells`
L2096, NaN parked last; sorted-header edge-rule styling reused). Local sort
state is module-level `frameCSort`, default `{col:"n",dir:-1}` ("top = most run",
the owner's definition, matching `jumpToComps` L2083); reset to the default on
spec switch. **Cap 5 applied AFTER the sort** (house rule; slice the sorted
array). Sorting re-renders only this block, not the page.

Membership & rows:

```js
const members=(window.__compRows||[]).filter(r=>r.comp.some(c=>c.cls*100+c.spec===ctx.presKey));
const gated=members.filter(r=>r.n>=state.compMin);
```

- `#` = `r.rank ?? "–"` (global strength rank). Composition cell reuses the
  section's markup (role initial + "Spec Class" class-colored, L2935-2942) with
  the frame's spec getting the existing `.sel` static emphasis (CSS L379).
  Strength cell reuses the `+x.x` up/down delta text style (text-only color).
- Below-gate fallback mirrors the section: if `gated.length===0 &&
  members.length>0` ⇒ top 3 (after sorting members by the current sort) muted
  with the existing `.subgate` row class (L381) + line `all below the
  <compMin>-run gate — lower Min runs in Top Comps`. `members.length===0` ⇒ one
  line `no comps under the current filters field it` (no table). `Q===0` ⇒ the
  section's own "no comp has N+ runs" reality: sub-line prints `in 0 of 0` never
  — instead show the below-gate/zero-state lines above (same states the section
  already has; at keys 17–19 `Q=1` prints the true "in 1 of 1").
- Notes appended to the sub-line when relevant: `· period A` (Time compare —
  comps are computed on period A, same as the section L2905) and the hero-share
  note when merge is off: `· comps are class+spec — hero talents share them`
  (verbatim from L2904; `ctx.presKey` strips hero exactly as the Data Table's
  Comps cell does, L3306).

Footer link: `all <K> comps →` wired to `jumpToComps(ctx.presKey)` — opens the
full sortable section slice with the `#compspec` select synced (the built
feature, reused verbatim; it already sets `state.compSpec`, most-run sort, opens
and scrolls to the section).

## 7. Block 3 — Character stats + flask chips (chips live HERE and nowhere else)

Exists only when `ctx.stats` (§9 interface; spec key = `"Class|Spec"` names,
hero stripped — same bridge as comps). Title `CHARACTER STATS`.

Rows: one per stat in `ctx.stats.stats`, **ordered by p50 descending** (the
honest "priority read" our quantiles support — gate 6: NO "A > B > C" prose
line; the ordering plus the bias footer carry the priority). Each row:

- stat name in full (`Versatility`, not `Vers`)
- a 120px **range bar** (`.frange`, §2 CSS): track `--line1` hairline; filled
  span p25→p75 in `rgba(234,227,208,.14)`; a 2.5px `--tickref` tick at p50
  (the §15.10 reference-tick recipe, radius 1px)
- text `p50 (p25–p75)` tabular.

All bars share one x-scale (0 → max p75 shown) so relative magnitude reads
across stats. **No histogram is faked — we ship quantiles, we draw quantiles.**

**Flask chips** (hasFlask pattern), rendered only when `ctx.stats.flasks` is a
non-empty object: a chip row under the title — `All` + one chip per flask,
label = flask name minus a leading `"Flask of "` prefix + ` · n` count (full
name in `title=""`). Standard §15.4 chips; active chip uses the existing metal
`.chip.on` styling (L130). Selecting a chip sets `state.frameFlask` (display-only,
default `null`, reset to `null` on spec switch and on reload) and re-renders the
stat rows from `flasks[name].q` — **core 4 stats only**; tertiary rows simply
drop while sliced (honest absence, no placeholder). While a flask chip is
active, the n line reads **`n=<flasks[name].n> of <flaskKnown> (flask-known)`**
(graft, both judges) — never a bare chip count.

Footer, in order:
1. **`D.specstats.cohort` printed verbatim** (contract hard constraint — the
   pipeline-built sentence carries window, key range, n, gear-known and
   flask-known shares).
2. This spec's `n=<ctx.stats.n>` (or the flask-sliced form above).
3. The required disclaimer, verbatim: *"fixed build-time cohort — does not
   follow the filters, period, or lens above"* — this block is the only
   fixed-cohort number on the page and must say so.
4. The bias note, verbatim: *"ordered by median rating; what gear drops biases
   this"* (Archon's honesty, our words — no rarity tinting, no `>` arrows,
   nothing Archon-shaped).

Compare/lens have no effect here — and under Time/Skill compare the footer's
disclaimer already says why.

## 8. Edge cases (all must be built)

- **Rare spec** (Fire Mage: 0 of 22 qualifying, 9 distinct at defaults): comps
  block shows the muted below-gate fallback; stats entry omitted by the pipeline
  at <10 chars ⇒ stats block absent; identity still full. The frame never
  renders empty chrome.
- **Spec filtered out / trust-gated away** (`ctx.g===null`): identity numbers
  replaced by one sentence — `no parses match the current filters` — comps
  block shows its zero-state line, **stats still render** (fixed cohort,
  legitimately filter-independent). Frame stays open; no surprise auto-close.
  Names come from `presKey` decode (§4), so the header stays correct.
- **High keys** (Q=1 or 0 — real states at keys 17–19): sub-line prints the true
  `in 1 of 1` / the below-gate fallback — same states the section already has.
- **Compare (Time/Skill)**: §5 inline rendering; comps sub gets `· period A`;
  stats unaffected (and the footer says why). Bars stay clickable in `.cmp`
  mode — the trigger is still the bar element + label text span only (L2296-2300
  wiring unchanged apart from the handler body).
- **Archon mode**: flows through `aggregate()`; scope fragment names the mode;
  toggling re-renders the open frame via `render()`.
- **1366×768**: 1366−84 = 1282 > 1116 ⇒ full-width rail; blocks ≈
  240+520+300+2×32 gap ≈ 1120 → stats wraps to a second row worst-case;
  `max-height:38vh` scrolls internally. ≤900px: 18px positioner padding, blocks
  stack.
- **Hero split (merge off)**: frame keys by `groupKey` (L1763:
  `(cls*100+spec)*200+hero`) so identity numbers are per-hero; comps and stats
  bridge to class+spec via `presKey` with the standing "hero talents share
  these" note.
- **Arrow-stepping when the framed spec left the chart** (`indexOf<0`): keys do
  nothing; Esc still closes.
- **Chart re-render while open**: `.framed` is re-applied in the row loop;
  if no row matches, no cue is shown but the frame stays open.

## 9. Feature-detect interface — specstats / flask (EXACT contract)

The client ships **zero** UI for any of this until the payload carries it.
Detection is presence-only — no boolean flags, no config:

| Condition (client) | Effect |
|---|---|
| `D.specstats` absent | stats block does not exist anywhere; frame = identity + comps |
| `D.specstats.specs["Class|Spec"]` absent | stats block absent for that spec only |
| entry present, `flasks` absent/empty | stat rows render; NO chips row |
| `flasks` non-empty | chips row renders (`All` + one per key of `flasks`) |

Payload block, produced by a parallel change to `scripts/build_site_data.py`
(aggregating the gear journal `data/processed/gear.jsonl` /
`data/gear.jsonl.gz`) — SMALL, quantiles only:

```jsonc
"specstats": {
  // Built at build time; the client prints it VERBATIM. Must state: window,
  // key range, character count, gear-known share, and (once flask capture
  // exists) flask-known share. Example:
  "cohort": "1,842 characters with known gear, keys +12 and up, Aug 13 – Aug 26; gear known for 61.4% of parses in that window; flask known for 0%.",
  "specs": {
    "Mage|Arcane": {                    // key = D-name "Class|Spec", hero stripped
      "n": 412,                         // distinct characters behind the quantiles
      "stats": {                        // per secondary stat: [p25, p50, p75] rating
        "Critical Strike": [8123, 9410, 10877],
        "Haste":           [15200, 16890, 18345],
        "Mastery":         [9877, 11002, 12400],
        "Versatility":     [4310, 5120, 6488],
        "Leech":           [801, 1450, 2210]   // tertiaries optional, same shape
      },
      "flaskKnown": 0,                  // characters with known flask (0 until capture ships)
      "flasks": {                       // OMITTED ENTIRELY while flaskKnown == 0
        "Flask of Tempered Aggression": {
          "n": 41,                      // characters on this flask
          "stats": { /* CORE 4 ONLY, same [p25,p50,p75] shape */ }
        }
      }
    }
  }
}
```

Pipeline rules: omit a spec entry below **10 characters**; omit `flasks` (the
whole key) until flask capture has non-zero coverage; per-flask entries need
**≥10 characters** each; quantiles use the same linear-interpolated `qp`
definition as the client (L1715). The collector change (capture flask identity
from the WCL summary combatant info, forward-only; no backfill assumed) is a
separate task — the client contract above is complete without it.

Client accessor (the only coupling): `D.specstats?.specs?.[cls+"|"+spec]` with
`stats` map order irrelevant (client sorts by p50 desc) and `q=[p25,p50,p75]`
arrays. **If any of this is absent the corresponding UI simply is not rendered
— never a placeholder, per feedback removal #3.**

## 10. Grafts folded in (traceability)

| # | Graft (source) | Status |
|---|---|---|
| 1 | Arrow-key ↑/↓ stepping through chart order (J1-1, J2-2) | **IN** — §3 |
| 2 | `.framed` static gilded bar highlight (J1-2, J2-1) | **IN** — §2/§3 |
| 3 | 4pc standing line in identity, Lab-gated (J1-3, J2-opt) | **IN** — §5 (one gated row; zero-residue retirement; J2's "only if room" satisfied — it is one line) |
| 4 | Priority prose "Mastery > Crit > …" (J1-4) | **REJECTED** — directly banned by J2 veto 6 (Archon sentence shape, `>` glyphs). Vetoes outrank grafts. Its intent survives as the p50-desc row order + the bias footer (§7), which J2 explicitly endorses. |
| 5 | Flask-slice denominator `n=41 of 123 (flask-known)` (J1-5, J2-3) | **IN** — §7 |
| 6 | `__compRows` publish covering the empty-state path (J1-6, J2-5) | **IN** — §6 (single publish at L2876, provably before both early returns — equivalent, simpler) |
| 7 | Per-mode compare wording: Time=A/B/Δ via deltaHTML, Skill=p{lens} vs p{pctlB} on distribution rows with honest captions (J2-4) | **IN** — §5 |
| 8 | Remove first-open animation class on `animationend` (J2-6) | **IN** — §3 |

Also honoured from the verdicts: the winner-text corruption ("自动") is gone —
the sentence reads "the block automatically states the elite statistic" (§5).

## 11. Deliberately NOT added

Multi-pin (owner rejected); hover-preview in the rail (tooltip owns hover; calm
UI); fake histograms; any talents/trinkets stub or "coming soon" slot (the
registry makes them one future entry — dormant UI is banned); flask chips
anywhere outside the stats block (owner: "don't pollute the rest of the
dashboard"); a duplicate full comp sorter inside the frame (the mini-table
sorts; the link reaches the complete one); outside-click close; frame
persistence across reloads; auto-open on load; toast repositioning; changes to
`aggregate()`, `renderComps`' math, the sidebar, section order, the
compspec/feeder-link feature, or the tooltip.

## 12. Code touchpoints (all in `/home/user/wowlogs/site/index.html`; line numbers verified against the working tree)

1. **L2300** — handler swap: `bar.onclick=()=>openFrame(r.key);
   lspan.onclick=()=>openFrame(r.key);`
2. **~L2214** (after `view` is final in `render()`) — `CHART_KEYS=view.map(r=>r.key)`.
3. **~L2244** (row loop) — `row.classList.toggle("framed", r.key===state.frameKey)`.
4. **L2876** (`renderComps`, right after `const rows=rowsAll.filter(...)`) —
   `window.__compRows=rowsAll;` (one line; §6).
5. **L2741** (`renderSetBonus`, right after `const rows=setBonusRows()`) —
   `window.__setRows=rows;` (one line; §5 4pc row).
6. **~L754** (state literal, next to `compSpec`) — add
   `frameKey:null, frameFlask:null,   // Spec Frame — display-only, cleared on reload`.
7. **In `render()`** — stash `FRAME_A=A; FRAME_B=B;` (near L2119-2122) and append
   `renderFrame();` after `renderStrip(rows,A);` (L2334).
8. **New JS** (~120 lines): `FRAME_BLOCKS`, `renderFrame()`, `openFrame()`,
   `closeFrame()`, `frameScope()`, `paintFramed()`, the keydown listener (§3),
   the mini-table sort wiring, the flask chip wiring, the `main` padding-bottom
   management.
9. **New markup** — `#frame-pos` block after `</main>` (§2); **new CSS** (~45
   lines, §2) near the `#tip` rules.
10. **Copy** — `#overview-sub`: static default at **L598** and the string inside
    `scopeLine("overview", …)` at **L2326** both become
    `click any bar for its Spec Frame — comps and stats in one place`.
11. **Pipeline (parallel task)** — `scripts/build_site_data.py` emits
    `specstats` per §9; collector starts capturing flask identity (forward-only).

Everything else — `jumpToRow`, `jumpToComps`, `#compspec`, `syncCompSpecUI`,
`.complink` wiring, `#tip` and `attachTip`, `aggregate()`, `renderComps` math,
sidebar, section order, toasts — **untouched**.

## 13. Persona check (acceptance narrative)

Daily meta check: **one click** on any Overview bar ⇒ that spec's comps
(most-run first, honest `K of Q`) and stat distributions (with flask slicing,
once data exists) visible together, DPS/spread/deaths/rating beside them; ↑/↓
walks the ladder without another click; a second click of the same bar or Esc
dismisses; the chart never moves, shrinks, or scrolls. Alt evaluation = click
the alt's bar, read spread + comps + stats without leaving the chart. Compare
on ⇒ the frame shows the same A·B(Δ) the Data Table would — prediction stays
first-class.
