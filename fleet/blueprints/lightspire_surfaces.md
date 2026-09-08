# BLUEPRINT — Lightspire Core in-light share · UI SURFACES lens · 2026-09-08

Scope of this document: WHERE the number lives, what it says, how it degrades, and how the
Lab gate makes it vanish. It names the spells and the per-row contract only as far as the
surfaces need them; collection cost and the WCL query plan are another lens's job.
Nothing in `site/index.html`, `scripts/*` or any existing file was edited for this.
Every `site/index.html:LINE` below was read against the working tree today (7,397 lines).

## 0. The metric, stated once (owner's clarification governs)

Not classic uptime. Per wearer-parse:

```
A  = union of [t, t + 12,000 ms) over every cast of 1263762 "Radiant Light" by the player,
     clipped to the fight                                  -- "the time the trinket was up"
I  = total length of  A ∩ {bands of 1263768 "Light's Blessing" on the same player}
ratio = I / A                                              -- "of that time, how much in the light"
```

Aggregated for a spec under the current filters, two readings, both shipped by the one
aggregator (`beamStats`, lightspire_ratio_pipeline.md §4.2): the **headline is the per-parse
MEDIAN** of `I / A` — one vote per parse, "the player" of the owner's sentence, immune to a
single 40-minute key — and the **pooled `Σ I / Σ A`** (time-weighted, the literal "percentage
of the time") prints beside it in the footnote. Rows with `A = 0` (a wearer whose beam never
appeared) are counted, printed, and excluded from both. Unknown is never 0%: a pending or
unavailable row is a count, never a value.

Only the INTERSECTION goes in the numerator. That is load-bearing, not pedantry: if a
teammate's beam can bless the wearer (unknown, §7), or if a band outlives the 12 s window by
server timing, a naive `Σ bands / Σ A` can exceed 100%. `A ∩ bands` cannot.

**Spell chain (verified 2026-09-08 on wago.tools db2 CSVs + wowhead tooltip JSON + simc):**

| id | name | what it is | evidence |
|---|---|---|---|
| item 250214 | Lightspire Core | the trinket, quality 3 | `data/names_items.json` key `"250214"` = `{"n":"Lightspire Core","q":3}`; icon `inv_enchant_essenceastrallarge` in `data/names_icons.json`; `site/icons/i250214.jpg` exists |
| 1250527 | Lightspire Core | driver + passive: effect 0 = Proc Trigger Spell → 1263762; effect 1 = aura 189 Mod Rating, misc0 33554432 (Mastery mask); effect 2 = dummy (coef 0.6985, the in-light amount) | `SpellEffect?filter[SpellID]=exact:1250527` rows 1254622 / 1275854 / 1275873; `ItemEffect` 214265 TriggerType 1 (on equip); wowhead tooltip: "Approximately 1.25 procs per minute" |
| 1263762 | Radiant Light | the BEAM: effect 179 Create Area Trigger (misc0 40323), DurationIndex 29 → **12,000 ms**, 100 yd, instant; the player is the caster | `SpellEffect` row 1274640; `SpellMisc` 816696 DurationIndex 29; `SpellDuration` 29 = 12000 |
| 1263768 | Light's Blessing | the in-light buff: aura 189 Mod Rating, misc0 33554432 (Mastery); DurationIndex **21 = −1**, i.e. NO timed duration — held while inside the trigger, dropped on exit | `SpellEffect` for 1263768; `SpellMisc` DurationIndex 21; `SpellDuration` 21 = −1 |

simc (`engine/player/unique_gear_midnight.cpp:2690-2711` at c1935b9) uses exactly these three
ids, models the buff as `duration(1263762) × lightspire_core_duration_multiplier`, and that
multiplier defaults to **0.5** in `player.hpp` under `// TODO: Emulate not standing in the
light`. In other words the simulator assumes a 50% in-light share and says so; the owner's
metric measures the real number. Worth printing beside the field-wide figure once (§4.3).

## 1. The rules the surface must obey (quoted, so the trade-offs below are checkable)

