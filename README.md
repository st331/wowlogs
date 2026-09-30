# WoW Mythic+ Performance Dashboard — Midnight Season 2

A data-collection pipeline and interactive dashboard that tracks, aggregates
and visualizes Mythic+ performance for **Midnight Season 2**, built on the
[Warcraft Logs v2 GraphQL API](https://www.warcraftlogs.com/api/docs).

```
scripts/wcl_client.py        quota-aware WCL GraphQL client
scripts/build_hero_map.py    trait-node → hero-talent mapping from SimC data
scripts/fetch_data.py        checkpointed collection pipeline → data/mythic_runs.csv.gz
scripts/execution.py         the execution bundle: five extra tables per gated run + the quota gate
scripts/build_site_data.py   packs the CSV into site/data.json (+ sidecars)
scripts/build_baselines.py   site/baselines.json.gz + site/runs/<hh>.json.gz for the Key Level Logs site
scripts/fetch_abilities.py   per-ability damage breakdown (tuning projection)
scripts/project_tuning.py    re-scores parses under an announced tuning pass
scripts/hero_from_abilities.py  recovers hero talents from the abilities cast
scripts/backfill_keystone.py    tops up keystone clock times for aged-out reports
site/                        static dashboard — index.html + data.json (docs/ mirrors it)
```

## Quick start

```bash
pip install -r requirements.txt

# 1. credentials (either env vars or files under .secrets/)
export WCL_TOKEN="<bearer token>"            # or:
export WCL_CLIENT_ID="..."                   #   client-credentials flow is
export WCL_CLIENT_SECRET="..."               #   used when no token is given

# 2. build the hero-talent lookup (one-off; downloads SimC game data)
python3 scripts/build_hero_map.py

# 3. collect (checkpointed — kill/re-run any time, it resumes)
python3 scripts/fetch_data.py                # sweep → summaries → export
python3 scripts/fetch_data.py --stage status # progress at a glance

# 4. pack for the site
python3 scripts/build_site_data.py
```

`site/` is a dependency-free single-page dashboard: all filtering and
aggregation runs client-side over a compact columnar `data.json`, so it loads
fast and needs no server. `docs/` is a byte-identical mirror because GitHub
Pages serves only from the repo root or `/docs`.

## How the pipeline stays inside the API budget

The WCL client API allows **18,000 points/hour**. The pipeline maximises data
per point:

1. **`fightRankings`, not `characterRankings`.** Fight rankings return one
   entry per *run* (report code, fight ID, keystone level, duration, medal,
   score and the 5-player roster); character rankings repeat every run once
   per ranked player.
2. **One `Summary` table query per run.** A single ~1-point report query
   returns, for all five players at once: total damage done, the raw death
   events and the full combatant talent trees.
3. **GraphQL alias batching.** Rankings pages and report tables are batched
   ~10–12 sub-queries per HTTP request, so quota — not latency — is the limit.
4. **Live budget tracking.** Every response piggybacks `rateLimitData`; when
   spend approaches the cap the pipeline sleeps until the window resets.
5. **Checkpoint everything.** Rankings pages, fetched summaries and parsed
   player rows are journaled to `data/raw/` and `data/processed/`; re-running
   skips completed work.

## Scope and caveats

**Retention (owner policy, 2026-09-14): the site holds the newest two weekly resets per
region and nothing older than 15 days; disk keeps 16 days measured from the newest row.**
The numbers live in `scripts/retention.py`; the page prints the span it actually holds on
every build (`payload.retention`); the collector refuses older runs at discovery, the
committed seed is windowed by `export()`, and `scripts/prune_journals.py` prunes the
journals (dry run first, then at most every 20 h). Nothing older exists anywhere on the
site; git history keeps the daily seeds up to 2026-09-14 only.


* **Population:** every run WCL serves through fight rankings for zone 55
  (Midnight M+ Season 2), keystone brackets 1–29 = key levels 2–30
  (bracket = key − 1). WCL caps each dungeon × bracket leaderboard at 20
  pages × 50 runs; below +10 the collector takes only the first 4 pages,
  since those leaderboards are effectively bottomless and the dashboard
  opens on +10 and up anyway.
* **This is a top-of-leaderboard sample, not a census.** The API serves the
  top runs *by score* per dungeon × key, so the dataset skews toward faster,
  higher-DPS runs. Read it as "what strong runs pull," not "the average run."
* **Unlogged runs are skipped by necessity.** A large share of ranked entries
  are Blizzard-leaderboard imports or anonymized logs with no report attached
  — there is no per-player data to fetch for them, via API or website alike.
* **Duplicate uploads are collapsed.** Several members of a group often each
  upload the same fight, so one real run arrives under multiple report codes.
  A run is identified by dungeon + key + keystone clock + exact roster.
  Start timestamps cannot be used: each uploader's report begins at a
  different moment, tens of seconds apart for the same fight.
* **Keystone timers are derived, not published.** WCL exposes no par time, so
  each dungeon's timer is inferred as the threshold separating timed from
  depleted runs on the keystone clock, snapped to the nearest 30s. Every
  derived value landing on an exact round minute is the check that it worked.
* **DPS** = per-player total damage done ÷ fight duration (the report's own
  `totalTime`), matching WCL's "Overall DPS" for dungeon runs.
* **Deaths** are counted per player from the report's raw death events.
* **Hero talents** come from an offline SimulationCraft trait-tree mapping
  (`build_hero_map.py`). Parses whose log carries no combatant info arrive
  labelled `Unknown`; `hero_from_abilities.py` recovers most of them from the
  abilities they cast, since each tree grants abilities its siblings do not.

## Tuning projection

`data/tuning_patches.json` records each class-tuning pass with the UTC instant
it went live, powering the "Since latest tuning" filter. **It is empty at the
Season 2 launch** — the Aug 18 pass shipped with the season, so every recorded
run already postdates it and there is nothing to split on. Add an entry at the
top of `patches` after each future pass.

The dashboard can also project an *announced but unreleased* pass onto recorded
runs. `fetch_abilities.py` collects a per-ability damage breakdown, and
`project_tuning.py` re-scores each parse line by line against the announced
changes, shipping a per-parse multiplier so any aggregate stays exact under any
filter. Both are dormant until rules are configured: `RULES` is empty, with the
Aug 18 2026 pass kept as `RULES_AUG18_2026` for reference on the rule
vocabulary (spec auras, named abilities, set-bonus scalars, compensating auras,
time- and hero-gated hotfixes). The dashboard hides the toggle when there is
nothing to project.

## Dashboard

* Groups by **Class / Spec / Hero Talent**, with tabs for Average and Median
  DPS, Mean − Median, Average Deaths, Deathless %, Unique Characters, Score,
  and a Trend view whose metric is selectable.
* Filters: class, spec and hero-talent multiselects, a key-level range, an
  **item level** control (one thumb over the levels that actually hold players,
  with a density strip; a two-range form is one click away), region, role, a
  **minimum unique characters** threshold that scales with the sample,
  weekly-reset and day-granularity pickers, timed-only, and a **Compare**
  axis — Off | Time (a second period) | Skill (a second percentile of the same
  parses) | Gear (a second item-level cohort of the same period, set by that
  same single thumb) — with the B side drawn as grey ghost bars and a % badge
  for A's change vs B.
* Trajectory plots over **time**, **key level** or **item level** — the last
  answers how a spec scales with gear, with the key-level confound printed.
* A **Top Comps** table ranks 5-player compositions by a key-normalised
  Strength score, sortable on every column.

## Data dictionary (`data/mythic_runs.csv.gz`)

One row per player per run.

| column | meaning |
|---|---|
| `character`, `server`, `region` | player identity |
| `class`, `spec`, `hero_talent`, `role` | e.g. `Warlock`, `Demonology`, `Diabolist`, `DPS` |
| `dungeon`, `key_level`, `affixes` | encounter, keystone level, affix IDs (`\|`-separated) |
| `duration_s`, `keystone_s` | fight (combat) duration and the keystone clock |
| `damage_done`, `dps` | total damage and overall DPS for the run |
| `deaths` | this player's deaths in the run |
| `item_level` | player max item level during the run |
| `score`, `medal` | WCL points/medal for the run |
| `report_code`, `fight_id`, `started_at` | provenance of the parse |
| `exec` | 1 when the execution bundle was fetched for the run, 0 when not, empty on rows older than the bundle |
| `pots`, `hs`, `deaths_chain` | potions and healthstones used; own deaths within 5 s of another party death (from the Summary; empty on older rows) |
| `kicks`, `kicks_by` | interrupts landed, and per enemy spell id (`guid:n\|guid:n`, `none` when zero); empty unless `exec` = 1 |
| `stops` | interrupts landed with anything but the spec's kick (stuns, knocks, incapacitates, silences...): the Interrupts table's per-ability breakdown minus the spec's `kick.name` in `lists.json`; 0 when fetched and none, empty when the table was not fetched or on rows older than the column (2026-09-28) |
| `dispels`, `dispels_by` | dispels landed, same shape |
| `avoid_dmg`, `def_casts` | damage taken from the dungeon's avoidable list; casts of the spec's defensives/self-heals/consumables |
| `heal_total`, `heal_over` | healing done and overhealing |

## Execution bundle and baselines sidecars

The [Key Level Logs](https://st331.github.io/keylevel_addon/) vetting site scores an
applicant's runs against this collector's population. Its contract is
`keylevel_addon/design/baselines-from-wowlogs.md`; the rule it enforces is that no run is
ever pulled from Warcraft Logs twice (§1). What this repository adds:

* **The bundle** (`scripts/execution.py`, wired into `fetch_data.py`). For a gated run the
  per-run request carries five more tables under the same alias as its Summary --
  Interrupts, Dispels, DamageTaken filtered to the dungeon's avoidable list, Casts filtered
  to the roster's defensive/self-heal/consumable kit, Healing (design doc §4, exact
  GraphQL in `execution.bundle_subquery`). They parse into the per-player columns above
  (zero-filled for a player a table omits, empty when the table was not fetched) and a
  run-level record in `data/processed/runs.jsonl` (the run's per-spell interrupt and
  dispel sums). The curated spell lists are the addon's
  `https://st331.github.io/keylevel_addon/data/lists.json`, fetched at the start of every
  refresh run with the last fetched copy, then the vendored `data/lists.json`, as fallbacks.
* **Kicks vs stops, and the lean bundle.** `kicks` is every interrupt the player landed;
  `stops` is the part of it landed with anything but the spec's kick (the Interrupts table
  names the interrupting ability per player; `lists.json` names the spec's kick, so a
  Warrior's Shockwave counts and his Pummel does not). A spec the lists know without a kick
  (Holy Priest, Restoration Druid) has every interrupt as a stop; a class the lists do not
  know at all gets `stops` empty rather than a guess. The **lean bundle** the backfill
  (`scripts/backfill.py`) asks for is the Summary + Interrupts + Dispels only -- kicks, stops
  and the healers' dispels are what the vetting site scores -- at `est_cost` 3.0 a run
  (`execution.EST_COST_BUNDLE_LEAN`) instead of the full bundle's 7.5; a backfilled row has
  `avoid_dmg`, `def_casts`, `heal_total`, `heal_over` empty. The baselines carry `stops_min`
  (per minute, better high) over the rows that carry `stops`, with `n_stops` on every cell.
* **Pausing the bundle.** Commit an empty `data/bundle.paused` (or set
  `EXEC_BUNDLE=off` in the environment) and no run gets the bundle: the Summary
  sweep, the baselines and the run store go on with what is already journaled,
  and the WCL points the bundle was spending (about 2,900 an hour while the
  cells fill) stay free for other clients. Delete the file to resume; the gate
  picks up exactly where its counts left off. `exec.paused` in the health
  lines says which state a run was in.
* **The quota gate.** A run gets the bundle only while any of its five
  (spec, dungeon, 2-level band) cells holds fewer than 100 bundled player-rows over the
  trailing 14 days (specs from the sweep roster, class-level when the ranking carries no
  spec). The counter is rebuilt from the players journal at every start, consulted per
  batch as it is submitted, and saved beside the checkpoints (`data/processed/exec_quota.json`).
  Measured on the CSV: ~4,600 of ~9,000 runs/day at key >= 10 are admitted, every cell the
  population can fill reaches its quota, rare specs stay at ~100 % coverage.
* **The cost.** +1.0 point per table per run inside the same request (6.0 warm, 6.5--8.5
  cold for the six); the governor reserves `est_cost` 7.5 for a bundled run and 2.6 as
  before for a Summary-only one. Measured at the 20-minute cadence: **+≈960 points/hour**,
  ≈11,000/hour in total; at the 4-hour cadence (below) the same work lands in the hour a
  run starts, under the standing 70 % ceiling of 12,600 -- the retention policy is untouched.
* **The sidecars** (`scripts/build_baselines.py`, run by the refresh workflow right after
  `build_site_data.py`, from the same retention-windowed frame; `site/**` deploys as before):
  `site/baselines.json.gz` (design doc §2: per spec x dungeon x level cell at three tiers,
  quantiles [5,10,25,50,75,90,95] of every measure over timed leaderboard runs, plus the
  per-dungeon priority and dispellable tables) and `site/runs/<hh>.json.gz` (§3: every run
  in the window at +10 and up, whatever its medal, in 256 shards keyed by a hash of the
  report code -- `h = (h*31 + ord(ch)) mod 256` over its first four characters, two
  lowercase hex digits, the same arithmetic as the client's `charCodeAt` form -- every
  shard written so a fetch never 404s, with the stored per-player rows; a field the
  collector did not fetch is omitted and `"exec": false` says so). Health lines
  `baselines.*` land in `build_health.txt`, sizes and the largest shard included, with a
  flag when the set is over the 40 MB budget (13 MB gzipped today with no
  bundled rows; ~27 MB projected once half the runs carry the bundle).

## Cadence and quota

Since 2026-09-30 (owner: "a cadence of updating the data every 4 hours or so ... never end
up using more than 70% of my API quota") the refresh is a **cron, not a self-chain**, and
every collector runs under one cap. Both knobs are in **`data/cadence.json`**
(`scripts/cadence.py` reads it; a missing or bad value is its default, said in the log):

```json
{"every_hours": 4, "quota_fraction": 0.70}
```

* **Every 4 hours.** `refresh.yml` runs on `cron: "0 */4 * * *"` (UTC: 00, 04, 08, 12, 16,
  20 -- 05:30, 09:30, 13:30, 17:30, 21:30, 01:30 IST). The hourly Warcraft Logs window resets
  at :00 UTC, so a scheduled run starts on a fresh window. A scheduled run (or a hand
  dispatch, or the watchdog's revival) dispatches **no successor**: the "Chain the next run"
  step chains only while `data/backfill.json` is in force or an explicitly dispatched
  `drain` still has a backlog (`cadence.chain_decision`). Every run at this period sweeps
  deep (~2,000 points) and fetches ~4 hours of new runs with the bundle where the gate admits;
  the trinket and keystone collectors run in the background of the build as before.
* **Never above 70 %.** `quota_fraction` is the share of the hourly limit (18,000 points, so
  12,600) **any** process on the account may spend. `wcl_client.quota_fraction()` reads it
  whenever `WCL_QUOTA_FRACTION` is not in the environment, and `refresh.yml` passes it into
  every step that runs a client (the Cadence step -> Fetch, the background collectors)
  besides. The governor measures the ceiling against the account's **live**
  `pointsSpentThisHour`, so the sweep, the bundle, the trinket and keystone collectors and
  the owner's own lookups from the vetting site add up under the one cap; a process that
  starts with the hour already over it sleeps to the reset within its cap (8 min for Fetch,
  30 s for the collectors) or stops cleanly with what it has -- it never pushes past. Two
  details make that hold at the edges: a worker that slept for the reset **re-reads the live
  spend** before it sends anything (WCL's `pointsResetIn` under-reports by tens of seconds;
  the old hour is still being billed when the sleep ends) and sleeps again while the old hour
  is reported, at most `WCL_RESET_GRACE_S` (5 min) past the first sleep; and a process whose
  starting reading fails (the probe is retried three times) is **blind**, admitting one
  request at a time until a response brings the reading, so a blind start costs one request,
  never a wave. `scripts/test_quota_ceiling.py` drives all of it against a fake WCL. The
  health lines say what happened: `fetch.cadence.every_hours`, `fetch.cadence.quota_fraction`,
  `fetch.quota.fraction` (the ceiling the run ran under) and `fetch.quota.share_at_end` (the
  account's spend share of the limit when Fetch ended; over `quota_fraction` is the number
  that must never happen).
* **The watchdog** (`watchdog.yml`) stays on, hourly, with its quiet threshold at
  `every_hours + 1 h` (`STALE_SUCCESS_MIN: 300`; alert at 540, stale data at 600): a healthy
  cadence never trips it, a dropped cron tick is revived within the hour, and the revived run
  is an ordinary cadence run. `scripts/test_cadence.py` pins the cron and these thresholds to
  the file.
* **To change either knob:** edit `data/cadence.json`; for the period also edit the cron in
  `refresh.yml` and `STALE_SUCCESS_MIN` in `watchdog.yml` (the test tells you if they
  disagree); the fraction needs nothing else. A `workflow_dispatch` may set `quota_fraction`
  for one deliberate run; the scheduled path never does. To pause everything: comment the cron
  out and put `if: false` on the job in both workflows (how the 2026-09-28 pause was held).
* **The backfill switch overrides it.** A committed `data/backfill.json` with a future
  `until` (`execution.backfill_mode`, `scripts/backfill.py`) puts every run at its `share` of
  the limit instead of `quota_fraction`, admits every run to the bundle, backfills the
  window's unbundled runs after the sweep and chains runs back to back until `until`; once it
  passes (or the file is deleted) the cadence and the cap are back with nothing else to touch.
  The 2026-09-27..30 backfill's file was deleted when it finished; a future backfill is one
  file away.

## Tests

Every suite is a standalone script and prints `PASS`; no workflow runs them, so run them
before a push:

```bash
for t in scripts/test_*.py; do python3 "$t" >/dev/null && echo "ok   $t" || echo "FAIL $t"; done
```

`test_retention.py` (the page's window), `test_retention_fetch.py` (the collector's),
`test_prune_journals.py` (the pruner), `test_site_contract.py` (the page's static contract),
`test_builds_sidecar.py`, `test_stats_sidecar_roundtrip.py`, `test_spec_stats.py`,
`test_trait_union.py`, `test_legacy_single_pass.py`, `test_names_scan.py`,
`test_gear_parse.py`, `test_export_stream.py`, `test_quota_ceiling.py`, `test_build_entry.py`,
`test_execution_bundle.py` (the bundle's parsers on the real fixture), `test_exec_gate.py`
(the quota gate), `test_exec_wiring.py` (the request, the caller's path, export and seed),
`test_build_baselines.py` (cells, tiers, shards, the round trip), `test_backfill.py` (the
backfill switch and mode), `test_cadence.py` (the cadence file, the cap every client
resolves, the Chain step's decision, the cron and the watchdog threshold pinned to the file).

