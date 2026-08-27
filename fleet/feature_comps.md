# Feature contract — "top comps for this spec" (2026-08-26)

## The owner's request (verbatim-in-substance)
"One question I want to answer easily is 'what are the top comps that this spec is
running with', where top here means the MOST RUN comps. Add a feature that allows me
to grok this easily. It could be on the tooltip but the tooltip has a lot of data
already, so if it is put there, it would need to be organized better. Consider other
places as well. Make sure my persona will find the feature useful and easy to use."

## Persona (see fleet/user_prefs.md for the full picture)
Top-2% M+ player chasing the 1% title. Daily meta checks; evaluating main/alt specs.
"Top comps for spec X" feeds two decisions: (a) does my main/alt candidate fit the
comps that are actually being run, (b) which teammates/comps should I aim for.
Answer must be reachable in <=2 interactions from a cold page, discoverable without
instructions, and calm (no new hover chrome that fires while sweeping the page).

## Hard constraints
- Numbers MUST agree with the existing comp surfaces: the Top Comps section and the
  Pulse "in K of Q qualifying comps" column. One comp model, one denominator
  (all comps passing the comps min-runs gate). No second computation that can drift.
- If the tooltip carries it: reorganize the tooltip so it stays scannable (grouped
  sections, not one long table). Tooltip behavior contract is untouchable: 450ms
  hover-intent delay, bar/label-text triggers only, position-once, hide on leave.
- Every table column added anywhere must be sortable both directions, NaN parked last.
- No dormant/placeholder states. No pinning. Nothing full-bleed; Gilded Glass tokens;
  nothing rotates; calm hover; radii 6/4.
- Preserve all existing contracts (Archon mode, percentile lens, compare roster
  gating, section order Overview -> Comps -> Set Bonus -> Breakdown -> Pulse ->
  Trajectory, sidebar order with Trust Gate under Key Level).
- Single-file site/index.html, client-only, same payload.
