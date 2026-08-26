# Build Blueprint — VERSION 4 · "Gilded Lens" (owner-dictated merge)

Chassis: fleet/builds/v2.html (start from this file, byte-for-byte, then apply this doc).
Donor: fleet/builds/v1.html (sidebar groups, comps-header slider, complete Data Table with
fixed p30–p85 spread, LAB manifest + active-modifier badges). v3: reference only.
Contract: fleet/feedback_round2.md (every numbered point mandatory; it sanctions the
deviations cited in §9). Skin: design_language.md WHOLE doc + §GG "Gilded Glass" overrides.
Checklist.md governs everything feedback does not change. Never purple; never Archon-alike
(litmus vs scratchpad/archon_page.png).

## 1) Section order & defaults

Main column, top to bottom (KPI cards + `#period-note` stay above all sections, as in v2):
1. **Overview / Rankings** (`data-sec="overview"`) — sortseg + metric tabs + bar chart +
   caption. ALWAYS first; expanded; the default view. (feedback §Section order 1)
2. **Top Comps** (`data-sec="comps"`) — v1 pattern: `#compmin` slider + `#compmin-v`
   readout sit IN the section header row, right of the title. (feedback 2; L21-22)
3. **Set Bonus Gain** (`data-sec="setbonus"`) — Lab output of the `tier4pc` manifest
   entry, amber 4PC stamp in its sub-line. (feedback 3)
4. **Data Table** (`data-sec="breakdown"`, feedback calls it Breakdown) — v1's COMPLETE
   renderTable ported wholesale, incl. the fixed p30–p85 spread column. (feedback 4, L53-54)
5. **Pulse** (`data-sec="pulse"`) — keep v2's pulse DESIGN (one table: DPS @ lens, Δ,
   rank, season spark, deaths, comps, verdict + its own printed windows/confound notes),
   demoted to fifth. (feedback 5, L25-28)
6. **Trajectory** (`data-sec="trajectory"`) — LAST. No Trend tab anywhere (§6). (feedback 6)

Defaults (all current defaults preserved, checklist 16-17): lens p50 · Compare **Off** ·
pctlB 85 · weeksA {0}, B = first prev bucket · role {DPS} · timed-only when hasTimed ·
t4 on · merge on · key band khi=kmax−1, klo=khi−5 · minchars 250 · compmin 20 · tab "med" ·
trendMetric "avg" · trendView lines · sort desc · table sorts {a_v,−1}/{pt,−1}/{strength,−1}
· pulse sort dps desc · all six sections expanded (persisted collapse honored, checklist 143).
render() DOM order changes only; pipeline spine (checklist 145) untouched, minus tooltip/
inspector calls, plus renderPulse relocated after tables in visual order (compute order free).

## 2) Top bar (sticky Lens Bar) — layout stability + custom B percentile

Keep v2's bar: wordmark · Lens group (p30/p50/p85/★Me chips + `pN +` readout chip opening
the `#lens-pop` popover with the `#pctl` 0–99 slider + ★ save/clear) · `#archon` chip ·
spacer · Compare group. Bar becomes glass per §8.

**Compare group** (right-aligned): label "Compare" + seg `Off | Time | Skill` (v2's engine:
strict XOR, `state.compare`/`state.skill`, per-axis B state kept across switches) + a
**RESERVED SUB-SLOT** immediately right of the seg:
- Fixed-size slot: `min-width` = widest content (the Skill vs-row), fixed height; unused
  content gets `visibility:hidden` (NOT display:none) so the slot always occupies space.
  Result: Off/Time/Skill buttons, Archon chip, and lens chips NEVER move when the axis
  changes. (feedback L45-48; owner: "don't over-index" — one slot, no second mechanism.)
- Slot content by axis — **Off**: empty (hidden placeholder). **Time**: one summary chip
  "B: last reset" (text from describePeriod(weeksB)); click scrolls to sidebar `#blockB`.
  **Skill**: "vs" + chips p30/p50/p85 + a `pB +` readout chip opening a **pctlB popover** —
  exact clone of the lens popover pattern: 0–99 step-1 slider `#pctlb`, readout "vs
  percentile: Nth". Chips highlight on exact match; slider is the custom value. (feedback
  L38-39: chips PLUS custom slider/popover, same as the lens side.)
- Keep v2 rule: entering Skill with pctl≥85 && pctlB===85 flips pctlB to 30.
- pctlB relabel reach: vs-chip readout, chart caption ("Solid: pA DPS · ghost: pB DPS"),
  Data Table pB column header, deaths caption (§4). Archon: axes disabled as in v2
  (setSkill no-op under elite; applyArchonState clears skill).
≤1366×768: bar condenses to one row (v2 behavior); the sub-slot shrinks with it but stays
reserved.

