# BLUEPRINT — Lightspire Core beam benefit · MINIMAL lens · 2026-09-08 06:10 UTC

Angle: the smallest change that answers the owner's question correctly; no new sidecar
beyond the one already committed, no payload stub, no second file, no new surface.
Written against the COMMITTED state — `be9ab78` (collector, journal, sidecar, workflow
step) and `2d2053f` (client) — both on `origin/claude/wow-mythic-dashboard-jv235l`.
Nothing existing was edited for this document.

## 0. Where things stand (verified, not assumed)

- Pipeline landed in `be9ab78`: `scripts/fetch_procs.py` (collector), `scripts/procs_spec.py`
  (the one tracked-trinket table: `{"key":"lscore","item":250214,"buff":1263768,
  "window_ms":12000}` :11-14), `build_site_data.procs_sidecar()` (:2315-2394, writes
  `site/procs.json.gz` :3069-3084), `fetch_data.parse_summary` now journals the actor id
  (:638-644), refresh.yml step "Trinket beam benefit" (:251-261: `--budget-pts 400
  --budget-s 240`, `WCL_MAX_SLEEP_S=30`, `WCL_BUDGET_MARGIN=400`, `|| echo ::warning`).
- Client landed in `2d2053f` (`site/index.html`): `state.beam:false` :1619;
  `hasBeam/BEAMC` :1628; boot reset + `loadProcsSidecar()` :1713-1714; LAB entry `beam`
  :2500-2519 (`stamp:false`, checkbox `#beamcb` + `<div id="beamtbl">`); `labMountLate`
  :2547; wiring :2556-2559; loader/decoder :3557-3613; `beamStats` :3615-3627;
  `beamTitle` :3630-3640; `beamInline` :3642-3646; `renderBeamTable` :3647-3688
  (`BEAM_MIN_N=10`); surfaces: Character-screen identity line `csBeamBandHTML` :4222-4235
  (PRIMARY per user_prefs #24), fold-out sub-line `csBeamSub` :4499-4504, pooled trinket
  tile foot + title :4766-4773; `render()` calls `renderBeamTable()` :6417; CSS :1264-1273.
- Live at 06:05Z: payload 751,687 rows; stats/builds sidecars cover 538,019 (72%)
  (`build_health.txt` lines 19-20); `procs.json.gz` → HTTP 404 (run #826 checked out
  `7b64360`, one commit before the collector); run #827 in progress on `be9ab78` (started
  06:00:57Z) — the FIRST collector run, and it runs the pre-`sourceID` query; the UI push of
  `2d2053f` (06:06:52Z) triggers deploy-site.yml, which overlays the UI on the latest refresh
  artifact — the client will 404 on the sidecar until the next refresh build ships it.

## 1. The metric and the log signature (settled by diag passes 1-2, 50 procs)

Per wearer-fight: `bands` = Light's Blessing (1263768) intervals on the wearer, from the
wearer's OWN beams (`sourceID = targetID = actor`, `fetch_procs.py:232-246`); the area
trigger spell 1263762 is never logged, and every band START coincides with a beam spawn
(cast == applybuff to the ms, 50/50). So

    avail   = ∪ over band starts s of [s, min(s + 12 000 ms, fight_ms)]
    benefit = |∪bands ∩ avail| / |avail|          (`benefit()`, fetch_procs.py:90-105)

`r = None` when no beam spawned (no evidence; sidecar 255; counted, never 0%). Reference
fights 41.4% / 35.9% against classic uptimes 8.2% / 8.7%. SimulationCraft's stand-in for
this number is `midnight.lightspire_core_duration_multiplier = 0.5`.

