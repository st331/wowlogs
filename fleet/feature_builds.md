# Feature contract — the Builds deep-dive (2026-08-29)

## The owner's request (verbatim-in-substance)
"I want to be able to see the gear, talents, enchants and stats of the top players
per spec. Extend the character pane so we can SWITCH WORKFLOWS between examining
performance between classes and deep-diving into what toons of a class look like.
Archon is very good at this (example: /wow/builds/retribution/paladin/mythic-plus/
overview/high-keys/all-dungeons/this-week) — look through it for INSPIRATION, don't
copy; it has way more features than I want. Unlike Archon, I want to CHANGE FILTERS
and see the data change. Filterable just like the stats are currently. I care about:
1. GEAR: what the spec is wearing, per slot; which CRAFTED gear and EMBELLISHMENTS
   are used; and a gear OVERVIEW like Archon's #gear-overview (most common gear per
   slot at a glance).
2. TALENT BUILDS per spec. Hero-talent logic: with 'merge hero into spec' ON, show
   hero-spec distribution too; zoomed into ONE hero talent spec, only the other two
   trees are useful — find an intuitive way to show these."

## Non-negotiables
- Everything follows the page filters + the lens window exactly like live-mode stats
  (same row-pass, same ±10-point lens semantics, n printed everywhere).
- Frame gains a mode switch (e.g. Performance | Builds) — no separate page; the
  frame stays the one deep-dive surface. Mode is per-frame UI state, calm, obvious.
- Only the listed features. No rotation, no cursor-chasing, Gilded Glass, bounded.
- Data cost: a SECOND lazy sidecar (builds data), fetched only when the Builds mode
  first opens; main payload and stats sidecar untouched. Target <=3 MB gz, cap 5 MB.
- Item/enchant/embellishment NAMES: journal has ids only. Resolve names at build
  time into a committed, slowly-growing vocabulary (a public static item DB fetched
  by the collector and cached), with plain-id + wowhead-link fallback when a name is
  missing. No third-party runtime scripts on the page.
- Talent builds: identified by talentImportString; top builds per spec with share,
  median DPS, and a copy-import-string affordance. Hero display per the owner's
  logic above (merged -> hero distribution visible; unmerged/hero-zoomed -> the two
  non-hero trees only).
- Crafted/embellishment identification comes from the gear record's bonus ids /
  quality fields as available in the journal — the design must verify what the
  writer actually stores and state what is and is not identifiable.
- The Set Bonus / tier logic, comps, stats block: untouched.
