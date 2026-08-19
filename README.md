# WoW Mythic+ Performance Dashboard — Midnight Season 2

A data-collection pipeline and interactive dashboard that tracks, aggregates
and visualizes Mythic+ performance for **Midnight Season 2**, built on the
[Warcraft Logs v2 GraphQL API](https://www.warcraftlogs.com/api/docs).

```
scripts/wcl_client.py        quota-aware WCL GraphQL client
scripts/build_hero_map.py    trait-node → hero-talent mapping from SimC data
scripts/fetch_data.py        checkpointed collection pipeline → data/mythic_runs.csv.gz
scripts/build_site_data.py   packs the CSV into site/data.json + the /llms export
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

* **Population:** every run WCL serves through fight rankings for zone 55
  (Midnight M+ Season 2), keystone brackets 9–24 = key levels 10–25+
  (bracket = key − 1; the top bracket includes 25+). WCL caps each
  dungeon × bracket leaderboard at 20 pages × 50 runs.
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
* Filters: class, spec and hero-talent multiselects, key-level range, region,
  role, a **minimum unique characters** threshold that scales with the period,
  weekly-reset and day-granularity pickers, timed-only and compare-periods.
* A **Top Comps** table ranks 5-player compositions by a key-normalised
  Strength score, sortable on every column.
* `/llms.txt` and `/llms/*.csv` expose the whole dataset for LLM analysis.

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
