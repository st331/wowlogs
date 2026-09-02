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

- **INCIDENT 2026-09-02 17:18-~20:00 UTC (22:48-01:30 IST): every refresh failed on
  `NameError: STAMP_FILE`** -- the LLM-export removal cut a line range that also held the
  build-stamp constant main() reads; no suite runs main(), so tests stayed green. The
  watchdog saw two three-minute failures with a recent last success and did not revive
  (75-minute rule), and its cron is throttled to ~4 h. Fixed: constant restored,
  test_build_entry.py runs the entrypoint, the watchdog retries a failure streak <= 3.

- **LLM export REMOVED (owner, 2026-09-02 22:41 IST: "remove the llm export feature.")**
  build_llms(), llms.yml, llms_asset.sh, the refresh unpack step, robots.txt/sitemap.xml
  (the AI-crawler welcome mat) and the footer links are gone. The `llms` GitHub Release
  (prerelease + llms.tar.gz) is an orphan the owner can delete from the Releases page.
  Stage B/C MUST NOT re-add any llms step; the blueprint's llms items are void. The stage B
  worktree branched before this removal -- expect refresh.yml/build_site_data.py conflicts
  at merge; resolve by keeping the removal.

- **Cadence 20 min (owner, 2026-09-02: "start with 1 and then do 2").** Chain pacing
  1800 -> 1200 s; the daily-commit window narrowed to 02:00-02:19 to stay once a day.
  Commit step now refuses to stage any file over 95 MB (GitHub rejects 100 MB at push):
  gear.jsonl.gz is already ~150 MB and had never been committed; the CSV crosses the
  line around end of September. Durable snapshots move to Release assets under (2).
- **PR-1 stage A LANDED 17:09 IST** (merge 2801b15), 17 green chained runs since. Measured
  in production: build.wall_s 238 s (was ~7-8 min), gear pass 104 s single walk over 668k
  records, trait union incremental (0.2 s, 1,305 new records parsed per run), export
  1,365 MB peak RSS. Note: the sample prefilter skips only 6.5% today because the legacy
  payload carries every row (no sampling yet); the flatness comes into play once sampling
  does. export() measured 241 s per run -> export_gear() (two passes over the gear journal,
  an artifact nothing reads between runs) is now gated to commit_export/regear runs.
  Watchdog cron confirmed firing on its own (08:44, 12:59, 17:07 UTC -- throttled to ~4 h,
  hence the failure-wakes-watchdog path). The commit_export dispatch was pre-empted by the
  chain's successor (run 554 cancelled); the commit step now self-heals when the committed
  seed is older than 20 h.
- **PR-1 stage B**: built (B1 foundation, B2a steps 1-3, B2b cubes + wiring), two verify
  rounds: operations lens PASS, equivalence and incremental lenses each hold two blockers
  (rankings overlay when a revised score turns null; pending-file duplication after a kill
  between cache save and unlink; tuning-patch invalidation range). Round 3 fix + verify
  running. Not merged.
