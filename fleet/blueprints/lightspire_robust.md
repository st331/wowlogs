# BLUEPRINT — Lightspire Core beam benefit · ROBUST lens · 2026-09-08

Angle: correct under every knob (Archon replica and both compare axes included), N tracked
trinkets from day one, quota-safe backfill, and "unknown" that can never print as 0%.
Written against the working tree of 2026-09-08 (HEAD be9ab78 + the uncommitted client diff).
Line numbers marked *wt* are the working tree's and will drift; the function names hold.
Nothing existing was edited for this document.

## 0. What is already true (do not re-derive)

- **Metric** (owner): of the time the trinket's beam was available, the share the wearer
  spent inside it. Not classic uptime.
- **Log signature, measured** (`scripts/diag_lightspire.py`, `diag_lightspire2.py`, 50 procs on
  two fights): the area-trigger spell 1263762 "Radiant Light" is **never logged** (0 events,
  any source). 1263768 "Light's Blessing" is applied to the wearer at the instant a beam
  spawns (cast and applybuff coincide 50/50 to the ms), no `refreshbuff`, bands 0.1–12.0 s,
  hard-capped at 12.0. So a band's **start is a spawn**; the beam stays available 12,000 ms
  (SpellDuration 29). Reference fights: 41.4 % (MM, 23 beams) and 35.9 % (Arcane, 27) against
  classic uptimes of 8.2 % / 8.7 %.
- **Shipped in HEAD**: `scripts/fetch_procs.py` (model `benefit()` :90, `bands_from_table()`
  :108, `candidates()` :132 byte-prefilter over gear.jsonl, `load_done()` :162,
  `fetch_batch()` :232 — one `table(dataType: Buffs, abilityID: 1263768, sourceID: aid,
  targetID: aid)` alias per wearer-fight, 12 per request, `est_cost=1.0`; `run()` :249 with
  `--budget-pts 400 --budget-s 240`, newest first :191); `scripts/procs_spec.py:11 TRACKED`;
  `fetch_data.py:643 "actor": p.get("id")` on every new gear record; journal
  `data/processed/procs.jsonl` (bands kept) + `procs_failed.txt`;
  `build_site_data.py:2315 procs_sidecar()` → `site/procs.json.gz` (cols `idx r a b p`, r u8
  percent with 255 = no beam, a/b u16 seconds), written/unlinked at :3069-3083;
  `refresh.yml:246-261` step (never fails the run); `scripts/test_procs.py`.
- **In flight (uncommitted, another implementer)**: `site/index.html` *wt* — `state.beam`
  :1616, `hasBeam/BEAMC` :1626, `loadProcsSidecar()` from `initData` :1712, manifest entry
  `id:"beam"` :2498-2517 with `stamp:false` honoured at :2590/:2601, loader/decoder/
  `beamStats`/`renderBeamTable` :3566-3712, fold-out sub-line `csBeamSub` :4483, tile foot in
  `poolTile` :4747-4758, `renderBeamTable()` called from `render()` :6401.
- **Sizing** (diag part 1 + fetch_procs docstring): ~73k wearer-fights on 2026-09-08 =
  **7.6 % of gear-known parses**, growing ~30k/week; ≈36.7k wearer rows inside the payload's
  482,935 gear-known rows.

## 1. The interval model, hardened (collector; `fetch_procs.benefit`, :90)

Per wearer-fight, ms from fight start, `F = [0, fight_ms]`, bands `[s,e]` from the Buffs table:

