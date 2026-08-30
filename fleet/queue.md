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

- **Gear slots** — kill the "other / none" headline on every doll slot (root cause: the
  sidecar's degradation ladder halved every vocabulary to 12/20 because the 3.0 MB target
  is too tight against a 5.0 MB hard cap, so the tail is truncated into the "other"
  bucket); and pool rings/trinkets so the two ring spots show the #1 and #2 most-used ring
  and the two trinket spots likewise. Needs a data rebuild once merged.
- **Talent build diff** — pin a build as base, then show additions/subtractions/rank
  changes/choice swaps on the trees. Client-only, no rebuild.

## Specced, queued

1. **Upgrade surface + universal sorting** (fleet/blueprints/upgrade_surface.md) — remove
   per-slot item level; add "Upgrade lean"; make sorting automatic by construction.
   Parts 1 and 3 ship without a rebuild; Part 2 needs sidecar addendum 1.8.

## Rule of thumb learned the hard way

Never run two implementers against site/index.html at once unless their regions are
provably disjoint; merge the first before starting the second. And after any data run
deploys, check the live UI markers — a refresh run publishes ITS checkout's site/, so a
run that started before a UI push will revert the interface when it lands.
