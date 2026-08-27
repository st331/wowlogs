"""Per-spec stat aggregation against synthetic gear-journal records.

The journal itself lives only in the Actions cache, so every record here is
synthesised through the REAL writer -- parse_summary in fetch_data.py -- and
written as JSON lines exactly as the collector writes them, then read back
through the real reader and aggregation in build_site_data.py. What this
pins: the journal schema round-trip, cohort membership (window, key floor,
timed-only), one-record-per-character dedup, journal last-copy-wins,
hero-merged keying, quantile arithmetic, per-flask re-slicing, and every
absence case (empty journal, stats-less records, torn stats, no flasks)
ending in an absent block or key rather than a crash or an invented zero.
"""
import base64
import gzip
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import parse_summary
import build_site_data as bsd


class _Hero:
    def resolve(self, tree):
        return "Hero"


BASE_MS = 1_787_000_000_000          # epoch ms, like the collector's start_time
DAY_MS = 86_400_000


def make_parse(code, fid, char, server, cls, spec, *, key=14, medal="gold",
               start_ms=BASE_MS, stats=None, flask=None, region="US",
               drop_flask=False, dmg=9_000_000, build=None, trinkets=None,
               ench=None, gems=None):
    """One (player row, journal record) through the real collector path.

    stats: {"Crit": 1200, ...} rating minimums, or None for a stats-less
    combatantInfo. flask: an aura name to put in the aura list, "" for an
    aura list carrying no flask, or None for no aura list at all.
    drop_flask strips the record's flask key entirely, simulating a journal
    line written before flask capture existed. dmg sets the parse's DPS
    (totalTime is 1500s, so dps = dmg / 1500). build is a talent loadout
    string; trinkets/ench/gems populate a full positional gear array
    (trinkets in slots 12/13, the enchant on slot 15, gems on slot 0).
    """
    if trinkets or ench or gems:
        gear_list = [{"id": 5000 + s, "itemLevel": 710} for s in range(16)]
        gear_list[3] = {"id": 0}                       # empty shirt slot
        gear_list[12] = gear_list[13] = {"id": 0}      # trinkets only if set
        for j, tid in enumerate(trinkets or ()):
            gear_list[12 + j] = {"id": tid, "itemLevel": 720}
        if ench:
            gear_list[15] = dict(gear_list[15], permanentEnchant=ench)
        if gems:
            gear_list[0] = dict(gear_list[0], gems=list(gems))
    else:
        gear_list = [{"id": 1001, "itemLevel": 720}]
    ci = {"gear": gear_list,
          "talentTree": [{"id": 1, "rank": 1}], "specID": 1}
    if build:
        ci["talentImportString"] = build
    if stats is not None:
        ci["stats"] = {k: {"min": v} for k, v in stats.items()}
    if flask == "":
        ci["auras"] = [{"ability": 1, "name": "Skyfury"}]
    elif flask is not None:
        ci["auras"] = [{"ability": 1, "name": "Skyfury"},
                       {"ability": 43000, "name": flask}]
    fight = {"code": code, "fid": fid, "dungeon": "Halls", "key_level": key,
             "region": region, "score": 400.0, "medal": medal,
             "affixes": [9], "start_time": start_ms,
             "rank_duration_ms": 1_500_000}
    player = {"id": 1, "name": char, "server": server, "type": cls,
              "specs": [spec], "icon": f"{cls}-{spec}",
              "maxItemLevel": 720, "combatantInfo": ci}
    table = {"data": {"totalTime": 1_500_000,
                      "playerDetails": {"dps": [player]},
                      "damageDone": [{"id": 1, "total": dmg}],
                      "deathEvents": []}}
    rows, gear_rows = parse_summary(fight, table, _Hero())
    rec = gear_rows[0]
    if drop_flask:
        del rec["flask"]
    return rows[0], rec


def st(crit=1000, haste=2000, mastery=3000, vers=4000, **extra):
    d = {"Crit": crit, "Haste": haste, "Mastery": mastery,
         "Versatility": vers}
    d.update(extra)
    return d


