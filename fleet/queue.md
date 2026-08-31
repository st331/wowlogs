# Work queue — owner-set priority. Read this before picking up work.

The owner sets priority explicitly; when they do, it is recorded here verbatim and it
outranks whatever order the work happened to arrive in.

## SCRAPPED — session persistence (owner, 2026-08-30)

> "scrap the refresh change request"

The owner first asked for filters/view state to survive a refresh, with Reset filters /
Home / Reset site buttons, then set it as lowest priority, then **scrapped it outright**.
Design was stopped before any spec or code was written; nothing was implemented, and no
`session_state.md` exists. **Do not resurrect it** — not as a "small win", not as a
side effect of another change. A refresh continues to open on payload defaults, and the
character screen's `#cs=` URL hash remains the ONLY thing that survives a reload.

If it is ever revived, the trap that made it worth thinking hard about is worth
re-reading: the key-level range default is data-driven (a six-wide band anchored to the
top key anyone has logged), so faithfully restoring a stored range would silently freeze
the page on stale content while looking normal — which cuts against the owner's entire
use of the site. Distinguishing a chosen range from a default-at-the-time one is the
crux of any future attempt.

## In flight

Nothing.

## Landed 2026-08-31

- **Talent diff — visual pass** (fleet/blueprints/talent_visual.md). Prominence treated as a
  RATIO rather than a size: the field goes quiet, the mark moves onto a raised plate BEHIND the
  tile, and direction is carried by position (tick on the plate's top edge for a gain, bottom
  for a loss), mass and polarity (solid chip = present, hollow = gone) and texture (solid vs
  segmented tick), with hue as redundant confirmation only. Icons 44 -> 40px, which roughly
  doubles both gutters. The ghost thumbnail, all three sub-3:1 diff rings, the dashed rim, the
  border-colour mechanism and both opacity tiers are DELETED — the whole "colour the tile's own
  perimeter" family was the measured cause of 24% of marks never rendering (clip-path eats it on
  every choice node). A new #cs-changes strip above the trees names WHICH talent changed.
  Verified by three independent lenses, second round clean. The greyscale gate — name every mark
  with the hue stripped out, no legend — passed on the first round; it is the check both earlier
  attempts at this surface lacked, and it is why they failed.
  One round of blockers was found and fixed: the column and row pitches were still derived from
  the ICON (nd+4 / nd+6), numbers predating the plate, so nine-column specs spent slack down to a
  45px pitch — one pixel inside the 46px plate. Gutters are now derived from the mark set;
  minimum clearance 2.00 -> 3.00px and marks under the 3px floor went 14/100 -> 0, with the icon
  still at 40px (the fix did not buy clearance by shrinking anything).

- **Upgrade surface, all three parts** — per-slot item level gone from the doll, the Upgrade
  lean surface built and shipped DARK (no toggle in the DOM until the sidecar's `iup` field
  arrives in a data run), §1.8 `iup` in the builds sidecar, and universal sorting: `sortHead`
  is now the only path to a `<thead>`, so a table physically cannot ship an unsortable header.
  One shipping blocker was caught by the verify panel and fixed before merge — the div->table
  conversion had moved the fold-out share bar's gradient onto the 92px Share cell, silently
  changing its denominator from the row and compressing the encoding 4.7x (six of twelve rows
  under 4px). It is painted on the `<tr>` again; measured widths now match the old build
  exactly. All four suites pass; live since 12:58 IST.

## Landed 2026-08-30

- **Gear slots** — no tile can headline "other / none"; rings and trinkets pooled so the
  two spots show #1 and #2 most-used; doll rebalanced 7/7. Root cause was the emitter's
  degradation ladder halving every vocabulary (12/20) because the 3.0 MB target was
  unreachable — the lowest rung measures 3.29 MB. Target raised to 4.3 MB against the
  5.0 MB hard cap and the ladder made a staircase. Confirmed live: vocabs back to 24/40.
