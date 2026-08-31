# Talent diff — visual pass 3 (SPECIFICATION ONLY; this workflow does not edit site/index.html)

Surface: the character-screen Talents pane — `csTreePanesHTML`, `csTalentsHTML`, the `#cs-trees` CSS block. Everything is addressed by
FUNCTION NAME and CSS SELECTOR so it survives the concurrent upgrade-surface merge. The talent build LEDGER (`csDiffLedgerHTML` markup,
`#cs-dl` headers, `sortState/sortHead/sortRows/wireSort`) is NOT touched by this pass.

## 0. What is actually broken (measured — do not re-tread)
1. **24% of marks never render.** `.tico.ch{border:none; clip-path:…}` makes `border-width:2px` inert and clips the ring away: 21/21 swaps
   and 10/109 adds+drops ship ~2% perimeter ink. The encoding must leave `.tico` entirely.
2. **Direction lives in hue.** `--up` vs `--down` 1.56:1 (1.29:1 deuteranopic); `--up` vs `--accent` 1.00:1, ΔL* 0.0. The hue-free fallback
   is 6px² of glyph ink.
3. **The opacity tiers are inert.** marked vs still-taken 1.18:1; 48% of still-taken nodes are brighter than the median marked node —
   opacity multiplies art spanning ~7x in luminance.
4. **The mark sits where there is no room.** Badge top edge `ico.y−7` = the bottom edge of the node above, every spec. Median
   badge-to-neighbour clearance over 130 real marks: **0.00px**; 54% touch or overlap.
5. **Signal is 3% of a loud field** (46.7% of the canvas is saturated spell art). 6. **3 of 130 marks paint under a stacked sibling** —
   counted in the tally, invisible on screen.

**Resolution: prominence is a RATIO.** The field goes quiet, the mark moves off the contested seam onto a plate of its own, direction is
carried by position + mass + polarity + texture, and total canvas ink goes DOWN while ink on the marks goes UP. A third size increase would
worsen (4) and touch none of (1), (2), (3).