def run_block(rows, recs, fn=None):
    """Rows/records -> the real reader + aggregation, via a journal on disk,
    mirroring build(): started from started_at ms, timed from the medal."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = pathlib.Path(tmp) / "gear.jsonl"
        with journal.open("w") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        bsd.GEAR_JOURNAL = journal
        bsd.GEAR_EXPORT = pathlib.Path(tmp) / "absent.jsonl.gz"
        df = pd.DataFrame(rows)
        started = pd.to_datetime(pd.to_numeric(df["started_at"]), unit="ms")
        timed = df["medal"].map(bsd.MEDAL_TIMED).fillna(-1).astype(int)
        return (fn or bsd.spec_stats_block)(df, started, timed, "test")


rows, recs = [], []


def add(*a, **kw):
    r, g = make_parse(*a, **kw)
    rows.append(r)
    recs.append(g)


# --- Rogues: 11 characters, Crit 1000..11000, so the quantiles are knowable;
# Leech on every one, so the tertiary must ride along
for i in range(1, 12):
    add(f"R{i}", 1, f"Rogue{i}", "Illidan", "Rogue", "Assassination",
        stats=st(crit=i * 1000, Leech=500))
# cohort rejections, each with a poison Crit that would shift the quantiles:
add("RX1", 1, "LowKey", "Illidan", "Rogue", "Assassination",
    key=11, stats=st(crit=999_999))                      # below the key floor
add("RX2", 1, "Depleted", "Illidan", "Rogue", "Assassination",
    medal="none", stats=st(crit=999_999))                # over the timer
add("RX3", 1, "Ancient", "Illidan", "Rogue", "Assassination",
    start_ms=BASE_MS - 20 * DAY_MS, stats=st(crit=999_999))   # outside window
torn = st(crit=999_999)
del torn["Mastery"]
add("RX4", 1, "Torn", "Illidan", "Rogue", "Assassination",
    stats=torn)                          # torn stats capture: reader skips it
add("RX5", 1, "GearOnly", "Illidan", "Rogue", "Assassination",
    stats=None)                          # gear-only record: no stats at all

# --- Mages: 10 characters at Crit 3000; M1 also has an OLDER parse at a junk
# value (per-character dedup must keep only the latest), and M2's record is
# duplicated in the journal with the good copy LAST (last copy wins, matching
# the journal's append-and-supersede contract). Leech on only half of them,
# below the 90% bar, so the tertiary must NOT appear for this spec.
_, stale_rec = make_parse("M2", 1, "Mage2", "Area52", "Mage", "Arcane",
                          stats=st(crit=999_999))
recs.append(stale_rec)          # journal-only: the refetch appends after it
for i in range(1, 11):
    extra = {"Leech": 400} if i <= 5 else {}
    add(f"M{i}", 1, f"Mage{i}", "Area52", "Mage", "Arcane",
        stats=st(crit=3000, **extra))
add("M1old", 1, "Mage1", "Area52", "Mage", "Arcane",
    start_ms=BASE_MS - 1 * DAY_MS, stats=st(crit=999_999))

# --- Priests: 24 characters for the flask split: 12 on one flask with Crit
# 1000..12000, 10 on another at a constant, 2 with auras but no flask
for i in range(1, 13):
    add(f"P{i}", 1, f"Priest{i}", "Moonguard", "Priest", "Discipline",
        stats=st(crit=i * 1000), flask="Flask of Tempered Aggression")
for i in range(13, 23):
    add(f"P{i}", 1, f"Priest{i}", "Moonguard", "Priest", "Discipline",
        stats=st(crit=5000), flask="Flask of Saturated Malice")
for i in range(23, 25):
    add(f"P{i}", 1, f"Priest{i}", "Moonguard", "Priest", "Discipline",
        stats=st(crit=5000), flask="")

# --- Druids: 9 characters, one short of the floor -> spec omitted entirely
for i in range(1, 10):
    add(f"D{i}", 1, f"Druid{i}", "Elune", "Druid", "Balance",
        stats=st())

# --- journal lines written before flask capture existed (no flask key), on
# their own spec: must aggregate into "all" with no flasks key and no crash
for i in range(1, 12):
    add(f"W{i}", 1, f"Warr{i}", "Ragnaros", "Warrior", "Arms",
        stats=st(crit=4000), drop_flask=True)

# hero-merged keying: the journal has no hero identity and the block must key
# on class+spec alone, so rows split across hero talents still land together
for rw in rows[:5]:
    rw["hero_talent"] = "OtherHero"
block = run_block(rows, recs)

assert block is not None
specs = block["specs"]
assert set(specs) == {"Rogue|Assassination", "Mage|Arcane",
                      "Priest|Discipline", "Warrior|Arms"}, set(specs)
print(f"specs     : {sorted(specs)} (Druid at 9 chars omitted, "
      f"never guessed)")

r = specs["Rogue|Assassination"]
assert r["n"] == 11, r["n"]
assert r["q"]["Crit"] == [3500, 6000, 8500], r["q"]["Crit"]
assert r["q"]["Haste"] == [2000, 2000, 2000], r["q"]["Haste"]
assert r["q"]["Leech"] == [500, 500, 500], r["q"]
print(f"quantiles : Crit {r['q']['Crit']} over n={r['n']} -- rejects "
      f"(low key, depleted, stale, torn, gear-only) all excluded")

m = specs["Mage|Arcane"]
assert m["n"] == 10, m["n"]
assert m["q"]["Crit"] == [3000, 3000, 3000], m["q"]["Crit"]
assert "Leech" not in m["q"], m["q"]         # 50% coverage < the 90% bar
print("dedup     : per-character latest parse wins; stale journal copy "
      "superseded by the later line; thin tertiary dropped")

p = specs["Priest|Discipline"]
assert p["n"] == 24, p["n"]
fl = p["flasks"]
assert set(fl) == {"Flask of Tempered Aggression",
                   "Flask of Saturated Malice"}, set(fl)
agg = fl["Flask of Tempered Aggression"]
assert agg["n"] == 12 and agg["q"]["Crit"] == [3750, 6500, 9250], agg
mal = fl["Flask of Saturated Malice"]
assert mal["n"] == 10 and mal["q"]["Crit"] == [5000, 5000, 5000], mal
assert "flask known for" in block["cohort"], block["cohort"]
print(f"flasks    : 2 variants (n=12, n=10) re-sliced; 2 no-flask chars in "
      f"'all' only; cohort states coverage")

w = specs["Warrior|Arms"]
assert w["n"] == 11 and "flasks" not in w, w
print("pre-flask : records without the field aggregate into 'all' only")

for needle in ("+12", "14 days", "ratings", "not percentages"):
    assert needle in block["cohort"], (needle, block["cohort"])
assert block["keyMin"] == 12 and block["windowDays"] == 14
print(f"cohort    : {block['cohort']!r}")

# --- llms flatten of the same block
frame = bsd.spec_stats_frame(block)
assert list(frame.columns) == ["class", "spec", "flask", "characters",
                               "stat", "p25", "p50", "p75"], frame.columns
prow = frame[(frame["spec"] == "Discipline")
             & (frame["flask"] == "Flask of Saturated Malice")
             & (frame["stat"] == "Crit")].iloc[0]
assert prow["characters"] == 10 and prow["p50"] == 5000, prow
n_disc = len(frame[frame["spec"] == "Discipline"])
assert n_disc == 12, n_disc                  # (all + 2 variants) x 4 stats
print(f"llms csv  : {len(frame)} long-format rows, columns pinned")

# --- absence cases: the block must vanish, exactly like hasTier/hasRating
assert run_block(rows, []) is None           # journal empty
gearless = [dict(rc, talents=None) for rc in recs]
assert run_block(rows, gearless) is None     # journal has no stats anywhere
# every run below the key floor -> empty cohort. (An all-stale dataset cannot
# empty it: the window rides the newest run on purpose, so rebuilding old
# data keeps the block rather than silently dropping it.)
low_rows = [dict(rw, key_level=11) for rw in rows]
assert run_block(low_rows, recs) is None
print("absence   : empty journal / stats-less journal / sub-floor cohort -> "
      "block None (payload key absent)")

# hasFlask: with no flask anywhere, no spec carries a flasks key and the
# cohort line does not mention coverage
noflask_rows, noflask_recs = [], []
for i in range(1, 13):
    rw, rc = make_parse(f"N{i}", 1, f"Monk{i}", "Tichondrius", "Monk",
                        "Brewmaster", stats=st(), drop_flask=True)
    noflask_rows.append(rw)
    noflask_recs.append(rc)
nb = run_block(noflask_rows, noflask_recs)
assert nb and "flasks" not in nb["specs"]["Monk|Brewmaster"]
# the constant copy now names consumables ("flask, food"); only the coverage
# CLAUSE must be absent without flask data
assert "flask known" not in nb["cohort"], nb["cohort"]
print("hasFlask  : zero coverage -> no flasks keys, no coverage claim")

# --- payload size sanity at full scale: 27 specs x 24 chars, two flask
# variants each -- the worst realistic shape the block should ever take
big_rows, big_recs = [], []
n = 0
for s in range(27):
    cls, spec = f"Class{s % 9}", f"Spec{s}"
    for i in range(24):
        n += 1
        flask = ("Flask of Tempered Aggression" if i < 12
                 else "Flask of Saturated Malice")
        rw, rc = make_parse(f"B{n}", 1, f"Char{n}", "Srv", cls, spec,
                            stats=st(crit=1000 + i * 37, Leech=300),
                            flask=flask)
        big_rows.append(rw)
        big_recs.append(rc)
bb = run_block(big_rows, big_recs)
assert len(bb["specs"]) == 27
blob = json.dumps(bb, separators=(",", ":"))
assert len(blob) < 40_000, len(blob)
print(f"size      : 27 specs x 5 stats x (all + 2 flasks) = "
      f"{len(blob):,} bytes -- comfortably payload-sized")

# ===========================================================================
# specmeta: the generic per-dimension best-players block
# ===========================================================================
mrows, mrecs = [], []


def madd(*a, **kw):
    rw, rc = make_parse(*a, **kw)
    mrows.append(rw)
    mrecs.append(rc)


# Hunters: 40 characters, dps = i*1000. Builds A (1-19), B (20-38), C (39-40,
# under the entry floor). Trinket 111 on everyone, 222 on 1-20, 333 on 21-40.
# Enchant 15:7008 on everyone, gem 90001 on 1-25 only.
for i in range(1, 41):
    madd(f"H{i}", 1, f"Hunt{i}", "X", "Hunter", "Marksmanship",
         dmg=i * 1_500_000,
         build=("BUILD_A" if i <= 19 else "BUILD_B" if i <= 38 else "BUILD_C"),
         trinkets=(111, 222) if i <= 20 else (111, 333),
         ench=7008, gems=(90001,) if i <= 25 else None)
# Hunt1's OLDER parse on a junk build: per-character dedup must drop it (its
# dps still counts toward the spec's percentile base, as a real parse would)
madd("H1old", 1, "Hunt1", "X", "Hunter", "Marksmanship",
     start_ms=BASE_MS - 1 * DAY_MS, dmg=1_500_000, build="BUILD_STALE",
     trinkets=(999, 998), ench=1, gems=(1,))
# below the key floor: out of cohort entirely
madd("HX", 1, "LowKey", "X", "Hunter", "Marksmanship", key=11,
     dmg=99 * 1_500_000, build="BUILD_JUNK", trinkets=(999, 998))

# Paladins: 20 characters, dps = i*100; a build on only the first 10 (the
# builds denominator must shrink to 10), default single-item gear so the
# trinket/enchant/gem dimensions have nothing to say; top quartile = 5 chars,
# under the floor, so the spec ships without a "top" band
for i in range(1, 21):
    madd(f"L{i}", 1, f"Pal{i}", "Y", "Paladin", "Holy", dmg=i * 150_000,
         build="BUILD_H" if i <= 10 else None)

mb = run_block(mrows, mrecs, bsd.spec_meta_block)
assert mb["bands"] == ["all", "top"], mb["bands"]
assert mb["dims"] == ["builds", "trinkets", "enchants", "gems"], mb["dims"]
assert set(mb["specs"]) == {"Hunter|Marksmanship", "Paladin|Holy"}

h = mb["specs"]["Hunter|Marksmanship"]
V = h["vals"]
# per-character bests are 1000..40000; the quartile cut interpolates to
# 30250, so the top band is exactly the ten characters 31..40
assert h["n"] == 40 and h["ntop"] == 10, (h["n"], h["ntop"])
ba = h["dims"]["builds"]["all"]
assert ba["d"] == 40, ba
assert [(V[e["v"]], e["n"], e["dps"]) for e in ba["e"]] == \
    [("BUILD_A", 19, 10000), ("BUILD_B", 19, 29000)], ba["e"]
bt = h["dims"]["builds"]["top"]
assert bt["d"] == 10, bt
assert [(V[e["v"]], e["n"], e["dps"]) for e in bt["e"]] == \
    [("BUILD_B", 8, 34500)], bt["e"]    # BUILD_C's 2 chars: floor-dropped
assert "BUILD_STALE" not in V and "BUILD_JUNK" not in V, V
print(f"meta builds : all A/B 19+19 (C under floor), top band n={bt['d']} "
      f"pure BUILD_B; stale + sub-floor parses invisible")

ta = h["dims"]["trinkets"]["all"]
assert [(V[e["v"]], e["n"]) for e in ta["e"]] == \
    [("111", 40), ("222", 20), ("333", 20)], ta["e"]
assert ta["e"][0]["dps"] == 20500, ta["e"][0]     # median of all 40 parses
tt = h["dims"]["trinkets"]["top"]
assert [(V[e["v"]], e["n"]) for e in tt["e"]] == \
    [("111", 10), ("333", 10)], tt["e"]
ea = h["dims"]["enchants"]["all"]
assert [(V[e["v"]], e["n"]) for e in ea["e"]] == [("15:7008", 40)], ea["e"]
ga = h["dims"]["gems"]
assert [(V[e["v"]], e["n"]) for e in ga["all"]["e"]] == [("90001", 25)]
assert "top" not in ga, ga        # no top-band char has a gem: band absent
print("meta gear   : trinkets by slot pair, slot-qualified enchant, gems; "
      "empty top slices vanish instead of shipping thin")

pl = mb["specs"]["Paladin|Holy"]
assert pl["n"] == 20 and pl["ntop"] == 5, (pl["n"], pl["ntop"])
assert set(pl["dims"]) == {"builds"}, set(pl["dims"])
pb = pl["dims"]["builds"]
assert "top" not in pb, pb        # 6 top chars < the 10-char floor
assert pb["all"]["d"] == 10, pb   # denominator: chars whose build is known
assert [(pl["vals"][e["v"]], e["n"], e["dps"]) for e in pb["all"]["e"]] == \
    [("BUILD_H", 10, 550)], pb["all"]["e"]
print("meta bands  : thin top quartile omitted; observability shrinks the "
      "share denominator, not the spec")

for needle in ("+12", "14 days", "top quartile", "latest parse"):
    assert needle in mb["cohort"], (needle, mb["cohort"])
print(f"meta cohort : {mb['cohort']!r}")

# absence: empty journal, and a journal with neither builds nor gear
assert run_block(mrows, [], bsd.spec_meta_block) is None
bare_recs = [dict(rc, gear=None,
                  talents={"tree": [{"id": 1, "rank": 1}], "specID": 1})
             for rc in mrecs]
assert run_block(mrows, bare_recs, bsd.spec_meta_block) is None
print("meta absence: empty / build-and-gear-less journal -> block None")

# size at full scale: 27 specs x 40 chars, two ~250-char loadout strings per
# spec, trinkets, enchants and gems everywhere -- the worst realistic shape
mbig_rows, mbig_recs = [], []
for s in range(27):
    cls, spec = f"Class{s % 9}", f"Spec{s}"
    for i in range(1, 41):
        rw, rc = make_parse(
            f"Z{s}_{i}", 1, f"C{s}_{i}", "Srv", cls, spec,
            dmg=i * 1_500_000,
            build=f"B{s}{'A' if i <= 20 else 'B'}" + "X" * 240,
            trinkets=(111000 + s, (222000 if i <= 20 else 333000) + s),
            ench=7000 + s, gems=(90000 + s,))
        mbig_rows.append(rw)
        mbig_recs.append(rc)
mbb = run_block(mbig_rows, mbig_recs, bsd.spec_meta_block)
assert len(mbb["specs"]) == 27
mblob = json.dumps(mbb, separators=(",", ":")).encode()
mgz = len(gzip.compress(mblob))
assert mgz < 40_000, mgz
print(f"meta size   : 27 specs, 4 dims, 2 bands = {len(mblob):,} bytes raw / "
      f"{mgz:,} gz -- inside the 40 KB gz target")

# ===========================================================================
# stats sidecar: per-parse packed ratings, row-aligned with the payload
# ===========================================================================


def run_sidecar(rows, recs, enc=None, cap=None):
    """Same journal-on-disk path as run_block, returning (df, parsed doc)."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = pathlib.Path(tmp) / "gear.jsonl"
        with journal.open("w") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        bsd.GEAR_JOURNAL = journal
        bsd.GEAR_EXPORT = pathlib.Path(tmp) / "absent.jsonl.gz"
        df = pd.DataFrame(rows)
        kw = {} if cap is None else {"cap": cap}
        doc = bsd.stats_sidecar(df, bsd.stats_from_gear_journal(),
                                "test", enc=enc, **kw)
        return df, (json.loads(doc) if doc is not None else None)


