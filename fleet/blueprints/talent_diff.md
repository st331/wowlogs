# Talent pane: build diff, median-DPS clarity, pane geometry

One change to the Talents pane of `site/index.html`. Three owner items — base/candidate diff, median DPS made clear, panes
inconsistently sized — ship together because they all rewrite `csTreePanesHTML` / `csBuildChipsHTML` / `csTalentsHTML` and compete
for the same space.

**No pipeline change; none is justified.** Everything consumed already ships: `sel` on 960/960 builds (`[nodeId,rank]` doublets,
`[nodeId,rank,entryIdx]` triplets) and `talents.json.gz`'s `nodes[].{id,x,y,r,n,ic,t,s,es}` + `edges`. The diff is a map-vs-map over
two arrays of ≤81 entries — microseconds, once per render. A precomputed diff would be 22,080 ordered pairs, a second source of
truth for something derivable, and would break the moment the base is a pair it did not precompute. `med` is already computed
(`qp(dps,.5)`) and already rendered. The geometry fix is arithmetic. Refuse any pipeline work. Line numbers are **@ b52d0a4**;
anchor by function name + the quoted code, which is authoritative. Build order: §1 geometry → §2 model → §3 UX → §4 encoding → §5
render → §6 surfaces → §7 median → §8 edge cases.

## §1 — Pane geometry (Item 3)

**Diagnosis** (measured, live doc, 40 specs / 162 panes). `csTreePanesHTML` computes `x0/y0`, `spanX/spanY`, `gx/gy` **per pane**
(:3838–3850) then overrides the shared scale **per pane** (:3870–3871). Four consequences — the screenshot exactly:

1. **`p.y0` is per-pane**: each pane normalises to its own topmost row, so panes with different top rows sit shifted by an arbitrary
   amount. **26 of 40 specs** have `class y0 ≠ spec y0` (Warrior|Arms: class 600, spec 900 → every spec row lands 300 units off
   every class row). Rows never align in 65% of specs.
2. **Heights always diverge**: `H = spanY*sy + node`, and `|spanY_class − spanY_spec|` is **never 0** — 150 units in 24 specs, 750
   in 14, 450 in 2. Ret is 6300 vs 7050 → Spec hangs ~50px below Class.
3. **The empty band is a real gap drawn at full scale**: Ret class rows are `600, 1500, 2100…` (two isolated nodes at 600, then a
   900 gap where the step is 600); Ret spec ends `6900, 7650`. The gaps differ per pane, so they also *cause* (2).
4. **The wrap is a header overflow**: the width budget (:3851) subtracts one global `44` for "a pane is never narrower than its
   header". The Hero canvas is ~150px but its header (`HERO  Herald of the Sun  14 pts`) measures ~200px, so the row exceeds the
   budget and `flex-wrap:wrap` (:711) drops Hero to row 2, where `justify-content:center` centres it alone.

**Fix — one shared unit, ladders on both axes, one common height.** Replace :3838–3876 (per-pane geometry loop, fit block,
`p.sx/p.sy` loop, `px/py` closures) with:

```
UNIT      pooled gap step over ALL panes, both axes (existing gstep() on merged counters).
          Measured 600 everywhere; derived, not hardcoded.
ladder(v) -> {m:Map(coord->slot), slots}   distinct snapped coords ascending; the slot
          advances by 1 when the gap is <= 1.5*UNIT, else by 2 — one empty slot preserved
          (real structure), never more. Every extreme gap compressed UNIFORMLY, every pane.
rowLad    ONE row ladder over the UNION of Class+Spec snapped y; both panes read it, giving
          identical rhythm AND true cross-pane row alignment.
heroLad   Hero gets its OWN row ladder, same rule, same pitch. Forced: hero y0 sits up to 4200
          units (7 empty rows) below the class/spec origin, so a shared origin opens an absurd
          blank band. Separate tree: the PITCH must match, not the origin.
colLad    per pane (columns are not comparable across trees), same rule, shared pitch.
for(node=44; node>=34; node-=2){                    // pref #10: 34px floor, never below
  colPitch = node+4;  boxW(p) = max((p.cols-1)*colPitch + node, headW(p));
  if(sum(boxW) + (P-1)*12 + P*22 <= availW) break;  // CHROME=22 unchanged
}
grow colPitch by any slack, capped at node+26;      // don't over-stretch sparse trees
rowsMax  = max(rowLad.slots, heroLad.slots);
rowPitch = clamp(node+6, floor((capH-node)/(rowsMax-1)), node+22);
H_common = (rowsMax-1)*rowPitch + node;             // EVERY pane's canvas height
px(n) = colLad.m.get(snap(n.x)) * colPitch;
py(n) = (pane is Hero ? heroLad : rowLad).m.get(snap(n.y)) * rowPitch;
```

