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

Nothing. Both the gear-slot work and the talent pass are merged and live.

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
