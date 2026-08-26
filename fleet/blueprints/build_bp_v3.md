# Build Blueprint — VERSION 3 · E19 "Evidence Ledger"

Rebuilt from podium notes after loss of the original. Contract sources: `fleet/checklist.md`
(all 157 items migrated below), `fleet/podium_notes.md` §E19 + Shared build rules,
`fleet/user_prefs.md`, `fleet/design_language_essence.md` (Candlelit Ledger v2 skin assumed).
Base: `site/index.html` (2281 lines, single file). Grafts are demands; vetoes are absolute.

## 1) Design thesis

Trust IS the architecture: every number arrives holding its own evidence — inline grey n,
printed baseline dates, stated denominators, generated confound banners. One recipe object
produces one rendered truth: `scopeLine(section)` writes every scope sentence; hand-written
scope prose is banned, so no section can lie about its filters. The sticky Evidence Strip is
the canonical control surface; the Pulse board answers "is the meta moving" as section #1;
transients live and die inside a manifest-driven LAB frame; detail lives in a docked,
pin-to-compare Spec Dossier — never a cursor-chasing surface.

## 2) Page map (top → bottom, with default state)

**Grid unchanged:** `300px 1fr`, sidebar sticky ≥901px, single column ≤900px. Main column
centered `max-width:1200px`; `#chart ≤ 960px`. Candlelit Ledger v2 tokens: warm graphite
ground, champagne accent, Marcellus wordmark (once), radii 6/4px, static +/− markers, no
rotation anywhere, color-only hover.

### A. Sidebar (aside `#side`) — reduced to Who / Cohort / Where / Quality / LAB
1. `↺ Reset filters` button (unchanged: `location.reload()`).
2. **WHO** — Class / Spec / Hero Talent details (`#f-cls/#f-spec/#f-hero`, counts,
   cascade pruning unchanged); `#merge` switch (default ON); Role details (`#f-role`,
   open by default, {DPS}); `#melee`/`#ranged` XOR pair.
3. **ARCHON** — `#archon` switch + hint (unchanged position, contract byte-identical).
4. **COHORT** — `#tierbox` (t0/t2/t4, t4 default ON, `#tierhint`), shown iff hasTier.
5. **WHERE** — Dungeon details (`#f-dun`); Region details (`#f-reg`).
6. **QUALITY** — `#minchars` slider (250, `#minchars-v`, `#minchars-eff`,
   `#minchars-help` prose preserved verbatim); `#compmin` slider (20).
7. **⚗ LAB** — dashed-border panel rendered from the `LAB_FEATURES` manifest (§3.4).
   Default state: frames for post-tuning filter (checked when hasTune), projection
   (off, hasProj), pctl-ghost-compare (off), pin tray status; dormant rows greyed,
   labeled, visibly distinct from removed.
8. Bottom hint: "Empty selections mean no filter…" (unchanged text).
   All disclosure summaries use static +/− swap (no chevron). Details default closed
   except Role; non-persisting, as today.

**MOVED OUT of sidebar entirely (strip popovers are their ONLY home):** period presets A,
custom weeks A, compare switch, quick-compare chips, block B (presets/weeks B), percentile
slider, key-level dual slider. Timed seg moves from the header into the strip.

### B. Main column
1. **Header** — "⚔️ Mythic+ Performance" (Marcellus wordmark), `#hero-sub`, gold rule.
   Timed seg / tunebox / projbox markup slots relocate (strip / LAB).
2. **Evidence Strip** (sticky, measure-aligned, new `#estrip`) — two states:
   - *Ledger state* (top of page): KPI row — Dungeon runs · Player parses · Groups
     compared · Run dates (ids `#k-runs/#k-parses/#k-groups/#k-dates`) + "N of M specs
     shown" tally; beneath it the chip bar.
   - *Condensed state* (scrolled): 44px chip bar only. **The strip never wraps to a
     second row** (veto). If chips overflow at 1280px the overflowing controls fall
     back to sidebar blocks and the strip shows read-only summary text for them.
   - Chips (each chip IS the canonical control; popover = relocated original DOM,
     same element ids): **Period** ("This reset" / "A vs B" — holds `#presetA`,
     `#f-weeksA`, `#cmp`, `#quickcmp`, `#blockB` with `#presetB`/`#f-weeksB`);
     **Percentile** ("p50" + ★Me slot + "vs p__" ghost affordance — holds `#pctl`);
     **Keys** ("+13–18" — holds `#klo/#khi/#key-fill`); **Timed** (`#timedseg`,
     default "⏱ Timed only" when hasTimed); **⚗LAB** amber chip (only when a
     non-default Lab filter is active); **Archon** state badge (read-only mirror
     of `#archon`, shown only while active).
