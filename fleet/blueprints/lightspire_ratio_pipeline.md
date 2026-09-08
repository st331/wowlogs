# BLUEPRINT — Lightspire Core "light uptime" (benefit ratio) · PIPELINE & PAYLOAD lens · 2026-09-08

Lens: where the per-parse ratio lives (journal → sidecar → client), how it stays row-aligned,
what it costs, and how the client aggregates it under the usual knobs. Surfacing (which
tile/tooltip/block shows it) is another lens's file; this one pins the data contract those
surfaces read. Owner prefs override everything (fleet/user_prefs.md; esp. #7, #8, #11, #20).
Nothing in this document edits an existing file — it is the spec for the implementer who will.

## 0. Verified ground truth (do not re-derive)

### 0.1 The trinket, and the metric the owner actually asked for
- **Item 250214 "Lightspire Core"**, rare (data/names_items.json → `{"n":"Lightspire Core","q":3}`;
  icon cache → `inv_enchant_essenceastrallarge`). Dungeon drop (Lightwarden Ruia). Tooltip at
  ilvl 334: "+179 Agility or Intellect. Equip: You are embraced by the Light, increasing Mastery
  by 115. Your damaging spells and abilities can call a beam of radiant light nearby. Standing
  in the light blesses you with 200 Mastery while you stand in it." (wowhead tooltip endpoint).
- **Spell 1250527** (driver): "Apply Aura: Proc Trigger Spell → 1263762", "Apply Aura: Mod
  Rating" (the passive Mastery), a dummy effect (wowhead spell page).
- **Spell 1263762 "Radiant Light"** — the beam. DB2 (wago.tools build 12.1.0, pulled by a
  sibling lens into the scratchpad: `se_1263762.csv`, `sm_1263762.csv`, `spellduration.csv`):
  exactly ONE effect row, `Effect 179` (Create Area Trigger) with `EffectMiscValue_0 40323`,
  `EffectAura 0`, `ImplicitTarget_0 18` (destination location); `DurationIndex 29` →
  **12,000 ms**. **It applies NO aura to the player** — wowhead's "buff tooltip / 12 seconds
  remaining" is generic rendering of `AuraDescription_lang`, not an Apply-Aura effect. So the
  beam's existence is observable in a log ONLY as the wearer's **cast** of 1263762 (WCL
  `type:"cast"` event, `sourceID` = wearer), and its lifetime is the spell duration.
- **Spell 1263768 "Light's Blessing"** — the benefit. `se_1263768.csv`: `Effect 6` (Apply
  Aura), `EffectAura 189` (Mod Rating), `EffectMiscValue_0 33554432` (= 1<<25, Mastery),
  `ImplicitTarget_0 1` (caster = self); `DurationIndex 21` → **-1, i.e. no timer: it lasts
  while the player remains inside**. Description: "The light blesses you with N Mastery while
  you remain within it." In a log: `applybuff`/`removebuff` of 1263768 with `targetID` =
  wearer. SimulationCraft `engine/player/unique_gear_midnight.cpp` L2690-2711 comments the
  trio as `1250527 Driver & Passive Mastery Buff / 1263768 Light mastery buff / 1263762 Area
  Trigger`, models the blessing with `set_duration( find_spell(1263762)->duration() )` times
  `lightspire_core_duration_multiplier = 0.5` (player.hpp) and carries `// TODO: Emulate not
  standing in the light`. SimC ASSUMES 50% stood-in; the owner wants it MEASURED.
- 1250527 (`se_1250527.csv`): effect 0 = Apply Aura 42 (Proc Trigger Spell → 1263762),
  effect 1 = Apply Aura 189 Mod Rating Mastery (the passive), effect 2 = Effect 3 (Dummy).
- Proc rate: a sibling lens's DB2 read says ~1.25 RPPM (≈28 beams per 22-min key,
  `scratchpad/lightspire_uptime_alias.graphql`); **not verified here** and not needed — the
  denominator is measured per fight, not modelled.