`headW(p) = 34 + 7.2*name.length + 46` (+62 when a base is pinned, for the `.tdf` tally) replaces the global `44` fudge — the direct
fix for the wrap. Delete `p.x0/p.y0/p.spanX/p.spanY/p.sx/p.sy` and the `sx=Math.min(sx,(node+26)/600)` line; keep `snap` and
`gstep`.

**Common height, content top-aligned** (not size-to-content): `#cs-trees{align-items:stretch}`, every `.ttcv` gets
`height:H_common`. Sizing to content is what produces today's ragged bottoms — the class/spec span difference is non-zero in 40/40
specs, so "identical metrics but content-sized" still leaves one pane hanging. **Wrap**: `#cs-trees` becomes `flex-wrap:nowrap`; the
solver emits `class="fit"` (`justify-content:center`) when the row fits, else `class="ovf"` (`justify-content:flex-start`) — a
centred *overflowing* flex row clips its leading edge and makes the Class pane unreachable. On overflow it scrolls **inside**
`#cs-trees` (existing `overflow-x:auto`, :711): the standing bounded-container rule; the page never scrolls sideways.

**Verified against the live doc.** Worst spec DemonHunter|Vengeance = 27 column slots → 1014px + 90 chrome = **1104 ≤ 1116** at node
34; median spec 23 slots → node **40**. Max union rows **12** → at node 40 / rowPitch 46, `H_common = 546 ≤ capH 560`. At 1366×768
`capH` falls to 380, the `node+6` floor wins and the PAGE scrolls vertically (pref #10 forbids buying the fit by shrinking icons);
at ~1200 the worst spec scrolls inside `#cs-trees`. No page-level horizontal scroll at 1920, 1366 or 1200.

## §2 — Diff model (Item 1)

`selMap(sel)` = the existing idiom at :3807–3809, `nodeId -> [rank, entryIdx|null]`. Build one for the base (A) and one for the
candidate (B); classify over the union of keys:

| Category | Test | Record |
|---|---|---|
| **add** | in B, not in A | `{k:"a", r}` |
| **drop** | in A, not in B | `{k:"d", br, bi}` |
| **rank up** | in both, same `entryIdx`, `r > br` | `{k:"u", r, br}` |
| **rank down** | in both, same `entryIdx`, `r < br` | `{k:"w", r, br}` |
| **swap** | in both, `entryIdx` differs | `{k:"s", r, br, i, bi}` |
| shared | in both, same rank, same `entryIdx` | none |

Test **swap before rank** — disjoint in live data (185/185 rank changes sit on plain `r=2, t=0` nodes carrying no `entryIdx`), but
if they ever collide swap wins and the pip still prints both ranks. Over 22,080 ordered pairs: 123,150 add, 123,150 drop, 2,267 rank
up, 2,267 rank down, 26,378 swap; median diff 7, p90 16, max 30 on the two always-visible panes. Never flatten rank or swap into
"shared": 7.8% of build-1 pairs have zero adds and zero drops and differ only by swap, and 19.1% of pairs carry a rank change.
`paneOf` = one flat `Map(nodeId → "cls"|"spec"|"hero")` from the three `nodes` arrays; node ids are unique across panes (0 orphans,
0 duplicates over 960 builds), so one map serves all three. `heroOfSel(sel, trees.hero)` = the first sel id found in a hero tree's
node set; agreement with the modal `R.hero` tag is **960/960**, so the base's hero tree is nameable even when `state.merge` is off
and `row.hero` is undefined.

**Counting rule, stated once, applied on every surface.** Adds and drops count **class+spec panes only**; rank changes and swaps
count the **full sel**. Adds/drops must exclude hero or all seven Paladin|Retribution chips read `+16 −16` when the truth is "2 to
11 talents plus a different hero tree". Rank/swap are safe over the full sel: a cross-hero pair shares *no* hero node ids (disjoint
trees ⇒ no rank or swap event can arise there) and a same-hero pair has measured 0 hero adds/drops, so no hero substitution can
inflate them.

