# Blueprint — Talent Build Diff (base pin)

Owner: "select a talent layout as a base layout, then when clicking other trees should
highlight the additions and subtractions (in a way that it is clear what is an addition and
what is a subtraction)." Chassis: pin one build as BASE by its hash; every other chip then
carries a diff count; clicking a chip paints the diff on the trees it already draws. A closed
disclosure below the trees holds a sortable ledger — the only surface able to state a
difference that has no pixel. Target: `site/index.html`, single file, no build step.

**NO PIPELINE CHANGE IS REQUIRED AND NONE MAY BE ADDED.** Everything consumed already ships:
`sel` on 960/960 builds (`[nodeId,rank]` or `[nodeId,rank,entryIdx]`) and `talents.json.gz`
(`nodes[].id/x/y/r/n/ic/t/s/es`, `edges`). The diff is a map-vs-map over two arrays of ≤81
entries — microseconds per render. A precomputed diff would be 22,080 ordered pairs of
redundant bytes, a second source of truth for something derivable, and would bake UI policy
(hero suppression, class+spec-only counters) into the pipeline. `data.json.gz` /
`builds.json.gz` stay row-aligned and untouched.

## 1. Taxonomy and algorithm

Six categories exhaust the ways two `sel` arrays differ. Base = A, compared = B, keyed by
nodeId. Node ids are unique across the class/spec/hero panes of a spec (0 orphans, 0
duplicates over all 960 builds), so ONE flat map serves all three panes.

| k | condition | live volume |
|---|---|---|
| `add` | id in B, not in A | 123,150 over all ordered pairs; 90% of pairs |
| `drop` | id in A, not in B | 123,150; 91% of pairs |
| `up` | in both, same entryIdx, B.rank > A.rank | 2,267; only on plain `r=2,t=0` nodes |
| `down` | in both, same entryIdx, B.rank < A.rank | 2,267 |
| `swap` | in both, different entryIdx (same or different rank) | 26,378; 70% of pairs |
| shared | in both, same rank, same entryIdx | residue, median ~66 of ~76 |

Rank change and choice swap are disjoint families in live data (0 nodes are both). Test swap
FIRST regardless; if they ever collide, swap wins and `dp` still carries the point move.

```js
function csDiff(baseSel,rowSel){                 // → Map<nodeId,{k,br,bi,r,i,dp}>
  const M=a=>{const m=new Map(); if(a) for(const s of a)
    if(Array.isArray(s)&&s.length>=2) m.set(+s[0],[+s[1],s.length>2?+s[2]:null]); return m};
  const A=M(baseSel), B=M(rowSel), o=new Map(), P=r=>Math.max(1,r);
  for(const [id,b] of B){
    const a=A.get(id);
    if(!a){ o.set(id,{k:"add",r:b[0],i:b[1],dp:P(b[0])}); continue }
    const rec={br:a[0],bi:a[1],r:b[0],i:b[1],dp:P(b[0])-P(a[0])};
    if(a[1]!=null&&b[1]!=null&&a[1]!==b[1]) o.set(id,Object.assign({k:"swap"},rec));
    else if(b[0]>a[0]) o.set(id,Object.assign({k:"up"},rec));
    else if(b[0]<a[0]) o.set(id,Object.assign({k:"down"},rec));
  }
  for(const [id,a] of A) if(!B.has(id)) o.set(id,{k:"drop",br:a[0],bi:a[1],dp:-P(a[0])});
  return o;
}
```

`P(r)=Math.max(1,r)` is the SAME expression as the point accumulator at :3698. Reuse it
literally, never re-derive: per-node `dp` must sum exactly to each pane header's points delta,
and the owner will check. `csPaneOf(trees)` → `Map<nodeId,"cls"|"spec"|"hero">`, one pass over
`trees.cls.nodes`, `trees.spec.nodes` and every tree in `trees.hero`; memoise per spec key.
`csHeroOfSel(sel,trees.hero)` → hero tree name: the first `sel` id found in a hero tree's node
set (verified 960/960 agreement with the modal `R.hero`), so the base's hero is known even
when `state.merge` is off and `row.hero` is undefined.