**The metric (owner clarification, verbatim in substance):** per player per fight,
`ratio = |Blessing ∩ Window| / |Window|`, where
- `Window` = union over the wearer's **casts of 1263762** of `[t_cast, min(t_cast + 12,000 ms,
  fightEnd)]` (the beam exists),
- `Blessing` = union of the wearer's **Light's Blessing (1263768)** aura intervals (apply →
  remove, clipped to the fight),
- only the INTERSECTION counts in the numerator (a blessing that lingers past the window is
  clipped). Both timelines belong to the SAME actor id, in report-relative ms.
The one modelling assumption is "beam lifetime = the 12,000 ms spell duration" (the AreaTrigger
DB2 row 40323 could not be fetched — `atcp_40323.csv` came back empty — and SimC uses the same
spell duration). It is guarded, not trusted: §2.3 step 5 measures how much blessing time falls
OUTSIDE the modelled windows, season-wide, and the health line prints it.

### 0.2 What the pipeline already has (anchors, verified against the working tree)
- Gear is journaled per (report_code, fight_id, character, server) with a positional `gear`
  list: scripts/fetch_data.py L446 `compact_gear` keeps `id/ilvl/set/ench/gems/bonus`;
  L634-640 writes `{"report_code","fight_id","character","server","class","spec","gear",
  "talents","flask"}`; L978 `gear_fh.write(...)`. Slots 12/13 = trinkets
  (build_site_data.py L829 `TRINKET_SLOTS = (12, 13)`). **No actor id is journaled** — the
  Summary's `p.get("id")` is used for the damage join and dropped.
- One journal walk per build: build_site_data.py L2360 `gear_journal_pass(codes)` →
  L2436 `meta[key] = {"build": build, "gear": gear}` — the gear list of every payload-sampled
  row is ALREADY in memory at build time. Wearer detection needs no new walk.
- Row alignment discipline: L651 `stats_sidecar` walks `df` in payload order, sparse
  column-major Uint16 body + delta-coded Uint32 `idx` (L595 `_sidecar_json`), `n` must equal
  the payload's row count; client rejects otherwise (index.html L2965 `decodeStatsSidecar`,
  `(j.n>>>0)!==N`). L1525 `builds_sidecar` — same walk, `fl` bit0 gear / bit1 build, per-spec
  vocab, unknown keys ignored (fleet/blueprints/builds_tab.md §1.2).
- Sizes today (build_site_data.py L579-587): payload 751,748 rows, stats-covered 482,935
  (64%); stats.json.gz **4.97 MB gz against a 5.5 MB target / 6.5 MB cap**; builds.json.gz
  ~6.12 MB gz against 7.0 target / 7.5 cap (L1142-1143), windowed to 3 resets (L1151).
- Writer discipline in `build()`: L2948 stats → written or UNLINKED per SITE_DIRS, L2968
  builds, L2983 talents; every rung on `health()` (L46) → site/build_health.txt (pref #20).
- Payload gate precedent: index.html L1697 `hasProj=!!(D.projection&&R.tmul)`; the Lab
  manifest L2466 `LAB_FEATURES` (`gate/active/controlHTML/scopeBits/exemptions`), L2486
  `renderLabCards()` renders nothing for a false gate; L2535 `labStamp`.
- Knob-filtered row set: L3020 `frameLiveIdx()` (baseMasks + rowPass L2585 + periodPass
  L1956 + elite/Archon branch + `projSkip`) → L3251 `frameLensSlice()` (pctl ±10 window by
  `dpsAt`) → L3882 `screenData(ctx)` (`win = L.idx.filter(i=>L.inWin.has(i))`). Every knob
  the owner named funnels into these three: key range, period/resets, dungeon, region,
  role, timed-only, percentile lens, merge hero talents (`groupKey`), Archon replica (elite
  branch), post-tuning, projection.
- Lazy sidecar loaders: L2942 `loadStatsSidecar` (cached promise, DecompressionStream guard,
  `cache:"no-cache"`, silent failure), L3507 `loadBuildsSidecar`.
- Quota: a NEW process shares the account ceiling — scripts/wcl_client.py L185
  `_probe_quota` reads `rateLimitData` before the first request, L99 `ceiling = limit *
  fraction` (fraction from `WCL_QUOTA_FRACTION`, L54), L102 `admit()` refuses past it. So a
  second script in the same workflow run cannot exceed the standing 70%; it only competes
  for what the summary stage left.
- Secondary-fetch precedents: scripts/fetch_abilities.py (per-fight tables, journal in
  data/raw, done-set by re-reading the journal); scripts/fetch_rio.py (`--budget-s`, atomic
  journal under data/processed, "the workflow cache carries data/processed; a new cache path
  orphans every cache" — refresh.yml L173-180 says the same in capitals).
- Inflow: N went 439,963 (Aug 24 payload) → 751,748 (Sep 6) = ~24k rows/day ≈ 168k
  rows/week ≈ 34k fights/week (5 rows per fight).
- Client name collision: `trk` is already the pooled-trinket token (index.html L3739-3741
  `CS_POOL_SLOTS={ring, trk}`; fold ids `"trk"`). The Lab id/state key must not be `trk`;
  this document uses **`beam`** (no hits for `state.beam`/`hasBeam` today).

### 0.3 Unknowns (say so, do not guess)
- Whether the combat log carries a `SPELL_CAST_SUCCESS` for the proc-triggered 1263762 (most
  triggered area-trigger spells do log one; a "do not log" flag would leave the window
  unobservable). The §2.6 first-run check settles it on 20 fights before anything is built on
  it; if it is absent, the fallback is `events(dataType: Summons)`/`Casts` of any child spell
  the same run reveals, and this document comes back for a revision.
- The area trigger's own lifetime vs the 12,000 ms spell duration (see §0.1; guarded by §2.3
  step 5).
- Points per fight for the events sub-queries: measured precedents are ~2.6 pts/run for a
  Summary table (fetch_data.py L833), ~1 pt/run for a CombatantInfo events sub-query (commit
  82fb19e), 2.0/1.6 pts per DamageDone table (fetch_abilities.py). Budget the first run at
  **est_cost 4 pts/fight** and let the governor self-correct from `rateLimitData`.
- Wearer share among gear-known rows: not derivable from this checkout (the committed payload
  is the Aug 24 build without specmeta; journals are cache-only). First measurement is one
  line in the diagnose workflow (§2.6). Everything below is sized for 5-30% of gear-known rows.

## 1. Decision (one recommendation)

**A new, tiny, self-contained lane: journal → `site/trinkets.json.gz` → lazy client doc,
gated by a ≤200-byte `lab` block in data.json.** Concretely:

1. **Journal field: a NEW journal file, not a field on the gear record.** The ratio needs a
   second WCL query after the Summary (events), fetched on a different cadence and budget.
   Appending it to `gear.jsonl` would mean rewriting a 4 GB append-only file or a two-line
   identity, and the gear journal's readers (`gear_journal_pass`) must stay exact. So:
   `data/processed/trinket_uptime.jsonl`, one record per (fight, wearer), keyed by the SAME
   `_gear_key` tuple (build_site_data.py L347) so the join is the existing one.
2. **Sidecar: a new small document, NOT a column in stats.json.gz or builds.json.gz.**
   - stats.json.gz sits 0.53 MB under its target (4.97 vs 5.5 MB); two Uint16 columns over
     482,935 covered rows add ~1.9 MB raw / ≈1.2 MB gz and would push it onto the DEGRADED
     rung (drop tertiaries → 3-reset window) for a transient feature. Its `stats:[names]`
     contract is also "ratings only"; a ratio and a duration are not ratings.
   - builds.json.gz is 0.88 MB under target with a ladder that trades away the Enchants pane
     first; a Lab column would compete with the Character screen's vocabulary bytes, would be
     windowed to 3 resets whether or not the knobs ask for more, and would leave residue in
     the §1 contract when the feature is retired (LAB birth rule: retiring = deleting one
     entry).
   - A separate doc is ≈0.1-1.5 MB gz at any plausible wearer share (§3.3), ships/omits on
     its own health line, and is fetched ONLY when the Lab toggle is on (nothing dormant).
3. **Row alignment: identical discipline** — emitted from the same `df` walk in payload row
   order inside `build()`, sparse over WEARER rows only, delta-coded `idx`, `n` == payload
   rows, client rejects on mismatch. Wearer = the row's journal gear list carries item 250214
   in slot 12 or 13 (`gj.meta[key]["gear"]`), independent of the builds vocab cap (a trinket
   below the top-40 pools into "other" there and would be invisible).
4. **Per-row payload: three columns, 5 bytes** — `av` Uint16 (window, deciseconds), `bn`
   Uint16 (blessing ∩ window, deciseconds), `st` Uint8 (0 pending, 1 measured, 2
   unavailable). The ratio is derived client-side as `bn/av`; shipping the denominator is
   what lets a 4 s window be gated out instead of reading as 100%.
5. **Client aggregation: one function over a row list**, fed by `frameLensSlice()` /
   `screenData().win`, so every surface — Spec Frame block, doll tile, a future table cell —
   prints the same number: **median of per-parse ratios** (window ≥ 12 s), plus pooled
   Σbn/Σav, n measured / n wearers in the lens window, and Σav in minutes.
6. **Config seam** `data/lab_trinkets.json` (committed) lists the tracked item(s) with their
   window/benefit spell ids; fetcher, emitter and doc all read it. The next transient trinket
   is one JSON entry plus a backfill, zero new code.

## 2. Collection — `scripts/fetch_trinket_uptime.py` (new file)

### 2.1 Inputs
- `data/lab_trinkets.json` (new, committed):
  ```json
  {"items":[{"id":250214,"name":"Lightspire Core",
             "win":{"cast":1263762,"len_ms":12000},
             "ben":[1263768],
             "min_key":10}]}
  ```
- Wearer fights: walk `data/processed/gear.jsonl` (or `data/gear.jsonl.gz`) once, keep
  `(report_code, fight_id) → [(character, server)]` for records whose `gear[12]` or
  `gear[13]` has `id == 250214`. Restrict to fights present in `mythic_runs.csv.gz` /
  the players journal with `key_level >= min_key` (10 — the site's floor; below it the
  leaderboards are swept 5x shallower and nobody asks the +5 question), ordered **newest
  first** by `started_at` (pref #15; reuse the idea of fetch_data.py L733 `order_pending`).
- Done-set: `data/processed/trinket_uptime_done.txt`, `code:fid\tOK|FAILED\t<msg>` — the
  summaries_done.txt shape (fetch_data.py L959-989), same PERMANENT_ERROR regex (L67) to
  decide FAILED vs retry.

### 2.2 The query (one aliased sub-query per fight; batch 6-8 fights per HTTP request)
Argument names and defaults below are quoted from the WCL v2 schema (vendored copy in the
scratchpad, `ToppleTheNun_mchammer_…_schema.graphql` L1291-1380: `abilityID: Float = 0`,
`dataType: EventDataType = All`, `endTime: Float = 0`, `fightIDs: [Int] = []`,
`filterExpression: String = ""`, `hostilityType: HostilityType = Friendlies`, `limit: Int =
300` "Allowed value ranges are 100-10000", `sourceID: Int = 0`, `startTime: Float = 0`,
`useAbilityIDs`, `useActorIDs`; `ReportEventPaginator { data: JSON, nextPageTimestamp: Float }`
L1896; `ReportFight { startTime: Float!, endTime: Float!, friendlyPlayers: [Int],
keystoneLevel: Int }` L1907; `ReportActor { id, name, server, subType, type }` L2390).
```graphql
a0: report(code: "<code>") {
  fights(fightIDs: [<fid>]) { id startTime endTime friendlyPlayers keystoneLevel }
  masterData { actors(type: "Player") { id name server subType } }
  events(fightIDs: [<fid>], dataType: All, hostilityType: Friendlies,
         filterExpression: "ability.id in (1263762, 1263768)",
         startTime: 0, endTime: 100000000000, limit: 10000,
         useAbilityIDs: true, useActorIDs: true) { data nextPageTimestamp }
}
```
- **`startTime`/`endTime` are mandatory in practice**: the schema default `endTime: 0`
  returns nothing (learned on the flask sub-query, commit 82fb19e `EVENTS_END_MS`; restated in
  the sibling draft `scratchpad/lightspire_uptime_alias.graphql`). Pass a bound no report
  reaches and let `fightIDs` do the filtering; timestamps are REPORT-relative ms.
- Pagination: pass `nextPageTimestamp` back as `startTime` while non-null. Expected volume
  per wearer per 22-min key: ~28 casts + ≤ 6 buff events per beam → a few hundred events, one
  page; loop anyway.
- Fallback if `filterExpression` is refused or mis-scoped (verify on the first 20 fights):
  two aliased sub-queries, `c: events(dataType: Casts, abilityID: 1263762, …)` and
  `b: events(dataType: Buffs, abilityID: 1263768, …)`, same time bounds.
- Event fields used: `timestamp`, `type` (`cast`; `applybuff|removebuff|refreshbuff|
  applybuffstack|removebuffstack`), `sourceID`, `targetID`, `abilityGameID`.
- Wearer's actor id: `masterData.actors` row with `name == character` and `server == server`
  (both come from WCL's own actor table — the Summary's `playerDetails.server` is the same
  source). Name-unique match with a differently formatted server: accept, count
  `actor_server_mismatch` in health. No match → `st:2`, `why:"no_actor"`. Forward path
  option: journal `playerDetails[].id` in the Summary stage from now on (one added key on the
  gear record, tolerated by every reader) so future fights need no `masterData` field.
- `est_cost=4.0 * len(batch)` for admission; the governor replaces the estimate with the
  server's reading after every response (wcl_client.py L266).

### 2.3 Band math (pure function, unit-tested; §5)
Per wearer `w` (actor id), in report-relative ms, `F = [fightStart, fightEnd]`:
1. `W = union( [t, min(t + 12000, fightEnd)] for every event with type == "cast",
   abilityGameID == 1263762, sourceID == w )`. Overlapping beams UNION (a second beam 2 s
   into the first extends the window, it does not double-count).
2. `B` from the 1263768 buff stream with `targetID == w`: `applybuff` opens (a second apply
   with one open is ignored); `removebuff` closes; `refreshbuff` closes-and-reopens at the
   same instant; stack events ignored; still open at `fightEnd` → closes there; a `removebuff`
   with nothing open → opens at `fightStart`. Clip to `F`, union.
3. `av_ms = |W|`, `bn_ms = |B ∩ W|`, `bn_raw_ms = |B|`, `casts = number of cast events`.
4. Invariants (count violations into health, never crash): `0 ≤ bn ≤ av ≤ |F|`;
   `bn_raw ≥ bn`.
5. The guard on the one modelling assumption: season-wide `Σbn_raw / Σbn`. Exactly 1.0 means
   the blessing never exists outside a modelled window; a few percent is removal lag (the
   sibling draft's worked example clips an 800 ms lag); far above 1.0 means beams live longer
   than 12,000 ms and the definition comes back here BEFORE the number is trusted.
A reference implementation with a worked example (`avail 27,000 ms, inside 13,000 ms, 3
beams` over three cases: overlap-union, lag-clip, open-at-end) already exists at
`scratchpad/lightspire_ratio.py` (sibling lens, 80 lines); lift its `_union`/`_inter_len`
into the new script rather than re-deriving them, and add the invariant counters.

### 2.4 Journal record — `data/processed/trinket_uptime.jsonl` (append-only, cache-only)
```json
{"report_code":"AbCd...","fight_id":7,"character":"Name","server":"Realm",
 "item":250214,"st":1,"av_ms":184230,"bn_ms":121900,"bn_raw_ms":126400,
 "casts":15,"fight_ms":1712000,"at":1757300000}
