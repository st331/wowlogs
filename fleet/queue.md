# Work queue — owner-set priority. Read this before picking up work.

The owner sets priority explicitly; when they do, it is recorded here verbatim and it
outranks whatever order the work happened to arrive in.

## Standing priority rule (owner, 2026-08-30)

> "implement the refresh change after all other fixes and changes have landed. it is the
> lowest priority fix. if I give other feature requests, prioritize them over the refresh
> change."

**Session persistence (fleet/blueprints/session_state.md) is LAST.** It is designed and
specced, deliberately not implemented. Any other fix or feature — including ones the owner
has not asked for yet — goes ahead of it. Implement it only when nothing else is
outstanding, and never start it while another change is in flight against site/index.html.

## In flight

- **Gear slots** — kill the "other / none" headline on every doll slot (root cause: the
  sidecar's degradation ladder halved every vocabulary to 12/20 because the 3.0 MB target
  is too tight against a 5.0 MB hard cap, so the tail is truncated into the "other"
  bucket); and pool rings/trinkets so the two ring spots show the #1 and #2 most-used ring
  and the two trinket spots likewise. Needs a data rebuild once merged.
- **Talent build diff** — pin a build as base, then show additions/subtractions/rank
  changes/choice swaps on the trees. Client-only, no rebuild.

## Specced, queued (in priority order)

1. **Upgrade surface + universal sorting** (fleet/blueprints/upgrade_surface.md) — remove
   per-slot item level; add "Upgrade lean"; make sorting automatic by construction.
   Parts 1 and 3 ship without a rebuild; Part 2 needs sidecar addendum 1.8.
2. **Session persistence** (fleet/blueprints/session_state.md) — LAST, per the rule above.

## Rule of thumb learned the hard way

Never run two implementers against site/index.html at once unless their regions are
provably disjoint; merge the first before starting the second. And after any data run
deploys, check the live UI markers — a refresh run publishes ITS checkout's site/, so a
run that started before a UI push will revert the interface when it lands.
