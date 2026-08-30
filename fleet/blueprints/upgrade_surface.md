# Upgrade surface — per-slot ilvl removal, "Upgrade lean", and the universal sorting contract

Status: SPEC, buildable verbatim. Written 2026-08-30. Supersedes nothing; extends
`builds_tab.md` §1 (new addendum 1.8, below) and §3/§C (the character screen).

Owner, verbatim, the two requests this answers:
- (A) *"I don't care about the ilevels for the slots; get rid of them. I want you to think
  of another way that we can easily surface which gear pieces have been upgraded by specs
  the most. maybe make it a separate tab?"*
- (B) *"any of the tables in the character screen tabs (or in the future, anywhere else)
  should have their columns sortable."*

Decision record, so none of this is re-litigated:
- **Not a fourth tab.** The metric is a second *reading* of the same 16 slots and the same
  `d.gearIdx` rows the Gear pane already owns. It ships as a two-state mode on the Gear
  pane, rendering into `.dcenter` — a grid cell provably empty at rest
  (`site/index.html:552`, `:3484`). A fourth pane would have to redraw the paper doll to be
  legible, at which point it *is* the Gear pane with a different scope line.
- **Metric: share of a piece's wearers carrying it above that piece's own modal item
  level.** The item is its own control, so loot-source generosity cancels. Mean-ilvl-per-
  slot is refused — it ranks the loot table (Ret Main Hand 326.0 vs Wrist 300.9) and calls
  it priority; that is the dressed-up version of the number Part 1 deletes.
- **`iup` only** — `iq` (+16.6 KB) and `islot` (+4.3 KB) declined, §2.4. **Cross-spec is
  free and client-side** (`csFieldIup`). **Nothing on the tile is a number** — colour on
  the tile, numbers in tables, one scope line owning the static/live split.

---

# PART 1 — REMOVING PER-SLOT ITEM LEVEL

`e.ilvl` is referenced at exactly **three** places in `site/index.html` (verified:
`grep -n ilvl site/index.html` → 3422, 3427, 3468 and nothing else). One is a comment.
Nothing else in the client reads it — no CSS selector, no sort key, no tooltip, no export,
no hash token — and `data.json.gz` carries no per-row item level at all, so there is no
second source to keep in sync.

**1.1 — paper-doll tile meta, `csGearPaneHTML`, `:3468`. DELETE the whole line**
`+((e&&e.ilvl)?'<i>ilvl '+e.ilvl+'</i>':'')`. `.gmeta` becomes `<b>26%</b><i>n=3,015</i>`
plus the existing `crafted` tag, in both modes. Update the comment at `:3422`
(`share/n/ilvl` → `share/n`). The `.gtile title=` already carries only slot label +
embellishment — leave it.

**1.2 — fold-out row meta, `csFoldHTML`, `:3427`. DELETE**, and drop the ` · ` separator
that precedes it on `:3426`. The fold-out becomes a real table in Part 3 anyway, and in
that rewrite the free-standing item level simply has no column. Item level returns in
exactly one position — as the *baseline* inside the Lean cell's `title` (§2.6) — where it
is the reference point a percentage is measured against and is unreadable without it. That
is the precise difference from what was thrown away: `ilvl 311` alone answers no question.

**1.3 — the sidecar keeps `"ilvl"`.** No pipeline change, no rebuild: it is the input to
`iup` and the baseline in the Lean title. It stops being *rendered as a slot statistic*,
which is what was asked. **Ships immediately** alongside Part 3; see BUILD ORDER.

---

# PART 2 — THE UPGRADE SURFACE

## 2.1 Placement and name

A **mode toggle on the existing Gear pane**. No new tab, no new pane function, no new
sub-nav entry, no new thin-state, no new hash id beyond one suffix.

- A §15.2 segmented control, two segments — **`Share`** (default, `.on`) and **`Upgrade
  lean`** — at the right end of the section-header row `csSecOpen("geargrid",…)` already
  emits (`:3312`). Radius 4px, 26px tall, active segment on the §GG champagne gradient
  `linear-gradient(180deg,#f2cf76,#d9a83f)`, inactive graphite with a 1px
  `rgba(255,255,255,.06)` inset top lip. Hover is a colour step only.