## §3 — Base-pin UX, every control named

- **`base`** — `<button class="pbtn" data-base="<r.s>">base</button>` inside **exactly one** chip: the currently lit one, styled
  from the existing `.bldchip .cbtn` rule (:706). You light a chip to look at it, so the offer is under the cursor already; eight
  permanent buttons is clutter for a once-a-session control. `bkind:"hash"` for all 40 specs in production, so `copy` never renders
  and `base` takes its slot at zero layout cost. Not rendered on a `sel`-less row (the `withSel` guard, :3939) nor on
  `.bldchip.oth`.
- **`unpin`** — the same button on the pinned chip, label statically swapped (precedent: `#frame-pin` swaps `pin`→`pinned`; `.sec
  .mk::before` swaps `−`/`+`, :251–252). No transition, no rotation, no animation (pref #3).
- **`flip`** — `<button class="btn sm" data-flip>flip</button>` in the diff bar, `title="make Build 7 the base and Build 3 the lit
  build"`: "what does build 2 add over mine" and the reverse are the same question read backwards, and reading a `+` as a `−` in
  your head is the error this feature exists to prevent. **`clear base`** — `<button class="btn sm" data-clearbase>clear
  base</button>`, also in the diff bar.
- **Keyboard**, Talents tab only, behind the existing form-control guard (:3005): `←`/`→` walk the lit build along the chip row (the
  arrow branch at :3006 falls straight through to `return` on the Talents tab today — the keys are free); `b` pin/unpin the lit
  build; `f` flip; `Esc` stays swallowed and unclaimed (:2997–3002). Chips get `tabindex="0" role="button"` and `Enter`/`Space` —
  `<span onclick>` today, unreachable by keyboard, not acceptable for the primary control.

**State**: one module var `screenBaseS=null` beside `screenBuild` (:2977), holding the build **hash `r.s`, never the ordinal** —
`data-bld` indexes `T.rows`, recomputed from the lens window every render, and a base pinned at chip 8 leaves the row on 59% of lens
moves. Re-resolve every render from `d.vocab.builds.find(b=>b.s===screenBaseS)` — 24 static entries per spec, immune to the lens; a
module var (not DOM state) also survives the `renderScreen()` that `loadTalentsDoc` fires at :3070. **Resolving the base from
`T.rows` instead of `d.vocab.builds` is the one silent-failure bug here: it works until the lens moves, then the diff vanishes for
the ~23.5% of cases where the base leaves the top 8.**

**Do not refactor `screenBuild` to a hash in this commit** — a real latent bug (a lens move silently re-points the lit build), but
it touches five call sites and belongs in its own commit. `flip` needs only a sentinel: `screenBuild === -1` means "the pinned
base", handled in exactly two places — the clamp (:3941–3945) and the row lookup (:3946) — resolving to a synthesised row `{s, sel,
hero:heroOfSel(sel), n:null, share:null, med:null}`. The hoisted ghost base chip carries `data-bld="-1"`, so it stays clickable and
arrow-reachable. **Three chip states, no new colour**: idle `--line1` rim (today); lit `--metal-line` rim + `--accent` label (today,
:700–701); base `background:var(--metal); color:#241d0e; border-color:var(--metal-line)` — the metal fill used by `.chip.on` and
`#frame-pin.on`, currently unused on build chips. Base+lit shows fill plus accent label. `.bldchip`'s `transition` (:698) lists
`border-color, color` only, so the fill lands instantly — **leave `background` out of that list; do not "complete" it.**

