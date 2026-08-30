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

### 1.5 Addendum — build identity without import strings (2026-08-29, WIDENING)

Production diagnostic: the journal's 430,507 talent records ALL carry `talents.tree`
and NONE carry `talentImportString` (the field never appears in real WCL summaries).
Build identity is therefore the canonical TREE HASH: nodes sorted by id, serialized
`"id:rank"` (null rank = 0) joined with `"|"`, md5 hex truncated to 12 chars, prefixed
`"t:"` (e.g. `"t:3f9a2c81d04b"`) — visibly not an import string. Two additions, both
widenings (unknown-key tolerance already admits them):
- Per-spec vocab blocks (sidecar `specs[k]` AND the specmeta spec entry) carry
  `"bkind":"hash"|"string"` when `builds` is non-empty — "hash" iff every emitted
  build value is a hash. The client suppresses copy affordances for hashes (a hash
  cannot be pasted into the game; render it as a build label, never behind copy).
- Mixed data rule, per value and authoritative over `bkind`: a `builds[].s` starting
  `"t:"` IS a hash; anything else IS a verbatim import string. If WCL ever starts
  sending strings, they simply win at read time (string preferred over hash per
  record) and `bkind` flips per spec.

### 1.6 Addendum — item icons (2026-08-30, WIDENING)

Per the "Gear presentation upgrade" contract: item vocab entries MAY carry
`"ic":"<icon name>"` (e.g. `"ic":"inv_helm_plate_raidpaladin_x_01"`), present only
when the grow-only icon cache (data/names_icons.json, wowhead item-XML resolved)
knows the id. The image is SELF-HOSTED at `icons/<ic>.jpg` relative to the page
(site/icons/, zamimg medium JPGs downloaded once by the collector, published by the
build, committed daily) — the client never hotlinks and renders its iconless tile
when `ic` is absent or the image 404s. Widening only: unknown-key tolerance already
admits `ic`; no column, no size ladder impact (vocab text only).

### 1.7 Addendum — talent trees + build selections (2026-08-30, WIDENING)

Verified on wago.tools: the journal's `talents.tree` ids are TraitNodeENTRY ids
(mapped to nodes via TraitNodeXTraitNodeEntry; confirmed against hero_talent_map).
Two additions:
- Sidecar build vocab entries MAY carry `"sel":[[nodeId,rank],...]` (sorted by node
  id) — the build's selection set converted to TraitNode ids, from the modal
  selection blob of that identity's records (identical by construction for hash
  builds). Absent when the trait-geometry cache is unavailable. Choice-node entry
  identity is NOT carried (node+rank only).
- A second lazy document `talents.json.gz` beside the sidecar (rebuilt-or-unlinked
  every build, gitignored): `{"v":1, "trees":{"Class|Spec":{"class":TREE|absent,
  "spec":TREE, "hero":{"<HeroName>":TREE}}}, "classes":{...}?}` with
  `TREE = {"nodes":[{"id","x","y","r","n","ic","t"}], "edges":[[a,b],...]}` — raw
  db2 grid positions, r = max ranks, t = TraitNode.Type, n/ic from the spell cache
  (null when unresolved; `ic` lives in the shared self-hosted icons/ store). When a
  top-level `"classes"` map is present, spec entries carry `"classRef":"<Class>"`
  instead of `"class"` (emitter ships whichever variant gzips smaller). Node
  membership = union of nodes the spec's players ever allocated (journal-wide);
  class-vs-spec pane split at the largest PosX gap; hero panes by TraitSubTreeID.

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
while `state.screen`, the Esc branch of the keydown handler NEVER exits or falls
through to close the underlying rail state — its only permitted action is closing an
open gear fold-out within the screen (§3.4 slotfold; with none open it does nothing) —
and the pointerdown click-away
listener returns early; clicking anywhere on the screen or working the sidebar/lens
never leaves the mode. This contrasts DELIBERATELY with the Performance rail, which
keeps its light Esc + click-away dismissal — the rail is a peek, the screen is a place;
the client implements both behaviors side by side. Exit restores: sections un-hide, rail
re-renders exactly as left (frameKey intact, pinned state intact),
`window.scrollTo(0,screenReturnY)` on the next frame — the exact prior scroll position
and page state; nothing trapped, just nothing accidental. Screen state is NOT serialized
to the URL; reload lands on rankings. Re-entering goes straight to data (sidecar cached).