```
- `st:1` measured (including `av_ms:0` = the trinket never procced in this fight — a REAL
  no-window, not unknown); `st:2` unavailable with `"why":"report_gone"|"no_actor"|
  "no_events"`, written per wearer so the sidecar can tell pending from impossible.
  ~170 B/record; at 25k-100k wearer-parses/week that is 4-17 MB/week of cache, a rounding
  error next to the 4 GB gear journal.
- "Last copy wins" on the key, exactly like export_gear (fetch_data.py L1031); a regear
  refetch of a fight simply appends.
- Durability: cache-only like gear.jsonl (loss = refetch, acceptable for a Lab lane). If the
  owner wants it committed, it is small enough for the daily export step — not assumed.

### 2.5 Budget, cadence, health
- CLI: `--points 400` (hard cap on `client.spent` growth this run), `--budget-s 150`,
  `--limit-fights`, `--status`. Stop cleanly at whichever comes first; never sleep for the
  quota window (`WCL_MAX_SLEEP_S` stays the workflow's).
- Arithmetic: 34k fights/week × wearer share (5-30% of gear-known rows, 64% gear-known) ≈
  1.1k-6.5k wearer-fights/week × ~4 pts ≈ 4.4k-26k pts/week ≈ **26-155 pts/hour, 0.15-0.9%
  of the 18,000/h account budget**. The 400-pt cap × 3 runs/h = 1,200/h (6.7%) is a ceiling
  for the initial backfill, not the steady state; after the backfill the stage typically
  finishes in seconds.
- Backfill order: newest first, key ≥ 10, and the first pass stops at the builds window
  (3 resets, build_site_data.py L1151) before reaching further back — the Character screen
  cannot show older rows anyway, and the Spec Frame block states its covered range.
- Health: write `data/processed/trinket_health.txt` (`fetched=…`, `points=…`, `backlog=…`,
  `pts_per_fight=…`, `no_window=…`, `unavailable=…`, `bn_outside_window_share=…`,
  `actor_server_mismatch=…`), which `build()` folds into build_health.txt as
  `fetch.trinket.*` — the same fold it does for fetch_health.txt at L2804 (this is a
  build_site_data.py edit; do NOT write into fetch_health.txt, fetch_data.py rewrites that
  file wholesale from its own `_OUTPUTS`, L786).

### 2.6 First-run verification (before any UI is built)
1. Diagnose workflow (.github/workflows/diagnose.yml, read-only journal restore): a script
   that counts wearer records in gear.jsonl (`gear[12|13].id == 250214`), per week and per
   spec, and prints the top-10 specs — this is the wearer share everything above is sized on.
2. `fetch_trinket_uptime.py --limit-fights 20` on the runner: confirm `cast` events of
   1263762 exist for wearers (the window is unobservable without them — §0.3), confirm
   1263768 apply/remove pairs exist, print per-fight points from `rateLimitData`, print
   `Σbn_raw/Σbn`, and eyeball two wearers' windows against the WCL web UI's Casts and Buffs
   tabs for those fights.
3. Only then set `--points`/batch sizes and enable the workflow step.

## 3. Build — `trinkets_sidecar(df, gj, name)` in scripts/build_site_data.py

### 3.1 Emission (ONE walk, payload order, after `builds_sidecar` at L2968)
```
tj = trinket_journal()            # {_gear_key: record}, last copy wins, ~1 dict read
cfg = data/lab_trinkets.json
for i, (code, fid, ch, sv) in enumerate(zip(df.report_code, df.fight_id, df.character, df.server)):
    rec = gj.meta.get(_gear_key(code, fid, ch, sv))   # L2436: {"build","gear"}
    gear = rec and rec["gear"]
    if not (isinstance(gear, list) and any(s < len(gear) and isinstance(gear[s], dict)
            and gear[s].get("id") == item.id for s in TRINKET_SLOTS)): continue
    t = tj.get(key)
    idx.append(i)
    if t is None:           st=0, av=0, bn=0                   # wearer, not fetched yet
    elif t["st"] == 2:      st=2, av=0, bn=0                   # measured-impossible
    else:                   st=1, av=min(round(t.av_ms/100), 65535), bn=min(round(t.bn_ms/100), 65535)
