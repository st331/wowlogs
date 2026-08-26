# Owner feedback — round 2 (2026-08-26). This is the MERGE CONTRACT for version 4.
# Where this conflicts with the checklist, blueprints, or design language, THIS WINS.

## Base
V2 "Skill Lens" (fleet/builds/v2.html) is the chassis — "I like the v2 layout the best.
I like being able to have compare options at the top." Merge in the items below.

## Remove outright
1. Pinning, everywhere: no pin tray, no dossier pinning, no click-pin cards. ("I don't
   care about pinning certain classes.")
2. The hover tooltip: "the tooltip is enormous - that needs to go." No floating detail
   surface at all — details live in the tables (see 12).
3. Dormant features: "I don't want any dormant features, they are useless." A Lab/transient
   feature that is off or lacks data is simply NOT RENDERED — no greyed rows, no
   placeholders. Retirement = delete; nothing lingers.
4. V2's Trend tab that scrolls down to the Trajectory section: "it's useless." Remove the
   tab; Trajectory is just the last section. (Sanctioned checklist deviation.)

## Section order (top to bottom; Overview expanded/visible by default)
1. Overview / Rankings — ALWAYS first, the default view.
2. Top Comps — second most valuable. Keep V1's pattern: the comp-min slider sits in the
   section header, next to the section.
3. Lab features' output (Set Bonus Gain today) — third.
4. Breakdown — fourth.
5. Pulse — fifth ("the meta pulse is the least useful to me"). Keep V2's pulse DESIGN
   (one rank-capable table: DPS @ lens, Δ, rank, season spark, deaths, comps, verdict) but
   at this low position. Rationale: "this entire design assumes more movement in the meta
   than usually happens. early season moves will slow down a lot."
6. Trajectory — LAST ("I will likely rarely use that feature").

## Sidebar
Keep as much of V1's sidebar as possible (its purpose groups: SCOPE / WHEN + BASELINE /
COHORT / TRUST GATE / LAB), minus anything the removals above delete. comp-min moves to
the Comps header per V1.

## Compare system (top bar)
- Keep V2's Compare: Off | Time | Skill at the top — "the ghost compare… really baked in."
- NEW: the "vs" percentile (the B side of Skill compare) must accept a CUSTOM value, same
  as the lens side — chips (30/50/85) plus a custom slider/popover.
- FIX: percentile (Skill) comparison must work on the deaths tab. Implement honestly:
  collect each group's per-parse deaths distribution and show qp(deaths, pA) vs ghost
  qp(deaths, pB), clearly captioned (deaths at the pN-th parse of the deaths distribution;
  lower is better). Deathless % may remain average-only if a percentile read is not
  meaningful — but then its caption must say the skill compare does not apply there.
- Layout stability: top-bar buttons must NOT move when switching Off/Time/Skill — reserve
  space for axis-specific sub-controls (fixed slot or a consistent sub-row) so clickable
  positions stay put. Owner: "wherever possible, do not move the positions of clickable
  buttons… don't over-index on this."

## Tables
- EVERY column in EVERY table is sortable, both directions, NaN parked last: Pulse (all
  columns — currently none sortable), Breakdown, Set Bonus, Top Comps, Data Table.
- The Data Table is V1's complete version ("has all the details you may want, v2's is not
  enough"), including the fixed p30–p85 spread column.

## Modifier visibility
Keep V1's active-modifier indicators (e.g. the 4PC badge in the period note and section
stamps) — "I like how it tells me when modifiers are active like the 4pc filter."
Active modifiers only; nothing shown when off (see removal #3).

## Temporary-feature reality
The 4pc filter is temporary — "once most people have their 4pc this week, this feature
will likely be turned off." Its removal must be one manifest deletion leaving zero residue
(no dormant row, no badge, no empty section).

## Visual direction — "Gilded Glass" (design_language.md §GG overrides v2's flat-only rules)
"I really like glass/metal motifs in my elegant designs. the last visual redesign (before
the site reorganization and this redesign) was much more in the direction of what I liked."
Restore that direction: layered panel elevation with real (soft, layered) shadows, the
glossy bar highlight, metallic gold gradients on active elements, glassy translucent
sticky bars with backdrop blur, Cinzel display face for the wordmark/section titles.
UNCHANGED from v2's rules: radii 6px/4px, nothing rotates, static +/− markers, calm
hover (no grow/glow-on-pass), content-hugging triggers, centered bounded measure
(main ≤1200px, chart ≤960px), no cursor-following anything, NEVER purple / never
Archon-lookalike (Archon = pure black + purple + heavy sans).

## Still preserved (unchanged contracts)
Percentile lens 0-99 relabeling everywhere; Archon mode exact snapshot/restore/
auto-uncheck + elite path; tier filters with hasTier gating; compare defaults
(current vs last reset); merge hero talents; timed-only; filters; reset; toast;
llms/data links; current-reset default; sample-size/date visibility.