## §4 — Visual encoding

**The badge is the signed POINT delta at that node; `≠` when the points are unchanged and the spell is not.** The rank pip keeps its
job — the value in this build. Two corners, two questions. `.ttb` — new element on `.ttn`, top-left at `-5px/-5px`, diagonally
opposite the existing `.ttr` pip (`right:-5px; bottom:-5px`, :755), so they cannot collide even at the 34px floor. It overhangs into
pane padding rather than covering the icon face, which also sidesteps the octagon clip — no special-casing for `t===2`.
`pointer-events:none`: the icon stays the sole wowhead surface (pref
#11).

| Category | class | badge | tile |
|---|---|---|---|
| **add** | `da` | `+1`/`+2` in `--up` | `.pk`, rim `--up` (`.tico.ch{background:var(--up)}`) |
| **drop** | `dd` | `−1`/`−2` in `--down` | third state, below |
| **rank up** | `da` | `+1` in `--up` | `.pk`, rim `--up`; pip `1→2` in `--up` |
| **rank down** | `dd` | `−1` in `--down` | `.pk` (**not** dimmed — still lit); rim `--down`; pip `2→1` |
| **swap** | `ds` | `≠` in `--accent` | `.pk`, **rim unchanged**; ghost thumbnail |
| shared | — | none | byte-identical to today |

Badge value uses `Math.max(1,rank)` reused **literally** from :3897, never re-derived. Invariant: a pane's badges sum exactly to
that pane's `.tp` point delta — an owner who checks and finds it off by one stops trusting the surface. (A swap that also moves rank
— 0 measured — prints `≠` plus the signed remainder.)

**Glyph `≠`, not `⇄`.** A swap is a state, not a motion, and a two-headed arrow in the one slot whose job is stating direction is
the worst glyph for it — `/* no arrows/carets/triangles */` (:399) is close enough to bite. `→` stays allowed inside the rank pip,
where both values are printed. **Swap gets NO rim change**: `--accent` `#E8BC57` against the picked rim `--metal-line`
`rgba(242,207,118,.55)` is champagne on champagne, invisible. The badge and the ghost carry swap; both are shape channels, stronger
anyway.