**THE COUNTING RULE** — applies to every counter (chip, diff bar, pane tally, ledger header):
**adds and drops count CLASS+SPEC panes only; swaps, rank ups and rank downs count the FULL
`sel`, all three panes.** Adds/drops must exclude hero or all seven Paladin|Retribution chips
read `+16 −16` when the truth is "2 to 11 talents plus a different hero tree". Swaps are safe
across all three panes because hero add/drop is either exactly 0 (same hero — measured,
always) or suppressed (cross-hero, where no hero swap can exist). A zero category is OMITTED,
never printed as `+0`.

## 2. Base-pin UX — every control named

State: `let screenBaseS=null;` beside `screenBuild` at **:2969**, holding the build HASH
`r.s`, NEVER the ordinal. `data-bld` is a positional index into `T.rows`, recomputed from the
lens window every render; a base pinned at chip 8 changes position or leaves the row on 59%
of lens moves. Re-resolve every render from `d.vocab.builds.find(b=>b.s===screenBaseS)` — the
per-spec vocab (`screenData:3244`) is 24 static entries the lens cannot touch, so the DIFF
outlives every filter move. A module var, not DOM state, so it survives the `renderScreen()`
that `loadTalentsDoc` fires at **:3062**.

| control | where | label | action |
|---|---|---|---|
| pin | `<button class="pbtn" data-base="<hash>">` inside the LIT chip only | `base` | pin |
| unpin | same button on the pinned chip | `unpin` | `screenBaseS=null` |
| flip | `#cs-diffbar` `<button class="btn sm" data-flip>` | `flip` | swap base ↔ lit |
| clear | `#cs-diffbar` `<button class="btn sm" data-clearbase>` | `clear base` | unpin |

The pin button renders on the lit chip only: you click a chip to look at it, and the offer
appears under the cursor you just used — eight permanent buttons is clutter for a control used
once per session. It does NOT render on `.bldchip.oth`, on a row without `sel`, or when
`TALDOC` is absent. In production every build is `bkind:"hash"` so the `copy` button never
renders (:3583) and `base` takes the slot copy already paid for. Discoverability is closed by
the rest-state hint in §7, which is REQUIRED, not optional. `flip` renders ONLY when the base
build currently has a row in `T.rows`; it sets `screenBaseS` to the lit row's `s` and
`screenBuild` to the old base's row index, and when the base has fallen out of the window the
button is omitted (nothing dormant) — the ghost chip's suffix already says why.

**Do NOT refactor `screenBuild` from ordinal to hash in this change.** It is a real latent bug
(a lens move silently re-points the lit build) touching five call sites — ship it as its own
commit. This feature does not need it.

