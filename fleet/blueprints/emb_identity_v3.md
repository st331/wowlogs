# BLUEPRINT — Embellishment identity v3 · build-ready · 2026-08-30

Fixes "embellishments are broken, none show up for any spec". Live payload:
17,126 vocab entries, 1,397 `cr`, **840 `emb`, exactly ONE distinct label
("embellished")** — detection healthy, **naming 100% lost**.

Two dead ends, both shipped, both silent (§6 records them): **v1** named by
walking `bonus → ItemBonusTreeNode → MCRI → Item → ItemSparse` per bonus id —
that chain closes for EVERY optional reagent, so missives and sparks got named.
**v2** guarded it on the reagent's Embellished `ItemSparse.LimitCategory` —
**measured today, that field is `0` on every crafting reagent in the game**
(Duskthread Lining 222871/2/3) and non-zero on exactly SIX non-reagent worn
items, so it rejects 100% *by construction*. v3 stops asking per bonus id: it
precomputes ONE global set from two whole-table fetches and tests membership.
All numbers re-derived live 2026-08-30 (`ItemBonusTreeNode` 653,284 B / 19,095
rows / 4,410 trees; `ModifiedCraftingReagentItem` 35,873 B / 623 rows).

## 1. THE IDENTITY RULE

### 1.1 Derivation — `scripts/fetch_names.py`, replaces `resolve_emb_name()`

```python
MARKERS = set(load(EMB_MARKERS))       # {8960, 13555} — UNCHANGED, verified
nodes = _get_csv("ItemBonusTreeNode"); mcri = _get_csv("ModifiedCraftingReagentItem")
if nodes is _FAILED or mcri is _FAILED: return _FAILED   # write NOTHING, print [emb] FAIL
by_parent  : {ParentItemBonusTreeID -> [rows]}
emitted_by : {ChildItemBonusListID  -> {parent trees emitting it}}
tree2mcri  : {ItemBonusTreeID -> MCRI.ID}         # first row per tree, sorted

def lists(t, seen=frozenset()):        # cycle-guarded FULL recursion
    if t in seen: return set()
    out = set()
    for n in by_parent.get(t, []):
        if n.ChildItemBonusListID: out.add(int(n.ChildItemBonusListID))
        if n.ChildItemBonusTreeID: out |= lists(int(n.ChildItemBonusTreeID), seen|{t})
    return out

MARKER_TREES = {t for t in by_parent if lists(t) & MARKERS}     # 54
BACKED       = {t for t in MARKER_TREES if t in tree2mcri}      # 49
IDENTITY     = {}                     # bonus id -> owning BACKED tree
dropped_leak = 0
for t in sorted(BACKED):                          # sorted ⇒ deterministic
    for b in lists(t) - MARKERS:
        if emitted_by.get(b, set()) - MARKER_TREES:   # LEAK GUARD
            dropped_leak += 1; continue               # measured 0/49 today
        IDENTITY.setdefault(b, t)                 # first BACKED tree wins
```

**Measured today:** 53 trees emit a marker as a DIRECT child (child-count
distribution exactly `{2}`); one more (3902) reaches one through a subtree = 54;
**49 have an MCRI row**; result **49 identity bonus ids, zero leakage**.
Direct-children-only and backed+recursion yield the IDENTICAL 49 ids, so the two
guards are each other's insurance and both ship.
**MCRI-backing is the semantic filter** — we only name what is literally a
craftable reagent's tree; it excludes `8809`, whose marker is inherited two
levels up an ancestor chain via unbacked tree 3902, the exact shape of reasoning
that produced v1. **The leak guard is the tripwire** — it drops and COUNTS any
id also appearing under a non-marker tree (0 today), printed rather than
silently widening the set. **Recursion must be full and cycle-guarded**, not one
level (nested chains exist: `5287 → 5102 → 5974`); unbacked trees **3890, 3902,
4447, 4766, 5287** contribute nothing, by design.