def decode(doc, dtype, field="data"):
    return np.frombuffer(base64.b64decode(doc[field]), dtype=dtype)


def rowat(df, char, code):
    return int(df.index[(df["character"] == char)
                        & (df["report_code"] == code)][0])


# the specstats fixtures again: rogues (stats, no flask field... None), a torn
# capture, priests with flasks, warriors journaled before flask capture
df_s, sc = run_sidecar(rows, recs, enc="dense")
W = len(sc["stats"]) + 1
assert sc["stats"] == list(bsd.SIDECAR_STATS) and W == 11, sc["stats"]
assert sc["flask0"] == "unknown" and sc["flask1"] == "none"
assert sc["flasks"] == ["Flask of Saturated Malice",
                        "Flask of Tempered Aggression"], sc["flasks"]
assert sc["enc"] == "dense" and sc["n"] == len(df_s), (sc["enc"], sc["n"])
arr = decode(sc, "<u2")
assert len(arr) == len(df_s) * W, len(arr)
# base64 round-trips byte-exactly
assert base64.b64encode(base64.b64decode(sc["data"])).decode() == sc["data"]

i = rowat(df_s, "Rogue1", "R1")     # Crit 1000, Haste 2000, ..., Leech 500
assert arr[i * W:(i + 1) * W].tolist() == \
    [0, 0, 0, 1000, 2000, 3000, 4000, 500, 0, 0, 0], arr[i * W:(i + 1) * W]