Keyboard, in the handler at **:2927-2955**, Talents tab only, after the existing
`/^(INPUT|SELECT|TEXTAREA)$/` guard at :2937. `←`/`→` walk the lit build along the chip row —
the branch at **:2938** currently falls through to a bare `return` whenever
`screenTab!=="gear"`, so these keys are free, and holding the base while tapping right is the
feature's primary motion. `b` pins the lit build as base, or unpins if it already is. `f`
flips; no branch is taken when `flip` is not rendered. `Esc` stays swallowed exactly as at
:2929-2934 — clearing the base is a visible button. Chip states are three, separable without
colour vision and with no new hue: idle = `--line1` rim; lit = `--metal-line` rim + `--accent`
label (today's `.bldchip.on`, :693); base = the `--metal` FILL, currently unused on build
chips. Base+lit shows both; the diff bar names the base.

```css
/* .bldchip's transition list (:691) deliberately excludes background — the fill
   lands instantly. Do not "complete" that list; the pin must not animate. */
.bldchip.base{background:var(--metal); color:#241d0e; border-color:var(--metal-line)}
.bldchip.base .bl,.bldchip.base .sh{color:#241d0e}
.bldchip.base .mi,.bldchip.base .htag{color:#4a3c17}
.bldchip .pbtn{padding:.06rem .42rem; font-size:.66rem}
.bldchip.base.off .sh,.bldchip.base.off .mi{color:var(--ink3)}   /* ghost: .na treatment */
```

## 3. Visual encoding

Glyphs: `+` (U+002B), `−` (U+2212, already at :251), `≠` (U+2260), `→` in rank pips only.
**No `⇄`, no arrows/carets/triangles as state markers** (:392). Nothing rotates, nothing
animates, nothing grows or glows on hover (prefs #3, #7). No purple, no new hue.
`--up:#3DDC84` / `--down:#FF6B6B` (:24) stay glyph ink and 1px rims — never a fill.

Badge — one new element on `.ttn`, authored AFTER :746:

```css
.ttb{position:absolute; left:-5px; top:-5px; z-index:2; min-width:15px; height:15px;
  display:flex; align-items:center; justify-content:center; padding:0 .18rem;
  font:700 .66rem/1 var(--font); background:var(--surface2);
  border:1px solid currentColor; border-radius:var(--r1);
  font-variant-numeric:tabular-nums; pointer-events:none}
.d-add .ttb,.d-up .ttb{color:var(--up)}
.d-drop .ttb,.d-down .ttb{color:var(--down)}
.d-swap .ttb{color:var(--accent)}
```

Top-LEFT because `.ttr` owns `right:-5px; bottom:-5px` (:748) — diagonally opposite, they
cannot collide even at the 34px icon floor. At -5/-5 the badge overhangs into the pane's
existing 10px padding instead of covering the icon face. Fixed 15px, never scaled by `--tn`,
so pref #10 is untouched. `pointer-events:none` keeps the icon the sole wowhead surface
(pref #11). Content is the SIGNED POINT MAGNITUDE, emitted in code exactly like `deltaHTML`
(:2353): `+1`, `+2`, `−1`, `−2`, and a bare `≠` for a swap. The number IS the respec cost, and
a pane's badges sum to that pane's points delta, so the surface is auditable.

| k | node classes | badge | tile rim | body |
|---|---|---|---|---|
| add | `pk d-add` | `+N` up | `--up` | full colour, unchanged |
| drop | `dm d-drop` | `−N` down | `--down` **dashed** | THIRD TIER, `opacity:.62` |
| up | `pk d-up` | `+N` up | `--up` | unchanged; pip `a→b` |
| down | `pk d-down` | `−N` down | `--down` | unchanged; pip `a→b` |
| swap | `pk d-swap` | `≠` accent | **UNCHANGED** | picked option fronts; ghost bottom-left |
| shared | `pk` | none | `--metal-line` | byte-identical to today |

**Swap gets no rim change, deliberately.** `--accent` #E8BC57 against the picked rim
`--metal-line` rgba(242,207,118,.55) is champagne on champagne — the change would be
invisible and claiming it as a channel would be false. The `≠` badge and the ghost carry
swap, and both are shape. Same reason `--amber` is refused there. **The swap marker is a
badge on `.ttn`, never a modification of the octagon**: `.ch` is gated on `n.t===2` at
**:3700** while 122 of 1,044 measured swaps sit on `t=1`/`t=3` nodes.

**The third state.** A dropped node keeps `dm` (so `.ttn.dm .tico img{filter:grayscale(1)
brightness(.55)}` still applies — it genuinely is not in this build) and adds `d-drop`. Four
independent channels fire at once, only one of which is colour: (1) luminance tier .62,
between untaken .42 and picked 1.0; (2) dashed rim, where untaken is solid `--line1` and
picked is solid `--metal-line` — achromatic, survives greyscale and every form of colour
blindness; (3) the `−N` badge in a slot untaken nodes leave empty; (4) IDENTITY — the tile
fronts the BASE's option, its icon, name and spell link. You are looking at what you lose.

```css
/* MUST be authored AFTER :741-746. .ttn.dm .tico and .ttn.d-drop .tico are both
   (0,2,1); source order alone decides. Get this wrong and every drop silently
   loses its rim — the most likely bug in this build. */
.ttn.d-drop{opacity:.62}                                   /* tier: .42 → .62 → 1.0 */
.ttn.d-drop .tico{border-color:var(--down); border-style:dashed}
.ttn.d-drop .tico.ch{background:rgba(255,107,107,.55)}     /* octagon rim = background;
   it CANNOT be dashed (border:none, :730). Tier + badge + colour carry it. Say so. */
.ttn.d-add  .tico{border-color:var(--up)}   .ttn.d-add  .tico.ch{background:var(--up)}
.ttn.d-up   .tico{border-color:var(--up)}   .ttn.d-up   .tico.ch{background:var(--up)}
.ttn.d-down .tico{border-color:var(--down)} .ttn.d-down .tico.ch{background:var(--down)}
/* hover hole: :746 repaints ANY .dm octagon champagne on hover, erasing a drop
   marker at the moment the reader looks hardest at it. Still colour-only hover. */
.ttn.d-drop a.whico:hover .tico{border-color:var(--down)}
.ttn.d-drop a.whico:hover .tico.ch{background:rgba(255,107,107,.55)}
.ttn.d-add a.whico:hover .tico.ch,.ttn.d-up a.whico:hover .tico.ch{background:var(--up)}
.ttn.d-down a.whico:hover .tico.ch{background:var(--down)}
/* swap ghost — 15px greyscale thumbnail of the base's option, bottom-left */
.ttn .dgh{position:absolute; left:-4px; bottom:-4px; z-index:3; width:15px; height:15px;
  border-radius:var(--r1); border:1px dashed var(--down); overflow:hidden;
  background:var(--surface2); pointer-events:none}
.ttn .dgh img{width:100%; height:100%; object-fit:cover; filter:grayscale(1) brightness(.8)}
.ttcv line.wasl{stroke:rgba(255,107,107,.26); stroke-width:1.2; stroke-dasharray:3 3}
.ttcv line.newl{stroke:rgba(61,220,132,.28); stroke-width:1.4}
.tthead .tdf{font-size:.68rem; font-variant-numeric:tabular-nums}
.tdf .a{color:var(--up)} .tdf .d{color:var(--down)} .tdf .s{color:var(--accent)}
```

The ghost images `n.es[df.bi]`'s icon: "was that, now this", stated on the map with no hover.
`onerror="this.remove()"` as elsewhere; on octagons `bottom:1px; left:calc(50% - 7.5px)`.
`.ttr` (bottom-right) and `.dgh` (bottom-left) never coincide. **Do NOT put a `title` on
`.dgh`** — it is `pointer-events:none` and the tooltip could never fire; append the base
option's name to the `.ttn` div's existing `title` (`Lava Burst · base had Frost Shock`).

Rank pips (`.ttr`, :3711): no base pinned, or shared/swap/add → today's `s[0]+"/"+n.r` on
picked multi-rank nodes. Rank up/down → `a→b`, or `a→b/r` when `n.r>2`, with
`.ttr.ru{color:var(--up); border-color:var(--up)}` and `.ttr.rd` the mirror in `--down`. Drop
on a multi-rank node → `N→0` in `.ttr.rgone{color:var(--down); border-color:var(--down);
border-style:dashed}`; a single-rank drop prints no pip. Edges (:3684-3688):
`const lit=sel.has(a.id)&&sel.has(b.id)` becomes three-way against a base selection map `bsel`
built the same way — `lit` (both), `wasl` (base only), `newl` (this only). This is the
connective tissue; an added limb reads as a limb, not scattered pips. It is also the FIRST
THING TO CUT if it reads as noise at the 30-mark worst case — the badges carry the feature
alone.

Off the tree: a fourth chip `.mi` slot `<i class="d-add">+</i>4 <i class="d-drop">−</i>3
<i class="d-swap">≠</i>1`, tabular-nums, per the §1 counting rule, with the base's own chip
printing the word `base` there. The `.htag` gains a `≠` prefix in `--accent` on any chip whose
hero differs from the base's — one glyph says "this costs a hero respec" before a click, and
on Paladin|Retribution that is 6 of 7 chips. `.tthead` (:3713) gains
`<span class="tdf">+4 −3 ≠1</span>` between `.tn2` and `.tp` at zero added height (it is an
existing single-line baseline flex row with `.tp` on `margin-left:auto`), and `.tp` extends to
`82 pts · base 84 −2` with the delta in `.delta.down`.

## 4. `#cs-diffbar` — one line, between the chips and the trees

Rendered only when a base is pinned; `display:flex; gap:.6rem; align-items:baseline;
font-size:.74rem; margin:.1rem 0 .55rem`. The reader crosses it on the way down to the trees,
which is why the honesty notes of §6 live here and not in a foot line below three panes.
`DIFF` uses the `.tthead .tl` micro-label treatment (.6rem, uppercase, .14em, `--ink3`);
buttons right-align via `margin-left:auto`. The longest note state is ~72 characters against a
~1116px measure — one line, always.

```
DIFF   Build 3 ≠ Build 7   +4 −3 ≠1   <note>                      [flip] [clear base]
```

## 5. The ledger — closed disclosure below the trees

Trigger, using the existing static marker mechanism (`content:"−"` open / `"+"` closed,
:250-252 — nothing rotates): `<div id="cs-dltrig"><span class="mk"></span>Diff ledger ·
8 changes</div>`. Open state keyed `"taldiff"` in `screenSecClosed`, **default closed** — add
it to the initialiser at :2966 and to `resetScreenPerSpec` (:3212), and wire one handler in
`wireScreen` toggling the key and calling `renderScreen()`. Closed cost ~28px. It sits BELOW
the trees, so it never enters the tree layout solver and **`capH` (:3664) is NOT reduced.
Pinning a base must never shrink the trees** — the owner's request is about the trees; a
feature that degrades its own subject to make room for its index has the priority inverted.

`.tblwrap` + `table.data`. Six columns, **every one sortable through `cmpCells` (:3927)** per
pref #12, following :2676-2710 verbatim (`<th data-c>`, the `.sorted .desc/.asc` edge rule, a
module `csDiffSort={col:"pane",dir:1}` toggled on click).

| col | key | content | sort value |
|---|---|---|---|
| Δ | `k` | glyph AND word: `+2 ADD` · `−1 DROP` · `+1 RANK` · `−1 RANK` · `≠ SWAP` | category ordinal ADD 5, RANK+ 4, SWAP 3, RANK− 2, DROP 1 |
| Talent `.txt` | `name` | node name; icon on add/drop/rank rows | name string |
| Base | `br` | `2/2`, `—`, or the base option's icon + name on swap rows | base rank, `null` for `—` |
| This build | `r` | `1/2`, `—`, or this build's icon + name | this rank, `null` for `—` |
| Pane `.txt` | `pane` | `Class` / `Spec` / `Hero` / `Hero (not shown)` | pane ordinal |
| Δpts | `dp` | `+1` `−2` `0` in `.delta.up/.down` | signed integer |

Read left to right and the direction is a sentence — `− DROP | Ravager | 1/1 | — | Spec | −1`
— with one side a literal `—`, a shape, not a colour. `cmpCells` parks `null` last in both
directions, exactly right for the dashes. Icon rule (pref #11): add/drop/rank rows carry one
icon in **Talent**; swap rows put the node name as plain text in Talent and the TWO icons in
**Base** and **This build**, because for a swap the spell identity is the before/after — swap
rows are then the only rows with icons on the right, so shape reinforces `≠` for free. Default
order: pane, then node `y` — the reading order of the tree, so ledger and map agree; no `th`
carries `.sorted` until the owner clicks one. Apply `max-height` + `overflow-y:auto` **only
when rows > 10** (unconditional overflow gives a one-row ledger a stray scrollbar); `.data th`
is already `position:sticky; top:0` (:384), so the sticky header arrives free the moment the
wrap scrolls. `.dl-t{min-width:820px}` overrides `table.data{min-width:700px}` (:383) so Base
and This build cannot collapse into each other. Any explanatory note goes **above** the
`.tblwrap` as a `.dl-note` div — never a spanning `<tr>`, which would break the sort and
therefore pref #12.

## 6. Edge cases — all mandatory

**A. Cross-hero base** (121 of 920 build-1 pairs; 6 of 7 Ret chips). No node-level hero diff
is attempted: the base's 14 hero nodes have no coordinates in this pane and vice versa, so the
diff would be exactly 14 adds + 14 drops every time — "different hero tree" written 28 times.
Pass `dmap=null` for the hero pane; its 14 nodes render as plain `.pk`. Class and Spec diff
fully and still carry real signal (median 14, min 1, max 24 marked nodes; 0 cross-hero pairs
had identical class+spec trees). The hero header states the substitution in the same markers —
`<span class="tdf"><i class="d">−</i> Templar</span>` after `.tn2` — and `.tp` reads `14 pts ·
base 14 · different tree`. One `.dl-note` above the table: `≠ HERO TREE — base Templar → this
build Herald of the Sun · all 14 hero talents differ; node-level diff suppressed (different
trees, nothing to compare)`. Counters exclude hero add/drop by the §1 rule, so Ret chips read
`+2 −2 ≠2`, the truth.

**B. Same-hero, merge on.** The hero pane diffs normally, but only `swap` can ever fire there
(measured: hero add/drop across all same-hero ordered pairs is exactly 0 — every build in a
hero tree allocates the identical 14 nodes). With no swap the header reads `identical` in
`--ink3`; the measurement proves nothing else can hide, so the word is safe.

**C. Merge off / hero-zoomed — THE SILENT LIE.** `csTreePanesHTML:3600` gates the hero pane on
`state.merge && row.hero`, and `row.hero` is only computed under merge (`csTalentRows:3283`),
so no hero pane is drawn. **The diff is computed over the FULL `sel` regardless.** For 15 of
920 build-1 pairs (112 ordered pairs) the entire difference is a hero-tree choice swap, and
today those render as two byte-identical pane pairs. Diff-bar note, verbatim: `identical in
Class and Spec · 1 hero-tree choice differs (hero pane not shown in this hero-zoomed view)` —
in `--ink2`, not `.na` grey; it is a finding, not an absence. The ledger carries it as a real
row with Pane = `Hero (not shown)` in `.na` ink. Amend the caveat at **:3752-3755** from "hero
fixed — builds differ in class/spec trees only" to `hero fixed — differences in class/spec
trees are drawn; hero-tree choices are counted but not drawn here`. **Do not add a hero pane
to merge-off mode**: the frame identity is per-hero there by design and pref #10 forbids
buying the width back by shrinking anything.

**D. Base leaves the slice** (3,200 lens observations: 76.2% still on the chips, 18.9% folded
into `other`, 4.6% gone; a chip-1 base survives 99.2%, chip-8 only 40.6%). The diff never dies
— the base's `sel` is in the static vocab; its STATISTICS do. Render a ghost
`.bldchip.base.off` **forced to the head of `#cs-bldchips`**, keeping name, hero tag and
`unpin`, with share/median/n as `—` in the `.delta.na` treatment (:306) and a trailing `.mi`
reading `below top 8 in this window` (rows exist in the `byV` map `csTalentRows` builds at
:3270-3275 but folded into `other`) or `0 runs in this window` (absent from `byV`). Never a
stale number from the previous slice. **Never silently unpin** — the owner moved the lens, not
the base.

**E. Spec change.** `screenBaseS=null` joins the existing resets in `resetScreenPerSpec`
(**:3209-3213**) — one line. Forced: the 960 hashes are globally distinct with 0 collisions,
so a stale base would resolve to nothing and render a diff against a blank. A merge on/off
flip on the SAME spec KEEPS the base: `sel` is unchanged and its hero tree is derivable from
`sel` in either mode.

**F. Base == compared.** All six categories are zero by definition. Render the trees exactly
as today — `.pk`/`.dm`, no badges, rims, ghosts, `wasl`/`newl`, zero counters or ledger table.
Diff bar: `Build 3 is the base — shown plain · click another chip to diff against it`. No
auto-unpin. The counts are genuinely zero here (unlike C), so the words are safe.

**G. `TALDOC` absent** (`loadTalentsDoc` :3052-3066 fails silently and permanently). No panes
render, so no `base` button, no diff bar, no counters, no ledger — nothing dormant. Refuse the
temptation to put whole-`sel` counts on the text table: without the doc there is no node→pane
map, so adds/drops cannot be split from hero, and the only available number is the bimodal
whole-`sel` total (median 7, p90 36) that a hero swap inflates to 28+ for zero gain — a
misleading number is worse than none. TALDOC arriving mid-session calls `renderScreen` (:3062)
and `screenBaseS`, being a module var, survives.

**H. Thin window / sel-less build.** Below `CS_THIN` (:3148) `csTalentRows` returns null
(:3269) and the section collapses to `csThinHTML`; nothing about the pin renders, but
`screenBaseS` survives so moving the lens back restores it. A row without `sel` gets no `base`
button and cannot be compared — the `withSel` guard (:3742) and the clamp (:3743-3746) already
exist and must be respected (0 occurrences in production).

**I. Orphan ids.** 0 measured, and `csTreePanesHTML` already ignores unhoused ids silently
(:3606-3607), which stays correct for the compared build. But the diff CREATES real entries
with no home (A and C): they must be COUNTED and SAID IN WORDS, never discarded. A drop with
no pixel to sit on is the one place this feature can lie unnoticed.

## 7. Caption / copy — exact strings

- Rest state, appended to the existing `.cs-foot` at **:3749-3756** when no base is pinned
  (REQUIRED — this is the discoverability of the whole feature):
  `· pin a build as base to diff the others against it`
- Foot legend with a base pinned, replacing the "Build N lit — picked talents in full color,
  the rest dimmed" sentence: `Base: Build 3 · Templar · 82 pts — Build 7 shown. + added ·
  − dropped (dimmed one tier, dashed rim, base's icon and rank shown) · ≠ choice swapped
  (base's option ghosted bottom-left) · a→b = rank changed · unmarked = same in both.`
- Diff bar, all six categories zero: `no differences — identical selections` (printable only
  when the FULL-`sel` diff is empty).
- Ledger trigger: `Diff ledger · 8 changes` / `Diff ledger · 1 change`.

## 8. Anchors to modify (`site/index.html`)

| line | change |
|---|---|
| :24, :250-252, :304-306, :345, :392-394 | tokens/precedents reused as-is; no edits |
| after :746 | ALL new diff CSS (`.ttb`, `.dgh`, `.d-*`, `.ttr.ru/.rd/.rgone`, `line.wasl/.newl`, hover overrides). Source order is load-bearing — §3. |
| :693-694 | add `.bldchip.base`, `.bldchip.base.off`, `.bldchip .pbtn` |
| :2927-2955 | `b` / `f` key branches; use the dead `←`/`→` fallthrough at :2938 |
| :2966 | add `"taldiff"` to the default `screenSecClosed` set |
| :2969 | `let screenBaseS=null;` |
| :3209-3213 | `screenBaseS=null;` in `resetScreenPerSpec` |
| new, near :3068 | `csDiff`, `csPaneOf`, `csHeroOfSel` |
| :3576-3594 | `csBuildChipsHTML(T,base,diffs)` — `.base` class, `pbtn`, counter `.mi`, `≠` htag prefix, hoisted ghost chip |
| :3595 | `csTreePanesHTML(ctx,trees,row,base)` — one extra argument |
| :3608-3611 | build `bsel` from the base with the identical three-line idiom |
| :3684-3688 | three-way edge predicate (`lit` / `wasl` / `newl`) |
| :3690-3699 | `eIdx = picked ? s[1] : (df&&df.k==="drop" ? df.bi : null)`; `multi = (picked \|\| (df&&df.k==="drop")) && +n.r>1`; **`pts` stays gated on `picked` alone**. All three currently gate on `picked`, so a dropped node would otherwise render the generic first-entry glyph with no pip. |
| :3708 | third class token beside `pk`/`dm`; `.ttb` span; `.dgh` span; pip class; base option appended to `title` |
| :3713-3715 | `.tdf` tally between `.tn2` and `.tp`; `.tp` gains `· base N ±D` |
| :3741-3756 | `#cs-diffbar`, `#cs-dltrig` + ledger, foot rewrite |
| :3875-3892 | extend the guard to `e.target.closest("[data-copy],[data-base],a")`; add `[data-base]`, `[data-flip]`, `[data-clearbase]`, `#cs-dltrig` handlers mirroring the `[data-copy]` shape with `e.stopPropagation()` |
| :3927 | `cmpCells` reused unchanged by the ledger |

Budget: ~55 lines CSS, ~150 lines JS, one module var, two one-line reset edits.