**The hard case is not a case.** An item carrying an embellishment + a missive +
a spark intersects to exactly one id: Draconic Missive `8791`, Spark of Tides
`13751`, Spark of Radiance `12066` are **absent from the 49-set** (verified).

### 1.2 Naming — positive only, `Item` hop deleted

```python
for b in sorted(IDENTITY):
    if NAME(b): continue                              # a name is never re-asked
    rows = _get_csv("ItemSparse", {"filter[ModifiedCraftingReagentItemID]":
                                   f"exact:{tree2mcri[IDENTITY[b]]}"})
    if rows is _FAILED: continue                      # write nothing → retry
    disp = {r.Display_lang for r in rows if r.Display_lang}
    if len(disp) == 1: names[b] = disp.pop()          # REFUSE on disagreement
```
`ItemSparse` carries `ModifiedCraftingReagentItemID` itself — the `Item` hop is
deleted. Quality tiers return several rows that agree (MCRI 484 → 3 rows, all
"Duskthread Lining"); take the name only when they agree on one string.
**`ItemSparse.LimitCategory` is never read for a reagent again.** Today **48 of
49 named**; `13587` (tree 5862, MCRI 676) has ZERO ItemSparse rows — a printed
gap, hand-nameable via overrides.

### 1.3 Intrinsic items · 1.4 cost and ordering

`ItemSparse?filter[LimitCategory]=exact:512` (and `697`, zero rows) → 6 items
222458, 222459, 222463, 244463, 244472, 251073 into `data/emb_items.json`; one
request, positive only, closing the gap `builds_tab.md §2` documents.
Steady state **2 requests/run** (first run 2 + ≤1: the 48-name map ships
committed). **Delete the journal-driven `candidates` set at
`fetch_names.py:397-401`.** The emb pass is journal-independent: run it **FIRST
in `main()`, exempt from `spend()`** — today it runs last and dies on `if not
spend(): break` once `--limit 2000` is consumed, a second live failure that
would survive a naming-only fix.

### 1.5 `emb_of()` — `build_site_data.py:1455`, every branch

```python
def emb_of(item):
    bonus = item.get("bonus")
    if not isinstance(bonus, list):                       return None
    if not (any(b in emb_markers for b in bonus)
            or item.get("id") in intrinsic):              return None
    hits = [b for b in bonus if b in emb_ids]         # 49-element global set
    if len(hits) > 1:  EMB["conflict"] += 1;              return -1
    if len(hits) == 1 and NAME(hits[0]):  EMB["named"] += 1; return hits[0]
    EMB["known_unnamed" if hits else "unidentified"] += 1; return -1
```
NAME() = overrides first, then `emb_identity.json["names"]`.

| case | returns | vocab key `(id, emb, ilvl)` | shipped `emb` |
|---|---|---|---|
| not gear / no bonus list, or no marker and not intrinsic | `None` | no split — **un-embellished crafted items never split** | absent |
| exactly one identity id, named | that bonus id | splits per embellishment — the intended new split | the name |
| identity id present but unnamed (13587), or marker with no identity id | `-1` | one generic bucket | `"embellished"` |
| **≥2 identity ids** | `-1` **+ CONFLICT counter** | generic bucket | `"embellished"` |

**Never guess.** Two identity ids on one item is impossible under
`ItemLimitCategory 512 Quantity=2` — evidence the model broke, not a tie to
break; v2's `sorted(named)[0]` is deleted, a smallest-id pick is a miniature v1.
`build_site_data.py:1612` keeps its shape (`emb_names.get(emb) or "embellished"`;
`get(-1)` is `None`). **Never emit a `#<digits>` label** — `index.html:3361`
silently swallows it. **Splitting is the intended, visible cost:** 98/636 doll
winners carry `emb`, so a two-way split flips ~31 tiles, and 566/640 vocab
columns are saturated at the 24/40 cap so each new identity evicts one tail entry
(median tail mass 0.10%) — correct consequence of correct identity, PREDICTED §3.

