# Build Blueprint — VERSION 2 · E27 "Skill Lens" (prediction-first)

Rebuilt from fleet/podium_notes.md (E27 section + shared rules), fleet/checklist.md,
fleet/user_prefs.md, fleet/design_language_essence.md. Vetoes are absolute; grafts are demands.
Skin: Candlelit Ledger v2 (warm graphite, champagne active-accent, Marcellus wordmark once,
nothing rotates, calm hover, centered ≤1200px measure, docked inspector).

## 1) Design thesis (5 lines)

1. The percentile slider is promoted to a sticky global **Lens Bar**; the whole page re-reads through the chosen lens (p30/p50/p85/★Me chips + slider popover), and every label follows it.
2. **One compare engine, two axes — "Compare: Off | Time | Skill"**: the existing ghost-bar/Δ/A-B renderer is generalized so p30-vs-p85 is one click (a second `qp()` pass on the same aggregation; ranks always anchor to Lens A so bars never shuffle).
3. The p30↔p85 spread is a first-class **difficulty/forgiveness proxy** ("Punishes most / Forgives most" sorts) — the owner's self-eval question (p30-vs-p85 across specs) answered without leaving the chart.
4. **Pulse board above the fold** answers "is the meta shifting" daily: Δ at current lens, rank change, rank-sorted top-now DPS, sparkline, comp presence over all qualifying comps, roster stability tags — with both windows' dates, n, and confound warnings printed inline.
5. Trust and transience are architectural: sidebar regrouped SCOPE / COHORT / TRUST / ⚗LAB with the E01 LAB_FEATURES manifest (dormant / retired / graduated), generated scopeLine() evidence sentences everywhere, and Archon as a Lens Bar preset chip with one generated scope card.

## 2) Page map (top to bottom)

### 2.1 Sticky Lens Bar (global header, full-time sticky, top layer)
- Left: wordmark "Mythic+ Performance" — Marcellus letterspaced caps, used exactly once (replaces Cinzel hero; ⚔️ mark kept as favicon only).
- Center — **Lens group**: chips `p30 · p50 · p85 · ★Me` (crisp --r1 rectangles; active = champagne). `p50` active by default (pctl=50 unchanged). ★Me hidden until saved; saving lives in the slider popover ("★ Save current lens"), stored `localStorage["wowlogs.lens.me"]`, try/catch-guarded. A `pN ▾` readout chip opens the **slider popover** holding the existing `#pctl` range (0–99, step 1, default 50) + hint text (checklist 69) + the ★ save/clear row. Chip click = set pctl; slider = fine control; chips highlight on exact match.
- **⚔ Archon chip**: wraps the existing `#archon` checkbox (id kept). On = champagne-filled chip. Tooltip title preserved (see Risks R6). Behavior byte-identical to today (§5.3).
- Right — **Compare seg** `Off | Time | Skill` (--r1 buttons, 2px underline active). `Off` default. `Time` drives the existing `#cmp` state (period A vs B). `Skill` drives new `state.pctlB` (default 85; 30 when lens A ≥85). Strict XOR — one ghost system, one axis at a time; per-axis B state (weeksB / pctlB) is kept and restored on axis switch.
- At ≤1366×768 the Lens Bar and context strip (2.3) **condense to one row** (mandatory, E01 graft): wordmark shrinks to mark, KPI numbers drop labels, chips keep text. Sticky budget target ≤80px condensed (Risk R1).