```
- Deciseconds in Uint16: cap 6553.5 s = 109 min > any M+ fight; `R.dur[i]*10` (payload
  `dur`, L2902) bounds `av` — count and clamp violations, never ship `bn > av`.
- Coverage rule mirrors builds/stats: a wearer row is covered whether or not it is measured,
  so the client can print "n measured of n wearers" from the doc alone.

### 3.2 Document — `site/trinkets.json.gz` (and docs/), gz level 9, unlinked when empty
```jsonc
{"v":1, "n":751748,                      // MUST equal the payload rows length
 "items":[{
   "id":250214, "n":"Lightspire Core", "ic":"inv_enchant_essenceastrallarge",
   "win":{"cast":1263762,"len_ms":12000}, "ben":[1263768],   // provenance for the scope line
   "enc":"sparse", "layout":"col",
   "idx":"<b64 LE Uint32, delta-coded>", "idxdelta":true,
   "cols":{"av":"<b64 LE Uint16 ds>", "bn":"<b64 LE Uint16 ds>", "st":"<b64 Uint8>"},
   "m":48210, "measured":31877, "pending":15990, "unavail":343, "nowin":1204,
   "outside":0.03, "range":["2026-08-19","2026-09-08"]}]}
```
- Column-major by construction (one key per column). Unknown keys are ignored by the
  client; `items[]` lets a second trinket ride the same file with its own idx/cols.
- Client reject rules (mirror of L3533 `decodeBuildsSidecar`): `n>>>0 !== N`; any col shorter
  than `idx.length`; `cols.st/av/bn` missing; `items` not an array. Reject ⇒ treat as absent,
  `console.warn`, the Lab card's control stays but its output says "data unavailable".

### 3.3 Size (estimate; the health line prints the real one)
Per covered row: idx delta (Uint32 raw, ~1-1.5 B gz), av 2 B, bn 2 B, st 1 B → 9 B raw,
**≈4-6 B gz** (av/bn are mid-entropy like ratings, which gz at 0.73 in stats.json.gz; st
and idx deltas are near-free). Wearer rows W = 5-30% of the 482,935 gear-known rows =
24k-145k → **≈0.15-0.9 MB gz**, upper bound 1.5 MB at 250k. No ladder needed; one hard cap
of 3.0 MB with an OMITTED health line (pref #20a) is enough — and unlike stats, omission
here removes a Lab card, not a live block's data source.

### 3.4 Health (every line on the published channel — pref #20)
```
[season] trinkets sidecar: item 250214 Lightspire Core — wearers 48,210/751,748 rows
  (10.0% of gear-known), measured 31,877 (66% of wearers), pending 15,990, unavailable 343,
  no-window 1,204; blessing outside modelled windows 3.0% (Σbn_raw/Σbn 1.03); av>dur clamps 0;
  covered 2026-08-19 – 2026-09-08 || SHIPPED 0.29 MB gz (cap 3.0)
