# Build Blueprint — VERSION 1 · E01 "Command Center"

Skin: Candlelit Ledger v2 (fleet/design_language.md). Contract: fleet/checklist.md (all 157
items mapped in §4). Owner prefs override everything (fleet/user_prefs.md). Hard rules:
keep EVERY element id; every move is DOM relocation on unchanged compute paths; no new
view/tab system; defaults never change without owner sign-off (deviations parked in §7).

## 1. Design thesis (5 lines)

One page ordered as the owner's decision ladder: is the meta moving → who is strong now →
where is it heading → where does it land → evidence → raw table. The left rail is regrouped
by PURPOSE (Scope / When+Baseline / Cohort / Trust gate / Lab); a sticky Command Bar keeps
the lens (KPIs, percentile, compare, Archon) in hand at every scroll depth; every transient
feature lives and dies through one declarative LAB manifest, never as a loose checkbox.

## 2. Page map (top to bottom)

**A. Left rail** (sticky aside, 300px, `#side`; single column ≤900px — mechanics unchanged)
1. `↺ Reset filters` button (top, full width; still `location.reload()`).
2. **SCOPE** — micro-header; `<details>` (static +/− markers, no rotation): Class, Spec,
   Hero Talent (`#hero-box`, hidden when merged) + "Merge hero talents" switch, Dungeon,
   Key Level readout + dual-thumb slider, Region. Defaults: all closed, empty = no filter.
3. **WHEN + BASELINE** — Period A presets `#presetA`, Custom weeks A details, compare
   switch `#cmp` (default off), quick-compare chips `#quickcmp`, Period B panel `#blockB`
   (ghost-grey chips, hidden until compare), "Since latest tuning" `#posttune` row
   (hasTune-gated, default on when present).
4. **COHORT** — Role chips (Role details open by default, {DPS}), Melee / Ranged switches.
5. **TRUST GATE** — Minimum characters slider + `#minchars-eff` + `#minchars-help` prose
   (verbatim), Timed segmented control `#timedseg` (relocated from header; default Timed
   only when hasTimed).
6. **⚗ LAB** — manifest-rendered cards (see §3.3). v1 entries: `tier4pc` (active; hosts
   t0/t2/t4 switches + `#tierhint`; t4 on by default), `proj` (`#projcb`; dormant greyed
   row when !hasProj), `pctlcmp` (off), `spins` (off). Dormant rows greyed-labeled,
   visibly distinct from removed.
7. Bottom hint ("Empty selections mean no filter…") — unchanged.

**B. Main column** (centered, max-width 1200px; nothing full-bleed)
1. **Header** — ⚔️ wordmark "Mythic+ Performance" (Marcellus serif, used once),
   `#hero-sub`. Timed/tune/proj controls no longer live here (relocated per rail).
2. **Sticky Command Bar** (new sticky wrapper; ≤80px budget): compact KPI stats (relocated
   `#k-runs/#k-parses/#k-groups/#k-dates`; Groups reads "17 of 27 shown"), percentile
   slider `#pctl` + readout + ☆/★Me chip, Compare status chip, Archon chip (`#archon`
   input relocated, title verbatim), scroll-spy divergence slot (right-aligned, one line).
   MANDATORY: condenses to ONE row at ≤1366×768 (dates+runs fold into title attrs first).
   NO data/llms links here (veto). No second permanent row ever ships at laptop sizes.