i = rowat(df_s, "Priest1", "P1")    # Tempered Aggression -> code 3
assert arr[i * W + 3] == 1000 and arr[i * W + W - 1] == 3, arr[i * W:(i + 1) * W]
i = rowat(df_s, "Priest23", "P23")  # auras visible, no flask -> code 1
assert arr[i * W + W - 1] == 1
i = rowat(df_s, "Warr1", "W1")      # journaled before flask capture -> 0
assert arr[i * W + 3] == 4000 and arr[i * W + W - 1] == 0
i = rowat(df_s, "Torn", "RX4")      # torn stats: journal-skipped, all zeros
assert arr[i * W:(i + 1) * W].tolist() == [0] * W
i = rowat(df_s, "LowKey", "RX1")    # sidecar is per-parse: cohort-free; the
assert arr[i * W + 3] == 0xFFFF     # junk 999999 rating clamps to u16
print("sidecar     : dense decode pinned per row -- stats order, flask "
      "codes 0/1/2+, torn row zeroed, u16 clamp")

# ROW ALIGNMENT is with df order, not journal order: reverse the df and every
# value must move with its row
df_r, sc_r = run_sidecar(rows[::-1], recs, enc="dense")
arr_r = decode(sc_r, "<u2")
i = rowat(df_r, "Rogue1", "R1")
assert arr_r[i * W:(i + 1) * W].tolist() == \
    [0, 0, 0, 1000, 2000, 3000, 4000, 500, 0, 0, 0]