- **Talent pass** — base-pin diff (adds/drops/rank moves/choice swaps), median DPS made
  legible with a delta vs base, one shared pane geometry, and a legibility round after
  owner feedback (drops no longer near-black, swaps get their own rim, three-tier
  emphasis while a base is pinned).
- **Talent trees + all 1,898 spell icons**, crafted/embellishment identity v2, wowhead
  icon-only tooltips.
- **Two deploy races fixed**: deploy-site can no longer publish data older than what is
  live, and refresh now publishes the newest committed UI instead of its own checkout's.

## Known open

- **Rank-down chip and pip overlap by 1.4px** (3 nodes across 18 specs). Not fixable by nudging:
  the plate is 44px wide and the chip (16px) plus the pip ("2->1", 31px) is 47px, so they cannot
  share one edge. The honest fix is a narrower pip glyph, not a new position. Every alternative
  costed either shrinks the chip (which fights the entire point of the pass) or moves the tick's
  edge assignment (which breaks the greyscale gate).
- **Field quiet misses its stated targets on the HEAVIEST diffs.** Section 6's "under ~6% high-
  chroma pixels" and "marked nodes are the only nodes with chroma > 25" are both measurably not
  met once a pane carries 20+ marks; and the cross-hero `nodx` Hero pane, which opts out of the
  quiet field by design, is now the loudest region on the canvas. All three verifiers judged the
  encoding readable anyway and passed it. Revisit only if the owner says the pane still reads busy.
- **The change strip fits 4 cards on heavy diffs, not the 8 the spec budgeted** (Priest Shadow
  22 changes -> 4 + "+18 more"). The cap became a fit cap rather than a count cap; the count shown
  is true and `.cmore` opens the ledger, so nothing is hidden silently. The spec's stated common
  case (five or fewer changes) is fully answered above the trees.
- **Fit headroom is near zero in 4 of 18 specs.** With the gutter floor raised, the densest specs
  sum to within a few px of availW; a future tree with more columns would drop the icon to 38px,
  which pref #10 forbids. If Blizzard adds columns, raise availW (widen #charscreen) rather than
  letting the loop take another rung.
- **Six specs were never measured** — Evoker Augmentation/Devastation, Druid Restoration, Paladin
  Holy, DK Frost, Shaman Restoration are not reachable through the chart view the drivers used.
- **The Upgrade lean table has never been exercised live.** `leanOK` is false until a data run
  emits the sidecar's `iup` field, so the surface is dark by design and its sorting is untested.
- **Enchants ship empty** (`eslots: []`). Raising the size target restored the item
  vocabularies but the enchant columns are still being dropped by the ladder, so the
  Enchants half of that tab has been silently blank. Diagnose before designing anything
  on top of it.

## Specced, queued

1. **Upgrade surface + universal sorting** (fleet/blueprints/upgrade_surface.md) — remove
   per-slot item level; add "Upgrade lean"; make sorting automatic by construction.
   Parts 1 and 3 ship without a rebuild; Part 2 needs sidecar addendum 1.8.

## Rule of thumb learned the hard way

Never run two implementers against site/index.html at once unless their regions are
provably disjoint; merge the first before starting the second. And after any data run
deploys, check the live UI markers — a refresh run publishes ITS checkout's site/, so a
run that started before a UI push will revert the interface when it lands.

## Standing process rule (owner, 2026-08-31)

> "as soon as the upgrade surface lands, don't wait around, start the implementation on
> the visual pass. I would like all of these tasks finished up and implemented as early as
> possible, without any unnecessary delays."

CHAIN WORK BACK TO BACK. Do not idle between a merge and the next task, and do not wait to
be asked to start the thing that was already queued. When one piece of work is blocked only
by another's exclusive hold on a file, run its DESIGN phase in parallel and have the
implementation staged so it fires the moment the file frees. Pre-write the next workflow
script while waiting rather than after.
The one exception stays: never two implementers on site/index.html at once — that has cost
merge damage twice. Sequencing is the fix, not more parallelism.
