# Tournament podium — theses + full final-judge notes (preserved from the completed run)

30 entrants, 6 groups × 2 judge lenses → 12 semifinalists → 3-judge semifinal → 6 finalists
→ 3-judge final. Final standings: E01 268 · E27 255 · E19 254 · E15 248 · E17 242 · E06 233.
Checklist references ("92 items", "item 46/57/62-66/72") point at the preservation checklist
regenerated as fleet/checklist.md; the original numbering is approximate after regeneration —
treat named behaviors, not numbers, as the contract.

---

## E01 — "Command Center" (panel synthesis; build as VERSION 1)

### Thesis / page map (from its executive summary)
One page ordered as the owner's decision ladder: is the meta moving → strong now → heading
where → landing where → evidence → raw table. Left rail regrouped by purpose
(Scope / When+Baseline / Cohort / Trust gate / Lab). Main column: header → sticky Command Bar
(compact KPIs incl. "17 of 27 shown" + percentile slider + compare chip + Archon chip;
MANDATORY condensation to one row at laptop sizes) → period note → **Meta Pulse**
(Movers board: Δ at current percentile, rank change, parse-share change, sparkline, baseline
dates printed ON the board, low-confidence deltas greyed-not-blanked; Top-now incl. deaths;
Comp anchors) → Rankings (bar chart) → **Trajectory** (Trend promoted from metric tab to peer
section: slope sort, tuning-patch markers, truncation flags) → Top Comps (compmin in its
header; presence counted over ALL qualifying comps with stated denominator) → Set Bonus Gain
(a Lab output) → Data Table (last, expanded) → footer (llms links + data.json.gz link).
**LAB_FEATURES declarative manifest** governs every transient feature: entry = card + output
section + period-note badge + exemption notes; retirement = delete one entry; dormant = labeled
greyed row (visibly distinct from removed); localStorage namespaced wowlogs.lab.*; birth rule:
"adding a checkbox to Scope/Cohort/When/Trust is FORBIDDEN" — new transients enter via Lab.
ARCHON_RECIPE single-sourced generated scope card + additive "restore" button; archonPrev
snapshot/restore/auto-uncheck contract byte-identical. Hard requirement: keep every element id;
all moves are DOM relocation on unchanged compute paths. No new view/tab system.

### Judge grafts (demands for the build)
- Ship its own two phase-2 Lab candidates AT v1, entering through the LAB manifest (never as
  loose toggles): **pctlcmp percentile ghost-compare** (p-vs-p through the existing period-
  compare ghost/Δ path) and the **S-PINS pin tray**.
- **★Me saved lens preset** (E27): localStorage, hidden until saved.
- **scopeLine() single generator** (E19) for every section's evidence sentence; amber LAB badge
  stamped into the scope line of every section an active Lab filter touches.
- **Scroll-spy section-divergence slot** (E15) in the Command Bar: section-specific caveats
  appear exactly when that section is on screen.
- **Trajectory normalization seg** (E17): DPS | Rank | Share-of-chars, so a nerf reads as a
  fall during gear inflation.
- **p-spread "punishment proxy" column** (E06) in the Breakdown table.
- Generated one-line verdict (E15) for Pulse rows ("Drifting · +2.1% vs last reset · #3→#3 ·
  in 18/25 comps" — with all-qualifying denominator).

### Judge vetoes (must NOT ship)
- No ghost "Trend ↓" pseudo-tab in the metric tab row — the sticky peer section is enough.
- No Breakdown collapse seed of ANY kind (no localStorage seeding; demote by position only,
  expanded; in-memory-only defaults if ever, applied solely when no stored key exists).
- No data/llms links in the sticky Command Bar until each link carries its own build stamp
  matching the page's; links live in footer (+ data.json.gz there).
- Movers deltas when windows overlap or B is thin: grey + print warning, never remove
  (suppression-vs-grey at <3 days is PARKED as an owner question).
- Movers board always prints its own baseline dates — a compare toggle may never invisibly
  redefine the board's numbers.
- The always-two-row Command Bar may not ship: condense-to-one-row is mandatory at ≤1366×768.
- pctlcmp/pins must enter through the LAB manifest sequencing, not as scattered toggles.

---

## E27 — "Skill Lens" (prediction-first; build as VERSION 2)

### Thesis
The percentile slider is promoted to a sticky global header **Lens Bar** (p30/p50/p85 preset
chips + slider popover + ★Me saved preset); the entire page re-reads through the lens.
**One compare engine, two axes — "Compare: Time | Skill"**: generalizes the existing
ghost-bar/Δ/A-B-column renderer so p30-vs-p85 is one click (second qp() pass on the same
aggregation; ranks anchor to Lens A so bars never shuffle); the p30↔p85 spread doubles as a
labeled difficulty/forgiveness proxy ("Punishes most / Forgives most" sorts). **Pulse board
above the fold**: Δ at current lens, rank change, sparkline, comp-presence, both windows'
dates + n + overlap/gear-truncation warnings printed inline; union-roster stability tags
(new / left-sample). Trajectory as peer section with slope sort + tuning markers. Archon
becomes a Lens Bar preset chip with ONE generated scope card; auto-uncheck fires a visible
toast with "Restore my filters" replaying the still-held snapshot; skill-axis disabled under
Archon ("fixed p85" tag); per-axis compare-B state kept and restored on axis switch. Sidebar
regrouped SCOPE / COHORT / TRUST / ⚗LAB (dormant-not-hidden Lab entries; "graduated" status —
tier boxes graduate to permanent COHORT "Gear cohort" chips, hasTier-gated). Sticky context
strip: compressed KPIs + clickable scope chips + "17 of 27 specs shown". Docked click-pin
inspector, ≤3 pinned spec cards.

### Judge grafts
- E01's LAB_FEATURES declarative manifest replacing its prose Lab lifecycle (its
  dormant/retired/graduated taxonomy maps 1:1 onto manifest states).
