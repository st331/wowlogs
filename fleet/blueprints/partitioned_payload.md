# BLUEPRINT — Partitioned data path · build-ready · 2026-09-02 · revision 2

Owner prefs (`fleet/user_prefs.md`) override everything here. Two implementers work from
this file alone and never at the same time on the same file: PIPELINE (`scripts/*`,
`.github/workflows/*`, `data/season*.json`) ships first; CLIENT (`site/next/index.html`,
later copied over `site/index.html`) ships second. §2–§4 and §8 are the interface contract
between them: neither side deviates without editing this file first. The builds-sidecar
contract in `fleet/blueprints/builds_tab.md §1` is superseded, for the partitioned path only,
by §4 of this file; a pointer is added there at PR-1 merge.

Line references: B = `scripts/build_site_data.py`, F = `scripts/fetch_data.py`,
C = `site/index.html`, W = `.github/workflows/refresh.yml`, DS = `deploy-site.yml`,
P = `scripts/project_tuning.py`, HR = `scripts/hero_from_abilities.py`.
Numbers marked **[measured]** were taken by the design round on the committed seed CSV
(`data/mythic_runs.csv.gz`, 559,518 rows, 2026-08-18 → 08-26, one full reset week of
450,483 rows in-region; the review round re-measured 535k rows in one reset week counting
all regions, 117,772 rows on the reset day, 22k–118k rows per UTC day) and on the live
2026-09-02 artifacts. Everything else is a budget the perf test (§9.3) pins.

Revision 1 (Appendix A) answers the two adversarial reviews filed against the first draft.
Revision 2 (Appendix B) answers the two adversarial reviews filed against revision 1: nine
blockers closed at their cause (comps cube over clocked runs only; a generation guard across
a week's four cube files; the loader fetching every listed day; the tuning rule tables in
`inputs_sha`; `rankings.jsonl` treated as the per-run snapshot it is; no network and a hard
deadline inside the partition builder; the reseed split so a cache eviction never delays the
current reset; the clobber guard skipping instead of failing; one authoritative copy of
`season_pins.json`), plus the cheap weaknesses. Every change is recorded there with the
blocker it closes; section numbers are unchanged.

---

## 0. The problem and the decisions taken

### 0.1 Measured on 2026-09-02 (do not re-derive)

- 751k rows, +75k/day; ~13M rows by the end of a 23-week season.
- Legacy payload `data.json.gz` 7.2 MB (≈9.5 B/row gz), 2.4 s to first paint, 109 MB JS
  heap, all linear in rows. **`MAX_RUNS = 150_000` (B:249) is binding as of this week**:
  the legacy payload will not grow past ~750k rows; by season end it is a 6% uniform sample
  of runs that changes every build (the hash cut shrinks as `total` grows, B:296). Every
  "equivalence with legacy" statement below therefore means equivalence with a legacy build
  run at `MAX_RUNS=0` on the same rows; the live legacy site cannot be matched bit-for-bit
  by anything, including itself.
- Builds sidecar 4.7 MB target / 5.0 MB cap over every row of the season; the ladder fired
  once this week and dropped the enchant block. Stats sidecar 2.5 MB.
- Build step 7–8 min of a ~20-min cycle; 4–8 of those minutes are six full JSON passes over
  the 430k-record gear journal (B:369, :532, :894, :1219, and two more inside `build_llms`,
  which alone costs ~90 s and runs on every ordinary cycle). **Each pass costs ≈ 140 µs per
  record and the journal grows ~5 records per run (~375k records/day)**: left alone, the
  four non-LLM passes alone reach ~28 min by season week 6 and ~47 min by week 10 — past
  the 50-min job timeout around week 8–9. §7.4 and PR-1 deal with this directly; it is the
  binding clock of the transition, not disk.
- `mythic_runs.csv.gz` (23 MB) crosses the 100 MB push limit around end of September;
  `gear.jsonl.gz` (~150 MB) already cannot be committed. `data/checkpoints/` is dead code
  (F:159–166); the only cold start is `gear.jsonl.gz` inflation + `seed_from_csv()`, and
  `export_gear()` rewrites the durable copy from whatever the journal holds (the clobber
  hazard, F:174–182).
- `export()` itself (dedup + roster-signature collapse + `to_csv`) is O(season): 12 s at
  560k rows [measured], ~2 min by week 10. A modest growth term until PR-4 removes it.
- Two corrections to the brief's numbers, measured by all three design rounds and both
  judges: **typed columns are ~15–30% smaller on the wire than the JSON payload, not 3×**
  (legacy 9.46 B/row gz; typed 8.1; typed byte-planar 7.1 [measured]) — the real gains are
  zero parse time, 4–6× less heap and immutable per-day files that a returning browser never
  re-downloads. And **"a few hundred KB per week regardless of run count" is not reachable
  with exact distinct-character counts** (character ids alone are ~1.9 B/row gz); this
  design pays ~3.5 MB per closed week, loaded lazily, and keeps every count exact.

### 0.2 Decisions (owner-approved direction, with the amendments this blueprint makes)

| # | decision | status |
|---|---|---|
| 1 | Last THREE resets at row level; older weeks as per-reset aggregates | **kept, amended**: rows are partitioned by UTC day (§1.1), the client loads only the current reset for first paint and the other two behind it; closed weeks are served by four cube files per week (§3) whose counts, means, distinct characters, dates covered and DPS quantiles are exact, with one bounded statistic (distinct runs under hero/tier splits) |
| 2 | Typed binary columns | **kept** (container `WLP1`, §2.1), for heap/parse/caching reasons, not byte count |
| 3 | Per-spec sidecar shards over the row window | **kept, amended**: one block per (spec, UTC day) + one all-spec vocabulary file per run (§4); blocks are immutable and uncapped, so nothing is ever shed again |
| 4 | Incremental build | **kept** (§6): a frozen day/week is written once; an ordinary run touches 1–2 days |
| 5 | Dual-emit transition, deliberate cutover | **kept** (§10); the new builder runs *in parallel* with the legacy one so the transition adds zero latency to the current path, and PR-1 flattens the legacy builder's growth so dual-emit itself never slows the current reset |
| 6 | Durable copies to Release assets; daily commit shrinks to a manifest | **kept, amended**: journal snapshots are arrival-ordered byte-range parts of the three append-only journals (§7), not week files; `rankings.jsonl` is a per-run snapshot whose durable form is the overlay table (§6.2-1) |
| 7 | Pipeline first, client second; nothing touches `site/index.html` while anything else does | **kept**: the client is authored at `site/next/index.html` and copied over at cutover |

Additional decisions taken here that the design round left open are listed in §11.4. Two of
them (§11.4-6 and §11.4-19, the trust-gate pool and the Trends gate/ranking basis) need the
owner's nod **before PR-2 starts** and are presented together (§11.5).

---

## 1. Artifact inventory

All partitioned artifacts live under `site/d/<slug>/` (`slug` = `s2` this season) and are
**gitignored** like the three sidecars; they travel in the Pages artifact only (the `docs/`
mirror does not carry them). Every file except the manifest and `current.json` has a
content-hashed name (`<name>.<sha1[:10]>.<ext>`), is written once and never modified.

### 1.1 Partition unit

**Rows: one file per UTC calendar day** of the run's start instant
(`day = floor((started_at − 2026-01-01T00:00Z) / 86400 s)`, exactly the payload's `day`).
Reasons, in order: (a) the client computes a row's reset bucket from `day+hr+reg` against its
own region's instant at load time (C:1879–1926) and keeps doing so — a day file needs no
boundary decision and rolls over with no rebuild; (b) freshness: the only file that changes
every 20 minutes is today's (≤ 140k rows on the busiest day, ≤ 1.0 MB); (c) a late upload or
regear refetch dirties one day, not a week; (d) a run's five rows share one start instant, so
a run never straddles a file and comps stay whole by construction.

**Aggregates: one set of files per absolute reset week `W`** (§3.1), containing every
region's week `W`; emitted once when the week freezes.

**Sidecar shards: one block per (spec, UTC day)** in the row window, bound to that day's row
file by hash (§4).

### 1.2 Inventory

Sizes are gzip-9 on the wire. Window = the UTC days that can hold a row of the last three
resets (≤ 24 files, ≈ 1.6–1.7M rows once the season is ≥ 3 weeks old) **plus the days of
any older week whose cube is not yet published (§3.1 cube-gap invariant; normally none)**.

| file | producer | consumer | 751k rows (wk 3) | 4M (wk ~8) | 13M (wk 23) |
|---|---|---|---|---|---|
| `d/current.json` | `partition_build.py` | client boot | 40 B | 40 B | 40 B |
| `d/<slug>/manifest.json` | `partition_build.py` | client boot + 180 s poll | ~40 KB (10 KB gz) | ~60 KB | ~80 KB |
| `d/<slug>/rows/d<day>.<h>.bin` (kind `rows`) | `partition_build.py` step 2 | first paint (bucket 0), then buckets 1–2 | ~15 files, 5 MB total | 24 files, 11 MB | same |
| `d/<slug>/cube/w<W>.cells.<h>.bin` | step 4 (on freeze) | Trends, Compare/month presets, Pulse, KPIs for buckets ≥ 3 | 0 | 5 × 0.25 MB | 20 × 0.25 MB |
| `d/<slug>/cube/w<W>.dist.<h>.bin` | step 4 | quantile metrics (med/q30/q85/qb/lens), Pulse sparkline, deaths quantiles | 0 | 5 × ≤1.5 MB | 20 × ≤1.5 MB |
| `d/<slug>/cube/w<W>.chars.<h>.bin` | step 4 | distinct-character counts, trust gate, rating cohorts, Trends `chars` metric for buckets ≥ 3 | 0 | 5 × ≤1.2 MB | 20 × ≤1.2 MB |
| `d/<slug>/cube/w<W>.comps.<h>.bin` | step 4 | Top Comps when `weeksA` contains a bucket ≥ 3 | 0 | 5 × ≤0.7 MB | 20 × ≤0.7 MB |
| `d/<slug>/spec/<cls>-<spec>/d<day>.<h>.bin` (kind `shard`) | step 2 | Frame stats block + Character screen for that spec | ~600 files, ~15 MB (57% gear coverage × 36 B × 751k) | 960 files, ~35 MB | same |
| `d/<slug>/spec/vocab.<h>.json.gz` | step 3 (every run) | first frame open / Character screen (all specs) | ~0.5 MB | ~0.6 MB | ~0.6 MB |
| `d/<slug>/meta/charscore.<h>.bin` (kind `pairs`) | step 3, **rewritten at the daily slot only** | rating columns, after first paint | ~0.9 MB | ~1.8 MB | ~3 MB |
| `d/<slug>/meta/charscore.delta.<h>.bin` (kind `pairs`) | step 3, every run: pairs added or changed since the daily base | applied over the base; the only rating file an open tab re-downloads per cycle | ≤ 30 KB | ≤ 30 KB | ≤ 30 KB |
| `d/<slug>/meta/specstats.<h>.json.gz` | step 3 | frame stats fallback block | 30 KB | 30 KB | 30 KB |
| `d/<slug>/talents.<h>.json.gz` | `talents_doc()` unchanged | Talents pane | as today | | |
| `site/build_health.txt` | legacy builder writes, the Build step **appends** `data/processed/parts/health.txt` after both builders exit (§6) | watchdog, humans | +20 lines | | |
| `data/release_manifest.json` (git, daily) | `journal_parts.py` | `reseed_from_release.py` | ~10 KB | ~40 KB | ~120 KB |
| `data/season.json` (git, hand-edited) | owner / rollover | fetcher, both builders, client via manifest | 3 KB | | |
| `data/season_pins.json` (git, **a mirror**: the authoritative copy is `data/processed/parts/season_pins.json`, Actions cache + Release on every write; the commit step copies it into `data/` like `rio_scores`, §2.5/§7.2) | `partition_build.py` | builder (humans edit the git copy; the builder adopts an edit as a recorded upgrade, §6.4) | 2 KB | | |
| Release `data-<slug>` assets (§7) | `journal_parts.py` | reseed, `llms.yml` | ~0.5 GB | ~2.5 GB | ~8 GB across ~1k live assets (per-run `state.<seq>.json` / registry parts are deleted by the daily consolidation, §7.1) |
| `site/llms/*` + `llms.txt` | `build_llms()` via `llms.yml` (daily) → Release `llms.tar.gz` → unpacked by the refresh (§5); **untracked from git in PR-1** | LLM crawlers | 20 MB | 20 MB | 20 MB |
| legacy `data.json.gz`, `stats/builds/talents.json.gz` | `build()` unchanged | legacy client | until PR-4 | until PR-4 | — |

Pages footprint (data only, with the retention rule of §6.5): ~40 MB / ~90 MB / ~170 MB
(re-summed with the shard arithmetic above). Plus icons 11 MB and llms 20 MB. Under the 1 GB
soft limit by a wide margin. The Pages **artifact** (data + legacy files + icons + llms)
grows from ~40 MB today to ~90 MB at PR-1 and ~180 MB at season end; `upload-pages-artifact`
and `deploy-pages` scale with bytes, so the deploy step's wall is written to health as
`deploy.wall_s` and PR-1's acceptance holds it at its pre-PR value (§10, §11.1).
`deploy-site.yml` downloads the same artifact on every UI push.

Builder-side state (Actions cache, all under `data/processed/parts/<slug>/`, **slug-scoped**
so a rollover starts clean without touching the previous season's entry; **the cache path
list in W:181–188 is not edited**): `state.json`, `ids/chars.bin`, `ids/runs.sqlite`,
`learned/` (§5), `season_pins.json` (authoritative, §2.5), `days/d<day>/{raw,gear,abil,
thin}.npz` for **every day still inside the row window (frozen or not)**, `upload/` (staged
Release assets, §6.2-4), `out/` (byte-exact mirror of `site/d/<slug>/`). **Cache arithmetic,
corrected:** days/ ≈ 192 MB + `out/` ≈ 40–170 MB + `ids/chars.bin` (u32 id + name, ≈ 25
B/name ⇒ ~110 MB at 4.4M names) + `ids/runs.sqlite` (~200 MB at 2.6M runs) + dual-emit
journal growth (~70 MB/week gz) ⇒ **≈ 600 MB by week 6, ≈ 700 MB+ at season end**, +20–30 s
of cache restore **on the critical path** (restore runs before Fetch), and the 10 GB cache
holds ~15 entries (~5 h of runs). `ids/chars.bin` is stored name-sorted-free as a plain
`u32 id ‖ u16 len ‖ utf8` append log with a daily-consolidated sidecar `chars.idx` (sha1[:8]
→ id, 12 B/name) so lookups cost no full scan; the registry stays append-only. A day's
caches are deleted locally only when the day leaves the row window **and** its
`parts.d<day>.tar.gz` has been verified on the Release (§6.2 step 4 / §7.1); `state.json`
records per day whether the caches are `local`, `release` or both.

---

## 2. Byte-level formats, stable ids, manifest schema

### 2.1 Container `WLP1` (every `.bin`)

A gzip stream (level 9, inflated in the browser with `DecompressionStream`) whose payload is:

```
0..3      "WLP1"
4..7      u32 LE   H = header length in bytes
8..8+H    UTF-8 JSON header, space-padded so that 8+H is a multiple of 8
8+H..     data area; column i starts at header.cols[i].off (relative to data start,
          always a multiple of 8) and spans cols[i].n * itemsize bytes
```

Header, common part:
```jsonc
{"v":1,"kind":"rows"|"shard"|"cells"|"dist"|"chars"|"comps"|"pairs",
 "season":"s2","n":<primary row count>,
 "cols":[{"k":"dps","t":"u32","n":63860,"off":0,"p":1,"d":0}, ...],
 ...kind-specific fields...}
```
**Generation fields (part of the common header, checked by the client before any byte of
the data area is used):** kind `rows` carries `rows_sha` and `rules_sha` (§2.2); kinds
`cells`/`dist`/`chars`/`comps` carry `week` and **`cube_sha`** — one sha per week per
generation, identical across the four files of that generation (§3.2); kind `shard` carries
`rows_sha` of its day (§4.2). A file whose generation field does not match the manifest
entry (or, for a cube file, the resident `cells` of the same week) is rejected unread.

`t ∈ {u8,i8,u16,i16,u32,i32,u64}`, **little-endian throughout**. `p:1` = **byte-planar**:
for an item size `s` the column's bytes are laid out as `s` planes of `n` bytes (plane 0 =
least-significant byte of every item, then plane 1, …); the reader un-shuffles into a typed
array in one loop. Measured −12–16% on the wire for `dps` and `char` versus interleaved
[measured]. `d:1` = **delta-coded**: item `i` is stored as `v[i] − v[i−1]` (u32 wraparound),
with the running value reset to 0 at every group start listed by the file's `coff` column
(kind `dist` only). `u64` is written as two `u32` planar columns `<k>_lo`, `<k>_hi` (the
reader combines into a JS `Number`; every u64 here is a sum below 2^53). No timestamp lives
inside any `.bin` — a file is a pure function of its inputs (§6.3). Writer and reader live in
`scripts/partition_format.py` (Python) and are the reference for the client decoder;
`tests/test_partition_format.py` round-trips every dtype, planar and delta variant.

**Clamps (part of the bit-exact claim):** every integer column below states its clamp; the
writer counts clamped values per column and emits `parts.clamped.<file>.<col>=<n>` in health
whenever the count is non-zero. Legacy stores raw floats for durations; the day file stores
`round()`ed seconds, which is what every client formatter (`fmtDur`, C:1873) displays anyway,
and `test_rows_bitexact_vs_legacy` compares against `round()` of the legacy value.

### 2.2 `rows` — `d/<slug>/rows/d<day>.<h>.bin`, one per UTC day

Header adds: `day` (int), `n` (rows), `runs`, `rows_sha` (= the `<h>` in the file name,
sha1 of the gzip payload), `inputs_sha` (§6.3), **`rules_sha`** (the tuning rule-table
digest the day's `tmul` was computed with, §5/§6.3; present on every day file, also when the
`tmul` column is absent), `flags:{tier,timed,post,tmul}` (booleans: column present and
carries ≥ 1 non-sentinel value).

Row order is **content-deterministic**: sort by `(started_at ms, report_code, fight_id, role
rank Tank<Healer<DPS<Unknown, character, server)`. Runs are therefore contiguous and the
day-local run index `run` is non-decreasing from 0.

Row block (length `n`):

| col | t | notes |
|---|---|---|
| `cls spec hero role` | u8 | codes into `season.json` vocab (§2.5); `role` stays per row (the CSV carries it per row and bit-exactness with legacy is cheaper than deriving it from spec; the builder additionally asserts per build that `role` is a pure function of `(cls,spec)` — true for all 40 specs today [measured] — and emits `parts.role_impure=<spec>` if it ever is not, because the cube relies on it, §3.2) |
| `deaths` | u8 | clamped 255 (max 15 today) |
| `tier` | i8 | −1 unknown / 0–5 pieces of the pinned season set (§5) |
| `dps` | u32, p | `round(dps)` after the keystone-clock rewrite (B:252), exactly today's integer |
| `char` | u32, p | **season-global character id** (§2.4) |
| `run` | u16 (u32 when `runs ≥ 65536`), p | day-local dense run index |
| `tmul` | u16, p | ×10000; `0` = unprojectable tuned row (client drops from both sides, as today); computed with the pinned learned tables (§5) **and the rule tables whose digest is the header's `rules_sha`**. Column presence is a function of the rules generation, not of the day's data: the column is present iff the rule tables at `rules_sha` define a projection (`project()` returned non-null on the *window*, recorded per generation in `state.json`), so within one generation every day file agrees; a day whose `rules_sha` ≠ `manifest.projection.rules_sha` is **unprojected-pending**, never "1.0" (§3.3) |

Run block (length `runs`), stored once per run and **expanded to row length by the client**
through `run` so that `R.dun R.key R.reg R.timed R.post R.hr R.day R.dur R.kdur` exist
exactly as today (9 run-level columns cost 0.83 B/row gz instead of ~2.5 expanded
[measured]):