**The ghost** `.ttg` — a 15px greyscale thumbnail of the base's option, bottom-left, `border:1px dashed var(--down)`,
`pointer-events:none`, `onerror="this.remove()"`. It states "was that, now this" on the map with no hover, for the most common
non-membership change there is (26,378 instances, 70% of pairs). Its detail goes on the `.ttn` div's existing `title` (`Lava Burst ·
base had Frost Shock`) — **never a `title` on the ghost, which is `pointer-events:none`.**

**The third state — a drop must not read as an unpicked node.** Today a dropped talent is `.dm`, indistinguishable from the median
19.5 (class) / 11 (spec) genuinely-untaken nodes. Four simultaneous channels, one of them colour: (1) **luminance tier**
`.ttn.dd{opacity:.68}` — picked 1.0, dropped .68, untaken .42; (2) **dashed rim** `border-style:dashed` in `--down` — untaken solid
`--line1`, picked solid `--metal-line`, dropped dashed; achromatic, survives greyscale and every colour-blindness; (3) the **`−n`
badge**, in a slot untaken nodes leave empty; (4) **identity** — the tile fronts the *base's* option (icon, name, spell link) and a
`−2/2` base pip: you are looking at the thing you would lose. Octagons cannot dash a `background` rim (`.tico.ch{border:none}`,
:738) — note it in a comment and let tier + badge + `--down` ink carry them; do not fake a dash. **Colour discipline:**
`--up`/`--down` are text/rim ink only, never fills (design_language.md:50); the `.tico.ch` `background` is the octagon's own
existing rim mechanism (already used by `.ttn.pk .tico.ch`), not a new fill. No new hue, no purple, nothing rotates, nothing grows
or glows on hover. **Hover hole — fix it:** :752–753 read `.ttn.pk a.whico:hover .tico` and `.ttn.dm …`; a dropped node is still
`.dm`, so hovering a dropped octagon repaints its `--down` rim champagne and the marker vanishes just as the reader looks hardest.
Add `.da`/`.dd` hover overrides after that block — still colour-only hover, pref #7 intact. **Edges** (:3883): `lit` becomes
three-way — `lit` (today, champagne, dominant), `wasl` (lit in base only) `stroke:rgba(255,107,107,.26); stroke-dasharray:3 3`,
`newl` (lit here only) `stroke:rgba(61,220,132,.28); stroke-width:1.4`; a dropped node otherwise floats unconnected. **First thing
to cut** if it reads as noise at 30 marks.

## §5 — Tree render changes (`csTreePanesHTML`, exact sites)

Signature `(ctx,trees,row)` → `(ctx,trees,row,base)` at :3794, `base` = `{sel,hero}` or null.

- **:3807–3809** — build a second map the identical way from `base.sel`; classify per §2 into `dmap=Map(nodeId→record)`.
- **:3799–3803** — when `base` exists and `heroOfSel(base.sel) !== row.hero`, pass `dmap=null` for the Hero pane (§8.1).
- **:3889–3898** — the subtle part: a dropped node has `s===null`, so today it would front the generic first-entry glyph, print no
  pip and skip the `es` resolution. Replace with `const df=dmap&&dmap.get(n.id), drop=df&&df.k==="d";` `const eIdx = picked ? s[1] :
  (drop ? df.bi : null);` `const rk = picked ? s[0] : (drop ? df.br : 0);` `const multi = (picked||drop) && +n.r>1;` and leave
  `if(picked) pts+=Math.max(1,s[0]);` **unchanged** — a drop never inflates the point total.
- **:3907** — the single class-list write gains a third token (`da`/`dd`/`ds`), the `.ttb` badge span, the `.ttg` ghost on swaps,
  and `.ttr` gains `rup`/`rdn` and `a→b` content.
- **:3912–3914 `.tthead`** — insert `<span class="tdf">+4 −3 ≠1</span>` between `.tn2` and `.tp` (which tree changed is a different
  respec cost from how much changed). Zero added height — `.tthead` is an existing baseline flex row with `.tp` on
  `margin-left:auto` (:721). `.tp` extends to `82 pts · base 84 −2`.
- **CSS source order is load-bearing.** `.ttn.dm .tico` (:749) and `.ttn.dd .tico` are both `(0,2,1)`, so source order alone
  decides: the diff block MUST be authored **after** :748–751 or every drop silently loses its rim. Commit-message material — most
  likely bug in the build, least likely to be noticed (drop-only).

## §6 — Chips, diff bar, ledger

**Chips** (`csBuildChipsHTML`, :3775). With a base pinned each chip gains one `.mi` counter slot — `+3 −2 ≠1`, zero categories
omitted rather than printed as `+0`, glyph spans inked `--up`/`--down`/`--accent`, tabular-nums; the base chip prints the word
`base` there. A cross-hero chip's `.htag` is prefixed `≠ Templar` in `--accent`: one glyph, zero pixels, and on Retribution — six of
seven chips a hero swap — it answers "does this cost a hero respec" before a click. With no base pinned the chip row is
byte-identical to today plus the `base` button on the lit chip.

**Diff bar** `#cs-diffbar`, one line between chips and trees so the reader crosses it on the way down (`display:flex; gap:.6rem;
align-items:baseline; font-size:.74rem`, ~26px), reading `DIFF   Build 3 · Build 7   +4 −3 ≠1   median 1.46M · +41k vs base
[flip] [clear base]`. `DIFF` uses the `.tl` micro-label treatment (:718); buttons right-aligned on `margin-left:auto`; honesty
clauses (§8) render `--ink2` in the middle, longest state ~72 chars in a ~1116px measure.

**Ledger** — a `.sec`-style disclosure below the trees, **closed at rest** (~28px), trigger `− Diff ledger · 8 changes` using the
existing static `.mk` marker (:251–252 — nothing rotates). It exists for the one thing a tree cannot do: print a difference that has
no pixel. Opened, it is `.tblwrap` + `table.data` (:381–401), six columns, **every one sortable through `cmpCells` (:4131)** per
pref #12, on the house pattern at :2685 (`<th data-c>`, `[...rows].sort(...)`, `th.onclick` toggling a module `screenDlSort`):

| Column | Content | Sort key |
|---|---|---|
| **Δ** | glyph **and word**: `+ ADD` · `− DROP` · `+1 RANK` · `−1 RANK` · `≠ SWAP` | category ordinal ADD 5 / RANK+ 4 / SWAP 3 / RANK− 2 / DROP 1 |
| **Talent** `.txt` | node name; icon on add/drop/rank rows | name, `localeCompare` |
| **Base** | `2/2`, `—`, or the base's option name + icon | base rank; `null` for `—` (cmpCells parks it last both ways) |
| **This build** | `1/2`, `—`, or this build's option + icon | same |
| **Pane** | `Class` / `Spec` / `Hero` / `Hero (not shown)` in `.na` ink | pane ordinal |
| **Δpts** | `+1` `−2` `0` in `.delta.up/.down` | signed int; sums to the header delta |

Left to right it is a sentence with a literal dash on one side: `− DROP | Ravager | 1/1 | — | Spec | −1`. Colour-blind, greyscale
and screenshot all survive. **Icon rule (pref #11):** the icon lives in whichever cell names a distinct spell — one icon in
**Talent** for add/drop/rank rows; on swap rows the name is plain text and the two icons sit in **Base** and **This build**, because
for a swap the spell identity *is* the before/after (swap rows are then the only rows with icons on the right: shape reinforces `≠`
for free). Default order = pane, then node y (the tree's own reading order, so ledger and map agree); no `th` carries `.sorted`
until clicked. `.dl-t{min-width:820px}` beats `table.data{min-width:700px}` so the two state columns cannot collapse; `max-height`
(10 rows) + `overflow-y:auto` **only when rows > 10**, or a one-row ledger grows a stray scrollbar; `.data th` is already
`position:sticky; top:0` (:384), so the sticky header is free; any note (`.dl-note`) sits **above** the `.tblwrap`, never as a
spanning `<tr>`, which would break the sort. Sitting **below** the trees and closed at rest, the disclosure never enters the layout
solver and `capH` is untouched: pinning a base must never shrink the trees, which are the subject.

## §7 — Median DPS made clear (Item 2)

The number already exists (`csTalentRows`: `med: qp(dps,.5)`) and renders as a bare `<span class="mi">246k</span>` between the share
and `n=`, in the same muted style as `n`. The defect is legibility, not absence. **Do not add a second copy elsewhere.**

1. **Label and weight it on the chip.** Replace the bare `.mi` with `<span class="med"><i>med</i> 1.42M</span>` —
   `.med{color:var(--ink); font-weight:650; font-variant-numeric:tabular-nums}`, `.med i{font:700 .6rem/1 var(--font);
   text-transform:uppercase; letter-spacing:.14em; color:var(--ink3); font-style:normal; margin-right:.28rem}`. It now outranks
   share and `n`, which stay `.mi` muted — the whole fix for "reads as one more anonymous number".
2. **Delta against the base.** With a base pinned every non-base chip appends `<span class="delta up">+41k</span>` / `.down`, the
   existing token verbatim (:304–306). **Sign convention: candidate − base; positive means this build's cohort median is higher,
   inked `--up`.** The base chip prints `base` there. **Prominence for the lit build**, near the trees: the diff bar's clause
   `median 1.46M · +41k vs base` in the same `.delta` ink.
3. **Thin builds.** `const CS_MEDN=20;` when `min(n_base, n_cand) < CS_MEDN` the delta renders `.delta.na` (grey, weight 500 — the
   existing "no counterpart" treatment) instead of up/down, `title="one of these builds has fewer than 20 parses — the median is not
   separable"`. The 95% interval on a median is roughly ±1.25σ/√n; below ~20 parses it is wider than the gaps between adjacent
   builds. `n=` stays on every chip.