- E01's comp-presence rule: count over ALL comps passing compmin, denominator stated
  everywhere presence appears (mandatory).
- E01's keep-every-element-id hard requirement + per-item migration granularity.
- E01's Archon "[Restore my pre-Archon filters]" button spec (archonPrev survives).
- E01's mandatory condensed-strip rule at laptop sizes (~80px sticky budget was flagged).
- E19's scopeLine() generated evidence sentences + programmatic-change notice slot
  (quick-compare/Archon state rewrites announced in one line) + amber LAB stamping.
- E15's Archon-Pulse rule: grey Pulse with "movement unavailable in Archon replica";
  E15's generated verdict line for Pulse rows and inspector cards.
- E17's Share-of-population trend normalization complementing slope sort.

### Judge vetoes
- "in N/25 top comps" presence denominator must NOT ship anywhere (the Demo 1-of-25 trap).
- Trend Overlay→Grid default flip and "Best/Worst first" sort relabel must NOT ship without
  owner sign-off → KEEP current defaults; list both as one-line reversible options.
- The context-strip "show anyway" (drops min-chars to 1) must not ship without an undo chip
  and a visible strip-state change — no silent trust-gate bypass.
- Do not place the full Trajectory section above Overview unless Pulse carries a rank-sorted
  "top now" column — the daily top-DPS-now read must not require scrolling past a season chart.

---

## E19 — "Evidence Ledger" (prediction-first; build as VERSION 3)

### Thesis
Trust IS the architecture. A sticky **Evidence Strip** condenses from a KPI-ledger row to a
44px chip bar; each chip IS the canonical control (period/compare, percentile, keys, timed
move OUT of the sidebar entirely — strip popovers are their ONLY home; rule: the strip never
wraps to a second row — if overflow fails at 1280px, controls fall back to the sidebar).
**Pulse board is section #1**: per-spec Δ% at current percentile with n_A/n_B, rank change,
season sparkline, deaths, comp presence over ALL qualifying comps (denominator stated),
roster-share Δ; sortable; auto-generated confound banners (regional-window overlap,
filter-truncated ranges) printed beside the deltas; thin-B deltas greyed (.na treatment),
never blanked. **One recipe object → one rendered truth**: generated scopeLine(section)
everywhere (hand-written scope prose BANNED — when a filter appears/retires every section's
evidence sentence updates itself); generated Archon scope card (contract byte-identical,
bypassed controls greyed, restore-offer notice); amber LAB chip + amber badge stamped into
every section an active Lab filter touches. Dashed-border LAB panel: standard frame shape
(name · status · generated scope line · n-impact · control), dormant rows visibly distinct
from removed, delete-the-frame retirement — mechanized by adopting E01's LAB_FEATURES
manifest (graft). Docked pin-to-compare **Spec Dossier** (≤3 specs) on the click-pin
inspector rail, duel-view row-aligned columns (E15). Inline grey n beside every ranked value.
llms/data links stamped with export date.

### Refine decisions already made (its refine stage completed before the crash — honour them)
All 12 grafts absorbed, none rejected: stretch items promoted to core (tuning markers,
percentile ghost-compare, slope sort, share mode); E01 Movers spec into Pulse (parse-share Δ,
sortable columns); E01 LAB_FEATURES manifest; E01 element-id hard requirement; E01 grey-.na
thin-B rule; E06 second-percentile pin as the ghost-compare implementation with a
one-ghost-system rule (XOR with period compare); E15 duel-view aligned columns for the 3-pin
tray; E27 ★Me preset on the p-chip (opt-in, localStorage, p50 default untouched); E17
rank/share trend normalization; **peer-section Trajectory** (Trend leaves the tab row so
ranking+trend read together — replacing its original [Ranking|Trend] view seg).

### Judge vetoes
- Tuning markers and percentile ghost-compare may NOT ship as unbuilt "stretch" — they are core.
- Strip-popover controls need keyboard/aria parity with the sidebar widgets they replace.
- Comp presence: all-qualifying denominator only (its own Pulse column said /25 — unify).
- Strip never wraps to a second row.

---

## Shared build rules (all three versions)
- Preserve the Archon-mode contract exactly: snapshot (archonPrev) on enter, exact restore on
  toggle-off, auto-uncheck on any divergence from the replica recipe; elite aggregation path
  (per-spec top-decile key floor, p85, last 14 days, bypasses key clamp/period/min-chars).
- Percentile slider 0-99 relabels every DPS reading everywhere (tab labels, captions,
  Set Bonus DPS columns, tooltips/inspector).
- Tier filters gate on gear-visible parses (hasTier); Set Bonus table sorts every column both
  directions with NaN parked last; compare defaults current-reset-vs-last; merge hero talents;
  timed-only; region/role/class/dungeon/key filters; reset-filters; update toast (ETag poll);
  llms.txt + sitemap links; current-reset default period.
- Defaults NEVER change without owner sign-off; park flagged deviations as reversible options.
- Candlelit Ledger v2 skin per fleet/design_language.md (regenerate first if missing).