| col | t | notes |
|---|---|---|
| `r_dun r_key r_reg` | u8 | |
| `r_timed r_post` | i8 | 1/0/−1 semantics unchanged (`MEDAL_TIMED` B:66; `post_tuning_flag` B:78 against `season.json`'s patch cutoffs) |
| `r_hr` | i8 | UTC hour, −1 unknown |
| `r_dur r_kdur` | u16 | `round()` seconds, keystone clock applied; `r_kdur` 0 when absent; clamped 65535 (18.2 h; a Mythic+ timer is ≤ 45 min, a clamp is a data fault and trips health) |

`day` is not a column (the file is the day); the client fills `R.day` per block. Undated rows
(`day = −1`; **0 rows today**) go to a single `rows/undated.<h>.bin` listed **exactly once**,
last in the manifest, so they still count in unfiltered totals exactly as today's bucket-999
rows; the manifest entry's `d`, the `rows` header's `day` and every shard header's `day` for
that file all spell it `"undated"` (the client's block guard compares them; the builder's
state key for the day is `-1` and never leaks into the manifest).

Budget: **≤ 7.5 B/row gz** (byte-planar row columns measured 6.7 B/row before moving the
run-level columns out; expected ≈ 6 B/row). 27 B/row raw in the client after expansion.

### 2.3 `shard` — see §4.2. `cells`/`dist`/`chars`/`comps` — see §3.2. `pairs` — §5.

### 2.4 Stable identities

**Characters — `data/processed/parts/ids/chars.bin`**: an append-only registry
`character@server@region → u32 id`, assigned in journal-arrival order, never reused, never
re-sorted. Only the id ships. It is the equality token for every `Set<R.char>` (per-group
`chars`, `charSeen`, `refChars`, Trends `chars` metric), the index into `CHARSCORE`, and the
id carried by cube `chars` files — so distinct counts **union exactly across row weeks and
cube weeks** (e.g. "Last month" = rows of buckets 0–2 ∪ chars file of bucket 3). The client
counts unions with one generation-stamped `Uint32Array(char_max+1)` (§3.3; 17.6 MB at season
end) rather than a `Set` per group.

**`manifest.char_max` is top-level and equals the registry size at the moment the manifest
is written.** Every file the manifest names was written before the manifest, so no id in any
`rows`, `chars` or `pairs` file it names can exceed it (a character whose only runs are in an
old week and who arrives late still gets an id below the size of the registry that existed
when its file was written). The client sizes the stamp array and `CHARSCORE` from this field
and never from a window-scoped count; `test_cube_equivalence` asserts `max id over every file
the manifest names ≤ manifest.char_max`.

Durability: the registry is append-only, so every run that assigned ≥ 1 id uploads
`ids/chars.<from>-<to>.part` (the appended byte range, a few KB) to the Release **in the
same run, together with that run's `state.json`** (§7.1); the daily slot consolidates the
parts into `ids.chars.bin`. A from-scratch replay of the same journals in arrival order
reproduces the registry byte-for-byte (§6.3). A stateless md5-u32 id was rejected: +1.2 B/row
on the wire and ~260 merged characters by season end, which would make an exact statistic
inexact.

**Runs**: no run id ships. Rows of a run are contiguous in one day file, `run` is day-local
and dense, and the client offsets it by `rbase[day] = Σ runs of earlier loaded days` (per-day
`runs` is in the manifest, so offsets are stable regardless of load order). `runCount = Σ
runs` of loaded days; `RUNS[]`, `runSeen = Uint8Array(runCount)` (C:1673, 1852, 2763) work
unchanged and gap-free. Builder-side, `ids/runs.sqlite` maps `report_code:fight_id → (day,
first_seen_offset)` for **routing** gear/rankings/abilities records to a day, and holds the
**signature table** `sig → (day, report_code, fight_id, roster_n)` used by the cross-day
duplicate-upload collapse (§6.2 step 2). It is rebuildable from the day caches and is not an
identity anyone depends on.

**Categoricals — `data/season.json` (committed, hand-edited)**, §2.5. A value not in the
list is coded as `Unknown` and reported (`vocab.unknown=<col>:<value>:<rows>` in
`build_health.txt`, which the watchdog surfaces); it is never appended silently, because an
append order that depends on arrival would renumber frozen files after a cache loss.

**Items / enchants / builds in shards**: raw Blizzard item id (u32), raw enchant id (u16),
embellishment label code into the manifest's `emb` list, and the first 64 bits of the
talent-tree md5 (`_tree_build_id`, B:867) — all stateless (§4.2).

### 2.5 `data/season.json` (committed; the fetcher, both builders and — via the manifest — the client read it)

```jsonc
{"slug":"s2","name":"Midnight Season 2","zone":55,"epoch":"2026-01-01",
 "start_utc":"2026-08-11T15:00:00Z",                       // first region's first reset of the season
 "reset_rules":{"US":[1,15],"EU":[2,4],"*":[2,22]},      // [weekday Mon=0, hour UTC] — C:1544 verbatim
 "vocab":{"classes":[...],"specs":[...],"heroes":[...],"dungeons":[...],
          "regions":[...],"roles":["DPS","Healer","Tank","Unknown"]},
                                                          // seeded ONCE from the legacy sorted order (enc(), B:2242)
                                                          // so codes equal the legacy payload during dual-emit
 "spec_class":[...],                                       // class index per spec index
 "keep_previous":false,
 "tuning_patches":"data/tuning_patches.json"}              // unchanged file; cutoffs per region as today
```

**The fetcher reads `zone` from this file** (PR-1 replaces the `ZONE_ID = 55` constant,
F:73, with `season.json.zone`; `ENCOUNTERS` moves next to `dungeons` as `encounters:{id:
name}`), so the season's zone is defined in exactly one place and a rollover cannot leave the
fetcher sweeping the old zone.

`season_pins.json` — **the authoritative copy is `data/processed/parts/<slug>/season_pins.json`**
(Actions cache; uploaded to the Release in the run that writes it, so an eviction cannot
re-derive a different pin). `data/season_pins.json` in git is a **mirror** the commit step
copies from it (like `rio_scores`, §7.2). Every chained run is a fresh checkout of the branch
head, so the git copy is routinely *older* than the authoritative one (an upgrade at 10:00 is
not in the 10:20 checkout); the builder therefore never reads git as authority. **Human-edit
detection:** `state.json` records `pins.mirrored_sha` = the sha of the git copy the builder
last wrote out; at the start of a run, if `sha(data/season_pins.json) ≠ pins.mirrored_sha`
**and** the git copy differs from the authoritative copy, the git copy is a deliberate human
edit: it is adopted as the new authoritative copy with an `upgrades[]` entry `{"key":…,
"by":"human"}` and the invalidation of §6.4 follows. A git copy that merely lags (equal to
`mirrored_sha`, or equal to the authoritative content) is ignored. **Machine-written under
the rules below** — the builder writes a pin once and changes it only through a recorded
*upgrade*:
```jsonc
{"pars":{"Murder Row":2040, ...},        // seeded from data/keystone_pars.json on the first run
 "tier_sets":{"Paladin":{"id":2062,"since":"2026-08-14","history":[...]}, ...},
                                          // §5: first-write on ≥ 20k class parses; AUTO-UPGRADED to a strictly
                                          // higher id that clears SEASON_SET_MIN_SHARE on ≥ 20k trailing-7-day
                                          // parses in 3 consecutive daily slots; never auto-downgraded
 "learned":{"hero_markers":"sha256…","tuning_items":"sha256…"},
                                          // §5: content shas of data/processed/parts/learned/*.json (the files
                                          // themselves are Release assets + Actions cache); upgraded daily under
                                          // the same 3-consecutive-slots rule
 "eslots":[0,4,6,7,8,10,11,14,15,16],     // §4.2
 "hero_map_sha":"...",                    // sha of data/hero_talent_map.json the frozen days were built with
 "upgrades":[{"at":"2026-08-21T02:11:00Z","key":"tier_sets.Paladin","from":1985,"to":2062}],
                                          // every automatic change, newest last — the audit trail the
                                          // refuse-to-publish guard (§6.2-5) accepts as an invalidation record
 "format":3}                              // FORMAT_VERSION of the partition writer
```

### 2.6 Manifest — `d/<slug>/manifest.json` (plain JSON; the only `cache:"no-cache"` fetch)

```jsonc
{"v":1,"fmt":3,"slug":"s2","season":"Midnight Season 2","epoch":"2026-01-01",
 "built":"2026-09-02T14:20:11Z","newest_row":"2026-09-02T14:02:40Z","seq":18412,      // seq strictly increasing
 "char_max":2210441,                                  // registry size at build time (§2.4) — TOP-LEVEL, sizes the
                                                      // stamp array and CHARSCORE; never window-scoped
 "reset_rules":{"US":[1,15],"EU":[2,4],"*":[2,22]},
 "anchors":{"US":"2026-01-06T15:00:00Z","EU":"2026-01-07T04:00:00Z","*":"2026-01-07T22:00:00Z"},  // §3.1
 "vocab":{"classes":[...],"specs":[...],"heroes":[...],"dungeons":[...],"regions":[...],"roles":[...]},
 "spec_class":[...],
 "spec_role":[...],                                   // role code per spec code (asserted pure per build, §2.2)
 "emb":["","Radiant Hem", ...],                       // embellishment labels; shard `em` codes index this
 "pars":[1800,1920,...],                              // per dungeon code, from season_pins
 "tuning":{...as today...}|null,
 "projection":{...as today..., "rules_sha":"e1b2…"}|null,   // rules_sha = digest of the tuning rule tables (§5/§6.3);
                                                      // a day file whose header rules_sha differs is unprojected-pending
 "flags":{"tier":true,"timed":true,"tune":true,"proj":false,"rating":true},
 "window":{"day_from":233,"day_to":245,"rows":1412300,"runs":282460,
           "keys":[2,3,...,22],                        // distinct key levels over window rows (seeds klo/khi defaults)
           "refchars":{"|0|0":412000,"DPS|0|0":...,"DPS,Healer|0|0":...,"DPS,Healer,Tank|1|0":...,...}},
                                                      // refChars() under DEF filters, projection off, over the whole
                                                      // window, for ALL 24 reachable keys = 8 role subsets × 3 attack
                                                      // states, keyed with the client's exact string
                                                      // [...state.role].sort().join(",")+"|"+melee+"|"+ranged
                                                      // ("" for the empty role set — never "any") (§8.1)
 "days":[{"d":245,"n":38210,"runs":7642,"frozen":false,
          "w":{"US":[35,36],"EU":[36,36],"*":[36,36]},   // [Wlo,Whi] per region: a reset day holds rows of two weeks
                                                      // (US Tuesday: W before 15:00 UTC, W+1 after); builder bookkeeping
                                                      // only — the client buckets per row from day+hr and never reads it
          "f":"rows/d245.3fa9c1e2ab.bin","b":261040,"rules_sha":"e1b2…",
          "specs":{"3":"spec/mage-arcane/d245.1a2b3c4d5e.bin", ...}},   // spec code → shard block present that day
         ..., {"d":"undated","n":0,"runs":0,"frozen":true,"f":null,"specs":{}}],
                                                      // every window day PLUS every day of an older week whose cube
                                                      // is not published yet (§3.1 cube-gap invariant). THE CLIENT
                                                      // FETCHES EVERY FILE LISTED HERE (§8.2-1b), not only buckets 0–2
 "weeks":[{"w":33,"cube_sha":"7d0a…",                 // generation of the four files below; identical in each header
           "f":{"cells":"cube/w33.cells.….bin","dist":"cube/w33.dist.….bin",
                "chars":"cube/w33.chars.….bin","comps":"cube/w33.comps.….bin"},
           "b":{"cells":190000,"dist":1300000,"chars":1100000,"comps":600000},
           "reg":{"US":{"n":184296,"runs":36859,"chars":70211,"dmin":229,"dmax":236},"EU":{...},...}},
          ...],                                        // EVERY week of the season incl. window weeks (counts only, f and
                                                       // cube_sha omitted until the cube is published)
 "spec_vocab":{"f":"spec/vocab.9f8e01c2d3.json.gz","b":610000},
 "charscore":{"f":"meta/charscore.7c7c….bin","pairs":412000,
              "delta":{"f":"meta/charscore.delta.19ab….bin","pairs":1840}},   // daily base + per-run delta (§5)
 "specstats":{"f":"meta/specstats.….json.gz"},
 "talents":{"f":"talents.7ac1….json.gz"},
 "legacy":{"f":"../../data.json.gz"}}                  // present during dual-emit only
```

`weeks[].reg[].n/runs/chars` are what `weekCounts`, `availWeeks`, `weekTitle` and the period
chips (C:1913–1916, 1985) read; for window weeks they are computed from the very row files
being published, so they equal a row scan bit-for-bit. `d/current.json` =
`{"slug":"s2","manifest":"s2/manifest.json"}`.

---

## 3. Cubes: schema, which views read them, tolerance

### 3.1 Absolute reset week

`anchor[reg]` = first instant ≥ `EPOCH` at that region's reset weekday/hour
(`reset_rules`, mirrored in Python: US Tue 15:00 → 2026-01-06T15:00Z; EU Wed 04:00 →
2026-01-07T04:00Z; `*` Wed 22:00 → 2026-01-07T22:00Z).
`W(row) = floor((started_ms − anchor[reg]) / 604,800,000)`. Client bucket
`b = W(now, reg) − W(row)`, with **`now = max(Date.now(), Date.parse(manifest.built))`** so
a client clock running behind the builder's cannot bucket the leading region's newest rows
into a week the manifest has already advanced past (the manifest is never older than the
rows it names, so this clamp can only move `now` forward to a value the builder saw).

Identity with `computeResetBuckets` (C:1900–1908): let `a` be the anchor in hours, `h0` the
row hour, `hn` now's hour. Legacy `b0 = a + 168·floor((hn−a)/168)`; if `h0 ≥ b0` legacy gives
0 and `W(now) = W(row)`; else legacy gives `ceil((b0−h0)/168) = floor((hn−a)/168) +
ceil((a−h0)/168) = W(now) − floor((h0−a)/168) = W(now) − W(row)`. All boundaries are on the
hour, so truncating ms → hour changes nothing. `tests/test_reset_week.py` asserts this for
every hour of 2026 under all three rules, and `test_reset_rule_tables_match` parses
`RESET_RULES`/`RESET_DEFAULT` out of `site/index.html` (and later `site/next/index.html`) so
the builder's table cannot drift from the client's.

**Rows started after the current reset instant** (an uploader clock running ahead; +3 h has
been seen in production, the fixture plants +6 days): `computeResetBuckets` gives `h0 ≥ b0 →
0` however far ahead the row is, so the identity requires **`W(row) = min(W(row), W(now,
reg))`** on the builder side — `thin.npz`'s `W`, `weeks[].reg`, `days[].w` and the cube
partials all use the clamped week, and the manifest never names a week past `W(now)` of
any region. A day holding clamped rows records the clamp (`days[].w_clamp` in `state.json`,
region → the `W(now)` used) and is **re-queued once that reset has passed** (the client now
buckets those rows one week later; the week's cube, if any, is re-emitted under a new
`cube_sha`).

A cube week `w<W>` holds every region's week `W`. The three anchors lie within 31 hours, so
`curW` differs between regions only inside that band after the US reset; the client applies
`b = curW[reg] − W` **per cell**, exactly as it applies it per row.

**Serving rule.** Let `cubed(W)` = the manifest lists `weeks[].f` for `W`.
- a **cell** of week `W` is used iff `cubed(W)` and its bucket ≥ 3;
- a **row** is used iff its bucket ≤ 2, **or** its week is not `cubed` (the row-served
  fallback for a week whose cube has not been published yet).

No row is ever counted twice and no week is ever half-served. **Cube-gap invariant
(builder):** a week's UTC days stay in `manifest.days` (and their files in `out/`) until that
week's four cube files are named by a published manifest. **Loader obligation (client):**
the client fetches **every** file `manifest.days` lists — buckets 0–2 first, then every
remaining listed day oldest-last (§8.2-1b) — and a period that touches an un-cubed week whose
days are not all resident is **withheld** behind the pending scope line, exactly like a
period waiting for a `dist`/`chars` file (§3.3). Together the two halves are what make "the
client always has either rows or cells for every week the chips offer" true: the builder
keeps the days listed, the client actually loads them. The builder emits
`parts.cube_missing=<W>` in health whenever a week older than the window is still row-served
(the watchdog surfaces it), and `parts.window_days` so a growing window is visible. Which
days a week owns is derived from the rows (`thin.npz` carries `W` per row), never from
`days[].w`, which is informational.

### 3.2 The four files per frozen week

**`w<W>.cells.<h>.bin`** (kind `cells`) — exact sums. Three tables:

Table A `cell` (one row per non-empty cell; header `n_cells`; sorted lexicographically by the
dims in the order listed):

| col | t | |
|---|---|---|
| `reg cls spec hero role dun key` | u8 | `role` is a dimension (it does not add cells because role is pure per spec, §2.2, but it makes the default `role=DPS` filter, C:1588, a plain cell filter rather than a derivation) |
| `timed post tb` | i8 | `tb` = `setBucket(tier)` (C:2614): −1 unknown, 0 (<2 pieces), 1 (2–3), 2 (4+) |
| `n` | u32 | parses |
| `dsum` | u64 (lo/hi) | Σ dps |
| `dth` | u32 | Σ deaths |
| `dz` | u32 | deathless parses |
| `nr` | u32 | distinct runs with ≥ 1 row in the cell |
| `dmin dmax` | u16 | min / max `day` of the cell's rows — makes the KPI "dates covered" (`dmin/dmax` in `aggregate`, C:2771) **exact** under any filter for cube weeks |
| `doff` | u32 | first row of this cell in the `dist`/`chars` files |

Table B `rl` (run-level per group; dims `cls spec dun key reg timed post` — no hero, no tier):
`nr_rl` u32 = distinct runs with ≥ 1 row of that spec in the run; `dup_rl` u32 = runs that
field the same spec twice (different hero or tier splits them across Table-A cells).

Table C `rg` (dims `dun key reg timed post`): `nrun` u32 = distinct runs.

Measured 35,206 Table-A cells per week at (cls,spec,hero,dun,key,reg,timed,tb) before
`post` [measured]; `post` is constant inside a week unless a patch cutoff falls in it, so
budget ≤ 60k cells ≈ **≤ 0.3 MB gz** (42 B/cell raw before compression).

**`w<W>.dist.<h>.bin`** (kind `dist`) — the per-parse distribution, **exact**. Header
`n` = week rows. Rows are ordered by Table-A cell, and **within a cell by `dps` ascending**.

| col | t | |
|---|---|---|
| `coff` | u32 | `n_cells + 1` cell offsets (the `dist` group starts; delta reset points) |
| `dps` | u32, p, d | exact integer dps, delta-coded within the cell (ascending ⇒ small deltas) |
| `deaths` | u8 | per row, same order |

Budget **≤ 2.5 B/row gz → ≤ 1.5 MB per 565k-row week** (a 1.01-log-bin version measured
0.99 B/row; exact sorted deltas are expected ≈ 1.5–2 B/row; the perf test pins it). Exactness
was chosen over the ±0.5% bin because every quantile the site prints — `qp(sorted, p)`
(C:2582) — then reads identical integers in identical order, so the answer is bit-identical
to the row path, and the equivalence story collapses to one exception (§3.4).

**`w<W>.chars.<h>.bin`** (kind `chars`): `char` u32 planar, one per `dist` row, same order.
≈ 2 B/row gz → **≤ 1.2 MB/week**. Loaded when a distinct-character count over that week is
requested (§8.3); the resident policy is "every cube week of the selected period(s)" plus an
LRU of 8 beyond that (§8.5).

**`w<W>.comps.<h>.bin`** (kind `comps`) — **defined over runs with a keystone clock only
(`kdur > 0`)**. Legacy `renderComps` qualifies a run iff `o.pct !== null`, i.e. `par &&
o.kdur` (C:1872, 6892); 15.33% of the seed CSV's runs (17,154 of 111,900) have no clock
[measured], and a cube that counted them would score them as 0-second runs, pull the global
fit, make `best` a 0:00 time and divide `avgkey`/`deaths` by the wrong `n`. So the builder
filters `thin.npz` on `kdur > 0` before grouping (one line; `thin.npz` carries `kdur`), and
the `par == 0` half of the legacy condition stays **client-side per `dun` cell** (the client
skips cells whose dungeon has no par) so a par re-pin needs no rebuild. Header: `K` = the
largest roster in the week (5 today; 18 six-row rosters exist in the seed CSV after the
per-(report,fight,char,server) dedup [measured]), `comps: u16[K] × C` with `0xFFFF` padding
past a comp's length and a `clen: u8 × C` column, member codes `cls*100+spec` sorted exactly
as `buildRuns` sorts them (C:1867) — the client keys a comp from `comps[c][0:clen[c]]` and a
6-member roster is a 6-member comp on both sides. Cells `(comp, dun, key, reg, timed, post)`
for **every** comp in the week (no threshold — exact `n`):

| col | t | |
|---|---|---|
| `comp` | u32 | index into header `comps` |
| `dun key reg` | u8 | |
| `timed post` | i8 | |
| `n` | u32 | clocked runs |
| `ksum` | u32 | Σ kdur (all > 0) |
| `kmin` | u16 | best run's kdur (> 0) |
| `bday bdeaths` | u16, u8 | best run's day and summed deaths (`best` in `renderComps` is the max-score run, C:6900; inside a fixed-key cell that is the min-`kdur` run; on an exact `kdur` tie the builder keeps the first run in content order, the client keeps the first encountered with strictly greater score — `bday` may differ on exact ties and the test tolerates only that) |
| `dsum` | u32 | Σ deaths over the clocked runs |

`pct = (par − kdur)/par·100` is affine in `kdur`, so `Σpct, Σkey·pct, Σkey², Σkey, n` for
the regression (C:6893–6899) and the shrunk mean, best, avg key and deaths (C:6923–6935) are
exact from these sums; storing `kdur` rather than `pct` makes the cube invariant to a par
re-pin. `n` and `dsum` are u32 (a popular comp in one cell with five average deaths passed a
u16 `dsum` at ~13k runs; the cost of u32 is negligible at ~60k cells). Budget **≤ 0.7
MB/week**. Unclocked runs still count in `cells`/`rl`/`rg` (legacy counts them everywhere
except comps), so the Top Comps "n" and the KPI "k-runs" differ for a cube week exactly as
they differ for a row week.

**Generation guard across the four files.** `cells.doff` and `dist.coff` are absolute row
offsets into the *same-generation* `dist` and `chars` files; any invalidation of a cubed week
(§6.4) re-emits all four under new hashes and shifts every later offset. Therefore: (a) the
builder computes one `cube_sha = sha256(sorted thin partials of the week ‖ FORMAT_VERSION ‖
pins that enter the cube)` per emission and writes it into the header of all four files and
into `manifest.weeks[].cube_sha`; (b) the client keys residency of `cells`/`dist`/`chars`/
`comps` by **`(W, cube_sha)`**, not by `W`; (c) a `dist`/`chars`/`comps` file whose header
`cube_sha` differs from the resident `cells` of the same week is rejected unread (never
sliced); (d) the moment a polled manifest shows a different `cube_sha` for `W`, the client
drops all four resident files of `W` at once (the LRU entry is the tuple) and re-fetches only
what the current period needs, withholding meanwhile (§3.3 pre-arrival state). A slice of an
old-generation `dist` at a new-generation offset can therefore never be read.

### 3.3 Which client views read cubes vs rows

| view | rows (buckets 0–2) | cubes (buckets ≥ 3) |
|---|---|---|
| main chart / KPIs / Data table (`aggregate`, C:2759) | as today | `cells` for n/avg/adeaths/deathless/dates covered; `dist` for med/q30/q85/qb/qdA/qdB; `chars` for chars, trust gate, arating/mrating; `rl`/`rg` for runs |
| Compare (A vs B, month presets, any two week sets) | as today | same as above per week set |
| Trends (`renderTrend`, C:7057) | as today | per-bucket points from `cells` (avg/adeaths/deathless/share), `dist` (med), `chars` (chars); gate and top-N per the rule below |
| Pulse (`renderPulse`, C:6549) | now/prev from rows | sparkline tail from `dist` |
| Top Comps (`renderComps`, C:6872) | `RUNS[]` from rows | `comps` cube (§3.4 caveats) |
| Set-bonus table, Archon replica (14-day tail), Frame lens/live stats, Character screen, projection toggle | rows | **not served** — §3.4-4/5 define the scope line for cube-only *and mixed* periods |
| `updateTierHint` count | rows | `cells` (Σ n where `tb ≥ 0`) |

**`aggregate()` becomes two accumulators into one per-group record.** The row part is
today's code unchanged; the cube part appends each passing cell's `dist` slice to `g.dps`,
adds `n/dsum/dth/dz`, extends `dmin/dmax`, and adds `nr_rl`/`nr`. Since `g.dps` is then
sorted once by the existing code, the merge order does not matter.

**Distinct characters — the group-major stamp pass.** A gate is a count over a union, not a
column, so the client computes every per-group distinct-character count with one reusable
`STAMP = Uint32Array(manifest.char_max + 1)` and a generation counter:
1. Once per row-window change, build a CSR index of loaded rows by `groupKey` (`gorder`
   Uint32Array(N) + offsets; 6.8 MB at 1.7M rows, ~20 ms). Once per loaded `cells` file,
   build `Map<groupKey → [cell ranges]>` (cells are sorted with `reg` leading, so a group's
   cells are a handful of contiguous ranges per region).
2. Per group `g` that has ≥ 1 passing row or cell: `gen++`; walk the group's passing rows
   (`gorder`) and, for every cube week in the period, the group's passing cells' `chars`
   slices; `if (STAMP[c] !== gen) { STAMP[c] = gen; count++ }`. `count` is `g.chars` — the
   exact union across row weeks and cube weeks. The reference pool (`refChars`, `charsAll`)
   uses the same array under its own generation.
3. Total work is one touch per row/slice element in the period — the same order as today's
   `Set.add` per row — and the memory is one array regardless of the number of groups.

**Pre-arrival state.** A period that needs a `chars` file (or a `dist` file for a quantile
metric) that is not yet resident **withholds the chart**: the scope line shows the existing
pending message ("N rows pending" style, one line, no spinner) and the chart area keeps its
previous content greyed. The chart is **never rendered ungated and never shows "—" in place
of a gated statistic**; it re-renders exactly once when the last file lands (pref #7).

**Trends gate and ranking (§11.4-19, owner nod bundled with §11.4-6).** Legacy `renderTrend`
gates candidates on `t.chars.size ≥ effMinFor(charsAll.size)` and ranks/top-N by
`calc(t.vals)` with both computed over every loaded row of the season (C:7071–7110). Done
literally over cubes that is a season-wide union per group and, for `med`, a season-wide
sorted bag of up to 13M values per render — all-season `chars` and `dist` residency
(~100 MB at week 23) and ~2 s per filter change. **This blueprint specifies instead:**
- eligible groups = groups whose distinct characters **over the row window** (buckets 0–2,
  rows only) ≥ `effMinFor(window pool)` — the same rule and the same pool as the main chart's
  gate under §11.4-6, one trust gate for the whole site;
- top-N and sort order (`state.trajSort !== "slope"`) = `calc` over the **row window** bag —
  "rank by where a spec stands now, plot how it got there", which is the prediction question
  of pref #5 and #8;
- per-bucket points, Share denominators (`wTot`), rank normalisation and the `slope` fit
  read per-week bags exactly as today: window weeks from rows, cube weeks from `cells` (n,
  Σ), `dist` (per-week `med`: the week's passing slices concatenated and sorted once —
  identical integers, identical order) and `chars` (per-week distinct via the stamp pass);
  the daily fallback (`wks.length ≤ 3`, C:7138) is unchanged because it only ever spans
  row-served weeks;
- Trends is withheld (pending scope line) until the whole window and every cube week's
  `cells` (+ `dist` for `med`, + `chars` for `chars`) are resident.
`sitecalc.py` implements this rule; `test_cube_equivalence` asserts the eligible set,
`trendMin`, the top-N order and every plotted point against it. **If the owner declines
(§11.5), the exact legacy rule is implemented as follows and the heap/time costs above move
into §8.5/§9.3:** resident policy "all cube weeks" for `chars` (and `dist` under `med`); per
group, the season union via the stamp pass over rows + every week's slices; `med` via a
per-group Uint32Array bag (Σ slice lengths) sorted natively, `qp` unchanged. Both variants
share every other line of this section.

**Projection (`state.proj`) is a row-window feature.** `tmul` exists only in day files
(§5). When the toggle is on, **cube weeks are excluded from every accumulator** (aggregate,
Compare, Trends, Pulse, comps, tier hint); their chips are greyed with the tooltip-free
caption "projection covers the last three resets", the toggle's own caption says the same,
and the KPI/Data-table scope line reads "projected · last three resets (N of M parses in
this period)". A mixed period such as "Last month" (buckets 0–3, C:1995) therefore never
blends projected and recorded DPS in one number. `test_cube_equivalence` asserts the
exclusion (cube-week contributions are zero under `proj=1`) and the caption state.

**Projection is also a single-generation feature.** The owner edits the rule tables in
`project_tuning.py` whenever a tuning/PTR analysis comes or goes (pref #8); legacy
recomputes every row against the current tables on every build. Here every day file carries
the `rules_sha` it was built with (§2.2) and the manifest carries the current one
(`projection.rules_sha`). **The toggle is available iff every resident window day's
`rules_sha` equals `manifest.projection.rules_sha`**; otherwise the toggle is greyed with
the caption "projection updating · N of M days" and the pending scope line, and no
projected number is rendered — a day of the old generation is *unprojected-pending*, never
treated as multiplier 1.0 and never dropped. A rules edit dirties every window day newest-
first (§6.4), so the greyed state lasts ≤ 3 cycles under the 8-days-per-run cap and the
current reset's days are the first to catch up. When `manifest.projection` is null the
toggle is hidden as today regardless of per-day columns.

### 3.4 Stated tolerance for cube weeks (everything not listed here is exact)

1. **Distinct runs.** Per group: exact (Σ `nr_rl`) when neither a hero filter (unmerged view,
   `state.hero`) nor a tier box splits the run-level cell; otherwise Σ `nr` over Table-A
   cells, an overcount bounded by Σ `dup_rl` of the passing `rl` cells (measured 431
   hero-split + 377 tier-split of 111,900 runs = **≤ 0.7%** [measured]); the client prints
   the bound as "≤". **Surface rule:** the unmerged view is the default for Spec Frame
   arrow-stepping (`CHART_KEYS` carry hero codes, C:3785), so the Data table's Runs column
   shows "≤" for **every** cube-week period whenever `state.merge` is false, whether or not a
   hero chip is set, and Compare's Runs delta inherits the bound from either side (rendered
   "≤ Δ"). Global `k-runs` KPI: exact (Σ `nrun`) under no roster-dimension filter or a single
   spec; otherwise Σ `nr_rl` shown with "≤".
2. **Comps.** The cube holds clocked runs only (`kdur > 0`, §3.2), which is legacy's own
   qualification; the client additionally skips cells whose dungeon has no par, completing
   `par && kdur`. Qualification honours class/spec, **role and melee/ranged** (all derivable
   from the member spec codes through `spec_role` and the melee table, exactly as `rowPass`'s
   any-member-passes rule), region, dungeon, key, timed, post; only hero and tier filters are
   ignored (they are not in the comp key). Rosters of 6 are 6-member comps on both sides. The
   `median` column is parked last as "—" (pref #12: dashes park last). `strength`, `best` (up
   to the exact-tie note in §3.2), `avgkey`, `deaths`, `n` exact.
3. **Rating cohorts** need the week's `chars` file; the chart is withheld until it lands
   (§3.3 pre-arrival state), never rendered with "—".
4. **Not served** (row-level only): set-bonus cells, Archon replica, frame lens, live stats,
   Character screen, projection. For a period made **only** of cube weeks the surface shows
   the one-line notice "row-level detail covers the last three resets". For a **mixed**
   period (e.g. "Last month" = buckets 0–3) the surface renders from the row-served weeks
   and its scope line reads **"row-level detail covers the last three resets: N of M parses
   shown"**, where `M` is the period's total from `weekCounts` and `N` the rows used; the
   greyed chips of the cube weeks carry the same notice. Character-screen blocks are fetched
   for the days intersecting the row-served part of `weeksA` only.
5. **KPI dates covered** for cube weeks are exact from per-cell `dmin/dmax` (§3.2).
6. **Cross-day duplicate collapse is a one-cycle transient, not a tolerance.** When the
   losing copy of a duplicate upload sits in a frozen neighbour day and this run's dirty
   budget is spent (§6.2-2), the manifest serves both copies for ≥ 1 cycle; legacy never
   does (its export collapses over the whole season). The builder counts these in
   `parts.invalidated_days=<done>/<pending>` so `nightly-compare` (§9.2) treats a non-zero
   `pending` as expected drift for that night, not as a regression; the pending collapse is
   always processed before any older dirty day (it is a correctness item, ordered right
   after today's file).

Every DPS quantile, every count, every mean, every date bound and every distinct-character
count for cube weeks is exact. `tests/test_cube_equivalence.py` (§9.1) asserts exactly this
list, plus the §3.3 gate, projection and mixed-period behaviours.

---

## 4. Sidecar sharding and the row join

### 4.1 The join, before and after

Today: `i → BUILDSC.map[i] → column[r]`, `Int32Array(N)` map, rejected unless `j.n === N`
(C:3538) and every column has ≥ M bytes (C:3559). New: **`map` is still an `Int32Array(N)`
over the client's concatenated window, filled per block**: for a block of day `d`,
`map[rowBase[d] + pos[k]] = shardBase + k`. The guard becomes **per block**: a block is
accepted only if its header `rows_sha` equals the `<h>` of the day file the client actually
loaded (the manifest names both, and both are content-addressed). A mismatching block (a
manifest raced a deploy) is dropped with `console.warn`; that day reads "no gear detail"
until the next manifest poll replaces it; **the Character screen never dies for the session**
(`buildsSidecarFailed`, C:3525, is reserved for a malformed file). Because a day's row file
and its 40 blocks are written by the same build step and named by hash, `pos` can never
drift: any event that changes row positions in a day rewrites that day's blocks in the same
run (§6.4). `bldItV/bldEnV/bldV` (C:3656–3659) keep their signatures; their bodies read
window codes from client-synthesised columns (§4.3). **When the window relayouts (§8.4) —
a mid-window day was invalidated and its `n`/`runs` changed — `rowBase`, `rbase`, `map`
and every synthesised `it/en/bld` column are re-derived from the retained blocks in the same
pass that rebuilds `RUNS`;** the blocks themselves are immutable and are not re-fetched
unless their day's `rows_sha` changed.

### 4.2 `shard` — `d/<slug>/spec/<cls>-<spec>/d<day>.<h>.bin`

One block per (spec code, UTC day) for every day in the window that has ≥ 1 covered row of
that spec. Header: `spec:"Mage|Arcane"`, `spec_code`, `day`, `rows_sha`, `m` (covered rows),
`slots:[0,1,2,4,5,6,7,8,9,10,11,12,13,14,15,16]`, `eslots:[0,4,6,7,8,10,11,14,15,16]`
(the season's enchant slots, pinned in `season_pins.json` from today's measured list; the
vocab file's `eslots` is the window-measured 1% subset the client displays, so the
legacy rule is preserved), `stats:[10 SIDECAR_STATS names]`. Columns over the `m` rows,
ascending `pos`:

| col | t | |
|---|---|---|
| `pos` | u32, p | row index inside that day's row file |
| `fl` | u8 | bit0 gear known, bit1 talent build known, bit2 stats known |
| `it0..it15` | u32, p | raw item id per slot, 0 = empty |
| `em0..em15` | u8 | embellishment label code (manifest `emb`), 0 none — from `emb_identity` exactly as the legacy vocab split |
| `en0..en9` | u16, p | raw enchant id per eslot, 0 none |
| `bld_lo, bld_hi` | u32, p (u64) | first 64 bits of the tree md5 (`_tree_build_id`, B:867), 0 none |
| `st0..st9` | u16, p | stat ratings, 0 unknown (rows without bit2 are zeros) |

**No per-slot vocabulary caps, no size ladder, no nibble packing, no local remap.** Raw ids
are stateless, so a block is immutable once its day freezes and a wearer can never fall out
of a window statistic because it missed a per-day top-N (the flaw the judges found in the
per-day-local-vocab design). Budget ≈ 36 B/covered row gz; largest spec-day ≈ 3.2k covered
rows ≈ 115 KB; the largest spec's whole window ≈ 1.3 MB over ≤ 24 requests, and its "This
reset" subset ≤ 0.5 MB. Per-file tripwire 1 MB → `build_health` warning; **nothing is ever
shed**. Rejected alternative: per-(spec, reset-week) shards (3 requests per spec) — they would
rebuild 40 files of up to 0.6 MB every run and re-download for every open Character screen
every 20 minutes.

### 4.3 `spec/vocab.<h>.json.gz` — all 40 specs, rewritten every run

Exactly today's `specs["Class|Spec"]` object from `builds_sidecar()` (B:1462 ff.), computed
over the **row window** from the per-day `gear.npz` caches (not from the shipped blocks):
`items[16][{id,n,ilvl,iup,ic,cr,emb}]` with the legacy caps (24; 40 on slots 12/13/15/16;
sorted by descending count; `ilvl` median over distinct wearers; `iup` as
`upgrade_surface.md`), `ench[len(eslots)][{id,n}]` (cap 15), `builds[{s,n,sel,bkind}]` (cap
40), `eslots` (window-measured 1% rule), `bkind`. Also the per-spec `w` (distinct wearers)
per item. The client builds, per spec, `Map<(id<<8|em) → windowIndex+1>` per slot,
`Map<enchId → idx+1>` per eslot and `Map<hash64 → idx+1>`, and synthesises the legacy-shaped
`it[si]`, `en[j]`, `bld` Uint8 columns from the raw block columns once per loaded block.
Everything above the three accessors — `csGearModel`, `csPoolModel`, `csEnchHTML`,
`csCraftedModel`, `csTalentRows`, `csFieldMap` (which walks all 40 specs' vocab: this file)
— is unchanged. Names never render as raw ids anywhere (pref #11): an id absent from the
vocab is "other", exactly as today.

**Deliberate change under decision 3, not an equivalence:** the legacy vocab's top-24/40 per
slot is season-wide; the new one is window-wide, so which ids fall into "other" for a
This-reset view can differ mid-season (the fixture's season equals its window, which is why
`test_shard_join` sees them equal). The window vocab is the better answer for the screen's
question (what this spec wears *now*); it is listed in §11.4-20.

`frameLiveStatsHTML` (C:3265) reads `st*` from the same blocks; `stats.json.gz` disappears
as a separate artifact.

---

## 5. The fate of every precomputed block

| block | today | new |
|---|---|---|
| `specstats` | whole df, 14-day tail anchored on the newest run, per-character latest (B:583–640) | same code fed from the open + last 14 days' day caches (`raw.npz` + `gear.npz` stats columns, numpy, no JSON); ships as `meta/specstats.<h>.json.gz`, loaded with the first frame open |
| `specmeta` | emitted (B:2337), **never read** (only a comment at C:3900) | **dropped** |
| `pars` | `derive_pars()` over all rows, every build | **pinned** in `season_pins.json` (seeded from `data/keystone_pars.json`); a dungeon without a pin gets one from `derive_pars` over the window once it has ≥ 500 runs with both outcomes; comps cubes store `kdur` and day files carry no par-derived column, so **`pars` is excluded from `inputs_sha`** and a re-pin rewrites only the manifest |
| `tuning` / `post` | per row vs region cutoff (B:78) | per run in day files (`r_post`); a **new patch** (head of `tuning_patches.json` changes) dirties every day ≥ the earliest cutoff day − 1 and the cube of any week that straddles a cutoff (`post` is a cell dimension, so weeks wholly before/after are constant) |
| `tmul` / `projection` | over `post==1` rows from `abilities.jsonl`; `project()` (P:394–419) learns `items = classify_abilities(rows)` (ability names seen across ≥ `ITEM_CLASS_THRESHOLD` classes) and `tier = tier_sets(rows)` (modal 4pc set per class) **from every ability record of the season on every build**, against the **code-level rule tables** `RULES`, `B_CENTRAL`, `HOTFIX_BAND`, `B_BAND`, `PROJECTION_DATE/LABEL` (P:49–50, 83, 272, 360–386), which the owner edits whenever a tuning/PTR analysis comes or goes | row window only, from the per-day `abil.npz` cache, with `items` read from the **pinned learned table** `learned/tuning_items.json` and `tier` from **`season_pins.tier_sets`** (one tier-set definition for the whole pipeline; `project_tuning.project()` gains an `items=`/`tier=` injection so legacy and new use the same tables in tests). **The rule tables are an input like any other:** `project_tuning.rules_digest()` = `sha256(RULES_VERSION ‖ canonical JSON of RULES, B_CENTRAL, HOTFIX_BAND, B_BAND, PROJECTION_DATE, PROJECTION_LABEL)` (`RULES_VERSION` is a string the owner bumps by convention; the digest catches an unbumped edit anyway) is part of every day's `inputs_sha` (§6.3) and is written into the day header and `manifest.projection.rules_sha`; an edit dirties every window day newest-first under the ≤ 8/run cap and the projection is withheld until every window day matches (§3.3). Whether the `tmul` column exists is decided once per generation on the window (not per day), so no day of a generation is ever "column missing = 1.0". **Not served for cube weeks** (§3.3 projection rule) |
| `charscore` | JSON array parallel to the per-build `char` factorize | `meta/charscore.<h>.bin` (kind `pairs`): sorted `char` u32 planar delta-coded + `score` u16, **every** rated character in the rio journal (`score > 0`), so rating cohorts also work for cube weeks once their `chars` file is loaded; client builds `CHARSCORE = Int16Array(manifest.char_max+1)` filled −1. **The base file is rewritten only at the daily slot**; every run writes `charscore.delta.<h>.bin` (same kind: pairs whose score is new or changed since the base, applied after it), so an open tab re-downloads ≤ 30 KB per cycle instead of 3 MB (~150 MB/day per tab otherwise). `hasRating` and `ratingCoverage` come from manifest counts (`pairs` + `delta.pairs`, and distinct chars in loaded rows) |
| `tier` | season set per class read off the data every build (`tier_pieces`, B:459–484: highest set id clearing `SEASON_SET_MIN_SHARE` = 5% of the class's equipped set pieces, **else the most-worn set** — legacy always assigns once any gear exists) — self-correcting when a new season's set enters circulation | per row at day build against **`season_pins.tier_sets`**. **Pin rule:** the **first daily slot** after a class has ≥ 1 parse with a visible set piece writes the pin with `newest()` **verbatim, fallback included** (so the set-bonus table, tier boxes and `updateTierHint` populate on the new path on the same day they populate on legacy, not 3–4 days later); thereafter the daily slot re-runs `newest()` over the trailing 7 days' `gear.npz` and **auto-upgrades the pin to a strictly higher set id** that clears 5% on ≥ 20k trailing parses in **3 consecutive daily slots** (never a downgrade, never a sideways change; a human edit does both). A first-write made under the fallback is marked `"basis":"fallback"` and is upgraded to the first id that clears 5% on ≥ 20k class parses as soon as one does (a recorded upgrade, no 3-slot wait, because legacy would already show it). An upgrade appends to `pins.upgrades[]` and dirties all days and cubes, newest first (§6.4). Between the first gear record and the first daily slot (< 24 h, season week 1 only) rows carry `tier = −1`; stated in §11.4-29 as the one deviation. `test_rollover` includes the week-1 old-set-first scenario: Paladins log parses in s2's set, the pin is written for s2 (fallback), the s3 set clears the share a week later, the pin upgrades and every s3 day is rebuilt |
| hero resolution | `resolve_hero_talents` (B:141–178) calls `HeroResolver.learn` over every known-hero parse in the df (`MIN_TREE_PARSES = 3`, `MIN_IN_TREE = 0.85`, HR:27–29) — markers relearned over the whole season every build | markers come from the **pinned learned table** `learned/hero_markers.json` (per `"Spec Class"`: hero → marker ability set, exactly `HeroResolver.learn`'s output), applied per dirty day from `abil.npz`. Learning runs in the daily slot over **all** window `abil.npz` caches (≥ 40k parses per spec by week 2, the population legacy had); a new table is adopted only if it differs from the pin and has been byte-identical for 3 consecutive daily slots (hysteresis, so a marker set cannot flap between runs), recorded in `pins.upgrades[]`, `learned.hero_markers` sha updated, all days dirty newest first. Until the first table is pinned (first daily slot after PR-1 ships) `Unknown` stays `Unknown`. Thus a day built in week 3 and one built in week 8 use the same markers unless an upgrade was recorded, and the upgrade rebuilt both |
| learned `items` for `tmul` | as above (P:394) | `learned/tuning_items.json`, same daily learn / 3-slot hysteresis / upgrade record. `tier_sets(rows)` (P:395) is replaced by the tier pin |
| `llms` (`build_llms`, ~90 s every run) | every ordinary run, re-reads the CSV | **leaves the refresh path in PR-1**: `BUILD_LLMS=0` unconditionally in `refresh.yml`; new `.github/workflows/llms.yml` runs it daily at 03:30 UTC on its own runner (reads the CSV until PR-4; afterwards restores the season's day caches from the Release and reads them), uploads `llms.tar.gz` to the Release, **never pushes to the branch and never writes the Actions cache**; the refresh downloads `llms.tar.gz` with `curl -m 30` into `data/processed/llms.tar.gz` (Actions cache, so a fresh runner still has the previous tarball) and unpacks it into `site/` and `docs/` (`scripts/llms_asset.sh`) — a missing, slow or corrupt asset = keep the cached tarball, health line `llms.unpack=stale`; a `fresh` drain run with a cached tarball never downloads (`cached`); **an empty cache and a failed download = build the export inline once with `build_site_data.py --llms-only` (~50–150 s) and pack it into the cache (`built`), so no deploy can drop a tree the previous deploy carried** — the first refresh after PR-1 lands, before `llms.yml` has ever run, and a cache-evicted runner during a Release outage are exactly this path; only a failed inline build ships without `llms/` (`none`), never a red run. The tarball carries an `llms.built` stamp, reported as `llms.built=`/`llms.age_h=` with a `::warning::` past 36 h, because `fresh` means the download succeeded, not that the data is new. PR-1 untracks `site/llms/*`, `site/llms.txt` and `docs/llms/*` (38 files today; ~20 MB/day of history otherwise) |

The two learned tables (`hero_markers`, `tuning_items`), the tier pin **and the tuning rule
digest** are inputs to a day file, so their shas are part of `inputs_sha` (§6.3); `pars` is
not.
`test_rows_bitexact_vs_legacy` runs the legacy side with the same pinned tables injected
(`WOWLOGS_PINS=<path>` read by B's `resolve_hero_talents`, `tier_pieces` and P's `project`),
so `hero`, `tier` and `tmul` are compared value-for-value, not skipped.

---

## 6. The incremental build — `scripts/partition_build.py`

Runs every cycle **in parallel** with the legacy builder during dual-emit. **Two rules bound
it so the deploy can never wait on it longer than on the legacy builder: the partition
builder does no network I/O at all, and it runs under a hard deadline.** The Build step:

```bash
set +e
T0=$(date +%s)
# deadline: the legacy builder's own wall (rolling 7-run median from build_health history,
# default 420 s) minus 60 s; never below 120 s so today's day always fits
PARTS_DEADLINE_S=${PARTS_DEADLINE_S:-$(python scripts/partition_build.py --deadline-default)}
timeout -s TERM -k 30 "$PARTS_DEADLINE_S" \
  python -u scripts/partition_build.py --deadline "$PARTS_DEADLINE_S" > parts.log 2>&1 &
PARTS_PID=$!
python -u scripts/build_site_data.py; rc=$?
wait "$PARTS_PID" || echo "::warning::partition build failed or hit its deadline (see parts.log)"
cat data/processed/parts/health.txt >> site/build_health.txt 2>/dev/null || true
echo "build.step_wall_s=$(( $(date +%s) - T0 ))" >> site/build_health.txt
exit $rc
```

The legacy exit code alone decides the job (`wait $!` on the partition builder would red the
run and stop the self-chain, W:436 `if: success()`, until the watchdog's 75-min revival — the
opposite of the intent). **Deadline semantics:** the builder checks the clock between days,
between stages **and between tail batches of step 1** and stops cleanly at the first
boundary past `deadline − 30 s` (SIGTERM is the backstop, SIGKILL 30 s later); `state.json`
is checkpointed **after every completed day and after every tail batch** (sqlite commit →
registry log → `state.json` last, so a kill between any two loses nothing: the journal offset
moves only in `state.json`, anything past it is re-tailed and the day caches drop the exact
re-appended records), so a multi-day rebuild or a season-long replay drains across cycles
with today's day always first (pref #15) and nothing is ever lost or half-written (every
output is temp+rename, `state.json` last per checkpoint). A deadline stop writes the manifest with whatever days
completed (the previous generation of the rest stays referenced) and `parts.deadline_hit=1
parts.deadline_s=<n> parts.days_left=<k>` in health; three consecutive hits are a watchdog
line. **No network:** Release uploads are staged in `upload/` and performed by
`journal_parts.py` after the deploy (§6.2-4, §7.1); the refuse-to-publish guard's Pages
fetch is `timeout 5` and fails open (§6.2-5); the reseed's Release downloads happen in the
pre-Fetch step (journal/legacy needs only) and, for partition state, inside this step but
**before** the builder starts and under the same deadline (§6.6). Since the legacy builder
ends the step, `wait` can never extend the step beyond `max(legacy wall, deadline) ≤ legacy
wall + 30 s` — `test_build_step_exit` asserts it with a stalled builder. The partition
builder writes its own health file, `data/processed/parts/health.txt` (`parts.status`,
`parts.seq`, …), because legacy `write_health()` (B:51–58) truncates `site/build_health.txt`
when it finishes; the step appends after both exit, so the watchdog and `nightly-compare`
see both. A partition failure is a `::warning::` plus `parts.status=failed`, never a red run
and never a half-written state. 16 GB / 4 vCPU comfortably hold both (legacy ≈ 3–4 GB, new
< 1 GB; the replay's 4 workers, §6.6, ≤ 3 GB together).

### 6.1 State (`data/processed/parts/<slug>/`)

```
state.json        fmt, status (ok|reseed_pending|failed), per APPEND-ONLY journal (players, gear, abilities)
                  {offset consumed, sha256 of the 64 KiB preceding it, seeded: bool} -- advanced per tail batch;
                  arrival counters (arrival_seq, gear_seq, abil_seq: the _seq/_gseq/_aseq stamps a re-tailed
                  batch reproduces); rankings: {snapshot_sha} (it is a per-run snapshot, §6.2-1, no offset);
                  clocks_seeded (the legacy keystone_times.json seeded once, §6.2-1); per-day {inputs_sha,
                  rows_sha, rules_sha, frozen, n, runs, w, w_clamp (§3.1), caches: local|release|both,
                  tar: staged|uploaded}; per-week {cube_sha,
                  frozen, published}; pins snapshot + pins.mirrored_sha (§2.5); projection {rules_sha,
                  has_tmul} per generation; seq; last_manifest_sha; char_registry_size; deadline log
ids/chars.bin     char registry (§2.4) + chars.idx    ids/runs.sqlite   run key → day (routing) + the row's own
                                                       score/medal, signature table, AND the rankings overlay
                                                       table (code,fid) → (score, medal, rank_duration_ms,
                                                       first_seen_run, present-on-the-current-pages) (§6.2-1)
learned/          hero_markers.json, tuning_items.json (pinned by sha in season_pins, §5)
season_pins.json  the authoritative pins (§2.5); git holds a mirror
days/d<day>/raw.npz    canonical rows after dedup/collapse (all CSV columns incl. names, + char_id, hero_resolved,
                       keystone_s — the per-row keystone clock, so a day's tar is self-contained and the clock is
                       inside inputs_sha through the canonical rows, §6.3)
days/d<day>/gear.npz   per covered row: pos, char_id, 16×(item id, emb code, ilvl), 10 enchant ids, hash64, bonus ids, set counts, 10 stats
days/d<day>/abil.npz   compact ability vectors (tuning + hero resolution)
days/d<day>/thin.npz   cube partial: (reg, W, cell dims, dps, deaths, char_id, run_id, kdur, comp sig, roster_n)
upload/                staged Release assets (parts.d<day>.tar.gz, state.<seq>.json, ids/chars.*.part, season_pins) —
                       written by the builder, uploaded and deleted by journal_parts.py after the deploy (§7.1)
out/                   byte-exact mirror of site/d/<slug>/ (copied over each run)
prev/<slug>/           the previous season's final d/<slug>/ tree while season.json.keep_previous is true (§6.7)
```
**Every day inside the row window keeps all four caches locally, frozen or not** (§1.2); a
frozen day's caches are additionally packed into `parts.d<day>.tar.gz` on the Release at
freeze time, and deleted locally only after the day leaves the row window and the Release
copy has been verified (GET-back sha). Step 3 therefore never downloads anything on an
ordinary run.

### 6.2 Per run

1. **Tail the three append-only journals** (`players`, `gear`, `abilities`) from the stored
   offsets. Before consuming, verify the stored `sha256` of the 64 KiB preceding each offset
   against the file on disk; a mismatch means the journal is not the byte stream the offset
   was taken in (a `seed_from_csv()` rewrite, F:187–217, produces a different byte order) →
   treat the journal as **new**: replay from byte 0 with key-dedup against `ids/runs.sqlite`
   and the day caches (idempotent by §6.3), and never combine a CSV-seeded journal (marker
   `players.jsonl.seeded`, written by `seed_from_csv()` in PR-1) with stored offsets.
   `_iter_journal` torn-line tolerance kept; a torn last line is not consumed. Route each
   record to a UTC day: players/abilities carry `started_at`; gear maps through
   `ids/runs.sqlite` (a key not yet known is new, and its players record in the same tail
   names the day; a gear record whose run is unknown parks in `pending/` and is retried for
   7 days). Assign char ids for new names. Mark every touched day **dirty**. **Every batch
   is a checkpoint** (default 100k records, `PARTS_TAIL_BATCH`): the batch's records are
   appended to the day pending files (parked records to `pending/` too), then sqlite commits,
   the registry log flushes, and `state.json` records the consumed offset + its preceding
   sha, the arrival counters and the dirty marks — last. A kill at any instant therefore
   re-tails at most one batch, whose records carry the same `_seq`/`_gseq`/`_aseq` stamps
   (the counters were checkpointed) and are dropped as duplicates when the day's caches
   are read (`dedupe_records` keys on the **arrival stamp alone** — unique per journal record
   by construction and the one projection of a record that survives the cache round trip
   unchanged (the `gseq`/`aseq` columns); the journal-shaped fields do not (`flask`,
   `actor_id`, a guid, ints back as floats), so a whole-record key could never recognise a
   record the cache had already absorbed — the pending file that survives a kill between the
   three cache saves of a day rebuild and its unlink then doubled the day's gear/abilities
   caches for good. Records that merely share content carry distinct stamps and are never
   collapsed, so the legacy readers' last-wins rules see every one; `PARTS_TEST_CRASH_AT=
   day:after_save:<n>` stands in that window). The deadline is checked between batches; a tail stopped at a batch boundary
   (`parts.tail_partial=1`) resumes next run, so a season-long replay drains across cycles
   instead of being SIGKILLed mid-tail forever.

   **`rankings.jsonl` is not append-only and is not tailed.** Every scheduled and chained
   run passes `--resweep` (W:239–241; only an explicit `resweep=false` clears it) and
   `main()` does `RANKINGS_FILE.unlink()` before `sweep()` rewrites the whole ~1,900-page
   snapshot (F:1238–1241). Tailing it by offset would see a "new" journal every cycle and
   dirty every day that still has a run on any leaderboard page — dozens, every run. So it
   is treated as what it is, **a per-run snapshot**: (a) parse it whole (~40 MB, a few
   seconds; skipped when its sha equals `state.rankings.snapshot_sha`); (b) derive the
   per-run **overlay** exactly as legacy `load_fights()`/`export()` do (F:372–401,
   1106–1160): `(code, fid) → (score, medal, rank_duration_ms)` with the same
   last-wins-within-a-page rule; (c) compare each derived triple against the **overlay
   table** in `ids/runs.sqlite`; (d) a day is dirtied **only when a served value actually
   changes** for a run in it. What legacy serves (F:1228–1278): the **clock** is
   the union of every snapshot ever seen (`keystone_times.json` accumulates, a run dropping off
   the pages keeps it; a null or zero `duration` keeps the old clock — legacy's `if ms:`), but
   **score and medal come from the CURRENT snapshot alone**, the row's own value otherwise
   (`jmap.get(col) if jmap.get(col) is not None else v` — per component, so a listed entry whose
   score/medal **turned null after a revision serves the row's own value again**, never the
   stored earlier revision). The overlay table mirrors that with a `present` flag and stores
   the current entry's score/medal **as-is, null included**; a day is dirtied when the
   **served** value changes — served = the stored value if present and not null, else the
   row's own (kept per run in the routing table) — or the clock does: a new medal, a first-seen
   clock, a score revision, a value turning null, a run **leaving or re-entering the pages**
   with a stored value that differs from the row's own; the common case, a run dropping off
   with the value it arrived with, dirties nothing; a run not yet in the routing table parks its triple in the
   overlay table keyed by `(code, fid)` and is applied when its players record arrives (same
   tail or later). The revision-then-drop case is the fixture's `revised_dropped` run. The
   keystone-clock map is persisted the way `data/keystone_times.json` is (the overlay table
   *is* that map plus medal/score/presence); **on its first run (and after a registry loss)
   the builder seeds that legacy file into the overlay once** (`clocks_seeded`; clock only,
   `present=0`) — a builder starting mid-season has seen none of the earlier snapshots, and
   without the seed every run older than the current pages would lose its clock; the day's
   `raw.npz` carries the applied clock per row (§6.1), so a restored tar reproduces the day
   without the snapshot. The snapshot is consumed in three saves (dirty marks → overlay
   commit → `snapshot_sha`) so a kill between any two loses no dirty mark.
   Ordinary run: ~1k runs → ~5k records per append-only journal + one snapshot diff with a
   few hundred changed triples, 1–2 dirty days, ~4 s.
2. **Rebuild dirty days, newest first, at most 8 per run** (a mass invalidation drains over
   cycles while the manifest keeps pointing at the still-valid previous files; today's file
   is never queued behind history). A day rebuild = today's `build()` pipeline on one day's
   frame from `raw.npz` + tail deltas: `drop_duplicates keep="last"` on
   `(report,fight,char,server)` (F:1103), keystone attach, medal/score overlay, keystone
   clock, then the **duplicate-upload collapse, global by construction**: for every run of
   the day with a strong signature (dungeon/key/keystone_s/roster, F:1132–1152; weak while
   `keystone_s` is unknown) look the signature up in the `sig` table of `ids/runs.sqlite`;
   the canonical copy is the one with the largest roster, then the smallest `report_code`,
   **across all days**; a loser found in *another* day marks that day dirty (restoring its
   caches from `parts.d<day>.tar.gz` if they are not local) and is dropped when that day is
   rebuilt — in the same run when the dirty budget allows, else next run (**a pending
   neighbour collapse is ordered immediately after today's day — any dirty day at or past
   today — ahead of every other dirty day**, and counted in
   `parts.invalidated_days=<done>/<pending>`; the one-cycle transient is stated in §3.4-6);
   the winner's day records the event. **The queue is re-derived after every completed
   day**, so a neighbour already built earlier in the same run is rebuilt again in that run
   (a one-shot, replay or rebuild-all build walks newest first and meets the loser's day
   before the winner's — the fixture's reverse-order midnight pair); `parts.days_left`
   counts every day still dirty when the run stops, budget or deadline. A keystone overlay that turns a
   weak signature strong triggers the same lookup. The same-day case is the ordinary path.
   Then hero resolution with the pinned markers, `post`/`tmul` with the pinned `items` and
   the current rule tables (the day's `rules_sha`), `tier` against the pin; sort by the
   content key (§2.2); write the `rows` file, the spec blocks, `thin.npz` (**every** run,
   clocked or not, with `kdur` and `roster_n` per run — the comps cube's `kdur > 0` filter,
   §3.2, is applied at cube emission, not here), update the day's `inputs_sha`; checkpoint
   `state.json`. ~3–5 s per 90k-row day.
3. **Window-level, every run** (numpy over the ≤ 24 **local** window caches — nothing is
   downloaded): `spec/vocab` (~6 s), `specstats` (~2 s), `charscore` (~3 s),
   `window.refchars` / `keys`. Skipped entirely when no window block changed (fingerprint),
   in which case `seq` does not advance and `built` is unchanged.
4. **Freeze**: a day is frozen when it is **quiescent** — no arrival into it for 72 h (the
   `--regear-days 3` refetch, W:36, the newest-first budgeted backlog of pref #15 and the
   rankings resweep all land inside that) — **or** when its end + 7 days < now, whichever
   comes first, and it is not dirty → `frozen:true`; its caches are packed to
   `upload/parts.d<day>.tar.gz` (**staged, not uploaded** — the builder never touches the
   network; `journal_parts.py` uploads it after the deploy, GET-back verifies the sha and
   flips the day's `caches` to `release`/`both` and `tar` to `uploaded` in `state.json`; a
   failed upload leaves the staged file and retries next run) and **stay local until the day
   leaves the window**. When every day touching week `W` (all regions: 9 UTC days, derived
   from the rows' `W`, not from `days[].w`) is frozen, emit `w<W>.{cells,dist,chars,comps}`
   from the `thin.npz` partials (all local by §6.1; concat, sort, group — ~5 s; comps over
   `kdur > 0` runs only) under one `cube_sha` written into all four headers and the manifest
   (§3.2) — roughly 4–8 days after the week closes. The oldest region's week leaves the row
   window 14 days after it closes (the `*` rule closes Wed 22:00 and is bucket 3 two
   Wednesdays later), so the margin between cube emission and window exit is **≥ 6 days** in
   the worst case; the cube-gap invariant plus the loader obligation (§3.1), not the margin,
   is what guarantees a served week.
5. Write the manifest (`seq+1`) — a week's days remain listed until its cube is named
   (§3.1) — prune `out/` to the files referenced by the new and the previous manifest plus
   nothing else (**`*.tmp` and hashed files unreferenced by both manifests are deleted, so
   a cancelled run cannot grow the cache entry**; `pending/` is pruned of records older
   than 7 days), copy `out/ → site/d/<slug>/`, write health lines (`parts.status`,
   `parts.seq`, `parts.window_rows`, `parts.window_days`, `parts.dirty_days`,
   `parts.invalidated_days`, `parts.cube_missing`, `parts.stage.<name>=<s>` per stage,
   per-artifact sizes, any tripwire). **Refuse to publish if a frozen day's `n` would fall
   below the reference manifest's `n` for that day without an invalidation record** (a
   `parts.invalidated_days` entry from this or an earlier run in `state.json`, or a
   `pins.upgrades[]` entry newer than the day's `rows_sha`). The reference is
   `max(seq)` of {the live manifest fetched from Pages **with `timeout 5`, the only
   network call the builder makes and the one it can live without**, the previous local
   manifest}; if the fetch times out or fails the guard **fails open** with
   `parts.guard=skipped` in health — a CDN one generation stale or an outage must never
   block the current reset. Stage `state.<seq>.json`, `ids/chars.<from>-<to>.part` and (when
   written) `season_pins.json` into `upload/` for `journal_parts.py`.

Ordinary run: **≈ 30–60 s**, independent of season length; +20–30 s cache I/O (§1.2).

### 6.3 Idempotence and determinism

`inputs_sha(day) = sha256(canonical rows ‖ gear digest ‖ abil digest ‖ FORMAT_VERSION ‖
tier_sets pin ‖ learned.hero_markers sha ‖ learned.tuning_items sha ‖ **rules_sha**
(`project_tuning.rules_digest()`, §5) ‖ hero_map_sha ‖ eslots ‖ patch id **for the days the patch can touch** (day ≥ earliest cutoff − 1, and the undated day — the §6.4 scope; a constant for every earlier day, whose `post`/`tmul` no patch can change, so a patch change leaves those files byte-identical to a from-scratch replay's) ‖ vocab sha)` —
**`pars` is deliberately absent** (no day file or block reads it). "Canonical rows" are the
`raw.npz` rows **including the applied keystone clock `keystone_s` and the rankings overlay
(score, medal)** — so the clock source is inside the digest and a day's tar reproduces the
day regardless of which `rankings.jsonl` snapshot or `keystone_times.json` a restored runner
holds. A `rows` file, a block, a cube are pure functions of their inputs (no timestamps
inside), so rebuilding with unchanged inputs writes byte-identical files and the manifest
does not change. A from-scratch replay of the same journals in the same arrival order with
the same pins and rule tables reproduces every artifact byte-for-byte, char registry
included (`test_incremental_idempotent`, §9.1). `inputs_fingerprint()` (B:3363) is not used
by the new builder.

### 6.4 Invalidation matrix

Each row below is one branch of `Builder.prepare_pins`, compared component by component
(`pins`, `vocab`, `fmt`, `rules`, `patch`) — never through one combined digest, so a
rule-table edit or a new patch can never widen into the all-days rows — and labels the days'
`state.days[].reasons` with its own name (`pins`, `vocab`, `format`, `rules`, `patch`,
`rebuild_all`, `arrival`, `overlay`, `collapse`, `future`), so health and
`state.invalidations` can explain a mass rebuild.

| trigger | scope | cost |
|---|---|---|
| late upload / regear refetch / a **changed** rankings overlay value (medal, score, first-seen clock) for a run in a closed day | that day (rows file + its blocks + partial) → its week's four cube files under a **new `cube_sha`** (if frozen) → `parts.d<day>.tar.gz` re-staged | ~10 s; `parts.invalidated_days=` in health. A run merely dropping off the leaderboard pages is **not** a trigger (§6.2-1) |
| cross-day duplicate-upload collapse (a copy of a run already frozen in day `d` arrives into `d±1`, or a keystone overlay strengthens a signature whose twin is in `d±1`) | the neighbour day too (same path as above), ordered right after today's day | ~10 s per day; one-cycle transient stated in §3.4-6 |
| new tuning patch (`patches[0]` changes) | `post`/`tmul` of days ≥ **min(old, new) earliest cutoff − 1** — `post` is relative to `patches[0]`, so the rows between the previous cutoff and the new one flip too; the previous patch's earliest day is kept as `state.static_inputs.patch_day` (unknown → every day); the undated day; the straddling weeks' cubes re-emit through the changed `thin` partials | ≤ 1 min |
| **tuning rule-table edit** (`rules_digest()` changes: `RULES`, `B_CENTRAL`, `HOTFIX_BAND`, `B_BAND`, `PROJECTION_DATE/LABEL`, or `RULES_VERSION`) | `tmul` of every **window** day (newest first, ≤ 8 per run); no cube (cubes carry no `tmul`); the projection is withheld until every window day's `rules_sha` matches the manifest's (§3.3) | ≤ 3 cycles; today's file first |
| automatic pin upgrade (tier set, learned hero markers, learned tuning items — §5) | all days (newest first, ≤ 8 per run) and all cubes; recorded in `pins.upgrades[]` | ≤ 15 min spread over cycles; today's file first |
| human edit of `season_pins.json` (tier set, par, hero map), detected as git copy ≠ `pins.mirrored_sha` **and** ≠ authoritative copy (§2.5), or `FORMAT_VERSION` bump | all days (newest first, ≤ 8 per run; or `workflow_dispatch rebuild_all=true` with the 110-min timeout does it in one go, ≈ 4 s/day — `--rebuild-all` lifts the per-run cap itself, the deadline stays the only bound) | ≤ 15 min once; the manifest is written after the current day and after every checkpointed day, so fresh rows never wait. A git copy that merely lags the authoritative one (the ordinary chained-run case) triggers nothing |
| `season.json` vocab edit | all days | as above |
| a reset passing over a day that holds rows started after it (§3.1 `w_clamp`) | that day → its week's cube | rare; ~10 s |
| char registry lost (cache + Release both gone) | full replay in arrival order from journal parts; every file renames | 12–15 min once; clients re-download the window (~11 MB) |

### 6.5 Retention: three generations, or younger than 15 minutes

`out/` keeps every file referenced by the current **and the two previous** manifests, plus
any hashed file written in the last 15 minutes whatever references it. Two generations were
tight against the 10-min edge TTL once PR-4 makes builds < 1 min: drain-mode fresh/backfill
runs deploy ~4 min apart, an edge-cached manifest can be three generations old, and the
`no-cache` refetch after a 404 revalidates against the same stale edge copy. Cost ≈ 5 MB. A
tab holding an older manifest can therefore always fetch its files inside the edge TTL; a
404 after that re-fetches the manifest and retries once (§8.5).

### 6.6 Fresh runner (cache evicted)

The reseed is **split in two so that nothing partition-related ever runs ahead of Fetch**:
the pre-Fetch half restores only what Fetch and the legacy builder need (~1–2 min of I/O,
what today's eviction run already spends in `seed_from_csv()`), and the partition half runs
inside the parallel Build step under the deadline of §6, today's day first, checkpointed per
day. `state.json` carries `status: reseed_pending` until the partition half has finished, so
the retry condition is *that flag*, not "state.json missing" — a partially seeded runner
(journals present after a failed reseed or a CSV seed) keeps retrying every run until the
Release restore succeeds, and never combines the journals it already has with stored
offsets unless the `(offset, sha)` pairs were written from those very bytes.

**Half A — before Fetch** (`scripts/reseed_from_release.py --journals`, W after :188), when
`data/processed/parts/<slug>/state.json` is missing **or** `status == reseed_pending`:
1. `curl -m 60` `release_manifest.<seq>.json` (highest seq, Release `data-<slug>`, §7.1) →
   the journal parts **after** the consumed offsets recorded in the newest `state.<seq>.json`
   (PR-1..3: also the folded prefix, because the legacy `export()` still reads whole
   journals), `done-index.txt.gz` → `summaries_done.txt`, `keystone_times.json.gz`,
   `rio_scores.csv.gz`. Journals already on disk are **extended** only if their existing
   bytes' sha matches the part they overlap; otherwise the on-disk journal is moved aside
   to `stale/` and rewritten — never merged blind. Write the per-journal `(offset, sha256 of
   the preceding 64 KiB)` pairs into a **provisional** `state.json` (`status:
   reseed_pending`) from the bytes just written.
2. If the Release is unreachable: **until PR-4** fall back to `seed_from_csv()` (the CSV
   exists; the run is slow, not red; the seeded journal carries the `.seeded` marker so
   §6.2-1 replays it from byte 0 instead of seeking) and keep `status: reseed_pending`;
   **from PR-4** (no CSV) exit 1 — no fetch, no build, watchdog woken.

**Half B — inside the Build step, before `partition_build.py` starts, under
`PARTS_DEADLINE_S`** (`reseed_from_release.py --state`), while `status == reseed_pending`:
3. Download the newest `state.<seq>.json`, `ids.chars.bin` **plus every `ids/chars.*.part`
   newer than it** (so the registry is the one that existed at the last run that assigned
   ids, not the last daily slot), `season_pins.json`, `learned/`, `ids.runs.sqlite`
   (routing + signature + overlay tables) and `parts.window.tar.gz` (daily). Each download
   is `curl -m 120`; a failure leaves `reseed_pending` set and the builder proceeds with
   what it has (today's day from the journal tail is always buildable; the manifest it
   writes lists only days it can vouch for, so the site shows fewer old days, never wrong
   ones).
4. Fetch every frozen output the live manifest names from the public Pages URL
   (`https://st331.github.io/wowlogs/d/<slug>/...`) and accept a file **only if its
   `rows_sha`/`cube_sha` equals the entry in the restored `state.json`** (a name-hash check
   alone would accept a day file built *after* the snapshot with ids the restored registry
   does not know — the X/Y id-reuse fault). Any day or week whose live sha differs, or which
   `state.json` does not know, is queued as dirty and rebuilt from its `parts.d<day>.tar.gz`
   / the journal parts **by the ordinary per-run loop, newest first, ≤ 8 days per run under
   the deadline**; **the site is the durable store of built outputs, `state.json` is the
   authority on which build they belong to.** `status` flips to `ok` when no queued day
   remains.

Normal case: Half A ≈ 1–2 min before Fetch (no worse than today's CSV seed), Half B ≈ 1–3
min in parallel with the legacy builder, zero rebuilds. Worst case (day tars lost too): a
**full replay from journal parts** — at 13M rows ≈ 13M gear records × ≥ 15 µs ≈ 3–5 min
parse + ~170 day rebuilds × ~4 s ≈ 11 min + ~7 GB gz streamed ≈ 3 min ⇒ **20–25 min
single-core, ≈ 8–10 min with the replay's 4 worker processes (one day per worker, ≤ 3 GB
RSS together)** — and it drains across cycles under the deadline with today's day first,
so **the eviction run's time-to-deploy is ≤ today's eviction run** (PR-1 acceptance, §10)
and no run can exceed the 50-min timeout because of it.

### 6.7 Season rollover

Edit `data/season.json` (`slug:"s3"`, `zone`, `start_utc`, `dungeons`, `encounters`,
cleared `season_pins.json`) and commit; `tests/test_rollover.py` rehearses it on a synthetic
season with a new zone id, a new dungeon **and one dungeon shared with the previous season
that keeps its encounter id** (returning dungeons keep their encounter ids across seasons),
plus the week-1 old-tier-set-first scenario of §5. **Rows are routed to a season by
`enc ∈ season.encounters` of the season whose `start_utc ≤ started_at`, else by
`started_at ≥ season.start_utc` alone** — the rankings record carries `enc`/`bracket`/`page`
(F:275), not a zone, so the zone id is *not* the routing key; never by dungeon name; a
straddling UTC day cannot mix.

**Close-out of the old season (the run that first sees the new slug, before anything for
`s3`):** every remaining week of `s2` is **force-frozen and cubed** — dirty or quiescent or
not, the last ~3 weeks included, because those are the weeks pref #5's season-over-season
prediction cares about most — the final `s2` manifest is written with a cube for **every**
week and `days` reduced to the last three resets, and the `d/s2/` tree (≈ 100 MB: final
window rows + all cubes + blocks + vocab) is packed once as `site_final.tar.gz` on `data-s2`.
The close-out runs under the §6 deadline like any rebuild and may span 1–3 cycles; until it
completes, `current.json` still points at `s2` and `s3` rows are journaled but not built —
a deliberate ≤ 1 h delay on the *first day of a new season*, accepted because the
alternative (a permanently un-cubed tail of the previous season) is a silent wrong number
under `?s=s2` forever. `test_rollover` asserts the old slug's final manifest names a cube for
every week. Then the run opens Release `data-s3`, fresh slug-scoped state/registries, writes
`d/s3/`, flips `current.json`. While `season.json.keep_previous` is true the previous
season's tree lives cache-resident at `data/processed/parts/prev/s2/` (restored once from
`site_final.tar.gz` only on eviction, in Half B of §6.6) and is copied into the Pages
artifact each run — never downloaded on the critical path. The client browses it via `?s=s2`
(pref #5), and a tab left open across the rollover flips because the poll reads
`current.json` first (§8.4).

---

## 7. Journal partitioning, Release-asset snapshots, the new daily commit

### 7.1 `scripts/journal_parts.py` (runs **after the deploy step**, like the rio upload)

**Three** of the journals are append-only, so their snapshot is a byte range: parts
`players.p0001.jsonl.gz`, `gear.p0001.jsonl.gz`, `abilities.p…` are closed at ≥ 64 MB gz or
at the 02:00 daily slot and never touched again. **`rankings.jsonl` is a per-run snapshot
(§6.2-1) and is not partitioned**; its durable form is the overlay table inside
`ids.runs.sqlite` (daily) plus `keystone_times.json.gz` (daily, legacy's own map); the latest
`rankings.jsonl.gz` is uploaded daily as a convenience for `llms.yml`, nothing depends on it.
Per-day caches of frozen days ship as `parts.d<day>.tar.gz` (~8 MB) **from the builder's
`upload/` staging directory: this script performs the upload, GET-back verifies the sha,
flips the day's `caches`/`tar` fields in `state.json`, and deletes the staged file** — the
builder itself never opens a socket (§6). **Every run** uploads the staged `state.<seq>.json`
and, when ids were assigned, `ids/chars.<from>-<to>.part` (tiny; this is what makes the
registry restorable to the last run rather than the last day). Daily-overwritten assets:
`ids.chars.bin` (consolidated), `ids.runs.sqlite`, `parts.window.tar.gz`, `learned.tar.gz`,
`season_pins.json`, `keystone_times.json.gz`, `rio_scores.csv.gz`, `done-index.txt.gz` (all
`code:fid OK` pairs → `summaries_done.txt`), and — dual-emit only — `mythic_runs.csv.gz`,
`gear.jsonl.gz`. **The daily consolidation deletes every per-run `state.<seq>.json` and
`ids/chars.*.part` older than the consolidated `ids.chars.bin` it just verified** (~96/day
would otherwise reach ~15k assets by season end; the live inventory stays ≈ 1k). Uploads use
the REST uploads API with `github.token` (`contents: write` already granted, W:102).
**Overwrite protocol:** the REST "clobber" is delete-then-POST, not atomic, so every daily
asset is uploaded under a dated name (`state.20260902.json`), and the release manifest itself
is **never replaced in place**: each run uploads `release_manifest.<seq>.json` after a
GET-back sha check of every asset it names, readers take the highest `seq`, and manifests
older than the newest daily consolidation are deleted last; a failure anywhere leaves the
previous generation intact and retries next run. `refresh.yml` is the **single writer** of
`release_manifest.<seq>.json` (its `wowlogs-refresh` concurrency group, cancel-in-progress
false, is the lock); `llms.yml` and `nightly-compare.yml` write disjoint assets and never a
manifest. One Release per season, tag `data-<slug>`, marked pre-release "machine-managed".
Largest asset ≈ 64 MB against the 2 GB cap. Nothing here is on the critical path to the
deploy: the daily csv.gz + gear.jsonl.gz set (300–500 MB by week 8), the day tars and the
weekly part close (~1 min gzip + upload) run after the site is live.

### 7.2 The daily commit

Shrinks to `data/release_manifest.json` (a copy of the newest `release_manifest.<seq>.json`:
asset names, sizes, sha256, journal offset ranges, day → week map, line counts),
`data/season_pins.json` (**copied from the authoritative
`data/processed/parts/<slug>/season_pins.json` in this step, exactly like `rio_scores`,
W:393, and its sha recorded as `pins.mirrored_sha` in `state.json` so the next run can tell
a lagging mirror from a human edit, §2.5**), the small name caches and icons.
`mythic_runs.csv.gz` is committed daily until PR-4 **only while `too_big()` allows** (W:399;
the Release copy is the durable one from PR-1 on, so the git copy going stale is harmless);
**`data/keystone_times.json` (~82 B/run, 9 MB today, 100 MB near week 11) goes behind the
same `too_big()` guard in PR-1** — its durable copy is the daily Release asset and the day
caches carry the applied clock, so a stale git copy is harmless too; the Monday
`gear.jsonl.gz` commit is removed in PR-1 (it already cannot land). The commit
step runs `git pull --rebase --autostash origin <branch>` before `git push` (W:429) and is
`continue-on-error: true` with a `::warning::` — a rejected push (an owner UI push in the
last 20 minutes) is retried by the next cycle's commit, never a red run, and nothing the
runner needs lives only in that commit (pins and the release manifest are also on the
Release).

### 7.3 Clobber guard (PR-1, transition safety)

`export_gear()` and `export()` **skip the export — nothing written, the run proceeds to
build and deploy** — when the journal they are about to export from holds < 90% of the line
count recorded in the newest release manifest. The skip is a `::warning::` plus the health
line `export.skipped=clobber_guard journal=<name> have=<n> expect=<m>`, which the watchdog
surfaces like `newest_row`. It is deliberately **not** a red run: the durable copy is already
safe (the dated-name protocol of §7.1 never overwrites it), so the only thing a red run would
add is a site freeze — a cache eviction during a Release outage would otherwise fetch and
journal every 20 minutes and never deploy until the outage cleared, whereas today the same
eviction deploys fresh rows with a thin sidecar. With the skip, that run deploys from the
legacy path exactly as today's eviction run does, and `status: reseed_pending` (§6.6) keeps
retrying the restore every run until the Release is back; the guard clears itself when the
journal is whole again. Silent truncation of the durable copy is still impossible; it is now
a loud *skip* instead of a loud *stop*.

### 7.4 Disk and the legacy builder's clock

The hot journals stay whole during dual-emit because the legacy `export()` reads them whole.
Raw `gear.jsonl` grows ~0.5 GB/week; disk alone would allow dual-emit until season week ~10
(≈ 5 GB hot + 3 GB CSV/pandas headroom on the 14 GB disk). **The binding clock is the
legacy builder's wall time, not disk** (§0.1): its four gear-journal passes are O(season) at
≈ 140 µs per record-pass, which crosses the 50-min job timeout around week 8–9 and — worse
for the owner's first priority — pushes mean time-to-fresh from ~23 min to ~45 min by week 6.
PR-1 therefore changes the legacy builder in one contained way: **`gear_journal_pass()`, a
single walk that parses each line once and feeds the four consumers** (`sets_from_gear_journal`,
`stats_from_gear_journal`, `meta_from_gear_journal`, `_trait_journal_pass`, B:369/532/894/
1219), **guarded by a byte-level prefilter on `report_code` against the set of runs the
legacy payload actually samples** (`sample_runs`, B:296, is moved ahead of the passes; it
depends only on the df). Records of unsampled reports cost a substring test (~2 µs) instead
of a parse, so the legacy build is **O(sample) for the rest of dual-emit, flat at today's
~7 min**. The prefilter's granularity is the **report** (`report_code`), not the run: every
fight of a sampled report passes, measured at ~1.8× the sampled records. **It applies to
sets/stats/meta only** — those consumers only ever read sampled keys, so for them it is exact.
It is **not** exact for the trait union (`_trait_journal_pass`: which talent entries and hero
subtrees a spec's players *ever* allocated, the modal selection blob of a build): `talents_doc`
draws tree geometry and hero panes from it journal-wide, and computing it over sampled records
lost one node in 40/40 specs and Retribution's Lightsmith pane on an adversarial journal (~34%
of reports are sampled today, ~11% by season end; revision 3 wrongly called this change
invisible). **The trait union is therefore complete and incremental** (`TraitUnion`, stage A
follow-up): the journal is append-only and the union only grows, so it is persisted as
`data/processed/trait_union.json.gz` — inside the existing cache path list, no new path — with
a checkpoint {source, byte offset, size, sha of the first 64 KB, sha of the 64 KB before the
offset, line and record counts}. Each build parses **only the bytes appended since the offset**
(O(new records), ~1–2k per 20-min cycle) and merges; a checkpoint that no longer describes the
file (evicted cache, a reseed that rewrote the journal, a file shorter than the offset)
triggers **one** whole-journal rebuild. A torn trailing line is merged for that build's
consumers when it parses but never committed to the checkpoint. Tree-hash builds' blobs are
not persisted (the hash is the blob's md5; the sampled pass of the same run supplies the blob
for any build it can want), so the state is ~30 bytes per distinct build. `build.wall_s`,
`build.gear_records_parsed`, `build.trait_union_mode=incremental|rebuild`,
`build.trait_union_rebuild=<reason>`, `build.trait_records_parsed`, `build.trait_union_records`
and `build.trait_union_s` are written to `build_health.txt`, `build.wall_s` with a tripwire at
10 min (`::warning::` + watchdog line). `test_trait_union` proves `talents.json.gz` and
`builds.json.gz` byte-identical three ways — HEAD-style whole walk, cold checkpoint, two
incremental appends — on that adversarial journal. **The fetch side has its own clock:** `export()` materialises `list(_iter_journal(PLAYERS_FILE))` as
dicts (~2 KB per row) — ≈ 6 GB at week 6 and ≈ 10 GB at week 10 on a 16 GB runner, an OOM
path that would precede the 10-min wall trigger. PR-1 therefore makes `export()` stream the
players journal in 200k-row chunks into the frame (the dedup and roster collapse already
work on the frame), and writes `export.wall_s` and `export.rss_mb` (`resource.getrusage`) to
health with tripwires at 5 min / 6 GB. Dual-emit ends when §10's preconditions are met,
**target season week 6, hard stop when `build.wall_s` exceeds 10 min, or `export.wall_s`
exceeds 5 min, or `export.rss_mb` exceeds 6 GB, on two consecutive days** — a measured
deadline, not a calendar one. After PR-4, `journal_parts.py` truncates a journal's folded,
uploaded and verified prefix, keeping the runner at ≤ 1 GB of hot journals for the rest of
the season.

---

## 8. Loader protocol (the CLIENT implementer works from this section)

### 8.1 First paint — nothing on the critical path but the current reset

1. `GET d/current.json` → `GET d/<slug>/manifest.json` (both `cache:"no-cache"`). Build `D`
   from the manifest (`vocab`, `spec_role`, `pars`, `tuning`, `projection`, `epoch`,
   flags). `now = max(Date.now(), Date.parse(manifest.built))` (§3.1). Compute
   `boundsH/boundsD` per region exactly as C:1889–1898 and `curW[reg]`. Bucket-0 days are
   `manifest.days` with `d ≥ floor(min_reg boundsH[reg] / 24)`. Allocate `STAMP =
   Uint32Array(char_max+1)` and `CHARSCORE = Int16Array(char_max+1)` from the top-level
   `manifest.char_max`.
2. Fetch those day files in parallel (1–9 requests, `cache:"default"` — names are
   immutable). Preallocate the window columns at `manifest.window.rows`; place each block at
   `rowBase[d]`, expand the run block through `run`, fill `R.day`, compute `rbucket` and
   `atk` per block; rows not yet loaded carry `rbucket = 999` and are excluded by
   `periodPass` and every count. `weekCounts`/`availWeeks` come from `manifest.weeks[].reg`
   (mapped `W → bucket` per region), so the period chips are complete before any row lands.
   The filter pools `clsSpecs`/`csHeroes` (C:1675–1684) are built from the manifest vocab
   and, for `hero`, from the union of loaded rows and every loaded `cells` file — a hero or
   spec present only in cube weeks stays selectable under "All season". Key defaults
   (`kmax`, `khi`, `klo`) come from `manifest.window.keys`, not from loaded rows;
   `refChars()` returns `manifest.window.refchars[key]` with **the client's own key string**
   (`[...state.role].sort().join(",")+"|"+state.melee+"|"+state.ranged`, C:2645 — all 24
   reachable keys are precomputed, so the lookup never misses and never falls back to a
   partial-window scan) (projection off) until the full window has landed, then recomputes
   over loaded rows — identical by construction (§9.1 asserts it). Two transients
   self-correct on the §8.2-1 re-render and are covered by the pending scope line meanwhile:
   with the projection toggle on before the window lands, `refChars` uses the projection-off
   pool (`refChars` skips `projSkip` rows, C:2657); and `aggregateElite` (Archon replica,
   14-day tail from `curMaxDay`) computed before buckets 1–2 land is incomplete. The
   projection toggle itself is enabled only once every resident window day's `rules_sha`
   equals `manifest.projection.rules_sha` (§3.3).
3. `initData` as today minus JSON; `#loading` hidden; `render()` — **first paint**. Budget
   (restated from the measured 535k-row reset week and 118k-row reset day): ≤ 4.5 MB on the
   last day of a week ⇒ **≤ 1.3 s at 50 Mbps**; ≤ 1.0 MB / ≤ 0.4 s on reset day; returning
   visitor: manifest + today's file (≤ 1.1 MB on the busiest day), < 0.5 s. Today: 2.4 s and
   7.2 MB every visit.
4. `csRestoreFromHash()` last, as today (C:1778). **Recorded dependency:** this is sound only
   because the boot period is `{0}` (C:1738) and bucket 0 is fully loaded before the first
   `render()`, so `CHART_KEYS` for `{0}` is final and the `#cs=` hash resolves exactly as
   today (C:3796–3800). If a future change persists the period across reload, the restore
   must be deferred until that period is fully served (the same "withheld" state as §3.3),
   otherwise `k == null` silently drops a bookmarked screen.

### 8.2 Background, fixed order; each step re-renders exactly once when complete (pref #7)

1. Bucket-1 then bucket-2 day files → Compare "This vs last reset", Pulse `prev`, Archon
   14-day tail complete; `refChars` recomputed once; `gorder` CSR built; `render()`.
   1b. **Every remaining file in `manifest.days` not yet loaded, newest first (so the
   oldest lands last)** — these are the days of un-cubed older weeks (a cube gap, §3.1) and
   the row-served tail of a previous season under `?s=`; normally zero files. A period that
   touches an un-cubed week is **withheld** (pending scope line, §3.3) until all of that
   week's listed days are resident; `weekCounts` from `manifest.weeks[].reg` is complete
   from the start, so the chips never lie about what the period will hold. Row arrays are
   preallocated at `manifest.window.rows`, which the builder defines as Σ `n` over **every**
   listed day, not over buckets 0–2. A period made only of bucket ≤ 2 weeks never waits on
   this step.
2. `charscore` base, then `charscore.delta` → rating columns fill.
3. All `weeks[].f.cells` newest → oldest (≈ 0.25 MB each) → Trends (default metric `avg`,
   C:1598), month presets and Compare over old weeks go live for count/mean metrics; the
   Trends gate needs only the window (§3.3), so Trends renders as soon as step 1 and this
   step are done. Each `cells` file is accepted only if its header `cube_sha` equals
   `manifest.weeks[].cube_sha` for that week, and is stored under the key `(W, cube_sha)`.
4. `weeks[].f.dist` newest → oldest at idle priority (`requestIdleCallback`) → Pulse
   sparkline, quantile metrics extend week by week. Resident policy: **every cube week of the
   selected period(s) A/B and of Trends' span under `med`**, plus LRU 8 beyond, **keyed by
   `(W, cube_sha)`**; a `dist` whose header `cube_sha` ≠ the resident `cells` of that week is
   rejected unread (§3.2); evicted weeks re-read from the HTTP cache.
5. `weeks[].f.chars` at idle for buckets 3–7 (the two month presets); every cube week of a
   selected period on demand. Resident policy and generation check as for `dist`.

### 8.3 Interaction-gated

| interaction | fetch |
|---|---|
| bar click (`openFrame`) / Character screen (`enterScreen`) for spec S | `spec_vocab` (once) + `specstats` (once) + `days[d].specs[S]` blocks for days intersecting the row-served part of `state.weeksA`, newest first; more days when the period changes |
| Talents pane with `sel` | `talents` as today |
| a period containing a bucket ≥ 3 with Comps visible | those weeks' `comps` files (generation-checked against the resident `cells`) |
| a period touching an un-cubed week (bucket ≥ 3, `weeks[].f` absent) whose listed days are not all resident | nothing new — step 1b is already loading them; the period is withheld with the pending scope line until the last of that week's days lands, then re-renders once |
| a distinct-character statistic (any gate, `chars` column, rating cohort) over a bucket ≥ 3 not yet resident | **all** of that period's non-resident `chars` files; the chart is withheld with the pending scope line until the last one lands (§3.3), then re-renders once |
| a quantile metric over a bucket ≥ 3 not yet resident | that period's `dist` files, same withholding rule |
| a cube-only period in Frame / Character screen / Set-bonus / Archon | nothing; one-line notice (§3.4-4); a mixed period renders the row-served part with the "N of M parses shown" scope line |

### 8.4 Refresh without reload

The existing 180 s poll (C:7441–7477) polls **`d/current.json` first (40 B; a changed slug
means a season rolled over under an open tab — reload the page rather than mixing seasons)**
and then `d/<slug>/manifest.json`, instead of index.html's ETag (the UI-deploy check stays).
On a higher `seq`: fetch only files whose names changed (normally today's row file, today's
blocks of the open spec, `charscore.delta`, `spec_vocab` if the screen is open), rebuild the
window arrays (memcpy of unchanged blocks, ≤ 50 MB, ~20 ms), re-derive `rowBase`/`rbase`,
rebuild `RUNS`, **re-derive the sidecar `map` and the synthesised `it/en/bld` columns from
the retained blocks** (a mid-window invalidation shifts every later day's base), rebuild
`gorder`; **for every week whose `cube_sha` changed, drop all four resident cube files of
that week at once and re-fetch only what the current period needs** (§3.2 guard); re-check
the projection toggle's generation rule (§3.3); render once. Fresh runs reach an open tab
**≤ 3 min after the
CDN edge revalidates**: GitHub Pages serves `Cache-Control: max-age=600` and `cache:
"no-cache"` revalidates against the edge, not the origin, so the honest bound is **≤ 13 min
after deploy** (today's ETag poll has the same exposure) with ≤ 1 MB transferred.

### 8.5 Caching, failure, memory

Immutable names → browser cache hits without revalidation; only `current.json` and the
manifest are conditional. A 404 on a hashed file → re-fetch the manifest, retry once, then
degrade that file (row day: excluded with a console warning and a visible "N rows pending"
in the scope line; block: no gear detail for that day; cube week: chip greyed).

`deploy-site.yml`: the older-than-live branch compares **`manifest.seq`** (monotonic), not
the `built` string; when it keeps live data it fetches the live manifest and the files it
names that the artifact lacks **immediately before upload**, not at overlay start, so a UI
deploy racing a refresh can roll `d/` back by at most the files that changed in that one
cycle.

Heap budget, restated from the arithmetic of the parts (season end = 1.7M window rows, 20
cube weeks, `char_max` ≈ 4.4M):
- row columns 27 B + `rbucket/atk/day/run-global` 9 B = **61 MB**; `gorder` + offsets 7 MB;
- `RUNS` as typed columns with interned comp ids ≈ **14 MB** (**the one client refactor the
  budget depends on**: `RUNS[]` stops being an array of objects with a string key,
  C:1851–1874);
- `STAMP` Uint32 17.6 MB + `CHARSCORE` Int16 9 MB;
- `cells` 20 weeks × ≤ 60k × 42 B ≈ **50 MB** as typed arrays, all resident (Trends reads
  every week);
- resident `dist` (≈ 2.9 MB decoded per 565k-row week) and `chars` (≈ 2.3 MB) — 8 weeks each
  in steady state (≈ 42 MB), every cube week under "All season" (20 × 5.2 ≈ 104 MB);
- one spec's blocks ≤ 5 MB.

Summed honestly: 61 + 7 + 14 + 17.6 + 9 + 50 + 42 + 5 ≈ **205 MB steady state at season
end (≈ 120 MB at 4M rows / week 8); peak ≈ 270 MB at season end with "All season" selected
on a quantile metric (≈ 185 MB at 4M), falling back to steady when the period changes
(LRU).** These are the figures §9.3 asserts (the earlier "160 MB" omitted the LRU-resident
`dist`+`chars`). Today's 109 MB is for a 6% sample and would be ≈ 2 GB at 13M rows on the
legacy path. If the owner picks the exact Trends rule (§3.3 / §11.5) the "All season"
residency becomes the steady state and the steady ceiling is 270 MB. Cube residency is
keyed by `(W, cube_sha)` throughout, so a generation change frees the old tuple rather than
leaking it.

---

## 9. Equivalence and performance tests

Fixture: `scripts/make_eq_fixture.py` cuts **three reset weeks plus 2 days** out of the
committed CSV shape (synthetic rows generated with the CSV's marginals when the real file is
shorter), with matching synthetic gear/abilities/rankings journals reusing
`tests/test_builds_sidecar.py`'s record shapes, including a region-boundary day, **one
duplicate-upload pair whose copies straddle UTC midnight and whose smaller-code copy arrives
in a later chunk than its twin (after the twin's day is frozen)**, one same-spec-twice run,
**one six-member roster, clock-less runs (15% of runs, spread so that several comp cells mix
clocked and unclocked runs)**, one tuning cutoff inside a week, one character whose only
runs are in the oldest week and who is registered last, a fourth (cube-served) week so mixed
periods exist, **and a fifth week older than the window whose cube is deliberately withheld
(a cube gap)**, **a reverse-order midnight pair (the winner, smaller code, in the OLDER day;
its copy arrives in chunk 3)**, **an undated run**, **a future-dated run past the next US
reset**, **a run revised in snapshot 6 (none → gold) that drops off the pages in
snapshots 7 and 8**, and **a run that stays listed while its entry's score/medal turn null
(gold/+25 in snapshots 2–5, null/null in 6–8: `null_after_value`)**. The legacy side of every equivalence test is the **real
`fetch_data.export()`** run over the concatenated journals with the last snapshot and a
`keystone_times.json` accumulated over every snapshot — the production sequence, never a
replica. Rankings are fed to the incremental test as **full per-chunk snapshots** (the
journal is rewritten every run, §6.2-1), with a chunk in which runs drop off the pages.
`WOWLOGS_NOW` freezes the clock in both paths; `WOWLOGS_PINS` injects the pinned tier
sets and learned tables into the legacy path (§5). `scripts/sitecalc.py` is a Python port
of `aggregate` (two accumulators + stamp pass), `aggregateElite`, `refChars/effMinFor`,
`setBonusRows`, `renderPulse`'s bags, `renderTrend`'s bags/calc/gate/top-N/slope/daily
fallback under the §3.3 rule, `buildRuns` + `renderComps` scoring, and
`computeResetBuckets`.

### 9.1 `tests/test_partition_equivalence.py` (and siblings)

| test | asserts |
|---|---|
| `test_sitecalc_matches_js` | the real functions sliced out of `site/index.html` run under `node` on the same fixture produce identical output to `sitecalc.py` for 200 random states — the oracle cannot drift from the client |
| `test_rows_bitexact_vs_legacy` | legacy `build()` with `MAX_RUNS=0` and `WOWLOGS_PINS` vs the day files, joined on `(report_code, fight_id, character, server)`: every column equal **including `hero`, `tier`, `tmul`** (names mapped through both vocabularies; durations against `round()`), `run`/`char` bijections, `charscore[char]` equal, the surviving copy of the midnight-straddling duplicate pair is the same on both sides |
| `test_aggregates_bitexact` | `sitecalc.aggregate` over a grid (40 specs × merged/unmerged × timed × 3 key bands × each dungeon × US/EU/any × tier boxes × p ∈ {30,50,85} × week sets {0},{1},{0,1},{0,1,2}, compare on/off, post on/off, proj on/off) on legacy rows vs concatenated window rows: identical `n runs chars avg med q30 q85 qb qdA qdB adeaths deathless arating mrating`, `weekCounts`, per-run `comp key_ pct deaths`, `RUNS` count, set-bonus cells, elite floors, KPI dates, **`CHART_KEYS` (the gated set) and `effMin`** |
| `test_refchars_manifest_equals_scan` | `manifest.window.refchars` equals `refChars()` over the loaded window for **all 24 keys** (8 role subsets × 3 attack states), keyed with the client's exact string including `""` for the empty role set; `window.keys` equals the row scan |
| `test_cube_equivalence` | the same grid (plus week sets {3},{0,1,2,3},{4..7},all) over weeks served by cubes vs the same weeks as rows: `n avg adeaths deathless chars med q30 q85 qb qdA qdB arating mrating dmin dmax` **exact**; `runs` exact when no hero/tier split, else `Δ ≤ Σ dup_rl` with the shipped bound asserted, and the "≤" label present whenever `state.merge` is false; comps `strength best avgkey deaths n kdur` exact **against `sitecalc`'s port of `renderComps` on a fixture whose comp cells contain clock-less runs** (`bday` up to exact-`kdur` ties), the six-member roster keyed as a 6-comp on both sides, comps qualification under role and melee/ranged filters exact; **a mismatched-generation pair (new `cells`, old `dist`/`chars`/`comps` of the same week) yields the withheld state and never a number, and a manifest `cube_sha` change drops all four resident files; a week set containing the un-cubed bucket-3 week is served from its rows and equals the row scan exactly, and is withheld until its last listed day is resident; the gated set `CHART_KEYS` and `effMin` identical under the §3.3 rule for every week set; Trends eligible set, `trendMin`, top-N order, every plotted point for all five metrics, the `slope` sort order and the daily fallback identical to `sitecalc`; under `proj=1` cube weeks contribute nothing and the caption state is asserted; a window with mixed `rules_sha` days renders no projected number and the toggle is greyed; mixed-period scope line `N of M` equals the row/cube split; `max id over every named file ≤ manifest.char_max`** |
| `test_reset_week` | `W()` vs `computeResetBuckets` for every hour of 2026 under all three rules, on both sides of each boundary, and with a client clock up to 6 h behind `manifest.built` (the `now` clamp) |
| `test_reset_rule_tables_match` | `RESET_RULES`/`RESET_DEFAULT` parsed out of `site/index.html` (and `site/next/index.html` once it exists) equal `season.json.reset_rules` |
| `test_shard_join` | for every legacy-covered row, the values resolved through block + vocab maps equal the legacy sidecar's `(item id, emb)`, enchant id, build hash and ten stats; `spec_vocab` equals the legacy `specs` object entry-for-entry **on this fixture (season = window; §4.3 states the mid-season difference as a deliberate change)**; a mutated `rows_sha` makes the block guard drop exactly that block; a mid-window invalidation re-derives `map` and the synthesised columns correctly |
| `test_incremental_idempotent` | journals fed in **eight** arrival chunks, rankings as a full snapshot per chunk (chunk 3 re-sends 1% of chunk 1 with changed gear + one late upload into a frozen day + the second copy of the midnight duplicate pair; chunk 4 a tier-set upgrade and a learned-table upgrade; chunk 5 empty **with a rankings snapshot from which 30% of runs have dropped off the pages and no triple changed**; chunk 6 a rankings snapshot in which 50 runs in frozen days gain a medal; chunk 7 an edit of `RULES['Arcane Mage']` (coefficient 1.10 → 1.06); chunk 8 the git mirror of `season_pins.json` replaced by a copy older than the chunk-4 upgrade): chunk 5 writes nothing, dirties **zero** days and `seq` does not advance; the late upload changed exactly one day and one week's four cube files under a new `cube_sha` identical in all four headers; the duplicate collapse dirtied exactly the neighbour day and the loser's rows are gone from it; chunk 6 dirtied exactly the days holding those 50 runs; chunk 7 rebuilt **every window day newest-first** within the per-run cap, no cube changed, and no manifest generation in between named window days with two different `rules_sha` values while `projection` was non-null without the withheld state being derivable from the manifest; chunk 8 triggered **no** rebuild and the authoritative pin survived; the upgrades produced `pins.upgrades[]` entries and rebuilt every day newest-first within the per-run cap; a from-scratch replay (4 workers) is byte-identical to the incremental result; a `seed_from_csv()` rewrite of `players.jsonl` mid-sequence is detected by the offset sha and replayed without duplicate rows; a run interrupted by SIGTERM between days leaves a consistent checkpoint from which the next run continues without rebuilding the completed days; **a run killed inside step 1 (after a batch's pending append and before its checkpoint, right after a checkpoint; players, gear and abilities tails, **and inside `build_day` between the three cache saves and the pending-file unlinks**) resumes to the byte-identical result of an uninterrupted run with no duplicated cache record; the chunk-7 rules edit touches exactly the listed days and no cubed, unlisted day; the `revised_dropped` run serves the row's own medal again after chunk 7; **the `null_after_value` run serves gold after chunk 4 and the row's own `none` after chunk 6, its clock kept, its day dirtied by chunk 6**; the reverse-order midnight pair collapses inside the one-shot replay; **a tuning patch inserted on top of the existing one dirties exactly the days ≥ min(old, new) earliest cutoff − 1 plus the undated day, newest first, and the drained result equals a from-scratch replay under the new patches file — every named file byte-identical, `r_post`/`tmul` of every listed day included** |
| `test_partition_format` | writer/reader round trips for every dtype, planar, delta, u64; clamp counters; generation fields present per kind |
| `test_partition_build_rerun` | a rerun over unchanged journals is a no-op (seq, manifest bytes); a two-step run equals the one-shot run; **`--rebuild-all` without `--max-days` rebuilds every day in one run with `days_left=0` and an unchanged manifest** |
| `test_rollover` | synthetic `season.json` with a new zone id, a new dungeon and a dungeon shared with the previous season **under the same encounter id**: new slug directory, routing by `enc ∈ encounters` / `start_utc` never mixes a straddling day; **the old slug's final manifest names a cube for every week of the old season (close-out) and its `d/` tree is byte-identical to `site_final.tar.gz`**; state is slug-scoped; the week-1 old-set-first tier scenario writes a fallback pin, upgrades it and rebuilds |
| `test_reseed` | with `parts/` deleted and a local mock of Pages + Release: Half A restores journals only and writes a `reseed_pending` state; Half B restores state and every frozen output byte-identical and flips to `ok`; a live day file newer than the snapshot is rejected and rebuilt (the registry keeps the snapshot's ids); with the Release unreachable and the CSV present it seeds from the CSV, keeps `reseed_pending`, and the next run with the Release back completes the restore **without duplicating journal rows**; journals present but state missing → extended only on matching overlap, else moved aside; with both unreachable and no journal/CSV it exits 1; Half B under a 60 s deadline stops at a day boundary and the next run continues |
| `test_clobber_guard` | `export_gear()`/`export()` **skip** (no file written, exit 0, `export.skipped=clobber_guard` in health, `::warning::` emitted) on a journal < 90% of the manifest's line count, and export normally once the journal is whole |
| `test_build_step_exit` | a shell test of the §6 Build step: partition failure → legacy rc, `::warning::`, `parts.*` lines present in `site/build_health.txt` after both exit; **a partition builder that stalls (sleeps) does not extend the step past the legacy builder's exit + `PARTS_DEADLINE_S` grace, and `parts.deadline_hit=1` is written**; a builder run with the network namespace disabled completes an ordinary run (no socket is ever opened) |
| `test_legacy_single_pass` | `gear_journal_pass()` with the sample prefilter produces the same `sets/stats/meta` dicts as the original passes on the fixture, and parses ≤ the records of sampled **reports** (the prefilter keys on `report_code`, so unsampled fights of a sampled report are parsed too) plus the lines it cannot classify; the sampled trait material it retains equals the original walk over the sampled records alone, and completed by the `TraitUnion` it reproduces the whole-journal talents doc, builds sidecar and usage dict |
| `test_trait_union` | on a journal written by the real collector where an entry, a class node, a hero subtree (Lightsmith), a whole spec and the modal blob of a string-identified build exist only in unsampled reports: the stage-A sampled material loses each of them; `talents.json.gz` and `builds.json.gz` are byte-identical three ways (HEAD-style whole walk, cold checkpoint, two incremental appends) and the usage dict equals the whole walk's including counts; an incremental run parses exactly the appended lines, an idle run none; a journal shorter than the offset, a rewritten head and a rewritten body under the same head each trigger one whole rebuild with its reason; a torn trailing line (half a record; a record without its newline) never moves the checkpoint and is read correctly after `_repair_tail`; the `.gz` export as the source rebuilds once and is idle until rewritten |
| `test_llms_asset` | `scripts/llms_asset.sh` driven as the workflows drive it (bash, `file://` Release, fake build): `built` (no asset, no cache → inline build ONCE, cached), `stale` (cache kept, build never run), `fresh`, `cached` (`mode=fresh` skips the download when cached, downloads when not), corrupt download → cache kept, corrupt cache → rebuilt, `none` only when the inline build fails too (exit 0, warning); `llms.built`/`llms.age_h` carried from the stamp, warned past the threshold; `pack` = exactly the served set plus the stamp and refuses an empty tree; site/ and docs/ identical after every case |

### 9.2 Production checks

`.github/workflows/nightly-compare.yml` runs `scripts/compare_live.py`: downloads the live
legacy artifacts and `d/`, restricts the legacy side to its sampled runs (joined by
`(report_code, fight_id)` from the LLM parse chunks, which carry them), recomputes the §9.1
grid over the row window and writes `compare.rows_exact=1 compare.agg_exact=1
compare.cells_checked=… compare.expected_drift=<parts.invalidated_days pending>` into
`compare_health.txt`, **uploaded as a Release asset** (`compare_health.<date>.txt`, its own
disjoint asset name); the next refresh's Build step downloads the newest one best-effort
(`curl -m 10`, after the deploy, alongside `journal_parts.py`) and appends it to
`site/build_health.txt`; the watchdog reads the lines like `newest_row`. A night on which
`parts.invalidated_days` reported a pending cross-day collapse (§3.4-6) is marked
`compare.expected_drift=1` and does not count as red. The workflow writes only that asset,
never pushes to the branch and never writes the Actions cache (so `refresh.yml`'s single
concurrency group remains sufficient). **Seven consecutive green nights are a cutover
precondition.**

### 9.3 Perf test — `tests/perf_partitions.py` (manual + weekly CI job)

`scripts/make_synthetic_season.py` generates **4M rows / 8 weeks** (and `--rows 13M` on
demand) with the CSV's marginal distributions, then replays them through
`partition_build.py` in 20-minute-sized chunks, rankings as a per-chunk snapshot. Asserts:
ordinary run ≤ 90 s wall and ≤ 2 GB RSS **with zero dirty days from the rankings snapshot
when no triple changed**; full replay ≤ 12 min at 13M **with 4 workers** (≤ 25 min
single-core is the documented fallback, both draining under the deadline); a replay
interrupted at a 420 s deadline resumes without recomputing completed days; `rows` ≤ 7.5
B/row gz; `dist` ≤ 2.5 B/row; `chars` ≤ 2.2 B/row; `cells` ≤ 0.3 MB/week; `comps` ≤ 0.8
MB/week; largest block ≤ 1 MB; Pages data footprint ≤ 200 MB at 13M; legacy
`gear_journal_pass()` wall time flat (≤ 1.2× between the 4M and 13M runs at fixed
`MAX_RUNS`); streaming `export()` RSS ≤ 4 GB at 13M. Client side (from PR-2): a `node`
harness with the decoder measures window decode ≤ 250 ms for 1.7M rows and heap ≤ 100 MB
after `initData`; a Playwright run of `site/next/index.html` against the synthetic `d/`
asserts first paint ≤ 1.3 s (throttled 50 Mbps, last day of a week), **steady heap ≤ 130 MB
/ peak ≤ 190 MB at 4M and ≤ 210 MB / ≤ 270 MB at 13M** (the §8.5 sums; peak = "All season"
on `med`), and that the Trends render under the §3.3 rule is ≤ 150 ms at 13M.

---

## 10. Sequencing

### PR-1 — pipeline, dual-emit (production behind the legacy payload)

Delivers: `scripts/partition_build.py`, `partition_format.py`, `journal_parts.py`,
`reseed_from_release.py`, `compare_live.py`, `sitecalc.py`, `make_eq_fixture.py`,
`make_synthetic_season.py`; `data/season.json` (with `zone`, `start_utc`, `encounters`),
`data/season_pins.json` (seeded); the legacy builder's `gear_journal_pass()` + sample
prefilter + `build.wall_s` health line (§7.4) and the `WOWLOGS_PINS` injection hooks (§5);
the fetcher reading `zone` from `season.json` and `seed_from_csv()` writing the `.seeded`
marker; **`export()` streaming in chunks with `export.wall_s`/`export.rss_mb` health lines
(§7.4)**; `project_tuning.rules_digest()` + `RULES_VERSION` (§5); `refresh.yml` edits
(reseed Half A before Fetch and Half B inside the Build step, the §6 Build step with
`timeout` + `PARTS_DEADLINE_S`, `BUILD_LLMS=0` always, `journal_parts` + every Release upload
**after the deploy** from the builder's `upload/` staging, llms unpack with `-m 30` from the
cached copy, the clobber guard as a skip, `git pull --rebase` + non-failing commit, the
`season_pins.json` mirror copy in the commit step, `keystone_times.json` behind `too_big()`,
drop the Monday gear commit, untrack `site/llms`, `deploy.wall_s` health line);
`.github/workflows/llms.yml`, `nightly-compare.yml` (both read-only on git and the cache);
`deploy-site.yml`: the older-than-live branch (DS:77–80) compares `manifest.seq` and, when it
keeps live data, fetches the live manifest plus **only the files it names that the artifact
lacks**, immediately before upload; `site/d/` gitignored; a pointer in `builds_tab.md §1` to
§4 here; `refresh.yml` `workflow_dispatch` inputs `rebuild_all` and `evict_cache_test`.

Acceptance: every §9.1 test green; `perf_partitions` green at 4M; seven consecutive
`compare.*` green nights; `parts.stage.*` ≤ 60 s ordinary; **`parts.dirty_days` ≤ 2 on
ordinary runs across a week (the rankings snapshot dirties nothing by itself)**; **legacy
`build.wall_s` not above its pre-PR value and flat across a week of growth**;
**`build.step_wall_s` and `deploy.wall_s` not above their pre-PR values** (the partition
builder and the bigger artifact cost the deploy nothing measurable); `evict_cache_test`
dispatch: Half A ≤ 2 min, **time-to-deploy on the eviction run ≤ today's eviction run**, no
row loss, no registry id reuse, `status` reaches `ok` within 3 runs; the current reset's
time-to-fresh unchanged or better (it improves by the ~90 s `build_llms` no longer spends on
the refresh path).

### PR-2 — client (`site/next/index.html` only)

**Precondition: the owner's nod on §11.5** (trust-gate pool and Trends gate/ranking basis),
because both rules are implemented here and nowhere else. Copy of `site/index.html` with the
loader of §8, the two-accumulator `aggregate` + stamp pass, the `RUNS` typed refactor, the
per-block sidecar join, the projection/mixed-period rules and the cube notices. Nothing else
touches `site/index.html` meanwhile. Deployed beside the live site so the owner compares live
vs next on the same data.

Acceptance: `test_reset_rule_tables_match` covers `next`; the fleet QA battery renders both
paths with identical `#state` for the window periods and diffs KPI / Data-table / Trends text
against `sitecalc.py` on the same `d/` artifacts (exact, except the documented `runs` bound
and the §11.5 gate rules); Playwright budgets of §9.3; the sidecar block guard, the 404 path,
the withheld-chart state and the mixed-period scope line exercised; prefs #1–#4, #7, #10–#13
unchanged (no new hover, nothing moves, tables sort).

### PR-3 — cutover (one deliberate commit)

`site/index.html := site/next/index.html`; footer link (C:1490) → `d/<slug>/manifest.json`;
`deploy-site.yml` guard keeps both; the new builder emits `built=`, `rows=`, `newest_row=`
verbatim (with the future-dated-row exclusion, B:2233) so the watchdog (`^built=`,
`^newest_row=`, `^rows=`) keeps working when the legacy writer goes. Precondition: the two
"≤" labels approved. Legacy emit continues 7 more days. Revert = `git revert` of this commit;
the legacy payload is still being produced.

### PR-4 — deletion (≥ 7 days after PR-3; target season week 6, hard stop per §7.4)

Remove `build()`'s payload/sidecar writers, `stats_sidecar`, `builds_sidecar`'s ladder,
`specmeta`, the tracked `site/data.json.gz`, the `docs/` data mirror, `export()`,
`export_gear()`, `seed_from_csv()`, `restore_checkpoints()` and `data/checkpoints/`
references, the CSV daily commit, `data/keystone_pars.json` (now `season_pins`); switch
`llms.yml` to day caches; enable journal truncation; `reseed_from_release.py` exits 1 when
the Release is unreachable. Acceptance: ordinary refresh cycle build stage ≤ 90 s;
`evict_cache_test` passes again without the CSV; disk ≤ 3 GB after a run; `compare_live`
retired; watchdog lines verified from the new builder alone.

---

## 11. Risks, reverts, open owner decisions

### 11.1 Risks and mitigations

| risk | mitigation |
|---|---|
| a Pages deploy mid-poll: manifest names a file that is not there yet / no longer there | content-hashed names + three-generation / 15-min retention (§6.5) + retry-once + per-file degradation (§8.5) |
| char registry lost (cache and Release) | per-run registry parts on the Release; reseed accepts live files only against the restored `state.json` (§6.6); full replay reproduces it deterministically; clients re-download 11 MB |
| a new vocab value (hero rename, dungeon) | coded `Unknown` + loud health line; one-line `season.json` commit; frozen files never renumber |
| legacy builder wall time during dual-emit | `gear_journal_pass()` makes it O(sample); `build.wall_s` tripwire; measured hard stop (§7.4) |
| runner disk during dual-emit | disk allows ~week 10; the wall-time clock ends dual-emit earlier; journal truncation after PR-4 |
| Pages artifact size (≈ 180 MB at season end, ≈ 90 MB at PR-1) | measured against a 1 GB soft limit; `upload-pages-artifact` tars everything regardless of change count, so ~80 changed files per run cost nothing extra — but bytes do: `deploy.wall_s` is in health with a PR-1 acceptance line and a tripwire at 1.5× its pre-PR value |
| "immutable, never re-downloaded" is suspended during a backlog drain | during a drain like this week's, up to 8 force-frozen days and their weeks' cubes change hash every cycle (~15 MB/cycle per open tab) and `parts.invalidated_days` is legitimately non-zero; the claim holds in the ordinary cadence, and the drain is visible in health rather than silent |
| a week's cube files of two generations mixed in one client | `cube_sha` in all four headers + manifest; residency keyed `(W, cube_sha)`; mismatch rejected unread; manifest change drops all four (§3.2) |
| a tuning rules edit splits the window into two `tmul` generations | `rules_sha` in `inputs_sha`, every window day rebuilt newest-first, toggle withheld until uniform (§3.3, §6.4) |
| the partition builder or the reseed delays the deploy | no network in the builder, `timeout` + per-day checkpoints, reseed split around Fetch, clobber guard skips instead of failing (§6, §6.6, §7.3) |
| `dist` exact-delta size overshoots 2.5 B/row | perf test catches it in PR-1; fallback (a one-line switch) is 1.002-log bins (≤ 0.1% relative), which would move "exact" to "≤ 0.1%" in §3.4 and the test |
| heap above budget on the client | LRU caps on `dist`/`chars` outside the selected period; the `RUNS` refactor is mandatory, not optional; ceilings stated per season stage in §8.5 |
| parallel builders exceed 16 GB | legacy ≈ 4 GB peak measured by its own OOM history at 340k rows for the *fetcher* export, not the builder; the new builder < 1 GB; `parts.status=failed` is a warning, never a red run |
| duplicate-upload collapse across a UTC-midnight skew between uploaders | global signature table; a match dirties the neighbour day; fixture pair straddles midnight (§6.2, §9) |
| a week's cube not ready when it leaves the window | cube-gap invariant: days stay in the manifest, client serves rows, `parts.cube_missing` health line (§3.1) |
| learned tables / tier pin drift between days | pinned tables with 3-slot hysteresis; every change is a recorded upgrade that rebuilds all days (§5) |
| a CSV-seeded journal combined with stored offsets | offset + preceding-64 KiB sha in `state.json`; `.seeded` marker; replay from 0 (§6.2-1) |

### 11.2 Reverts

- PR-1: disable the parallel step (`PARTS=0` env) — the legacy path is untouched by design
  except `gear_journal_pass()`, which is output-equivalent (`test_legacy_single_pass`) and
  can be flag-reverted (`GEAR_PASS=legacy`); Release uploads and `BUILD_LLMS=0` can stay.
- PR-2: delete `site/next/`.
- PR-3: `git revert`; legacy artifacts are still emitted for 7 days.
- PR-4: irreversible without a re-implementation of the legacy writers; hence the 7-day gate
  and the `evict_cache_test` re-run before it.

### 11.3 Freshness accounting (the owner's first priority)

Today: 20-min pace + fetch 1–3 + build 7–8 + deploy ~2 ⇒ mean ≈ 23 min, worst ≈ 33. The
fetch term grows slowly with `export()` (≈ +1 min by week 10) until PR-4 removes it. After
PR-1: build −1.5 min (`build_llms` off the path, partition build in parallel) **and flat for
the rest of dual-emit** (§7.4; without the single-pass change it would have reached ~28 min
by week 6). After PR-4: build < 1 min ⇒ mean ≈ 16, worst ≈ 26, and an open tab shows the new
day file within 3 min of the CDN edge revalidating (≤ 13 min after deploy) instead of on the
next reload. Nothing in any phase slows the current reset: today's day file is the first
artifact written every run and the smallest one on the wire, dirty days are always processed
newest-first (pref #15), step 3 never downloads, the partition builder opens no socket and
cannot outlive the legacy builder by more than its 30 s grace (§6), the eviction run's
partition work happens in parallel with the legacy build rather than ahead of Fetch (§6.6),
a Release outage degrades to a skipped export rather than a red run (§7.3), and every
Release upload runs after the deploy. The one accepted delay is the ≤ 1 h season close-out
on the first day of a new season (§6.7).

### 11.4 Decisions taken here that the design round did not settle (owner may overrule)

1. Old-week DPS quantiles are **exact** (sorted, delta-coded `dist`), not binned.
2. Shards carry **raw ids + 64-bit build hashes**, no local vocab, no caps; one all-spec
   vocab file per run.
3. Shard unit stays **(spec, UTC day)**; per-(spec, week) rejected for churn.
4. Comps cube keeps **every** comp (no ≥ 5-run threshold) and adds the `post` dimension.
5. Cells carry the run-level (`rl`) and global (`rg`) run tables; the `runs` exactness rule of
   §3.4-1 is the only bounded statistic.
6. **Trust-gate reference pool = the row window** (not the season sample, not the viewed week
   set — the latter makes `effMin` a flat 250 and empties "This reset" early in a reset).
   Manifest carries the precomputed pool so first paint uses the final value. **Needs the
   owner's nod before PR-2 (§11.5).**
7. `chars` per week is a separate lazily loaded file; resident for the selected period plus
   an LRU of 8.
8. `season.json` (hand-edited) is split from `season_pins.json` (machine-written; first-write
   plus recorded automatic upgrades).
9. Row order is content-deterministic; run ids are day-local; the run registry is
   routing-only (plus the duplicate signature table).
10. Char ids come from an arrival-order registry, durable on the Release per run; md5 ids
    rejected.
11. Day caches stay local while the day is in the row window; `llms.yml` restores its own.
12. `deploy-site.yml` older-than-live fix compares `manifest.seq` and fetches only the
    live-manifest files the artifact lacks, right before upload.
13. Dual-emit is bounded by the legacy builder's measured wall time (target week 6), not disk.
14. `role` stays a per-row column (bit-exactness over derivation) **and** is a cube-cell
    dimension; the builder asserts role purity per build.
15. `charscore` covers every rated character, so rating columns work on old weeks.
16. `site/d/` is gitignored and not mirrored to `docs/`; the previous season stays browsable
    via `?s=<slug>` from a Release tar while `keep_previous` is set.
17. Dirty-day cap 8 per run, newest first; `rebuild_all` dispatch bypasses the cap.
18. `BUILD_LLMS=0` on the refresh path from PR-1, with a daily `llms.yml` that ships through
    the Release; `site/llms` untracked.
19. **Trends gate and top-N basis = the row window** (§3.3), per-bucket points from per-week
    bags exactly as today. **Needs the owner's nod before PR-2 (§11.5)**; the exact
    season-wide rule is specified as the fallback with its costs.
20. The builds vocabulary is window-wide, not season-wide (§4.3) — a deliberate change under
    decision 3.
21. Projection is a row-window feature: cube weeks are excluded, not blended, when the toggle
    is on (§3.3).
22. Learned tables (hero markers, tuning items) and the tier set are **pinned with recorded
    automatic upgrades** (§5), never relearned silently per day.
23. Freeze on quiescence (72 h without arrivals) or at day end + 7 days, whichever first.
24. The comps cube is defined over clocked runs only (`kdur > 0`), matching legacy's
    `par && kdur` qualification; the `par` half stays client-side per dungeon (§3.2).
25. A week's four cube files share one `cube_sha`; the client keys residency by
    `(W, cube_sha)` and never slices across generations (§3.2).
26. `rankings.jsonl` is a per-run snapshot diffed against the overlay table, never tailed;
    absence from the pages is not a change (§6.2-1).
27. The partition builder does no network I/O and runs under `PARTS_DEADLINE_S` with per-day
    checkpoints; the reseed is split around Fetch; the clobber guard skips (§6, §6.6, §7.3).
28. `season_pins.json` has one authoritative copy under `data/processed/parts/<slug>/`; git
    holds a mirror; a human edit is detected against `pins.mirrored_sha` (§2.5).
29. Tier pin first-write uses legacy's `newest()` verbatim (fallback included) at the first
    daily slot, upgraded without hysteresis to the first id clearing 5%; the only deviation
    from legacy is `tier = −1` between the first gear record and that first daily slot
    (< 24 h, season week 1) — stated in the owner-facing notice.
30. The tuning rule tables are an input (`rules_digest()` in `inputs_sha`); the projection
    toggle is single-generation (§3.3, §5).
31. The old season is closed out (every week cubed) before the new slug is opened (§6.7).
32. Retention is three generations or 15 minutes; `charscore` is a daily base plus a per-run
    delta; the poll reads `current.json` first (§6.5, §5, §8.4).

### 11.5 The one owner decision to take before PR-2 (present both items together, pref #5)

Compare and Trends are the owner's primary use case, and both rules below live only in the
client, so PR-2 cannot start without them:

| item | proposed | alternative | consequence of the alternative |
|---|---|---|---|
| trust-gate reference pool (`effMinFor`) | the row window (last three resets), precomputed in the manifest — the slider means the same thing all season | the season (legacy) | needs every week's `chars` resident for any gated view; the slider's meaning drifts as the season grows |
| Trends eligibility and top-N | gate and rank over the row window; plot every week (§3.3) — "rank by where a spec stands now, plot how it got there" | legacy: gate and rank over every week of the season | all-season `chars` (+ `dist` under `med`) resident permanently (≈ +100 MB at week 23), ~2 s per Trends filter change at season end, and a spec that was meta in month 1 but is gone now still takes a top-N slot |

Either answer is implementable from this file; the proposed one is the one §8.5 and §9.3 are
budgeted for.

---

## Appendix A — Revision 1 (2026-09-02): changes and the blockers they answer

Reviewer 1 = R1, reviewer 2 = R2; B = blocker, W = weakness. Every change is in place; no
section number moved.

| # | change | where | answers |
|---|---|---|---|
| 1 | Trends gate/top-N specified as a rule (row-window basis) with the exact season-wide algorithm as a costed fallback; the group-major stamp pass replaces per-group `Set`s/bitmaps for every distinct-character count; pre-arrival state = chart withheld with the pending scope line, never ungated, never "—"; resident policy = every cube week of the selected period; `test_cube_equivalence` asserts `CHART_KEYS`, `effMin`, Trends eligible set/`trendMin`/top-N/points/slope/daily fallback | §2.4, §3.3, §3.4-3, §8.2-4/5, §8.3, §9, §9.1, §11.4-19, §11.5 | R1-B1, R1-W13, R1-W14 |
| 2 | Hero markers and tuning `items` become pinned learned tables (`learned/`, sha in `season_pins`), learned daily over the window caches with 3-slot hysteresis, adopted only as a recorded upgrade that dirties all days newest-first; `tier_sets(rows)` for `tmul` replaced by the tier pin; both shas in `inputs_sha`; `WOWLOGS_PINS` injects the same tables into the legacy path so `hero`/`tier`/`tmul` are compared | §2.5, §5, §6.1, §6.3, §6.4, §9, §9.1 | R1-B2 |
| 3 | Projection on ⇒ cube weeks excluded from every accumulator, chips greyed, toggle caption; mixed periods on row-only surfaces get the "N of M parses shown" scope line; both asserted | §3.3, §3.4-4, §8.3, §9.1 | R1-B3 |
| 4 | Tier-set pin auto-upgrades to a strictly higher id clearing 5% on ≥ 20k trailing-7-day parses in 3 consecutive daily slots, recorded in `pins.upgrades[]`, all days/cubes dirty newest-first; never downgrades; `test_rollover` week-1 old-set-first scenario | §2.5, §5, §6.4, §6.7, §9.1 | R1-B4 |
| 5 | Duplicate-upload collapse made global through a signature table in `ids/runs.sqlite`; a match in a neighbour day dirties that day (tar restore if needed) and records the invalidation; keystone-overlay strengthening handled the same way; fixture pair straddles midnight and arrives in a later chunk after the twin's day froze | §2.4, §6.2-2, §6.4, §9, §9.1, §11.1 | R1-B5 |
| 6 | `char_max` moved to the manifest top level = registry size at build time; stamp array and `CHARSCORE` sized from it; test asserts max id over every named file ≤ `char_max` | §2.4, §2.6, §5, §8.1, §9.1 | R1-B6 |
| 7 | Build step rewritten: legacy rc decides the job, `wait` on the partition pid only warns; partition builder writes its own `health.txt`, appended to `build_health.txt` after both exit; `test_build_step_exit` | §1.2, §6, §9.1 | R2-B1 |
| 8 | Legacy builder growth removed at cause: one `gear_journal_pass()` with a byte-level report-code prefilter against the sampled runs ⇒ O(sample); `build.wall_s` + tripwire; dual-emit deadline by measured wall time (target week 6, hard stop at 10 min twice); `test_legacy_single_pass`; flag-revertible | §0.1, §0.2-5, §7.4, §9.1, §9.3, §10 PR-1/PR-4, §11.1, §11.2, §11.3, §11.4-13 | R2-B2, R2-W17 |
| 9 | Registry parts + `state.<seq>.json` uploaded every run that assigns ids; reseed accepts a live file only if its sha matches the restored `state.json` entry, otherwise rebuilds it | §2.4, §6.6, §7.1, §11.1 | R2-B3 |
| 10 | Per-journal `(offset, sha256 of preceding 64 KiB)` in `state.json`, verified before tailing; mismatch ⇒ replay from 0 with key dedup; `seed_from_csv()` writes a `.seeded` marker that is never combined with stored offsets; covered in `test_incremental_idempotent` | §6.1, §6.2-1, §6.6-3/4, §9.1, §11.1 | R2-B4 |
| 11 | Day caches stay local for every day in the row window (frozen or not), deleted only after window exit + verified Release copy; step 3 never downloads; `caches: local|release|both` in `state.json` | §1.2, §6.1, §6.2-3/4, §11.4-11 | R2-B5, R1-W6 |
| 12 | Season routing by zone id on the rankings record, else `started_at ≥ season.start_utc`; fetcher reads `zone`/`encounters` from `season.json`; `test_rollover` includes a shared dungeon | §2.5, §6.7, §9.1, §10 PR-1 | R2-B6, R1-W10 |
| 13 | Cube-gap invariant: a week's days stay in the manifest until its cube is published; serving rule "cell iff cubed and bucket ≥ 3; row iff bucket ≤ 2 or week not cubed"; `parts.cube_missing`, `parts.window_days` health lines; `days[].w` per region | §1.2, §2.6, §3.1, §6.2-5, §11.1 | R2-B7 |
| 14 | Client `now = max(Date.now(), manifest.built)`; tested with a 6 h-slow clock | §3.1, §8.1, §9.1 | R1-W1 |
| 15 | Per-cell `dmin/dmax` ⇒ KPI dates covered exact for cube weeks | §0.2-1, §3.2, §3.3, §3.4-5, §9.1 | R1-W2 |
| 16 | `role` is a cell dimension; builder asserts role purity per build with a health line; manifest `spec_role` | §2.2, §2.6, §3.2, §11.4-14 | R1-W3 |
| 17 | Comps cube honours role and melee/ranged; `best`/`kmin` tie note; comps `n`/`dsum` widened to u32 | §3.2, §3.4-2, §9.1 | R1-W4, R1-W12, R2-W11 |
| 18 | `pars` excluded from `inputs_sha` | §5, §6.3 | R1-W5 |
| 19 | Freeze on quiescence (72 h) or day end + 7 d; margin restated (≥ 6 days) | §6.2-4, §11.4-23 | R1-W6, R2-W16 |
| 20 | Window-wide builds vocab stated as a deliberate change (§11.4-20), not an equivalence | §4.3, §9.1 | R1-W7 |
| 21 | Relayout re-derives `rowBase`/`rbase`/`map`/synthesised columns/`gorder`; stated in §4.1 and §8.4 and tested | §4.1, §8.4, §9.1 | R1-W8 |
| 22 | Filter pools from manifest vocab + loaded cells | §8.1 | R1-W9 |
| 23 | Projection-off pool and Archon-tail transients named as self-correcting behind the pending scope line | §8.1 | R1-W11 |
| 24 | Release uploads moved after the deploy; dated-name upload + manifest pointer + GET-back verify instead of clobber; verify-then-delete for day tars | §7.1, §6.2-4, §11.3 | R2-W1, R2-W2 |
| 25 | First-paint budget restated (≤ 4.5 MB / ≤ 1.3 s on the last day of a week; ≤ 1.0 MB today's file) | §1.1, §8.1, §9.3 | R2-W3 |
| 26 | Heap arithmetic restated: steady ≈ 160 MB / peak ≈ 260 MB at season end (95/175 at 4M), cells at 42 B, all-period residency; §9.3 assertions updated (superseded by Appendix B #29: 205 / 270 MB) | §8.5, §9.3, §11.1 | R2-W4 |
| 27 | CDN TTL stated: ≤ 3 min after edge revalidation, ≤ 13 min after deploy | §8.4, §11.3 | R2-W5 |
| 28 | `llms.yml` ships `llms.tar.gz` via the Release, the refresh unpacks it; `site/llms` and `docs/llms` untracked in PR-1; both new workflows read-only on git and the cache | §1.2, §5, §9.2, §10 PR-1, §11.4-18 | R2-W6, R2-W15 |
| 29 | Daily commit does `git pull --rebase --autostash` and never fails the job | §7.2 | R2-W7 |
| 30 | PR-3 requires the new builder to emit `built=`/`rows=`/`newest_row=` verbatim for the watchdog | §10 PR-3/PR-4 | R2-W8 |
| 31 | §6.6-4 contradiction resolved: CSV seed while it exists, exit 1 only from PR-4 | §6.6, §10 PR-4 | R2-W9 |
| 32 | `season_pins.json` written to the Release in the run that writes it | §1.2, §2.5 | R2-W10 |
| 33 | Clamps documented (`r_dur`/`r_kdur` u16, `deaths` u8) with per-column clamp counters in health; bit-exact claim against `round()` | §2.1, §2.2, §9.1 | R2-W11 |
| 34 | Prune deletes `*.tmp`, unreferenced hashed files and stale `pending/` so a cancelled run cannot grow the cache | §6.2-5 | R2-W12 |
| 35 | Refuse-to-publish guard compares against max(live, previous local) and fails open with a health line; `pins.upgrades[]` counts as an invalidation record | §6.2-5 | R2-W13 |
| 36 | `deploy-site.yml` compares `manifest.seq`; live-file fetch runs immediately before upload | §8.5, §10 PR-1, §11.4-12 | R2-W14 |
| 37 | `export()` growth term acknowledged in §0.1 and §11.3 | §0.1, §11.3 | R2-W17 |
| 38 | The two owner-nod items bundled into §11.5 and made a PR-2 precondition | §0.2, §10 PR-2, §11.5 | R1-W14, R1 notes |

---

## Appendix B — Revision 2 (2026-09-02): changes and the blockers they answer

Reviewer 1 = R1, reviewer 2 = R2 (the two reviews filed against revision 1); B = blocker,
W = weakness. Every change is in place; no section number moved. Each blocker is closed at
its cause (a contract or mechanism change plus a test), not with a caveat.

| # | change | where | answers |
|---|---|---|---|
| 1 | Comps cube defined over runs with `kdur > 0` only (legacy's `par && kdur`, C:1872/6892); `par == 0` exclusion stays client-side per dungeon so a re-pin needs no rebuild; the filter is applied at cube emission from `thin.npz` (which carries `kdur` for every run); header `K = max roster` + `clen` so six-member rosters key exactly as `buildRuns`; fixture gains clock-less runs in comp cells and a six-member roster; `test_cube_equivalence` asserts `n/strength/best/avgkey/deaths/kdur` against `sitecalc` | §3.2, §3.4-2, §6.1, §6.2-2, §6.2-4, §9, §9.1, §11.4-24 | R1-B1, R1-W3 |
| 2 | Generation guard across a week's four cube files: one `cube_sha` per emission in every header and in `manifest.weeks[].cube_sha`; client residency keyed `(W, cube_sha)`; a `dist`/`chars`/`comps` whose sha differs from the resident `cells` is rejected unread; a manifest sha change drops all four at once; generation fields listed as part of the common container header; test with a mismatched pair asserts the withheld state | §2.1, §2.6, §3.2, §6.2-4, §6.4, §8.2-3/4/5, §8.3, §8.4, §8.5, §9.1, §11.1, §11.4-25 | R1-B2 |
| 3 | Loader obligation: §8.2 step 1b fetches every `manifest.days` file (un-cubed older weeks, previous-season tail), newest first; a period touching an un-cubed week is withheld until its listed days are resident; `window.rows` defined over every listed day; §6.7 close-out force-freezes and cubes every remaining week of the old season before `site_final.tar.gz` (accepted ≤ 1 h delay on day 1 of a season); fixture gains an un-cubed bucket-3 week; `test_cube_equivalence` asserts row-served equality and the withheld state; `test_rollover` asserts a cube for every old-season week | §2.6, §3.1, §6.7, §8.2-1b, §8.3, §9, §9.1, §11.3, §11.4-31 | R1-B3 |
| 4 | Tuning rule tables become an input: `project_tuning.rules_digest()` (`RULES_VERSION` ‖ RULES/B_CENTRAL/HOTFIX_BAND/B_BAND/PROJECTION_DATE/LABEL) in `inputs_sha`, in every day header (`rules_sha`) and in `manifest.projection.rules_sha`; a rules edit dirties every window day newest-first; `tmul` column presence decided per generation on the window, never per day; the toggle is enabled only when every resident window day matches the manifest (greyed + caption otherwise), a mismatched day is unprojected-pending, never 1.0; `test_incremental_idempotent` chunk 7 edits a rule; `test_cube_equivalence` asserts the withheld toggle | §2.2, §2.6, §3.3, §5, §6.3, §6.4, §8.1-2, §8.4, §9.1, §11.1, §11.4-30 | R1-B4 |
| 5 | `rankings.jsonl` treated as the per-run snapshot it is (`--resweep` default + `unlink()`, W:239–241, F:1238–1241): parsed whole when its sha changed, per-run overlay `(score, medal, rank_duration_ms)` derived as legacy `load_fights()`/`export()` do, diffed against an overlay table in `ids/runs.sqlite`; a day is dirtied only when a stored value changes; absence = no change; no offset for rankings in `state.json`; not partitioned on the Release; `test_incremental_idempotent` feeds snapshots per chunk with a drop-off chunk asserting zero dirty days and a medal chunk asserting exactly the touched days; perf test asserts zero snapshot-caused dirty days | §6.1, §6.2-1, §6.3, §6.4, §7.1, §9, §9.1, §9.3, §10 PR-1, §11.4-26 | R2-B1 |
| 6 | No network in the partition builder: day tars, `state.<seq>.json`, registry parts and pins are staged in `upload/` and uploaded/verified/flipped by `journal_parts.py` after the deploy; the guard's Pages fetch is `timeout 5` fail-open; the builder runs under `timeout -s TERM` with `PARTS_DEADLINE_S` (legacy wall − 60 s, floor 120 s), self-checks between days/stages, checkpoints `state.json` after every completed day, writes `parts.deadline_hit`; `build.step_wall_s` in health; `test_build_step_exit` asserts a stalled builder does not extend the step and that an ordinary run opens no socket | §1.2, §6, §6.1, §6.2-2/4/5, §7.1, §9.1, §10 PR-1, §11.1, §11.3, §11.4-27 | R2-B2 |
| 7 | Reseed split: Half A (before Fetch) restores only journals/done-index/keystone_times/rio (~1–2 min, no worse than today's CSV seed) and writes a `reseed_pending` state; Half B (inside the Build step, before the builder, under the deadline) restores partition state/registry/caches and queues rebuilds through the ordinary newest-first ≤ 8/run loop; the retry condition is the `reseed_pending` flag, not a missing `state.json`; journals present without state are extended only on matching overlap; full-replay arithmetic corrected (20–25 min single-core, 8–10 min with 4 workers, always draining under the deadline); PR-1 acceptance adds "time-to-deploy on the eviction run ≤ today's" | §6, §6.1, §6.6, §9.1, §9.3, §10 PR-1, §11.1, §11.3 | R2-B3, R2-W13, R2-W17 |
| 8 | Clobber guard skips the export (`::warning::` + `export.skipped=clobber_guard` health line, watchdog-visible) and the run proceeds to build and deploy; the Release copy is safe by the dated-name protocol; `reseed_pending` keeps the restore retrying every run; `test_clobber_guard` asserts skip semantics and recovery | §6.6, §7.3, §9.1, §11.1, §11.3 | R2-B4 |
| 9 | One authoritative `season_pins.json` under `data/processed/parts/<slug>/` (cache + Release per write); the git file is a mirror copied by the commit step like `rio_scores`; `pins.mirrored_sha` in `state.json`; a human edit = git sha ≠ mirrored sha **and** ≠ authoritative content, adopted as a recorded upgrade; a lagging checkout triggers nothing; `test_incremental_idempotent` chunk 8 asserts no rebuild on a stale mirror | §1.2, §2.5, §6.1, §6.4, §7.2, §9.1, §11.4-28 | R2-B5 |
| 10 | `days[].w` becomes `{reg:[Wlo,Whi]}`; week↔day ownership derived from the rows' `W`; the client never reads it | §2.6, §3.1, §6.2-4 | R1-W1 |
| 11 | Tier pin first-write at the first daily slot with legacy's `newest()` verbatim (fallback included, marked `basis:fallback`), upgraded without hysteresis to the first id clearing 5%; the < 24 h deviation stated in §11.4-29 | §5, §9.1, §11.4-29 | R1-W2 |
| 12 | `window.refchars` carries all 24 reachable keys with the client's exact key string (`""` for the empty role set); test covers all 24 | §2.6, §8.1-2, §9.1 | R1-W4 |
| 13 | Cross-day duplicate collapse stated as a one-cycle transient (§3.4-6), ordered right after today's day, counted as `parts.invalidated_days=<done>/<pending>`; `nightly-compare` marks such nights `expected_drift` | §3.4-6, §6.2-2, §6.4, §9.2 | R1-W5 |
| 14 | Runs "≤" shown for every cube-week period whenever `state.merge` is false; Compare's Runs delta inherits the bound | §3.4-1, §9.1 | R1-W6 |
| 15 | `csRestoreFromHash()` dependency on the `{0}` boot period recorded | §8.1-4 | R1-W7 |
| 16 | Keystone clock inside `inputs_sha` through the canonical rows (`raw.npz.keystone_s` + overlay), so a restored runner reproduces a day regardless of its `keystone_times.json` | §6.1, §6.3 | R1-W8 |
| 17 | Pages artifact growth stated; `deploy.wall_s` health line, PR-1 acceptance and tripwire; `deploy-site.yml` download noted | §1.2, §10 PR-1, §11.1 | R2-W1 |
| 18 | Actions cache arithmetic corrected (~600 MB week 6, ~700 MB+ season end, +20–30 s on the critical path, ~15 entries); compact registry log + index | §1.2, §6.2-5 | R2-W2 |
| 19 | `export()` streams in chunks; `export.wall_s`/`export.rss_mb` tripwires added to the dual-emit hard stop | §7.4, §9.3, §10 PR-1 | R2-W3 |
| 20 | `data/keystone_times.json` behind `too_big()` in PR-1 | §7.2, §10 PR-1 | R2-W4 |
| 21 | Daily consolidation deletes superseded per-run assets; `release_manifest.<seq>.json` with highest-seq readers, never replaced in place; `refresh.yml` named as the single writer | §1.2, §7.1, §7.2 | R2-W5 |
| 22 | Poll reads `d/current.json` first; a slug change reloads | §6.7, §8.4, §11.4-32 | R2-W6 |
| 23 | State slug-scoped; previous season's tree cache-resident under `prev/<slug>/`, restored once on eviction, never on the critical path | §1.2, §6.1, §6.7 | R2-W7 |
| 24 | Retention = three generations or files younger than 15 min | §1.2, §6.5 | R2-W8 |
| 25 | `compare_health` travels as a Release asset, downloaded best-effort with `-m 10` | §9.2 | R2-W9 |
| 26 | llms unpack: `curl -m 30`, cached copy under `data/processed/llms/` as the fallback | §5 | R2-W10 |
| 27 | `charscore` = daily base + per-run delta (≤ 30 KB) | §1.2, §2.6, §5, §8.2-2, §8.4, §11.4-32 | R2-W11 |
| 28 | Season routing rule stated as `enc ∈ season.encounters` else `started_at ≥ start_utc` (rankings records carry `enc`, not a zone); `test_rollover`'s shared dungeon shares the encounter id | §6.7, §9.1 | R2-W12 |
| 29 | Heap sum restated at ≈ 205 MB steady / 270 MB peak (120/185 at 4M); §9.3 asserts these | §8.5, §9.3 | R2-W13 (heap) |
| 30 | Block-size arithmetic corrected (~15 MB window, ~35 MB at 1.7M rows); Pages footprint re-summed (~40/90/170 MB) | §1.2 | R2-W15 |
| 31 | "Immutable, never re-downloaded" qualified for backlog drains in the risk table | §11.1 | R2-W16 |

## Appendix C — Revision 3 (2026-09-02, PR-1 stage A implementation): changes and the blocker they answer

| # | change | sections | answers |
|---|---|---|---|
| 32 | The llms unpack is **self-healing**: no Release asset and no cached tarball → `build_site_data.py --llms-only` inline once, packed into the cache (`llms.unpack=built`), so the first refresh after stage A lands (the repo had **zero** Releases and the live site served `/llms/` — the R2-W10 wording accepted "no llms/ in that deploy" for an outage, but the transition would have made it a scheduled ~18 h regression, 3 deploys/h) and a cache-evicted runner during a Release outage both keep the tree; `cached` state for `fresh` drain runs (never a download on the latency-critical path); `llms.built` stamp in the tarball → `llms.built=`/`llms.age_h=` health lines and a `::warning::` past 36 h (`fresh` never meant the data was new); `llms.yml` `timeout-minutes` 30 → 120 (it owns the O(season) tier pass alone, ~+50 s/day); mechanism factored into `scripts/llms_asset.sh` (`pack`/`unpack`) under `test_llms_asset`; `test_legacy_single_pass` wording corrected to the prefilter's report granularity | §5, §9.1, §10 PR-1 | stage-A review blocker 1, minors 6, 7, 8, 2 (wording) |
| 33 | **The trait union is complete and incremental** — revision 3's §7.4 claim that computing it over sampled records was invisible was wrong: an adversarial verifier showed `talents.json.gz` losing one node in 40/40 specs and a hero pane (Retribution [Lightsmith, Templar] → [Templar]) whenever an entry or subtree occurs only in unsampled reports (~34% of reports sampled today, ~11% by season end). `TraitUnion` persists the whole-journal union in `data/processed/trait_union.json.gz` (existing cache path) with a checkpoint {source, offset, size, head sha, pre-offset sha, counts}; each build parses only the appended bytes, a stale checkpoint triggers one whole rebuild, a torn tail is never committed; `build.trait_union_mode` / `build.trait_union_rebuild` / `build.trait_records_parsed` / `build.trait_union_records` / `build.trait_union_s` in health; `test_trait_union` (three-way byte identity on the adversarial journal). §7.4 restated: the prefilter applies to sets/stats/meta, its granularity is the report (~1.8× sampled records), not the run. Also: `llms.yml` publishes nothing when no journal was restored or the export failed (the committed CSV is a days-old seed; `::warning::`, Build/Pack/Publish skipped); the refresh's inline self-heal build runs under `timeout 300` and on timeout the deploy goes out without `llms/` plus a `::warning::` (never red; half tree removed); `site/llms.built`, `docs/llms.built` gitignored | §7.4, §9.1, §10 PR-1 | adversarial verification of stage A (trait union), llms.yml stale-seed publish, unbounded inline build |

## Appendix D — Revision 4 (2026-09-02, PR-1 stage B verification): changes and the blockers they answer

| # | change | sections | answers |
|---|---|---|---|
| 34 | The undated day is listed **once** (`d:"undated"`, its state key `-1` filtered out of the numeric list); rows/shard headers and manifest agree on the spelling; the fixture plants an undated run | §2.2, §9 | stage-B blocker 1 (double listing, four unusable blocks, 5 phantom rows) |
| 35 | Step 2 **re-derives the queue after every day**: a neighbour re-dirtied by a collapse is rebuilt in the same run even when it was built earlier in it; "today" = any dirty day at or past today; `days_left` counts everything still dirty; fixture: reverse-order midnight pair | §6.2-2, §9 | blocker 2 (loser served for a cycle, false `days_left=0`) |
| 36 | Overlay semantics **mirror `export()` exactly**: clock accumulates, score/medal served from the current snapshot only (`present` flag; a flip dirties a day only when the served value changes; row's own score/medal kept per run); the legacy `keystone_times.json` is seeded once; **§6.2-1's "exactly as legacy keeps the old value" was false** and is restated; the fixture's legacy side is the real `export()`, its replica deleted; fixture: `revised_dropped` run | §6.1, §6.2-1, §9 | blocker 3 (a served number differed: Paladin Holy n 413 vs 414 in the real client) and minor 1 |
| 37 | **Future rows**: `W(row)` clamped to `W(now, reg)` (identity with `computeResetBuckets`), `w_clamp` re-queues the day once the reset passes; no manifest week past now; fixture: +6-day run | §3.1, §6.4 | blocker 4 (phantom `-1` chip, 5-row undercount of "this reset") |
| 38 | Invalidation matrix compared **per component** — the combined `all` digest that turned every rules/patch change into an all-days `vocab` rebuild is gone; reasons labelled | §6.4 | blocker 5 (25 days instead of 20; cubed days rebuilt for a tuning edit) and the reason-label minor |
| 39 | Step 1 **checkpointed per batch** (offset + sha, counters, dirty marks, parked records), deadline checked between batches, exact-duplicate dedupe on the arrival stamps, three-phase snapshot consumption; `PARTS_TEST_CRASH_AT` hook and the kill cases in `test_incremental_idempotent` | §6, §6.1, §6.2-1, §9.1 | blocker 6 (2× caches after a SIGKILL, replay livelock past the deadline, lost dirty marks) |
| 40 | `--rebuild-all` lifts the per-run cap (the dispatch really rebuilds every day in one go); asserted by `test_partition_build_rerun` | §6.4, §9.1 | blocker 7 |
| 41 | Build step: `build.wall_s` appended to the cached `legacy_wall.txt` so the adaptive deadline has history; `site/d/` mirrored from `out/` when the builder never published (hashed files first, `manifest.json` last — the builder's own mirror copies in that order too); `parts_util.legacy_rules_root` + `test_tmul_bitexact_vs_legacy_under_the_rule` (tmul and the projection meta against legacy under the same rule) | §6, §9.1 | minors: dead adaptive deadline, `d/` vanishing for a cycle, manifest-before-files copy order, tmul never compared |

## Appendix E — Revision 5 (2026-09-02, PR-1 stage B round 3): changes and the blockers they answer

| # | change | sections | answers |
|---|---|---|---|
| 42 | Overlay: a run present in the snapshot stores the entry's score/medal **as-is, null included**, the clock only when the duration is truthy (legacy's `if ms:`); a day is dirtied when the **served** value (stored if present and not null, else the row's own) or the clock changes — `snapshot_diff` no longer treats a null component as "no information"; fixture: `null_after_value` run; asserted in `test_incremental_idempotent` | §6.2-1, §9, §9.1 | round-2 equivalence blocker 1 (a listed ranking whose score/medal turned null was served the stale revision: Shaman Elemental Farseer n 506 vs 507 in the real client; the incremental result was not replay-identical) |
| 43 | `dedupe_records` keys on the **arrival stamp** (`_seq`/`_gseq`/`_aseq`), the one projection identical on both sides of the cache round trip, instead of the whole record (the journal-shaped fields are lossy through `gear.npz`/`abil.npz`); `PARTS_TEST_CRASH_AT=day:after_save:<n>` inside `build_day`; the kill case added to `test_incremental_idempotent` (cache counts, named files, no pending leftovers) | §6.2-1, §9.1 | round-2 equivalence blocker 2 / incremental blocker 1 (a kill between the cache saves and the pending unlinks doubled the day's gear/abilities records for good and flipped served vocab values) |
| 44 | New tuning patch: scope = days ≥ **min(old, new) earliest cutoff − 1** (the old patch's `patch_day` kept in `state.static_inputs`; unknown → every day); the case (a patch inserted on top of an existing one, drained result == from-scratch replay) added to `test_incremental_idempotent` | §6.4, §9.1 | round-2 incremental blocker 2 (day 251 kept `post=1` / the old `tmul` between the two cutoffs) |
