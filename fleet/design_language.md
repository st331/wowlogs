# "Candlelit Ledger" v2 — design language (complete spec)

One-sentence brief: a near-black WARM-graphite ledger on a centered, bounded measure, where
hue belongs to the data, champagne metal marks what is ACTIVE, nothing moves because a cursor
passed over it, and nothing rotates, ever.

Builders: copy values from this doc **verbatim**. The live site (`site/index.html`) is v1 —
its palette family and component skeletons are the base, but v2 changes listed here override
it (see §16 for the delete list). Owner prefs (`fleet/user_prefs.md`) override everything.

## 1. The five hard rules (a build breaking any one fails review)

1. **Nothing rotates.** Open/closed and every other state reads through a static marker swap
   or a tick's length/color — never a turned glyph. No chevrons, carets, or triangles anywhere
   (breadcrumbs, sorts, dropdown affordances, disclosure markers included).
2. **The measure is centered and bounded.** `main`: max-width 1200px, margin 0 auto;
   `#chart` ≤960px (bar track lands ~650–800px); no bar, rule, or dock runs from or to a
   screen edge. Any inspector rail = inert fixed positioner + bounded inner box (≤1116px).
3. **Content-hugging triggers + docked inspector.** Detail surfaces are a measure-aligned
   DOCKED inspector rail with click-pinning (the page's signature interaction) — never
   cursor-anchored tooltips. Trigger zones hug actual content (the bar, the label TEXT);
   empty row/column space is inert.
4. **Calm hover.** State changes are instant and subtle; color-only hover; nothing shimmers,
   grows, glows, or moves as the cursor passes. Permitted click-driven state motion: a slide
   (≤160ms ease) — never rotation.
5. **Accent budget / flat surfaces.** Flat fills, hairline (1px) borders, no elevation stack;
   champagne gold is reserved for ACTIVE state and key emphasis; class colors carry the data.

Standing owner constraints baked in below: prediction is first-class (Compare + Trends stay
prominent and effortless); transient tuning/PTR features live in the Lab treatment (§15.13);
sample-size honesty (dates covered, run counts, groups) is always visible in captions/KPIs.

## 2. Color tokens (copy this block)

```css
:root{
  /* ground — warm graphite, warmed from live #15171C family. NEVER blue, NEVER purple. */
  --bg0:#181511;            /* page ground */
  --bg-side:#11100C;        /* sidebar ground (darker rail) */
  --surface1:#211E18;       /* panels, cards, controls, table wrap */
  --surface2:#29251D;       /* raised: sticky table headers, inspector rail, toast */
  /* text — three tiers */
  --ink:#EAE7E0;            /* primary: values, cell data, active labels */
  --ink2:#B8B2A6;           /* secondary: button text, section titles, body labels */
  --ink3:#908B80;           /* tertiary: captions, hints, th, idle chips/tabs */
  /* accent — champagne / candle gold (softened from live #F8B700). ACTIVE + key emphasis only. */
  --accent:#E8BC57;
  --accent-dim:rgba(232,188,87,.10);   /* active fills (seg.on, chip.on) */
  --accent-line:rgba(232,188,87,.42);  /* active borders, hero rule head, pin tag */
  /* semantic — text-only deltas, never fills */
  --up:#3DDC84;  --down:#FF6B6B;
  /* hairlines — warm-white alpha, three tiers */
  --line0:rgba(234,227,208,.05);       /* table row rules */
  --line1:rgba(234,227,208,.08);       /* panel/section hairlines, tab rail, grid */
  --line2:rgba(234,227,208,.14);       /* control borders: buttons, seg, selects, inspector */
  /* neutrals for compare-ghosts & reference ticks */
  --ghost:rgba(200,198,190,.45);       /* Period-B ghost bars */
  --tickref:#C9C7C2;                   /* reference tick on bars */
}
```

Page ground (the only gradient allowed on the page — the "candlelight"):

```css
body{background:
  radial-gradient(1100px 420px at 78% -120px, rgba(232,188,87,.04), transparent 62%),
  var(--bg0);
  background-attachment:fixed}
```

- `::selection{background:rgba(232,188,87,.28); color:#fff}`
- `:focus-visible{outline:2px solid var(--accent); outline-offset:2px}` (accessibility trumps
  the accent budget — focus rings are always champagne).
- Scrollbar: width/height 10px; thumb `#3A362E`, radius 4px, 2px `#1C1914` border; hover
  `#46413A`; track transparent.
- Class colors (WoW class palette) are used verbatim from the game and only on data: bars,
  class dots, sparkline strokes. Never on chrome.

---

## 3. Typography

Load: `https://fonts.googleapis.com/css2?family=Marcellus&family=Inter:wght@400..800&display=swap`

```css
--font:'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
--font-display:'Marcellus','Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
```

- Body: `font:14.5px/1.5 var(--font)`, antialiased, `text-rendering:optimizeLegibility`.
- **Every numeral that is data gets `font-variant-numeric:tabular-nums`** (KPI values, table
  cells, bar values, inspector values, deltas, slider readouts).
- **Marcellus is the wordmark face and appears exactly once per page** (the hero title).
  Marcellus ships one weight (400) — never fake-bold it. It replaces Cinzel everywhere.

Role scale (sizes/weights are fixed):

| Role | Face | Size | Weight | Case/tracking | Color |
|---|---|---|---|---|---|
| Wordmark (once) | Marcellus | 1.9rem | 400 | UPPERCASE, ls .14em, lh 1.15 | --ink; ONE word may be --accent; no text-shadow |
| Hero sub | Inter | .88rem | 400 | ls .04em | --ink3 |
| Section title | Inter | .8rem | 700 | UPPERCASE, ls .22em | --ink2 (hover --ink) |
| Section scope line | Inter | .76rem | 400 | — | --ink3 |
| Sidebar group label | Inter | .64rem | 700 | UPPERCASE, ls .18em | --accent |
| KPI label | Inter | .62rem | 600 | UPPERCASE, ls .15em | --ink3 |
| KPI value | Inter | 1.75rem | 700 | ls −.015em, tabular | --ink |
| Table header (th) | Inter | .63rem | 600 | UPPERCASE, ls .11em | --ink3; sorted --accent |
| Table cell | Inter | .83rem | 400 | tabular | --ink |
| Bar label | Inter | .78rem | 400 | nowrap, ellipsis | --ink3; hot --ink |
| Bar value | Inter | .8rem | 650 | tabular | --ink |
| Bar inlay | Inter | .67rem | 500 | nowrap | per contrast on class color |
| Delta | Inter | .72rem (.84rem in tables) | 700 | tabular | --up/--down; n/a --ink3 500 |
| Buttons/segments/tabs | Inter | .8–.84rem | 500; active 600 | — | see §15 |
| Chips | Inter | .75rem | 500 | lh 1.4 | see §15.4 |
| Counts (chip/summary) | Inter | .72rem | 600 | — | --accent |
| Caption | Inter | .76rem | 400 | lh 1.6 | --ink3 |
| Hint | Inter | .73rem | 400 | lh 1.5 | --ink3 |
| Footer | Inter | .74rem | 400 | lh 1.7 | --ink3 |

---

## 4. Spacing scale

`2 · 4 · 6 · 8 · 12 · 16 · 20 · 24 · 32 · 48 · 72` (px). Canonical uses:

- 2: tab gap, bar-row vertical padding (2.5px), tick inset
- 4: chip-row gap vertical, marker gaps
- 6: chip gap, delta margin-left, class-dot offset
- 8: bar value padding-left, control inner gaps
- 12: bar-row grid gap, trend-card grid gap, KPI grid gap (14px allowed), table cell x-pad (.75rem)
- 16: tab rail margin-bottom, hero title→controls
- 20: sidebar padding-top, toast offset from viewport corner
- 24: hero rule margin-bottom (26px), mobile main x-pad (18px)
- 32: main top padding (34px)
- 42: main side padding (fixed — it derives the 1116px inspector width)
- 48: sidebar bottom padding, section rhythm (`.sec` margin-top 2.6rem ≈ 42px)
- 72: main bottom padding

---

## 5. Radii (halved from v1 — "less rounded, not sharp, halfway")

```css
--r2:6px;  --r1:4px;
```

| --r2 (6px) | --r1 (4px) | Special |
|---|---|---|
| panels, KPI cards, table wrap, trend cards, toast, Lab panels, inspector TOP corners | buttons, segment containers, selects, sidebar summaries, chips, pin tag, toast dismiss, switch track, scrollbar thumb | slider thumb 4px; class dot 9×9 radius 2px; switch knob 12×12 radius 3px; reference tick radius 1px; **bar rows radius 0**; tabs radius 0 |

**Never `border-radius:999px`, never pills, never circles** (the only circle-free exception
list above is exhaustive). Nothing fully square either — controls always get --r1.

---

## 6. Panel / elevation recipe (the whole recipe)

```css
.panel{background:var(--surface1); border:1px solid var(--line1); border-radius:var(--r2)}
```

That is the entire recipe. **No box-shadows, no drop-shadow stacks, no glows, no gradient
sheens, no backdrop-filter.** Overlay surfaces (inspector rail, toast) separate themselves by
using `--surface2` and `--line2` instead — still flat, still 1px.

---

## 7. Motion & interaction rules

- **Hover = color only.** Permitted properties on `:hover`: `color`, `border-color`,
  `background-color`, `opacity`. Transition ≤.13s. Forbidden on hover: transform, width,
  height, box-shadow, filter, font-weight, letter-spacing — nothing moves, grows, or glows.
- **Click-driven motion:** one slide (translate or width), ≤160ms ease, fired only by a state
  change the user clicked. Examples: switch knob travel, section tick 56px↔20px, inspector
  rail first-open slide, toast entrance. Content swaps inside an open surface are instant.
- **Nothing rotates. Ever.** No `rotate()` in any transition/animation/spinner. No animated
  `transform` outside the click-slide allowance.
- **Trigger zones hug content.** Hover/click targets are the rendered bar, the label TEXT
  (inline span), the sparkline point, the chip — never the empty remainder of a row, label
  column, or table cell. Wide monitors must never turn a data row into a screen-wide trap.
- **No cursor-anchored tooltips.** `title=""` on tiny metadata (week-chip dates/volume) is
  the only browser-native exception; all data detail goes to the inspector rail (§15.11).
- **Active/pressed:** no `translateY` press effect; color change only.
- Loading state: no spinner. A 120×2px `--line1` track with a 40px `--accent` segment sliding
  end-to-end (1s ease-in-out alternate) beside `--ink3` "Loading data…" text.

---

## 8. Layout & the centered bounded measure

- Two-column grid: `grid-template-columns:300px 1fr; min-height:100vh`. Sidebar rail painted
  full-height: `linear-gradient(90deg, var(--bg-side) 299px, var(--line1) 299px 300px,
  transparent 300px)` on the layout container.
- `aside`: `background:var(--bg-side); border-right:1px solid var(--line1);
  padding:20px 18px 48px`; ≥901px: sticky, `top:0; height:100vh; overflow-y:auto`.
- `main{max-width:1200px; margin:0 auto; padding:34px 42px 72px; min-width:0}` — the content
  floats mid-viewport; nothing is full-bleed, nothing starts at the screen's left edge.
- `#chart{max-width:960px}` — the bar track column lands ~650–800px wide.
- ≤900px: single column, `main{padding:26px 18px 56px}`, sidebar unsticks, layout bg none.
- Hero rule (the one big champagne moment, under the wordmark):
  `height:2px; border:0; margin:18px 0 26px;
  background:linear-gradient(90deg, var(--accent-line), rgba(232,188,87,.10) 45%, transparent 75%)`.

---

## 15. Component specs

### 15.1 Buttons (.btn)
`background:var(--surface1); border:1px solid var(--line2); color:var(--ink2);
border-radius:var(--r1); padding:.45rem .85rem; font-size:.84rem; font-weight:500`.
Hover: `color:var(--accent); border-color:var(--accent-line)` — nothing else. Active: no
transform. Disabled: opacity .5, cursor default. Full-width sidebar reset button uses this.

### 15.2 Segmented controls (.seg)
Container: `display:inline-flex; border:1px solid var(--line2); border-radius:var(--r1);
overflow:hidden; background:rgba(234,227,208,.02); flex-wrap:wrap`.
Buttons: transparent, `color:var(--ink3); padding:.42rem .95rem; font-size:.8rem;
font-weight:500; border:0`. Hover: `color:var(--ink)`.
On: `background:var(--accent-dim); color:var(--accent); font-weight:600;
box-shadow:inset 0 0 0 1px var(--accent-line)` (inset ring is a border substitute, not
elevation — allowed). State change instant.

### 15.3 Tabs (.tabs)
Rail: `display:flex; gap:2px; border-bottom:1px solid var(--line1); margin-bottom:16px`.
Buttons: **no radius, no fill — ever, including hover**: `background:none; border:0;
color:var(--ink3); padding:.5rem .85rem; font-size:.84rem; font-weight:500;
border-bottom:2px solid transparent; margin-bottom:-1px`.
Hover: `color:var(--ink)` only. On: `color:var(--accent); font-weight:600;
border-bottom-color:var(--accent)` — a 2px accent underline sitting on the rail baseline.

### 15.4 Chips (.chip) — crisp rectangles, NOT pills
`font-size:.75rem; font-weight:500; line-height:1.4; padding:.26rem .6rem;
border-radius:var(--r1); border:1px solid rgba(234,227,208,.10); background:transparent;
color:var(--ink3); user-select:none`.
Hover: `color:var(--ink); border-color:var(--line2)`.
On: `color:var(--accent); border-color:var(--accent-line); background:var(--accent-dim)`.
Ghost variant (Period-B selections, `.chip.ghosted.on`): `color:#D8D6CF;
border-color:#8E8C86; background:rgba(200,198,190,.10)` — grey, matching the ghost bars.
**Week chips** are this exact chip; their dates + run volume go in `title=""` AND surface in
the inspector rail on click. Chip flex rows: `gap:6px; padding:9px 2px; flex-wrap:wrap`.

### 15.5 Switches (label.small input[type=checkbox]) — restyled off the pill
Track: `appearance:none; width:34px; height:18px; border-radius:var(--r1);
background:rgba(234,227,208,.09); border:1px solid var(--line2); position:relative;
vertical-align:-4px; flex:none`.
Knob (::after): `top:2px; left:2px; width:12px; height:12px; border-radius:3px;
background:#B9B6B0; transition:transform .16s ease, background .16s` (knob travel is
click-driven — allowed).
Checked: track `background:var(--accent); border-color:var(--accent)`; knob
`transform:translateX(16px); background:var(--bg0)`. **No glow box-shadow.** Label text:
`.84rem var(--ink)`, gap 7px. Plain checkboxes elsewhere: `accent-color:var(--accent)`.

### 15.6 Range sliders — 4px thumb, no halo
```css
input[type=range]{appearance:none; width:100%; height:24px; background:none; margin:.15rem 0}
/* track */ height:4px; border-radius:2px; background:rgba(234,227,208,.11);
/* thumb  */ width:14px; height:14px; border-radius:4px; background:var(--accent);
             border:2px solid var(--bg-side); margin-top:-5px;
```
Firefox `::-moz-range-progress`: `height:4px; border-radius:2px; background:var(--accent)`
(flat — no gradient). Hover/drag: thumb `background:#F0C868` — color only, **no halo rings,
no glow shadows**. Value readouts next to sliders: `b{color:var(--accent)}` tabular
(active-value emphasis is within the accent budget).

### 15.7 Dual key-level slider (.dual)
`position:relative; height:28px`. `.track`: `top:12px; left:0; right:0; height:4px;
border-radius:2px; background:rgba(234,227,208,.11)`. `.fill` (between thumbs): `top:12px;
height:4px; border-radius:2px; background:var(--accent)` — flat, **no gradient, no glow**.
Two stacked transparent inputs, `pointer-events:none`; thumbs `pointer-events:auto`, same
14×14 radius-4px champagne thumb as §15.6 (`margin-top:7px` on the 28px lane).

### 15.8 Selects, number inputs, sidebar summaries
Selects/inputs: `background:var(--surface1); color:var(--ink); border:1px solid var(--line2);
border-radius:var(--r1); padding:.32rem .55rem; font-size:.85rem`. Native select arrow is
tolerated (OS-drawn); custom dropdown affordances may NOT add carets.
Summaries (sidebar `details>summary`): `background:var(--surface1); border:1px solid
var(--line1); border-radius:var(--r1); padding:.42rem .65rem; font-size:.84rem;
font-weight:500; color:var(--ink); display:flex; align-items:center; gap:.5rem;
list-style:none` (+ `::-webkit-details-marker{display:none}`).
**Static marker swap — no rotation, no transition:**
```css
summary::after{content:"+"; color:var(--ink3); margin-left:auto; font-size:.72rem}
details[open] summary::after{content:"−"}
```
Hover: `border-color:var(--line2)` only. Count spans: `.72rem 600 var(--accent)`.
`details{margin:.4rem 0}`.

### 15.9 Section headers (.sec) — static marker + champagne state tick
```css
.sec{display:flex; align-items:baseline; gap:.75rem; margin:2.6rem 0 1rem;
  padding-bottom:.55rem; border-bottom:1px solid var(--line1); cursor:pointer;
  user-select:none; position:relative}
.sec .mk{width:.85rem; flex:0 0 auto; color:var(--ink3); font-size:.72rem}
.sec .mk::before{content:"−"}          .sec.closed .mk::before{content:"+"}
.sec::after{content:""; position:absolute; left:0; bottom:-1px; width:56px; height:2px;
  background:var(--accent); opacity:.85; transition:width .16s ease, opacity .16s ease}
.sec.closed::after{width:20px; opacity:.35}
```
The 56px champagne tick IS the open/closed indicator: 56px bright open → 20px dimmed closed
(click-driven slide, allowed). The −/+ marker slot swaps content statically — **no
transition on the marker, no rotation, no chevrons**. Hover: `.t{color:var(--ink)}` and
`.mk{color:var(--ink2)}` — color only; the tick NEVER responds to hover.
Title `.t`: caps .8rem 700 ls .22em `--ink2`. Scope line `.s`: .76rem `--ink3` — always
states the live scope (period, key range, sample size). `.sec.closed{margin-bottom:.4rem}`;
body collapse via `[hidden]{display:none}` — instant, no height animation.

### 15.10 Chart bar rows — flat, bounded, no radius
Row: `display:grid; grid-template-columns:minmax(150px,300px) 1fr; gap:12px;
align-items:center; padding:2.5px 6px 2.5px 2px; border-radius:0`. **No full-row hover
background.** JS toggles `.hot` on the row only while the pointer is over the bar element or
the label TEXT span; `.brow.hot .blbl{color:var(--ink)}` — that is the entire hover effect.
Label `.blbl`: right-aligned, .78rem `--ink3`, nowrap/ellipsis; the trigger is an inline
`<span>` hugging the text, never the column box.
Track `.btrack`: `position:relative; height:20px` — bounded by the grid column inside the
≤960px `#chart`; bars grow rightward from the track's left edge, never from the screen edge.
Bar `.bbar`: `position:absolute; top:0; bottom:0; border-radius:0; min-width:2px` — flat
class-color fill, **no gloss overlay gradient, no shadow, no brightness filter on hover**.
Compare mode (`.cmp`): A-bar `height:12px; top:0`; ghost B `.bghost`: `top:14px; height:5px;
border-radius:0; background:var(--ghost); min-width:2px`.
Reference tick `.btick` (e.g. period-A marker): `top:-2px; bottom:-2px; width:2.5px;
border-radius:1px; background:var(--tickref); opacity:.9`.
Value `.bval`: `.8rem 650 tabular var(--ink); padding-left:8px`, vertically centered.
Inlay `.binlay`: `.67rem 500`, `left:8px`, `pointer-events:none`.
Deltas `.delta`: `.72rem 700; margin-left:6px`; `.up{color:var(--up)} .down{color:var(--down)}
.na{color:var(--ink3); font-weight:500}` — semantic color on text only, never fills.

### 15.11 Docked click-pin inspector rail — the signature interaction
Replaces ALL cursor tooltips (`#tip` is deleted). Two-layer structure:
```html
<div id="inspect-pos"><div id="inspect">…</div></div>
```
```css
#inspect-pos{position:fixed; left:0; right:0; bottom:0; z-index:50; display:flex;
  justify-content:center; padding:0 42px; pointer-events:none}  /* inert positioner */
#inspect{pointer-events:auto; width:100%; max-width:1116px;     /* = 1200 − 2×42 */
  background:var(--surface2); border:1px solid var(--line2); border-bottom:0;
  border-radius:var(--r2) var(--r2) 0 0;                        /* top corners only */
  padding:.75rem 1.1rem .85rem; font-size:.78rem; max-height:38vh; overflow-y:auto}
@media(max-width:900px){#inspect-pos{padding:0 18px}}
```
The rail docks to the viewport bottom but the visible box is measure-aligned and bounded —
it never runs edge to edge (the positioner is inert and invisible).
**Behavior:** hovering a trigger (bar, label text span, sparkline point, comp row, week chip)
shows that subject in the rail as a PREVIEW (header name in `--ink3`); clicking the trigger
PINS it: header name `--ink` 600 + class dot, rail border-color becomes `var(--accent-line)`,
and a pin tag appears. Preview content swaps instantly with zero motion; the rail's
first-open is one `translateY(8px)→0` + fade slide, 160ms ease (click/state-driven). The
rail never follows, chases, or repositions with the cursor.
Pin tag: `.72rem var(--accent); border:1px solid var(--accent-line);
border-radius:var(--r1); padding:.1rem .5rem` reading `PINNED · <subject>`; dismiss `×`
button: `--ink3`, radius --r1, hover `--ink`. Unpin via ×, Esc, or clicking the pinned
trigger again; rail hides when unpinned and no trigger is hovered.
Body layout: stat groups as 2-col rows — key `--ink3` left-aligned, value `--ink`
right-aligned tabular, cell padding `.08rem .5rem .08rem 0`; groups sit in a flex row with
2rem gaps; sample size + dates always included.

### 15.12 Update toast (#upd-toast)
`position:fixed; right:20px; bottom:20px; z-index:60; display:flex; align-items:center;
gap:.55rem; font-size:.84rem; background:var(--surface2); border:1px solid
var(--accent-line); border-radius:var(--r2); padding:.65rem .45rem .65rem .95rem` —
flat, **no shadow, no gradient**. `b{color:var(--accent); font-weight:600}`
`span{color:var(--ink3)}`. Entrance: translateY(8px)+fade, 160ms ease, once (event-driven).
Dismiss `.x`: `background:none; border:0; color:var(--ink3); padding:.15rem .5rem;
border-radius:var(--r1)`; hover `color:var(--ink); background:rgba(234,227,208,.07)`.
A small box floating near the corner is fine; it must not span any edge.

### 15.13 Lab / dashed-border panels — the designed home for transient features
Temporary tuning/PTR analyses (post-tuning toggle, projection, the 4pc filter) come and go;
they get a visually quarantined home so their removal never scars the layout:
`border:1px dashed var(--line2); border-radius:var(--r2); background:transparent;
padding:8px 12px; margin-top:.45rem`. Corner tag: `TEMP` — `.6rem 700 caps ls .14em
var(--ink3); border:1px solid var(--line2); border-radius:var(--r1); padding:.05rem .4rem`.
Controls inside are standard components. **Never accent-bordered** — transient ≠ active.
Retiring the feature deletes the whole panel. The solid sibling (`.bpanel`, e.g. Period-B
block): same but `border:1px solid var(--line1); background:rgba(234,227,208,.02)`.

### 15.14 KPI cards (.kpi)
Pure panel recipe: `background:var(--surface1); border:1px solid var(--line1);
border-radius:var(--r2); padding:.95rem 1.15rem .9rem` — **no gradient sheen, no shadow,
no gold top tick** (v1's `::before` accent bar is deleted; accent budget).
Label: KPI-label type (§3). Value: KPI-value type, tabular. Grid: `repeat(4,1fr); gap:14px`
(≤700px: 2 columns). `.kpi-note`: `.78rem var(--ink3) lh 1.55; margin-top:.7rem;
b{color:var(--ink); font-weight:600}` — carries dates covered / run counts / groups.

### 15.15 Tables + sortable headers
Wrap `.tblwrap`: `overflow-x:auto; background:var(--surface1); border:1px solid
var(--line1); border-radius:var(--r2)` — no shadow. Table: `border-collapse:collapse;
width:100%; font-size:.83rem; min-width:700px`.
`th`: `position:sticky; top:0; z-index:1; background:var(--surface2); color:var(--ink3)`,
th type (§3), `padding:.6rem .75rem; text-align:right; cursor:pointer; white-space:nowrap;
box-shadow:inset 0 -1px 0 var(--line2)` (inset hairline, not elevation). `.txt{text-align:left}`.
Hover: `color:var(--ink)` only. Sorted: `color:var(--accent)` plus a **static edge rule for
direction — no arrows/carets/triangles:**
```css
.data th.sorted.desc{box-shadow:inset 0 -2px 0 var(--accent)}  /* underline = descending */
.data th.sorted.asc {box-shadow:inset 0  2px 0 var(--accent)}  /* overline  = ascending  */
```
`td`: `padding:.44rem .75rem; border-top:1px solid var(--line0); text-align:right;
tabular`. Row hover: `background:rgba(234,227,208,.03)` — neutral warm tint, not gold.
Class dot: `9×9px; border-radius:2px; margin-right:8px; no shadow`. Comp cells: role labels
`.66rem caps ls .06em var(--ink3)`, names `.8rem` class-colored, wrap with `.15rem .5rem` gaps.

### 15.16 Trend cards (.tcard) & trend chart
Card: panel recipe (surface1/line1/--r2), `padding:10px 12px 6px`; hover
`border-color:var(--line2)` — color only. Name: `.75rem 600 nowrap ellipsis`; value line:
`.7rem var(--ink3)`. Grid: `repeat(auto-fill,minmax(230px,1fr)); gap:12px`. Sparklines:
class-colored strokes, no glow filters; points are inspector triggers (content-hugging).
Big trend SVG: axis text `11px var(--font) var(--ink3)`, gridlines `var(--line1)`; series
emphasis on click = others drop to opacity .25 (color-only); Trends and Compare stay
top-level and one click away — prediction is a first-class use case.

### 15.17 Captions, scope lines, hints, footer
Caption: `.76rem var(--ink3) lh 1.6; margin-top:.8rem` — under every chart/table, stating
scope: period + dates, key range, N runs / N players, percentile in force.
Hints: `.73rem lh 1.5 var(--ink3)`. Footer: `.74rem lh 1.7 var(--ink3); border-top:1px solid
var(--line1); padding-top:1.2rem; margin-top:3rem`. Links: `color:var(--accent);
text-decoration-color:var(--accent-line); text-underline-offset:2px`.

---

## 16. Migrating from live v1 — delete on sight

- Cinzel (→ Marcellus, §3). The `.gold` text-shadow glow on the hero word.
- `#tip` cursor tooltip block and all its JS positioning (→ inspector rail §15.11).
- Every `rotate()`: sidebar `summary::after` chevron transform, `.sec .chev`, the `@keyframes
  spin` loader (→ §7 loading slide). Every `❯`/`▾` glyph (→ static −/+ swaps).
- All `border-radius:999px` / pill radii: chips, switch tracks, slider tracks/fills (→ §5).
- All box-shadows and gradient sheens on: kpi, tblwrap, bbar::after gloss, tcard, toast,
  summary, dual .fill glow, slider halo rings, switch glow, `.kpi::before` gold tick.
- `.sec:hover::after{width:100px}` tick growth; `.tabs button:hover` background fill;
  `.brow:hover` row background + `.bbar` brightness filter; `.btn:active` translateY.
- The second (cool white) body radial; the blue-leaning `#15171C/#1F232B/#242933/#101216`
  ground family (→ warmed tokens §2).

## 17. How this differs from Archon (deliberate; verify side-by-side)

| Axis | Archon.gg (current) | This design |
|---|---|---|
| Ground | pure black / blue-black | warm near-black graphite (#181511 family) |
| Accent | purple/violet filled pills & buttons | champagne gold, hairline-applied (purple banned) |
| Title | heavy italic geometric sans caps | Marcellus serif letterspaced caps, once |
| Controls | filled purple segments, chevroned dropdowns, caret sort glyphs | flat outlined segments, static −/+ markers, edge-rule sort indicators |
| Layout | full-bleed, left-anchored | centered bounded 1200px measure |
| Detail surface | floating cursor tooltips/popovers | measure-aligned docked inspector with click-pinning |

**Litmus test:** put the page beside archon.gg (reference: `scratchpad/archon_page.png`) —
if the ground reads the same hue, if any accent reads purple, if any glyph is a
chevron/caret, or if the wordmark reads as bold geometric sans, it fails. "Inspired by" means
the near-black + single-accent + data-color discipline — never the same clothes.

---

# §GG — "Gilded Glass" update (2026-08-26, owner-directed; OVERRIDES the flat-only rules above)

The owner: "I really like glass/metal motifs in my elegant designs. the last visual
redesign … was much more in the direction of what I liked." Restore material richness
while keeping every calm-interaction rule.

## What changes
- **Panels**: layered elevation returns — soft stacked shadows (e.g.
  `box-shadow: 0 1px 2px rgba(0,0,0,.3), 0 14px 30px -24px rgba(0,0,0,.7)`) on panels,
  KPI cards, and table wraps. Subtle top-edge highlight line (1px inset
  rgba(255,255,255,.06)) for a machined-metal lip.
- **Chart bars**: the gloss overlay returns — `::after` with
  `linear-gradient(180deg, rgba(255,255,255,.22), rgba(255,255,255,.03) 48%, rgba(0,0,0,.16))`,
  pointer-events:none. Bars read as lacquered metal, not flat ink.
- **Sticky bars/rails** (lens bar, context strip): glass — translucent ground
  (rgba of the surface at ~.82 alpha) + `backdrop-filter: blur(8px)` with a solid fallback.
- **Active/metal accents**: active chips, slider thumbs, and the active tab underline may
  use a champagne METALLIC gradient (e.g. `linear-gradient(180deg,#f2cf76,#d9a83f)`)
  instead of flat accent; borders on active elements may pick up a gold sheen.
- **Display face**: Cinzel for the wordmark and section titles (the face the owner liked);
  Inter stays for all UI/data. Marcellus is retired.

## What does NOT change (still hard rules)
Radii 6px/4px; nothing rotates; static +/− markers; hover is color/brightness only —
nothing grows, moves, or glows on cursor pass (a static gloss is material, not motion);
content-hugging triggers; centered bounded measure; no cursor-following surfaces;
no purple/violet; never Archon-lookalike. Glass/metal is a MATERIAL treatment, not
added reactivity.