## 2. CACHE SHAPE AND MIGRATION

| file | shape | written by | rule |
|---|---|---|---|
| `data/emb_markers.json` | sorted int list `[8960, 13555]` | fetch_names | **unchanged, correct** |
| `data/emb_identity.json` | `{"ids":[...49...], "names":{"<bonusid>":"<name>"}}` | fetch_names | `names` grow-only **POSITIVE ONLY — never a null**; `ids` rewritten on a successful run, never on `_FAILED` |
| `data/emb_items.json` | `{"<itemid>":"<name>"}` | fetch_names | positive only |
| `data/emb_overrides.json` | `{"names":{"<bonusid>":"..."},"ids":[...]}` | **HUMAN ONLY, never machine-written** | highest precedence over both |
| `data/names_bonus_emb2.json` | v2 | — | **RETIRED. Read once by the migration, then never again.** |

**Why a new file.** `unseen()` (`:110-112`) returns only ids ABSENT from a
cache and `merge_grow_only()` (`:97-103`) never overwrites, so a cached `null`
is never re-asked; v2 wrote one null per candidate — every identity id plus
every missive, spark, quality and ilvl bonus. **The committed `{}` is not
evidence**: `refresh.yml:189/193` mirror `data/processed/<f>.json` in and out and
commit once a day, and committed `names_items.json` is `{}` while the live site
carries 17,126 names. Assume the CI copy is a block of nulls — a corrected
resolver against it resolves ZERO names.

**Migration, once, in `fetch_names.py` before the identity pass:** read
`names_bonus_emb2.json` if present, copy its **truthy** values into
`emb_identity.json["names"]` grow-only (hand-typed names survive), **drop every
null**, print `[emb] migrated N names from names_bonus_emb2 (dropped M nulls)`;
leave the old file in place — M is the only hard evidence of how large the sticky
block was. **The bug class is deleted, not flushed:** the candidate universe is
db2-bounded (49 ids), so an unresolved id stays absent and is retried at ≤1
request — a negative can no longer be stored.

**`.github/workflows/refresh.yml` — three edits or the caches evaporate.** L189
(pre-run copy loop) becomes `names_items names_enchants crafted_ids
names_bonus_emb2 emb_identity emb_items emb_overrides names_icons` —
`names_bonus_emb2` stays for ONE release so the migration can read the CI copy,
then goes. L193 (copy-back): same list minus `names_bonus_emb2`. L307
(`git add`): add the three new files, drop `data/names_bonus_emb2.json`. The
`actions/cache` PATH LIST (`:118-130`) names DIRECTORIES and **must not change**
— new files inside `data/processed` ride along, nothing is orphaned.

**Seed `emb_identity.json` in the same commit** from the verified 48-name map
(`…/scratchpad/emb_identity_seed.json`; independently re-derived — set difference
`{13587}` only), so the first post-merge build is correct with ZERO network
calls, a wago outage on merge day cannot delay the fix, and a human can read 48
names in the diff and spot a missive.

## 3. PROOF OBLIGATION — `site/build_health.txt`

Both prior versions failed silently because this file says nothing about
embellishments. Append via `health()` (`:44`); **`verdict` is the FIRST line, a
greppable token.**

```
[emb] verdict: ok
[emb] db2 map: 54 marker trees (8960:50, 13555:3, nested:1), 49 reagent-backed -> 49
      identity ids, 48 named, 1 unnamed [13587] | fetched 0, failures 0 | wago OK
[emb] invariants: direct marker trees with !=2 children 0/53 | max recursion depth 2 |
      identity∩markers empty | leak-guard dropped 0 | 5 unbacked trees skipped | intrinsic 6
[emb] migrated 0 names from names_bonus_emb2 (dropped 641 nulls)
[emb] journal: 1,397 crafted entries, 840 marked -> named 812 (96.7%), known-unnamed 6,
      unidentified 22, CONFLICT 0 | labels 31 distinct | top Duskthread Lining 214, ...
[emb] AUDIT co-occurrence: identity ids min withMarker/seen 1.000 (41 ids, support>=12,
      >=3 items) | non-identity max 0.71 (96 ids) -> CLEAN
[emb] vocab: 425 saturated columns hold an emb entry; split evicted 37 tail entries
      (median 0.10%); doll winners carrying emb 98/636, changed vs last build 31
```