- **(2) Partitioned payload + incremental build** -- blueprint LANDED
  (fleet/blueprints/partitioned_payload.md, 1,709 lines, two adversarial revision rounds,
  31 recorded changes). Rows partitioned by UTC day; window = last three resets; four cube
  files per frozen week (exact counts/means/chars/quantile bins); one shard per (spec, day);
  WLP1 typed container; incremental builder in parallel with the legacy one; Release-asset
  journal snapshots. PR-1 (pipeline, dual-emit) is run as THREE STAGES, each merged and
  proven in production before the next starts, nothing touching site/index.html:
    A. freshness + safety now: single-pass gear journal with sample prefilter (legacy
       build flat at ~7 min instead of growing to the timeout by week 8), streaming
       export() with RSS/wall tripwires, llms off the refresh path into its own daily
       workflow. No new artifacts.
       LANDING NOTES for stage A (read before pushing it):
       - /llms/ + /llms.txt now come from the `llms` Release asset, unpacked by
         scripts/llms_asset.sh. The repo has NO Release yet, so the first refresh
         that checks out the new refresh.yml finds no asset and no cached tarball:
         it BUILDS the export inline once (~1-3 min, `llms.unpack=built`), caches
         the tarball, and every later refresh unpacks that (`stale`, <1 s) until
         llms.yml publishes. The tree never leaves the site; the cost is one slow
         Unpack step per runner cache. Still: DISPATCH llms.yml BY HAND right after
         the push (Actions -> "LLM export (daily)" -> Run workflow) -- a freshly
         added schedule can take hours to fire, and until an asset exists a
         cache-evicted runner pays the inline build again.
       - The first llms.yml run does `gh release create llms`, which writes a git
         TAG `llms` at the branch head. A new ref, nothing triggers on it, never a
         branch push. Later runs only replace the asset (`--clobber`).
       - Health lines on build_health.txt: `llms.unpack=fresh|cached|stale|built|
         none`, `llms.built=<UTC>`, `llms.age_h=`, `llms.files=`. `fresh` = the
         download worked, NOT that the data is new: read `llms.built`. Past 36 h
         the refresh prints a ::warning:: -- that is llms.yml having failed for a
         day (its job timeout is 120 min; it owns the O(season) tier pass alone).
       - `/llms.built` is served as a one-line file next to /llms.txt (the stamp
         travels in the tarball). site/robots.txt and site/sitemap.xml stay tracked
         but are overwritten by the tarball on every run; the daily commit stages
         an explicit list, so it can never sweep them in.
    B. partition emission behind the legacy payload: format, builder steps 1-4, manifest,
       sitecalc oracle, fixture, the equivalence + incremental + perf tests, Build step
       running both builders under a deadline.
    C. durability: journal_parts to Release assets, reseed, season pins, clobber guard,
       nightly compare, deploy-site handling of site/d/.
  PR-2 (client, site/next/index.html) needs the owner's answer to blueprint section 11.5
  (trust-gate pool and Trends gate/ranking basis: window vs season). PR-3 cutover, PR-4
  deletion after seven green nightly compares.

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

## INCIDENT — data collection silently dead 2026-08-27 07:27 UTC -> 2026-09-01 (fix pushed 2026-09-01 23:58 IST)

