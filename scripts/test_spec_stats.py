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
import json
import pathlib
import sys
import tempfile

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
               drop_flask=False):
    """One (player row, journal record) through the real collector path.

    stats: {"Crit": 1200, ...} rating minimums, or None for a stats-less
    combatantInfo. flask: an aura name to put in the aura list, "" for an
    aura list carrying no flask, or None for no aura list at all.
    drop_flask strips the record's flask key entirely, simulating a journal
    line written before flask capture existed.
    """
    ci = {"gear": [{"id": 1001, "itemLevel": 720}],
          "talentTree": [{"id": 1, "rank": 1}], "specID": 1}
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
                      "damageDone": [{"id": 1, "total": 9_000_000}],
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


def run_block(rows, recs):
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
        return bsd.spec_stats_block(df, started, timed, "test")


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
assert "flask" not in nb["cohort"], nb["cohort"]
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

print("\nPASS")