- **Wiring subtlety, do not miss it:** that header is also the section-collapse trigger.
  The toggle's handler must `e.stopPropagation()` or switching mode collapses the section.
- State: `let gearMode="share";` beside `screenFold` (`:2968`), reset to `"share"` in
  `resetScreenPerSpec()` (`:3209`) — a mode is a per-spec reading, not a session identity.
- Hash: `csSyncHash` (`:3158`) emits `#cs=<pk>[.<hero>].gear~lean` only when
  `gearMode==="lean"`; `csRestoreFromHash` (`:3169`) regex becomes
  `/^#cs=(\d+)(?:\.(\d+))?\.(gear|ench|tal)(~lean)?$/`. **Hard guard, not a lenient
  parse:** restore sets `"lean"` only if §2.3.4's feature detection passes for that spec,
  else `"share"`. A bookmarked lean URL can never land on a dead control.

**Naming rule (standing).** *Upgrade* may appear as a **control label** — it is the owner's
own word — and must **never** sit attached to a number. The column is `LEAN %`; the metric
noun in prose is *lean*. The words *upgraded*, *crests*, *invested*, *priority* appear in
no rendered string. A PR relabelling the column "Upgraded %" is a review failure.

## 2.2 The metric

**Per-item static half — sidecar `iup`** (widening §1.8, §2.4). For each item vocab entry
(`spec × slot × item id`), over that entry's journal-observed wearers **deduplicated to
distinct `(character, server)`**:

```
mode = most common ilvl among the entry's distinct wearers   (ties → the HIGHER value)
iup  = round(100 * |{w : ilvl(w) > mode}| / |wearers|)       int 0..100
```

Emitted only at **≥20 distinct wearers**. Absent means *unknown*, never zero.

Two deliberate details. **Dedupe:** the raw journal is parses, and a 40-key grinder — the
exact population that spends upgrade currency — would otherwise contribute 40 observations,
inflating `iup` unevenly across items (weekly-lockout raid pieces vs. farmable dungeon
pieces). `ch`/`sv` are already zipped into the accumulating loop at
`build_site_data.py:1438-1440`; zero wire cost. **Tie rule:** ties resolve to the *higher*
value, because a lower mode leaves more mass strictly above it and would bias lean upward.

**Per-slot live half — client, recounted every render.** `csGearModel` (`:3395`) already
builds, per slot, a `Map` of vocab index → live wearer count over `d.gearIdx`. Extend that
same single pass — no second pass, no second slice:

```
for slot si, over entries e with v>0 and e.iup != null:
    W(si)    = Σ c                                   // live covered wearers
    lean(si) = Σ (c * e.iup) / W(si)                 // 0..100, one decimal
    cov(si)  = W(si) / Σ c over all v>0 in slot si   // coverage, rendered
```

**Denominator, plainly: `W(si)` — wearers in the current lens window whose worn piece
resolves to a vocab entry carrying `iup`.** The `other / none` bucket (`v===0`), entries
below the vocab cap and entries below the 20-wearer floor are excluded from **both**
numerator and denominator — never counted as zero-lean, which would drag sparse slots
toward calm when they are merely unknown. The excluded fraction is always rendered.

**Cross-spec, free — `csFieldIup()`.** A memoised `Map("slot|id" → [iup,…])` built once at
sidecar decode by walking all 40 specs' item vocabularies (~8,806 entries, sub-millisecond,
never rebuilt). Per item row: `field` = median `iup` across every OTHER spec's entry with
the same `(slot, item id)`, `null` under 3 such specs. Item id held constant means drop
source held constant. **Zero bytes, zero row passes** — the cross-spec read without a
second implementation of the lens.

## 2.3 Thin-data floors — four, none of them a placeholder

1. **Entry:** no `iup` (pipeline floor, <20 distinct wearers) → dropped from `W` entirely.
2. **Slot cell:** `W(si) < 40` **or** `cov(si) < 0.50` → `lean(si)` is `null`, the cell
   renders `–`, **the row still renders and `n` still prints** so the owner sees *why* it
   is a dash. Rows are never dropped — the universe is a fixed 16 slots and a table that
   changes length as the lens moves is its own small lie. The floor is 40, not `CS_THIN`'s
   10, because lean is a proportion of a proportion.