4. **Honesty caption, verbatim:** `median = the middle DPS of that build's parses in the current window — it moves with the filters
   and the trust gate. It is a median, so the percentile lens does not change it (the lens relabels every other DPS reading on this
   page). Builds are compared, not causes: a higher median is that cohort's result, not a measured gain from the talents.`

## §8 — Edge cases (all measured; none optional)

1. **Cross-hero base** — 121/920 build-1 pairs (13.2%), 15.3% of all ordered pairs; six of Retribution's seven chips. **No
   node-level hero diff**: the base's 14 hero nodes have no coordinates in this pane and vice versa, so the diff would be 14 adds +
   14 drops every time — "different hero tree" written 28 times. Instead: Class and Spec diff fully (median 14, max 24 marked nodes;
   **0 cross-hero pairs had identical class+spec trees**, so there is always signal); the Hero pane's 14 nodes stay `.pk` with no
   badges; its `.tthead` states the substitution in the same markers — `HERO  Herald of the Sun   − Templar  + Herald of the Sun`;
   the ledger carries the row `Hero | hero tree | ≠ SWAP | Templar | Herald of the Sun | —`; all counters exclude hero adds/drops
   (§2).
2. **Same-hero, merge on** — the hero pane diffs normally but only swap can fire (0 hero adds and 0 hero drops across all same-hero
   pairs — every build in a hero tree allocates the identical 14 nodes), so a hero pane with no badge is *truthfully* identical and
   the header may print `identical` in `--ink3`.