### 3.2 Screen layout (top to bottom, all in page flow) — REVIEWED 2026-08-29

Two-judge layout review (owner directive: "ease of use… I don't want much clutter…
you can have sub-navs and other panes/pages/tabs"): both judges picked the
SUB-NAVIGATED shell over the single scroll (84/66, 86/66). Binding outcome: a 2-pane
`Gear | Talents` sub-nav with known-counts; the per-slot section AND its 16-chip
selector are DELETED, replaced by an in-place fold-out anchored to the clicked gear
tile; one hybrid element from the single-scroll variant survives — an always-visible
one-line build digest in the identity band, so the daily "which build do I copy"
needs zero navigation. Never reintroduce a second slot-navigation surface.

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
   **Build digest line (hybrid element, review-mandated):** one additional plain-text
   inline line closing the band — `top build 61% · median 2.31M · Templar 88% /
   Herald of the Sun 12%` + a compact `copy` .btn (.68rem) that copies the TOP build's
   verbatim import string (same clipboard behavior + "copied" swap as the talents
   rows; rendered only when navigator.clipboard exists). All values from the lens
   slice, identical to the Talents pane's top row — the digest is a mirror, never a
   second computation. The text portion is a content-hugging trigger that switches to
   the Talents pane (hover: color only). Hero-split segment follows the §3.4 hero
   logic: it renders merged-only; unmerged/hero-zoomed it is omitted (the band already
   says "· Templar"). Builds unknown in the window (<10 known rows, or spec absent
   from vocab) ⇒ the whole line is absent — never a dormant stub.
3. **Sub-nav** — §15.3 .tabs rail, exactly two tabs: `Gear` and `Talents`, each with
   a known-count suffix in .72rem --ink3 tabular (`Gear · 1,602 known`,
   `Talents · 1,676 known` — the window's fl-bit counts, re-sliced live like
   everything else). Active tab: §15.3 champagne underline treatment. Inactive tab
   MUST read interactive: hover color:var(--ink) + a neutral (--line2) underline,
   cursor:pointer. Tab switch is instant (no slide/fade). The active tab is module
   state, NOT URL state (§3.1 stands): it survives every re-slice and lens move, and
   persists across exit/re-enter within the session so a deliberate return lands on
   the pane the owner left. Two destinations, both one click, counts visible — no
   what-am-I-missing anxiety.
4. **Gear pane** (default): gear overview grid with in-place per-slot fold-out
   (§3.4 geargrid + slotfold), then enchants (§3.4 enchants), then crafted &
   embellishments — collapsed by default (§3.4 crafted).
5. **Talents pane**: talent builds + hero logic (§3.4 talents).
In-pane sections use the §15.9 `.sec` header treatment (static −/+, champagne tick,
collapsible) so the screen reads as native dashboard, not a foreign pane. All content
sits directly in page flow — the page scrollbar is the only scrollbar. At 1366×768
the Gear pane's core loop — grid + an opened fold-out — sits entirely above the fold;
that above-the-fold property is the layout's acceptance bar, do not regress it.

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
signal: the fetch result. Success: `renderScreen()` fills the build digest line, the
sub-nav, and the active pane.

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

**geargrid — "Gear overview"** (the owner's #gear-overview ask; Gear pane). CSS grid
`repeat(auto-fill,minmax(132px,1fr))` (≈8 per row on the 1116 px content width, 4-up
≤900px). Tile (panel recipe on --surface1, --r1, §GG stacked shadow + top-edge lip):
slot label (.62rem caps --ink3: HEAD, NECK, …, MAIN HAND), most-common item name
(.78rem --ink, 2-line clamp, `"n":null` ⇒ `#id` wowhead link), share (tabular .8rem
650 --ink) + `n` (.68rem --ink3), med ilvl (.68rem --ink3, "ilvl 723 · all records"
honesty — vocab annotation, not lens-sliced). CRAFTED tag (.6rem caps, --line2 border,
--r1 — the §15.13 tag treatment, never accent) and `· <emb>` suffix when the winning
entry carries them. Winner = argmax count of vocab values over gear-known slice rows;
value 0 competes as "other/none". **Weapon-tile exceptions (review fixes):** the
MAIN HAND winner name is never ellipsized — it wraps up to 3 lines and the tile (and
its grid row) grows; and when the OFF HAND winner is "other/none" (2H specs), the Off
Hand tile does NOT render — the Main Hand tile carries a footnote line (.62rem --ink3)
`off hand: none — two-handed 99%`, and Off Hand is skipped in the fold-out arrow walk.
When a real off-hand item wins, the Off Hand tile renders as an ordinary tile. The
grid is therefore 15 or 16 tiles, never a near-empty 17th piece of furniture.
The whole tile is a content-hugging trigger; the geargrid .sec's scope line carries
the microcopy `click a slot to unfold its distribution in place` (never "below" —
copy must point AT the click target). Active tile: border-color --accent-line —
color-only, nothing moves. Tile row order is fixed regardless of fold-out insertion —
muscle memory holds.

**slotfold — the in-place per-slot fold-out** (replaces the former standalone
per-slot section AND its 16-chip selector — neither may return). Clicking a tile
inserts ONE full-grid-width panel directly after that tile's grid row (grid-column
1/-1 so later rows shift down without reflowing columns; row order stable): panel
recipe on --surface1, border 1px --accent-line, plus a small top-edge caret/notch
aligned under the owning tile (pure CSS triangle in the same border color — the
parent-child link must be legible to a first-time viewer). Content: slot label
header + the per-slot distribution table. Rows (≤10 + "other/none"): item name |
flat share bar (hairline track rgba(234,227,208,.07), fill rgba(234,227,208,.28) —
neutral: hue belongs to data, champagne stays ACTIVE-only) | share % · n · med ilvl ·
CRAFTED/emb tags. Entries with n<3 in the window fold into "other" (SPECMETA_ENTRY_MIN
echo). Exactly ONE fold-out open at a time: clicking another tile swaps the content
(and moves the panel) in place, clicking the open tile closes it — all instant, no
height animation. **Keyboard:** while a fold-out is open, Esc closes IT (the screen's
Esc-swallow in §3.1 still never exits the mode — the handler closes the fold-out when
one is open, otherwise does nothing); ArrowLeft/Right move the fold-out to the
adjacent slot in tile order (skipping a suppressed Off Hand) — the one convenience
the chip row had, without the furniture. ArrowUp/Down remain the spec ladder.
**State:** fold-outs default CLOSED on every screen entry (deliberate-exit calm — no
restored open panel), but an open fold-out and its slot SURVIVE every lens move and
filter re-slice while the screen is open, so A/B-ing the lens against one slot's
distribution works without re-aiming. Reset on spec switch. **1366:** when the
fold-out opens in (or moves to) the last fully-visible grid row, auto-scroll the page
just enough to reveal the panel's full height (scrollIntoView block:'nearest',
instant) — a fold-out never renders partly off-screen.

