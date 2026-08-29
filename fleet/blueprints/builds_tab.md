# BLUEPRINT — Builds deep-dive: the Character Screen · build-ready · 2026-08-29

Contract: fleet/feature_builds.md. Skin: fleet/design_language.md **including §GG Gilded
Glass**. Owner prefs override everything. Two build agents work from this file alone:
PIPELINE (scripts/*) and CLIENT (site/index.html). §1 is the interface contract — neither
agent may deviate from it without updating this file first.

## 0. Verified ground truth (2026-08-29 — do not re-litigate)

**Journal (scripts/fetch_data.py, compact_gear L447 / compact_talents L484).** Per gear
record: positional `gear` list (index = retail slot: 0 head, 1 neck, 2 shoulder, 3 shirt,
4 chest, 5 waist, 6 legs, 7 feet, 8 wrist, 9 hands, 10/11 fingers, 12/13 trinkets, 14 back,
15 mainhand, 16 offhand, 17 tabard — pinned by TRINKET_SLOTS=(12,13) and the "15:ench"
convention in build_site_data.py). Per item: `id` always; `ilvl`, `set`, `ench`
(SpellItemEnchantment id), `gems` (list, entries may be ints OR dicts {id,...} — WCL's
shape varies; take `.id` when dict), `bonus` (bonusIDs list) when non-empty. **`quality` is
NOT stored** (trimmed as presentation noise) → crafted/embellishment identification MUST
come from item id + bonus ids (§2 — verified workable). Talents: `tree` [{id,rank}],
`talentImportString`, `specID`, `heroTalentTreeID` (when WCL sends it), `stats`.
**Hero tree is already resolved per payload row**: fetch_data's HeroResolver (votes over
tree node ids via data/hero_talent_map.json) writes `hero_talent` into every run row; the
payload ships it as `rows.hero` indexing `D.heroes` (42 names). The sidecar therefore
ships NO hero column — the client cross-tabulates `R.hero` live.

**Payload today**: N = 439,963 rows, 17 row arrays, season "Midnight Season 2". The gear
journal lives on the collector (data/processed/gear.jsonl, gitignored here); coverage
fraction is unknowable from this checkout — the pipeline prints it, the ladder (§1.4)
absorbs it.

**Archon (fetched via web.archive.org snapshots of both contract URLs; live host is
Cloudflare-challenged to curl — structure verified from `__NEXT_DATA__`)**: overview page
= 3 sections (BuildsStatPrioritySection #stats: per-stat histogram intervals + sampleCount;
BuildsTalentTreeBuildSection #talents: `talentTreeBuildSets[].alternatives[]` each
{title "Recommended/Alternative Class Tree #N", popularity "9.5%", keystoneLevel,
talentTree.dehydratedBuild.selectedNodes, exportCodeParams.exportCode = the import string},
blueprint carries `heroTrees:[{name,id}]`; BuildsBestInSlotGearSection #gear-overview:
12 gear + weapons + trinkets entries, each an icon-component string with item id, stat
combo, gems[], enchants[], `topLabel` share % + `bottomLabel` "11.5k parses"). The
gear-and-tier-set page adds per-slot tables (14 tables: item/Max Key/DPS/Popularity rows),
crafted-gear (15 rows), crafted-gear-stats (missives), embellishments (rows are PAIRS of
embellishment names), tier-set. We copy none of this presentation; we keep the owner's
subset: per-slot most-common at a glance + share, expandable per-slot lists, crafted +
embellishments, enchants, talent builds with share/DPS/import-string, hero logic.

**Client (site/index.html)**: stats sidecar loader L2182 (lazy, cached promise, silent
failure), decoder L2205 (layout-tolerant, `n!==N ⇒ reject`), `frameLiveIdx()` L2237 (the
one row-pass incl. the Archon-replica branch), lens math in frameLiveStatsHTML L2461
(rank within view by `dpsAt`, window `[max(0,pctl-10), min(100,pctl+10)]`), block registry
`FRAME_BLOCKS` L2506 ({id,has,html,wire}), `renderFrame()` L2530, frame markup ~L825
(.fhead: dot, name, hero, scope(margin-left:auto), pin, ×; #frame-blocks flex-wrap).

## 1. Interface contract — site/builds.json.gz

### 1.1 Exact JSON (gzipped by the same writer path as stats.json.gz)

```jsonc
{"v":1, "n":439963,                  // MUST equal the payload rows length
 "enc":"sparse",                     // or "dense"
 "slots":[0,1,2,4,5,6,7,8,9,10,11,12,13,14,15,16],   // item columns, this order (no shirt/tabard)
 "eslots":[0,4,6,7,8,10,11,14,15,16],// enchant slots, MEASURED: slots where ≥1% of
                                     // gear-known records carry `ench`; order ascending
 "idx":"<b64 LE Uint32Array>",       // sparse only: payload row index per covered row
 "cols":{                            // column-major, one b64 per column, covered-rows length
   "fl":"<b64 Uint8>",               // bit0 = gear list present, bit1 = import string present
   "it":["<b64 Uint8>", ...],        // len == slots.length; value = 1-based index into the
                                     //   row's spec vocab for that slot; 0 = other/empty
   "en":["<b64 Uint8>", ...],        // len == ceil(eslots.length/2); PACKED NIBBLES:
                                     //   byte j: low = eslots[2j], high = eslots[2j+1];
                                     //   nibble 0 = none/other; vocab cap 15 per slot
   "bld":"<b64 Uint8>"},             // 1-based talent-build vocab index; 0 = other/unknown
 "specs":{"Paladin|Retribution":{    // key = "class|spec", same convention as specmeta
   "items":[[{"id":249961,"n":"Luminant Verdict's Unwavering Gaze","ilvl":723,
              "cr":1,"emb":"Radiant Hem"}, ...], ...],
                                     // outer list aligned to `slots`; inner sorted by
                                     // descending count over ALL journal-known rows of the
                                     // spec; cap 24/slot (40 for slots 12,13,15,16);
                                     // "n" null when unresolved; "ilvl" = median observed;
                                     // "cr":1 iff item id ∈ crafted set; "emb" only when
                                     // the entry was split out by embellishment identity
   "ench":[[{"id":8017,"n":"Empowered Rune of Avoidance"}, ...], ...],  // aligned to eslots, cap 15
   "builds":[{"s":"CYEAAAAA...","n":412}, ...]}}}   // cap 40, desc count; s = verbatim
                                     // talentImportString; n = journal count (context only —
                                     // the client always recounts within the lens window)
```

Vocab entries whose item id + embellishment-marker bonus (§2) co-occur in the journal are
SPLIT: one entry per (item id, embellishment) pair actually observed, `emb` naming it (or
`"emb":"#<bonusid>"` when unnamed). Crafted-ness is an item-id property (`cr`), so
crafted/embellishment need NO row columns — shares fall out of the item distributions.

### 1.2 Alignment guarantee (the whole contract)

Emitted from ONE walk over the build's df in payload row order — the same discipline as
stats_sidecar L705 (never a separate join). A covered row = journal record found by
`_gear_key` with a gear list or an import string. Client MUST reject (treat as absent,
console.warn) when: `n>>>0 !== N`, any col missing/short for its declared length, or
`slots`/`eslots` are not arrays. Unknown extra keys are ignored (layout tolerance — the
same discipline that let `flaskcol` come and go).

### 1.3 Decode (client, mirror of decodeStatsSidecar)

`BUILDSC = {map:Int32Array(N) payload row→covered row (-1 unknown), fl:Uint8Array, it:[Uint8Array×16], en:[Uint8Array], bld:Uint8Array, slots, eslots, specs}`.
Accessors: `itV(s,i)=it[s][map[i]]`; `enV(j,i)= j%2 ? en[j>>1][map[i]]>>4 : en[j>>1][map[i]]&15`;
vocab lookup for row i resolves via the row's OWN spec: `specs[D.classes[R.cls[i]]+"|"+D.specs[R.spec[i]]]`
(absent spec key ⇒ every value renders as other/unknown — never throws).

### 1.4 Size: measured estimate + ladder

23 B/row of columns (16 it + 5 en + 1 bld + 1 fl). Simulated at N=439,963 with correlated
loadouts (60% archetype copies, modal shares 0.6–0.95, zipf tails — calibrated to Archon's
55–98% modal slots): **dense ≈ 3.5 MB gz** (worst-case i.i.d. bound 5.5 MB); **sparse at
55% journal coverage ≈ 2.5 MB; at 35% ≈ 1.6 MB**. Vocab block adds ~0.2–0.4 MB gz
(≈19k item entries, names dedupe well). Expected ship today: sparse, ~2–2.5 MB gz.
Ladder, exactly stats_sidecar's shape, loud at every step: build both encodings, ship the
smaller; if > 3.0 MB target → halve vocab caps (24→12, 40→20, builds 40→24) and rebuild;
if still > 3.0 → drop `cols.en` + `ench` vocab entirely (client feature-detects: no `en`
⇒ no enchant block); if > 5.0 MB cap → do not ship the file. Pinned by test.

## 2. Name vocabulary pipeline (PIPELINE agent)

**Source: wago.tools db2 CSV exports — verified working 2026-08-29** against Archon-current
ids (all four fetches returned 200 with the expected fields):
- Item names: `https://wago.tools/db2/ItemSparse/csv?filter[ID]=exact:<id>` → `Display_lang`
  ("Luminant Verdict's Unwavering Gaze" for 249961). One request per unseen id
  (`any:` multi-id filtering does NOT work — verified empty).
- Enchants: `.../db2/SpellItemEnchantment/csv?filter[ID]=exact:<id>` → `Name_lang`
  (8017 → "Enchant Helm - Empowered Rune of Avoidance |A:...|a"). Strip `\|A:[^|]*\|a` atlas
  tags and the leading `Enchant <anything> - ` prefix. (wowhead's nether tooltip endpoint
  has NO enchantment entity — 404 "invalid"; items-only there. We standardize on wago.)
- Crafted set: `.../db2/CraftingData/csv` (whole table, 133 KB) → the set of
  `CraftedItemID` values (222435 Everforged Vambraces verified present).
- Embellishment markers: bonus ids whose `ItemBonus` rows (Type=35) set an
  `ItemLimitCategory` whose name contains "Embellished" — today 8960→512 "Embellished"
  and 13555→697 "Outdoor Embellished" (both verified). A journaled item is embellished
  iff its `bonus` list intersects the marker set. Identity: (a) inherently-embellished
  items carry LimitCategory 512 in ItemSparse directly (6 items today, e.g. 251073
  Voidstone Shielding Array) — name = item name; (b) optional-reagent embellishments:
  resolve bonus→reagent through ItemBonusTreeNode(ChildItemBonusListID)→ParentItemBonusTreeID
  →ModifiedCraftingReagentItem(ItemBonusTreeID)→reagent item name where the chain closes,
  else record `null` and display `#<bonusid>` — data/names_bonus_emb.json accepts MANUAL
  entries (grow-only union), so the owner can name stragglers once per season.

**Cache files (committed, grow-only — merge, never overwrite):**
`data/names_items.json` `{ "<itemid>": {"n": "...", "q": 4} | {"n": null} }`,
`data/names_enchants.json` `{ "<enchid>": "cleaned name" | null }`,
`data/crafted_ids.json` sorted int list, `data/names_bonus_emb.json` `{ "<bonusid>": "name" | null }`.

**Flow**: new `scripts/fetch_names.py`, run by the collector workflow BETWEEN fetch_data
and build_site_data. Reads the journal, diffs ids against the caches, fetches ONLY unseen
ids (typically dozens/week; 150 ms sleep between requests, UA
"wowlogs-collector/1.0"), rewrites caches sorted (stable diffs), commits with the weekly
journal export. CraftingData + the marker probe refresh whole-table once per run (2 small
fetches). **Failure behavior**: any fetch error → keep the cached value (or store nothing,
NOT null — null means "asked, source had no name" and is never re-asked; absent = retry
next run), print a summary line, exit 0. build_site_data.py never fetches — it reads
caches only, and a missing cache file degrades to all-null names. Client fallback for
`"n":null`: render `#<id>` linking `https://www.wowhead.com/item=<id>` (plain `<a>`,
target=_blank; enchants render `enchant #<id>` unlinked). No third-party scripts, ever.

## 3. The Character Screen (CLIENT agent) — OWNER DECISION 2026-08-29, binding

Performance stays the existing bottom rail, untouched and glanceable. **Builds is a
TOTAL MAIN-COLUMN TAKEOVER**: a full character screen for the framed spec replacing
EVERYTHING in the main column below the top bar — no rankings remnant, no KPI strip, no
rail, no peek or summary of any other dashboard element (owner: "anything not in the
area of those two bars is irrelevant in that mode"). Normal PAGE FLOW — no inner scroll
containers, no overlay, no modal, no dimming, measure-bounded exactly like the rest of
the page (`main` ≤1200 px, 42 px side padding). The COMPLETE control surface is the
sidebar filters + the top bar (header + sticky lens bar) — both stay visible and fully
interactive, and the ENTIRE screen re-slices live under them — the owner's anti-Archon
property. The screen's own furniture is minimal by decree: identity band, a slim
spec-hop affordance, the back affordance — nothing more; the canvas is 100% the spec.
Immersive, never restrictive: page flow, persistent controls, lossless exit.

### 3.1 Enter / exit / state

Entry affordances (both): a `Character screen →` .btn in the rail's `.fhead` (after
`#frame-scope`, before `#frame-pin`; .72rem compact variant), and the rail's spec NAME
(`#frame-name`) becomes the same trigger (inline span, content-hugging, hover color-only).
`state.screen ∈ {null, true}` — the SPEC is still `state.frameKey` (one source of truth;
the screen is a projection of the framed spec). Entering: record `window.scrollY` into
`screenReturnY`, set `state.screen=true`, `renderScreen()`, `window.scrollTo(0,0)`.
While open: `document.body.classList.add("charscreen")`; CSS hides the page's section
list and the rail (`body.charscreen #sections, body.charscreen #frame-pos{display:none}`
— the CLIENT agent wraps the existing top-level sections in ONE `<div id="sections">`;
header, hero rule, and the sticky lens bar stay outside it and remain live). The rail's
state (frameKey, pin, comps sort) is preserved untouched behind the screen.
**Exit is deliberate work (owner addendum): the screen is a destination, not a popup.**
Exactly two exits, both explicit: the `← Rankings` .btn and the wordmark
(cursor:pointer only while `body.charscreen`). NO Esc-to-exit, NO click-outside-to-exit:
while `state.screen`, the Esc branch of the keydown handler does nothing (and must not
fall through to close the underlying rail state), and the pointerdown click-away
listener returns early; clicking anywhere on the screen or working the sidebar/lens
never leaves the mode. This contrasts DELIBERATELY with the Performance rail, which
keeps its light Esc + click-away dismissal — the rail is a peek, the screen is a place;
the client implements both behaviors side by side. Exit restores: sections un-hide, rail
re-renders exactly as left (frameKey intact, pinned state intact),
`window.scrollTo(0,screenReturnY)` on the next frame — the exact prior scroll position
and page state; nothing trapped, just nothing accidental. Screen state is NOT serialized
to the URL; reload lands on rankings. Re-entering goes straight to data (sidecar cached).

### 3.2 Screen layout (top to bottom, all in page flow)

1. **Ladder strip** — ONE slim row, the screen's only furniture above the identity band:
   `← Rankings` .btn, then rank-ordered spec chips = the chart's CURRENT ranking
   (`CHART_KEYS` under live filters): 9×9 class dot + spec name, §15.4 chip treatment,
   active chip accent-bordered (§GG sheen); chips wrap (measure-bounded, never a
   horizontal scroller). Click = switch spec in place; ArrowUp/Down still steps the
   ladder (same CHART_KEYS walk as the rail — reuse the existing keydown handler; it
   re-renders the screen when `state.screen`). The strip re-orders live when filters
   change the ranking — chips repaint, active key stays unless filtered out (then: the
   identity band shows its "no parses match" state, chips offer the way out; never
   auto-exit).
2. **Identity band** — minimal: class dot + "Class Spec" (Inter 600, 1.05rem — NOT
   Marcellus; the wordmark stays the page's only display-face use) + "· Hero" when
   unmerged, the scope line (`frameScope()`), one inline row of the identity block's key
   numbers (n · median DPS at lens · timed rate — reuse frameIdentityHTML's computed
   values, laid horizontally, no KPI cards), and the shared lens sub-line (§3.3b):
   `players around p60 (lens ±10) · n=214 of 1,842 in view · gear known 87% · builds
   known 91%`. Comps and character stats STAY in the Performance rail — the screen is
   the builds surface (owner's structure list), and it duplicates no dashboard element.
3. **Gear overview grid** (§3.4 geargrid).
4. **Per-slot distributions** (§3.4 slotdetail).
5. **Crafted & embellishments** (§3.4 crafted).
6. **Enchants** (§3.4 enchants).
7. **Talent builds** (§3.4 talents).
Sections 3–7 use the §15.9 `.sec` header treatment (static −/+, champagne tick,
collapsible) so the screen reads as native dashboard, not a foreign pane. All content
sits directly in page flow — the page scrollbar is the only scrollbar.

### 3.3 Loading & absence (nothing dormant)

`loadBuildsSidecar()` mirrors loadStatsSidecar verbatim (cached promise, Decompression-
Stream guard, `fetch("builds.json.gz",{cache:"no-cache"})`, silent catch, re-render on
success). Kicked ONLY by the first screen entry (contract: second lazy sidecar). On entry
while pending: the ladder strip + identity band render immediately (payload data), the
block area shows the §7 loading treatment (120×2 px --line1 track, 40 px --accent
slider, "Loading builds data…" --ink3) — no skeleton grids. On fetch failure or decode
reject (§1.2): exit the screen automatically (full restore), remove BOTH entry
affordances for the session, console.warn — the affordance never sits dead, honoring
"the mode button may simply not render until data is loadable" on the only knowable
signal: the fetch result. Success: `renderScreen()` fills sections 3–7.

### 3.3b One lens, one slice (the semantics guarantee)

REFACTOR, do not duplicate: extract from frameLiveStatsHTML the window computation into
`frameLensSlice()` → `{idx, Y, lo, hi, sdps, inWin:Set<rowIdx>}` (rank rows of
`frameLiveIdx()` by `dpsAt` within the current view, keep percentile ∈ [max(0,pctl−10),
min(100,pctl+10)], ends clamped). frameLiveStatsHTML consumes it unchanged (stats output
byte-identical — verify by eye against a live filter set); every screen section consumes
the SAME object (frameLiveIdx reads `state.frameKey`, which the screen shares). Elite/
Archon mode needs zero builds-specific code — frameLiveIdx's elite branch already feeds
the slice, the scope line already says "Archon replica". Every upstream control
(dungeon, region, keys, period, timed only, tier cohort, hero/merge, projection, the
pctl slider) already funnels into the master refresh that ends in `renderFrame()`
(L2865 "last: every control refresh reaches an open frame through this one path") —
`renderFrame()` becomes the dispatcher: `state.screen ? renderScreen() : <rail render>`,
so the whole canvas re-slices live with zero new wiring. The lens sub-line (§3.2.2)
prints n for the window and both coverage denominators from `fl` bits.

### 3.4 Screen sections (all live-only; every count from the lens slice)

**geargrid — "Gear overview"** (the owner's #gear-overview ask). CSS grid
`repeat(auto-fill,minmax(132px,1fr))` (16 tiles ≈ 8×2 on the 1116 px content width,
4-up ≤900px). Tile (panel recipe on --surface1, --r1, §GG stacked shadow + top-edge
lip): slot label (.62rem caps --ink3: HEAD, NECK, …, MAIN HAND, OFF HAND), most-common
item name (.78rem --ink, 2-line clamp, `"n":null` ⇒ `#id` wowhead link), share (tabular
.8rem 650 --ink) + `n` (.68rem --ink3), med ilvl (.68rem --ink3, "ilvl 723 · all
records" honesty — vocab annotation, not lens-sliced). CRAFTED tag (.6rem caps, --line2
border, --r1 — the §15.13 tag treatment, never accent) and `· <emb>` suffix when the
winning entry carries them. Winner = argmax count of vocab values over gear-known slice
rows; value 0 competes as "other/none" (offhand legitimately shows it for 2H specs).
The whole tile is a content-hugging trigger: click SELECTS the slot for the per-slot
section below (active tile: border-color --accent-line — color-only, nothing moves).

**slotdetail — "Per-slot distribution"** (its own .sec, directly under the grid). A
selected-slot table (default slot 0 Head; tile click or a 16-chip selector row switches
it — chips mirror the tiles for keyboard/narrow use). Rows (≤10 + "other/none"):
item name | flat share bar (hairline track rgba(234,227,208,.07), fill
rgba(234,227,208,.28) — neutral: hue belongs to data, champagne stays ACTIVE-only) |
share % · n · med ilvl · CRAFTED/emb tags. Entries with n<3 in the window fold into
"other" (SPECMETA_ENTRY_MIN echo). Selection swap is instant (no height animation —
the section keeps one table's height class).

**crafted — "Crafted & embellishments"**. Two mini-tables side by side (stack ≤900px).
(1) Crafted worn: rows = slots where a `cr` entry appears in the slice: slot · top
crafted item · share of gear-known · n. (2) Embellishments: aggregate vocab entries by
`emb` across ALL slots: name · players · share — denominator = gear-known rows in
window; caption states "counted per slot; a player can carry two". Zero crafted entries
in the slice ⇒ the whole .sec does not render (never an empty shell).

**enchants**. Table: one row per eslot with any data: slot label · top enchant name ·
share of enchant-known · n; click row → the row expands its alternatives list in place
(accordion-of-one, static −/+ marker, instant). Share denominator = slice rows with
`fl&1` (an empty nibble on a gear-known row IS "unenchanted" — a real zero; show it as
the "none" line when it wins). Absent `cols.en` (ladder step) ⇒ section absent.

**talents — "Talent builds"** (+ hero, per the owner's logic). Top rows (≤8 + "other"):
share bar (same neutral bar) · share % · median DPS over that build's slice rows
(`dpsAt`, fmtDps) · n · hero tag (see below) · `copy` button (.btn, .68rem, padding
.2rem .5rem) that `navigator.clipboard.writeText(vocab.s)` and swaps its label to
"copied" for 1.2 s (color-only state change; button only rendered when
navigator.clipboard exists — nothing dormant). Foot: "builds known for X of Y in window ·
identity = exact talent-string match". HERO LOGIC, exactly the contract's: **merged**
(`state.merge`): the section header carries a one-line hero distribution — for each hero
with ≥1 slice row (from `R.hero`, no sidecar data): name · share, as inline text chips
(.72rem, name --ink2, share tabular --ink), sorted desc; each build row's hero tag =
`D.heroes[mode of R.hero over that build's slice rows]` (.68rem --ink3) — an import
string encodes exactly one hero tree, so the mode is the identity, resolver noise aside.
**Unmerged / hero-zoomed** (frame key carries the hero): the distribution line and the
per-row tags do NOT render — the framed key is already one hero (identity band shows
"· Templar"); only the class+spec trees still differentiate builds, which the plain list
now expresses; foot gains "hero fixed — builds differ in class/spec trees only".

### 3.5 Edge cases (all must be built)

- **Thin n**: any section whose known-row count in the window is <10 renders its n-line +
  "sample too thin — widen the lens or filters" (.fsub) and no rows. Never fake bars.
- **No coverage for the spec** (spec key absent from `specs`, or zero `fl&1` rows):
  gear/crafted/enchant sections absent; talents may still render (strings arrive
  independently), and vice versa. All absent ⇒ the screen shows ladder + identity band +
  "no builds data for this spec yet" — honest, not dormant (data exists file-wide, just
  not here).
- **Framed spec filtered out** (frameLiveIdx empty / key gone from CHART_KEYS): identity
  band shows the existing "no parses match the current filters" line; sections 3–7 render
  nothing; ladder chips (built from the live ranking) are the way out. Never auto-exit.
- **Archon replica**: nothing special-cased (§3.3b). Scope line + lens semantics identical.
- **Merged + sidebar hero filter**: hero mask only applies unmerged (rowPass L1830) — no
  interaction; do not add one.
- **1366×768**: `main` is 1200 − 84 padding = 1116 px content — geargrid lands 8 columns;
  everything in page flow, page scrollbar only; no horizontal scroll anywhere (tables
  keep §15.15 .tblwrap overflow-x as their own bounded scroll, per the standing rule).
- **≤900px**: grid 4-up via auto-fill; crafted pair stacks; ladder chips wrap; sidebar
  unsticks exactly as today (the screen changes nothing about the shell).
- **Set Bonus / tier / comps / stats / Performance rail**: untouched files, untouched
  numbers. Tier items simply appear as ordinary items in geargrid.

## 4. Work split

### PIPELINE agent (scripts/, data/, .gitignore, workflow)

1. `scripts/fetch_names.py` (§2): journal scan → unseen ids → wago fetches → grow-only
   cache merge; CraftingData + embellishment-marker refresh; summary print; exit 0 on any
   network failure. No other script fetches names.
2. `build_site_data.py`: `builds_from_gear_journal()` (one journal read → per-key
   {gear, build string}; reuse meta_from_gear_journal or extend it — do NOT read the
   journal twice more); `builds_sidecar(df, journal, name)` per §1: one df-order walk,
   per-spec vocab assembly (counts over ALL journal-known rows of the spec in df, caps
   §1.1, entry split by embellishment identity, `cr`/`emb`/`ilvl`/`n` annotations from the
   §2 caches), fl/it/en/bld columns, dense+sparse, ladder §1.4, loud prints (coverage %,
   chosen enc, sizes). Wire into `build()` next to the stats sidecar write (both SITE_DIRS).
3. `.gitignore`: add `site/builds.json.gz`, `docs/builds.json.gz` (rebuilt every run).
4. `scripts/test_builds_sidecar.py`: synthetic journal + df → (a) n == len(df) and column
   lengths match declared slots (alignment); (b) a known row round-trips to the right
   vocab entries through a reference decoder implementing §1.3; (c) crafted flag appears
   iff id ∈ crafted_ids, emb split works, marker-bonus detection works; (d) ladder drops
   `en` then refuses over-cap; (e) talents-only and gear-only records set fl bits right;
   (f) `_gear_key` normalization reused (None server joins). Run it in CI with the
   existing tests.
5. Do NOT touch fetch_data.py, the payload schema, specstats/specmeta, or stats.json.gz.

### CLIENT agent (site/index.html only)

1. Extract `frameLensSlice()` (§3.3b); re-point frameLiveStatsHTML at it; verify stats
   render unchanged under (a) filters, (b) lens moves, (c) Archon replica.
2. `loadBuildsSidecar()` + `decodeBuildsSidecar()` per §1.3 (reject rules §1.2), globals
   `BUILDSC=null, buildsCP=null`.
3. Shell: wrap the existing top-level sections in `<div id="sections">` (DOM relocation
   only — zero compute changes; header, hero rule, lens bar stay outside); add
   `<div id="charscreen" hidden>` after it; `body.charscreen` CSS hiding #sections and
   #frame-pos. Verify the rankings page is pixel-identical with the wrapper in place.
4. Enter/exit per §3.1: `state.screen`, `screenReturnY`, the two rail affordances, the
   `← Rankings` button + wordmark exit — the ONLY exits: while `state.screen`, the
   keydown handler swallows Esc without acting and the click-away pointerdown listener
   returns early (the rail keeps its light Esc/click-away untouched — peek vs place,
   both behaviors verified side by side); scroll save/restore; session removal of the
   affordances on sidecar failure (§3.3). Screen never serialized to the URL.
5. `renderScreen()` + dispatcher: `renderFrame()` routes to it while `state.screen` (the
   L2865 master-refresh path then re-slices the whole screen on every control change,
   lens included). ArrowUp/Down + ladder chips both step `state.frameKey` through
   CHART_KEYS and re-render in place.
6. Screen sections per §3.2/§3.4 as SCREEN_BLOCKS entries `{id, has, html, wire}` —
   same registry pattern as FRAME_BLOCKS (which stays rail-only and untouched except the
   two entry affordances). Selected-slot + accordion state = module vars, reset on spec
   switch.
7. CSS: ladder strip, identity band, geargrid tiles, neutral share bars, tags, .sec
   reuse — radii 6/4, §GG materials, no rotation, no new tooltips (`title=""` nowhere
   new), hover = color only, nothing full-bleed, no inner scroll containers (only
   .tblwrap's standing overflow-x).
8. Trigger discipline: tiles, chips, rows, spec-name are content-hugging targets; empty
   cells inert. Click-away-close applies to the RAIL only — the screen never dismisses
   on outside clicks (it is the page).

### Pinned between them

File name `builds.json.gz` beside stats.json.gz in both publish dirs; JSON per §1.1;
reject rules §1.2; absence signals (`cols.en` missing ⇒ enchants section off; file
missing/rejected ⇒ both Character-screen affordances removed after the first failed
fetch); vocab 1-based with 0 = other/none; `n` must equal the payload's rows length or
the client ignores the file. Either agent changing any of these updates THIS section in
the same commit.

## 5. Acceptance narrative (persona check)

Owner clicks the Ret Paladin bar → the Performance rail opens, unchanged. They click
"Character screen →" — the rankings give way, in place, to a full-page character screen:
ladder strip of rank-ordered spec chips, identity band with the scope line and lens
sub-line, then the 16-tile gear grid — "Luminant Verdict's Unwavering Gaze · 98% ·
ilvl 723 · n=214"; the wrist tile carries CRAFTED · its embellishment; the enchant table
shows the helm rune at 84%; the talent section lists two builds at 61%/22% with median
DPS and copy buttons; the hero line reads "Templar 88% · Herald of the Sun 12%". The
sidebar and lens bar never left: they drag keys to +14–+16 and the whole screen
re-slices; they push the lens to p95 and the build order flips — the story Archon's
fixed page cannot tell. ArrowDown hops to the next spec without leaving the mode; a
stray Esc or click does nothing — the screen is a place, and only "← Rankings" (or the
wordmark) leaves it, returning to the rankings at the exact scroll they left. Every
section states its n. Nothing rotated, nothing purple, nothing full-bleed, and nothing
leaves by accident.