3. **Tile:** a slot whose cell is `null` gets no wash and the dashed rule (§2.6) — visibly
   distinct from a genuinely cold slot. Unknown must never render as low.
4. **Mode:** the toggle is emitted only if (a) any entry of this spec's vocab carries
   `iup`, (b) ≥6 slots clear floor 2, (c) the §2.8 duplicate-id scan passes. Otherwise
   `gearMode` is forced to `"share"` and **the toggle is not emitted at all** — the Gear
   pane is byte-identical to today. No greyed control, ever. The pane-level `CS_THIN=10`
   floor is inherited unchanged.

## 2.4 WIDENING — builds_tab.md §1.8, to be inserted verbatim into section 1

> ### 1.8 Addendum — per-entry upgrade lean (2026-08-30, WIDENING)
>
> The sidecar's `"ilvl"` on an item vocab entry is a median over that entry's observed
> wearers (`build_site_data.py:1541`) — it answers "what item level is this piece usually
> at", never "how many of its wearers carry it higher". That is a *within-entry* question,
> so the answer is a within-entry aggregate. Item vocab entries MAY additionally carry
> **`"iup"`** — integer 0-100: the percentage of the entry's journal-observed wearers,
> **deduplicated to distinct `(character, server)`**, whose copy sits **strictly above the
> entry's own modal item level** (mode ties resolve to the higher value).
>
> Derived from `t["ilvl"][k][ident]`, the list the emitter already accumulates at `:1484` —
> **no extra journal pass, no new column, no collector change, no new name cache**. The
> accumulator changes shape from `list[int]` to `dict[(ch,sv) → int]` (first observation
> wins); `ch`/`sv` are already in scope at `:1438-1440`. Consequence, stated honestly: the
> existing `"ilvl"` median becomes a per-character median and may shift by up to one track
> step. Its key, type and meaning are unchanged; no client reads it differently.
>
> - **Defaults / when omitted.** Emitted only at **≥20 distinct wearers**; omitted when no
>   `ilvl` was journaled. Absent means *unknown*, never `0` — a client MUST render nothing
>   rather than a zero.
> - **Feature detection.** Presence of `iup` on any entry of a spec's item vocab. No new
>   top-level key, no `slots`-aligned array, nothing to length-check; §1.2's unknown-key
>   tolerance already admits it (as it carried `ic` in §1.6 and `sel` in §1.7). `"v"` stays
>   `1`. **Degradation:** pure vocab text — no column, no `eslots`, no ladder-rung
>   interaction; under the halving rung the key travels with the surviving entries; an
>   older client ignores it and renders exactly what it renders today.
> - **Size, measured** by re-serialising the live `builds.json.gz` with the emitter's own
>   `separators=(',',':')` / `gzip level=6` against a 3.2868 MB baseline: **+15.0 KB gz
>   (+0.47%)**; ≈ **+30 KB** at restored rung-1 caps (1.7 B/entry × ~17.6k entries). It
>   does not scale with row count. For scale, `sel` costs 108 KB and `ic` 47 KB. If bytes
>   must come back, cut `iq`, then `islot`, then `iup` — all before `ic` or `sel`.
> - **REJECTED, do not re-litigate.** `"iq":[d25,d75]` (+16.6 KB): dispersion rises with
>   loot-source variety as much as with contention, so it misleads in the same shape as the
>   item level just removed, for more bytes than the headline. `"islot"` (+4.3 KB): a
>   df-wide per-spec scalar that **cannot re-slice under the lens** — the one frozen number
>   on a screen where everything beside it moves. Per-row per-slot ilvl columns (+3.33 MB)
>   and a per-row character ilvl (+421 KB) are unaffordable at any encoding.
> - **BLOCKING PREREQUISITE, fail-closed at the emitter.** Ships only after the §2
>   embellishment-marker fix reaches production. On pre-fix bytes, 1,697 of 8,806 entries
>   are duplicate item ids inside one spec+slot split by a meaningless bonus id, and 413
>   such groups disagree on median item level by up to 59 points; `iup` over that partition
>   is an artifact of a bonus id. The emitter **refuses to write `iup` at all** — it does
>   not warn, and it does not fail the build — when any `(spec, slot, id)` triple carries
>   two entries. Pin it with a test on that invariant. The client re-checks the same
>   invariant independently (§2.8), so the guarantee is not owned solely by the pipeline.

