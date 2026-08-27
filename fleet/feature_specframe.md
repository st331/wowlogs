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