3. `#period-note` — unchanged composition, plus amber ⚗LAB badges for active Lab entries
   and the pctlcmp XOR notice line when applicable. ARCHON_RECIPE scope card renders here
   when Archon active (replaces the elite sentence's slot; same generated text source).
4. **§ Meta Pulse** (`data-sec="pulse"`, default open) — the movers board, section #1:
   - Board header prints ITS OWN baseline dates: "This reset (Aug 20–25) vs last reset
     (Aug 13–19)" — always bucket 0 vs bucket 1 under current non-period filters; the
     compare toggle NEVER redefines these numbers (veto).
   - Movers table, one row per gated group: Spec (class dot) · Δ pN DPS vs baseline
     (colored %; grey `.na` + printed warning when B thin or windows overlap — never
     removed) · Rank now→then · Parse-share Δ · 8-bucket med sparkline · generated
     verdict line ("Drifting · +2.1% vs last reset · #3→#3 · in 18 of 31 qualifying
     comps"). Sortable by Δ (default), rank change, share Δ.
   - **Top now** column block: top 5 by current-tab metric at current percentile, with
     avg deaths printed.
   - **Comp anchors**: top 3 comps by strength, presence over ALL comps passing compmin,
     denominator stated ("in 18 of 31 qualifying"). Never "/25".
   - Archon active → board greys with "movement unavailable in Archon replica" note.
5. **§ Rankings** (`data-sec="overview"`, title "Rankings") — sort seg (always visible
   now), metric tabs `#tabs` (Trend button REMOVED — no ghost pseudo-tab, veto), bar
   chart `#chart` (≤960px), caption. All chart/tooltip/compare mechanics unchanged.
6. **§ Trajectory** (`data-sec="trajectory"`, default open) — Trend promoted to a peer
   section: `#trendseg` metric picker (med label tracks slider), NEW normalization seg
   "DPS | Rank | Share of chars" (default DPS = today's behavior), `#trendview`
   Overlay/Grid (default Overlay), slope-sort toggle on the series list, tuning-patch
   markers (vertical hairlines from `d.tuning`, hasTune-gated), truncation flags when
   filters clip the season span, `#trendbox`. All trend semantics per checklist L.
7. **§ Top Comps** (`data-sec="comps"`) — `#compmin` slider relocated into this section's
   header row; table/caption/strength model unchanged; sub states the all-qualifying
   denominator.
8. **§ Set Bonus Gain** (`data-sec="setbonus"`) — unchanged content; now the OUTPUT
   section of Lab entry `tier4pc` (amber badge in header; hidden when !hasTier as today).
9. **§ Data Table** (`data-sec="breakdown"`, title "Data Table") — Breakdown moved LAST,
   EXPANDED. Demoted by position only; NO collapse seed of any kind (veto). + p-spread
   column (§6.8).
10. **Footer** — methodology, `#foot-src`, reset note, `#built`, links: llms.txt ·
    llms/ HTML tables · sitemap.xml · **data.json.gz** (added here, footer only — veto).
11. `#upd-toast` + ETag poller, `#tip` — unchanged.
12. S-PINS docked tray (when Lab-active and pins exist): fixed positioner, measure-aligned
    bounded inner box (max-width ~1116px), above footer viewport edge. See §6.4.

## 3. Control architecture

### 3.1 Groups (every existing control has exactly one home)
| Group | Controls (ids kept) |
|---|---|
| Rail top | reset button |
| SCOPE | f-cls, f-spec, merge, hero-box/f-hero, f-dun, keys-v, klo, khi, f-reg |
| WHEN + BASELINE | presetA, f-weeksA, cmp, quickcmp, blockB (presetB, f-weeksB), posttune (+tunehint) |
| COHORT | f-role, melee, ranged |
| TRUST GATE | minchars (+minchars-v/-eff/-help), timedseg |
| ⚗ LAB | t0, t2, t4, tierhint (entry tier4pc) · projcb, projhint (entry proj) · pctlcmp card · spins card |
| Command Bar | k-runs, k-parses, k-groups, k-dates, pctl (+pctl-v), ★Me chip, Compare chip, archon, divergence slot |
| Section headers | compmin (+compmin-v) in Top Comps header; sortseg/tabs in Rankings; trendseg/trendview/normalization seg in Trajectory |

### 3.2 Command Bar rules
- Compare chip is a STATUS chip (new element): shows "Compare off" / "A vs B on"; click
  when off fires the default quick-compare (this-vs-last), click when on = setCompare(false).
  The canonical `#cmp` switch stays in WHEN + BASELINE, behavior byte-identical.
- Archon chip = the relocated `#archon` input restyled as a chip; long title preserved
  verbatim; derived-state sync (`__archonMatches` at render start) unchanged.
- Divergence slot: IntersectionObserver on section bodies; shows the on-screen section's
  standing caveat (Trajectory: "whole season — ignores your period selection"; Set Bonus:
  "tier boxes do not apply here"; Comps: "timer-margin runs only"; Pulse: its baseline
  dates; Rankings/Table: "≥ N chars here"). Empty at page top. Text only, calm, no motion.

### 3.3 LAB_FEATURES declarative manifest (transient home + lifecycle)
```js
LAB_FEATURES = [ { id, name, status: "active"|"dormant"|"off", gate: ()=>bool,
  controlHTML, outputSecId?, noteBadge, exemptions: [secId...], scopeBits: ()=>string,
  nImpact: ()=>string } ]
```
- **Entry** = one object → Lab card (frame: name · status · generated scope line ·
  n-impact · control) + optional output section + amber period-note badge + exemption
  notes stamped into exempt sections' scope lines.
- **Retirement** = delete the entry: card, badges, output section, storage all go.
- **Dormant** (gate false, e.g. !hasProj) = greyed labeled row, visibly distinct from
  removed. localStorage namespaced `wowlogs.lab.*` only.
- **Birth rule (FORBIDDEN otherwise):** adding a checkbox to Scope/Cohort/When/Trust is
  forbidden — every new transient enters via a manifest entry.
- v1 entries: `tier4pc` (active; owns t0/t2/t4 + Set Bonus Gain output; exemption:
  setbonus + tierhint compute with `anyTier=true` as today), `proj` (gate hasProj;
  posttune force-off interlock unchanged), `pctlcmp` (§6.3), `spins` (§6.4).

### 3.4 ARCHON_RECIPE single source
One object {pctl:85, elite, klo:min, khi:max, timedOnly:false, tiers off, merge, no
compare, role DPS, weeks all} generates BOTH `applyArchonState`'s payload and the scope
card text (keys/period/min-chars "bypassed" list, eliteHidden clause). Additive
"[Restore my pre-Archon filters]" button: after auto-uncheck exit, `archonPrev` is
retained; the button (in the fading scope card / period note) replays it via
`applyArchonState(archonPrev)` then clears it. Explicit toggle-off path unchanged.

## 4. Migration map — every checklist item

| Item(s) | New location / behavior |
|---|---|
| 1 | Unchanged; single file; Google Fonts request swaps Cinzel→Marcellus (+ Inter) |
| 2, 3 | Unchanged (title rewrite, ⚔️ favicon, robots, viewport) |
| 4 | Retokenized to Ledger v2 (warm graphite, champagne, radii 6/4); focus/selection/scrollbar contracts kept |
| 5, 6 | Unchanged grid/sticky/collapse; rail content regrouped per §2.A; centered 1200/960 measure kept |
| 7–10 | Unchanged (loading gate, SEASON cfg, gz-first + cache:no-cache, payload shape) |
| 11–15 | Unchanged (MELEE/RANGED sets, CLASS_COLORS, cascade maps, has* flags, selection pruning) |
| 16, 17 | Unchanged byte-for-byte (per-load defaults, DEF snapshot, state defaults; "trend" tab id retired from #tabs UI only — see 86) |
| 18–21 | Unchanged (reset buckets, periodPass semantics, RUNS, formatters/esc) |
| 22 | Resize re-render fires when Trajectory section is expanded (was: Trend tab active) |
| 23 | Unchanged; rail top |
| 24 | Micro-headers reworded to SCOPE / WHEN + BASELINE / COHORT / TRUST GATE / ⚗ LAB; same style |
| 25 | Same details/collapse + Role-open default; rotating ❯ → static +/− marker swap (skin rule 1); still non-persistent |
| 26 | Unchanged (all eight .cnt spans, syncCounts) |
| 27 | Chips restyled to --r1 rectangles; toggle/cascade/render + ghosted-B variant unchanged |
| 28 | Unchanged everywhere |
| 29–31 | SCOPE group; unchanged |
| 32 | SCOPE; unchanged (grouping key, column swap, sub wording, no min-chars rescale) |
| 33 | Switch pattern kept, champagne restyle |
| 34 | #archon → Command Bar Archon chip; title verbatim; inline hint text absorbed into ARCHON_RECIPE scope card |
| 35–45 | Unchanged, byte-identical (snapshot fields, applyArchonState single writer, explicit restore + fallback, auto-uncheck-keeps-changes, predicate scope, elite path constants/passes/output, replica note incl. eliteHidden) — note text now generated from ARCHON_RECIPE, wording preserved |
| 46 | Unchanged (gate skipped in elite; minchars-eff still displays) |
| 47, 48 | COHORT group; unchanged (role default {DPS}; XOR melee/ranged) |
| 49–52 | t0/t2/t4 + tierhint become LAB entry `tier4pc`'s control block; ids, t4-on default, setBucket/tierPass/updateTierHint logic and text unchanged; hasTier gating = manifest dormant/hidden |
| 53–55 | SCOPE; unchanged (dungeon chips, keys readout, dual-thumb slider mechanics) |
| 56–59 | WHEN + BASELINE; unchanged (presets, titles, custom weeks, refreshPeriodUIs) |
| 60, 61 | WHEN + BASELINE; unchanged (setCompare plumbing, A={0}/B=first>0 defaults); Command Bar chip mirrors state additively (§3.2) |
| 62–64 | WHEN + BASELINE; unchanged (quick-compare, blockB ghost styling, describePeriod) |
| 65–68 | TRUST GATE; unchanged (slider range/default 250, eff annotation, scaling contract, help prose verbatim) |
| 69–72 | #pctl + readout → Command Bar; hint kept as title/expanded text; range/default/oninput/TABS-mutation unchanged; relabel reach EXTENDED: + Pulse Δ column header, Trajectory metric button, pin tray, ★Me chip title (see §5.4); qp math unchanged |
| 73 | #compmin → Top Comps section header; range/default/hint/behavior unchanged |
| 74, 75 | Region → SCOPE (details, unchanged); bottom hint unchanged |
| 76 | Header; Marcellus wordmark; hero-sub unchanged |
| 77 | #timedseg → TRUST GATE; buttons/default/filter semantics unchanged |
| 78 | #posttune → WHEN + BASELINE; gating/default/hint/disable-under-proj unchanged |
| 79, 80 | #projcb → LAB entry `proj`; filter/projSkip/projDelta/period-note prefix contracts unchanged; !hasProj now renders dormant greyed row (was hidden — manifest rule; flagged §7) |
| 81, 82 | KPI values relocate into Command Bar as compact stats (ids kept); Groups gains "of N" total ("17 of 27 shown"); computations unchanged; gold-accent card styling adapts to compact strip |
| 83 | #period-note unchanged in content/order; + amber LAB badges; + pctlcmp XOR notice; ARCHON card slots here |
| 84 | Section retitled "Rankings"; data-sec="overview" kept; collapsible |
| 85 | sortseg unchanged, but never hides (chart always renders; Trend no longer a tab) |
| 86 | Trend button removed from #tabs (→ Trajectory section); no ghost pseudo-tab (veto); other tabs/default "med" unchanged |
| 87–101 | Unchanged in full (TABS contracts, rank-before-sort, CHART_MAX 40, x-scale/negatives, shade/gloss, labels, inlays, ticks, compare ghosts/deltas, bval anchoring, tooltip attach/position-once/content, empty state, caption + ratingCoverage, A-side join semantics) |
| 102 | Trajectory is always-on peer section: trendseg/trendview/trendbox relocate; Breakdown metric fallback rule retired (Data Table always uses active tab; on no case does a "trend tab" exist) — table metric = active tab, unchanged otherwise |
| 103, 104 | Unchanged (metric picker incl. med relabel, default "avg"; Overlay default) + new normalization seg (default DPS = current behavior) and slope-sort toggle (default off = current ranking) |
| 105–114 | Unchanged (season-wide pass, own effMin pool, TREND_MAX 16, empty states, weekly→daily fallback, overlay SVG geometry/dash/labels, spotlight + trendPin, cursor-following point tips kept as-is, caption sentences, grid view) + tuning markers/truncation flags drawn additively |
| 115–120 | Section = "Data Table", LAST, expanded, no collapse seed (veto); sub text, columns, compare columns, sort rules (NaN→−Inf), no row cap, sticky header, projection badges ALL unchanged; + p-spread column (§6.8) |
| 121–130 | Set Bonus Gain unchanged (visibility dance, sub, matched-cell stats, shares, m0/m2/m4 percentile columns, column set/sort NaN-last, caption substance + live numbers, wrapping headers); now labeled as tier4pc Lab output with amber badge |
| 131–138 | Top Comps unchanged (inputs, strength model, qualification, columns, comp cell, live-number caption, self-contained re-render); compmin in header; sub/presence wording states all-qualifying denominator |
| 139 | Footer unchanged + sitemap.xml + data.json.gz links (footer ONLY — veto) |
| 140, 141 | Toast + hardened ETag poller unchanged |
| 142–144 | Collapse system unchanged; data-sec set gains pulse + trajectory (default open, never seeded); rotating chevron → static −/+ + 56→20px accent tick; localStorage key + try/catch guards unchanged |
| 145 | render() pipeline extended: archon sync → aggregates → gate → KPIs → period note → PULSE → chart+caption → TRAJECTORY → Data Table → Set Bonus → Comps → tier hint; every surface refreshes on every state change |
| 146–150 | Unchanged (rowPass order, aggregate stats, p0/p99 edges, TABS-mutation reach extended per item 71 row, persistent sorts/pins/sets across renders and Archon cycles) |
| 151–157 | Unchanged (sort fallback validation, fmtInt/fmtDay everywhere incl. new surfaces, chart/table/tooltip trio agreement — Pulse documents its own bucket-0/1 basis, projSkip on every stat incl. Pulse/pins, q50 vs qp both kept, esc() on all payload strings incl. verdicts, has*-gated rendering for every new surface) |

## 5. Interaction contracts

1. **Control-change visibility.** Any state change → full render(): Command Bar KPIs,
   scope lines, Pulse, chart, Trajectory, tables, tier hint all refresh (item 145). Every
   section's evidence sentence comes from ONE `scopeLine(section)` generator (hand-written
   scope prose banned); active Lab entries stamp an amber ⚗ badge into the scope line of
   every section they touch, with exemptions ("tier boxes do not apply here") generated
   from the manifest.
2. **Compare mode.** #cmp on → blockB shows, gain/loss sorts appear, ghost bars + Δ badges,
   B-scaled effMinB, KPI = period A (all unchanged). Pulse board is INDEPENDENT: always
   bucket 0 vs 1 with its own dates printed on the board; compare may never redefine it.
   pctlcmp is XOR with period compare: enabling either turns the other off with a visible
   one-line notice in the period note — one ghost system at a time.
3. **Archon entry/exit.** Enter: archonPrev=snap() → applyArchonState(ARCHON_RECIPE) —
   byte-identical fields. Explicit exit: wholesale restore, archonPrev=null. Divergence:
   predicate fails at render → auto-uncheck, user's changes KEPT, archonPrev retained and
   the additive "[Restore my pre-Archon filters]" button offered (§3.4). While active:
   generated scope card + greyed bypassed controls; Pulse greys with the replica note.
4. **Percentile relabeling.** Slider 0–99 relabels every DPS reading: med tab, trend
   metric button, chart inlay/tooltip/captions, Breakdown pN header, Set Bonus three DPS
   columns + caption, projection rows — PLUS Pulse "Δ pN DPS" header/verdicts, Trajectory
   med button, S-PINS cards, ★Me title. Exception by design: the p-spread column is fixed
   p30–p85 and says so in its header. Elite path stays pinned at 0.85.
5. **Defaults (all preserved; none change).** pctl 50 · minchars 250 · compmin 20 · keys
   six-wide band ending kmax−1 · weeksA {0} · compare off · B = last reset · t4 on only ·
   merge on · timedOnly = hasTimed · postTune = hasTune · role {DPS} · tab "med" ·
   trendMetric "avg" · trendView "lines" · sorts {a_v,−1}/{pt,−1}/{strength,−1} · sort
   "desc" · Role details open, others closed · sections open. New controls default to
   current behavior: normalization = DPS, slope-sort off, Lab pctlcmp/spins off, ★Me
   hidden until saved. Flagged deviations live ONLY in §7 as reversible options.

## 6. What's new (each judge-justified, client-side from the existing payload)

1. **Meta Pulse movers board** — E01 thesis core; answers the owner's daily "is the meta
   shifting" without clicks. Computed from existing aggregate paths on buckets 0/1;
   sparklines reuse the trend weekly-med pass. Thin-B greyed `.na` + printed warning.
2. **Generated verdict lines** on Pulse rows (E15 graft) — template over already-computed
   Δ/rank/presence; all-qualifying denominator (E01 rule).
3. **pctlcmp percentile ghost-compare** (judge demand, via LAB) — second qp() pass at
   pctl-B (default 30) over the same aggregation; renders through the EXISTING period-
   compare ghost/Δ path; owner's stated p30-vs-p85 self-eval in one toggle.
4. **S-PINS pin tray** (judge demand, via LAB) — click-pin ≤3 specs from chart/table rows
   into a docked, measure-aligned tray (skin's signature docked-inspector pattern);
   row-aligned mini-cards: med/avg/deaths/presence/verdict.
5. **★Me saved lens preset** (E27 graft) — ☆ save beside pctl stores {pctl, klo, khi,
   role} in wowlogs.lab.me; ★Me chip hidden until saved; click applies.
6. **scopeLine() generator + amber LAB stamping** (E19 graft) — one recipe → every
   section's evidence sentence; filters can never silently apply to a section.
7. **Scroll-spy divergence slot** (E15 graft) — Command Bar shows the on-screen section's
   caveat exactly when it matters.
8. **Trajectory normalization seg** DPS | Rank | Share-of-chars (E17 graft) — rank/share
   series derived from the same weekly bags, so a nerf reads as a fall during gear
   inflation; plus slope sort, tuning-patch markers (d.tuning, hasTune), truncation flags.
9. **p-spread "punishment proxy" column** (E06 graft) — per-group qp(.85)−qp(.30) (as %
   of p85) in the Data Table; fixed percentiles, labeled; NaN parked per table rules.
10. **LAB manifest + Lab rail group** (thesis) — the designed home for transients the
    owner asked for; ARCHON_RECIPE card + restore button (thesis).
11. **Command Bar** (thesis) — lens always in hand; "17 of 27 shown" total; one-row
    condensation at laptop sizes (mandatory).
12. **Footer data.json.gz link** (veto-directed placement).

## 7. Risks & parked owner questions

1. **PARKED (judge-flagged):** Movers deltas when baseline window <3 days — grey-with-
   warning ships; full suppression is the alternative. Owner to decide.
2. **PARKED:** Data Table (Breakdown) moves from position #2 to last. Position-only
   demotion, still expanded; reversible by moving one DOM block back.
3. **PARKED reversible option:** Trajectory default metric stays "avg" (current default);
   option: default "med" to match the page's percentile lens. NOT shipped.
4. **PARKED reversible option:** `proj` dormant-greyed row when !hasProj (manifest rule)
   vs today's fully hidden box. Shipping greyed per manifest; one-line revert if disliked.
5. **PARKED:** ★Me capture scope — v1 saves {pctl, keys, role}; option: full lens
   (period/timed/minchars). 
6. **Risk:** Command Bar ~80px sticky budget at 1366×768 — condensation order is
   KPI-dates → KPI-runs → divergence slot truncates; verify with real chrome. The bar may
   never wrap to a permanent second row.
7. **Risk:** early-season payloads with one bucket — Pulse renders "no baseline reset
   yet", board greyed, never blank; quick-compare already handles this (item 62).
8. **Risk:** Pulse + sparklines add per-render cost; both reuse memoized passes (trend
   bags, aggregate A) — profile at 60k parses; sparklines render only when section open.
9. **Risk:** relocating #timedseg/#pctl/#archon must not break applyArchonState's control
   sync (it queries by id — ids kept, selectors unchanged).
10. **Litmus checks before review:** side-by-side vs archon.gg (ground must differ), no
    rotation anywhere, no purple, no cursor-growth hover, tooltip triggers content-only.