## 2.5 Live re-slicing under filters and the percentile lens

`d.gearIdx` is `screenData`'s (`:3241`) window through `frameLensSlice()` (`:2767`). Every
upstream control — dungeon, region, key range, period, timed-only, tier cohort, role,
projection, merge, Archon mode, the percentile slider — already funnels into
`renderFrame()`, so extending `csGearModel`'s existing loop makes the surface re-slice live
with **zero new wiring**. What moves and what does not, stated once in the UI and repeated
here so no one oversells it: **the per-item rates are season-wide constants; the mix that
weights them is 100% live.** Narrow to +18 keys, one dungeon, p95 — the item mix changes,
`c` changes, every tile re-tints and the table re-ranks. The lens cannot reach inside one
item's wearer distribution, so its effect is a lower bound on the true effect. The read
this enables belongs in the caption: *does the top decile lean differently than the 30th
percentile?* — slide p30 → p85 and watch which slots hold their tint.

## 2.6 Layout at 1920×1080

Chrome above the pane is unchanged (ladder strip + identity band + sub-nav + section head)
and the pane's bounding box **does not grow by a pixel at ≥1151px**, because the ranking
lands in a cell that is empty at rest. `#cs-doll` keeps its four regions and widths
(`:552`): `.dcol.l` (6 tiles), `.dcol.r` (8), `.dbottom` (weapons), `.dcenter` ~500px
between them.

**Tiles.** Unchanged in size, type and content — 40px icon, name, `<b>share%</b>`,
`<i>n=…</i>` — with `ilvl` deleted (Part 1). In lean mode they gain **tint only**, driven
by a per-tile `--t: lean(si)/max(lean over qualifying slots in view)` (normalised
within-spec, within-lens, so the doll is a comparison of this spec against itself):
`.gtile.lean` takes a left-to-right champagne wash
`rgba(217,168,63, .05+.30*t)` → `rgba(217,168,63, .012+.075*t)` at 62% → transparent, plus
a `border-left:2px solid rgba(217,168,63, .18+.52*t)`. A slot below the floor gets
`.gtile.leanx{border-left:2px dashed var(--line2)}` and no wash — **unknown is visibly not
cold**. Champagne on warm graphite, the §GG accent, never purple. Nothing rotates, grows or
glows; hover is a colour step; the selected tile keeps its `.on` outline. Colour is **never
the sole channel** — every tinted tile has a numbered sortable row beside it and the Lean
cell repeats the wash.

**`.dcenter` at rest holds the ranking** — `<table class="cs-lean data">`, 16 rows ×
~22px + header ≈ 380px, inside the doll's own height.