| case | rule | why |
|---|---|---|
| ordinary band | window `[s, min(s+12000, F)]`; benefit `= band ∩ window` | spawn = band start (§0) |
| overlapping beams | windows **union**, bands union, intersect once | "time the trinket was active" counts once; `benefit()` already does this (test :48-51) |
| band outlives its window | numerator capped by the intersection (already); **count `o`** when `e−s > 12,500` | model guard; a season-wide `o` share > 1 % means beams outlive 12 s and the definition returns here |
| **band starts at t0** (`s <= 0`) | **exclude from numerator AND denominator**, count `x` | the aura was up before the pull, the spawn is unknown; treating t0 as a spawn would print ≈100 % for a beam we never saw. NEW — v1 counts it as a spawn |
| fight ends inside a beam | window clipped to `F` (already, test :57) | the beam was not available past the log |
| band open at fight end | `bands_from_table` clips to `endTime` (already, :122) | — |
| wearer died inside a beam | **not clipped** in v1: the remaining window counts as time outside | literal reading of "did the player get its effect"; the bias is ≤ 12 s per death, expected ≈ (Σavail/fight) × 6 s ≈ 1.5 s ≈ 0.4 % per death. v2 hook: a `Deaths` table alias (+~1 pt/fight) clips at the death timestamp; the journal keeps bands so it re-derives without refetch |
| no band at all | `r = None`, `n = 0` — **no evidence**, never 0 % (already, test :60) | a wearer whose beam never fired is a count, not a value |
| second spawn while already blessed | invisible to bands (no new applybuff, no refresh observed) — availability under-counted, numerator likewise; bounded by P(spawn within a ~3.7 s stay) ≈ 7 %/proc | **tripwire** §9: sampled `Casts` table count vs band count |
| teammate's beam blessing the wearer | excluded by `sourceID = targetID = aid` (:241-243) | verified only on single-wearer fights; the same sampled tripwire (casts by wearer < bands on wearer ⇒ foreign blessings) covers it |

Journal record gains `"x"` and `"o"`; `"i"` (inside ∩ available) already exists and is the
only numerator any consumer may use — `b` (raw buff ms) is diagnostic. Prototype with
assertions: `scratchpad/procs_robust_model.py` (t0 exclusion, guard counter, v1 arithmetic
unchanged).

## 2. Data acquisition (keep the Buffs-table plan; fix quota accounting; add durability)