3. **Notice slot** (`#notice`, one line under the strip) — programmatic-change
   announcements: quick-compare rewrite, Archon auto-uncheck (+ "Restore my
   pre-Archon filters" button), ghost-XOR swaps. Empty by default; 160ms slide, no
   other motion.
4. **Period note** (`#period-note`) — now `scopeLine("page")`: projection prefix,
   post-tuning, timed, tier-cohort caveat, Archon replica note, period sentence —
   same content/order as checklist item 83, generated not hand-written. Under Archon
   it is replaced by the generated **Archon scope card** (§5.3).
5. **§1 Pulse** (new section `data-sec="pulse"`, `#sec-pulse`, open by default) —
   the movers board. One row per spec passing gates; columns: Spec (class dot),
   **Δ% at current percentile** (current reset vs previous reset — the board's OWN
   baseline, both windows' dates + n_A/n_B printed in the board header and per-row
   grey n's; independent of the compare toggle), rank now + Δrank, avg deaths,
   season sparkline (per-week values, class color), comp presence "in K of Q
   qualifying comps" (Q = ALL comps passing compmin — denominator stated, never
   /25), roster-share Δ (share of distinct chars). Sortable columns (default: Δ%
   desc); thin-B deltas greyed `.na`, never blanked; auto-generated confound
   banners above the rows (regional reset-window overlap, filter-truncated ranges).
   Greyed wholesale under Archon: "movement unavailable in the Archon replica"
   (elite path has no period axis).
6. **§2 Overview** (`data-sec="overview"`) — sortseg (5 buttons; gain/loss visible
   whenever a ghost is active — period OR percentile), metric tabs `#tabs`
   (**Trend entry removed** — see Trajectory; no ghost pseudo-tab), bar chart
   `#chart` (rank-then-sort, top 40, shade/tick/inlay contracts unchanged), caption
   ending in `scopeLine("overview")`. Default tab "med", sort desc.
7. **§3 Trajectory** (new peer section `data-sec="trajectory"`, replaces the Trend
   tab) — always-present section: metric seg (`#trendseg`, default "avg"), view seg
   (`#trendview`, default ☰ Overlay), **normalization seg** DPS | Rank |
   Share-of-chars (default DPS = today's behavior), **slope sort** toggle (default
   off = today's ranking), **tuning-patch markers** (vertical hairlines from
   `d.tuning` dates; dormant when payload lacks tuning), truncation flag when
   filters clip the season window. Overlay/Grid renderers, spotlight, pins,
   MINP/fallback rules unchanged. Caption = generated span sentence +
   `scopeLine("trajectory")`.
8. **§4 Breakdown** (`data-sec="breakdown"`) — table unchanged in columns/sort
   contracts; sub-line = `scopeLine("breakdown")`; inline grey n beside ranked
   values. Expanded by default (no collapse seeding of any kind).
9. **§5 Set Bonus Gain** (`data-sec="setbonus"`, hasTier-gated) — unchanged math,
   columns, NaN-parked sorting; caption keeps methodology substance, scope fragment
   generated.
10. **§6 Top Comps** (`data-sec="comps"`) — unchanged model/columns; sub-line =
    `scopeLine("comps")` + "min N runs" (compmin echoed in the header).
11. **Spec Dossier dock** (new `#dossier`) — fixed bottom positioner (inert), inner
    box measure-aligned max-width ~1116px, top corners 6px. Hidden when empty.
    Click-pin ≤3 specs from any bar, Pulse row, Breakdown row, or Trajectory
    series; duel-view ROW-ALIGNED columns (one metric row spanning all pinned
    cards): Runs, Parses, Characters, Avg DPS, pN DPS, Avg deaths, Deathless %,
    ratings (hasRating), comp presence K/Q, roster share. A/B columns appear per
    card while a ghost is active. Hovering a bar previews the spec in a transient
    dock slot (no cursor-anchored tooltip); click pins it. Unpin per card; pins
    survive re-renders (`state.pins`, mirrors trendPin semantics).
12. **Footer** — methodology text, `#foot-src`, reset-weeks note, `#built`, and the
    machine-readable links **stamped with the export date**: "llms.txt · HTML
    tables · data.json.gz — data built {d.built}". Links live here only.
13. `#upd-toast` + ETag poller IIFE — unchanged.

## 3) Control architecture

### 3.1 Groups
| Group | Home | Controls |
|---|---|---|
| Scope (time) | Strip · Period chip popover | presetA, weeksA, cmp, quickcmp, presetB, weeksB |
| Lens | Strip · Percentile chip popover | pctl slider, ★Me save/apply, "vs p__" ghost pin |
| Scope (difficulty) | Strip · Keys chip popover | klo/khi dual slider |
| Scope (validity) | Strip · Timed chip | timedseg |
| Who | Sidebar | cls/spec/hero chips, merge, role, melee/ranged |
| Cohort | Sidebar | t0/t2/t4 (hasTier) |
| Where | Sidebar | dungeon, region |
| Trust gate | Sidebar | minchars, compmin |
| Mode | Sidebar (+ strip badge) | archon |
| LAB | Sidebar dashed panel | posttune, projcb, pctl-ghost, pin-tray status; future transients |
| Per-section | Section headers | sortseg, tabs, trendseg, trendview, norm seg, slope sort, table header sorts, collapse toggles |

### 3.2 Strip popover mechanics
Chips are `<button aria-haspopup="dialog" aria-expanded>`; popover is a focus-trapped
panel anchored under the chip containing the RELOCATED original elements (same ids,
same handlers — DOM relocation on unchanged compute paths; element-id preservation is
a hard requirement). Esc / outside-click closes, focus returns to the chip. Full
keyboard/aria parity with the sidebar widgets they replace (veto). Chip labels
re-render every `render()` from state (period name via `describePeriod`, `dpsLabel()`,
"+lo–+hi", timed state).

### 3.3 ★Me preset
On the Percentile popover: "★ Save as Me" stores {pctl} in `localStorage
wowlogs.me.*`; a ★Me chip appears beside the slider ONLY once saved (hidden until
then); clicking applies it. Opt-in, p50 default untouched, try/catch-guarded storage.

### 3.4 LAB — transient home & lifecycle (E01 manifest, mechanized)
`const LAB_FEATURES=[{id, name, status:"active"|"dormant", gate?, control, scopeFrag,
nImpact, badgeSections, outputSection?}]` is the single source of truth.
- **Frame shape (standard):** name · status tag · generated scope line (its
  scopeFrag) · live n-impact ("affects N parses in view") · its control.
- **Entry:** add one manifest entry → frame renders, scopeFrag joins `scopeLine()`
  for badgeSections, amber LAB chip logic picks it up. **Retirement:** delete the
  entry — frame, badges, scope fragments all vanish. **Dormant** (gate false, e.g.
  hasTune=false): greyed labeled row, visibly distinct from removed.
- **Birth rule:** adding a loose checkbox to Who/Where/Cohort/Trust is FORBIDDEN;
  every new transient enters via the manifest.
- **Amber stamping:** amber ⚗ chip in the strip + amber badge inside
  `scopeLine(section)` of every section a lab filter touches — only when the lab
  state deviates from its payload-blessed default (posttune ON when hasTune is its
  default, so it prints in scope text without amber; unchecking it, or enabling
  projection/p-ghost, stamps amber).
- **Initial entries:** `posttune` (default = hasTune, disabled under projection),
  `projection` (default off; projDelta badge + projSkip contracts unchanged),
  `pctlghost` (§5.2), `pintray` (dossier status row). localStorage namespaced
  `wowlogs.lab.*`, guarded.

### 3.5 scopeLine(section) — one recipe → one rendered truth
Single generator over the recipe object {weeksA/B, cmp, ghost, pctl, keys, timed,
tier, roles, atk, regions, dungeons, who, effMin, lab[], archon}. Emits each
section's evidence sentence (Breakdown sub, Set Bonus scope fragment, Comps sub,
Overview caption tail, Trajectory caption tail, Pulse header, page period note,
Archon card). When a filter appears/retires, every sentence updates itself.
Hand-written scope prose is banned; static help prose (minchars-help, methodology,
footer) is exempt.