| # | header | cell | sort key / type |
|---|---|---|---|
| 1 | `SLOT` | `CS_SLOT_LABEL[slot]` | string, localeCompare |
| 2 | `TOP PIECE` | 22px `csIcoHTML(e,ph,true)` + plain-text name. **The icon is the sole wowhead surface** (pref #11); the name is inert text | the plain name; unresolved → `null`, parks last |
| 3 | `LEAN %` | `24%`, right-aligned, cell background a champagne wash at the same `t` | number; **default, descending**; `null` → `–` |
| 4 | `n` | `n=3,015` — live covered wearers `W(si)` | number |
| 5 | `COV` | `84%` — `cov(si)` | number |

Off Hand is suppressed on 2H specs by the doll's existing `ohNone` test, so the two
surfaces cannot disagree. Every column sorts, both directions, through Part 3.

**Clicking a slot tile** behaves exactly as today: `.dcenter` swaps the ranking for the
fold-out, caret aimed at the owning tile; closing restores the ranking. They are alternates,
never neighbours — no scroll, no reflow, no pane growth. In lean mode the fold table
(§3.4) carries two extra sortable columns: **`LEAN %`** = `e.iup`, `title` *"season-wide:
N% of this piece's wearers carry it above its usual item level of `e.ilvl`"* — the one
place item level survives, as a baseline; and **`FIELD`** = the `csFieldIup` median, `null`
under 3 specs → `–`, `title` *"median across the N other specs that list this piece"*.

**Below 1151px** `.dcenter` reflows to `grid-column:1/-1; grid-row:2` (`:559-563`) and a
table there would add ~380px of real height. So under that breakpoint the ranking degrades
to a **two-row chip strip** — 16 chips, `Ring 1 41%`, ~60px total, same tint scale, no
table; tint and fold behaviour unchanged. At 1366×768 the pane is tiles + chip strip,
inside the §C.1 bar. **No page-level horizontal scroll at any width**: tables live in
bounded columns, long names ellipsis with a `title`, and each wrap carries
`overflow-x:auto` so a pathological name scrolls its own container, never `body`.

## 2.7 Caption and scope wording — verbatim, all three are mandatory

Section scope line in lean mode (third argument to `csSecOpen`, replacing `:3444`):

> `upgrade lean · per-item rates are season-wide, the mix weighting them is your current lens · covers 78% of 12,481 gear-known in window — 22% fall outside the item vocabulary or below the 20-wearer floor`

Naming both exclusion reasons is required — it lets the owner tell a vocab-cap problem from
a thin-data one, and under today's halved caps 15.2% of Ret head wearers already fall into
`other`. Always-visible footnote beneath the ranking, **a rendered line, not a tooltip**:

> `Lean = the share of a piece's wearers carrying it above that piece's own most common item level. The piece is its own baseline, so a generous loot table cannot inflate it. Above its own baseline means an upgrade spent here — or a luckier drop source; the log records no upgrade track, so we do not claim which. It measures disagreement: a piece every one of its wearers carries high scores LOW. Slide the percentile lens to see whether the top decile leans differently than the field.`

Column header `title` on `LEAN %`:

> `share of this item's wearers carrying it above the item level most of its wearers carry`

## 2.8 Honesty gates the client owns itself

1. **Duplicate-id scan.** Before enabling the mode, scan the spec's item vocab for a
   repeated `id` within one slot. If any exist the §2 embellishment fix has not landed in
   this build and `iup` would be a percentage over an arbitrary partition of a piece's
   wearers — the toggle is suppressed **silently**. Second of two independent locks on the
   one failure mode that produces a confidently wrong number.
2. **Raw-id leak, same pass (pref #11).** Audit every embellishment display path
   end-to-end — `csCraftedModel:3538` and `csCraftedHTML:3562` — and route all of them
   through `embOf` (`:3110`, which already carries the `/^#\d+$/` filter) so no `#<digits>`
   string can reach the DOM. The shipped vocab is dominated by `"#6652"`.

---

# PART 3 — THE UNIVERSAL SORTING CONTRACT

The owner's words are a **standing rule**, not a one-off: the mechanism must make a table
sortable *by construction*, so a future table gets it automatically rather than by someone
remembering.

## 3.1 Four helpers, beside `cmpCells` (`site/index.html:3927`)

`cmpCells` is unchanged and remains the ONE comparator: click toggles, repeat flips,
strings via `localeCompare`, and `null` / `""` / non-finite parked **last in both
directions**. Add:

- **`sortState(id, defaultCol, cols)` → `{col,dir}`** — one module-level registry keyed by
  table id, replacing `state.csort`, `state.ssort`, `state.tsort`, `state.pulseSort` and
  module-level `frameCSort`. Applies the stale-column guard
  (`if(!cols.some(c=>c[0]===st.col))` → default) that three sites carry today and two do
  not. Default `dir:-1` (first click descending).
- **`sortHead(cols, st)` → `<thead>…</thead>`** — the **only** path to a header row. `cols`
  entries are `[key, label, cls?]`; stamps `data-c` and `class="… sorted asc|desc"`. **A
  `null` key emits a bare `<th>` with no `data-c`** — the genuinely non-sortable column
  (expander, copy button, icon). With no other path to a `<thead>`, a future table
  physically cannot ship an unsortable header.
- **`sortRows(rows, st, accessor)`** —
  `[...rows].sort((a,b)=>cmpCells(accessor(a,st.col),accessor(b,st.col),st.dir))`. **Caps
  are applied by the caller AFTER the sort** — house rule at `:2679`/`:4774`, so sorting by
  `worn` ascending yields the rarest known rows, not a reordered top slice.
- **`wireSort(host, st, rerender)`** — `host.querySelectorAll("th[data-c]")`, **with the
  attribute filter**, fixing by construction the live defect at `:4615`, `:4803`, `:5215`
  (bare `th`): the day a grouping or action column appears, `th.dataset.c` is `undefined`,
  `bad()` is true for every row, the comparator returns 0 throughout, and the table
  **silently keeps its order showing no indicator** — no throw, no warning.

Each existing call-site collapses to four lines:

```js
const st=sortState("comps","strength",COLS);
const view=sortRows(rows,st,val).slice(0,COMPS_MAX);   // cap AFTER the sort
h=sortHead(COLS,st)+body(view);
wireSort(t,st,()=>renderComps(A));
```

## 3.2 Markup / attribute convention (the "by construction" part)

1. Every sortable table declares a `cols` array of `[key,label,cls?]`; `null` key = not
   sortable. 2. Every header comes from `sortHead` — a hand-written `<thead>` is a review
   failure. 3. Every accessor returns a `Number`, a `String`, or **`null`/`NaN` — never the
   string `"–"`**; the dash is produced by the *formatter*, not the model. This is the one
   semantic trap: `–` as text sorts as text and interleaves, breaking the single rule the
   comparator exists to enforce. 4. A column that does not apply in the current state
   (`VS B` when `state.compare` is off) is **absent from `cols` entirely** — not blank, not
   `–`, and therefore not sortable. Nothing dormant.

## 3.3 Header affordance — §GG, static, nothing rotates

Already in the sheet at `:391-394` and now owned by `sortHead` so all tables inherit it:
`.data th.sorted{color:var(--accent)}`, `.desc{box-shadow:inset 0 -2px 0 var(--accent)}`,
`.asc{box-shadow:inset 0 2px 0 var(--accent)}`. **No arrows, no carets, no triangles,
nothing that rotates** (pref #3). Header hover is `color:var(--ink)` only; row hover is
`background:rgba(234,227,208,.03)`, neutral warm, no growth, no glow (pref #7). Extend the
same three rules to `.cs-ench`, `.fold-t`, `.cs-tal`, `.cs-fold` and `.cs-lean`.

## 3.4 Every table, named — conversion list

**Migrate to the helpers (behaviour-identical, five hand-rolled copies deleted):**

| table | fn | today |
|---|---|---|
| `#comps` Top Comps | `renderComps` :4650 | `state.csort`, **bare `th`** :4803 |
| `#settbl` Tier set bonus | `renderSetBonus` :4567 | `state.ssort`, **bare `th`** :4615 |
| `#tbl` main Data Table | `renderTable` :5117 | `state.tsort`, **bare `th`** :5215 |
| `#pulse` Meta Pulse | `renderPulse` :4307 | `state.pulseSort`, `th[data-c]` ✓ |
| `.data` Spec Frame mini-comps | `frameCompsHTML` :2654 | `frameCSort` (module-level, reset at :2483, :3218) |

**Opt in — the character screen, all five, each keeping its present order as the default so
nothing visibly changes until a header is clicked:**

| table | fn | `cols` |
|---|---|---|
| `.cs-ench` :3525 | `csEnchHTML` :3491 | `[slot, enchant, share, n, null]` — the expander is the `null` column |
| `.fold-t` Crafted worn :3554 | `csCraftedHTML` :3545 | `[slot, item, share, n]` |
| `.fold-t` Embellishments :3561 | `csCraftedHTML` :3545 | `[name, carries, share]` |
| `.cs-tal` :3777 | `csTalentsHTML` :3723 | `[build, share, med, n, hero, null]` — copy button is `null` |
| **`#cs-fold`** :3407 | `csFoldHTML` | **markup decision, resolved here** |

**`#cs-fold` becomes a real table.** It renders `.frow` divs today and cannot satisfy a
standing "every table sorts" rule without a markup decision. Convert to
`<table class="cs-fold data">`, columns `[null(icon), item, share, n]` plus
`[lean, field]` in lean mode, default **share-descending** (today's order). The `.fbar`
share bar becomes a cell-background gradient on the Share cell — same information, same
colour language, no layout shift. The `other / none` row stays pinned last regardless of
sort (it is an aggregate, not a row of data) and its numeric accessors return `null`.

**Exempt, and stated so the contract is unambiguous:** `tipHTML` :2384 and `.frows` :2642
are transposed key/value blocks with no column axis. `.fstat` :2723 may become sortable but
**p50-descending must remain the default** — blueprint §3 is explicit that the p50-desc
order *is* the priority read.

## 3.5 Sort state across a live re-slice

Sort state lives in the `sortState` registry, outside every render function, so a filter
change, a lens tick, a period switch or a compare toggle re-renders the rows and **keeps
the column and direction**. Three rules: (1) the **stale-column guard runs on every read** —
if the active column is not in the current `cols` (`VS B` when compare goes off, `hero`
under merge) the state reverts to the table's default rather than sorting on nothing;
(2) **switching the framed spec resets** character-screen sort state, exactly as
`resetScreenPerSpec()` (`:3209`) resets `screenFold`/`screenBuild`; (3) sorting **never
re-slices** — it re-renders its own table only, never `render()`, except where a call-site
already does so today (`#pulse`, `#tbl`).

## 3.6 `fleet/user_prefs.md` gains standing preference #12 (and #13 for Part 1)

Written into that file by this change.

---

# BUILD ORDER

**Ships this week, zero data dependency, no rebuild:**

1. **Part 3** — extract the four helpers beside `cmpCells` (`:3927`); migrate the five
   existing call-sites (fixing the bare-`th` defect at `:4615`/`:4803`/`:5215` by
   construction); opt in all five character-screen tables including the `#cs-fold`
   div→table conversion. Request (B) lands whole. Write prefs #12/#13.
2. **Part 1 + §2.8.2** — delete `:3468` and `:3427`, fix the comment at `:3422`, close the
   `embOf` raw-id path. Request (A)'s first half lands. ~1 hour, no rebuild.

**Gated on the pipeline:**

3. **§2 embellishment-marker fix to production.** Blocking for everything below.
4. **§1.8 `iup`** in `build_site_data.py` (~15 lines around `:1484`/`:1541`: accumulator →
   `dict[(ch,sv)→ilvl]`, mode + strictly-above count, emit at ≥20), the fail-closed
   uniqueness gate, the invariant test, full sidecar rebuild. `data.json.gz` and
   `stats.json.gz` untouched; row alignment not in play.
5. **Part 2 client** — `gearMode` + toggle + hash suffix (~25 lines), `csGearModel`
   extension (~15), `csLeanRows` (~25), `csLeanTableHTML` on the Part-3 helpers (~35),
   `csFieldIup` (~20), CSS (~30). ~150 lines net, `site/index.html` only — no new file, no
   new fetch, no library. It is the helpers' first *new* consumer and the proof the
   abstraction is right: if the lean table needs no bespoke sort code, it is.

# RISKS

1. **The emb fix slips and `iup` ships over split entries** — highest consequence, numbers
   confidently wrong rather than absent. Two independent locks: the emitter refuses to
   write `iup` (§1.8) and the client refuses to enable the mode (§2.8.1).
2. **Saturation.** `iup` is a *disagreement* statistic: a piece every wearer carries high
   has its mode at the high level and scores ~0, identical to a piece nobody pushes. Stated
   in the mandatory footnote; `FIELD` is the partial detector (a spec whose whole
   population moved shows a low own-rate against a mid field rate).
3. **Within-item source multiplicity.** Holding the id fixed cancels *between*-item source
   differences, not *within*-item ones: a piece dropping from raid at 311 and vault at 298
   reads leaning with zero currency spent. Named in the footnote; not claimable away.
4. **Season-wide baseline vs. a short lens.** Item levels inflate across a season, so a
   two-week lens understates current behaviour; the scope line states the split. **Do not
   add a period delta** — `iup` is period-invariant, so any such delta is a pure mix shift
   and would read as "the field upgraded rings this week", which is a lie.
5. **Halved vocab caps depress coverage.** Coverage is in the scope line with both reasons
   named; the 50% slot floor blanks any slot it undermines. Restoring rung-1 caps improves
   this pane independently — the halving bought almost nothing (94% of bytes are `cols`).
6. **Colour as a channel** — every tinted tile has a numbered sortable row beside it and
   the Lean cell repeats the wash; colour is never load-bearing alone. **`–` sorting as
   text** — the trap Part 3.2.3 prevents; worth one unit test per converted table's
   accessors.