print("sidecar     : alignment follows the df (payload) order, pinned "
      "against a reversed frame")

# sparse: same values, addressed through the Uint32 row-index array
_, sp = run_sidecar(rows, recs, enc="sparse")
assert sp["enc"] == "sparse" and sp["n"] == len(df_s)
sarr, sidx = decode(sp, "<u2"), decode(sp, "<u4", "idx")
assert len(sarr) == len(sidx) * W
lookup = {int(r): j for j, r in enumerate(sidx)}
i = rowat(df_s, "Priest1", "P1")
j = lookup[i]
assert sarr[j * W:(j + 1) * W].tolist() == arr[i * W:(i + 1) * W].tolist()
assert rowat(df_s, "Torn", "RX4") not in lookup    # unknown rows: no entry
print(f"sidecar     : sparse carries {len(sidx)} known rows + Uint32 "
      f"indices, values identical to dense")

# auto mode picks one of the two and says which
_, auto = run_sidecar(rows, recs)
assert auto["enc"] in ("dense", "sparse"), auto["enc"]

# cap: tertiaries dropped first (and said so), then nothing rather than over
g10 = len(gzip.compress(json.dumps(
    run_sidecar(rows, recs, enc="dense")[1],
    separators=(",", ":")).encode(), 6))
_, capped = run_sidecar(rows, recs, enc="dense", cap=g10 - 1)
assert capped is not None
assert capped["stats"] == list(bsd.SIDECAR_STATS[:bsd.SIDECAR_CORE]), \
    capped["stats"]
assert len(decode(capped, "<u2")) == len(df_s) * 8
_, gone = run_sidecar(rows, recs, enc="dense", cap=1)
assert gone is None
_, empty = run_sidecar(rows, [])
assert empty is None
print("sidecar     : cap drops Leech/Speed/Avoidance first, then omits; "
      "empty journal -> no file")

print("\nPASS")