- **No cursor tooltips as a data surface.** user_prefs #7: "No cursor-chasing tooltips";
  feedback_round2 removal #2: "the tooltip is enormous - that needs to go. No floating
  detail surface at all — details live in the tables"; design_language §7: "`title=""` on
  tiny metadata (week-chip dates/volume) is the only browser-native exception; all data
  detail goes to the inspector rail". upgrade_surface §2.7 on the lean footnote: "a
  rendered line, not a tooltip".
- **The wowhead tooltip is not ours.** user_prefs #11: "show the wowhead tooltip on
  hovering on the icon, and that's it"; it is wowhead's `tooltips.js` (`site/index.html:7395`)
  attached to `a.whico` (`:3714`). It cannot carry our number, and nothing else on the
  tile may open a tooltip.
- **Nothing dormant.** feedback_round2 removal #3 and the manifest header
  (`:2458-2465`): "gate() false ⇒ NOTHING renders — no dormant rows, no placeholders";
  "BIRTH RULE: adding a loose checkbox to Scope/Cohort/When/Trust is forbidden — every new
  transient enters through this manifest".
- **Every table sorts on every column** (user_prefs #12), "Exempt only: transposed
  key/value blocks with no column axis (the hover tip, the frame identity block)". A
  column whose value exists for one row and is `–` for the other nine is technically
  sortable and practically clutter; the fold-out's own comment (`:4226-4228`): "a column
  that does not apply in the current state is ABSENT from cols entirely (3.2.4): not
  blank, not a dash".
- **Sample sizes and dates visible** (user_prefs #8 "Likes visible dates covered, run
  counts, groups, sample sizes"; design_language §15.17 captions state scope).
- **The pane may not grow.** upgrade_surface §2.6: "the pane's bounding box does not grow
  by a pixel at ≥1151px"; tiles are "Unchanged in size, type and content — 40px icon,
  name, `<b>share%</b>`, `<i>n=…</i>`".
- **A fallback that cannot follow the filters says so ABOVE its numbers and withholds them
  when the live view is empty** (user_prefs #20).
- **Modifier badges are for modifiers.** feedback_round2 "Modifier visibility": "I like
  how it tells me when modifiers are active like the 4pc filter … Active modifiers only".
  `labStamp` (`:2535`) and `labNoteBadges` (`:2546`) stamp EVERY active entry into every
  section; a readout that changes no number elsewhere must not.
- **Precedent for exactly this shape:** specframe.md §5 "4pc standing (graft, both judges):
  one row, rendered only when `hasTier && labHas("tier4pc")` … Retirement of the tier4pc
  manifest entry ⇒ `labHas` false ⇒ the row simply never renders — zero residue (Lab-gated,
  the owner's 'is it gear or me' question at the exact moment of vetting a spec)". That row
  shipped, lived in `frameIdentityHTML`, and was deleted in one commit (user_prefs #22).

## 2. Candidate surfaces, enumerated

"Knobs free" = the number re-slices under key range, period, dungeon, region, role,
timed-only, merge-hero, Archon replica and projection with no new wiring because it is
computed from a row set those already filter. Two row sets exist today and they are NOT
the same population:

- **the view** — `frameLiveIdx()` `:3020-3052`: the chart's own row-pass for the framed
  group, elite branch included, "everything EXCEPT the min-characters trust gate". This is
  the population behind every number in the identity block (`ctx.g` comes from
  `aggregate()` over the same predicate).
- **the lens window** — `frameLensSlice()` `:3251-3263`: the view ranked by `dpsAt`, kept
  to `[pctl−10, pctl+10]`. This is the population behind EVERYTHING on the Character
  screen (`screenData()` `:3882-3898`; band text "players around pN (lens ±10) · n=… of …
  in view" `:4028`; builds_tab §3.3b "One lens, one slice").

**Decision: every live Lightspire number on the page is computed over the LENS WINDOW**, and
every surface that prints one names that population. The Spec Frame already carries a
lens-window block with its own scope line ("players around pN (lens ±10) · n=…" `:3278`),
so the frame has precedent; the Character screen is lens-window by construction
(`screenData(ctx).win` wraps `frameLensSlice`, `:3883`), so the tile, the fold-out row and
the frame row read ONE set and cannot disagree; and the percentile lens becomes a free knob
— p30 window against p85 window answers "do the best players stand in the light more", the
owner's own self-eval habit (user_prefs #8). The alternative (frame row over the whole
view) would make it the one number in the Overview block that ignores the lens while its
neighbours `g.med` and the skill-compare deaths move with it, and would print a second,
different `n` beside the screen's. (Superseded by this decision: an earlier draft of this
document computed the frame row over the view.)

| # | surface | where in code | discoverable | clutter | no wearer in view | knobs | rule conflicts | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | **Spec Frame → Overview block, one key/value row** | `frameIdentityHTML` `:3079-3118`, `row()` `:3084`, `ab()` `:3086`, `.fnote` `:3102`; CSS `.frows/.fnote` `:358-365` | one click from any bar (`openFrame` `:3362`); the frame is where a spec is vetted | one row, only when on + data | row absent; 1–9 measured → muted thin line (comps-block precedent `:3138` "real data, all below the gate — muted, never hidden") | lens window: all free incl. the percentile lens and Archon (`frameLiveIdx` elite branch); Time compare via `ab()` once `frameLiveIdx`/`frameLensSlice` accept a weeks argument (B ranks within its own view) | none; identity block is the sort-rule exemption; the 4pc row precedent | **PRIMARY** |
| 2 | Character screen → pooled Trinket fold-out row, second `.fsub` line | `csFoldTR` `:4215-4227` (`.fsub` already carries `crafted`/emb `:4219-4222`); `csPoolFoldHTML` `:4297-4329`; CSS `.cs-fold .fsub` `:683-685` | 3 clicks (bar → frame → "Character screen →" `:1502` → trinket tile); but it is literally "on the trinket", the owner's instinct | one sub-line under one row; rows already have sub-lines | LC not in the top-10 pooled entries (`:4302`) ⇒ no row ⇒ nothing; row present but <10 measured ⇒ muted | lens window: all free, incl. pctl slider | adds ~14 px inside `#cs-fold`, which lives in the stretch cell `.dcenter` (`:566`) — not a pane-growth (§2.6 forbids growth of the DOLL) | **SECONDARY** (with 2b) |
| 2b | same datum on the Trinket tile's `.gmeta` when LC is the #1/#2 pooled trinket | `poolTile` `:4554-4567`, meta at `:4565`; CSS `.gmeta i` `:634` nowrap/ellipsis | visible without clicking, when relevant | one `<i>` token beside `n=`; must NOT wrap or ellipsize `n=` (verify at 1920; fallback = drop 2b, keep 2) | tile shows another trinket ⇒ nothing | lens window | tile content is "unchanged in size, type and content" (§2.6) — a third meta token is a content change; kept to ONE token, one line | ships with 2 if the width check passes |
| 3 | Character screen → tile `title=` (native tooltip) | `cell()` `:4520-4536`, `title="'+o.title+'"` at `:4528` = slot label + embellishment; th/td `title` already used for the LEAN definitions `:4241,4271` | hover-only; invisible to a glance | zero visible | — | — | design §7 "title on tiny metadata only"; feedback #2; a headline number hidden behind hover contradicts pref #8 | **REJECT as a data surface.** A definition sentence may live in a control's `title` (the `tunebox.title` pattern `:1705`), never the number |
| 4 | wowhead tooltip on the icon | `a.whico` `:3714`, `whRefresh` `:6097/7395` | — | — | — | — | third-party script, icon-only by pref #11; no injection hook | **REJECT** (impossible + forbidden) |
| 5 | fold-out COLUMN "In light" | `csFoldCols` `:4237-4245`, `csFoldVal` `:4250-4256` | same as 2 | a column that is `–` on 9 of 10 rows, every render | column of dashes | lens window | pref #12 sorts it, but `:4226-4228` "ABSENT from cols entirely … not a dash" is the house reading | **REJECT** |
| 6 | Data Table column | `renderTable` `:7236`, `cols` `:7255-7266` (12 columns already), `sortHead` `:6157` | section starts closed (pref #18), one click; best surface for "which SPECS stay in the light" | 13th column; melee/Str/Agi specs all `–`; under compare the table is metric-only (`:7244-7254`) so the column is view-only | `–` parks last (NaN rule) | view: free; compare: A only | none hard; heavy for one trinket | **HOLD** — the documented next step IF the owner asks for a cross-spec ranking; not in v1 |
| 7 | a Lab OUTPUT section (per-spec sortable table) | slot 3 of feedback_round2's section order "Lab features' output (Set Bonus Gain today)" | one click, but a whole section | a section for one number/spec | table empty ⇒ section shows its sub-line | view: free | none; it IS the sanctioned home for output | **HOLD** — same trigger as #6; the frame row + card cover per-spec + field-wide today |
| 8 | Character screen identity band `.csnums` / `.cssub` | `csBandHTML` `:4000-4033`, lens line `:4028` | always visible on the screen | the band is already three dense lines | absent | lens window | duplicates the frame row on a screen that "duplicates no dashboard element" (builds_tab §3.2.2) | **REJECT** |
| 9 | Lab card scope line | `labScopeRefresh` `:2520-2534` writes `scopeBits()` into `.labscope` every `render()` (`:6198`) | sidebar, always visible while on | a sentence the card must carry anyway | n/a — it prints coverage facts from the payload stub, not a live ratio | n/a (a third population would be a third number for one metric) | none — "generated evidence sentence" is what the slot is for | **INCLUDED with the gate** (§4.3) as dates + coverage; the card is a control, not a readout |
| 10 | period note badge / strip scope chip / section ⚗ stamps | `:6242-6263`, `renderScopeChips` `:6427`, `labStamp` `:2535` | — | badges on six sections for a number that changes none of them | — | — | "Active modifiers only" — a readout is not a modifier | **REJECT**; manifest gets `stamps:false` (§4.1) |
| 11 | Overview bar inlay / hover tip `tipHTML` `:2842` / Pulse / Trajectory | — | — | — | — | — | the tip is the 450 ms hover surface being phased out; charts carry DPS | **REJECT** |
| 12 | Spec Frame as a NEW block (`FRAME_BLOCKS` `:3302`) | one registry entry | one click | a fourth block for one row; flex reflow; specframe §11 "any talents/trinkets stub … dormant UI is banned" | block absent | view | none, but disproportionate | **REJECT** in favour of #1 |

## 3. Recommendation

**PRIMARY — one Lab-gated row in the Spec Frame's Overview block** (surface #1).
Inserted in `frameIdentityHTML` after the Deathless row (`:3108`) and before the
`hasRating` row, through a helper that returns `""` unless the gate, the toggle, the data
and a wearer are all present:

```
Lightspire Core      in the light 78% of the time it was up
                     median of n=214 parses around p50 · pooled 76% · 1.9 h of beam · worn in 63%
```

- `td1` = `Lightspire Core` (plain text; no icon — the identity block has none, and an
  icon would summon pref #11's wowhead surface into the rail).
- `td2` = `in the light <b>78%</b> of the time it was up` on the value line — `78` is
  `beamStats(rows).med`; `<div class="fnote">median of n=214 parses around p50 · pooled 76%
  · 1.9 h of beam · worn in 63%</div>` beneath, the same `.fnote` the Deaths row uses
  (`:3102`). `rows` = the lens window (`L=frameLensSlice(); L.idx.filter(i=>L.inWin.has(i))`,
  the stats block's own slice `:3283`). "around p50" is `state.pctl`, so the fnote states its
  population the way the stats block's sub-line does.
- Formats: percentages `Math.round(x)+"%"`; beam time from `sAv` deciseconds — `≥ 36000 ?
  (sAv/36000).toFixed(1)+" h" : Math.round(sAv/600)+" min"`; `worn in` =
  `Math.round(100*wearers/rows.length)+"%"` (wearer rows over the window, measured or not).
- **Time compare on** (`state.compare`): value `78% · 74% <Δ>` via the block's own
  `ab(a, b, v=>v+"%", true)` (`:3086-3089`, `deltaHTML` relative, `betterUp:true`); fnote
  `median of n=214 · 187 parses (A · B) · 1.9 h · 1.6 h of beam`. B's rows come from the same
  slice helpers given `state.weeksB` — `frameLiveIdx`/`frameLensSlice` gain an optional
  `weeks` argument defaulting to `state.weeksA` (the one small piece of new wiring; B ranks
  within its own view, as the chart's ghost bars do). Missing B ⇒ `ab()`'s own `–`.
- **Skill compare on** (`skillOn()`): plain value + the Deathless row's exact suffix
  `<span class="fnote">(skill compare does not apply)</span>` (`:3108`).
- **Archon replica**: nothing special-cased — the aggregate reads the same elite row set
  `frameLiveIdx()` `:3025-3040` builds (14-day window, per-group `floorK`).
- **Projection on**: rows with `projSkip(i)` excluded, as the identity numbers exclude them
  (`:3047`).
- **Thin** (`beamStats().thin`, i.e. fewer than `CS_THIN`=10 qualifying parses or less than
  `BEAM_MIN_SUM_AV` of beam behind them): value replaced by
  `<span class="na">too thin — n=4 parses, 3 min of beam (floor n=10 · 10 min)</span>`, no
  fnote. Floors are the aggregator's constants; this lens asks for `BEAM_MIN_SUM_AV = 6000`
  ds (10 min) rather than the pipeline draft's 600 ds — a 0-dp percentage over one minute of
  beam is noise wearing a number's clothes (pipeline §8 Q2).
- **Wearers but nothing measured** (`wearers > 0`, `measured = 0`): value
  `<span class="na">worn in 12% of the window — beam data pending</span>` when `pending > 0`,
  `… — reports unavailable` when only `unavail > 0`. Honest coverage, not a placeholder: it
  says the trinket is here and the fetch has not reached it, and it never prints 0%.
- **No wearer in the window** (`wearers = 0`) or `g === null`: the row does not exist. A
  Warrior's frame never mentions an Intellect trinket.
- **Pending/unavailable beside a real number**: the fnote gains `· 12 pending` / `· 3
  unavailable` only when those counts are non-zero — a count, never a value.

Why this wins the owner's four words: *accessible* — the frame is one click from every bar
and the place a spec is vetted; *out of the way* — one row, present only while the Lab is
on AND the spec wears the thing; *clean* — the block is a transposed key/value ledger,
exempt from the sort rule, no column to justify; *grokable* — the value line says the
denominator in words ("of the time it was up") so it cannot be read as classic uptime, and
the fnote carries n, hours and share on one line.

**SECONDARY — the trinket entry on the Character screen** (surface #2, with #2b when the
width check passes). The owner pointed at "a tooltip on the trinket on the character
screen"; the rules forbid the tooltip (§1) and the screen already owns an in-place detail
surface for exactly this gesture — `csGearPaneHTML`'s scope line reads "click a tile to
unfold its distribution in place" (`:4445-4447`). So the number rides the entry itself:

- **Fold-out row** (`csPoolFoldHTML` → `csFoldTR`): when the row's entry is item 250214, a
  second sub-line inside the existing `.fsub` slot (`:4219-4222`, which already prints
  `crafted` / embellishment there):
  `in the light 78% of the time it was up · n=187 · 1.6 h of beam`.
  The row's own `n=214` column is wearers in the lens window (gear-known); `n=187` on the
  sub-line is the subset with a measured beam window, so the two n's are visibly different
  things. The `.fsub` is nowrap/ellipsis (`:683`), and this string is ~58 characters at
  .72rem — inside the name cell's width at 1920 (`.dcenter` ≥ 400 px); the pooled figure is
  deliberately left to the frame fnote to keep it so. Thin: `<span class="na">beam data too
  thin (n=4)</span>`; nothing measured: `<span class="na">beam data pending</span>` /
  `<span class="na">reports unavailable</span>`. NOT a column (surface #5 rejected) and
  therefore no sort implication; the row still sorts on Item/Share/n like every other.
- **Tile meta** (`poolTile` `:4554-4567`, meta string `:4565`): when `x.e.id === 250214`, append
  `<i>in light 78%</i>` after `<i>n=…</i>`. One token, one line, thin ⇒ token omitted (the
  fold-out says why). Acceptance check at 1920×1080: no `.gmeta i` ellipsis on either
  trinket tile in either doll column (`.dcol.r` is mirrored, `:595-597`); if the token
  ellipsizes `n=`, ship the fold-out line only — never a `.gfoot` second line, which grows
  the tile (§2.6).
- Population: the lens window, like everything on the screen. The band already declares it
  ("players around pN (lens ±10) · n=… of … in view", `:4028`), so the sub-line adds
  no scope prose of its own. Because the frame row reads the same slice (§2 decision), the
  tile, the fold-out row and the frame row print the SAME 78% and the same n=187 — one
  number per metric on the page, identical by construction, not by coincidence.

**The Lab card itself** (§4) carries dates covered and beam-data coverage in its scope line,
read from the payload stub — the manifest's standing slot for a generated evidence sentence.
It is a control with provenance, not a third readout: a field-wide ratio there would be a
third population for one metric, and nobody asked for it.

## 4. The Lab gate — how everything vanishes when off or when the data is absent

### 4.1 Manifest entry (`LAB_FEATURES` `:2466`; append, nothing else changes)

```js
{id:"light", name:"☀ Lightspire Core", badge:"LIGHT", mount:"labbox", card:true,
 stamps:false,                  // a READOUT, not a modifier: it changes no number in any
                                // section, so it never stamps ⚗ badges (feedback: "active
                                // modifiers only") — labStamp/labNoteBadges skip it
 gate:()=>hasLight&&!lightDead, // payload stub present AND the sidecar has not failed
 active:()=>hasLight&&state.light,
 controlHTML:'<label class="small" id="lightbox" title="'+LIGHT_DEF+'">'
   +'<input type="checkbox" id="lightcb"> Show in-light share'
   +'<span class="hint" id="lighthint"></span></label>',
 scopeBits:()=>!state.light ? "off — no Lightspire readout anywhere"
   : !LIGHTC ? "loading beam data…"
   : "on · beam data for "+fmtInt(S.measured)+" of "+fmtInt(S.wearers)
     +" wearer-parses ("+Math.round(100*S.measured/S.wearers)+"%) · "
     +fmtDay(S.dmin)+" – "+fmtDay(S.dmax)},   // S = the D.light stub entry for 250214
```

`hasLight = !!(D.light && Array.isArray(D.light.items) && D.light.items.some(t=>t.id===250214))`
— the stub's exact shape is the pipeline's (lightspire_ratio_pipeline.md §3.5, emitted only
when `measured ≥ 50`); this lens reads four fields of it: `measured`, `wearers`, `dmin`,
`dmax`. The item name is NOT read from the stub — "Lightspire Core" is a string constant in
the manifest, so the card never renders an id (pref #11).

Two one-line additions make `stamps:false` real: `if(f.stamps===false) continue;` as the
first statement of the loops in `labStamp` (`:2537`) and `labNoteBadges` (`:2548`). `proj`
and `posttune` have no `stamps` key and behave as today.

Naming hazard for the implementer: `wireScreen` already holds a local closure named `light`
(`:5952`) and the change strip a `lightUp` (`:6005`). Keep every new identifier prefixed —
`LIGHTC`, `beamStats`, `loadLightSidecar`, `decodeLightSidecar`, `lightDead`, `hasLight`,
`state.light` — and never introduce a module-level `light`.

`LIGHT_DEF` (the control's `title`, the sanctioned long-explanation slot like
`tunebox.title` `:1705` and `archonTitle()` `:2449`): *"Of the seconds a wearer's own
Radiant Light beam stood on the ground (12 s per proc), the share they spent inside it —
beam windows are the denominator, the Light's Blessing aura the numerator, both from that
player's own combat log. Not classic uptime: a fight with no beam has no ratio and is
counted separately. Headline = median over parses; the footnote adds the time-weighted
pooled share. SimulationCraft assumes 50%."*

`#lighthint` (from the payload stub, so it exists before any fetch): `(Aug 20 – Sep 8 ·
beam data on 31,877 of 48,210 wearer-parses)` — the dates-covered / sample-size line pref
#8 asks for, in the same hint slot `projhint`/`tunehint` use (`:1704-1705`). The scope line
repeats it while on (above) so the card reads the same whether the hint or the scope is
what the eye lands on.

`state.light:false` joins the state literal beside `proj:false` (`:1607`).

### 4.2 Feature detection and loading (mirrors `hasProj` + the sidecars)

- **Payload stub** `D.light = {items:[{id:250214, measured, wearers, dmin, dmax, …}]}`
  (≤200 B), emitted by the build ONLY when the sidecar shipped and `measured ≥ 50` (both or
  neither — the "rebuilt-or-unlinked every build" discipline of `talents.json.gz`,
  builds_tab §1.7; pipeline §3.5). `hasLight` (§4.1) is set next to
  `hasProj=!!(D.projection&&R.tmul)` (`:1697`), before `renderLabCards()` (`:1701`).
- **Sidecar** `light.json.gz` (name adopted by both lenses — pipeline §8 Q6), row-aligned
  with the payload like `stats.json.gz` (`decodeStatsSidecar` `:2965-3018`: sparse `idx` +
  `idxdelta`, `n !== N ⇒ reject` with the same `console.warn`). Per covered row, THREE
  columns (pipeline §3.2, adopted verbatim): `av` Uint16 **deciseconds** of beam window,
  `bn` Uint16 deciseconds of blessing ∩ window, `st` Uint8 status — `0` pending (wearer,
  not fetched), `1` measured (`av = 0` then means a real no-window fight), `2` unavailable
  (report gone / actor missing). Deciseconds because a single-beam parse is 12,000 ms and
  whole-second rounding on both ends moves a ratio by up to ±8%; a status byte because a
  sentinel in `av` would fold "not yet" into "never", and the two print differently (§3).
  Covered rows = EVERY wearer-parse the gear journal knows (slot 12/13 `id === 250214`,
  `TRINKET_SLOTS` `scripts/build_site_data.py:829`, `compact_gear`
  `scripts/fetch_data.py:447-483`), so wearer counts and coverage are computable
  client-side under any filter without the API. Size: ~9 B raw per covered row before gzip
  (pipeline §3.3); read `site/build_health.txt` for the real figure (user_prefs #20c: "Sizes
  in the blueprints are stale by default").
- **Fetch on first toggle-on** (`wireLabControls` `:2505`, beside `pc.onchange` `:2509`): `$("lightcb").onchange =
  e => { state.light = e.target.checked; if(state.light) loadLightSidecar(); render(); }`.
  `loadLightSidecar()` is `loadBuildsSidecar` (`:3507-3522`) verbatim with the file name
  swapped; on decode it sets `LIGHTC` and calls `render()` (the one path that reaches an
  open frame and the screen, `:6198` onward). Until then every surface renders nothing and
  the card's scope line reads `loading beam data…`.
- **Failure** (fetch error / decode reject / n mismatch): `lightDead = true;
  state.light = false; renderLabCards(); setNotice("☀ Lightspire beam data unavailable this
  session.")`. `gate()` is now false, so the card is removed and the LAB group hides itself
  when it was the only card (`:2502`). No greyed control, no dead checkbox — the same
  "affordance never sits dead" treatment `buildsSidecarFailed` gives `#frame-char`
  (`:3524-3531`). **Divergence to settle** (pipeline §3.2 keeps the card and has its output
  say "data unavailable"): this lens holds that a card whose only output is "unavailable"
  IS the placeholder feedback_round2 #3 bans, and that the one notice says the same thing
  once instead of every render.

### 4.3 One aggregator, the lens window in, numbers out

No surface computes its own. `beamStats(rows)` (pipeline §4.2: walks `rows`, skips rows the
sidecar does not cover, splits `st`, returns `{wearers, measured, pending, unavail, noWin,
n, med, q25, q75, pooled, lightMin, thin}`, `null` while `!LIGHTC`) is called with:

- the Spec Frame row: `const L=frameLensSlice(); beamStats(L.idx.filter(i=>L.inWin.has(i)))`
  — the stats block's own slice (`:3283`); under Time compare a second call with the
  `weeks`-parametrised slice for B;
- the Character screen (tile + fold-out): `beamStats(screenData(ctx).win)` — the same set by
  construction (`:3883`), computed once per `renderScreen()` and handed to `poolTile` and
  `csPoolFoldHTML` rather than recomputed in each.

Every reader treats `null`, `!labActive("light")` and `wearers===0` as "render nothing".
The Lab card reads the payload stub only (§4.1); it never calls the aggregator.

### 4.4 Retirement = one manifest deletion plus its read sites

Delete the `light` entry, `loadLightSidecar`/`decodeLightSidecar`/`LIGHTC`/`beamStats`, the
three read sites (frame row helper, `csFoldTR` sub-line, `poolTile` token), the optional
`weeks` argument's two call sites if nothing else took it up, and `state.light`; the
pipeline stops emitting `D.light` + `light.json.gz`. An older client reading a payload that
still carries `D.light` ignores it (unknown-key tolerance); the new client reading a payload
without it renders exactly today's page. Zero residue, by construction of §4.1's `gate()`.

## 5. Acceptance walk (what a reviewer clicks)

1. Fresh load, payload without `D.light`: sidebar LAB group identical to today; no
   "Lightspire" string anywhere in the DOM.
2. Payload with `D.light`: LAB card present, chip `off`, hint shows dates + n. Frame and
   screen unchanged until the toggle.
3. Toggle on: chip `active`, scope `loading beam data…` → numbers; the open frame gains the
   row without moving any other row's position above it; the strip, period note and
   section scope lines gain NO badge.
4. Click a Str/Agi spec's bar: no row. Click a Holy Priest: row. Narrow keys to +20–+20: the
   row goes muted-thin or vanishes together with the identity numbers, never a number under
   "no parses match the current filters" (user_prefs #20).
5. Compare: Time ⇒ `A · B (Δ)`; Skill ⇒ the "(skill compare does not apply)" suffix; Archon
   replica ⇒ numbers change, wording does not.
6. Character screen → Gear → click a Trinket tile: the LC row carries the sub-line and its
   78% / n=187 are the frame row's exact figures; drag the percentile lens to p85: both move
   together and stay equal; the tile token (if shipped) matches too.
7. Toggle off: everything from 3–6 disappears; card scope reads `off — no Lightspire
   readout anywhere`.
8. Simulate a 404 on `light.json.gz`: card removed, one notice, page otherwise intact.

## 6. Deliberately NOT built

A `title=` number on the tile or row (§1); a fold-out column (surface #5); a Data Table
column and a Lab output section (surfaces #6/#7 — held behind the cross-spec question, not
asked); a fourth frame block (#12); a strip chip or ⚗ stamps (#10); any per-parse
"uptime" wording — the word "uptime" appears nowhere in the UI because the owner's metric
is not that; any rendering of the spell ids (pref #11: "Raw numeric ids must never render").

## 7. Open questions for the data lens (each changes a string, none changes a surface)

1. Do casts of 1263762 reach the combat log as `SPELL_CAST_SUCCESS` by the wearer (the usual
   shape for a Create-Area-Trigger proc), or only as the area trigger's own events? Decides
   how `A` is read; one report from a known wearer settles it.
2. Can a teammate's beam apply 1263768 to the wearer? The intersection keeps the ratio
   ≤ 100% either way, but if yes the card's definition sentence should say "any beam".
3. Does the area trigger really despawn at exactly 12,000 ms (SpellDuration 29), or does
   1263768 linger a tick after leaving? Affects rounding only.
4. Backfill depth vs forward capture: the hint line prints whatever `D.light.dmin/dmax`
   say; the surfaces need no change either way.
5. `noWin` ("wearer, fight fetched, zero beams"): not printed anywhere in v1; if the first
   run shows it is common (short fights), promote it to the frame fnote as `· 12 with no
   beam`, a count beside the other counts.
6. Settled between the two lenses in this revision (pipeline §8 Q6): names are this
   document's (`light.json.gz`, `D.light`, `state.light`, `LIGHTC`); units, status byte and
   the dual pooled/median output are the pipeline's; headline = median, pooled in the
   footnote; population = lens window on every surface. Still open: the failure-mode
   treatment (§4.2, card removed vs card kept with "unavailable") and `BEAM_MIN_SUM_AV`
   (600 vs 6000 ds).
