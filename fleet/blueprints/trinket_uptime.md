# BLUEPRINT — Lightspire Core "in the light" (trinket benefit ratio) · SYNTHESIS · 2026-09-08

Winner: the OWNER-FIRST design (`lightspire_owner_first.md`), built on HEAD b87aebf's events
collector, with the grafts the judgement named from MINIMAL (`lightspire_minimal.md`) and
ROBUST (`lightspire_robust.md`). The two research drafts (`lightspire_ratio_pipeline.md`,
`lightspire_surfaces.md`) supplied the spell chain and the surface ranking. Every anchor below
was read against the working tree at HEAD **a7a24ff** (06:43 UTC; `site/index.html` 7,632
lines, `scripts/fetch_procs.py` 461 lines) and the two diagnostic job logs (diagnose.yml jobs
101950096306 and 101951035988). Nothing existing was edited for this file. Line numbers drift;
function names hold.

Owner's ask, verbatim: (1) "I want to know the trinket uptime for this trinket: Lightspire
Core … add this as a special labs feature that needs to be turned on … filter the uptime the
usual knobs … maybe the uptime could show up on a tooltip on the trinket on the character
screen? however, think hard about where else it would make sense to surface it as well. pick
whatever seems more easily accessible, out of the way, clean and easily grokable." (2) "of the
time that the trinket was active, what percentage of time did the player stay in the buff to
get its effect … the uptime of the buff on the player as a percentage of when the buff was
actually available to the player."

## 0. Where it stands (state of the world, not of this file)

**Shipped on the branch.**
- be9ab78 — collector `scripts/fetch_procs.py`, `scripts/procs_spec.py` (`TRACKED` :11-14),
  `build_site_data.procs_sidecar()` :2315-2392 → `site/procs.json.gz` (write/unlink :3070-3085),
  refresh.yml step "Trinket beam benefit" :253-261 (`--budget-pts 400 --budget-s 240`,
  `WCL_MAX_SLEEP_S=30`, `WCL_BUDGET_MARGIN=400`), `fetch_data.py:643` journals the report-local
  actor id on new gear rows, `scripts/test_procs.py`.
- 2d2053f — client: `state.beam` :1619, `hasBeam/BEAMC` :1628, boot reset + `loadProcsSidecar()`
  :1713-1714, LAB entry `beam` :2508-2519 (`stamp:false`), `labMountLate` :2547, toggle handler
  :2556-2559, decoder :3584-3629, `beamStats` :3631-3642, `beamTitle` :3646-3656, `beamInline`
  :3658-3662, `renderBeamTable` :3666-3703, identity line `csBeamBandHTML` :4245-4251, fold-out
  `csBeamSub` :4516-4520, tile foot in `poolTile` :4782-4790, CSS :1264-1275.