**`verdict: ok`** iff `named > 0` AND distinct labels > 1 while marked > 0 AND
`CONFLICT == 0` AND every invariant holds AND db2 answered; else `DEGRADED` (with
the reason), or `DEAD -- 840 marked entries, 0 named` for the exact signature of
today's bug. After the build step in `refresh.yml`: `grep -q '^\[emb\] verdict:
ok' site/build_health.txt || echo "::warning::embellishment identity degraded"` —
**a warning annotation, never a build failure**; a data problem must never stop
the site publishing.

**Which line catches which failure:** v2 (naming dead) → `journal:` reads `0
named (0.0%)`, `verdict: DEAD`. v1 (false names) → the AUDIT line, computed in
`scan_journal`'s existing single pass, no extra I/O: per **distinct crafted bonus
configuration** (not per carry) count `seen[b]` and `withmk[b]`.
An identity bonus is a sibling of the marker in one tree, so its ratio is
**exactly 1.000**; a missive or spark spreads over a partly-embellished recipe
family and lands near the 840/1,397 = 0.60 base rate. WARN BOTH ways — an
identity id `< 1.000` (model broke), or a non-identity id at `1.000` with
support ≥12 configurations across ≥3 item ids (new embellishment, or v1's
mistake from the other side). **Validator, never a classifier**: a missive
exclusive to an always-embellished family also scores 1.000, which is why the
decision stays structural. Budget starvation → `fetched 0` beside a non-zero
`named`; a missed CI edit shows `48 fetched, 0 cached` daily. Drift → invariants.

Unnamed/ambiguous ids print with the literal one-line JSON to paste into
`data/emb_overrides.json`; `fetch_names.py`'s summary (`:443-451`) drops `emb
bonuses N (M)` for `emb identity: 49 ids, 48 named (0 fetched, 0 failures)`.

## 4. UI — `site/index.html`, `csCraftedModel` (:3970) / `csCraftedHTML` (:3999)

`embOf` (:3361), the doll and pooled tile `title`s (:3870, :3887) and the
fold-out `.gemb` sub-line (:3754-3756) inherit names for free — `"Wrist —
embellished"` becomes `"Wrist — Duskthread Lining"`. **Delete the vestigial
`/^#\d+$/` guard in `embOf`**: dead code, and a trap that would swallow a future
placeholder and render the section *emptier*.

**4.1 The 141.1% is a real defect — fix the arithmetic, not the caption.** The
numerator sums per-slot ITEM INSTANCES over 16 slots; the denominator is
`d.gearIdx.length`, all gear-known rows *including the 13.6% wearing none* (Ret
season-wide 14,901/11,687 = 127.5%; the windowed 302/214 = 141.1% is the same
shape). It is a rate wearing a share's clothes, and double-counts Ring1/Ring2 and
Trinket1/Trinket2 against `csPoolModel`'s set semantics (:3701-3745). Invert the
loop to **per-player set semantics** — rows outer, slots inner, one `Set` of
labels per row, each label incremented once, plus `any`/`two` tallies, same
O(rows×16) cost. **No row can then exceed 100%**, the column may still sum above
it, rings/trinkets stop double-counting.

**4.2 What renders.**
* Header: `10 slots · 86% of players embellished` (windowed gear-known rows
  with ≥1 embellished entry; Ret 86.4%). `M.emb.length` — the LABEL count, which
  today prints the absurd "1 embellishment" — is gone.