## 4) Migration map — every checklist item

| Item(s) | Disposition |
|---|---|
| 1–3 | Unchanged (single file; fonts swap Cinzel→Marcellus per skin — still Google Fonts only; title/favicon/robots unchanged) |
| 4 | Palette re-tokenized to Candlelit Ledger v2 (same warm-graphite/gold family); focus/selection/scrollbars kept |
| 5–6 | Unchanged (grid, sticky sidebar, breakpoints, centered 1200px, 960px chart) |
| 7–10 | Unchanged (loading/error, SEASON cfg, gz-first loadJSON + cache:no-cache, payload shape) |
| 11–15 | Unchanged (MELEE/RANGED sets, CLASS_COLORS, cascade maps, has* flags, selection pruning) |
| 16 | Unchanged defaults; DEF snapshot semantics identical |
| 17 | Unchanged state defaults (every one preserved verbatim) |
| 18–22 | Unchanged (reset buckets, periodPass, RUNS, formatters, resize hook — resize also re-renders Trajectory + sparklines) |
| 23 | Unchanged (reset = reload), stays at sidebar top |
| 24 | Micro-headers renamed to group names in §3.1; style contract kept |
| 25 | Unchanged except chevron → static +/− swap (skin rule 1); Role open default kept |
| 26–28 | Unchanged (.cnt counts, chipGroup incl. ghosted-B variant, empty-set = no filter) |
| 29–33 | Unchanged (Who cascade, merge behavior incl. no-rescale comment, pill-switch CSS re-radiused to 4px) |
| 34 | Archon switch unchanged in sidebar; title text: preserve verbatim (update parked as owner question, §7) |
| 35–45 | Unchanged, byte-identical Archon contract: snapshot/apply/restore/auto-uncheck, predicate, elite path constants & math, elite note; plus new resyncs (strip chips, Pulse grey, dossier) run inside applyArchonState/render |
| 46 | Unchanged (minchars-eff still shows while bypassed; harmless) |
| 47–52 | Unchanged (role chips, melee/ranged XOR, tierbox + buckets + tierPass + tierhint) |
| 53–55 | Unchanged; keys slider relocates to strip Keys popover (same ids/handlers) |
| 56–59 | Relocate to strip Period popover; preset logic, titles, weekLabel/weekTitle, refreshPeriodUIs unchanged |
| 60–61 | `#cmp` moves into Period popover; setCompare + A/B defaults unchanged; gain/loss buttons now also shown under pctl-ghost |
| 62 | Quick-compare chips in Period popover; click behavior unchanged + one-line notice announcing the rewrite |
| 63–64 | blockB with ghosted chips inside Period popover; describePeriod feeds scopeLine |
| 65–68 | Unchanged (minchars slider/eff/scaling contract/help prose), sidebar Quality |
| 69–70 | pctl slider relocates to Percentile popover; oninput contract unchanged + also rewrites chip label, Pulse Δ header, dossier rows |
| 71 | Relabel reach extended: all current surfaces + strip chip, Pulse column, dossier pN rows, ghost badge labels |
| 72 | Unchanged (qp math; elite pins 0.85) |
| 73 | compmin unchanged (sidebar Quality); value echoed in Comps header |
| 74–75 | Unchanged (region chips, bottom hint) |
| 76 | Header kept; wordmark font → Marcellus (skin) |
| 77 | timedseg relocates to strip Timed chip; defaults/no-op/filter semantics unchanged |
| 78 | posttune becomes a LAB frame; default/filter/disable-under-projection/hint unchanged |
| 79–80 | projection becomes a LAB frame; force/disable coupling, projMul/projSkip, projDelta badges, period-note prefix all unchanged |
| 81–82 | KPI cards move into Evidence Strip ledger state; same ids, values, period-A semantics, styling contract (gold accent bar) |
| 83 | period-note content/order preserved, now emitted by scopeLine("page") |
| 84 | Overview section kept as §2 (after Pulse); sub-line updated |
| 85 | sortseg unchanged; gain/loss visibility extends to pctl-ghost; hidden rule now "no ghost" instead of trend-tab (Trend no longer a tab) |
| 86 | Tabs unchanged MINUS the Trend entry (moved to Trajectory peer section — refine decision); default "med" kept |
| 87–101 | Unchanged (TABS contracts, rank-then-sort, CHART_MAX, x-scale, shading, labels, inlays, ticks, compare chart/ghost/delta, bval anchoring, empty state, caption + ratingCoverage, A-side join) — with: tooltip surfaces (97–98) migrate to the Spec Dossier (hover = transient dock preview built from the same tipHTML data; click = pin; trigger zones still bar + label text only, empty space inert); ghost renderer shared by period- and pctl-ghost |
| 102 | Trend-tab switching logic replaced: Trajectory is an always-visible peer section; Breakdown never falls back (tabs always non-trend) — compare metric fallback rule (117) becomes moot but code path kept |
| 103–104 | trendseg/trendview move into Trajectory header; defaults avg / Overlay KEPT (E27 veto honored globally) |
| 105–114 | Unchanged (season-wide pass, calc, TREND_MAX ranking — plus optional slope sort, MINP, fallback, overlay SVG, dash cycling, edge labels, spotlight/pin/dblclick, grid view); trend point tooltips (112) become dock previews (no cursor-following); caption generated with scopeLine tail; new: normalization seg (Rank/Share recompute per-bag before calc), tuning markers layer |
| 115–120 | Unchanged Breakdown contracts (columns, compare columns, sorting incl. NaN→−Inf, no cap, sticky header, projDelta); sub via scopeLine; + p-spread column NOT added (not in E19's graft list) |
| 121–130 | Unchanged Set Bonus (visibility dance, row pool, matched-cell gain math, shares, percentile columns, NaN-parked sort, caption substance + live numbers, wrapping headers) |
| 131–138 | Unchanged Top Comps (inputs, strength model, qualification, columns, comp cell, live-number caption, self-contained re-render); presence figures elsewhere (Pulse/dossier) use all-qualifying denominator |
| 139 | Footer links kept in footer only (veto) + data.json.gz link + export-date stamp |
| 140–141 | Unchanged (toast + hardened ETag poller) |
| 142–144 | Collapse system extended to pulse + trajectory sections; chevron → static − / + marker with 56px→20px accent tick (skin rule 1); localStorage key, try/catch, applyCollapsed timing, header hover accent (color/length only, no growth animation on hover) preserved; NO collapse seeding of any kind |
| 145 | render() pipeline order preserved with insertions: archon sync → aggregates → gates → strip KPIs/chips → notice → scopeLines → Pulse → comps → chart/Trajectory → tables → tier hint → dossier |
| 146–157 | Unchanged (rowPass order, aggregate stats, p0/p99 edges, TABS mutation relabeling, state persistence incl. new state.pins, sort fallbacks, fmtInt/fmtDay, chart/table/dossier agree on one gated rows array, projSkip everywhere, q50 vs qp kept distinct, esc() on all payload strings — incl. new Pulse/dossier/scopeLine surfaces, has*-gated rendering incl. dormant LAB rows and dormant tuning markers) |

## 5) Interaction contracts

1. **Control-change visibility.** Every state change runs the single render();
   strip chip labels, KPI ledger, scopeLines, Pulse, chart, tables, dossier all
   refresh in that pass. Programmatic rewrites of user-visible state (quick-compare,
   Archon enter/exit/auto-uncheck, ghost XOR swap) print one line in `#notice`.
2. **Compare — one ghost system (XOR).** Exactly one ghost may be active:
   *period ghost* (`#cmp`: A vs B periods) or *percentile ghost* ("vs p__" pin on
   the Percentile chip: same period, second qp() pass at p_B; ranks anchor to the
   primary lens so bars never shuffle). Enabling one silently-visibly disables the
   other (notice line). Both use the identical grey-ghost/Δ-badge/A-B-column
   renderer; badge label states the baseline ("vs last reset" / "vs p30").
   Gain/loss sorts operate against the active ghost. **Pulse is exempt:** it always
   computes and PRINTS its own baseline (current vs previous reset, dates + n) —
   no toggle ever invisibly redefines the board.
3. **Archon.** Enter: `archonPrev=snap()` → `applyArchonState(replica)`; generated
   Archon scope card (single-sourced from the recipe) replaces the period note;
   bypassed controls (keys, period, timed, tier, minchars) greyed in strip and
   sidebar; Pulse greys with "movement unavailable in the Archon replica";
   percentile ghost disabled with a "fixed p85" tag. Explicit off: wholesale
   archonPrev restore. Divergence: auto-uncheck keeps the user's changes, drops
   elite, and the notice offers "Restore my pre-Archon filters" (additive replay of
   the still-held snapshot). Contract byte-identical to checklist items 35–45.
4. **Percentile relabeling.** dpsLabel() drives: med tab + trendseg buttons, chart
   inlay/tooltip-data/captions, Breakdown header, Set Bonus columns + caption,
   Percentile chip, Pulse "Δ pN DPS" header, dossier pN row, ghost badge labels.
   Live mutation of the TABS med entry preserved.
5. **Defaults — NEVER changed.** pctl 50 · tab med · sort desc · trendMetric avg ·
   trendView Overlay · norm seg DPS · slope sort off · compare off · ghost off ·
   minchars 250 · compmin 20 · tier4 on · merge on · timedOnly=hasTimed ·
   postTune=hasTune · role {DPS} · weeksA {0} · weeksB {first >0} · keys 6-wide
   top band · sections open · dossier empty · ★Me hidden. Flagged deviations live
   only in §7 as reversible options.

## 6) What's new (each judge-justified, client-side from the existing payload)

1. **Pulse board** — E19 thesis "Pulse board is section #1" + owner's daily
   question; computed from two extra aggregate passes (buckets 0 and 1) + per-week
   value bags already built for Trend.
2. **Evidence Strip** — E19 thesis (KPI ledger → 44px chip bar; chips ARE the
   controls); pure DOM relocation + CSS position:sticky.
3. **scopeLine() engine + notice slot** — E19 "one recipe → one rendered truth";
   string generation over existing state.
4. **LAB manifest panel** — E19 refine graft of E01's LAB_FEATURES; hosts existing
   posttune/projection plus new transients; config + render only.
5. **Trajectory peer section** — refine decision (Trend leaves the tab row) +
   slope sort, tuning markers (`d.tuning`), rank/share normalization (E17 graft);
   all derived from the existing trend pass.
6. **Percentile ghost-compare** — core, not stretch (veto); E06 second-percentile
   pin via a second qp() on the same aggregation; XOR one-ghost rule.
7. **Spec Dossier dock (≤3 pins, duel-view aligned rows)** — E19 thesis + E15
   graft + skin rule 3; built from existing tipHTML row data.
8. **★Me preset** — E27 graft; localStorage only.
9. **Inline grey n everywhere + stated comp-presence denominators** — E19 thesis +
   E01 presence rule (all-qualifying comps, never /25).
10. **Stamped llms/data links** — E19 thesis; `d.built` interpolation in footer.

## 7) Risks & parked owner questions

1. **Movers windows <3 days:** grey + printed warning ships; suppression-vs-grey
   is PARKED as an owner question (per E01 notes; applies to Pulse identically).
2. **Archon switch title text** (item 34) predates p85/per-spec-floor reality —
   shipped verbatim; parked: update wording? (one-string change, reversible).
3. **Dock-preview-on-hover** replaces static tooltips per skin rule 3. Reversible
   fallback if the owner dislikes it: restore the current position-once static
   tooltip (owner hotfix) with the dossier click-pin kept.
4. **Tier boxes → LAB graduation** when the 4pc question retires (owner said it
   will): parked owner question; today they stay in COHORT untouched.
5. **posttune/projection relocated into LAB frames** — placement-only, defaults
   and coupling unchanged; reversible to header toggles in one move.
6. **Strip overflow at 1280px** falls back to sidebar; if real-device testing shows
   the 44px condensed bar too tight for touch, bump to 48px (token change).
7. **Reversible default options (NOT shipped, per veto/sign-off rule):**
   Trend Overlay→Grid default flip; "Highest/Lowest first"→"Best/Worst first"
   relabel; Pulse default sort Δ%-desc could be rank-asc instead (Pulse is new, so
   its default is ours — flagging for the owner regardless).
8. **New localStorage keys** `wowlogs.lab.*`, `wowlogs.me.*`, `wowlogs.collapsed`
   (existing): all try/catch-guarded; page fully functional with storage blocked.
9. **Not shipped (out of E19 scope, avoiding graft-bleed):** E15 scroll-spy slot,
   E06 p-spread column, E15 verdict lines, E27 "show anyway" — they belong to
   V1/V2; adding them here would blur the three-version comparison.
