# WoW Mythic+ Performance Dashboard — Midnight Season 1

A data-collection pipeline and interactive Streamlit dashboard that tracks,
aggregates and visualizes Mythic+ performance for **Midnight Season 1**
(high keys, +12 → +25 and above), built on the
[Warcraft Logs v2 GraphQL API](https://www.warcraftlogs.com/api/docs).

**Current dataset:** 60,235 dungeon runs / 301,185 player parses across all 8
dungeons and keys 12–25+ (runs dated 2026-03-28 → 2026-07-02), all regions
(CN 117.8k, EU 110.8k, US 57.6k, KR 14.9k player rows). 14 classes, 36
specs, 42 hero talents. Of 94,613 ranked leaderboard entries, 34,325 were
unlogged/anonymous (no report exists — nothing to fetch); every public run
was collected. Total API cost: ~170k points across ~9 hourly quota windows.

```
scripts/wcl_client.py      quota-aware WCL GraphQL client
scripts/build_hero_map.py  trait-node → hero-talent mapping from SimC data
scripts/fetch_data.py      checkpointed collection pipeline → data/mythic_runs.csv
dashboard.py               Streamlit dashboard
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

# 3. collect data (checkpointed — kill/re-run any time, it resumes)
python3 scripts/fetch_data.py                # sweep → summaries → export
python3 scripts/fetch_data.py --stage status # progress at a glance

# 4. dashboard
streamlit run dashboard.py
```

## How the pipeline stays inside the API budget

The WCL client API allows **18,000 points/hour**. The pipeline is designed
around getting the maximum data per point:

1. **`fightRankings`, not `characterRankings`.** Fight rankings return one
   entry per *run* (with report code, fight ID, keystone level, duration,
   score and the 5-player roster), while character rankings repeat every run
   once per ranked player. One sweep of 8 dungeons × 14 keystone brackets
   (keys 12–25+; WCL serves at most 20 pages × 50 runs per bracket) costs
   ≈ 2,000 points.
2. **One `Summary` table query per run.** A single ~1–3 point report query
   returns, for all five players at once: total damage done, the raw death
   events, and the full combatant talent trees. That is every per-player fact
   we need for ≈ 0.5 points per player-row; fetching deaths/talents as
   separate event queries would triple the cost.
3. **GraphQL alias batching.** Rankings pages and report tables are batched
   ~10–12 sub-queries per HTTP request, so the quota — not round-trip
   latency — is the only limiter.
4. **Live budget tracking.** Every response piggybacks `rateLimitData`; when
   the spend approaches the hourly cap the pipeline sleeps until the window
   resets, then continues. HTTP 429s and GraphQL quota errors are handled the
   same way, transient network failures with exponential backoff.
5. **Checkpoint everything.** Rankings pages, fetched summaries and parsed
   player rows are journaled to disk (`data/raw/`, `data/processed/`);
   re-running skips all completed work.

### Scope and caveats

* **Population:** every run WCL serves through fight rankings for zone 47
  (Midnight M+ Season 1), keystone brackets 11–24 = key levels 12–25+
  (bracket = key − 1; the top bracket includes 25+). WCL caps each
  dungeon × bracket leaderboard at 20 pages × 50 runs.
* **This is a top-of-leaderboard sample, not a census.** Because the API only
  serves the top ~1,000 runs *by score* per dungeon × key, the dataset skews
  toward high-scoring (faster, higher-DPS) runs. Aggregate DPS here therefore
  runs a bit **higher** than sites that average the full logged population
  (e.g. mythicstats / WCL's own statistics), by roughly 8–12 % — the ranking
  order matches, the absolute numbers sit above a full-population mean. Read
  it as "what strong runs pull," not "the average run."
* **Unlogged runs are skipped by necessity.** Roughly 70 % of ranked entries
  are Blizzard-leaderboard imports or anonymized logs with **no report
  attached** (`report.code == ""`, `deaths == 300000000` sentinel) — there is
  no per-player data to fetch for them, via API or website alike.
* **Regions:** all regions are collected by default (`--regions US,EU`-style
  allow-lists are available for restricted runs). About two-thirds of public
  entries carry no server tag on the leaderboard — each player's true region
  (from the report itself) lands in the CSV's `region` column, and the
  dashboard's Region filter works off that.
* **DPS** = per-player total damage done ÷ fight duration (the report's own
  `totalTime`), matching WCL's "Overall DPS" for dungeon runs.
* **Deaths** are counted per player from the report's raw death events
  (`deathEvents` in the summary table).

## Hero talents

WCL only exposes combatant talents as raw trait-tree node IDs; there is no
string-translation endpoint. `scripts/build_hero_map.py` derives an offline
mapping from **SimulationCraft's** open-source DBC dumps
(`engine/dbc/generated/trait_data.inc`, `midnight` branch): every trait node
carries a sub-tree ID, and `__trait_sub_tree_data` names all 41 hero trees
("Diabolist", "Rider of the Apocalypse", …). A player's hero talent is
resolved by majority vote over their equipped talent nodes, which is robust
to the shared selection node that SimC attributes to a sibling tree.

## Dashboard

* Groups by **Class / Spec / Hero Talent** with **Total Runs, Average DPS,
  Median DPS, Average Deaths**; DPS and run counts use comma separators.
* Sidebar filters: Class, Spec and Hero Talent multiselects, a Key Level
  range slider, a Minimum Runs threshold slider (default 3) that hides
  statistical outliers, plus an optional Role filter.
* Data is cached with `st.cache_data`; the **🔄 Refresh Data** button clears
  the cache and re-reads `data/mythic_runs.csv` from disk without restarting
  the server.

## Data dictionary (`data/mythic_runs.csv`)

One row per player per run.

| column | meaning |
|---|---|
| `character`, `server`, `region` | player identity |
| `class`, `spec`, `hero_talent`, `role` | e.g. `Warlock`, `Demonology`, `Diabolist`, `DPS` |
| `dungeon`, `key_level`, `affixes` | encounter, keystone level, affix IDs (`\|`-separated) |
| `duration_s` | fight duration in seconds |
| `damage_done`, `dps` | total damage and overall DPS for the run |
| `deaths` | this player's deaths in the run |
| `item_level` | player max item level during the run |
| `score`, `medal` | WCL points/medal for the run |
| `report_code`, `fight_id`, `started_at` | provenance of the parse |