**enchants** (Gear pane, under the grid). Table: one row per eslot with any data:
slot label · top enchant name · share of enchant-known · n · a trailing FIXED-WIDTH
expander column holding the static −/+ marker with a hover state (marker --ink3 →
--ink2, row border --line2 — expandability must be discoverable, not decorative);
click row → the row expands its alternatives list in place (static marker swap,
instant); expanded rows are REMEMBERED per slot while the screen is open (surviving
re-slices), reset on entry/spec switch, and the expanded state is obvious (marker −,
alternatives indented under the row). Row height ~15% tighter than the standard
§15.15 table (this is the last remaining furniture block — keep it dense but
legible). Share denominator = slice rows with `fl&1` (an empty nibble on a gear-known
row IS "unenchanted" — a real zero; show it as the "none" line when it wins). Absent
`cols.en` (ladder step) ⇒ section absent.

**crafted — "Crafted & embellishments"** (Gear pane, last; secondary daily reading —
CRAFTED badges already surface on grid tiles). Its .sec renders COLLAPSED by default,
with the collapsed state made explicit: static `+` marker AND a scope-line summary
`3 slots · 4 embellishments` (live counts) so the header is legible without opening —
no invisible collapse. Open ⇒ two mini-tables side by side (stack ≤900px), equalized
heights and one shared gutter so the row reads as one band at 1366. (1) Crafted worn:
rows = slots where a `cr` entry appears in the slice: slot · top crafted item · share
of gear-known · n. (2) Embellishments: aggregate vocab entries by `emb` across ALL
slots: name · players · share — denominator = gear-known rows in window; caption
states "counted per slot; a player can carry two". Open/closed state persists while
the screen is open; zero crafted entries in the slice ⇒ the whole .sec does not
render (never an empty shell).