[season] lab.trinkets: SHIPPED (measured 31,877 ≥ floor 50)
```
Plus the folded `fetch.trinket.*` lines (§2.5). None of these match what watchdog.yml reads
(`built`, `rows`, `newest_row` — watchdog.yml L118-119), so the watchdog is unaffected.

### 3.5 The payload gate (≤200 B in data.json, feature-detected like `projection`)
In `build()` after the sidecar: `payload["lab"] = {"trinkets":[{"id":250214,
"n":"Lightspire Core","ic":"…","measured":31877,"wearers":48210}]}` ONLY when
`measured ≥ 50`; otherwise the key is absent and the Lab card does not exist (nothing dormant,
LAB manifest rule). The doc itself is never fetched at page load. Old clients ignore the
unknown key (builds_tab.md §1.2 discipline); the new client ignores a payload without it.

### 3.6 Why not `specmeta`/`specstats` (build-time cohort blocks)
They are fixed cohorts that cannot follow the knobs — the exact failure pref #20 records
("as i adjust key levels, the stats don't seem to change at all"). The owner asked for the
usual knobs; only a row-aligned doc delivers that.

## 4. Client contract (site/index.html; the surfacing lens decides WHERE it renders)

### 4.1 Detection, manifest entry, loader
- `let hasBeam=false; state.beam=false;` in `initData` (L1673): `hasBeam=!!(D.lab&&
  Array.isArray(D.lab.trinkets)&&D.lab.trinkets.some(t=>t.id===250214))` — set BEFORE
  `renderLabCards()` at L1701, like `hasProj` at L1697.
- LAB_FEATURES entry (L2466), one object: `{id:"beam", name:"⚗ Lightspire Core · light
  uptime", badge:"BEAM", mount:"labbox", card:true, gate:()=>hasBeam,
  active:()=>hasBeam&&state.beam, controlHTML:<checkbox #beamcb + hint "measured on
  31,877 parses">, scopeBits:()=>…, …}`. This feature FILTERS NOTHING — it is an overlay
  metric — so `labStamp` (L2535) must not stamp every section's scope line when it is on:
  either add a manifest flag `stamps:false` (one line in `labStamp`/`labNoteBadges`) or list
  every section under `exemptions` with "does not change these numbers". The surfacing lens
  chooses; the flag is cleaner.
- `loadBeamDoc()` mirrors L2942 `loadStatsSidecar` exactly (cached promise, DecompressionStream
  guard, `fetch("trinkets.json.gz",{cache:"no-cache"})`, silent failure), kicked by the FIRST
  `state.beam=true` (checkbox) and re-rendering the open frame/screen on arrival. Decoder
  `decodeBeamDoc(j)` → `BEAMC={id, map:Int32Array(N) (payload row → covered row, -1),
  av:Uint16Array, bn:Uint16Array, st:Uint8Array, meta}` with the §3.2 reject rules and the
  delta-idx running sum from L3000-3004.

### 4.2 The one aggregator (row list in, numbers out — no surface computes its own)
```js
const BEAM_MIN_AV=120;        // ds: one full 12 s window before a parse's ratio counts
const BEAM_MIN_SUM_AV=600;    // ds: ≥60 s of light behind any printed median
function beamStats(rows){     // rows: payload row indices ALREADY knob-filtered
  const T=BEAMC; if(!T) return null;
  let wearers=0, measured=0, unavail=0, noWin=0, sAv=0, sBn=0; const r=[];
  for(const i of rows){
    const k=T.map[i]; if(k<0) continue; wearers++;
    const st=T.st[k];
    if(st===2){unavail++; continue}          // measured-impossible: never 0%
    if(st!==1) continue;                     // pending: unknown, never 0%
    measured++;
    const av=T.av[k], bn=T.bn[k];
    if(!av){noWin++; continue}               // real no-window: excluded, counted
    sAv+=av; sBn+=bn;
    if(av>=BEAM_MIN_AV) r.push(bn/av);
  }
  r.sort((a,b)=>a-b);
  const ok=r.length>=CS_THIN&&sAv>=BEAM_MIN_SUM_AV;        // CS_THIN=10, L3750
  return {wearers, measured, unavail, noWin, pending:wearers-measured-unavail,
    n:r.length, med:ok?100*qp(r,.5):null, q25:ok?100*qp(r,.25):null, q75:ok?100*qp(r,.75):null,
    pooled:sAv?100*sBn/sAv:null, lightMin:sAv/600, thin:!ok};
}
```
- Rows for the Spec Frame block: `const L=frameLensSlice(); beamStats(L.idx.filter(i=>
  L.inWin.has(i)))`. Rows for the Character screen: `beamStats(screenData(ctx).win)`. These
  are the SAME set by construction (`screenData` wraps `frameLensSlice`, L3883), so a tile
  and the rail block cannot disagree. A per-spec column for a table would call it once per
  group with that group's `frameLiveIdx`-equivalent rows.
- `qp` is the client's linear-interpolated quantile (L2575) — same definition the stats block
  uses, so "median" means one thing on the page.
- Knob coverage, by construction (nothing new to wire; `render()` L6185 ends in
  `renderFrame()` L6407, which dispatches to `renderScreen()` while the screen is open):

  | knob | where it already bites |
  |---|---|
  | key range, dungeon, region, role, melee/ranged, timed-only, post-tuning | `rowPass` L2585 inside `frameLiveIdx` |
  | period / reset weeks / day zoom | `periodPass` L1956 inside `frameLiveIdx` |
  | merge hero talents | `groupKey` decides which rows are "this spec" |
  | percentile lens (±10) | `frameLensSlice` L3251 → `inWin` |
  | Archon replica | `frameLiveIdx` elite branch (last 14 days, group floor key) |
  | tuning projection | `projSkip` drops breakdown-less parses from the same set |

- Display semantics the surfaces must keep: **unknown is never 0%** — pending and
  unavailable rows print as a count, not a value; `thin` prints the CS_THIN-style line
  ("n=7 measured in window · sample too thin — widen the lens or filters", L3968 pattern);
  a printed median always carries `n` and the light minutes behind it, e.g.
  `62% · n=143 · 41 min of light · pooled 59%`. The percentile lens makes the interesting
  read free: p50 window vs p90 window answers "do the best players stand in the light more".
- Wowhead surfaces stay icon-only (pref #11): this number must NOT ride the wowhead tooltip
  or the tile's icon anchor; a tile `title=` is the browser-native exception §7 allows only
  for tiny metadata — the surfacing lens should prefer a rendered line/row over a hover.

## 5. Tests to pin (new files under scripts/, same conventions as the existing suites)
1. `test_beam_bands.py` — the §2.3 pure function on synthetic events (start from the worked
   example in `scratchpad/lightspire_ratio.py`): apply/remove pairs; refreshbuff mid-window;
   blessing outliving the window (clipped); blessing starting before the window; blessing
   open at fight end; removebuff with no apply; two overlapping beams (union, not sum); a
   beam cast 5 s before fight end (window clipped to 5,000 ms); a wearer with blessing
   events but zero casts (`av 0`, no ratio, `bn_raw` counted); another player's casts of
   1263762 in the same fight never enter this wearer's window; `bn ≤ av ≤ |F|`;
   `bn_raw ≥ bn`.
2. `test_trinkets_sidecar.py` — records through the REAL writer path (`parse_summary` for the
   gear rows, the new stage's record writer for the ratio rows), the real emitter, and a
   reference decoder implementing §3.2: idx/cols aligned to df order; wearer detection on
   slot 12 AND 13 (and both); pending vs measured vs unavailable vs no-window bytes; the
   ds rounding and the 65535 clamp; `av>dur` clamp counted; `n` equals `len(df)`; the
   `lab` payload key present ≥ floor and ABSENT below it; empty journal ⇒ no file, health
   line says OMITTED; unknown extra keys in the doc are ignored.
3. `test_beam_doc_roundtrip.py` — lift `decodeBeamDoc` and `beamStats` out of index.html with
   `extract_js` (scripts/test_stats_sidecar_roundtrip.py L44) and run them under node against
   a real emitter document: every decoded value equals what went in; `beamStats` on a fixed
   row list matches a Python reference (median via the same `qp` definition; thin gates;
   pending/unavailable never enter the ratio).

## 6. Workflow wiring (.github/workflows/refresh.yml)
- New step **between `Fetch` (L219-244) and `Resolve names`**, same secrets env, after the
  summary stage so it only spends what that stage left under the shared 70% ceiling
  (wcl_client.py L185/L102):
  `python -u scripts/fetch_trinket_uptime.py --points 400 --budget-s 150 || echo
  "::warning::trinket uptime fetch failed; building with the existing journal"`.
  Skip it when `inputs.regear_min_key != ''` or `inputs.drain == 'true'` (those runs spend
  their whole budget on summaries by design, refresh.yml L206).
- **The cache path list does NOT change** (refresh.yml L173-180). The three new files live
  under `data/processed/` and ride the existing cache.
- The build step needs nothing new; `build()` writes/unlinks `trinkets.json.gz` beside the
  other sidecars, and deploy-site.yml's "keep live data files if newer" loop (deploy-site.yml
  L93-96) must add `trinkets.json.gz` to its `for f in …` list, or a UI push can roll the doc
  back one build while data.json's `lab` block stays new (the client then rejects on `n`
  mismatch and prints "data unavailable" — safe, but avoidable).
- .gitignore: add `site/trinkets.json.gz` and `docs/trinkets.json.gz` (same as stats/builds).

## 7. Removal path (the Lab promise)
Delete: the LAB_FEATURES entry + `hasBeam/state.beam` + `loadBeamDoc/decodeBeamDoc/beamStats`
+ the surface(s); `trinkets_sidecar()` and its call + the `lab` payload key; the workflow step
and `scripts/fetch_trinket_uptime.py`; `data/lab_trinkets.json`. The journals age out of the
cache. Neither `builds.json.gz` nor `stats.json.gz` nor their contracts are touched at any
point — which is the whole reason for the separate lane.

## 8. Open questions for the owner / other lenses
1. Median of per-parse ratios (recommended: one vote per parse, robust to one long key) vs
   pooled Σbn/Σav (time-weighted). Both are shipped; the surfacing lens picks the headline.
2. `BEAM_MIN_AV` = 12 s (one window) — too permissive? 30 s would drop short-fight parses.
3. Backfill depth beyond 3 resets: on request only (budget arithmetic in §2.5 says it is
   cheap; the reason to wait is that nobody has asked for August's light discipline).
4. Whether the journal should be committed daily (it is small enough) — a durability call.
5. If the first-run check (§2.6) shows no `cast` events for 1263762, or `Σbn_raw/Σbn ≫ 1`
   (beams outliving 12,000 ms), the definition of "available" comes back here before
   anything renders.
6. **Reconciliation with `fleet/blueprints/lightspire_surfaces.md`** (the UI-surfaces lens,
   landed the same hour). The two documents agree on the metric, the spell chain, the
   cast-window denominator, the intersection numerator, the lazy row-aligned doc, the
   payload gate and the one-entry Lab removal. They differ only in names and in two payload
   details; the implementer needs ONE contract, so:
   - **Names — adopt the surfaces lens's**: file `light.json.gz`, payload gate `D.light`,
     `state.light`, `hasLight`, `loadLightSidecar`/`LIGHTC`/`lightAgg`. Purely cosmetic; this
     document's `beam`/`trinkets.json.gz`/`D.lab.trinkets` read as those names throughout.
     (The `items[]` container in §3.2 can stay inside `light.json.gz` unchanged — it is what
     makes the next trinket a config line.)
   - **Units — keep deciseconds, not whole seconds.** The surfaces draft stores `av`/`in`
     as Uint16 seconds. A single-beam parse is 12,000 ms; second rounding on both ends
     moves a ratio by up to ±8%. Deciseconds fit the same Uint16 (cap 109 min) at no byte
     cost.
   - **Status — keep the explicit `st` byte, not a 65535 sentinel in `av`.** The sentinel
     conflates "wearer, not fetched yet" with "wearer, report gone", and both surfaces print
     "no beam data yet" vs "unavailable" differently (their §3 muted states). One byte/row.
   - **Headline — ship both, let the surface choose.** The surfaces lens leads with pooled
     `ΣI/ΣA` (time-weighted); this document's aggregator returns pooled AND the per-parse
     median. Pooled is the literal reading of "percentage of the time"; the median is one
     vote per parse and resists a single 40-minute key. Owner question #1 in §8.
   - The surfaces lens's `wowhead tooltip: "Approximately 1.25 procs per minute"` is the
     source for the RPPM figure quoted in §0.1.
7. Sibling-lens artefacts this document builds on, so the implementer does not re-fetch them:
   `scratchpad/se_1263762.csv`, `se_1263768.csv`, `se_1250527.csv`, `sm_126376{2,8}.csv`,
   `spellduration.csv`, `spell_126376{2,8}.csv`, `tt_126376{2,8}.json` (DB2 + tooltips);
   `lightspire_uptime_alias.graphql` (query draft, agrees with §2.2); `lightspire_ratio.py`
   (band math reference); the two vendored WCL schemas (`*_schema.graphql`).