## 3) Sidebar — v1's purpose groups, minus removals

Top: ↺ Reset filters (location.reload()). Groups (v1 order, uppercase micro-headers,
static +/− details markers, Role open, none persisted):
1. **SCOPE** — Class, Spec, "Merge hero talents" switch, Hero Talent (#hero-box), Dungeon,
   Key Level readout + dual slider, Region. (all cascades/.cnt/empty=no-filter unchanged)
2. **WHEN + BASELINE** — `#presetA`, Custom weeks A, `#cmp` switch (canonical Time-axis
   state carrier, synced both ways with the top-bar seg), `#quickcmp`, `#blockB` ghost
   panel (shown iff Time axis), `#posttune` row rendered ONLY when hasTune (feedback #3 —
   nothing rendered when absent; compute contract checklist 78 unchanged when present).
3. **COHORT** — Role chips (open, {DPS}), Melee/Ranged + hint.
4. **TRUST GATE** — Minimum characters slider + `#minchars-eff` + `#minchars-help`
   verbatim, `#timedseg`.
5. **⚗ LAB** — manifest-rendered cards, ACTIVE-GATED ONLY (§7). Today: exactly one card,
   `tier4pc` (t0/t2/t4 chips + `#tierhint`). `proj`/`posttune` manifest entries exist in
   code but render NOTHING while their gate is false. v1's `pctlcmp` entry: NOT ported —
   superseded by the top-bar Skill axis (feedback L37-39). v1's `spins` entry: NOT ported
   (feedback #1). No dormant rows of any kind (feedback #3).
Bottom hint unchanged. **`#compmin` leaves the sidebar** → Top Comps header (feedback L33-34).
All element ids survive; relocation only, compute paths unchanged.

## 4) Deaths under Skill compare (honest percentile reads)

Aggregation (normal path only; elite disables compare): each group additionally collects
`dth[]` = per-parse `R.deaths[i]`, sorted asc once per aggregate; store
`qdA = qp(dth, pctl/100)` and `qdB = qp(dth, pctlB/100)` (fix: base A read uses the lens
pctl, B the vs-pctl). Cost: one small int array per group.
Per-tab Skill behavior (chart ghost + Data Table pB/Δ columns):
- **med tab**: solid = qp(dps,pA), ghost = qp(dps,pB) — v2's existing path, unchanged.
- **Average Deaths tab**: solid = qp(dth,pA), ghost = qp(dth,pB); Δ badge colored by
  betterUp:false (lower better). Caption (mandatory wording substance): "Deaths at the
  pA-th / pB-th parse of each spec's deaths distribution — lower is better. Deaths are
  whole numbers, so percentile reads step." Tab label annotated "deaths @ pN" while Skill
  is on so the solid bar is never mistaken for the average. (feedback L40-43)
- **Deathless % tab**: average-only — NO ghost; caption appends: "Deathless % is a share
  of runs, not a per-parse distribution — the skill compare does not apply here."
  Data Table Δ cell renders "–" na. (sanctioned, feedback L43-44)
- **avg / rating / chars tabs**: same treatment as Deathless — no ghost, one caption
  sentence "the skill compare applies to distribution metrics (percentile DPS, deaths)".
  Honest over clever; scope kept to what feedback mandates.

## 5) Universal sortability — every column, every table

One shared `sortHook(tableEl, cols, stateSlot)` helper: click toggles (first click desc,
repeat flips); strings localeCompare; **NaN/null/"–"/missing parked LAST in BOTH
directions** (Set Bonus's rule, checklist 128, generalized — supersedes checklist 118's
−Infinity coercion; sanctioned by feedback L51-52 "NaN parked last"); sorted header =
accent color + inset overline(asc)/underline(desc), no glyphs; invalid stored column falls
back to the table default each render (checklist 151 pattern).
- **Pulse**: ALL 10 columns get `data-c` (v2 currently only dps/Δ): # rank, Spec (text),
  DPS @ pN, Δ, Rank change (rp−rn; null→last), Season spark (sort key = last−first spark
  point, i.e. net season movement; <2 points → parked), Deaths, Comps (presence count),
  Tag (text; empty last), Verdict (text). `state.pulseSort` validated per render.
- **Data Table**: v1's full column set (§1.4) through the shared helper.
- **Set Bonus**: already compliant; swap to shared helper, behavior identical.
- **Top Comps**: all 12 columns already sortable; adopt NaN/"–"-last both directions
  (Date "–", missing best-time). Cap-25-AFTER-sort kept (checklist 134).
- Overview chart is not a table; sortseg governs it (unchanged).

## 6) Removals and what replaces each need

- **Hover tooltip** (`#tip`, attachTip, tipHTML) — deleted entirely; no floating detail
  surface (feedback #2, L11-12). Replacement: every tipHTML field is a v1 Data Table
  column (Runs, Parses, Characters, Avg DPS, pN DPS, Avg Deaths, Deathless %, ratings,
  spread). Clicking a chart bar or its label span scrolls to that spec's Data Table row
  and applies a 2s background-tint highlight (color-only, calm). Bar hover keeps only the
  `.hot` label recolor.
- **Pinning, everywhere** (feedback #1, L9-10): no pin tray (v1 S-PINS not ported), no
  inspector click-pin cards (v2's inspector rail deleted if present in chassis), no
  dossier. `state.trendPin` + trend click-pin + dblclick-clear also removed (pins classes
  → covered by "pinning certain classes"); Trajectory hover-spotlight (dim others,
  thicken hovered) stays — it is hover, not pinning. Revert note in §10.
- **Trend point cursor-following tooltips** (checklist 112) — removed under feedback #2
  (floating detail surface). Grid cards keep "· VALUE latest" in the title.
- **Dormant rendering** (feedback #3, L13-15): manifest gate false ⇒ render NOTHING —
  no greyed rows, no "awaiting data", no placeholders. Applies to proj, posttune, and any
  future entry. has*-gated UI reverts to checklist-157 behavior (absent = gone).
- **Trend tab** (feedback #4, L16-17): TABS loses the trend entry; no ghost pseudo-tab;
  Trajectory is simply the last section with its own controls (trendseg, trendview,
  trendbox). Breakdown-metric fallback rule retired (table metric = active tab, always);
  resize re-render condition = Trajectory section expanded && lines view (checklist 22
  recast). All other Trend semantics (checklist 103-110, 113-114) unchanged.

## 7) Lab / modifier design — active-only, badges, one-deletion retirement

Port v1's `LAB_FEATURES` declarative manifest: entry = {id, name, badge, gate(),
active(), controlHTML, outputSecId?, exemptions{secId:text}, scopeBits(), nImpact()}.
- **Render rule**: gate() false ⇒ nothing at all (no dormant branch — delete it from
  v1's renderer; feedback #3). gate() true ⇒ Lab card (name · generated scope line ·
  n-impact · control).
- **Active badges (v1's, feedback L57-59)**: `labNoteBadges()` appends amber `⚗ 4PC`
  tags to `#period-note`; `labStamp(sec)` stamps section sub-lines of every touched
  section, incl. exemption text ("⚗ 4PC: tier boxes do not apply here" on setbonus,
  tierhint). Badges render ONLY while gate() && active() (a ticked tier box) — nothing
  shown when off.
- **Entries shipped**: `tier4pc` (owns t0/t2/t4 + tierhint; outputSecId "setbonus";
  exemptions per checklist 123/129), `proj` and `posttune` (gates hasProj/hasTune —
  currently false ⇒ invisible; full checklist 78-80 contracts intact when data arrives).
- **One-deletion retirement of tier4pc, zero residue** (feedback L61-64): every tier
  surface checks `labHas("tier4pc")`: Lab card + t0/t2/t4 + tierhint (manifest-rendered
  — gone automatically), Set Bonus section (`sec-setbonus` render/visibility gated on
  the entry, not just hasTier), period-note tier + single-cohort clauses, section stamps,
  and `tierPass()` (returns true when the entry is absent, so state.tier4=true can never
  filter invisibly). Deleting the one manifest object removes all of it; no empty
  section, no badge, no dormant row.

## 8) Gilded Glass application (design_language §GG over v2's flat skin)

Tokens, spacing, radii 6/4, type scale: unchanged. Changes on the v2 chassis:
- **Panels/KPIs/tblwrap/Lab cards/toast**: add `box-shadow: 0 1px 2px rgba(0,0,0,.3),
  0 14px 30px -24px rgba(0,0,0,.7)` + 1px inset top highlight rgba(255,255,255,.06).
- **Chart bars**: restore `::after` gloss `linear-gradient(180deg, rgba(255,255,255,.22),
  rgba(255,255,255,.03) 48%, rgba(0,0,0,.16))`, pointer-events:none (static — not hover).
- **Lens Bar**: glass — background rgba of --surface2 at ~.82 + `backdrop-filter:blur(8px)`
  with `@supports` solid fallback; hairline bottom border kept.
- **Active elements**: metallic champagne gradient `linear-gradient(180deg,#f2cf76,#d9a83f)`
  on seg `.on`, chips `.on`, slider thumbs, active tab underline; gold-sheen borders on
  active. Ink on metal: dark (--bg0) for contrast.
- **Display face**: Google Fonts link swaps Marcellus → **Cinzel**; Cinzel on the wordmark
  AND `.sec .t` section titles (multiple uses now allowed); Inter for all UI/data.
- **Unchanged hard rules**: nothing rotates, static +/−, color/brightness-only hover
  (gloss is material, not reactivity), content-hugging triggers, ≤1200/≤960 centered
  measure, no cursor-following surfaces, radii 6/4, NO purple, Archon litmus before review.

## 9) Migration map — checklist → v4 disposition (removal cites = feedback_round2.md)

- **Unchanged (contract intact)**: 1-3, 5-21, 23, 26-32, 35-70 (69's slider lives in the
  lens popover per v2 chassis), 72, 74-75, 77, 81, 83, 87-91, 93-96, 99-101, 103-110,
  113-114, 115-117, 119-130 (118 amended: NaN-last both directions, L51-52), 131-138,
  139-141, 142-143, 146-148, 150 (minus trendPin), 151-157. Fonts line of item 1: Cinzel +
  Inter (§GG). Item 4 tokens: Ledger v2 + GG materials. Item 82 KPI: GG shadow + inset lip
  replaces the flat recipe (gold top tick stays deleted per accent budget).
- **Relocated**: 24 (v1 group labels SCOPE/WHEN+BASELINE/COHORT/TRUST GATE/⚗LAB), 25
  (static +/−, per skin), 33 (switch restyle), 49-52 (tier controls → LAB tier4pc card;
  logic verbatim), 71 (relabel reach minus tooltip rows, plus Pulse header, vs-chips,
  Data Table pB column), 73 (compmin → Comps header, L33-34), 76 (wordmark in Lens Bar,
  Cinzel), 78-79 (posttune/proj → manifest entries; UI exists only when gated true),
  84-86 (Overview first; TABS minus trend entry), 92 (label span = click-to-row trigger),
  144 (56/20px tick, no hover growth), 145 (pipeline minus tip/inspector, plus §1 order),
  149 (TABS mutation reach incl. new surfaces).
- **Sanctioned-removed**: 97-98 tooltip attach/content → **feedback #2 L11-12** (details
  = v1 Data Table, §6); 111 trend click-pin/dblclick + 150's trendPin → **feedback #1
  L9-10** (hover spotlight kept); 112 cursor-following trend tips → **feedback #2 L12**;
  86/102 Trend tab + tab-switch behavior → **feedback #4 L16-17** (Trajectory = last
  section); dormant-row rendering of any absent feature (14/78/79 UI level) → **feedback
  #3 L13-15**; 118's NaN→−Infinity direction asymmetry → **feedback L51-52** (NaN parked
  last, both directions); section order of 84/115/121/131 → **feedback L19-29** (reorder
  sanctioned); v2 inspector/pin-tray & ★-independent pin surfaces → **feedback #1 L9-10**.
- **Still-preserved list (feedback L77-81)** honored verbatim: 0-99 relabeling, Archon
  snapshot/restore/auto-uncheck + elite path, hasTier gating, compare defaults, merge,
  timed-only, filters, reset, toast, llms/data links, current-reset default, sample-size/
  date visibility (captions/KPIs/scope lines unchanged).

## 10) Risks

1. **trendPin read**: "pinning, everywhere" interpreted as including Trajectory series
   pins. Revert = re-adding ~15 lines; hover spotlight already preserves the use case.
2. **NaN-last both directions** changes Data Table asc behavior vs checklist 118 —
   sanctioned reading of L51-52; one-line revert per table if owner objects.
3. **Skill on avg/rating/chars tabs** ships caption-only (no ghost) — minimal honest
   scope; extending qp reads to ratings is a future one-liner if asked.
4. **Deaths percentile steps**: deaths are small ints; qp reads plateau (many specs read
   0 at p30). Caption discloses; do not smooth.
5. **Reserved sub-slot width**: sized by the Skill vs-row; verify no wrap at 1366×768
   condensed layout — if tight, the sub-slot drops to a fixed-height second row (still
   position-stable; feedback allows either mechanism).
6. **Glass blur cost** on scroll over 60k-parse charts: backdrop-filter on ONE sticky
   element only; solid fallback path must be tested (Firefox settings, older GPUs).
7. **tier4pc retirement plumbing**: labHas() guards on tierPass/period-note/setbonus must
   be exercised by a test deletion before ship — silent tier4 filtering is the failure mode.
8. **Bar-click → row scroll** is new navigation; keep highlight color-only, no motion
   beyond the user-initiated scroll (owner pref 7).
9. **Archon interplay**: entering Archon must clear skill axis and grey Pulse (v2 already
   does); verify pctlb popover disabled under elite like the lens popover.
10. **Litmus before review**: side-by-side vs archon_page.png — warm ground, champagne
    metal (never purple), Cinzel serif vs their heavy sans, no chevrons, centered measure.
