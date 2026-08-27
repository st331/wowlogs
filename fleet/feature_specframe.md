# Feature contract — the Spec Frame (2026-08-27)

## The owner's request (verbatim-in-substance)
"I like how archon shows you the stats of characters — on this page:
https://www.archon.gg/wow/builds/arcane/mage/mythic-plus/overview/high-keys/all-dungeons/this-week#stats
I want a way that I can see that as well. Maybe all of this is leading towards having
a PINNED CLASS FRAME somewhere that gets activated when I click a bar. Here we can
show information like comps, stats, maybe other things later."
Plus: the in-flight "top comps for this spec" feature must be adjusted to this
context — one design covering both features.

## Reading of intent
- ONE spec frame (not multi-pin comparison — the owner earlier rejected pinning
  multiple classes; this is a single active frame for the clicked spec).
- Activated by clicking a bar (reconcile with the current bar-click -> Data Table
  jump; the frame supersedes or absorbs that gesture — designer decides, explicitly).
- Content blocks now: TOP COMPS (most-run comps containing the spec — the other
  feature folds in here if its winning design fits) and CHARACTER STATS
  (secondary-stat distributions like Archon's #stats). Architecture must make adding
  future blocks trivial (talents, trinkets, gear pieces are plausible next).
- Persona: daily meta checks + main/alt evaluation. Frame answers "who does this
  spec run with, and how are its players statted" in one click from the overview.

## Stats data reality
The published payload does NOT carry character stats today. They were captured into
the gear journal (data/processed/gear.jsonl, weekly-committed as data/gear.jsonl.gz)
since gear collection began. A parallel pipeline change to scripts/build_site_data.py
must aggregate per-spec stat distributions into a SMALL payload block (e.g. quantiles
per secondary stat + n + cohort statement), following the hasTier/hasRating pattern:
the client renders the stats block only when the payload carries it (feature-detect
is NOT a dormant feature; it is how tier/rating already work). Until the first
refresh ships the new payload, the frame shows comps and simply lacks the stats
block — no placeholder.

## Hard constraints (all standing rules apply)
- Comp numbers identical to the Top Comps section / Pulse denominator (one model).
- Stats block must state its cohort inline (window, key range, n, gear-known share).
- Frame is calm and bounded per Gilded Glass: measure-aligned, radii 6/4, no
  rotation, no cursor-following, no full-bleed; close affordance obvious; ESC closes.
- Tooltip contract untouched. All tabular content sortable where tabular.
- No multi-pin, no dormant placeholders, single-file client, section order intact.

## Addition (same day): FLASK filter — scoped to the frame only
Owner: "I want to be able to filter by which flask the spec used — see the stats of
players who used the crit flask, or the vers flask, etc. I don't want this feature
polluting the rest of the dashboard. Keep it scoped only to the spec detailed view."
- The flask filter lives EXCLUSIVELY inside the spec frame's stats block: small chips
  (All / per-flask) that re-slice the stat distributions. It must NOT appear in the
  sidebar, top bar, or any other section, and must not alter any number outside the
  frame.
- Data: flask identity was never collected; the collector must start capturing it
  from the WCL summary combatant info going forward. The stats pipeline aggregates
  per-flask quantiles once records carry it. The frame's cohort line states coverage
  ("flask known for X% of parses in window"); with zero coverage the chips simply
  are not rendered (hasFlask pattern, like hasTier). A quota-priced backfill of
  recent high keys is possible later if the owner asks — not assumed.

## Vision (owner, 2026-08-27): a builds-research suite
"I want to rebuild a lot of the functionality on Archon which allows me to research
how I should build my character based on data from other top runs."
Implications: the spec frame is the front end of a per-spec research surface, and the
pipeline aggregates are its data layer. Design both as a SYSTEM:
- Data layer: generic per-spec aggregation over the gear journal — talents (build
  strings with share + median DPS), gear per SLOT (not just trinkets), enchants,
  gems, stats (shipped), flasks (shipping) — each sliceable by skill band (all vs
  top quartile) and, where cheap, by dungeon (runs are dungeon-keyed). Add new
  aggregates by adding an entry, not a subsystem.
- Frame layer: the block registry renders whatever blocks the payload carries.
- Out of scope without a separate decision: anything needing per-fight event data
  (rotation/cast analysis, cooldown usage) — orders of magnitude more API cost than
  summaries. Everything above comes from data already journaled or newly flowing.