### 1.1 Edge cases, each with its decision
| case | what happens today | verdict |
|---|---|---|
| buff already up when the window opens | impossible by construction — the window OPENS at the band start; the only variant is a band open at fight start: `bands_from_table` clips to `max(0, s−t0)` (:122) so the window is `[0, 12 s]`, overstating availability by the pre-pull part, ≤12 s once per fight (≤4% of a ~300 s denominator, worst case) | accept |
| two beams overlapping | union (`union()` :63-73); at 1.25 rppm P(next proc < 12 s) ≈ 1−e^(−0.25) ≈ 22%, so without the union the denominator would run ~10% high — pinned by test_procs.py:49-51 | keep |
| leave and re-enter the same beam | the second band start reads as a second spawn and extends the window by ≤12 s (ratio biased LOW); 0 of 50 procs in the reference fights showed it | accept; tripwire in §6 (the journal keeps the bands, so a model change re-derives without refetching, fetch_procs.py:35-38) |
| death inside a beam | not clipped: the Buffs table carries no deaths; the aura drops at death and the rest of that window (≤12 s) counts as "out of the light". P(death falls inside a window) ≈ avail/duration ≈ 25%; expected bias ≈ 0.25 × 6 s / 300 s ≈ 0.5% per death per parse — under the u8 percent quantisation | accept; upgrade path = Summary `deathEvents` (already parsed for counts, fetch_data.py `parse_summary`) if they carry timestamps — verify before relying on it |
| fight ends inside a beam | window clipped to `fight_ms`, band clipped too (test :57-58) | keep |
| a band longer than 12 s (logging jitter) | numerator capped by the intersection (test :54-55) — but the SIDECAR ships `b` (total buff s), not `i` (inside s): `build_site_data.py:2371 b.append(... rec.get("b") ...)`, while the client comment says "a/b: u16 seconds available / inside" (:3566) and `beamStats` pools `Σb/Σa` (:3620-3626). `[[0,20000]]` would pool to 167% | **fix C1** |
| zero beams | `r=None` → 255 → `nb` count, excluded from every quantile (:3619) | keep |
| two wearers in one group | `sourceID` filter keeps each wearer's own beams (`2d2053f`) | keep; run #827's ≤400 records predate it → **C5** |

## 2. Data acquisition (unchanged plan; numbers to read, not to guess)