## 1. Geometry
One token changes in `csTreePanesHTML` — the fit loop's start rung: `for(let nd=44; nd>=34; nd-=2)` → `for(let nd=40; nd>=34; nd-=2)`.
Nothing else in the solver moves (`cp=nd+4`, `boxW`, `headW`, the slack spend, `capH`, `rowPitch`, `H`, `ladder()`, the fit/ovf logic,
`overflow-x:auto`). `boxW` is monotone in `nd` and the sum already fit at 44 with 12–14px spare in 12/12 specs, so 40 is taken on the first
rung in 12/12 and `#cs-trees` still gets `fit` (centred). `availW` is unchanged — `$("charscreen").clientWidth` = 1116px (pref #2), never
the viewport.

| | today (44px) | this pass (40px) |
|---|---|---|
| row pitch → **vertical gutter**, 12/12 specs | 51 → **7px** | 52 → **12px** |
| col pitch → gutter, four 9-column specs | 49 → **5px** | 49 → **9px** |
| … seven 7-column specs | 55 → 11px | 56 → **16px** |
| … DH Havoc | 63 → 19px | 66 → **26px** |
| diagonal corner-to-corner | 8.6 / 13.0 / 20.3px | 15.0 / 20.6 / 28.7px |
| canvas height `H` | 554 | 560 (+6px) |

Also `.ttpane{padding:7px 10px 10px}` → `9px 11px 10px` with `const CHROME=22` → `24`, and `#cs-trees{padding:2px 1px 6px}` → `5px 4px 8px`,
so a row-0 / column-0 plate is neither clipped by `overflow-x:auto` nor sitting on the pane border.

**Pref #10, stated explicitly.** #10 forbids shrinking type or icons TO BUY FIT. (a) Nothing is bought — the layout already fit at 44 with
12–14px spare; 4px of icon edge becomes 5px of white space, the opposite trade. (b) **No type shrinks anywhere**: `.ttb` stays `.82rem`
(13.12px) and goes 700→800, `.ttr` stays `.68rem` (10.88px), headers untouched. (c) 40px is two rungs above the band the code itself calls
readable (floor 34: "pref #10: 34px icon floor, never below"), and `tv_sizes_1to1.png` shows 44/40/36 at true 1:1, dSF 1 — at 40 the art is
fully identifiable, 36 is where marks out-mass it. (d) The thing the owner is trying to read gets BIGGER: mark ink per marked node rises
from ~66px² legible to ~370px², and occlusion of the art falls from up to 32.8% to 10.6%.

## 2. The mark set
**One object, one job.** PLATE = *a change happened here*. TICK = *which way, and how big a move*. CHIP = *which way, in the owner's own +
and −, with no hue at all*. PIP = *how much, here* — and only where it states arithmetic.
Add one token to the `:root` §GG materials group: `--plate:#2E2A21;  /* §GG: raised node plate — one warm-graphite step above --surface2 */`
— 3.16:1 against `--surface1`, so it is visible AS A SHAPE. (Today's badge pill is `--surface2` on `--surface1` = 1.09:1, measurably not a
shape.) No new hue; pref #6 intact.

### 2.1 Plate + tick — drawn on `.ttn`, never on `.tico`
    .ttn.dv{z-index:3}   /* a mark is never buried by an unmarked stacked sibling */
    #cs-trees.diff .ttn.dv::before{content:""; position:absolute; inset:-3px; z-index:-1; pointer-events:none;
      border-radius:var(--r1); background:var(--plate);
      box-shadow:0 0 0 1px rgba(0,0,0,.55), 0 2px 6px -1px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.07)}  /* §GG lip */
    #cs-trees.diff .ttn.dv::after{content:""; position:absolute; inset:-3px; z-index:2; pointer-events:none;
      border-radius:var(--r1); background-repeat:no-repeat}
    #cs-trees.diff .ttn.da::after{background-image:linear-gradient(var(--up),var(--up));
      background-size:62% 4px; background-position:top 1px center}
    #cs-trees.diff .ttn.dd::after{background-image:repeating-linear-gradient(90deg,var(--down) 0 6px,transparent 6px 10px);
      background-size:62% 4px; background-position:bottom 1px center}
    #cs-trees.diff .ttn.drk::after{background-size:34% 4px}                     /* rank move = short tick */
    #cs-trees.diff .ttn.ds::after{background-image:linear-gradient(var(--up),var(--up)),
      repeating-linear-gradient(90deg,var(--down) 0 6px,transparent 6px 10px);
      background-size:62% 4px, 62% 4px; background-position:top 1px center, bottom 1px center}
LOAD-BEARING, ship it as a comment: `::before{z-index:-1}` needs `.ttn`'s own `z-index` to keep establishing a stacking context (1 today, 3
when marked) or the plate falls behind the SVG lattice. The tick sits 1px inside the plate's dark ring, so two vertically adjacent ticks are
separated by ring + 6px of pane + ring and cannot read as one ladder rung. Ink: a 46x46 lifted tile at 3.16:1 plus ~114px² of tick — `--up`
at 8.03:1 or `--down` at 5.16:1 against the plate. **Zero occlusion of the icon**: the plate is BEHIND the tile.

### 2.2 The chip (`.ttb`, top-left) — the + and −, monochrome BY DESIGN
    .ttb{position:absolute; left:-3px; top:-3px; z-index:4; pointer-events:none; width:16px; height:16px; box-sizing:border-box;
      padding:0; display:flex; align-items:center; justify-content:center; border:0; border-radius:var(--r1);
      font:800 .82rem/1 var(--font); font-variant-numeric:tabular-nums}
    #cs-trees.diff .ttn.dv .ttb{background:var(--ink); color:var(--bg0); box-shadow:0 0 0 1px rgba(24,21,17,.85)}   /* SOLID = present */
    #cs-trees.diff .ttn.dd:not(.pk) .ttb{background:var(--bg0); color:var(--ink);
      box-shadow:inset 0 0 0 2px var(--ink), 0 0 0 1px rgba(24,21,17,.85)}                                          /* HOLLOW = gone */