**talents — "Talent builds"** (+ hero, per the owner's logic; the Talents pane's
whole content — one click from anywhere via the sub-nav or the identity-band digest,
which mirrors this section's top row and hero line exactly). Top rows (≤8 + "other"):
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
  the Gear pane's sections are absent — the pane shows only its n-line + the thin-n
  message, its tab count reads 0; talents may still render (strings arrive
  independently), and vice versa (then the Talents pane and the digest go absent
  instead). Both empty ⇒ the sub-nav does not render and the screen shows ladder +
  identity band + "no builds data for this spec yet" — honest, not dormant (data
  exists file-wide, just not here).
- **Framed spec filtered out** (frameLiveIdx empty / key gone from CHART_KEYS): identity
  band shows the existing "no parses match the current filters" line; digest, sub-nav
  and panes render nothing; ladder chips (built from the live ranking) are the way out.
  Never auto-exit.
- **Archon replica**: nothing special-cased (§3.3b). Scope line + lens semantics identical.
- **Merged + sidebar hero filter**: hero mask only applies unmerged (rowPass L1830) — no
  interaction; do not add one.
- **1366×768**: `main` is 1200 − 84 padding = 1116 px content — geargrid lands 8 columns;
  grid + an opened fold-out sit above the fold (§3.2 acceptance bar); the last-row
  fold-out auto-reveal (§3.4 slotfold) prevents any partly off-screen panel; a wrapped
  3-line Main Hand name must not cause orphan-tile row jitter (the row grows evenly);
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
   keydown handler swallows Esc (acting only to close an open fold-out, never to
   exit — §3.1/§3.4) and the click-away pointerdown listener
   returns early (the rail keeps its light Esc/click-away untouched — peek vs place,
   both behaviors verified side by side); scroll save/restore; session removal of the
   affordances on sidecar failure (§3.3). Screen never serialized to the URL.
5. `renderScreen()` + dispatcher: `renderFrame()` routes to it while `state.screen` (the
   L2865 master-refresh path then re-slices the whole screen — digest, tab counts,
   active pane, open fold-out — on every control change, lens included). ArrowUp/Down +
   ladder chips both step `state.frameKey` through CHART_KEYS and re-render in place.
6. Identity band per §3.2.2 including the build digest line: mirror of the Talents
   top row (top share · median DPS · merged-only hero split), copy .btn (clipboard
   guard + 1.2 s "copied" swap), text = content-hugging trigger switching to the
   Talents pane; absent when builds are unknown/thin in the window.
7. Sub-nav per §3.2.3: §15.3 .tabs, `Gear`/`Talents` with live known-count suffixes
   from the fl bits; inactive-tab hover (ink color + neutral underline, pointer);
   active tab = module var — survives every re-slice AND exit/re-enter within the
   session; never serialized to the URL (§3.1 stands). Default pane: Gear.
8. Gear pane per §3.4: geargrid (Off Hand tile suppressed when "none" wins — footnote
   on Main Hand; Main Hand name wraps ≤3 lines, never ellipsized; microcopy "click a
   slot to unfold its distribution in place"; stable tile order) + slotfold (single
   full-grid-width panel inserted after the owning tile's row, --accent-line border +
   aligned caret, one open at a time, swap/close instant, Esc closes the fold-out
   only — the §3.1 Esc-swallow still never exits the screen — ArrowLeft/Right walk
   adjacent slots skipping a suppressed Off Hand, last-visible-row auto-reveal via
   scrollIntoView block:'nearest') + enchants (fixed-width −/+ expander column with
   hover state, ~15% tighter rows, per-slot expanded memory) + crafted (collapsed by
   default with live `N slots · M embellishments` scope summary, equalized card pair).
9. Talents pane per §3.4 talents — unchanged hero logic, copy buttons, foot lines.
10. Screen sections as SCREEN_BLOCKS entries `{id, has, html, wire}` — same registry
   pattern as FRAME_BLOCKS (which stays rail-only and untouched except the two entry
   affordances). Fold-out slot, enchant-row expansions, crafted open/closed = module
   vars: fold-outs default closed on every entry, all of it survives re-slices while
   open, resets on spec switch; active tab alone persists across exit/re-enter (§3.2).
   Never rebuild any second slot-navigation surface (chip row) in any iteration.
11. CSS: ladder strip, identity band + digest, sub-nav, geargrid tiles, fold-out
   panel + caret, neutral share bars, tags, .sec reuse — radii 6/4, §GG materials, no
   rotation, no new tooltips (`title=""` nowhere new), hover = color only, nothing
   full-bleed, no inner scroll containers (only .tblwrap's standing overflow-x).
12. Trigger discipline: tiles, tabs, rows, digest text, spec-name are content-hugging
   targets; empty cells inert; the suppressed Off Hand footnote is inert text.
   Click-away-close applies to the RAIL only — the screen never dismisses on outside
   clicks (it is the page).
13. Verify at 1366×768: Gear pane grid + an opened fold-out fully above the fold; no
   partly off-screen fold-out from any row; no orphan-tile jitter when the panel
   inserts or the Main Hand name wraps.

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
ladder strip of rank-ordered spec chips; identity band with the scope line, lens
sub-line, and the digest — "top build 61% · median 2.31M · Templar 88% / Herald of the
Sun 12% · copy" — the daily build copied without a single navigation; then the Gear |
Talents sub-nav with live known-counts, Gear open. The gear grid reads at a glance —
"Luminant Verdict's Unwavering Gaze · 98% · ilvl 723 · n=214"; the wrist tile carries
CRAFTED · its embellishment; the Main Hand tile shows the full greatblade name over
three lines with "off hand: none — two-handed 99%" as its footnote. Clicking the wrist
tile unfolds its distribution right under its row — caret pointing at the tile — and
grid + fold-out sit whole above the fold at 1366; ArrowRight walks the fold-out to
Hands, Esc folds it away without leaving the screen. Below, the tightened enchant
table shows the helm rune at 84% with a discoverable −/+ column; Crafted &
Embellishments waits collapsed under "3 slots · 4 embellishments". The Talents tab —
one click, "1,676 known" on its face — lists two builds at 61%/22% with median DPS and
copy buttons and the hero line. The sidebar and lens bar never left: they drag keys to
+14–+16 and digest, counts, grid, and the still-open wrist fold-out all re-slice; they
push the lens to p95 and the build order flips — the story Archon's fixed page cannot
tell. ArrowDown hops to the next spec without leaving the mode; a stray Esc or click
never exits — the screen is a place, and only "← Rankings" (or the wordmark) leaves
it, returning to the rankings at the exact scroll they left; re-entering lands on the
tab they left. Every section states its n. Nothing rotated, nothing purple, nothing
full-bleed, no chip row reborn, and nothing leaves by accident.

## §C.1 — Compact-viewport addendum (BINDING; owner, 2026-08-29; overrides anything above that conflicts)
The owner: "my screen is not that tall - I don't want to have to scroll a lot.
Organize my data in a way that scrolling down is not required often and scrolling
right is almost never required." Rules:
1. ACCEPTANCE BAR (replaces the earlier 1366 bar): at 1366×768, EACH sub-nav pane's
   at-rest content — identity band + ladder + sub-nav + the pane — fits the viewport
   WITHOUT vertical scrolling. Fold-outs and expansions are the only things allowed
   to extend below the fold (with the auto-reveal already specced). Verify this
   explicitly in the build's playwright pass (document.body scroll height at rest
   <= viewport height per pane, all specs sampled: popular, rare, filtered-thin).
2. Compress the fixed chrome: identity band at most two dense lines (digest stays);
   ladder strip and sub-nav share a row if needed; no decorative vertical padding
   beyond the design language's minimum scale.
3. If the Gear pane cannot meet the bar with grid + enchants + crafted at rest,
   REDISTRIBUTE rather than scroll: move Enchants + Crafted & Embellishments to a
   third tab ("Gear | Enchants & Crafted | Talents") or collapse them to one-line
   summaries that expand in place — builder's choice, judged by the bar.
4. Horizontal scrolling: NEVER at >=1200px viewports — wrap or stack instead; inside
   any narrower table the existing overflow-x-in-container rule applies but the
   character screen must not produce such tables at all at 1366.
5. This preference is STANDING for all future site work (recorded in user_prefs).