One aliased `table(fightIDs:[fid], dataType: Buffs, abilityID: 1263768, sourceID: aid,
targetID: aid)` per wearer-fight, `PROC_BATCH=12` per request, admitted at 1.0 pt each;
`masterData.actors` once per REPORT for gear rows written before 2026-09-08 (`resolve_actors`
:200-225, memoised per run). Which fights: every gear-journal record wearing 250214
(`candidates()` :132-159, byte prefilter + exact id check), newest fight first
(`order_pending` :191-196 — user_prefs #15 respected), minus journaled/failed
(`load_done` :162-182). Budget per run: 400 pts / 240 s under the STANDING 70% ceiling
(`WCLClient` reads `rateLimitData` on every response, so the 1.0 estimate self-corrects).

Arithmetic: 48 runs/day × 400 = 19.2k pts/day = 6.3% of the 302k/day ceiling (12.6k/h).
Backlog ≈ 73k wearer-fights (7.6% of gear-known parses, diag pass 1) + one masterData
sub-query per pre-09-08 report (reports hold several fights; call it +20-30k) ≈ 100k pts
≈ 5-6 days at the cap; steady state ≈ 30k/week ≈ 4.3k/day ≈ 11 runs' worth — the other
~37 runs a day find `pending 0` and never open a client (`run()` :276-278, zero cost).
Newest-first means the current reset is covered within the first day.
Calibration: read run #827's log line `[procs] Lightspire Core: +N journaled, … P points, Ts`
— if P/N is materially above 1.0, lower `--budget-pts` is NOT the fix (the client governs);
the number only re-sizes the backlog days above.
Backfill policy: none beyond the standing budget. No drain, no dispatch input, no cap waiver.

## 3. Journal + sidecar shape (as shipped; no change)

`data/processed/procs.jsonl` — one line per wearer-fight per tracked key:
`{report_code, fight_id, character, server, key, actor, f, bands:[[s,e]…], n, a, b, i, r}`
(ms; `r` 4 dp or null). Rides the journals cache like every journal (refresh.yml:173-179).
`data/processed/procs_failed.txt` — `code:fid:character\tkey\treason`, never retried.

`site/procs.json.gz` — `{"kind":"procs","n":<payload rows>,"enc":"sparse","idxdelta":true,
"trk":[{key,item,name,buff,buff_name,window_ms}],"cols":{"lscore":{idx:u32Δ, r:u8 (255 =
no beam), a:u16 s, b:u16 s, p:u8}}}`, rows joined on `_gear_key` in df order, exactly like
stats.json.gz. No ladder: 10 B/row raw; at the ~41k wearer rows the 538k gear-known payload
rows imply (7.6%), ≈150-300 KB gz at full coverage — "tens of KB" holds only while coverage
is low. Fetched eagerly at boot (:1714) — acceptable beside a 4 MB payload; if
`build_health.txt` ever shows it past ~500 KB, switch the loader to fetch-on-first-toggle
(`loadBuildsSidecar` shape :3691-3706) — a 10-line change, not a format change.

Why no payload stub and no second file: the fetch IS the presence signal (`labMountLate`
:2547 mounts the card when the file lands), so a `D.light` stub would be a second data path
for one boolean; and folding the columns into `data.json.gz` would put a Lab feature on the
hot path and re-open the partitioned-payload bit-exact contract for ~40 KB of savings.

## 4. Client slicing under the knobs (as shipped)

Every number is `beamStats(rows)` over a row set the page already filters:
- per spec: `liveIdxFor(key)` (:3073-3106) = the chart's predicate — `baseMasks()`
  (class/spec/hero/dungeon/role/melee-ranged/region), key range and timed-only inside
  `rowPass`, period A via `periodPass`, `projSkip`, and the Archon branch (14-day window +
  per-group `floorK` from `FRAME_A`); then `lensSliceFor(key)` (:3305-3319) ranks by `dpsAt`
  and keeps `[pctl−10, pctl+10]`. Merge-hero flips `groupKey`; handled (:3659-3661).
- Character screen: `screenData(ctx).gearIdx` = lens window ∩ gear-known; covered rows are
  wearers by construction, so the identity line, tile and fold-out print the SAME figure.
- Time compare: period A only — say so (scopeBits already reads "follows every filter and
  the lens"; append "· period A under compare").

## 5. LAB manifest entry (exists; keep as is)
`{id:"beam", name:"✨ Lightspire Core · beam benefit", badge:"BEAM", mount:"labbox",
card:true, stamp:false, gate:()=>hasBeam, active:()=>hasBeam&&state.beam, controlHTML:
checkbox "Show beam benefit" + #beamtbl, scopeBits: on → "share of each Lightspire beam's
12 s its wearer spent inside it — on the Character screen's trinket tile and fold-out, and
per spec below; follows every filter and the lens" / off → "off — nothing added anywhere"}`.
`stamp:false` is honoured by `labStamp`/`labNoteBadges` (a readout, not a modifier — no
⚗ badges on sections). No `exemptions` needed: it modifies no section.

## 6. The corrections (the whole build is these; ordered by value)

C1 **Ship `i`, not `b`** — `build_site_data.py:2371`: `rec.get("b")` → `rec.get("i")`
   (keep the column key `b` so the committed decoder needs no change; fix the docstring
   :2323 "b":<u16 buff s> → "inside s"). Pooled can then never exceed 100%.
   test_procs.py:211: the fixture must carry `i` ≠ `b` on one row and assert `b` == `i`.
C2 **Call order in `render()`** — move `renderBeamTable()` (:6417) below `FRAME_A=A;`
   (or to the tail beside `renderFrame()` :6626): in Archon mode `liveIdxFor` reads
   `FRAME_A.groups.get(key).floorK` (:3078), so today the table is one render stale.
C3 **One pass, not one scan per spec** — `renderBeamTable` calls `lensSliceFor(k)` per key
   (:3657), each a full `liveIdxFor` scan: up to 40 specs × 751,687 rows ≈ 30M `rowPass`
   evaluations per `render()`, and `setPctl` (:2413-2422) calls `render()` on every slider
   input — a lens drag with the Lab on stutters. Fix: factor the ranking half of
   `lensSliceFor` into `lensWindowOf(idx)`, add `liveIdxAll()` = one pass over `N` with the
   same predicate (elite branch included, `floorK` looked up per row) bucketing indices by
   `groupKey`, and have the table do `for([k,idx] of liveIdxAll()) beamStats(lensWindowOf(idx).inWin)`.
   Cost becomes one scan + one sort of the view — what `aggregate()` already does each render.
   `lensSliceFor(key)` itself becomes `lensWindowOf(liveIdxFor(key))`, so the frame and the
   screen are untouched by construction.
C4 **`.gitignore`** — add `site/procs.json.gz` and `docs/procs.json.gz` beside the other
   sidecars (:31-40). No live risk (the daily commit uses targeted `git add`,
   refresh.yml:440-462); a local build otherwise leaves two untracked files.
C5 **Invalidate the pre-`sourceID` records (cheap, optional)** — run #827 journals ≤400
   wearer-fights with the `targetID`-only query (be9ab78). Contamination is bounded (≤0.5%
   of the backlog, and only if a teammate's beam can bless the wearer, which is unknown).
   Clean fix ≤400 pts: write `"q":2` on every new record (`run()` :329-331) and have
   `load_done` (:162-166) count only records with `q>=2`; `procs_sidecar` already keeps the
   LAST record per key (:2338). Skip if the point cost matters more than 0.5% purity.
Copy (decide once, no churn): the trinket tile now shows `n=312` in `.gmeta` and
`n=143` in the beam foot — two `n=` on one 40 px tile. Print the foot as
`beam benefit 41% · n=143 of 312` (`x.c` is in scope in `poolTile` :4767). The identity
line's "beam benefit 37 % n=89" is fine — its band states the population above it.

## 7. Degradation when absent (as shipped — verified paths)
- `procs.json.gz` 404 / no `DecompressionStream` / decode reject / `n !== N` (console.warn
  :3588) ⇒ `hasBeam=false` ⇒ `gate()` false ⇒ no card, no string "Lightspire" in the DOM,
  page byte-identical to before `2d2053f`. Reload resets `state.beam` (session-only, never in
  the URL hash — `csSyncHash` :3981 carries no beam token).
- File present but a spec has no covered rows in the window ⇒ table row absent; identity
  line prints `thin · n=k` under `BEAM_MIN_N=10`, never a number.
- Builder: journal missing / zero covered rows ⇒ `procs.json.gz` unlinked in both publish
  dirs, and the health channel says so (§8).

## 8. Health lines (`site/build_health.txt`)
Existing (be9ab78): `[season] procs sidecar: Lightspire Core -- {covered:,} of {wear:,}
wearer rows covered ({pct}%), {nob:,} with no beam` · `[season] procs sidecar: no
procs.jsonl journal; not shipped` · `[season] procs.json.gz not shipped (no journal or no
covered rows)`. That satisfies user_prefs #20 (the silencing case is on the channel).
Add, zero new mechanism: `fetch_procs.run()` appends `procs.lscore.total=`, `.done=`,
`.points=`, `.stopped=` to `data/processed/fetch_health.txt` (fetch_data rewrites that file
earlier in the same run and the builder folds every line as `fetch.<key>` :2891-2895), so the
collector's progress is readable without the Actions log. Tripwire for §1.1 re-entry: at
build time count bands that start inside a still-open window over the journal's `bands`;
`… , {k:,} bands inside an open window ({share}%)` — a share far above the ~22% rppm
expectation is the signal to move spawn detection to the 1263768 `cast` events.

## 9. Tests
- `scripts/test_procs.py` (exists, PASS): add the C1 assertion (`b` column == `i`), the C5
  `q` rule (an unversioned record is pending again; a `q:2` record is done).
- New `scripts/test_procs_roundtrip.py`, the `test_stats_sidecar_roundtrip.py` pattern
  (`extract_js` :46 lifts a top-level function; node required, "never a silent skip" :179):
  build a document with `procs_sidecar()` from a synthetic journal, decode it with the real
  `decodeProcsSidecar`, run the real `beamStats` — pin `map` (delta idx), 255 handling and
  `nb`, the interpolated p50 (`q()` :3623-3625), `pooled` == Σi/Σa, and rejection on
  `n !== N`. CI runs no test step (refresh.yml); run both locally before pushing.
- Manual: with the Lab on, drag the percentile lens at 1920×1080 — no visible stutter after
  C3; Archon on/off moves the table on the same render as the chart after C2.

## 10. Rollout — nothing here can break the live page
1. Already true: `be9ab78` builds the sidecar (first shipped by the build of run #827 or
   the next with ≥1 covered row); `2d2053f`'s UI deploys via deploy-site.yml within minutes
   and 404s on the sidecar until then ⇒ no card. Both orders are safe: the client
   feature-detects, the pipeline is inert to an old client.
2. One correction commit (C1-C5 + tests + this file). Value-only change to the sidecar (`b`
   slot carries `i`) ⇒ old and new client decode it identically. Push in the gap after a
   run's "Commit the export" step (user_prefs #21).
3. Verify live: `curl …/build_health.txt | grep procs`; hard reload; card present → toggle →
   table sorted by Benefit; Character screen → identity line → trinket tile when Lightspire
   is #1/#2 → fold-out row; Archon mode; lens drag.
4. Retirement = delete the manifest entry, the seven beam functions, the three surface call
   sites, the CSS block, the workflow step; the sidecar stops shipping. Zero residue.

## 11. Deliberately NOT built (the minimal line)
Spec Frame identity row (one `row()` in `frameIdentityHTML` :3132 later if asked — the
per-spec table already answers it a glance away); `D.light` payload stub; deciseconds /
status byte / `light.json.gz` rename; A·B compare of the ratio; death clipping; Casts-events
spawn detection; a Data Table column; any new mechanism for presence, loading or health.

Effort: ~1 day (C1-C5 half a day, tests half a day, live verification an hour).