* **Prevalence line**, one calm line above the table: `carried by 86.4% of
  11,687 gear-known — two 41.1% · one 45.2% · none 13.6%` (measured Ret
  1,595/5,283/4,809, never 3+). **Naming-independent, so it stays correct on the
  day naming breaks** — and against a one-row table it is visibly incoherent,
  the alarm this morning lacked.
* Table with real `<th … data-c>` sortable headers `EMBELLISHMENT | PLAYERS |
  %` (pref #12; matches :2849, :4693, :5530), default share desc; `CS_ENTRY_MIN
  = 3` (:3420) as every other fold surface, cap 10 named rows, rest into
  `other named (N)`.
* **Unnamed remainder row, rendered whenever nonzero, pinned last**,
  `var(--ink3)`: `unidentified · n · %`, `title` = "embellished items whose
  embellishment we could not name: no identity bonus, two candidates, or a
  reagent with no published name". **Above 50% of embellished players the header
  appends ` · naming degraded` and the remainder renders FIRST** — today's bug
  would read "unidentified 100% · naming degraded", not one tidy plausible row.
* Caption, matching the arithmetic word for word: `share of the 11,687
  gear-known players in this window wearing this embellishment on at least one
  item; a player wears up to two different ones, so the column sums above 100% —
  no single row can. Covers items inside the shipped per-slot vocabulary (93% of
  worn slots).` 566/640 vocab columns are saturated and ~7% of Ret slot
  observations fall outside them, so every figure here is a FLOOR.

No layout/color/motion/rotation change (prefs #3, #6, #7, #10, #11, #13); `csPoolModel`'s key `e.id+"|"+e.emb` (:3715) inherits the split, no edit.

## 5. TESTS — `scripts/test_builds_sidecar.py`

The suite passed through BOTH bugs because its fake db2 asserts a fiction:
`:417` gives the reagent `"LimitCategory": "512"` while **live db2 says `0` on
every reagent**. Fix the fixture FIRST — every fake reagent gets `LimitCategory:
"0"` — so the v2 code fails the corrected suite.

1. **v1 regression — a missive must NOT be named.** Item carries `[marker,
   IDENT, MISSIVE]`, `MISSIVE` the sole child of a marker-less tree: assert
   `MISSIVE` absent from the identity set, `emb_of` → `IDENT`, one vocab entry
   named — the missive never splits identity.
2. **v2 regression — a real embellishment MUST be named.** Every fake reagent
   has `LimitCategory: "0"`: assert the run names `IDENT`, that no reagent row's
   `LimitCategory` is read, and that `Item` is never queried (deleted hop).
3. **Nested tree** `T → T2 → T3 → IDENT` under a BACKED root — found only with
   full recursion; a one-level implementation fails this test. **4. Unbacked
   marker tree** (no MCRI row) — children never enter the identity set, nothing
   raises, the item degrades to `-1`/"embellished". **5. Leak guard** — an id
   emitted by both a marker and a non-marker tree is dropped and counted.
   **6. Conflict** — two identity ids on one item → `-1`, `CONFLICT == 1`, and
   NOT the smallest. **7. Name disagreement** — two ItemSparse rows with
   different `Display_lang` → no name stored (refuse, not pick).
8. **Sticky-null recovery** — pre-populate `names_bonus_emb2.json` with nulls
   including the identity id, run end-to-end, assert the id is still named,
   `emb_identity.json` holds NO null values, and the migration line reports the
   dropped count. This is what makes the fix un-no-op-able by its predecessor.
   **9. Health** — `[emb] verdict: ok` for a healthy fixture, `DEAD` for one
   with markers and an empty name map.

## 6. BLUEPRINT UPDATE — `fleet/blueprints/builds_tab.md` §2

Replace the v2 clause (the `names_bonus_emb2` / reagent-LimitCategory paragraph
and the "known gap" sentence) with:

> **Embellishments (v3, 2026-08-30).** The Embellished `ItemLimitCategory` (512
> Quantity 2; 697 "Outdoor Embellished" Quantity 1) is applied **by BONUS** —
> `ItemBonus` Type=35, `Value_0 ∈ {512,697}`, `ParentItemBonusListID ∈ {8960,
> 13555}` = `data/emb_markers.json`. An item is embellished iff its `bonus` list
> hits a MARKER (or it is one of the 6 intrinsic items in `data/emb_items.json`).
> **IDENTITY** = non-marker bonus lists emitted (full cycle-guarded recursion) by
> an `ItemBonusTreeNode` tree that emits a marker **and has a
> `ModifiedCraftingReagentItem` row**, minus any id also appearing under a
> non-marker tree (leak guard). Measured: 54 marker trees, 49 reagent-backed,
> **49 identity ids, 48 named**. Contrast: tree 4771 → `{8960, 11304}` =
> Duskthread Lining vs missive tree 3741 → `{8791}`, no marker. Name via tree →
> MCRI.ID → `ItemSparse?filter[ModifiedCraftingReagentItemID]=exact:<id>` →
> `Display_lang`, only when the quality-tier rows agree.
> `emb_of` returns the identity bonus id when exactly one is present and named,
> else `-1` (generic bucket, rendered "embellished"); **≥2 identity ids is a
> CONFLICT → `-1`, never a guess.** `emb` participates in the vocab identity
> key, so one base item with two embellishments is two entries.
> **DEAD END 1 (v1):** naming by walking the reagent chain per bonus id — the
> chain closes for EVERY optional reagent, so missives and sparks got named.
> **DEAD END 2 (v2):** validating the reagent's `ItemSparse.LimitCategory`.
> **That field is `0` on every crafting reagent in the game** and non-zero on
> exactly 6 intrinsically-embellished WORN items (222458, 222459, 222463,
> 244463, 244472, 251073). **It is never a reagent test**; rejection was 100% by
> construction. Corroboration only, never a classifier: the identity bonus
> grants the embellishment's spell (`ItemBonus` Type=23 → ItemEffectID; 13771 →
> ItemEffect 234561 → Spell 1297382 = MCRI 696's `$@spelldesc1297382`), but
> Type=23 spans 1,359 parent lists of which only 49 are embellishments.
> **Caches:** `data/emb_identity.json` (`ids` + `names`, **positive only — a
> null is never stored**; absence means retry), `data/emb_items.json`,
> `data/emb_overrides.json` (**human only, highest precedence**).
> `data/names_bonus_emb2.json` is RETIRED (its nulls are unfalsifiable and
> permanent); one migration copies its truthy values and drops the nulls.
> **Doctrine:** derive what you can derive and recompute it every run; cache
> only what you cannot derive; never store a null as the answer to a
> recomputable question; never let an external source decide an identity it does
> not encode — let it supply only the word.
> (§2's "known gap" clause is partly stale: 244472 is already in the live vocab
> flagged `emb`.)

## 7. FILES TOUCHED

`scripts/fetch_names.py` · `scripts/build_site_data.py` · `site/index.html` (+
`docs/` mirror via the existing sync) · `scripts/test_builds_sidecar.py` ·
`.github/workflows/refresh.yml` (L189, L193, L307 + the verdict grep) · **new,
seeded:** `data/emb_identity.json`, `data/emb_items.json`,
`data/emb_overrides.json` · `fleet/blueprints/builds_tab.md` §2. `emb_markers.json` untouched.

**After the first production build:** `curl https://st331.github.io/wowlogs/build_health.txt` and read the LADDER line FIRST — live is `caps 24/40 builds 40 en=y at 4.24 MB (target 4.3, hard cap 5.0)`, ~60 KB of headroom, and **rung 2 drops the enchant columns and ships `eslots: []`, silently killing the entire Enchants pane** (`build_site_data.py:1727-1730`); measured cost of this change ≈ +5.5 KB gzipped. Then read `[emb] verdict:`.
