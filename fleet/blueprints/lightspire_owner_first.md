# BLUEPRINT — Lightspire Core "in the light" · OWNER-FIRST lens · 2026-09-08

Angle: optimise for the owner's DAILY read — put the number where their eye already goes,
render nothing that is dormant, and use the cleanest label. Written against the working tree
at 06:16 UTC (HEAD f678b6b; `site/index.html` 7,632 lines) and the two diagnostics' job logs
(diagnose.yml runs #4 and #5, both read today). Nothing existing was edited for this file.
Line numbers are today's; the function names hold.

## 0. Ground truth (verified, not assumed)

**Shipped.** be9ab78 (collector `scripts/fetch_procs.py`, `scripts/procs_spec.py`,
`build_site_data.procs_sidecar()` :2315-2391 → `site/procs.json.gz` written/unlinked
:3068-3083, refresh.yml step "Trinket beam benefit" :246-261, `scripts/test_procs.py`),
2d2053f (client: `state.beam` :1619, `hasBeam/BEAMC` :1628, manifest entry `beam` :2508-2519
with `stamp:false`, loader/decoder :3586-3629, `beamStats` :3631, `beamTitle` :3646,
`beamInline` :3658, `renderBeamTable` :3666, identity line `csBeamBandHTML` :4245, fold-out
`csBeamSub` :4516, tile foot in `poolTile` :4779-4789, CSS :1264-1275), f678b6b
(`liveIdxMulti` :3083 — one pass for every spec, 62 ms at 750k rows; `renderBeamTable()` last
in `render()` :6642, after `FRAME_A` is set). user_prefs #24 and checklist §S 158-163 record it.

**Live at 06:25Z — the first run found a fault, and the health channel caught it.** Run #827
(be9ab78) ran the collector 06:12:51-06:14:58Z (127 s) and its build deployed at 06:21:37Z:
`build_health.txt` now reads `built=2026-09-08T06:16:30Z` and
`[season] procs sidecar: Lightspire Core -- 154 of 46,662 wearer rows covered (0%), 154 with no
beam`; the shipped `procs.json.gz` (500 B) decodes to 154 rows, every `r` = 255, every `a`,
`b`, `p` = 0. The collector journaled 240 wearer-fights and all 240 came back with zero bands
from `table(dataType: Buffs, abilityID: 1263768, targetID: aid)` — the same shape diag pass 1
had used to read 23 and 27 bands. That is the systemic-fault signature (§3 tripwire), not data,
and the live client is correctly showing a card whose every spec reads "no beam" (counted, never
0 %). The working tree already carries the fix (uncommitted at 06:25Z, `scripts/fetch_procs.py`
rewritten): one `events(dataType: Buffs, abilityID: 1263768, targetID: aid, limit: 5000)` plus
`fights(fightIDs:[fid]){startTime endTime}` per wearer-fight, records stamped `"v": 2` with the
wearer's OWN spawn times `sp` and a foreign-apply count `x`; `load_done` and `procs_sidecar`
skip `v < 2`, so the 240 empty records are redone and never ship. Until the first v2 run lands,
the builder finds zero coverable rows and unlinks the file — the card disappears, nothing
dormant remains. Two byproducts worth keeping: the payload holds **46,662 wearer rows**
(6.2 % of 751,652), a measured figure the size estimates below now use; and if the v2 run ALSO
journals empties, the query is not the suspect — actor resolution is (masterData name/server
join for pre-09-08 gear rows, `resolve_actors`), because diag pass 2 read the same events with
ids 9 and 505 and got 45 and 54 of them.

**What the log records** (diag pass 2, two fights, 50 procs — job 101951035988):
- `events under 1263762 (any source): 0` on both fights. The area-trigger spell is never
  logged. The research brief's "cast of 1263762 = availability" is therefore not available;
  the pipeline uses the band start of 1263768 instead, which pass 2 proved coincides with the
  logged `cast` of 1263768 to the millisecond (`applybuff minus cast (ms)`: all 0 or −1; `casts
  with NO applybuff within 500 ms: 0`).
- Bands: 23 and 27; durations p50 **1.79 s / 1.62 s**, max 12.022 s / 12.036 s (one band per
  fight ~30 ms over 12.0 — logging jitter, not a longer beam); no `refreshbuff`.
- **Overlap is common**: `casts within 12 s of the previous`: 9/23 and 10/27 (37-39 %), far
  above the 22 % a memoryless 1.25 rppm predicts — so the window union is not a nicety, it
  moves the denominator by ~10 %.
- Reference ratios 41.4 % / 35.9 % against classic uptimes 8.2 % / 8.7 %. `applybuff sourceID
  == targetID` on every event.

**Who wears it** (diag pass 1 — job 101950096306, gear journal 4.05 GB, 967,729 records):
73,650 records wear 250214 (**7.61 %**); 90,657 wearer-fights with a players row; 27,155
distinct characters. By ISO week: W34 25,057 · W35 30,414 · W36 30,604 · W37 4,582 (partial,
Tuesday 05:39Z). By key band: <10 6,603 · 10-11 15,912 · 12-13 22,732 · 14-15 25,437 ·
16+ 19,973. Top specs: **Elemental 16,186 · Arcane 16,034 · Holy Paladin 15,325** ·
Restoration Shaman 4,700 · Beast Mastery 4,697 · Balance 4,368 · Holy Priest 3,360 ·
Demonology 3,327 · Subtlety 2,164 · Devourer 2,039 · Guardian 1,961 · Preservation 1,498.
The owner plays Arcane (user_prefs #8) — the second-largest wearer spec.

**Cost, measured so far.** Pass 2 (four `events` sub-queries in three requests) moved
`pointsSpentThisHour` 10,107 → 10,108: **1 point**. Pass 1 (per fight: one unfiltered Buffs
table, one events call, one Casts table) moved it 5,846 → 5,857: **11 points for two fights**,
i.e. ≤ ~2 pts per table alias. The collector's `est_cost=1.0` per alias (fetch_procs.py:245)
was an admission guess in that range; the events rewrite admits at `est_cost=0.5` per alias
(diag pass 2: eight event sub-queries for one point ⇒ ~0.125 pt each), and
`WCLClient.observe()` replaces the guess with the real figure after every response. Read the
first v2 run's `[procs] … P points` line before re-sizing anything.

## 1. The metric and the label

Per wearer-fight, ms from fight start, `F = fight_ms`:

```
spawns    = band starts s of 1263768 on the wearer (source = target = wearer)
available = ∪ [s, min(s + 12 000, F)]                      -- "the time the trinket was active"
inside    = |∪ bands ∩ available|                           -- "stayed in the buff"
r         = inside / |available|                            -- None when no spawn (no evidence)
```
(`fetch_procs.benefit()` :90-105 — `a`, `b`, `i`, `r`; tests test_procs.py:46-68.)

**Label: "in the light", value, then the denominator in words.** The game's own copy is
"Stand in the Light" / "Standing in the light blesses you" (item 250214 description and
spell 1250527 tooltip); the owner's sentence is "stay in the buff … percentage of when the buff
was available". So every surface reads **`in the light 38% of beam time`** — the denominator
travels with the number and it cannot be read as classic uptime. "beam benefit" (shipped) names
the effect, not the behaviour, and "benefit" invites "how much DPS"; it goes. "uptime" appears
nowhere except the disclaimer "Not classic uptime" inside the definition tooltip. Compact forms
drop the trailing words where a neighbour already states the population:

| surface | exact string (HTML as rendered) |
|---|---|
| Spec Frame Overview row (§2.1) | key `Lightspire Core` · value `in the light <b>38%</b> of beam time` · fnote `median · n=143 around p50 (lens ±10) · 2.1 h of beam` (+ ` · 12 got no beam` when `nb>0`) |
| Character screen identity line (`csBeamBandHTML` :4245) | `✨ Lightspire Core: in the light <b>38%</b> of beam time <i>n=143</i>` |
| pooled Trinket tile foot (`poolTile` :4788) | `in the light <b>38%</b> <i>n=143 of 312</i>` — `312` is `x.c`, the tile's own wearer count, so the tile no longer prints two bare `n=` |
| fold-out sub-line (`csBeamSub` :4519) | `in the light <b>38%</b> <i>n=143</i>` |
| Data Table column (§2.3) | header `In light @ p50` · cell `38%` or `–` |
| thin (n < 10), everywhere | `in the light <span class="na">thin · n=4</span>` |
| definition (`beamTitle` :3646, one function) | "Time in the light: of the seconds a Lightspire Core beam was up for these wearers, the share they stood inside it (Light's Blessing active). A beam lasts 12 s from where it spawns; overlapping beams count once. Median over N wearer-parses in this window (p25 … · p75 … · time-weighted …); K more got no beam that run and are not counted. Not classic uptime." |
| ⚗ card name / checkbox | `✨ Lightspire Core · in the light` / `Show time in the light` |

`beamInline()` and `beamTitle()` are the only two functions that print the words, so the relabel
is two string edits plus the card entry. No raw id anywhere (pref #11).

## 2. Where the owner's eye already goes — the surfaces, ranked

The daily read (prefs #8, #15, #18): the page opens on the Overview alone → the owner reads the
ranked bars for "is the meta moving" → clicks a bar → the **Spec Frame** docks (one click, the
place a spec is vetted) → for gear/talents, `Character screen →` (:1514). The sidebar is the
control rail; its **⚗ Lab group is the LAST group** (:1391-1394, after Scope · Trust Gate ·
When + Baseline · Cohort — ~14 control rows), and `state.beam` is reset to `false` on every
load (:1713) and is not in the URL hash. Two consequences drive this lens: the number must
live one click from the bars, and the toggle must not cost a sidebar scroll every morning.

### 2.1 PRIMARY (new): one row in the Spec Frame's Overview block
`frameIdentityHTML` :3149. Insert after the Deathless row (:3175-3178) and before the rating
row (:3179), through `row(k,v)` :3154 — the block is a transposed key/value ledger, exempt from
the sort rule (pref #12), and the 4pc standing row lived exactly here until #22 retired it
(specframe.md §5 precedent). Helper `frameBeamRow(ctx)` returns `""` unless
`hasBeam && state.beam && ctx.g`, and unless `beamStats(rows).n + nb > 0`:

```js
const idx=liveIdxFor(ctx.key);                       // the frame's own view (:3322)
const A=beamStats(lensWindow(idx).inWin);            // lens window = the screen's population
if(skillOn()){                                        // p50 · p85 — the owner's self-eval axis
  const B=beamStats(lensWindow(idx,state.pctlB).inWin);
  value = pct(A)+" · "+pct(B); fnote = "in the light, players around p"+state.pctl+" · p"+state.pctlB+" (lens ±10) · n="+n(A)+" · "+n(B);
}else{
  value = 'in the light <b>'+pct(A)+'</b> of beam time';
  fnote = "median · n="+fmtInt(A.n)+" around p"+state.pctl+" (lens ±10) · "+hours(A)+" of beam"
        +(A.nb?" · "+fmtInt(A.nb)+" got no beam":"")+(state.compare?" · period A":"");
}
```
`lensWindow(idx, pctl=state.pctl)` gains that one optional argument (:3323 reads `state.pctl`
for `lo/hi`; nothing else changes). Time compare prints A with "· period A" (the `perANote`
idiom) — no B slice: week-over-week in-light discipline is not a prediction read, and
threading `weeks` through `liveIdxFor` is a refactor this number does not earn. Archon
replica needs nothing: `liveIdxFor` already carries the elite branch. Thin ⇒ value
`<span class="na">thin · n=4</span>`, no fnote. `hours()` = `sa ≥ 3600 ? (sa/3600).toFixed(1)+" h"
: Math.round(sa/60)+" min"` over `sa` seconds. The frame rail is hidden while the Character
screen is open (`body.charscreen #frame-pos{display:none}` :507), so this row and the
screen's identity line never show at once — they are one reading in two places, never two.

### 2.2 SECONDARY (shipped — keep, relabel): the Character screen
Identity line (`csBandHTML` :4232-4234 → `csBeamBandHTML` :4245), the pooled Trinket tile foot
(:4785-4789; `.gfoot` already exists for the off-hand note :4776) and the fold-out sub-line
(:4516-4520, `csFoldTR` 7th arg). All read `beamStats(d.gearIdx)` — `screenData()` :4086 wraps
`frameLensSlice()`, so they print the frame row's exact figure. The owner asked for "the trinket
on the character screen"; this is that, without a hover surface (design §7; the tile `title`
carries only the definition sentence).

### 2.3 CROSS-SPEC (new, replaces the sidebar table): a Lab-gated Data Table column
The shipped per-spec table lives inside the ⚗ card (`#beamtbl` :2514, `renderBeamTable` :3666,
`table.beamtbl` CSS :1270-1275): OUTPUT in the 264 px control rail, at the bottom of the sidebar.
feedback_round2 keeps the sidebar as control groups and puts Lab output in main; the owner's own
cross-spec surface is the Data Table ("has all the details you may want", feedback_round2
Tables), one click open, every column sortable (pref #12). So: in `renderTable` :7471, in the
non-compare branch, after the spread column (:7499) and before Comps (:7504):

```js
if(hasBeam&&state.beam) cols.push(["light","In light @ p"+state.pctl]);
// get(): case "light": { const s=BM.get(r.key); return (s&&s.n>=BEAM_MIN_N)?s.p50:NaN }
// fmtCell(): if(k==="light") return isFinite(v)?Math.round(v)+"%":"–";
```
`BM = beamByKey()` — the loop lifted out of `renderBeamTable` :3669-3674 (`liveIdxMulti(keys)`
→ `lensWindow(idx).inWin` → `beamStats`), computed once per `render()` only while the Lab is on
(62 ms measured). `r.key` is `groupKey`, so merge-hero is handled. `–` (NaN) parks last in both
sort directions (pref #12). The header `title`: "median share of each Lightspire Core beam's
12 s its wearer stood inside it, for the players around pN (lens ±10); – below n=10". Under Time
or Skill compare the table is metric-only (:7478-7490) and the column is absent, like the rating
columns. Ordering invariant: `renderTable` runs after `FRAME_A=A` in `render()`, so the elite
branch of `liveIdxMulti` reads the current aggregate (f678b6b's fix, kept). Retire `#beamtbl`,
`renderBeamTable`, its CSS and the `render()` tail call (:6642); `wireLabControls` :2558 calls
`render()` on toggle (the frame and screen re-render through `renderFrame` :6641).
Acceptance: at 1920×1080 the wrap must not gain horizontal scroll it did not already have
(pref #10); if it does, the fallback is to keep the shipped sidebar table and not add the column.

### 2.4 The ⚗ card is a control with provenance, not a readout
`labcard` (:2535-2542) keeps: name, `off/active` chip, scope line, checkbox, and a hint in the
`projhint` slot (:1704-1705 pattern) read from the sidecar's `trk[0].cov` when present (§6):
`(Aug 19 – Sep 8 · measured on 31,877 of 73,650 wearer-parses)` — dates covered and sample size
where the owner likes them (pref #8), zero per-render cost. No table, no number.

### 2.5 The remembered toggle
`state.beam` is session-only, so today the daily read is: scroll the sidebar to its last group,
tick, then click a bar. Persist the choice per browser the way the page already persists two UI
preferences — collapsed sections (`SEC_KEY="wowlogs.collapsed.v2"` :1830, `readCollapsed` :1839,
pref #18) and the saved lens (`wowlogs.lens.me` :2401/:2441). `LAB_KEY="wowlogs.lab.v1"` holds
`{"beam":true}`; written in the checkbox handler; read in `loadProcsSidecar` :3595 AFTER
`hasBeam=true` and before `labMountLate("beam")`, followed by `render()` when it turned on.
Only a decoded sidecar can honour it — no data, no card, no state, nothing dormant. Retiring the
manifest entry leaves an ignored key. This is NOT the scrapped session-persistence item
(queue.md): that was filter/view state with reset buttons, and its trap was the data-driven key
range; a Lab switch changes no filter and no number. The owner should confirm (§13).

### 2.6 Zero dormant UI — the conditions, per surface
| surface | renders only when |
|---|---|
| ⚗ card | `procs.json.gz` decoded (`n === N`, all columns present) |
| frame row, identity line, tile foot, fold-out, column | card on AND the spec has ≥1 covered wearer-parse in the window; below n=10 the muted `thin · n=k` form |
| hint / scope line | `cov` present (else the hint is omitted; the scope line still says what is on) |
Nothing else on the page changes (`stamp:false` — `labStamp` :2591 and `labNoteBadges` :2603
skip it: a readout is not a modifier, feedback_round2 "Modifier visibility").

## 3. Data acquisition (the events collector now on disk; numbers to read, two fixes to carry)
- **Query** (`fetch_batch`, working tree): per wearer-fight one alias
  `report(code){ fights(fightIDs:[fid]){startTime endTime} ev: events(fightIDs:[fid],
  dataType: Buffs, abilityID: 1263768, targetID: aid, limit: 5000){data nextPageTimestamp} }`,
  12 aliases per request, `est_cost=0.5` each, cursor followed for at most three more pages
  (a 30-min fight has ~100 events). `bands_from_events()` opens a band on
  apply/refresh/applybuffstack, closes on removebuff, clips to the fight; a spawn is an
  apply/refresh whose `sourceID == aid` (the wearer's own beam); other sources are counted in
  `x`. So the wearer's OWN beams define availability and any beam's light counts as inside —
  the teammate question of the earlier lenses is answered by the data itself. Two wearers in
  one group stay separate by construction.
- **Which fights**: every gear record wearing 250214 in any slot (`candidates` :132-159, byte
  prefilter + exact id; slots 12/13 by convention, `TRINKET_SLOTS` build_site_data.py:829), minus
  journaled/failed (`load_done` :162-182), **newest first** (`order_pending` :191-196 — pref #15,
  the collector sits behind the summary stage in the same run). Pre-2026-09-08 gear rows carry no
  actor id → one `masterData.actors` alias per report, memoised per run (`resolve_actors`
  :200-225); new rows carry `actor` (fetch_data.py parse_summary).
- **Budget**: 400 pts / 240 s per run under the standing 0.70 ceiling (the step sets no
  `WCL_QUOTA_FRACTION`; `WCL_MAX_SLEEP_S=30` so a full window stops the step). At the chain's
  observed cadence today (04:49 → 06:01, ~4 runs/h) that is ≤ 1.6k pts/h = 13 % of the 12.6k/h
  ceiling **while a backlog exists**, and the client's `admit()` still refuses anything past the
  ceiling. Backlog: 73,650 wearer-fights + masterData for pre-09-08 reports ≈ 100k pts ≈ 5-6
  days; newest-first fills "this reset" (W37) first. Steady state ≈ 30k wearer-fights/week ≈
  4.3k pts/day ≈ 180 pts/h (1.4 %); most runs then find `pending 0` and spend nothing.
- **Backfill policy**: none beyond the standing budget — no drain, no cap waiver. Older resets
  fill in over the week; the frame row prints `thin` until they do.
- **Fix A — rollover-safe budget** (`run()` :286): `client.spent - spent0` goes negative when the
  hour resets mid-run and the point budget never trips. Count deltas:
  `used += max(0, client.spent - last); last = client.spent` after each request.
- **Fix B — durability**: `procs.jsonl` rides only the Actions cache (refresh.yml:173-179); an
  eviction restarts the backfill. Export derived fields (`report_code fight_id character server
  key actor f n a i r`, ~100 B/record ⇒ ~2 MB gz at 73k) to `data/procs_seed.jsonl.gz` each run
  and `git add` it in the Monday slot beside `gear.jsonl.gz` (:455-462); restore when the journal
  is absent. Loss bound: one week ≈ 30k pts.
- **Systemic tripwire**: after ≥ 20 fetched fights in a run, ≥ 50 % with zero bands ⇒
  `::warning::` and stop (fetch_data's `SYSTEMIC_SHARE/SYSTEMIC_MIN` idiom) — a broken filter
  must not journal a season of "no beam".

## 4. Interval computation — edge cases and decisions
| case | today | decision |
|---|---|---|
| buff already up when the window opens | impossible by construction: the window OPENS at the band start. The only variant is a band open at t0: `bands_from_table` clips to `max(0, s−t0)` (:122) and the window becomes `[0, 12 s]` — availability overstated by the pre-pull part, ≤ 12 s once per fight | accept (≤ 4 % on a 300 s denominator, worst case); count it in health |
| multiple windows (37-39 % of spawns land inside the previous 12 s) | `union()` :63-73 merges; bands union too; one intersection | keep — pinned by test :48-51 |
| leave and re-enter the same beam | the second band start reads as a second spawn, window extended by ≤ 12 s (ratio biased LOW) | accept; 0 of 50 procs showed it; the journal keeps the bands (:331) so a model change re-derives without refetch |
| death inside a beam | not clipped: the aura drops at death and the rest of the window counts as "out" | accept — literal reading of "did the player get its effect"; expected bias ≈ 0.4 % per death per parse, under the u8 percent quantisation; a `Deaths` alias (+~1 pt/fight) is the upgrade path |
| fight ends inside a beam | window and band both clipped to `F` (test :57-58) | keep |
| band 22-36 ms over 12.0 s (jitter) | numerator capped by the intersection (test :54-55) | keep; health counts bands > 12.5 s |
| zero beams | `r = None` → sidecar 255 → counted as `nb`, never a value (test :60) | keep |
| teammate's beam blesses the wearer | events carry the source: a foreign apply does not open a window (`sp` = own spawns only) but its light inside an own window counts as inside; `x` counts foreign applies per record | keep; health prints Σx so the first week says how often it happens |
| the first run's 240/240 empty tables | a Buffs TABLE query returned no bands for every fight; records were journaled as "no beam" | fixed by the events rewrite + `v` stamp; the in-run tripwire below makes the next such fault stop after 20 fights instead of 240 |

## 5. Journal + sidecar (v1 shape kept; one value fix; one additive block)
- `data/processed/procs.jsonl`: `{v:2, report_code, fight_id, character, server, key, actor, f,
  bands:[[s,e]…], sp:[spawn ms…], x:<foreign applies>, n, a, b, i, r}` (ms; `r` 4 dp or null).
  `v < 2` records (the table query) are neither "done" for the collector nor shipped by the
  builder. `procs_failed.txt` never retried.
- `site/procs.json.gz` (`procs_sidecar` :2315): `{kind:"procs", n, enc:"sparse", idxdelta:true,
  trk:[{key,item,name,buff,buff_name,window_ms}], cols:{lscore:{idx u32Δ, r u8 (255 = no beam),
  a u16 s, b u16 s, p u8}}}`, joined on `_gear_key` :347 in df order; client rejects `n ≠ N`.
  Size: 10 B/row raw; the payload holds 46,662 wearer rows (health line, run #827) ⇒ ≈470 KB raw,
  ≈200-350 KB gz at full coverage — "tens of KB" holds only while coverage is low. Fetched at
  boot (:1714); switch to fetch-on-first-toggle (the `loadBuildsSidecar` shape :3711) if
  `build_health.txt` ever shows it past ~500 KB.
- **Fix C1 — ship `i`, not `b`**: :2371 `rec.get("b")` → `rec.get("i")`, keep the column key
  `b` so the live decoder needs no change; fix the docstring :2325. The client pools `Σb/Σa` as
  "time-weighted" (:3636, :3642) and a band past its window would push it over 100 %.
- **Additive `cov`** on the `trk` entry: `{"wearers": wear, "measured": len(idx), "nobeam": nob,
  "dmin": …, "dmax": …}` (ISO dates of the earliest/latest covered payload row). Old clients
  ignore it; the new client's hint omits itself when it is absent.
- `.gitignore`: add `site/procs.json.gz`, `docs/procs.json.gz` beside :25-34.
  `deploy-site.yml:93` keep-list: add `procs.json.gz` (else a UI push after an older artifact
  can roll the doc back one build while `data.json.gz` stays new ⇒ `n` mismatch ⇒ card vanishes).

## 6. Client slicing under the knobs (by construction)
Every number is `beamStats(rows)` over a row set the page already filters: `liveIdxFor(key)`
= `rowPass` (key range, dungeon, region, role, melee/ranged, timed-only, post-tuning) +
`periodPass` (period A) + `projSkip`, elite branch for Archon; `lensWindow` = the ±10 percentile
band; `groupKey` = merge-hero. Frame row, identity line, tile, fold-out and column therefore read
ONE population and agree to the digit.

## 7. LAB manifest entry (`LAB_FEATURES` :2482; the shipped object, amended)
```js
{id:"beam", name:"✨ Lightspire Core · in the light", badge:"LIGHT", mount:"labbox",
 card:true, stamp:false,                       // a readout, not a modifier
 gate:()=>hasBeam,                             // sidecar decoded — else the card does not exist
 active:()=>hasBeam&&state.beam,
 controlHTML:'<label class="small" id="beambox"><input type="checkbox" id="beamcb"> '
   +'Show time in the light<span class="hint" id="beamhint"></span></label>',
 scopeBits:()=>state.beam
   ?"share of each beam's 12 s its wearer stood inside it — Spec Frame row, Character screen, "
    +"Data Table column · follows every filter and the lens"
   :"off — nothing added anywhere"}
```
No `exemptions` (it modifies no section). The `title` on `#beambox` carries the definition
sentence (the `tunebox.title` slot :1705).

## 8. Degradation when absent
404 / no `DecompressionStream` / decode reject / `n ≠ N` ⇒ `hasBeam=false` ⇒ `gate()` false ⇒
no card, no row, no column, no "Lightspire" string in the DOM, the stored preference ignored.
Sidecar present but no covered wearer in a spec's window ⇒ nothing for that spec. Builder:
journal missing or zero covered rows ⇒ file unlinked in both publish dirs and
`procs.json.gz not shipped` on the health channel (:3082).

## 9. Tests
- `scripts/test_procs.py` (extend): C1 (`b` column == `i`, fixture with `i ≠ b`); rollover
  budget (spent sequence 100,112,124,3,15 counts 36); `cov` block; seed export/restore
  round-trip; the systemic stop leaves the rest pending.
- New `scripts/test_procs_client.py` (node, `extract_js` pattern of
  `test_stats_sidecar_roundtrip.py`): decode a `procs_sidecar()` document with the real
  `decodeProcsSidecar`; `beamStats` p50/pooled/nb against a Python reference; `beamInline`
  contains "in the light" and never "uptime"; static: `renderTable(` follows `FRAME_A=A` in
  `render()`; the manifest entry has `stamp:false`.
- Manual at 1920×1080: card on → click Arcane → row present, fnote n; Skill compare → `p50 ·
  p85`; Archon on → figures move; Data Table → `In light @ p50` sorts both ways with `–` last
  and no new horizontal scroll; Character screen → identity line equals the row; reload → still
  on; 404 the sidecar → nothing anywhere.

## 10. Health lines (`build_health.txt`, prefs #20)
Existing: `[season] procs sidecar: Lightspire Core -- X of Y wearer rows covered (Z%), K with no
beam` (:2378) and `procs.json.gz not shipped …` (:3082). Add, one line each: `… pooled Σi/Σa
NN.N% · median per-parse NN% · bands > 12.5 s: k (share) · bands open at t0: k`; the collector
appends `procs.lscore.total/pending/ok/failed/transient/points/stopped` to
`data/processed/fetch_health.txt` (fetch_data rewrites it earlier in the same run, :792; the
builder folds every line as `fetch.<key>` :2891-2895) so coverage is readable without the job
log. `::warning::` on the systemic stop.

## 11. Rollout — nothing here can break the live page
0. In motion: the events rewrite of `fetch_procs.py` (+ the builder's `v < 2` skip) must be
   committed and pushed first; the next refresh redoes the 240 empty records and ships the
   first real sidecar. Verify on that run: `[procs] … +N journaled … P points` with N > 0, and
   the health line's "with no beam" well under half of covered — if it is not, stop and look at
   actor resolution (§0), not at the query. The live 2d2053f/f678b6b client needs nothing: it
   feature-detects the file and shows nothing while it is unlinked.
1. **Client commit** (§1 strings, §2.1 row, §2.3 column, §2.4 hint, §2.5 remembered toggle;
   remove the sidebar table). Reads today's v1 sidecar unchanged; deploys via deploy-site in
   minutes; every addition is behind `hasBeam && state.beam`. Amend user_prefs #24 and checklist
   §S 161-162 in the same commit (they describe the card table and the "beam benefit" string).
2. **Pipeline commit** (§3 fixes A/B and tripwire, §5 C1 + `cov`, §10 lines, §9 tests). Live at
   the next refresh; the client tolerates `cov` absent. Value-only change to the `b` slot ⇒ old
   and new clients decode identically.
3. **Ops commit** (.gitignore, deploy keep-list, Monday seed `git add`). Push in the gap after a
   run's "Commit the export" step (pref #21).
Each step reverts alone; a missing later step renders nothing rather than something wrong.

## 12. Deliberately not built
A tooltip number on the tile (design §7; the `title` carries the definition only); a fold-out
column; a Lab output SECTION; ⚗ stamps on scope lines; time-compare B for this number; death
clipping; the v2 status byte (pending/unavailable) — the frame fnote prints measured n and the
card hint prints coverage, which answers the daily question without a schema change.

## 13. Open questions (owner)
1. Remember the ⚗ switch per browser (§2.5)? Recommended; it is the difference between a
   one-click daily read and a scroll-and-tick every visit.
2. Sidebar table vs Data Table column (§2.3): this lens says column; if the owner liked the
   table beside its switch, keep it and skip the column — never both.
3. Read the first v2 run's `[procs]` line: if points/alias is materially above 0.5, only the
   backlog days change; the per-run cap stays.
4. If the v2 run also journals empties: actor resolution for pre-09-08 gear rows (masterData
   `name`/`server` vs the Summary's strings) is the suspect — compare one resolved id against
   the fight's `friendlyPlayers` before spending another 400 points.
5. Death clipping — literal reading kept; owner's call if they want positioning discipline
   separated from dying.
6. Test debt: `scripts/test_procs.py` pins the table-shaped collector (FakeClient regex on
   `table(fightIDs`, :147); it must be re-pointed at the events shape (`fights` + `ev`, `v:2`,
   `sp`, `x`, pagination) in the same commit as the rewrite, or CI-less pushes carry a red test.

Effort: ~1.5 days (client half a day, pipeline + tests half a day, ops + live walk half a day).