3. **Merge off / hero-zoomed — the silent-lie case.** No hero pane renders (:3799 gates on `state.merge && row.hero`), but **the
   diff is computed over the full `sel` regardless**. For 15 of 920 build-1 pairs (112 ordered) the *entire* difference is a
   hero-tree swap, and today those render as two byte-identical pane pairs at the exact moment the owner is asking why one out-DPSes
   the other. The diff bar then reads, verbatim: `identical in Class and Spec · 1 hero-tree choice differs (hero pane not shown in
   hero-zoomed view)` in `--ink2` (a finding, not an absence), and the ledger carries the row with `Pane: Hero (not shown)`. **Never
   two clean trees and silence.** The existing foot caveat (:3952) — true about the panes, false by implication about the builds —
   is amended to `…in the trees shown; hero-tree choices are counted but not drawn here`. Do **not** add a hero pane here (pref
   #10).
4. **Base leaves the slice** — over 3,200 lens observations: on the chips 76.2%, folded into "other" 18.9%, gone 4.6% (chip 1
   survives 99.2%, chip 8 only 40.6%). The **diff always survives** (the base's `sel` is in the static vocab); only its statistics
   cease to exist. A ghost base chip is **forced to the head** of `#cs-bldchips` keeping its metal fill, name, hero tag and `unpin`,
   with share/median/n as `—` in `.delta.na` and a trailing `.mi` reading `below top 8 in this window` or `0 runs in this window` —
   both read from the `byV` map `csTalentRows` builds at :3300–3305. Never a stale number, and **never a silent unpin: the owner
   moved the lens, not the base.**
5. **Spec change** — `screenBaseS=null` joins `resetScreenPerSpec` (:3237–3242), one line beside `screenBuild=0`. The 960 hashes are
   globally distinct, so a stale base resolves to nothing and would render a Retribution diff against a blank — a lie by omission. A
   merge on/off flip on the **same** spec **keeps** the base: its `sel` is unchanged and its hero tree is derivable from `sel` alone
   (960/960).
6. **Base == candidate** — all six categories are zero by definition. Render the trees exactly as today: `.pk`/`.dm`, no badges,
   rims, ghosts, `wasl`/`newl` edges or zero counters. Diff bar: `Build 3 is the base — shown plain · click another chip to diff
   against it`. No auto-unpin; the counts are genuinely zero here (unlike case 3), so the words are safe.
7. **Talents doc absent** — `loadTalentsDoc` (:3060) fails silently and permanently and the pane falls back to the text table: **no
   pin button, no counters, no diff surface at all** — nothing dormant. Showing counts anyway is **refused**: without the doc there
   is no node→pane membership, so the only computable number is the whole-`sel` total, the bimodal one (median 7, p90 36) a hero
   swap inflates to 28+ for zero gain. The doc arriving mid-session calls `renderScreen` (:3070); `screenBaseS` is a module var, so
   the pin survives.
8. **Thin window / sel-less build** — below `CS_THIN` (:3176) `csTalentRows` returns null and the section collapses to `csThinHTML`;
   nothing about the pin renders and the letter keys are inert, but `screenBaseS` survives so moving the lens back restores it (8 of
   3,200 observations). A row without `sel` is neither pinnable nor lightable — the `withSel` guard and the clamp (:3939–3945)
   already exist and `base` respects them.
9. **Orphan nodes** — 0 measured, and the diff creates no new orphan class: every difference that cannot be drawn is **counted and
   named as a ledger row**, never silently discarded. A drop with no pixel to sit on is the one place this feature can lie without
   anyone noticing.

## §9 — Caption wording (verbatim)

- **No base pinned** — append to the existing `.cs-foot` (:3948, otherwise unchanged): `· pin a build as base to diff the others
  against it`. Required, not optional: `base` on the lit chip alone is right for a calm UI but invisible until something is clicked.
- **Base pinned** — replaces the "picked talents in full color, the rest dimmed" sentence: `Base: Build 3 · Templar · 84 pts — Build
  7 shown. + added · − dropped (dimmed one tier, dashed rim, the base's icon and rank shown) · ≠ choice swapped (the base's option
  ghosted bottom-left) · a signed rank pip (1→2) = the rank moved · unmarked = the same in both. Class+Spec: +4 −3 ≠1.` Then the
  median clause (§7.4, verbatim). Keep `builds known for N of M in window`, `identity = exact talent-tree match` and the hash caveat
  (:3949–3957) untouched.

## §10 — Anchors to modify (@ b52d0a4)

| Site | Line | Change |
|---|---|---|
| `let screenBuild` | 2977 | `+ screenBaseS=null; screenDlSort={col:null,dir:-1};` |
| keydown handler | 2997–3013 | `b` / `f` branches; `←`/`→` chip walk on the Talents tab |
| `resetScreenPerSpec` | 3237–3242 | `+ screenBaseS=null;` |
| `.bldchip` CSS | 698–707 | `.bldchip.base` metal fill; `.pbtn`; `.med`; counter slot |
| `#cs-trees` CSS | 711 | `flex-wrap:nowrap; align-items:stretch`; `.fit`/`.ovf` |
| `.ttn.dm` block | 748–755 | diff block sourced **after** it; `.da`/`.dd` hover overrides; `.ttr.rup` / `.ttr.rdn` |
| `csBuildChipsHTML` | 3775–3793 | `base`/`unpin`, counter, `≠` htag prefix, ghost chip, `.med` |
| `csTreePanesHTML` sig | 3794 | `(ctx,trees,row,base)` |
| pane geometry | 3838–3876 | **replaced** per §1 |
| edge predicate | 3883 | three-way `lit` / `wasl` / `newl` |
| node loop | 3889–3907 | `df`/`drop`/`eIdx`/`rk`/`multi`; badge, ghost, pip |
| `.tthead` | 3912–3914 | `.tdf` tally; `.tp` base delta |
| `csTalentsHTML` | 3922–3957 | diff bar, ledger disclosure (sorting via `cmpCells` :4131, unchanged), foot, clamp `-1` |
| chip wiring | 4077–4081 | guard → `closest("[data-copy],[data-base],a")`; `[data-base]`, `[data-flip]`, `[data-clearbase]` delegates mirroring `[data-copy]` at 4083 |

**Size:** ~45 lines CSS, ~150 lines JS net (the geometry rewrite is roughly line-neutral). **Risks, ranked:** (1) CSS source order
on `.dd` (§5) — silent, drop-only. (2) Badge crowding at node 34 with a 30-mark diff — mitigated by opposite corners and the
overhang into padding, and 30 marks *is* the state of that comparison. (3) Edge diff noise — cut first. (4) `≠` glyph — a one-line
`content:` swap if rejected. (5) Base resolved from `T.rows` (§3).