- f678b6b — `liveIdxMulti` :3083-3119 (one pass for every spec) and `renderBeamTable()` moved to
  the END of `render()` :6642, after `FRAME_A=A` :6438 (MINIMAL's C2/C3 are landed).
- b87aebf — the collector asks for buff EVENTS instead of the Buffs table; records carry `"v": 2`
  with own-spawn times `sp` and the foreign-apply count `x`; `load_done` :212-213 and the builder
  :2342-2343 skip `v < 2`; `test_procs.py` asserts `"table(" not in gql` :174 and PASSES on HEAD
  (run today: `PASS`). MINIMAL's C5 is superseded by this.

**Live at 06:43Z** (`build_health.txt`): `built=2026-09-08T06:28:12Z` · `[season] procs sidecar:
Lightspire Core -- 289 of 46,666 wearer rows covered (1%), 289 with no beam`. That document is
run #828's (sha 978045e, still the table query, builder without the `v<2` skip): 289 v1 records,
every `r=255`. The live client therefore shows a ⚗ card whose every spec reads "no beam"
(counted, never 0 %) — correct behaviour on wrong inputs.

**Run #829 — the FIRST events run — worked.** (id 34195245108, sha a7a24ff; the "Trinket beam
benefit" step ran 06:44:19-06:45:50Z, 91 s.) Its build went live at 06:51Z: `built=2026-09-08T06:47:06Z`
· `[season] procs sidecar: Lightspire Core -- 126 of 46,678 wearer rows covered (0%), 0 with no
beam`. 126 payload rows now carry a measured ratio and **zero** wearer-fights came back without a
beam (#827/#828: 289 of 289 empty). The v1 empties are gone from the document (the `v<2` skip).
The step-0 gate in §11 is passed. The job log (read 06:52Z): `[procs] Lightspire Core: 73,762
wearer-fights in the gear journal, 0 done, 73,762 pending` → `+192 journaled, 0 failed permanently,
0 left for the next run, 430 points, 91s; stopped: point budget 400`. So: **2.24 pts per
wearer-fight blended** (events alias + masterData for actor-less rows; the line does not split
them), 4.5× the admitted `est_cost=0.5`; the 400-pt cap overshot by one batch (the check runs
before each batch, the true cost lands after); 192 journaled → 126 payload rows (66 %, as in
#827's 240 → 154: the gear journal holds wearer-fights the payload does not sample).

## 1. Correct the record first (new, from verification — do this before any other edit)

The repository currently states the cause of run #827's empties as "the Buffs table with sourceID
AND targetID set returns nothing" (`fetch_procs.py` docstring :29-31; `user_prefs.md` #24 "run 827
proved the Buffs table with sourceID+targetID returns no bands"; `checklist.md` 159 "never the
Buffs table with sourceID+targetID"). That attribution is wrong on the facts:

- Run #827 ran **be9ab78** (Actions API: head_sha be9ab780…), whose query was
  `table(fightIDs: [fid], dataType: Buffs, abilityID: 1263768, targetID: aid)` — **no sourceID**
  (`git show be9ab78:scripts/fetch_procs.py` :236-239). That is the exact shape diag pass 1's
  follow-up used and got 23 and 27 bands from (`diag_lightspire.py` :215-216; job 101950096306:
  "aura 1263768 'Light's Blessing': 23 bands, buff 87.4s" / "27 bands, buff 93.8s").
- The `sourceID+targetID` variant was introduced in 2d2053f and ran only in #828 (978045e). It was
  never the first failure.
- The difference between the diag and the collector is `bands_from_table()` :154-169, which keeps
  only `auras` entries with `guid == 1263768` (:164), whereas the diag iterated every `auras`
  entry with no guid filter. **Most likely cause (unverified — no credentials here):** an
  ability-filtered Buffs table lists its `auras` per actor, so no entry carries the spell id as
  `guid` and the filter dropped every band. Whatever the exact shape, the events path does not
  touch it.

Actions: rewrite the cause in the three places above as "run 827 journaled 240/240 empties from
`table(dataType: Buffs, abilityID, targetID)` parsed through a `guid == 1263768` filter that the
diag never applied; the events path replaced it (and costs less)"; delete `bands_from_table()`
:154-169 and its test block (`test_procs.py` :98-114 — dead code carrying the wrong fixture
`guid: T["buff"]`). Points already sunk: #827 412 pts on 240 empties (b87aebf commit message);
#828 ≤400 pts on 49 more (289−240 live rows). Both sets are v1 and are redone by construction.

## 2. Goal and the exact metric

Per wearer-fight (one report, one fight, one player wearing item 250214), in ms from the fight
start, `F` = `fights{endTime} − fights{startTime}`:

```
own spawns S  = timestamps of applybuff / refreshbuff / applybuffstack of 1263768 on the
                wearer whose sourceID == the wearer's report actor id
available A   = ∪ over s ∈ S of [s, min(s + 12 000, F)]        -- "when the buff was available"
bands B       = ∪ of [apply, remove) of 1263768 on the wearer, ANY source, clipped to [0, F]
inside I      = |B ∩ A|                                          -- only the intersection counts
r             = I / |A|          ;  r is undefined (None → 255) when S is empty
```
Code: `fetch_procs.benefit()` :102-121 (`n=len(S)`, `a=|A|`, `b=|B|`, `i=I`, `r`),
`bands_from_events()` :124-151. Tests `test_procs.py` :48-96.

What defines each side, and why (sources in §3):
- **Denominator = the beam's lifetime from the wearer's own spawns.** The area-trigger spell
  1263762 "Radiant Light" is never logged (0 events from any source, both fights, pass 2), so the
  beam's existence is inferred from the blessing it applies the instant it spawns: every logged
  `cast` of 1263768 coincides with an `applybuff` on the wearer to the millisecond (23/23 and
  27/27; deltas all 0 or −1 ms; "casts with NO applybuff within 500 ms: 0"). Lifetime is the spell
  duration 12,000 ms (SpellDuration 29). A `refreshbuff` whose source is the wearer is a SECOND
  beam spawning while they stand in the first (`test_procs.py` :64-67) — the Buffs table could
  never show that.
- **Numerator = the wearer's Light's Blessing time inside those windows.** 1263768 has no timer
  (DurationIndex 21 = −1): it is held while inside the trigger and dropped on exit or expiry
  (bands 0.1 s … 12.0 s, capped). Any source's band counts — a teammate's beam inside the
  wearer's own window is light the wearer got — but a foreign apply never OPENS a window
  (`x` counts them; `test_procs.py` :68-71).
- **Overlap is load-bearing, not a nicety:** 9/23 and 10/27 spawns (37-39 %) fell inside the
  previous 12 s. Raw 23×12 = 276 s vs union 211.0 s (−24 %); 27×12 = 324 s vs 260.8 s (−20 %).
  Reference ratios **41.4 % / 35.9 %** against classic uptimes of 8.2 % / 8.7 %; "buff outside
  any beam window 0.0 s" on both.
- **Unknown is never 0 %.** No own spawn ⇒ `r=None` ⇒ 255 in the sidecar ⇒ counted as "got no
  beam", excluded from every statistic. A wearer-fight not yet fetched is simply uncovered.

Label everywhere: **`in the light 38% of beam time`** — the denominator travels with the number
so it cannot be read as classic uptime; the game's own copy is "Stand in the Light". "uptime"
appears only inside the definition's "Not classic uptime." "beam benefit" (shipped) goes: it
names the effect and reads as a DPS gain.

## 3. Research facts (keep; sources)

| fact | value | source |
|---|---|---|
| item | 250214 Lightspire Core, quality 3, icon `inv_enchant_essenceastrallarge` | `data/names_items.json`, `data/names_icons.json`, `site/icons/i250214.jpg` |
| driver / passive | 1250527: effect 0 Proc Trigger Spell → 1263762; effect 1 Mod Rating Mastery; effect 2 dummy. SpellProcsPerMinute 109, base **1.25 RPPM** | wago.tools DB2 (`diag_lightspire.py` :4-8); wowhead tooltip "Approximately 1.25 procs per minute" |
| the beam | 1263762 "Radiant Light": ONE effect, 179 Create Area Trigger (misc 40323), DurationIndex 29 = **12,000 ms**; applies no aura; **never logged** (0 events, any source, `filterExpression: "ability.id = 1263762"`) | DB2 via `lightspire_ratio_pipeline.md` §0.1; job 101951035988 |
| the blessing | 1263768 "Light's Blessing": Apply Aura 189 Mod Rating (Mastery mask 33554432), self-target, DurationIndex 21 = −1 (no timer) | same |
| log signature | `cast` 1263768 (sourceID wearer, targetID −1) and `applybuff` 1263768 (source = target = wearer) at the same ms, 50/50; bands p50 1.79 s / 1.62 s, max 12.022 s / 12.036 s (one >12.0 s per fight, logging jitter); no `refreshbuff` seen in 50 procs | job 101951035988 |
| simc | `unique_gear_midnight.cpp` :2690-2711 uses the same trio; `lightspire_core_duration_multiplier = 0.5` with `// TODO: Emulate not standing in the light` — the simulator ASSUMES 50 % | `lightspire_ratio_pipeline.md` §0.1 |
| wearers | 73,650 of 967,729 gear records (7.61 %); 27,155 characters; W34 25,057 · W35 30,414 · W36 30,604 · W37 4,582 (partial); Elemental 16,186 · Arcane 16,034 · Holy Paladin 15,325 · next 4,700; ~30k new wearer-fights/week | job 101950096306 |
| payload wearer rows | **46,666** (builder health line, run #828) | live `build_health.txt` |
| cost, measured | pass 1: 11 pts for 2 requests (2× unfiltered Buffs table + Casts events + Casts table, then 4 ability-filtered Buffs tables) ⇒ ≤ ~2 pts per table alias; pass 2: **1 point** for one request of 2 fights + 6 events sub-queries; run #827: 412 pts / 240 table aliases = **1.72 pts/alias**; **run #829 (events): 430 pts / 192 wearer-fights = 2.24 pts blended**, masterData share unknown | job logs; b87aebf message; job 101961425298 |
| cost, admitted | events alias `est_cost=0.5` (`fetch_procs.py` :297); masterData alias 1.0 (:257); `WCLClient.observe()` replaces the guess with `pointsSpentThisHour` after every response (`wcl_client.py` :114-119) | code |
| quota | account limit 18,000/h; standing ceiling 0.70 = 12,600/h (`wcl_client.py` :51); the step sets no `WCL_QUOTA_FRACTION` | code, refresh.yml :253-261 |

## 4. Data acquisition and quota policy (HEAD b87aebf + three fixes)

**Query** (`fetch_batch` :280-298), one alias per wearer-fight, `PROC_BATCH=12` per request:
```
a{i}: report(code: "<code>") {
  fights(fightIDs: [<fid>]) { startTime endTime }
  ev: events(fightIDs: [<fid>], dataType: Buffs, abilityID: 1263768, targetID: <aid>,
             limit: 5000) { data nextPageTimestamp } }
```
`parse_node` :301-315 rejects a node without a fight clock; the cursor is followed ≤3 more pages
(:391-396; a 30-min fight has ~100 events). Pre-2026-09-08 gear rows carry no `actor` → one
`masterData.actors` alias per report, `ACTOR_BATCH=10`, memoised per run (`resolve_actors`
:248-273, `actor_of` :276); unresolvable ⇒ `procs_failed.txt` "actor not resolved" (:368-372).

**Which fights:** every gear record wearing 250214 in any slot (`candidates` :178-205, byte
prefilter + exact id, last record wins) minus journaled `v ≥ 2` and failed (`load_done`
:208-230), **newest first** by the sweep's `start_time` (`order_pending` :239-244; pref #15).

**Budget:** 400 pts / 240 s per run (defaults :445-448; step :260) under the standing 0.70
ceiling — the governor's `admit()` (`wcl_client.py` :103-109) still refuses past the ceiling
with the 400-pt margin, and a drain run that already spent its share stops at the probe
(`_probe_quota` :185) under the 30 s sleep cap (`_sleep_for_reset` :244-261 raises
`QuotaDeadline`). Chain cadence today ≈ 5 runs/h (#822 04:49 → #829 06:34), so ≤ 2.0k pts/h
= 16 % of the ceiling **while a backlog exists**, ≈ 180 pts/h at steady state (~30k
wearer-fights/week). Backlog after #829: 73,762 − 192 = 73,570 wearer-fights at the **measured
2.24 pts** ≈ 165k pts ≈ 383 runs of 400 pts ≈ **3.2 days at 5 runs/h, ~8.6 days at 48 runs/day**
(≈ 192 fights, ≈ 126 payload rows per run). Steady state ≈ 4.3k wearer-fights/day × 2.24 ≈
9.6k pts/day ≈ 3 % of the daily 70 % ceiling. Pass 2 read 1 point for six events sub-queries,
so most of the 2.24 is probably the masterData alias (one per report, memoised per RUN only —
:349 `amaps` is rebuilt every run) on pre-09-08 rows, which is the whole backlog; the split is
not in the log (R4). **Policy:** the standing budget only — no drain, no cap waiver, never
raised for this feature.

**Fix R1 — rollover-safe point accounting** (`run()` :348, :355, :420): `client.spent` is the
account's `pointsSpentThisHour`; across `QUOTA.rolled_over()` (`wcl_client.py` :125-129) or a
real hour reset `client.spent − spent0` goes negative and the point cap never trips (only
`budget_s` and the 70 % ceiling bound the run). Replace with a delta sum after every request:
```python
used += max(0.0, client.spent - last); last = client.spent      # after fetch_batch/resolve_actors
if used >= budget_pts: s["stopped"] = f"point budget {budget_pts:.0f}"; break
s["points"] = round(used)
```
Test: spent sequence 100,112,124,3,15 counts 36, not −85.

**Fix R2 — in-run systemic stop** (mirror of `fetch_data.py` :705-706 `SYSTEMIC_SHARE=0.5`,
`SYSTEMIC_MIN=20`): after ≥ 20 fetched wearer-fights in a run, if ≥ 50 % have no own spawn,
print `::warning::procs: Lightspire Core -- N of M fetched fights have no beam; query or actor
fault suspected; stopping` and stop, leaving the rest pending (`s["stopped"]="systemic"`). It
would have stopped #827 at 20 fights instead of 240 and saved ~800 pts across #827/#828. A
genuine 50 % no-beam population is implausible at 1.25 RPPM over a 10-30 min key
(P(no proc in 10 min) ≈ e^−12.5).

**Fix R4 — admit what it costs, and say where it went:** `est_cost=0.5` per events alias (:297)
under-reserves by ~20 pts per 12-alias request against the governor's 400-pt margin; after the
first response of a run, admit the observed per-alias delta (`(spent − last) / len(parts)`,
floored at 0.5) instead of the constant. Account points separately for `resolve_actors` and
`fetch_batch` and print both in the run line (`… 430 points (events 210 · actors 220) …`) and in
`fetch_health.txt` (§7), so the first week says whether a persistent per-report actor cache
(`data/processed/procs_actors.json`, riding the journal cache) is worth its ~1 pt per report.
Also tighten the cap to "stop when `used + est(next batch) > budget`", so a run lands under 400
instead of one batch over (#829: 430).

**Fix R3 — workflow guard:** `if: inputs.regear_min_key == ''` on the step at :253 (a regear
run is the one path that sits through the reset; four minutes of collector there buys nothing).

**Durability (later, ROBUST):** `procs.jsonl` rides only the Actions cache (refresh.yml
:170-182 — DO NOT add cache paths); an eviction restarts the backfill. Add `fetch_procs.py
--export` → `data/procs_seed.jsonl.gz` with derived fields only (`v report_code fight_id
character server key actor f sp x n a b i r`, ~100 B/rec ⇒ ~2 MB gz at 73k, +0.5 MB/week),
`git add` in the Monday slot beside `gear.jsonl.gz` (:457-463), restored when the journal is
absent (the `fetch_data.py` :177-183 pattern). Loss bound: one week ≈ 30k wearer-fights.

## 5. The interval computation — edge cases and decisions

| case | HEAD behaviour | decision |
|---|---|---|
| buff already up when the window opens | impossible by construction: windows open at own spawns | — |
| band open at t0 (pre-pull aura, no apply seen) | `bands_from_events` never opens a band without an apply; a `removebuff` with no open band is ignored (:146). If an apply is logged at/after t0 it is a spawn | accept; health counts bands starting < 100 ms; ROBUST's t0 exclusion rests on an unobserved WCL behaviour and would drop a real pull-time spawn — not adopted |
| overlapping beams (37-39 % of spawns) | `union()` :75-85 on windows and bands; one `inter_len` :88-99 | keep — pinned :50-53 |
| second beam while already blessed | `refreshbuff` with `sourceID == wearer` is a spawn (:139-141) | keep — pinned :64-67; ROBUST's "≈7 % blind spot" and its Casts tripwire are moot |
| leave and re-enter the same beam | a second own apply reads as a second spawn ⇒ window extended ≤ 12 s ⇒ ratio biased LOW | accept; 0/50 procs showed it; bands and `sp` are journaled so a model change re-derives without refetch; health prints bands-inside-open-window share |
| teammate's beam blesses the wearer | foreign apply counted in `x`, opens no window; its band counts inside own windows (:68-71) | keep; health prints Σx / Σspawns |
| death inside a beam | not clipped: the aura drops, the rest of the window counts as "out" | accept — literal reading; ≈ 0.4 % per death per parse, under u8 quantisation; a `Deaths` alias is the named v2 hook, not built |
| fight ends inside a beam | window and band clipped to `F` (:113, :116; test :58-60) | keep |
| band 22-36 ms over 12.0 s (jitter) | numerator capped by the intersection (test :54-57) | keep; health counts bands > 12.5 s |
| zero own spawns | `r=None` → 255 → `nb`, never a value (test :61-63) | keep |
| `b` vs `i` | journal has both; **the sidecar ships `b`** (:2373) | **fix C1** (§6) |

## 6. Journal and sidecar contract

**Journal** `data/processed/procs.jsonl`, one line per wearer-fight per tracked trinket (:411-415):
```json
{"v":2,"report_code":"1g9zArwpd3tGWYNQ","fight_id":2,"character":"Paandorra","server":"Realm",
 "key":"lscore","actor":9,"f":1060021,
 "bands":[[117685,117911],[174415,180200]],"sp":[117685,174415],"x":0,
 "n":23,"a":211000,"b":87400,"i":87400,"r":0.4142}
```
ms from fight start; `r` 4 dp or null; `v<2` lines are neither done nor shipped.
`procs_failed.txt`: `code:fid:character<TAB>key<TAB>reason`, never retried.

**Sidecar** `site/procs.json.gz` (`procs_sidecar` :2315-2392) — v1 shape kept, one value fix,
one additive block:
```json
{"kind":"procs","n":751687,"enc":"sparse","idxdelta":true,
 "trk":[{"key":"lscore","item":250214,"name":"Lightspire Core","buff":1263768,
         "buff_name":"Light's Blessing","window_ms":12000,
         "cov":{"wearers":46666,"measured":31877,"nobeam":412,"dmin":"2026-08-19","dmax":"2026-09-08"}}],
 "cols":{"lscore":{"idx":"<b64 LE u32, delta-coded payload row indices>",
                   "r":"<b64 u8 percent 0..100; 255 = no own beam>",
                   "a":"<b64 LE u16 seconds available (|A|)>",
                   "b":"<b64 LE u16 seconds INSIDE AND AVAILABLE (I) -- see C1>",
                   "p":"<b64 u8 own beams (n)>"}}}
```
Rows are payload rows in `df` order joined on `_gear_key` :347 (null/NaN server joins); the
client rejects `n ≠ N` (:3603-3606). Coverage is against the payload's wearer rows (`meta`
gear lists the item, :2364-2367).

- **C1 (MINIMAL/OWNER-FIRST, value-only):** :2373 `rec.get("b")` → `rec.get("i")`; keep the
  column key `b` so the live decoder needs no change; fix the docstring :2320 ("b: u16 buff s"
  → "b: u16 s inside AND available") and the client comment :3582. Today the client pools
  `Σb/Σa` as "time-weighted" (:3636, :3642) and, with foreign-source bands journaled, that can
  exceed 100 %. Test: assert the `b` column equals `i` on a fixture where `i ≠ b`
  (`test_procs.py` :254 currently asserts `[0, 6]` where `b == i`; add a record with a foreign
  band outside any window).
- **`cov` (ROBUST, scaled down):** additive on the `trk` entry; `wearers=wear`, `measured=len(idx)`,
  `nobeam=nob`, `dmin/dmax` = ISO dates of the earliest/latest covered payload row. Old clients
  ignore it; the new card hint omits itself when absent. The per-row status byte `st`
  (pending/measured/unavailable) is DEFERRED to a v2 built only if pending rows persist past the
  backfill — the tile already prints `n=143 of 312` from the pool counts.
- Size: 10 B/row raw ⇒ 46,666 rows ≈ 467 KB raw, ≈ 200-350 KB gz at full coverage — "tens of
  KB" (:2309, prefs #24) holds only while coverage is low. Fetched at boot (:1714); switch to
  fetch-on-first-toggle (the `loadBuildsSidecar` shape :3711) if `build_health.txt` ever shows
  > ~500 KB. Print the size on the health channel, not only stdout (:3082-3083 is `print`).
- Ops: `.gitignore` gains `site/procs.json.gz` and `docs/procs.json.gz` beside :24-34;
  `deploy-site.yml:93` keep-list gains `procs.json.gz` — without it a UI deploy over an older
  artifact keeps the newer live `data.json.gz` but ships the artifact's older `procs.json.gz`,
  `n` mismatches, and the client rejects the document (card vanishes until the next refresh).

## 7. Health lines (`build_health.txt`; prefs #20)

Existing: `[season] procs sidecar: Lightspire Core -- X of Y wearer rows covered (Z%), K with no
beam` (:2380-2382), `… 0 of Y wearer rows covered; column not shipped` (:2376), `procs.json.gz
not shipped (no journal or no covered rows)` (:3085), `no procs.jsonl journal; not shipped` (:2330).

Add, one line each, from `procs_sidecar` (all from the journal records it already reads):
```
[season] procs sidecar: Lightspire Core -- pooled Σinside/Σavailable 39.2% · median per-parse 38% · SHIPPED 0.21 MB gz
[season] procs sidecar: Lightspire Core -- bands > 12.5 s: 12 (0.1%) · bands starting < 100 ms: 3 · bands inside an open own window: 4.1% of spawns · foreign applies: 1.9% of spawns
```
`::warning::` when `nobeam ≥ 50 %` of measured (the systemic signature reaching the build).
From the collector, appended to `data/processed/fetch_health.txt` (fetch_data rewrites it
earlier in the same run, :792; the builder folds every line as `fetch.<key>` :2891-2895):
```
procs.lscore.total=73650
procs.lscore.done=31877
procs.lscore.ok=228
procs.lscore.failed=0
procs.lscore.transient=0
procs.lscore.points=430
procs.lscore.points_events=210
procs.lscore.points_actors=220
procs.lscore.stopped=point budget 400
```

## 8. Client: slicing, Lab entry, surfaces

### 8.1 Slicing under the usual knobs — by construction
Every number is `beamStats(rows)` :3631-3642 (interpolated median of per-parse `r`, p25/p75,
pooled `Σb/Σa`, `nb`) over a row set the page already filters: `liveIdxFor(key)` :3075 →
`liveIdxMulti` :3083-3119 = `baseMasks` + `rowPass` (key range, dungeon, region, role,
melee/ranged, timed-only, post-tuning) + `periodPass(state.weeksA)` + `projSkip`, with the
elite branch reading `FRAME_A…floorK` for the **Archon replica**; `lensWindow(idx)` :3323-3336
keeps `[pctl−10, pctl+10]` (the **percentile lens**); `groupKey` :2652 folds **merge hero
talents**. Time compare is period A only (the `perANote` idiom). No new wiring.

**Population alignment (new):** `csBeamBandHTML` :4247, `csBeamSub` :4518 and `poolTile` :4787
read `d.gearIdx` (BUILDSC-gated, `screenData` :4090-4098 — the builds sidecar is windowed by
`_sidecar_window` :629 and vocab-capped :1102-1103, so gear-known ⊂ window). Change all three
to `d.win` :4088 (the lens window). Covered rows are wearers by construction, so no non-wearer
leaks in; the Character screen and the frame row then print the identical figure, and the line
still renders when BUILDSC is thin or absent. Keep "of 312" (`x.c`, wearers in the gear-known
pool) on the tile only.

### 8.2 The Lab entry (`LAB_FEATURES` :2482; amend the shipped object :2508-2519)
```js
{id:"beam", name:"✨ Lightspire Core · in the light", badge:"LIGHT", mount:"labbox",
 card:true, stamp:false,                       // a readout, not a modifier — never badges scope lines
 gate:()=>hasBeam,                             // sidecar decoded — else the card does not exist
 active:()=>hasBeam&&state.beam,
 controlHTML:'<label class="small" id="beambox"><input type="checkbox" id="beamcb"> '
   +'Show time in the light<span class="hint" id="beamhint"></span></label>'
   +'<div id="beamtbl"></div>',
 scopeBits:()=>state.beam
   ?"share of each Lightspire beam's 12 s its wearer stood inside it — Spec Frame row, "
    +"Character screen (identity line, trinket tile, fold-out row) and per spec below · "
    +"follows every filter and the lens · period A under compare"
   :"off — nothing added anywhere"}
```
No `exemptions` (it modifies no section). `#beambox.title` carries the one definition sentence
(the `tunebox.title` slot). `#beamhint` from `cov`: `(Aug 19 – Sep 8 · measured on 31,877 of
46,666 wearer-parses)`; omitted when `cov` is absent. The toggle handler :2556-2559 must also
call `renderFrame()` (today it calls `renderBeamTable()` and `renderScreen()` only).
`state.beam` stays session-only (not in the hash, no localStorage) — see §13 Q1.

### 8.3 Surfaces, ranked by where the owner's eye goes
The daily read (prefs #8, #18): Overview → click a bar → the **Spec Frame** docks (one click,
where a spec is vetted) → `Character screen →` for gear. The frame rail is hidden while the
screen is open (`body.charscreen #frame-pos{display:none}` :507), so the frame row and the
screen's identity line are one reading in two places, never two at once.

**PRIMARY (new) — one row in the Spec Frame's Overview block.** `frameIdentityHTML` :3149,
via `row(k,v)` :3154, inserted after the Deathless rows :3175-3178 and before the rating row
:3179-3181 — a transposed key/value ledger, sort-rule exempt (pref #12), where the 4pc row
lived until pref #22 (the specframe.md §5 precedent, retired in one commit).
```js
// inside frameIdentityHTML, after the Deathless rows: `row` is that function's local closure,
// so this lives there (or takes `row` as an argument) — not a free-standing helper
function frameBeamRow(ctx,row){
  if(!(hasBeam&&state.beam&&ctx.g)) return "";
  const idx=liveIdxFor(ctx.key);
  const A=beamStats(lensWindow(idx).inWin);
  if(!A.n&&!A.nb) return "";                                   // no covered wearer: no row
  const nb=A.nb?" · "+fmtInt(A.nb)+" got no beam":"";
  if(skillOn()){                                               // p50 · p85 — the owner's self-eval axis
    const B=beamStats(lensWindow(idx,state.pctlB).inWin);
    return row("Lightspire Core",beamPct(A.p50)+" · "+beamPct(B.p50)
      +'<div class="fnote">in the light, players around p'+state.pctl+" · p"+state.pctlB
      +" (lens ±10) · n="+fmtInt(A.n)+" · "+fmtInt(B.n)+nb+'</div>');
  }
  const v=A.n>=BEAM_MIN_N?'in the light <b>'+beamPct(A.p50)+'</b> of beam time'
                         :'in the light <span class="na">thin · n='+fmtInt(A.n)+'</span>';
  return row('<span title="'+escA(beamTitle(A))+'">Lightspire Core</span>',v
    +(A.n>=BEAM_MIN_N?'<div class="fnote">median · n='+fmtInt(A.n)+" around p"+state.pctl
      +" (lens ±10)"+nb+(state.compare?" · period A":"")+'</div>':""));
}
```
`lensWindow(idx, pctl=state.pctl)` gains that one optional argument (:3325 reads `state.pctl`
for `lo/hi`; nothing else changes). Archon replica needs nothing (`liveIdxMulti` carries the
elite branch). Exact strings:

| state | key | value line | fnote |
|---|---|---|---|
| normal | `Lightspire Core` | `in the light <b>38%</b> of beam time` | `median · n=143 around p50 (lens ±10)` |
| no-beam present | same | same | `… · 12 got no beam` |
| time compare | same | same | `… · period A` |
| skill compare | same | `38% · 51%` | `in the light, players around p50 · p85 (lens ±10) · n=143 · 88` |
| thin (n<10) | same | `in the light <span class="na">thin · n=4</span>` | none |
| no covered wearer / `!ctx.g` / card off | row absent | | |

**SECONDARY (shipped, relabelled) — the Character screen.** The owner's "tooltip on the
trinket" becomes the tile's rendered foot; the `title` carries only the definition sentence
(design §7 / prefs #7, #11: no cursor tooltip as a data surface; the icon is wowhead's).
- identity line (`csBeamBandHTML` :4249-4250): `✨ Lightspire Core: in the light <b>38%</b> of beam time <i>n=143</i>`
- pooled Trinket tile foot (`poolTile` :4788): `in the light <b>38%</b> <i>n=143 of 312</i>` —
  ONE `n` (today two bare `n=` sit on the tile: the meta's `n=312` and the foot's `n=143`)
- fold-out sub-line (`csBeamSub` :4519, `csFoldTR` 7th arg :4433): `in the light <b>38%</b> <i>n=143</i>`
- thin, everywhere: `in the light <span class="na">thin · n=4</span>`

**CROSS-SPEC (shipped, keep; verified checklist 163) — the ⚗ card's sortable per-spec table**
`renderBeamTable` :3666-3703 (`table.beamtbl`, sort id `beam:tbl`, default Benefit desc). Rename
the column `Benefit` → `In light`, header title "median share of each beam's 12 s its wearer
stood inside it, players around pN (lens ±10)"; empty text "no spec has 10+ wearer-parses with
beam data in the current filters (k below the floor)". OWNER-FIRST's Data Table column is
DROPPED: a 13th column at 1920 px risks pref #10 and mixes a lens-window statistic into a
full-view table.

**The two string functions** (`beamInline` :3658-3662, `beamTitle` :3646-3656) are the only
places the words live; the relabel is two edits plus the card entry:
- `beamInline(s)`: `s.n>=BEAM_MIN_N ? 'in the light <b>'+beamPct(s.p50)+'</b> <i>n='+fmtInt(s.n)+'</i>' : 'in the light <span class="na">thin · n='+fmtInt(s.n)+'</span>'`
  (callers that want "of beam time" append it — the identity line and the frame row).
- `beamTitle(s)`: "In the light: of the seconds a Lightspire Core beam was available to these
  wearers, the share they stood inside it (Light's Blessing active). A beam lasts 12 s from where
  it spawns; overlapping beams count once. Median over N wearer-parses in this window with beam
  data (p25 … · p75 … · time-weighted …); K more got no beam that run and are not counted. Not
  classic uptime."

Amend `user_prefs.md` #24 (:275-306: "beam benefit" → "in the light"; "tens of KB" → measured
size; the run-827 cause per §1; the Spec Frame row as primary) and `checklist.md` §S 158-163
(159 cause; 161 `beamInline` strings; 162 add the frame row, `d.win`) in the same commit.

## 9. Degradation
404 / no `DecompressionStream` / decode reject / `n ≠ N` ⇒ `hasBeam=false` ⇒ `gate()` false ⇒
no card, no frame row, no identity line, no tile foot, no fold-out line, no table, no
"Lightspire" string in the DOM (`renderLabCards` :2525 skips a false gate; `labMountLate` :2547
mounts only after decode). Sidecar present, spec has no covered wearer in the window ⇒ nothing
for that spec. Covered but `n<10` ⇒ the muted `thin · n=k` form, never a number. All 255 ⇒
"got no beam" counts, no percentage. Builder: journal missing or zero covered rows ⇒ file
unlinked in both publish dirs (:3073-3075) and the health line says so. A pipeline commit that
lands before the client commit changes nothing visible (`cov` ignored, `b` slot value-only).

## 10. Tests
- `scripts/test_procs.py` (extend; PASS on HEAD): C1 `b` column == `i` on a fixture with a
  foreign band outside any window (`i ≠ b`); rollover budget (spent sequence 100,112,124,3,15
  counts 36); systemic stop after 20 fetched / 10 empty leaves the rest pending and prints the
  warning; `cov` block values and dates; seed export/restore round-trip (when built); delete the
  `bands_from_table` block :98-114.
- New `scripts/test_procs_roundtrip.py` (node; `extract_js` from
  `test_stats_sidecar_roundtrip.py` :44, never a silent skip): decode a `procs_sidecar()` document
  with the real `decodeProcsSidecar` (delta idx map, 255 → `nb`, `a/b/p` values); `beamStats`
  p50 (interpolated) / pooled / nb against a Python reference, `pooled == Σi/Σa`; `beamInline`
  output contains "in the light" and never "uptime"; `beamTitle` contains "Not classic uptime";
  static: `renderBeamTable()` textually follows `FRAME_A=A` in `render()`, the manifest entry
  has `stamp:false`, `.gitignore` names `site/procs.json.gz`, `deploy-site.yml` keep-list names
  `procs.json.gz`, `refresh.yml` step carries `if: inputs.regear_min_key == ''`.
- Manual at 1920×1080 (the checklist-163 walk, extended): card on → click Arcane → frame row with
  fnote; Skill compare → `38% · 51%`; Time compare → `· period A`; Archon on → figures move on
  the first render; lens drag smooth; Character screen → identity line equals the row, tile foot
  one `n`, fold-out line; card table sorts both ways; card off → every surface gone; simulate
  404 → nothing anywhere, zero console errors.

## 11. Rollout order (the live page never breaks)
0. **PASSED at 06:51Z:** run #829's health line reads `126 of 46,678 wearer rows covered, 0 with
   no beam` and its run line `+192 journaled … 430 points, 91s` (§0). Had #829 been empty
   too, the next suspect would have been the actor id path — `playerDetails` id at `fetch_data.py:643`
   vs `masterData` ids at `fetch_procs.py:248-273` — not the query shape (diag pass 2 read
   these events with ids 9 and 505 and got 45 and 54). Do not spend another 400 pts before
   comparing one resolved id against the fight's `friendlyPlayers`.
1. **Record + pipeline commit:** §1 wording in three places, delete `bands_from_table` + its test
   block; C1 + docstring/comment; `cov`; R1 rollover; R2 systemic stop; R4 adaptive `est_cost` +
   the events/actors point split; §7 health lines and the `fetch_health.txt` append; tests. Live at the next refresh; value-only on the `b` slot, so old
   and new clients decode identically. Push in the gap after a run's "Commit the export" step
   (pref #21; rebase.autoStash is on since b87aebf :476).
2. **Client commit:** `beamInline`/`beamTitle` strings, manifest entry (name, badge `LIGHT`,
   checkbox text, hint, scope bits), `frameBeamRow` + `lensWindow(idx, pctl)` + `renderFrame()` in
   the toggle handler, `d.win` in the three screen surfaces, tile foot with one `n`, table column
   header; amend prefs #24 and checklist 159/161/162. Deploys via deploy-site in minutes over
   whatever sidecar is live; every addition is behind `hasBeam && state.beam`.
3. **Ops commit:** `.gitignore`, `deploy-site.yml:93` keep-list, `refresh.yml` `if:`. Inert to users.
4. **Later:** the Monday seed (`--export`, restore, `git add`) once the backfill has something
   worth keeping (~1 week of journal).
Each step reverts alone; a missing later step renders nothing rather than something wrong.
Effort: ~1 day (pipeline + tests half a day, client a third, ops + live walk the rest).

## 12. Deliberately not built (explicit non-goals)
A tooltip NUMBER on the tile (the `title` carries the definition only); a Data Table column; a
Lab OUTPUT section; A·B compare columns on the card table; time-compare B for this number;
deciseconds; the per-row status byte `st` (v2, only if pending rows persist); an N-trinket
dual-shape decoder (`procs_spec.TRACKED` already makes N trivial on the pipeline side; the
client keys on `BEAM_KEY` until a second entry exists); death clipping (the `Deaths` alias is
the named hook); Casts-table tripwire (redundant under the refresh-as-spawn model); a
remembered ⚗ toggle (§13 Q1); ROBUST's t0-band exclusion.

## 13. Open questions for the owner (only those that change the work)
1. **Remember the ⚗ switch per browser?** OWNER-FIRST proposed `localStorage wowlogs.lab.v1`
   like collapsed sections and the saved lens. `queue.md` :6-16 ("SCRAPPED — session
   persistence … Do not resurrect it — not as a small win, not as a side effect of another
   change") reads against it; the two precedents were owner-requested. NOT built unless you say
   yes — one line in the delivery note.
2. **Death inside a beam:** literal reading kept (counts as out of the light). Say so if you want
   positioning discipline separated from dying; that is a `Deaths` alias (+~1 pt/fight) and a
   re-derive from the journaled bands, no refetch.
3. **Card table vs Data Table column:** this synthesis keeps the shipped card table and adds the
   frame row; the column is off the table unless you want a cross-spec ranking beside the DPS
   columns (13th column at 1920 px — would be measured first).
4. **Backfill pace:** standing 400 pts/run only — at the measured 2.24 pts/wearer-fight that is
   ~3-9 days to clear 73.6k (this reset fills first, newest-first). Nothing changes unless you
   want a one-off drain for this; a persistent actor cache (R4) is the cheaper lever if the
   masterData share turns out to dominate.

## 14. As shipped — reconciliation against HEAD (2026-09-08, after the panel)

Adopted from this synthesis, in the commit that follows it: the record correction (§1)
in fetch_procs.py's docstring, user_prefs #24 and checklist 159, with `bands_from_table`
and its test deleted; C1 (the sidecar's `b` is the record's `i`, inside seconds); the
`trk[].cov` block and the card's "measured so far" hint; rollover-safe point accounting
(only positive `client.spent` deltas); the in-run systemic stop (first 20+ results held
back, ≥50 % no own spawn ⇒ nothing journaled, `::warning::`, stop); `if:
inputs.regear_min_key == ''` on the step; `procs.json.gz` in `.gitignore` and in
deploy-site.yml's keep-list; `fetch.procs.lscore.*` folded into build_health.txt via
fetch_health.txt; pooled/median in the sidecar health line and a `::warning::` at ≥50 %
no-beam; the relabel to "in the light **38%** of beam time · n=143 of 312" (card "✨
Lightspire Core · in the light", badge LIGHT, checkbox "Show time in the light",
"uptime" only inside "Not classic uptime."); the Spec Frame Overview row as the primary
surface with skill compare via `lensWindow(idx, pctl)`; population alignment (identity
line, tile, fold-out read `d.win`) so the frame row and the screen print one figure; the
toggle also re-renders the frame; static tests for render order and the strings.

Kept as shipped, per the panel's own MINIMAL grafts: the card's per-spec table as the
cross-spec surface (the Data Table column is dropped); no A·B compare columns; no
deciseconds; no N-trinket decoder; no per-row status byte (deferred until pending rows
persist past the backfill). Not built: the remembered toggle (queue.md's scrapped
session persistence — put to the owner, not done). Deferred: the weekly durability seed.

Verified after the grafts: test_procs.py (model, events parser, candidates, done/order,
run against a scripted client incl. v1 redo, rollover, systemic stop, sidecar with
b == i and cov, static client checks) and scratchpad/verify_beam.py in headless Chromium
against the live payload with a synthetic sidecar.

## 15. Durability audit (2026-09-08, after §14; three lenses, each adversarially verified)

* procs.jsonl cannot be re-collected without gear.jsonl: `fetch_procs.candidates()` walks
  GEAR_FILE and yields an empty work list when it is absent. gear.jsonl has no working
  off-cache copy (gear.jsonl.gz untracked, never committed, written only on
  commit_export/regear dispatches; data/checkpoints/ does not exist; Release assets unbuilt).
  Under a whole-cache loss the shipped feature therefore loses its entire history, not a
  week; only post-loss wearer-fights ever appear again. §4's "an eviction restarts the
  backfill" and the owner-first blueprint's "one week ≈ 30k pts" bound assumed gear survives.
* Cache exposure: one journals entry is 436.7 MB; the 10 GB default retains ~22-24 entries,
  ~7 h of history at the observed 3.3 runs/h. LRU and the 7-day rule cannot evict the newest
  entry at this cadence; the real loss sequence is an empty/failed restore (actions/cache
  warns and continues) followed by `Save (if: always())` of an impoverished key that every
  later run restores. Precedent: run 32625724812 (2026-08-23, green). No guard exists.
* Optimistic re-collection (gear intact): ~165k pts for 73.7k wearer-fights at 2.2 pts
  (masterData for pre-actor-id records; ~1.1 pts once actor ids are journaled), ~4.7-5.6 days
  gross / ~6-8 net at the observed ~80 runs/day; a season-end loss ~723k pts ≈ 22-25 days.
* Seed, measured (not the ~2 MB the panel assumed): bands-stripped 219 B raw / 54 B gz per
  record → 4.0 MB gz today, +1.6 MB/week, ~30 MB at season end; 17 weekly full-file commits
  ≈ 288 MB of history (gzip blobs do not delta), append-only weekly shards ≈ 31.5 MB/season.
  The daily CSV commit is ~50 MB per commit for comparison. A seed must carry `v` ≥ 2 or the
  builder and the done-set ignore it, and it needs its own `git add` in the daily list.
* Real per-run spend: Fetch ~1,950-2,010 pts + procs 412-430 (the 400 stop trips after the
  crossing batch); ~5.3k pts/h of ceiling headroom remain.

## 16. Drain outcome and the two facts it taught (2026-09-08)

* The one-reset backfill is complete: 99.2 % of this reset's payload wearer rows carry a
  beam record (17,993 of 18,147 at 19:20 IST), every region >= 98 %, the standing collector
  (1,500 pts / run at 85 %) finishing the last few hundred and then keeping pace with Fetch.
* Fact 1 -- date the window from players.jsonl, never from the rankings journal alone: the
  sweep dates only fights still on a leaderboard; the rest were "older" and the drain ended
  itself at 56 %. `fight_times()` fixed it (checklist 168).
* Fact 2 -- WCL's hour is rolling from the first spend after expiry (checklist 167); a
  100 % drain run therefore buys one full window and then ~45 min of 429s for everyone in
  the concurrency group. Budget the NEXT drain as "one window per ~75 min", not per clock
  hour; the 429 path's `WCL_MAX_SLEEP_S` cap is what keeps those runs short.
* Journal `done` (29k) is not site coverage (18.6k): ~10k records belong to fights the
  payload filters out. Read `cov.measured` / the health line, or scratchpad/cov_live.py.