Every refresh run for five days reported success and published the same 638,474
rows (last run dated Aug 27). Root cause: the flask removal (8c89701) took the
CombatantInfo-events argument off `parse_summary` but left the call inside
`fetch_summaries` passing it. Every report fetched since raised `TypeError`,
which the caller catches next to genuine bad-report errors and journals as a
PERMANENT failure. ~57,000 runs were paid for, discarded and marked done.
Nothing was red: the job exits 0, the export still writes, the site rebuilds.
The test suite passed throughout because it called `parse_summary` directly
and never through the caller. Diagnosed from the full run-log ZIPs (the Actions
API returns only a job's tail): the done-set grew 152,853 -> 209,564 between
Aug 28 and Sep 1 while the export grew by one run.
Fix: the summary stage now enters `parse_summary` only through `parse_node`,
which the suite exercises with a caller-shaped node; poisoned FAILED markers are
released on the next start (`--release-failed`, default = this bug's message)
so any run a leaderboard still lists is refetched, newest first; a parse
failure rate >= 50% over >= 20 reports now prints `::error::` and exits non-zero
AFTER the export and journal are on disk, so the chain stops and the run goes
red instead of green for a week. Also fixed in passing: the daily CSV commit
was gated on `event_name == 'schedule'` and chained runs are dispatches, so the
recovery seed sat at Aug 26 all week.
Cost that cannot be undone: runs that have since dropped off their (dungeon,
key) leaderboard (top ~2,000 by score, 20 pages) are unreachable through the
sweep. Recovering them by report code is a separate piece of work (queued).
Backlog drain: the owner then said "remove the cap and get the site updated
asap" -- the sanctioned per-operation relaxation. refresh.yml gained a `drain`
dispatch input: full hourly budget per run, successor chained at the quota
window's reset, self-terminating below a 300-run backlog, after which the chain
is an ordinary 70% run again. The scheduled path never sets it.

## Landed 2026-09-02 — reset bucketing by the INSTANT, not the calendar day

Surfaced by the owner minutes after the US rollover: "25% of the runs of the last
reset happen within a few hours of the reset taking effect". Rows carried only a
UTC day, so the client (and the llms export) could only compare calendar days
against the reset DAY; up to fifteen hours of pre-reset US Tuesday play counted
as "this reset". The payload now carries the UTC start hour (`hr`) beside `day`;
the client buckets in hours when it is present and falls back to days when it is
not (older payloads), and build_llms uses the same instant rule. Verified with a
synthetic `hr`: exactly the rows placed before EU's 04:00 boundary moved buckets,
nothing else changed, no errors. NOTE for reading the site over the next hours:
"this reset" means different date ranges per region (US rolled Tue 15:00 UTC, EU
rolls Wed 04:00 UTC), and the drain fills newest-first, so counts climb unevenly
until the backlog is gone.

## Known open

- **Runs lost off-leaderboard during the outage** (Aug 27 - Sep 1). Their
  `code:fid` keys are in the done journal's released lines but their fight
  metadata (dungeon, key, start time, region) came from the ranking entry and is
  gone with it. A resurrect stage could re-derive that from
  `reportData.report(code){fights(...)}` at ~1 pt per report. Design before
  spending: size the gap first from the next run's "released" vs "to go" counts.

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

## Incident record — 2026-08-27 → 2026-09-02 (read before touching the pipeline)

Three distinct failures, back to back, each invisible to the mechanism meant to catch it:

1. **Silent collection outage, five days (Aug 27 07:27 UTC → Sep 1 23:58 IST).** The flask
   removal changed `parse_summary` to three arguments; the caller kept passing four. Every
   summary raised TypeError, which the caller catches beside genuine bad-report errors and
   journals as a PERMANENT failure. ~57k runs paid for, discarded, marked done. Every run
   green; the site republished the same 638,474 rows. Tests passed because they called
   `parse_summary` directly. Fix: `parse_node` seam under test, poisoned markers released on
   start, a systemic-parse-failure exit that goes RED after the journal is saved.
2. **Runner OOM, seven hours (Sep 1 19:50 UTC → Sep 2 03:27 UTC).** `export_gear()` held
   three copies of every gear row (list of dicts, DataFrame, `to_dict`). The backlog refetch
   pushed it past the runner's memory; the runner was shut down mid-export, the run went
   red, failures do not chain, and the `*/30` dead-man cron fired twice in seven hours and
   failed identically both times. Fix: two-pass streaming export. **Next in line for the
   same failure: `export()` for player rows (still list-of-dicts + DataFrame).**
3. **Reset bucketing by calendar day.** Rows carried a UTC day only, so every US run played
   on Tuesday before 15:00 UTC counted as the NEW reset. Fix: `hr` column, instant-based
   bucketing client and llms side.

Also fixed along the way: chained runs were `workflow_dispatch` events and so skipped the
daily CSV commit gate for a week; push-triggered refresh runs pre-empted queued chain runs
(newest pending wins in the concurrency group) — the push trigger is REMOVED from
refresh.yml; drain runs could time out before chaining.

**The reliability layer (Sep 2):** `.github/workflows/watchdog.yml` runs on its own cron
and watches OUTCOMES: no successful refresh for 75 min with nothing running → dispatch one;
3 h without success, or ≥2 consecutive failures, or the published `newest_row` older than
6 h → open/update a GitHub issue "Refresh stalled" (label `watchdog`), auto-closed when
healthy. `build_health.txt` now starts with machine-readable `built=`, `rows=`,
`newest_row=` and the last run's `fetch.*` counters, so a completed run's fetch story is
readable without the Actions log API (which returns only tails).

## Known open (pipeline)

- **`export()` player rows will hit the same OOM wall** as `export_gear()` did; it holds
  the list of dicts and the DataFrame together (now released early, but the DataFrame
  alone is large). Stream it before the season's row count doubles.
- **Season-long growth.** ~65k player rows/day baseline; the payload, the builder's
  full-CSV pandas load and the sidecar ladder all assume the whole season fits. It will
  not by mid-season. A retention window (the UI already has "Last 2 months") or a
  partitioned payload is needed before then.
- **Off-leaderboard loss from the outage.** Runs from Aug 27–31 that dropped off their
  (dungeon, key) leaderboard before the fix cannot be re-swept; Aug 28 is the worst day.
  Recovering them would need a resurrect-by-report-code stage (the FAILED keys are known).
- **CSV recovery seed** last committed Aug 26; dispatch `commit_export=true` once no drain
  run is pending.