### 2.2 Sidebar (left rail, 300px, sticky ≥901px; single column ≤900px — layout contract unchanged)
Top: **↺ Reset filters** full-width button, `location.reload()` (unchanged).
Four groups, uppercase champagne micro-headers with top hairline; all `<details>` use static +/− marker swap (no chevron rotation), Role open by default, others closed, none persisted:
- **SCOPE** — Who: Class / Spec / Hero Talent details (+ cascades, `.cnt` counts), "Merge hero talents into spec" switch (default on), Role chips (default {DPS}), Melee/Ranged switches. Where: Dungeon details, Key Level readout + dual-thumb slider, Region details. When: `#presetA` chips, Custom weeks A details, quick-compare `#quickcmp` chips, Period B panel `#blockB` (ghost-grey chips) shown when Time axis on. The `#cmp` checkbox remains in the DOM as the state carrier, visually merged into the Lens Bar Compare seg (its sidebar row becomes the seg's mirror; checking either syncs both).
- **COHORT** — hasTier-gated. Gear cohort chips **graduated from the tier switches**: three --r1 chips "No set · 2-piece · 4-piece" wrapping the existing `#t0/#t2/#t4` inputs (ids, union semantics, disjoint buckets, t4 default ON — all unchanged), `#tierhint` line beneath.
- **TRUST** — Timed seg `#timedseg` (relocated from header; default ⏱ Timed only when hasTimed), "Minimum characters" slider + `#minchars-eff` + the full `#minchars-help` prose (verbatim), "Min runs per comp" slider.
- **⚗ LAB** — dashed-border panel; each entry a standard frame (name · status · generated scope line · n-impact · control), rendered from the `LAB_FEATURES` manifest (§3.3). Launch entries: `posttune` (Since latest tuning) and `projcb` (🔮 Project upcoming tuning) as manifest frames — active when hasTune/hasProj, **dormant greyed rows labeled "awaiting data"** when absent (deviation from today's display:none; Risk R5); a one-line "Gear cohorts — graduated → COHORT" status row.
Bottom hint "Empty selections mean no filter…" (verbatim, unchanged).

### 2.3 Main column (centered, max-width 1200px)
1. **Header**: hero sub-line `#hero-sub` (season/source text; title lives in Lens Bar).
2. **Sticky context strip** (sticks under the Lens Bar): compressed KPIs (Runs · Parses · Groups · Dates — same ids/values, period-A), "**17 of 27 specs shown**" live count (= gated rows / total groups passing rowPass pre-gate), clickable scope chips (one per active non-default filter; click scrolls to/opens its sidebar control — read-only otherwise), the **programmatic-change notice slot** (one line announcing quick-compare/Archon rewrites, E19 graft), and the trust-gate "show anyway" affordance (§6.12). Full KPI cards render at the strip's natural position; sticky form is the compressed row.
3. `#period-note` composite line — assembly order and every clause unchanged (checklist 83).
4. **Archon scope card** (only while Archon active): ONE generated card from the recipe object — scope sentence, bypassed controls listed greyed, "elite frontier" note incl. eliteHidden clause (text of checklist 45 preserved inside it).
5. **⚡ Pulse** (section #1, above the fold — new section `data-sec="pulse"`, collapsible like peers): movers board, rank-sorted by DPS-at-current-lens by default (the veto-mandated "top now" read). Columns: rank · Spec (class color dot) · **DPS @ pN** (lens-relabeled) · **Δ vs baseline** at current lens (± badge) · rank change (#3→#1) · season sparkline (per-reset med at lens) · deaths/run · **comp presence "in X of Y comps (Y = comps with ≥compmin runs)"** — denominator stated in-cell, never "/25" · roster tag (`new` / `left-sample` from union-roster diff) · generated one-line **verdict** ("Drifting · +2.1% vs last reset · #3→#3 · in 18 of 41 comps"). Board header prints its own fixed windows: "This reset (Aug 20–25, n=60,197) vs last reset (Aug 13–19, n=…)" — bucket 0 vs first bucket >0, independent of the compare axis, dates always printed. Inline confound banners (window overlap, gear/filter truncation). Thin-B deltas greyed `.na`, never blanked. Under Archon: whole board greyed with "movement unavailable in Archon replica" (E15 rule). Single bucket early-season: Δ/rank-change columns grey "no baseline yet", top-now still renders.
6. **Overview** (unchanged section): sortseg (labels unchanged: Highest/Lowest/Name/Gained/Lost; gain/loss visible whenever any compare axis is on — Δ vs the active ghost) · metric tabs · bar chart (≤960px, top 40, shade/tick/inlay contracts unchanged) · caption. Skill axis renders through the identical compare pipeline: ghost bar = Lens B value, Δ badge, ranks anchored to Lens A; caption prepends "Solid: pA DPS · ghost: pB DPS · badge: change vs pB."
7. **Trajectory** (peer section, promoted from Trend tab; sits after Overview — veto-safe since Pulse also carries top-now): metric seg (avg/med/adeaths/deathless/chars; med label lens-tracked) · **normalization seg `DPS | Rank | Share`** (E17: rank-over-time and share-of-population-of-parses over time, computed from the same weekly bags) · view seg ☰ Overlay (default, unchanged) / ▦ Grid · **"Sort: Metric | Slope"** toggle (metric default = today's ranking; slope = season linear fit at lens) · tuning-patch markers (vertical hairlines from `d.tuning`, hasTune-gated) · truncation flags on filter-shortened ranges · overlay/grid/spotlight/pin/tooltip contracts unchanged. The Trend TAB button remains in `#tabs` and scrolls to/activates this section (no ghost pseudo-tab styling; it is the same real tab id, relocated target).
8. **Breakdown** (expanded, unchanged position): all current columns + **"p30↔p85 spread"** column (qp(.85)−qp(.30) per group, % of p85; fixed percentiles, not lens-tracked) — its two sort directions ARE "Punishes most / Forgives most" (header title says so). Sort contracts, NaN-to-bottom, compare column variant unchanged.
9. **Set Bonus Gain** — unchanged in full (pool, gain stat, thresholds, NaN-parked sorts, caption substance + live numbers, hasTier gating, wrap-headers).
10. **Top Comps** — unchanged in full (strength model, compMin, cap-25-after-sort, live-number caption, self-contained re-render).
11. **Footer**: methodology text, `#foot-src`, reset-weeks note, `#built`, llms.txt · llms/ links + **data.json.gz link** (footer only — no data links in sticky surfaces).
12. **Docked pin inspector** (fixed bottom rail: inert full-width positioner, inner box max-width ~1116px measure-aligned): click a bar / Pulse row / Breakdown row to pin a **spec card** (≤3): tipHTML stats at current lens + verdict line + spread + presence; ✕ per card; cards re-render on every state change; hover tooltips unchanged alongside.
13. `#upd-toast` + ETag poller IIFE — unchanged, plus the Archon-exit toast reuses the same visual frame (§5.3).

### 2.4 Default state after load (all current defaults preserved)
p50 lens · Compare Off · weeksA={0} · role {DPS} · timed-only (when hasTimed) · t4 on · keys six-wide band ending kmax−1 · minchars 250 · compmin 20 · tab "med" · trendMetric "avg" · trendView "lines" · sort desc · all sections expanded (subject to persisted collapse state) · Pulse expanded, sorted by DPS-now · normalization seg = DPS · trajectory sort = Metric · inspector empty.

## 3) Control architecture

### 3.1 Groups (every existing control, placed)
| Group | Home | Controls (existing ids kept) |
|---|---|---|
| Lens | Lens Bar | `#pctl` slider (in popover) + p30/p50/p85/★Me chips (new), `pctl-v` readout |
| Compare | Lens Bar (+ SCOPE·When mirror) | Compare seg (new) ⇄ `#cmp`; new `state.pctlB`; `#quickcmp`, `#presetA/B`, `#f-weeksA/B` in SCOPE·When |
| Archon | Lens Bar chip | `#archon` |
| SCOPE·Who | Sidebar | `#f-cls #f-spec #f-hero #hero-box #merge #f-role #melee #ranged` |
| SCOPE·Where | Sidebar | `#f-dun #klo #khi #key-fill #keys-v #f-reg` |
| SCOPE·When | Sidebar | period A/B blocks as today (`#blockB` shown iff Time axis) |
| COHORT | Sidebar | `#tierbox → #t0 #t2 #t4` as chips, `#tierhint` |
| TRUST | Sidebar | `#timedseg` (relocated), `#minchars`(+`-v`,`-eff`,`-help`), `#compmin`(+`-v`) |
| ⚗ LAB | Sidebar | `#tunebox/#posttune/#tunehint`, `#projbox/#projcb/#projhint` re-framed as manifest entries |
| View | Overview / Trajectory | `#sortseg #tabs #trendseg #trendview` + new normalization seg, slope toggle |
| Sections | Main | four collapse headers + new `pulse` header; static +/− markers, persisted `wowlogs.collapsed` |
| Tables | In place | header-sort state `tsort/ssort/csort`, trend pins |

Hard rule (E01 graft): **every element id above survives**; regrouping is DOM relocation over unchanged compute paths. No new view/tab system.

### 3.2 Birth rule
Adding a loose checkbox to SCOPE/COHORT/TRUST is FORBIDDEN. Every transient feature enters as a `LAB_FEATURES` manifest entry and renders as a LAB frame.

### 3.3 LAB_FEATURES manifest (transient home + lifecycle)
Declarative array; one entry = `{id, name, status: active|dormant|retired|graduated, gate(has*), control, scopeLineFragment, nImpact(), exemptions[]}`. The frame renders: name · status · generated scope line · live n-impact ("N parses affected") · its control. Lifecycle: **entry** = add manifest object (auto: frame + period-note badge + amber section stamps + exemption notes); **dormant** = greyed labeled row, visibly distinct from removed; **graduated** = control re-homed to a permanent group, one-line status row remains for a season; **retirement** = delete the entry (frame, badges, stamps all disappear). localStorage namespaced `wowlogs.lab.*`. Launch entries: `posttune`, `projtuning` (both currently dormant on the live payload), `gearcohort` (status graduated). Amber ⚗ stamping: every section whose data an ACTIVE lab filter touches gets an amber LAB badge inside its scopeLine.

## 4) Migration map — every checklist item

| # | Item | V2 disposition |
|---|---|---|
| 1 | Single-file, fonts-only external | Unchanged; Google Fonts set becomes Marcellus + Inter (Cinzel dropped) |
| 2 | Title rewrite on load | Unchanged |
| 3 | Favicon/meta | Unchanged |
| 4 | Theme tokens | Reskinned to Candlelit Ledger v2 tokens (warm graphite ground, champagne accent, radii 6/4); focus-visible + selection accents kept |
| 5 | Grid 300px/1fr, sticky sidebar, ≤900px collapse | Unchanged (Lens Bar adds a grid header row spanning both columns) |
| 6 | 1200px measure, 960px chart | Unchanged (owner prefs 1–2) |
| 7 | Loading / failure states | Unchanged; Lens Bar renders inert until data |
| 8 | SEASON config | Unchanged |
| 9 | loadJSON gz-first + no-cache | Unchanged verbatim |
| 10 | Payload shape | Unchanged |
| 11 | MELEE/RANGED sets | Unchanged, exact membership |
| 12 | CLASS_COLORS | Unchanged |
| 13 | clsSpecs/csHeroes cascades | Unchanged |
| 14 | has* feature flags | Unchanged, except hasTune/hasProj absent now shows dormant LAB row instead of nothing (Risk R5); all compute gating identical |
| 15 | Selection pruning across sources | Unchanged |
| 16 | Per-load defaults + DEF snapshot | Unchanged, byte-for-byte |
| 17 | state defaults | Unchanged; adds pctlB=85, pulseSort, trendNorm="dps", trajSort="metric", pins[] — all new keys, no existing default touched |
| 18 | Reset buckets / rbucket / usB0 | Unchanged; Pulse + roster tags read the same buckets |
| 19 | periodCut/periodPass | periodPass semantics preserved; inert dayCut plumbing kept |
| 20 | RUNS build | Unchanged |
| 21 | fmtDay/fmtDur/fmtInt/esc | Unchanged |
| 22 | Resize re-render on Trend | Extended: re-renders when Trajectory section in lines view (same condition, section-based) |
| 23 | Reset = reload | Unchanged, top of sidebar |
| 24 | Side-labels | Relabeled SCOPE/COHORT/TRUST/⚗LAB (+Who/Where/When sub-labels), same styling contract |
| 25 | details collapsibles, Role open | Unchanged behavior; chevron → static +/− swap (owner pref 3) |
| 26 | .cnt counts ×8 | Unchanged |
| 27 | chipGroup mechanics | Unchanged; chips restyled --r1 rectangles |
| 28 | Empty set = no filter | Unchanged |
| 29–30 | Class/Spec cascade | Unchanged |
| 31 | Hero box hide on merge | Unchanged |
| 32 | Merge semantics | Unchanged incl. no-rescale comment |
| 33 | Switch visual identity | Restyled per skin (pill switch kept as sliding switch, radii softened); semantics unchanged |
| 34 | Archon switch + title | Control relocated to Lens Bar chip, same input id/label/hint; title text: Risk R6 |
| 35 | Archon ON snapshot/apply | Unchanged, byte-identical |
| 36 | applyArchonState single writer | Unchanged; additionally syncs Lens chips/Compare seg/Pulse grey |
| 37 | Archon OFF restore | Unchanged incl. hardcoded fallback |
| 38 | Derived checkbox / auto-uncheck keeps changes | Unchanged + additive exit toast with "Restore my pre-Archon filters" replaying still-held archonPrev (§5.3) |
| 39 | __archonMatches predicate | Unchanged (Skill axis counts as compare → disabled under Archon, predicate untouched) |
| 40 | elite path selection | Unchanged |
| 41 | aggregateElite window/bypasses | Unchanged verbatim |
| 42–43 | Elite floors / <50 drop | Unchanged |
| 44 | Elite p85 pin, NaN ratings | Unchanged |
| 45 | Elite period-note line | Text preserved, rendered inside the generated Archon scope card AND period note |
| 46 | Min-chars skipped in elite; -eff cosmetic | Unchanged; scope card greys the min-chars line (bypassed-control greying) |
| 47 | Role chips | SCOPE·Who, unchanged |
| 48 | Melee/Ranged XOR | Unchanged |
| 49 | Tier row + defaults | COHORT chips wrapping same inputs; t4 default ON; hint kept |
| 50–51 | setBucket / tierPass | Unchanged |
| 52 | tierhint computation | Unchanged |
| 53 | Dungeon chips | SCOPE·Where, unchanged |
| 54–55 | Key readout + dual slider | Unchanged (thumb radius 4px per skin) |
| 56–57 | presetA + titles | SCOPE·When, unchanged |
| 58 | Custom weeks A | Unchanged |
| 59 | refreshPeriodUIs | Unchanged |
| 60 | #cmp setCompare | Unchanged engine; checkbox mirrored by Compare seg "Time"; setCompare also exits Skill axis (XOR) |
| 61 | Compare defaults A={0}/B=prev | Unchanged |
| 62 | quickcmp chips | Unchanged + fires programmatic-change notice line |
| 63 | Period B ghost panel | Unchanged; shown only on Time axis |
| 64 | describePeriod | Unchanged; also feeds scopeLine() |
| 65 | Min-chars slider | TRUST, unchanged |
| 66 | -eff annotation | Unchanged |
| 67 | Threshold scaling contract | Unchanged (refChars/DEF/effMinFor/effMinA/B) |
| 68 | minchars-help prose | Preserved verbatim |
| 69 | pctl slider + hint | Lives in Lens popover; range/step/default/hint unchanged |
| 70 | pctl oninput TABS mutation | Unchanged mechanism; additionally syncs lens chips, Pulse header, inspector cards |
| 71 | Relabeling reach | Unchanged + new surfaces: Pulse "DPS @ pN", skill-ghost caption, inspector rows (spread column stays fixed p30/p85) |
| 72 | qp math | Unchanged; Skill axis = second qp() at pctlB on same sorted arrays |
| 73 | compmin slider | TRUST, unchanged; also the Pulse presence denominator source |
| 74 | Region chips | SCOPE·Where, unchanged (rbucket note kept) |
| 75 | Bottom hint | Unchanged |
| 76 | Hero title/sub | Wordmark → Lens Bar in Marcellus; `#hero-sub` stays atop main; glow dropped per accent budget |
| 77 | timedseg | Relocated to TRUST; ids, default, no-op-on-active, filter semantics unchanged |
| 78 | posttune | LAB frame; id/default/filter/disable-under-proj unchanged; dormant row when !hasTune |
| 79 | projcb + projection contract | LAB frame; projMul/projSkip/force-toggle unchanged; dormant row when !hasProj |
| 80 | projDelta badges everywhere | Unchanged; extends to Pulse/inspector values |
| 81 | Four KPI cards (period A) | Unchanged values/ids; full cards in flow + compressed duplicates in sticky strip (single source render) |
| 82 | KPI styling | Reskinned (flat surface, hairline, champagne top accent kept as 1px rule) |
| 83 | period-note assembly | Unchanged order/clauses |
| 84 | Overview header/sub | Unchanged; sub becomes scopeLine(overview) containing the same hover hint |
| 85 | sortseg | Unchanged labels (veto: no Best/Worst relabel); gain/loss shown when Time OR Skill axis on |
| 86–87 | TABS array + mini-contracts | Unchanged; Trend tab button retargets to Trajectory section (same data-tid) |
| 88 | Rank-before-sort | Unchanged; Skill axis ranks anchor to Lens A |
| 89 | CHART_MAX 40 + gain/loss sort | Unchanged; sentinels apply to whichever ghost B is active |
| 90 | Shared x-scale + headroom | Unchanged; Skill-ghost value participates as B |
| 91 | Bar shading/gloss | Shading kept; gloss gradient flattened per skin rule 5 (flat fills) |
| 92 | Bar label + inline span trigger | Unchanged (owner pref 1) |
| 93 | Inlays | Unchanged |
| 94 | Grey tick | Unchanged (suppressed under any compare, as today) |
| 95 | Compare chart mode | Unchanged; identical for Skill ghost |
| 96 | .bval anchoring | Unchanged |
| 97 | Tooltip attach/position-once | Unchanged verbatim (owner prefs 1, 7) |
| 98 | tipHTML content | Unchanged; Skill axis reuses A/B column form labeled pA/pB; same markup feeds inspector cards |
| 99 | Empty chart state | Unchanged text incl. effective threshold |
| 100 | Chart caption + coverage | Unchanged; Skill-axis prefix variant added |
| 101 | Compare join (A-side iteration) | Unchanged for Time; Skill axis joins on identical group set (same aggregation) so rule is trivially satisfied |
| 102 | Trend tab switch behavior | Recast: Trend tab activates/scrolls to Trajectory peer section; sortseg/chart untouched (they live in Overview); Breakdown metric fallback to avg kept while Trajectory is the "active tab" |
| 103 | Trend metric picker | Unchanged (default "avg"; Archon sets "med") |
| 104 | Overlay/Grid picker | Unchanged, Overlay default (veto: no flip — parked R2) |
| 105 | Trend data pass (season-wide) | Unchanged; also feeds Rank/Share normalization + slope + Pulse sparklines |
| 106 | Trend threshold + TREND_MAX 16 | Unchanged |
| 107 | Trend empty states | Unchanged |
| 108 | Weekly→daily fallback, MINP | Unchanged |
| 109–110 | Overlay SVG + edge labels | Unchanged; + tuning markers (hasTune) and truncation flag glyphs |
| 111 | Spotlight/pin/dblclick | Unchanged (trendPin persists) |
| 112 | Trend point tooltips (cursor-follow) | Kept as-is (existing behavior; noted vs owner pref 7 in R7) |
| 113 | Trend caption | Unchanged base + one sentence for normalization/slope mode when active |
| 114 | Grid view | Unchanged |
| 115 | Breakdown sub | Generated by scopeLine(breakdown); same substance (threshold, click-to-sort, compare variant) |
| 116–117 | Breakdown columns (both modes) | Unchanged + appended "p30↔p85 spread" column (non-compare mode); Skill-compare columns = pA/pB/Δ via same machinery |
| 118 | Table sorting rules | Unchanged; spread column sorts both directions = Punishes/Forgives |
| 119 | All rows, sticky header, tblwrap | Unchanged |
| 120 | Projection badges in table | Unchanged |
| 121 | Set Bonus hide-when-!hasTier | Unchanged incl. display:"" / [hidden] interplay |
| 122–130 | Set Bonus pool/gain/shares/columns/NaN-last/caption/wrapping | Unchanged in full; percentile-dynamic headers keep tracking the lens |
| 131–138 | Top Comps (sub, pool, strength, qualification, columns, comp cell, live caption, self-contained re-render) | Unchanged in full; Pulse presence column reads the same qualifying-comp set (denominator = its size) |
| 139 | Footer + llms links | Unchanged + data.json.gz link added (footer only) |
| 140–141 | Toast + ETag poller | Unchanged verbatim |
| 142 | Section collapse toggles | Unchanged mechanics + Pulse section joins; chevron → static +/− and tick-length state per skin rule 1 |
| 143 | Collapse persistence, try/catch | Unchanged ("pulse" id added to the array vocabulary) |
| 144 | Header accent hover | Recast to skin: 56px champagne tick (open) → 20px dimmed (closed); no growth-on-hover (owner pref 7) |
| 145 | render() single pipeline | Unchanged spine; inserts renderPulse (after aggregate, before chart) + renderInspector + notice-slot refresh; every surface refreshes per state change |
| 146 | rowPass order/semantics | Unchanged |
| 147 | Aggregate group stats | Unchanged; +qp30/qp85 for spread and pctlB pass reuse the same sorted arrays |
| 148 | p0/p99 edges | Unchanged |
| 149 | TABS med-label live mutation | Unchanged mechanism, extended to lens chips/Pulse/inspector |
| 150 | State survival across re-renders | Unchanged; pins/pctlB/pulseSort/trendNorm join the survive-list; buildControls keeps reusing state sets |
| 151 | Sort defaults + fallbacks | Unchanged; Pulse sort validated the same way (default dps-now desc) |
| 152 | fmtInt/fmtDay everywhere | Unchanged, incl. all new surfaces |
| 153 | Chart/table/tooltip agreement | Unchanged; Pulse + inspector built from the same gated rows |
| 154 | projSkip both-sides | Unchanged, incl. Pulse/spread/slope passes |
| 155 | q50 vs qp distinct | Unchanged |
| 156 | esc() XSS discipline | Unchanged, incl. verdict lines/scopeLine fragments |
| 157 | Optional-payload degradation | Unchanged compute gating; UI note in R5 |

Nothing dropped. Only behavioral deltas: items 14/78/79 (dormant LAB rows), 22 (resize condition recast), 33/91/144 (skin), 34/76/77 (relocation), 85 (gain/loss under Skill axis), 102 (tab→section), 115 (generated sub) — each additive or a sanctioned recast.

## 5) Interaction contracts

### 5.1 Control-change visibility
Every state change re-runs the single render() pipeline; every visible surface (chart, Pulse, Trajectory, tables, KPIs, strip, scope chips, scopeLines, inspector cards, captions) refreshes together. scopeLine(section) is the ONLY source of section evidence sentences — hand-written scope prose is banned; a filter appearing/retiring updates every sentence automatically; active LAB filters stamp an amber ⚗ badge into each touched section's line. Programmatic rewrites (quick-compare, Archon enter/exit, lens chip) announce themselves in the strip's one-line notice slot. Sidebar `.cnt` counts and preset highlighting update on every chip click as today.

### 5.2 Compare mode (one engine, two axes)
- `Off`: no ghosts, ticks + inlays shown, gain/loss hidden, sort reset to desc if it was gain/loss (existing setCompare rule).
- `Time`: exactly today's compare — #cmp on, blockB visible, A={0}/B={prev} defaults, grey ghost = period B, Δ badge, effMinA/effMinB scaling, A-side join, Breakdown A/B/Δ columns, KPIs stay period A.
- `Skill`: #cmp stays off (Archon predicate unaffected); ghost = qp(pctlB) on the SAME aggregation (one pass, two quantiles); ranks/order anchored to Lens A; Δ badge vs pB; Breakdown shows pA/pB/Δ; caption states both percentiles; blockB hidden.
- XOR always; switching axes stores and restores the other axis's B state (weeksB / pctlB). Pulse never re-reads from either axis — its windows are its own and printed on the board.

### 5.3 Archon entry/exit
Enter (chip on): `archonPrev = snap()` then `applyArchonState(replica)` — exact current recipe (p85, elite, full keys, all weeks, timed off, tiers off, merge, compare off, med tab, DPS role). Renders the ONE generated scope card; Lens chips show a "fixed p85" tag and the slider popover + Skill axis + Compare seg disable; Pulse greys with "movement unavailable in Archon replica". Explicit chip off: wholesale archonPrev restore (fallback defaults kept verbatim). Auto-uncheck: any covered setting diverging clears elite, keeps the user's changes (no restore), AND fires a visible toast — "Archon mode off — a setting moved. [Restore my pre-Archon filters]" — whose button replays the still-held archonPrev via applyArchonState; archonPrev is released only on explicit-off restore, toast-restore, or next snapshot. Predicate, elite aggregation, eliteHidden messaging: byte-identical.

### 5.4 Percentile relabeling
Lens change relabels every DPS reading everywhere: med tab + trendseg buttons (live TABS mutation), chart inlay/caption `{med}`, tooltip rows, Breakdown pN header, Set Bonus three DPS columns + caption, Pulse "DPS @ pN" header + verdicts, inspector cards, Skill-axis captions. p0 = "worst parse", cap 99. The spread column alone is pinned p30/p85 by name. Elite mode pins p85 as today.

### 5.5 Defaults
NO current default changes: p50, compare off, weeksA {0}, B first prev bucket, timed-only, t4 on, role DPS, merge on, minchars 250, compmin 20, key band, tab med, trendMetric avg, trendView lines (Overlay), sort desc, sortseg labels, all table sort defaults, Role-details open. Flagged deviations (Overlay→Grid flip, Best/Worst relabel) are PARKED as reversible one-liners in §7 — never shipped without owner sign-off. New controls' defaults are inert (Compare Off, ★Me hidden, inspector empty, LAB entries mirror today's has* behavior at the state level).

## 6) What's new (each judged-justified; all client-side from the existing payload)

1. **Lens Bar** with p30/p50/p85 chips + popover slider — E27 thesis (winning premise); pure UI over existing `state.pctl`.
2. **★Me saved preset** — E27 thesis (judges grafted it into E01 and E19 too); localStorage, hidden until saved, p50 default untouched.
3. **Compare: Skill axis** — E27 thesis; second `qp()` on already-sorted per-group arrays; renders through the existing ghost/Δ path.
4. **p30↔p85 spread column + Punishes/Forgives sorts** — E27 thesis + owner pref 8 (self-eval, main "moderately hard"); two extra quantile reads per group.
5. **Pulse board** with Δ-at-lens, rank change, top-now, sparklines, inline window dates/n/confounds, greyed thin-B — E27 thesis + veto #4 satisfied via rank-sorted top-now; computed from rbucket-split aggregations already available.
6. **Union-roster stability tags (new / left-sample)** — E27 thesis; set-diff of group keys across the two Pulse windows.
7. **Generated verdict lines** on Pulse rows and inspector cards — E15 graft; string assembly from computed stats, all-qualifying denominator.
8. **Comp-presence with stated all-qualifying denominator** — E01 graft (mandatory); count over the existing qualifying-comp set; "/25" banned everywhere (veto).
9. **Trajectory peer section + slope sort + tuning markers + DPS|Rank|Share normalization** — E27 thesis + E17 graft; reuses the season-wide trend pass; markers hasTune-gated; slope = least-squares over weekly points.
10. **scopeLine() generator + amber LAB stamps + programmatic-change notice** — E19 graft; one recipe object → one rendered truth.
11. **LAB_FEATURES manifest** (dormant/retired/graduated) — E01 graft mapped 1:1 onto E27's taxonomy; posttune/projection re-homed, gear cohorts graduated.
12. **Trust-gate "show anyway"** on the "17 of 27 shown" chip: drops effective min-chars to 1 for the session, strip turns amber "trust gate lowered — [undo]" with an undo chip restoring the slider value — ships ONLY with both veto conditions met; otherwise cut, never silent.
13. **Docked click-pin inspector (≤3 cards)** — E27 thesis + design-language signature; renders existing tipHTML data.
14. **Archon exit toast + [Restore my pre-Archon filters]** — E01 graft; replays held archonPrev.
15. **Condensed one-row sticky at ≤1366×768** — E01 graft (mandatory).

## 7) Risks & parked owner questions

- **R1 · Sticky budget.** Lens Bar + context strip risk >80px condensed; the ~80px budget was flagged at grafting. Mitigation: single-row condensation is mandatory at ≤1366×768; if still over, the context strip un-sticks (Lens Bar always sticks). PARKED: acceptable max sticky height?
- **R2 · Reversible option (needs sign-off):** flip Trajectory default Overlay→Grid. One-line change; NOT shipped.
- **R3 · Reversible option (needs sign-off):** relabel sortseg "Highest/Lowest first" → "Best/Worst first" (direction-aware via betterUp). NOT shipped.
- **R4 · Thin-B suppression vs grey** below 3 days of B data (inherited parked question): V2 ships grey + printed warning, never removes. PARKED: suppress instead?
- **R5 · Dormant LAB rows** for hasTune/hasProj=false payloads deviate from today's display:none (checklist 14/78/79/157 UI-level). Reversible one-liner per manifest entry (`hideWhenGated:true`). PARKED: dormant-visible or hidden?
- **R6 · Archon title text** (checklist 34) predates the p85/per-spec-floor implementation. Options: preserve verbatim vs consciously update to match the shipped recipe (single-sourced from ARCHON_RECIPE). PARKED — default: update, since the card/scope line must be truthful and single-sourced.
- **R7 · Trend point tooltips follow the cursor** today (checklist 112) vs owner pref 7. V2 keeps current behavior (no default/behavior change without sign-off). PARKED: switch trend points to position-once tooltips or pin-to-inspector?
- **R8 · Pulse early-season:** single data bucket leaves Δ/rank-change/roster columns grey "no baseline yet"; board still delivers top-now. No owner action needed; noted for launch week.
- **R9 · Skill-axis + gain/loss sorts** ("Gained most" under a percentile ghost reads as "gains most from skill") — labels kept identical to today; if confusing, a Skill-axis-only title tooltip is the reversible mitigation. PARKED: rename under Skill axis only?
- **R10 · ★Me/localStorage** unavailability (private windows): all reads/writes try/catch; chip simply stays hidden — matches collapse-state discipline.