- **Which fights**: every `(report, fight, character)` whose gear record carries a tracked
  item id in any slot (`candidates()`), newest first by the sweep's `start_time`
  (pref #15), across every tracked trinket in `TRACKED` order. No key floor — the payload
  itself starts at +2 and the knobs decide.
- **Query**: unchanged (`fetch_batch` :241). Cost measured ≈ 1 pt/alias; pre-2026-09-08
  gear records lack `actor` and cost one `masterData` alias per REPORT (memoised per run,
  `resolve_actors` :200). `est_cost` stays an admission guess; the governor replaces it with
  `rateLimitData` after every response (`wcl_client.py:114 observe`, :263).
- **Per-run cap**: 400 pts / 240 s (`refresh.yml:260`). 48 runs/day ⇒ ≤ 19.2k pts/day =
  **6.3 % of the 70 % ceiling** (12,600/h × 24). Backlog ≈ 73k × ~2 pts ≈ 146k pts ≈ **7.6
  days**; newest-first fills the current reset within a day. Steady state ≈ 4.3k wearer-
  fights/day × 1 pt ≈ 4.3k pts/day = **1.4 % of the ceiling**, ~90 pts/run.
- **FIX — rollover-safe accounting** (`run()` :286): `client.spent` is the account's
  `pointsSpentThisHour`; when the hour rolls over inside a run (`_sleep_for_reset` →
  `rolled_over()` :125, or a real reset) `client.spent - spent0` goes negative and the point
  budget never trips (only the 240 s cap bounds it). Replace with a delta sum:
  `used += max(0, client.spent - last); last = client.spent` after every request
  (prototype: 48 pts counted where the naive form read −97).
- **Quota safety by construction**: the step inherits the standing 0.70 fraction
  (`WCL_QUOTA_FRACTION` unset ⇒ `DEFAULT_QUOTA_FRACTION`), so in a drain/backfill run whose
  Fetch spent 100 %/80 % the probe (`_probe_quota` :185) sees spent ≥ ceiling and
  `QuotaDeadline` stops it under the 30 s sleep cap — it cannot eat the next run's summary
  budget. Add `if: inputs.regear_min_key == ''` on the step (a regear run is the one path that
  sits through the reset; 4 minutes of collector there buys nothing).
- **Systemic tripwire in-run**: after ≥ 20 fetched wearer-fights, if ≥ 50 % have zero bands,
  print `::warning::procs: <name> -- N of M fetched fights have no bands; spell id / table
  shape fault suspected; stopping` and stop — never journal a season of "no beam" from a
  broken filter (mirror of `fetch_data.py:705 SYSTEMIC_SHARE/SYSTEMIC_MIN`). Add
  `--release-nobeam` (drops `n == 0` records from the journal for refetch, the
  `--release-failed` idiom :1382).
- **Durability**: `procs.jsonl` is cache-only; an eviction (refresh.yml:173-179 explains how)
  costs the whole backfill. Add `fetch_procs.py --export` → `data/procs_seed.jsonl.gz`,
  derived fields only (`report_code fight_id character server key actor f n a b i r x`, ~110
  B/rec ⇒ ~2 MB gz today, +0.5 MB gz/week), written each run after `run()`, committed in the
  **Monday** slot beside `gear.jsonl.gz` (refresh.yml:457-464: `git add
  data/procs_seed.jsonl.gz`), restored when `procs.jsonl` is absent (the `restore_checkpoints`
  gear pattern, `fetch_data.py:177-183`). Loss bound: one week ≈ 30k pts. Bands are not in
  the seed; the builder never needs them.
- **Per-run health file** `data/processed/procs_health.txt` (`procs.<key>.total/pending/ok/
  failed/transient/points/stopped/nobeam_share`), folded by the builder as `fetch.procs.*`
  exactly like `fetch_health.txt` at `build_site_data.py:2891-2895`.

## 3. Sidecar contract v2 — `site/procs.json.gz` (builder `procs_sidecar`, :2315)

Ship it **before** the client commit (nothing live reads v1; the in-flight client must read
v2 only — one decoder, no shim).

```jsonc
{"kind":"procs","v":2,"n":<payload rows>,"enc":"sparse","idxdelta":true,
 "trk":[{"key":"lscore","item":250214,"name":"Lightspire Core","buff":1263768,
         "buff_name":"Light's Blessing","window_ms":12000,"model":"band_start",
         "cov":{"wearers":36703,"measured":31877,"nobeam":412,"pending":4400,
                "unavail":14,"dmin":"2026-08-19","dmax":"2026-09-08"}}],
 "cols":{"lscore":{
   "idx":"<b64 LE u32, delta-coded — EVERY wearer row of this item, payload order>",
   "st":"<u8: 0 pending · 1 measured · 2 unavailable>",
   "a":"<LE u16 deciseconds available (union of windows)>",
   "i":"<LE u16 deciseconds inside ∩ available>",
   "p":"<u8 beams>"}}}
```

- **Covered rows = all wearers**, not only measured ones: wearer = `meta[key]["gear"]` lists
  the item (`gj.meta` is built for every payload code, `gear_journal_pass(codes)` :2447 —
  not windowed, unlike the builds vocab whose 40-cap folds an unpopular trinket into
  "other"). `st=0` when no journal record, `st=2` when `procs_failed.txt` names
  `(code,fid,character)` for this key (the builder must read the failed file; today it does
  not), `st=1` otherwise. This is what makes "n measured of n wearers" exact under any knob
  and "pending" distinguishable from "no beam" — v1 cannot tell a wearer awaiting fetch from
  a non-wearer.
- **`i` replaces `b`** (v1 ships raw buff seconds; a band past the window makes Σb/Σa > 100 %;
  `i` is capped by construction). **`r` dropped**: the client derives `i/a` from deciseconds
  (error ≤ 0.05/12 = 0.4 % on a single-beam parse; whole seconds would be ±8 %). u16 ds caps
  at 6,553 s > p90 fight 1,722 s; clamp and count anyway.
- **N trinkets**: one `cols[key]` per `TRACKED` entry with its own idx; a row wearing two
  tracked trinkets appears in both. `model` is carried so a future trinket whose window is a
  cast (not a band start) is one dispatch branch in the collector, not a fork.
- Size: 36.7k rows × 11 B raw ≈ 404 KB ⇒ **≈0.18–0.28 MB gz**; no ladder, one 3 MB cap with a
  `::error:: OMITTED` line (pref #20a).
- `.gitignore`: add `site/procs.json.gz`, `docs/procs.json.gz` (stats/builds/talents are
  listed; procs is not — an accidental commit would overlay a stale doc on deploy).
  `deploy-site.yml:93-96` keep-list: add `procs.json.gz`, or a UI push after an older
  artifact rolls the doc one build back while data.json.gz stays new ⇒ `n` mismatch ⇒ the
  client rejects (safe, but the card silently vanishes).

## 4. Client — one decoder, one aggregator, one slice pass

- `decodeProcsSidecar` (*wt* :3583): require `v===2`, `n>>>0===N`, and for EVERY `trk`
  entry `cols[key].{idx,st,a,i,p}` of consistent length (any short column ⇒ `null` ⇒
  `hasBeam=false`). Result `BEAMC={list:[{key,item,name,buff,window,cov,map,st,a,i,p}],
  byItem:Map(item→entry)}`. Drop the `BEAM_KEY="lscore"` constant; surfaces resolve by
  `BEAMC.byItem.get(+e.id)`.
- `beamStats(T, rows)` — the ONLY arithmetic: per row `k=T.map[i]`; `k<0` ⇒ not a wearer;
  `st===0` ⇒ `pending++`; `st===2` ⇒ `unavail++`; `st===1 && a===0` ⇒ `noBeam++`; else
  `sa+=a; si+=i; if(a>=BEAM_MIN_A) v.push(i/a)`. Returns `{wearers, measured, pending,
  unavail, noBeam, n:v.length, p25,p50,p75 (qp :2627), pooled:sa?si/sa:null, minutes:sa/600,
  thin:n<BEAM_MIN_N||sa<BEAM_MIN_SUM_A}`. Constants: `BEAM_MIN_A=120` ds (one full window
  before a parse votes), `BEAM_MIN_N=10` (CS_THIN echo, :3936), `BEAM_MIN_SUM_A=6000` ds
  (10 min of light behind any printed number). **Unknown never prints as 0 %**: pending,
  unavailable and no-beam are counts; a number renders only when `!thin`.
- **Knobs, by construction**: every surface passes rows from `lensSliceFor(key, …).inWin`
  (*wt* :3305) — key range, dungeon, region, role, timed-only, period, projection through
  `liveIdxFor` :3082 (`rowPass`/`periodPass`/`projSkip`), merge-hero through `groupKey`, the
  percentile lens through `inWin`, **Archon replica through the elite branch** :3086-3100.
- **BUG to fix**: `render()` calls `renderBeamTable()` at *wt* :6401, before `FRAME_A=A;
  FRAME_B=B;` at :6407. The elite branch reads `FRAME_A.groups.get(key).floorK`, so under
  the replica the card table is one render stale and empty on the first render after
  toggling. Move the call to the end of `render()`, immediately before `renderFrame()`
  (:6610), where FRAME_A/B are current.
- **Cost**: `renderBeamTable` runs `lensSliceFor(k)` per spec with wearers (≈40 keys) — 40
  full N-row passes per render, on every slider tick while the card is on. Refactor
  `liveIdxFor` into a predicate + loop and add `liveIdxByKeys(keys, weeks)` (ONE pass
  bucketing by `groupKey`), then rank per bucket; the table costs one pass plus sorts.
- **Compare — real, not a caption**: `lensSliceFor(key, weeks=state.weeksA,
  pctl=state.pctl)` gains the two optional arguments (B ranks within its own view, as the
  ghost bars do). Time compare: B = `lensSliceFor(k, state.weeksB)`. Skill compare: the
  ratio is a per-parse distribution, so it honestly applies — B =
  `lensSliceFor(k, state.weeksA, state.pctlB)`. Both render in the card table as
  `A · B · Δ` (Δ in **percentage points**, `+4 pp`, `--up/--down`, `–` when B is thin; parked
  last by the NaN rule). The Character screen stays period A like the rest of the screen
  (`perANote` :6998 already says "· period A").

## 5. LAB manifest entry (`LAB_FEATURES`, *wt* :2498 — keep, amend)

```js
{id:"beam", name:"✨ Lightspire Core · beam benefit", badge:"BEAM", mount:"labbox",
 card:true, stamp:false,                       // a readout, not a modifier: no ⚗ stamps anywhere
 gate:()=>hasBeam,                             // sidecar decoded (v2) — else the card does not exist
 active:()=>hasBeam&&state.beam,
 controlHTML:'<label class="small" id="beambox" title="'+BEAM_DEF+'">'
   +'<input type="checkbox" id="beamcb"> Show beam benefit'
   +'<span class="hint" id="beamhint"></span></label><div id="beamtbl"></div>',
 scopeBits:()=>state.beam
   ?"share of each beam's 12 s its wearer spent inside it · on the Character screen's "
    +"trinket tile and fold-out, and per spec below · follows every filter, the lens and compare"
   :"off — nothing added anywhere",
 exemptions:{}}                                // it changes no section, so none to declare
```
`#beamhint` from `cov` (exists before any toggle): `(Aug 19 – Sep 8 · beam data on 31,877 of
36,703 wearer-parses · 4,400 pending)`. `BEAM_DEF` (title, the `archonTitle()` slot): *"Of the
seconds a wearer's own Lightspire beam was on the ground (12 s per proc, overlaps counted
once), the share they spent inside it — Light's Blessing active. Not classic uptime. A run
with no beam has no ratio and is counted separately; a wearer not yet fetched is 'pending',
never 0 %. Headline = median over parses; the tooltip adds the time-weighted share.
SimulationCraft assumes 50 %."* With a second `TRACKED` entry the name becomes
"✨ Trinket beam benefit" and the table gains a trinket group row; nothing else changes.

## 6. Surfaces and exact strings (population = lens window everywhere)

1. **Character screen, pooled Trinket tile** (`poolTile` *wt* :4747): when the tile's item is
   tracked and the Lab is on, one `.gfoot.beam` line:
   `beam benefit <b>41%</b> <i>n=143</i>`; with pending: `<i>n=143 · 12 pending</i>`;
   thin: `beam benefit <span class="na">thin · n=4</span>`; nothing measured:
   `beam benefit <span class="na">pending · 12 wearers</span>` /
   `<span class="na">reports unavailable</span>`; wearers = 0 ⇒ no line. Tile `title` carries
   the definition + `p25 … · p75 … · time-weighted … · 41 min of light · K no beam`.
2. **Fold-out row** (`csBeamSub` :4483): the same `beamInline` string in the row's `.fsub`.
3. **Lab card table** (`renderBeamTable`): columns `Spec | Benefit | n` (+ `B | Δ` under
   compare, header `p50 | p85 | Δ` under skill compare, `A | B | Δ` under time compare), all
   sortable via `sortState/sortHead/sortRows/wireSort` :6341-6373; row `title` = definition;
   footer `median of per-parse beam benefit · players around p50 (lens ±10)` + `· period A`
   / `· ⚔ Archon replica` as `frameScope()` prints; empty:
   `no spec has 10+ wearer-parses with beam data in the current filters (3 below the floor)`.

## 7. Degradation when absent

Missing/404/`v≠2`/`n≠N`/short column ⇒ `hasBeam=false` ⇒ `gate()` false ⇒ no card, no line,
no string "Lightspire" in the DOM; `state.beam` forced false. Journal absent ⇒ builder unlinks
the file and writes `procs.json.gz not shipped`. Sidecar present but a spec has only
pending rows ⇒ counts, never a percentage. Nothing dormant, ever.

## 8. Tests

- `scripts/test_procs.py` (extend): t0-band exclusion (`x`), `o` guard, `i ≤ a` under a
  20 s band, rollover-safe budget (spent sequence 100,112,124,3,15 counts 36), two `TRACKED`
  entries with a row wearing both, failed file ⇒ `st=2`, wearer without record ⇒ `st=0`,
  measured `a=0` ⇒ `st=1`, ds rounding + 65535 clamp, `cov` block, `v:2` and no `r/b`,
  seed export/restore round-trip, in-run systemic stop after 20 zero-band fights leaves the
  rest pending, `--release-nobeam`, static tripwires (`.gitignore` names
  `site/procs.json.gz`; deploy-site keep-list names `procs.json.gz`).
- `scripts/test_procs_roundtrip.py` (node; lift `decodeProcsSidecar` + `beamStats` with
  `extract_js`, `test_stats_sidecar_roundtrip.py:44`): decoded `st/a/i/p` equal the inputs
  row for row; `beamStats` on fixed row lists matches a Python reference (`qp` median,
  pending/unavail never enter, thin floors); static: in `render()` the text
  `renderBeamTable()` appears after `FRAME_A=A`.

## 9. Health lines (`build_health.txt`, one per tracked trinket)

```
[season] procs sidecar: Lightspire Core -- 36,703 wearer rows in payload (7.6% of gear-known);
  measured 31,877 (87%), no beam 412, pending 4,400, unavailable 14; newest reset 2,918 of 4,102 (71%)
[season] procs sidecar: Lightspire Core -- pooled Σinside/Σavailable 39.2%; bands over 12.5 s 0.1%;
  pre-existing bands excluded 37; a>dur clamps 0
[season] procs.json.gz SHIPPED 0.21 MB gz (cap 3.0)   |  ::error:: OMITTED when over the cap
fetch.procs.lscore.ok=… .pending=… .failed=… .points=… .stopped=… .nobeam_share=…
```
`::warning::` when measured ≥ 20 and no-beam share ≥ 50 %, or `o` share > 1 %.
**Sampled tripwire** (v1.1): every 50th wearer-fight adds `table(dataType: Casts, sourceID:
aid, abilityID: 1263768)` (+~1 pt, ≤ 20 pts/run); journal `c` = cast count; health prints
`casts>bands in 3.1% of sampled fights` (hidden overlapping spawns) and `casts<bands in
0.0%` (foreign blessings). Either above 5 % reopens §1.

## 10. Rollout (never breaks the live site)

1. **Pipeline commit**: collector hardening (§1, §2 fixes), sidecar v2 (§3), tests, health
   lines. Inert: the live client ignores the file. Verify `build_health.txt` after one run.
2. **Ops commit**: `.gitignore`, `deploy-site.yml` keep-list, `refresh.yml` (`if:`, `--export`,
   Monday `git add data/procs_seed.jsonl.gz`). Inert.
3. **Client commit** (the in-flight diff, amended to v2 + §4/§5/§6). `deploy-site.yml`
   overlays the UI on the latest refresh artifact, which already carries v2 ⇒ `n` matches.
   Verify: card present; toggle; Archon on ⇒ table populated on the FIRST render; time and
   skill compare columns; simulated 404 ⇒ no card; `docs/`/`site/` show no tracked procs file.
4. **v1.1**: sampled cast-count tripwire after a week of data.
Each step reverts alone; the gate guarantees a missing later step renders nothing.

## 11. Open questions

1. Overlapping spawn while blessed (invisible to bands) — bounded ≈ 7 %/proc; settle with the
   sampled cast count.
2. Foreign blessing source id — is `sourceID` the beam owner when a teammate's beam blesses
   the wearer? Only a 2-wearer fight answers; same tripwire.
3. Real Buffs-table cost per alias (est 1.0) — read `rateLimitData` deltas on a single-alias
   request on day 1; raise `est_cost` if > 1.5.
4. Death clipping (v2) — owner's call between the literal reading and positioning discipline.
5. A band present at t0 in a live Buffs table — confirm WCL reports `startTime == fight
   start` for a pre-pull aura before trusting the `x` counter.