The chip carries NO semantic hue — `--ink` / `--bg0` only. It is the channel that must survive greyscale, so it is drawn in greyscale; hue
lives on the tick and the rim as a redundant accelerator. That also keeps the language's `semantic — text-only deltas, never fills` rule for
the block fill; the coloured tick is a state TICK, the mechanism pref #3 / §15.9 already sanctions ("static marker swap … or tick
length/color"). Contrast: chip 11.2:1 vs plate, glyph 14.2:1 vs chip. Mass: solid ~256px², hollow ~112px² as a ring round a dark hole — 2.3x
plus a polarity inversion (today's entire non-hue signal is 6px²). The chip overhangs exactly 3px, like the plate, so it covers 13x13 =
169px² = **10.6% of the 1600px² icon**, in the corner (today's badge 10.5%; today's swap, badge+ghost+pip, 32.8%).

### 2.3 Per state, exhaustively
| state | classes | tick | chip | art | rim | pip |
|---|---|---|---|---|---|---|
| ADDED | `pk dv da` | top, solid `--up`, 62% | solid `+` | full colour | champagne | none |
| RANK-UP | `pk dv da drk` | top, solid `--up`, 34% | solid `+` | full colour | champagne | `1→2`, neutral |
| DROPPED | `dv dd` (no `pk`) | bottom, segmented `--down`, 62% | **hollow** `−` | full colour, `filter:none` | `--line2` | none |
| RANK-DOWN | `pk dv dd drk` | bottom, segmented `--down`, 34% | solid `−` | full colour | champagne | `2→1`, neutral |
| CHOICE-SWAP | `pk dv ds` | BOTH ticks | solid `≠` | full colour | champagne | none |
| still-taken | `pk` | — | — | `saturate(.28) brightness(.78)` | `--line1` | neutral |
| never-taken | `dm` | — | — | `grayscale(1) brightness(.40)`, `opacity:.55` | `--line0` | — |

Learnable at a glance: **hollow chip ⇔ not in this build**; **solid tick = gained, segmented = lost**; **short tick = same talent, different
weight**; **two ticks = the option changed**. A DROP KEEPS FULL COLOUR AND FULL OPACITY — it is a finding, not background. The owner already
rejected a dimmed drop; do not reintroduce one.

### 2.4 Composition
- **Marked AND a choice node (octagon).** Plate behind, tick on the plate — `clip-path` cannot touch either. **No diff rule ever colours
  `.tico`'s border or background**: that whole failure class is deleted, not repaired. The rim uses the shipped mechanism, which works on
  both shapes, and the champagne rim is REASSIGNED rather than added — in diff mode only marked-and-taken nodes wear it, a 4.27:1 signal
  acquired for zero new ink:

      #cs-trees.diff .ttn.pk .tico{border-color:var(--metal-line)}      #cs-trees.diff .ttn.pk .tico.ch{background:var(--metal-line)}
      #cs-trees.diff .ttn.pk:not(.dv) .tico{border-color:var(--line1)}  #cs-trees.diff .ttn.pk:not(.dv) .tico.ch{background:var(--line1)}
      #cs-trees.diff .ttn.dd:not(.pk) .tico{border-color:var(--line2)}  #cs-trees.diff .ttn.dd:not(.pk) .tico.ch{background:var(--line2)}
- **Swapped AND multi-rank.** The `≠` chip owns the top-left, the pip is suppressed (§2.5); any point move is stated by the pane `.tp` delta
  and by the strip card.
- **Marked AND stacked.** `.ttn.dv{z-index:3}` beats unmarked siblings; DOM order (edit A7) paints every marked node last, the only fix for
  a mark stacked under ANOTHER mark.
- **Cross-hero Hero pane** (`heroSub`, `dmap` null). It opts OUT of the quiet field so it cannot read as dead grey: emit
  `<div class="ttpane nodx">` plus `#cs-trees.diff .ttpane.nodx .ttn.pk .tico img{filter:none}` and
  `…nodx .ttn.pk .tico{border-color:var(--metal-line)}`. The substitution is stated by the existing `.tthead` `− Old / + New` line and by the
  strip's first card.
- **Hover (pref #7).** DELETE `.ttn.da a.whico:hover .tico` and `.ttn.dd a.whico:hover .tico` (and their `.tico.ch` variants) — a marked rim
  is a statement, not a control. The cue moves to the plate: `#cs-trees.diff .ttn.dv:hover::before{box-shadow:0 0 0 1px var(--accent-line),
  0 2px 6px -1px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.07)}` — colour only, identical geometry, nothing grows, glows, moves or
  rotates. Unmarked nodes keep today's champagne hover.
- **Wowhead (pref #11), unchanged.** `::before`, `::after`, `.ttb` and `.ttr` are all `pointer-events:none`; `<a class="whico">` still wraps
  the whole 40x40 `.tico` and remains the sole hover/click surface.

### 2.5 The pip
`.ttr` moves to `right:-3px; bottom:-3px` — inside the plate footprint, right edge at `ico.x+43` against the next node at `ico.x+49` = 6px
clear (today exactly 0px in every 9-column spec) — and is suppressed on plain adds, drops and swaps, where it said nothing the chip does not.
It survives on rank moves (`1→2`) and on unchanged taken nodes, neutral-inked; `.ttr.rup/.rdn/.dpip` semantic inks are neutralised inside
`#cs-trees.diff` so no third and fourth red/green token lands on one node. Revert lever: one boolean in `multi` (edit A5).

    #cs-trees.diff .ttr{color:var(--ink2); border-color:var(--line2); background:var(--surface2)}
    #cs-trees.diff .ttn.dv .ttr{color:var(--ink)}

## 3. Colour-independence — the testable claim
**Claim: with `filter:grayscale(1)` on `#cs-trees`, a reader can name every mark as added, dropped, rank-moved or swapped, at 100% zoom, at
1920x1080, with no legend.** Four hue-free channels, none of which requires green to be separated from red:
1. **POSITION** — tick at the plate's top edge (gain) or bottom edge (loss), 46px apart. Preattentive; perfectly preserved under every
   dichromacy and in greyscale. Today's equivalent is 6px² of glyph ink.
2. **POLARITY / MASS** — chip solid (light field, dark glyph) vs hollow (dark field, light ring and glyph): 2.3x ink mass and an inverted
   figure/ground at 11.2:1 and 14.2:1, both pure luminance relationships.
3. **TEXTURE** — gain ticks solid, loss ticks segmented (6 on / 4 off), at 4px height rather than a 2px dashed rim.
4. **SILHOUETTE and LENGTH** — a swap is the only mark with two ticks, so it cannot be read as either parent state (today its champagne badge
   is luminance-identical to `--up`, 1.00:1, so a swap is currently not separable from an addition in greyscale at all); a rank move is the
   only mark with a 34% tick.
Plus the binary presence of a plate, which no source-art variance can defeat, and the words in the strip (§6). Colour is redundant
confirmation only: `--up` 8.03:1 and `--down` 5.16:1 against the plate both clear 3:1 as marks, and the 1.56:1 ratio BETWEEN them is now
load-free. Not claimed: rank-up is not separable from a fresh add by chip alone — tick length and the pip carry that, as today.

## 4. What is REMOVED or quieted ("clean" was the owner's first word)
1. **`.ttg`, the ghost thumbnail** — CSS and the `ghost=` construction. 484px² each, 256px² of it on the node's own art, below
   icon-recognition threshold; it reads as a grey smudge. The base's option is stated in the node `title`, in the strip card (two 24px icons,
   base → now) and in the ledger — in forms that can be read.
2. **All three `box-shadow 0 0 0 3px` diff rings** — 2.22 / 1.93 / 2.55:1, all below the 3:1 non-text floor, and clipped away on octagons.
3. **The dashed `--down` rim, `border-width:2px`, and the three `.tico.ch{background:var(--up|--down)}` rules** — the entire "colour the
   tile's own perimeter" mechanism, i.e. cause (1).
4. **The magnitude digit on the chip** — 78% of 130 measured badges were ±1, and the digit is what forced a wide pill into the 7px gutter.
   Magnitude lives in the pip, the pane `.tp` delta, the `title` and the strip.
5. **The champagne `.pk` rim on ~95 unmarked nodes per pane, in diff mode** — at 4.27:1 the loudest object on the canvas, carrying no diff
   information. Reassigned (§2.4). 6. **~95 rank pips neutralised per pane**, and every champagne pip ink, in diff mode.
7. **All semantic colour in the edge lattice**, `#cs-trees.diff` only: `line{stroke:rgba(234,227,208,.06)}`,
   `line.lit{rgba(234,227,208,.16); 1.2}`, `line.newl{rgba(234,227,208,.28); 1.4}`, `line.wasl{rgba(234,227,208,.28); 1.4;
   stroke-dasharray:4 3}`. 83 champagne strokes at 3.34:1 and 19 green `newl` strokes stop competing (they wore the addition's hue as long
   strokes the eye follows); `wasl`, at 1.54:1 a channel that did not exist for a reader, becomes real and is distinguished by TEXTURE, not
   hue. Marked nodes are then the only chromatic objects on the canvas. Outside diff mode the lattice is untouched.
8. **The three opacity tiers, as a mechanism** (§5).
Added: one plate, one tick and one chip per marked node — 3 objects on a median diff (median 3 marks) against roughly 200 removed. Total
canvas ink falls; ink on the marks roughly triples.

## 5. The tiers
Opacity is deleted as the tier channel. The primary channel becomes **chroma** — marked nodes are the only chromatic objects in the pane, and
chroma is immune to the art's ~7x luminance spread — plus the **binary presence of a plate**. Luminance is a secondary trim only. The filter
goes on `.tico img`, NEVER on `.ttn`: the plate, tick, chip, pip and rim must keep their own ink. The build's shape stays fully legible in
grey; it only surrenders its chroma.

    #cs-trees.diff .ttn.pk:not(.dv){opacity:1}
    #cs-trees.diff .ttn.pk:not(.dv) .tico img{filter:saturate(.28) brightness(.78)}
    #cs-trees.diff .ttn.dm{opacity:.55}
    #cs-trees.diff .ttn.dm .tico img{filter:grayscale(1) brightness(.40)}
    #cs-trees.diff .ttn.dv .tico img{filter:none}

## 6. The change strip (`#cs-changes`) — what no mark can say: WHICH talent
New `csChangeStripHTML(base,row,paneOf,trees)`, called from `csTalentsHTML` between the `csDiffBarHTML(...)` and `csTreePanesHTML(...)` calls
(`if(base) h+=csChangeStripHTML(...)`); returns `""` when `base.s===row.s`. It rebuilds from `csDiffMaps(csSelMap(base.sel),
csSelMap(row.sel))` + `csNodeIndex(trees)` — the two functions the ledger already uses — so it holds no state and cannot drift from the trees,
chips, pane tallies or ledger. Reuse `csDiffLedgerHTML`'s row loop in structure: same `optOf`/`nmHTML` resolution, same cross-hero skip, same
cross-hero substitution row, same default order (`po`, then node `y`, then `o`) so left-to-right on the strip is top-to-bottom in the panes.

    <div id="cs-changes"><span class="tl">Changed</span><span class="ccnt">5</span>
      <div class="crail"><button class="ccard da" data-nd="12345">…</button>…</div></div>

Card: the same solid/hollow `.ttb` chip at true size, then a 24px `.cico` of the resulting talent (the base's on a drop; on a swap the base's
at `filter:grayscale(1) brightness(.7)`, then `→`, then the new one), `<b class="cnm">` name at `.74rem var(--ink)`, `<span class="cdt">`
detail at `.68rem var(--ink2)` ("added 2/2", "dropped · base had 2/2", "rank 1→2", "was Void Torrent"), and a one-word `<span class="cpn">`
pane chip at `.6rem var(--ink3)`. Each card repeats the map's grammar at card scale — `.ccard.da` a 3px solid `--up` top border, `.ccard.dd`
a 3px dashed `--down` bottom border, `.ccard.ds` both, `.ccard.drk` the same at 34% width — so the strip TEACHES the map and no separate
legend is needed. `.crail{display:flex; gap:.4rem; overflow-x:auto}`: bounded internally, the page never scrolls sideways.
**Capped at 8 cards**; past that print the count and a `.cmore` affordance ("+13 more · open the diff ledger") that sets the existing
`screenDlOpen=true` and re-renders. 63% of real diff pairs mark five or fewer nodes, so the common case is answered above the trees with the
ledger closed and the heavy case does not grow the page. Cost: one 34px row + `.45rem` margin ≈ **+41px**, only while a base is pinned AND
the builds differ. `capH` reads `window.innerHeight` alone, so the trees do not shrink; they already run ~180px past the 1080 fold, so this
is +41px on an existing scroll, and it buys back the scroll-to-the-ledger the reader performs today.
**Cross-highlight**, wired in the same block that binds `#cs-diffbar [data-flip]`: mouseenter / focus on `#cs-changes .ccard[data-nd]` adds
`.hi` to `#cs-trees .ttn[data-nd="…"]`, mouseleave / blur removes it, and **a click PINS it** (§15.11 makes click-pin the signature
interaction — the reader must be able to move the cursor to the node without losing it); a second click, or a click on another card, moves
the pin. Colour-only, fixed geometry: `#cs-trees .ttn.hi::before{box-shadow:0 0 0 1px var(--accent), 0 2px 6px -1px rgba(0,0,0,.55),
inset 0 1px 0 rgba(255,255,255,.07)}`. Nothing rotates or moves (prefs #3, #7). The cross-hero substitution is the FIRST card, both ticks,
no `data-nd`.

## 7. The edit list, by function name and selector
**A. `csTreePanesHTML`** — (1) fit-loop start rung `44`→`40`, and update the "34-44px band" comment here and in the `#cs-trees` CSS header
comment. (2) `const CHROME=22`→`24`. (3) diff classes gain a positive marked class and a rank class: `dcl=" dv da"` / `" dv dd"`; rank branch
→ `df.k==="u"?" dv da drk":" dv dd drk"`; swap → `" dv ds"`. **Use `drk`, never `dr` — `csCountHTML` already emits `<span class="dr">` and
`.dr{color:var(--ink2)}` exists.** (4) badge strings become the symbol alone: `"+"` for `k==="a"`/`"u"`, `"−"` (U+2212) for `"d"`/`"w"`, `"≠"`
for `"s"`; leave every `tnote` untouched. (5) `const multi=(picked||drop)&&+n.r>1;` → `… && !(df&&(df.k==="a"||df.k==="d"||df.k==="s"));`.
(6) delete the `ghost` variable, its `n.es[df.bi].ic` block, and `+ghost` in the emit. (7) emit `data-nd="'+n.id+'"` on the `.ttn` div; build
the node string into a local `const`, then `if(df) ndsMark+=…; else nds+=…;` and emit `nds+ndsMark`. (8) emit `<div class="ttpane nodx">` when
`heroSub && p.lab==="Hero"`. (9) correct the comment above the badge emission that claims the marks "overhang into the pane's padding rather
than covering the icon face" — measurably false today (up to 32.8%) and the reason this surface was misread twice.
**B.** `.ttpane` padding and `#cs-trees` padding per §1.
**C. The `#cs-trees` CSS block** — apply §2/§4/§5 verbatim, and DELETE: `.ttg` and `#cs-trees.diff .ttg`; the three
`#cs-trees.diff .ttn.* .tico{box-shadow:… 0 0 0 3px …}`; `#cs-trees.diff .ttn.* .tico{border-width:2px}`; `.ttn.dd .tico{border-style:dashed;
box-shadow:…}` and `.ttn.dd .tico img{filter:…}`; `.ttn.ds .tico{border-color; box-shadow}`; all three `.ttn.da/.dd/.ds
.tico.ch{background:var(--up|--down)}`; `.ttn.da/.dd/.ds .ttb{color; border-color}`; `#cs-trees.diff .ttb{font-size; padding; left; top}`;
`#cs-trees.diff .ttn.pk:not(.da):not(.dd):not(.ds){opacity:.62}` and `#cs-trees.diff .ttn.dm{opacity:.28}`; both `.ttn.da/.dd a.whico:hover`
rules. Every new rule is `#cs-trees.diff …` (specificity 1,x,x) so the documented source-order hazard between `.ttn.dd .tico` and
`.ttn.dm .tico` cannot bite it — keep the block after the `.ttn.dm` rules regardless.
**D. `csTalentsHTML`** — the one call line (§6), plus a rewrite of the `.cs-foot` legend, which today describes the deleted encoding ("dimmed
one tier, dashed rim … the base's option ghosted bottom-left"). Replace with: `<b class="dp">+</b> added (solid chip, tick on the tile's top
edge) · <b class="dn">−</b> dropped (hollow chip, segmented tick on the bottom edge) · <b class="dz">≠</b> choice swapped (both ticks) · a
short tick and a 1→2 pip = the rank moved · unchanged talents keep their shape in grey`.
**E.** New CSS for `#cs-changes / .ccnt / .crail / .ccard / .cico / .cnm / .cdt / .cpn / .cmore`, beside the existing `#cs-diffbar` rules:
radii `var(--r1)`, ground `--surface1`, 1px `--line1`, colour-only hover.
**NOT TOUCHED:** `csDiffMaps`, `csDiffCounts`, `csCountHTML`, `csSgn`, `csNodeIndex`, `csPaneOf`, `csHeroOfSel`, `csBuildChipsHTML`,
`csDiffBarHTML`, `csMedDeltaHTML`, `ladder()`, `snap()`, `headW`/`boxW`, the slack spend, `capH`, the fit/ovf logic, the counting rule, and
the diff LEDGER in every form (no column, no `<th>`, no sort key, no `screenDlSort` read or write).

## 8. Verification (prototype BEFORE editing site/index.html, then repeat after)
Apply the stylesheet and geometry at runtime against the prodmirror payload in the scratchpad, served on a port in **8940–8959 that you have
`curl`ed first** (stale servers are bound in 8977–8997 and will silently serve a different copy). Playwright, `executablePath
'/opt/pw-browsers/chromium'`, 1920x1080, dSF 1, 3–5s after load; reach the pane by clicking a `.bbar` (e.g. /Warrior Arms/), then
`#frame-char`, then sub-nav `data-t="tal"`, then a chip's `base` button, then another chip.
1. **Geometry, 12/12 specs.** `--tn` computes `40px`; row pitch 52 (gutter 12); col pitch ∈ {49,56,66}; `#cs-trees` has class `fit`,
   `offsetWidth ≤ 1116`, `document.documentElement.scrollWidth === 1920`.
2. **Nearest-neighbour census — nothing touches.** For every marked node in the heaviest real diff (Priest Shadow, 21 marks, colPitch 49) and
   in ≥4 other specs, expand the marked icon rect by 3px (the plate) and measure to the nearest OTHER icon rect. **ACCEPT: minimum ≥3px, zero
   touching, zero overlap** (today: median 0.00px, 54% touching). Repeat for `.ttb` and `.ttr`; chip on-icon area ≤11% of the icon rect.
3. **Wowhead (pref #11).** `document.elementFromPoint` at the icon centre, the chip centre and the pip centre must each return a node inside
   that `.ttn`'s `a.whico`, and the anchor rect must still cover the full 40x40 `.tico`.
4. **GREYSCALE GATE — binary pass/fail, the check two previous passes never had.** Inject `filter:grayscale(1)` on `#cs-trees`; screenshot
   the 21-mark Priest Shadow diff and a sparse Warrior Arms diff at 1:1; add / drop / rank-move / swap must each be NAMEABLE at 100% with no
   hue and no legend. Programmatic proxy: solid chip interiors ≥0.55 encoded luminance, hollow ≤0.15, and each mark's tick band in the
   expected 4px row of its plate. **The greyscale screenshot must be as readable as the colour one.**
5. **Vertically adjacent marks.** For every marked pair one row apart, require ≥8px of non-tick pixels between the two ticks. If a pair reads
   as one rung, drop the tick widths to 50%/28% and re-measure — never change a tick's edge assignment.
6. **Field quiet.** Chroma histogram over lit pane pixels, before/after: high-chroma pixels (chroma > 25) must fall from ~21.9% to under ~6%,
   field p90 chroma from ~62 to <15. **Tiers:** marked nodes must be the only nodes with chroma > 25, and `.dm` must sit visibly below
   `.pk:not(.dv)` in luminance.
7. **Every counted change has a pixel.** For each `csDiffMaps` entry the node's `.ttn` is visible (not fully covered by a later sibling) or it
   is a cross-hero hero node — 3 of 130 fail today. Assert `#cs-trees .ttg` count is 0 and `.ttb` count equals the marked-node count.
8. **Strip.** ≤8 cards; `.crail` scrolls internally while the page does not; `.cmore` opens the ledger; hover, focus and click-pin each
   highlight the right `.ttn`; the strip still renders from `base.sel` when the base has left the chip row (pin a base, move the lens until
   the chip ghosts).
9. **Non-diff path unchanged.** With no base pinned the trees are identical to today apart from the 40px band and the resulting pitch. Check
   a choice node with no `img` (the `.tph` single-letter fallback) at 40px in both modes.
10. **Paint cost.** ~115 filtered `<img>` per pane x3 in diff mode: profile a chip click on Priest Shadow. If it janks, move the filter from
    `.tico img` to one `filter` on `.ttcv` and un-filter marked nodes — a FALLBACK, not a first move (a pane-level filter also greys the
    rims, ticks and chips).

## 9. Risks, and what to re-verify after the upgrade-surface merge
- **`$("charscreen").clientWidth` must still measure 1116px.** Every number in §1 derives from `availW = clientWidth − (n−1)*12 − n*CHROME`.
  If the Upgrade lean surface or the table refactor moves `#frame`'s max-width or `#charscreen`'s box, re-run the 12-spec sweep before
  trusting `--tn:40`. The single most important post-merge check.
- **Class collisions.** `dv`, `drk`, `hi`, `nodx`, `ccard`, `crail`, `cico`, `cnm`, `cdt`, `cpn`, `ccnt`, `cmore` are new — grep every one
  against merged HEAD. `.dr` is already taken. Confirm the sort helpers neither emit nor consume `ttb`, `ttg`, `ttr`, `ttn`, `tico`, `ttcv`,
  `ttpane`, `da`, `dd`, `ds`, `dm`, `pk`, `dv`, `drk`.
- **Insertion point.** The only structural edit to shared code is one line in `csTalentsHTML` between `csDiffBarHTML(...)` and
  `csTreePanesHTML(...)` — re-anchor by function name, never by position. Confirm `screenDlOpen` still exists under that name and
  `csDiffLedgerHTML`'s signature is unchanged, and that the ledger still sorts on every column (pref #12); this pass neither helps nor harms
  that.
- **Stacking context.** If `.ttn{z-index:1}` is ever removed the plate falls behind the lattice.
- **Height.** The pane canvas +6px and the strip's +41px land on a page already ~180px past the fold; re-capture a full-page 1920x1080 shot
  and confirm no new scroll behaviour and that the ledger below is still reachable.
- **Aesthetic risk.** The quiet grey field is a large change to a pane the owner likes. It is scoped strictly to `#cs-trees.diff` —
  unpinning the base restores today's fully lit, champagne-rimmed tree exactly. Present it to the owner as a MODE, not a new default.
- **Reverts, one line each.** Magnitude back on the chip (edit A4 + widen `.ttb` to 24x16, then RE-RUN check 2 — at 24px wide the chip's left
  edge re-enters the left neighbour in nine-column specs). Pips back on every mark (edit A5). The strip off (the `csTalentsHTML` call).
