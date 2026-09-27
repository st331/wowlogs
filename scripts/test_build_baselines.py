#!/usr/bin/env python3
"""scripts/build_baselines.py: the baselines sidecar and the run store
(keylevel_addon design/baselines-from-wowlogs.md §2, §3, §5), pinned:

  * cells at the three tiers (spec|dungeon|level, spec|dungeon|bNN,
    spec|*|bNN) ship at n >= 20 with n and n_exec; a measure's quantiles
    ship when it has 20 values in the cell (so a cell of Summary-only rows
    carries dps / deaths and none of the bundle measures); quantiles are
    numpy's linear interpolation at [5,10,25,50,75,90,95];
  * rates are per duration_s; kick_prio weights kicks_by by the window's
    priority table (interrupted / begun, 0.5 unlisted); heal_eff_s is
    healers only;
  * the population is timed runs at levels 10-30; the run store holds every
    run in the window at +10 and up whatever its medal (a depleted +10
    included, a +9 not), in 256 shards keyed by the hash of the report
    code's first four characters (pinned against the JS form's output), every
    shard written (an empty one as "runs": {}), unfetched fields OMITTED
    with "exec": false kept, the run's dispel_spells when bundled and never
    its int_spells; a stale file from an earlier build is removed;
  * a round trip: a fixture CSV + runs journal -> both site directories,
    keys as the contract lists them, priority/dispellable summed over the
    journal, health lines APPENDED to build_health.txt.
"""
import gzip
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_baselines as bb                 # noqa: E402
import execution as ex                       # noqa: E402

NOW = pd.Timestamp("2026-09-27T06:00:00Z")
NOW_MS = int(NOW.timestamp() * 1000)
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def rows(spec, dungeon, level, n, *, exec_=1, prefix="P", role="DPS", dps0=100_000,
         medal="gold", region="US"):
    cls, sp = spec.split("-")
    out = []
    for i in range(n):
        e = bool(exec_)
        out.append({
            "character": f"{sp}{i}", "server": "Srv", "region": region, "class": cls, "spec": sp,
            "hero_talent": "H", "role": role, "dungeon": dungeon, "key_level": level,
            "duration_s": 1200.0, "damage_done": (dps0 + i * 1000) * 1200, "dps": dps0 + i * 1000,
            "deaths": i % 3, "item_level": 300, "set_counts": "none", "score": 400.0, "medal": medal,
            "affixes": "9|10", "report_code": f"{prefix}{sp[:3]}{dungeon[:2]}{level}x{i:03d}",
            "fight_id": 1, "started_at": NOW_MS - (i + 1) * 3_600_000, "keystone_s": 1230.0,
            "exec": exec_, "pots": 2, "hs": 0, "deaths_chain": i % 2,
            "kicks": (10 + i) if e else None, "kicks_by": ex.pack_by({"1": 6 + i, "2": 4}) if e else None,
            "dispels": 2 if e else None, "dispels_by": ex.pack_by({}) if e else None,
            "avoid_dmg": 1_000_000 if e else None, "def_casts": 20 if e else None,
            "heal_total": 5_000_000 if e else None, "heal_over": 1_000_000 if e else None,
        })
    return out


ALTAR, MURDER = "Altar of Fangs", "Murder Row"
frame = pd.DataFrame(
    rows("Warrior-Arms", ALTAR, 18, 25)                     # exact cell
    + rows("Warrior-Arms", ALTAR, 19, 19)                   # under 20 alone; band b18 = 44
    + rows("Warrior-Arms", MURDER, 18, 20, prefix="Q")      # exact at the floor; pooled b18 = 64
    + rows("Mage-Arcane", ALTAR, 20, 25, exec_=0)           # Summary-only cell
    + rows("Shaman-Restoration", ALTAR, 18, 20, role="Healer", prefix="R"))
priority = {ALTAR: {"1": {"name": "Hiss", "begun": 10, "completed": 2, "interrupted": 8}}}

# --- 1. measures ----------------------------------------------------------------------
m = bb.measures_frame(frame, priority)
w = m[(m.spec == "Warrior-Arms") & (m.dungeon == ALTAR) & (m.level == 18)]
check(len(w) == 25 and (w.band == 18).all() and (w["exec"] == 1).all(), "measures: coordinates, band 18, exec")
i0 = w.index[0]
check(abs(m.loc[i0, "kicks_min"] - 10 / 20) < 1e-9 and abs(m.loc[i0, "deaths_30m"] - 0) < 1e-9
      and abs(m.loc[i0, "kick_prio"] - (0.8 * 6 + 0.5 * 4) / 20) < 1e-9
      and abs(m.loc[i0, "avoid_dmg_min"] - 1_000_000 / 20) < 1e-9 and m.loc[i0, "pots"] == 2,
      "measures: per-minute rates over duration_s; kick_prio = Σ (interrupted/begun | 0.5) × kicks ÷ min")
check(m.loc[i0, "heal_eff_s"] != m.loc[i0, "heal_eff_s"], "measures: heal_eff_s NaN for a DPS")
h = m[m.spec == "Shaman-Restoration"]
check(abs(h["heal_eff_s"].iloc[0] - (5_000_000 - 1_000_000) / 1200) < 1e-6, "measures: heal_eff_s for a healer")
a = m[m.spec == "Mage-Arcane"]
check(a["kicks_min"].isna().all() and a["dps"].notna().all() and (a["exec"] == 0).all(),
      "measures: Summary-only rows carry dps and no bundle measures")

# --- 2. cells -------------------------------------------------------------------------
cells, counts = bb.build_cells(m)
want = np.percentile([100_000 + i * 1000 for i in range(25)], bb.QUANTILES)
c = cells["Warrior-Arms|Altar of Fangs|18"]
check(c["n"] == 25 and c["n_exec"] == 25 and c["dps"] == [int(round(v)) for v in want],
      f"exact cell: n 25, n_exec 25, dps quantiles = numpy linear ({c['dps']})")
check(all(k in c for k in bb.MEASURES if k != "heal_eff_s") and "heal_eff_s" not in c,
      "exact cell: every measure but heal_eff_s (healers only)")
check("Warrior-Arms|Altar of Fangs|19" not in cells, "a 19-row cell is not shipped")
b = cells["Warrior-Arms|Altar of Fangs|b18"]
check(b["n"] == 44 and b["n_exec"] == 44, "band cell b18 pools levels 18 and 19 (44 rows)")
p = cells["Warrior-Arms|*|b18"]
check(p["n"] == 64 and "Warrior-Arms|Murder Row|18" in cells and cells["Warrior-Arms|Murder Row|18"]["n"] == 20,
      "pooled cell over dungeons (64 rows); a 20-row exact cell ships")
mg = cells["Mage-Arcane|Altar of Fangs|20"]
check(mg["n"] == 25 and mg["n_exec"] == 0 and "dps" in mg and "deaths_30m" in mg and "chain_30m" in mg
      and "pots" in mg and not any(k in mg for k in bb.EXEC_MEASURES),
      "Summary-only cell: n_exec 0, dps/deaths/chain/pots shipped, bundle measures omitted")
sh = cells["Shaman-Restoration|Altar of Fangs|18"]
check(sh["heal_eff_s"] == [3333.333] * 7 or sh["heal_eff_s"] == [3333] * 7, f"healer cell: heal_eff_s {sh['heal_eff_s'][0]}")
check(counts == {"exact": 4, "band": 4, "pooled": 3, "exec_ready": 8, "exec_100": 0}, f"tier counts {counts}")
check(bb.round_quantiles([0.123456, 99.9999, 100.4, 123456.6]) == [0.123, 100.0, 100, 123457],
      "round_quantiles: 3 decimals under 100, whole numbers from 100")

# --- 3. shards ------------------------------------------------------------------------
# the JS form -- h = (h*31 + code.charCodeAt(i)) % 256 over i < 4, two hex digits -- run under node on these codes
JS_SHARDS = {"P3j1myqhvQcMp6Tk": "2a", "gDZJMwmzvykQprcB": "ad", "abcd": "42", "ABCD": "42", "0000": "00",
             "zzzz": "80", "9abcdef": "89", "a": "61", "PnKTGa9C2WtrDdyh": "07"}
check(all(bb.shard_of(c) == h for c, h in JS_SHARDS.items()), "shard_of: identical to the client's hash on 9 pinned codes")
check(bb.shard_of("abcdEFGH") == bb.shard_of("abcdzzzz") == "42", "shard_of: only the first four characters count")
check(bb.shard_of("") is None and bb.shard_of(None) is None and bb.shard_of(float("nan")) is None,
      "shard_of: no code -> None (not a run)")
check(bb.all_shards()[0] == "00" and bb.all_shards()[-1] == "ff" and len(set(bb.all_shards())) == 256,
      "all_shards: 00..ff")
small = pd.DataFrame(rows("Warrior-Arms", ALTAR, 18, 2, prefix="S") + rows("Mage-Arcane", ALTAR, 9, 2, exec_=0, prefix="p", medal="none")
                     + rows("Rogue-Assassination", MURDER, 12, 1, prefix="9")
                     + rows("Hunter-Marksmanship", MURDER, 10, 1, exec_=0, prefix="-", medal="none"))
recs = {f"{small.report_code.iloc[0]}:1": {"exec": True, "int_spells": {"1": {"name": "Hiss", "begun": 3, "completed": 1, "interrupted": 2}},
                                            "dispel_spells": {"7": {"name": "Sting", "applied": 4, "dispelled": 3, "expired": 1}}}}
shards, st = bb.build_run_store(small, recs, "2026-09-27T06:00:00Z")
stored = {k for s in shards.values() for k in s["runs"]}
check(set(shards) == set(bb.all_shards()) and st == {"runs": 4, "runs_exec": 3, "unsharded": 0, "players": 4, "below_level": 2}
      and all(s["built"] == "2026-09-27T06:00:00Z" for s in shards.values())
      and stored == {f"{c}:1" for c in small.report_code if not c.startswith("p")},
      f"build_run_store: all 256 shards with the built stamp; +9 rows left out, the depleted +10 kept ({st})")
c0 = bb.shard_of(small.report_code.iloc[0])
r0 = shards[c0]["runs"][f"{small.report_code.iloc[0]}:1"]
check(r0["dun"] == ALTAR and r0["lvl"] == 18 and r0["dur_s"] == 1200.0 and r0["timed"] is True and r0["exec"] is True
      and "int_spells" not in r0 and r0["dispel_spells"]["7"]["dispelled"] == 3 and len(r0["players"]) == 1,
      "stored run: dun/lvl/start/dur_s/timed/exec, dispel_spells from the journal, no int_spells")
pl = r0["players"][0]
check(pl["kicks"] == 10 and pl["kicks_by"] == {"1": 6, "2": 4} and pl["dispels_by"] == {} and pl["heal_over"] == 1_000_000
      and pl["deaths_chain"] == 0 and pl["pots"] == 2 and pl["hs"] == 0 and pl["class"] == "Warrior" and pl["role"] == "DPS",
      "stored player: the bundle fields, kicks_by / dispels_by as dicts")
c1 = bb.shard_of(small.report_code.iloc[5])
r1 = shards[c1]["runs"][f"{small.report_code.iloc[5]}:1"]
check(r1["timed"] is False and r1["lvl"] == 10 and r1["exec"] is False
      and not any(k in r1["players"][0] for k in ex.BUNDLE_COLUMNS)
      and set(r1["players"][0]) == {"name", "server", "region", "class", "spec", "role", "dps", "deaths",
                                    "deaths_chain", "pots", "hs"},
      "a depleted +10 run is stored with \"exec\": false and NO bundle keys (omitted, not null)")
old = pd.DataFrame(rows("Warrior-Arms", ALTAR, 12, 1, prefix="O"))
for k in ex.EXEC_COLUMNS:
    old[k] = np.nan                                            # a pre-bundle row
shards_o, _ = bb.build_run_store(old, {}, "x")
po = shards_o[bb.shard_of(old.report_code.iloc[0])]["runs"][f"{old.report_code.iloc[0]}:1"]["players"][0]
check(set(po) == {"name", "server", "region", "class", "spec", "role", "dps", "deaths"},
      "a pre-bundle row: pots / hs / deaths_chain omitted too (the client reads a missing field as null)")
empty_shards = [c for c, s in shards.items() if not s["runs"]]
check(len(empty_shards) == 256 - len({bb.shard_of(c) for c in small.report_code if not c.startswith("p")})
      and shards[empty_shards[0]] == {"built": "2026-09-27T06:00:00Z", "runs": {}},
      f"{len(empty_shards)} empty shards carry the stamp and an empty runs object")

# --- 4. the round trip --------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    csv = tp / "mythic_runs.csv.gz"
    both = pd.concat([frame, small], ignore_index=True)
    both.to_csv(csv, index=False, compression="gzip")
    journal = tp / "runs.jsonl"
    with journal.open("w") as fh:
        for k, rec in recs.items():
            code, fid = k.rsplit(":", 1)
            fh.write(json.dumps({"report_code": code, "fight_id": int(fid), "dungeon": ALTAR, **rec}) + "\n")
        # a second bundled run in the same dungeon: the tables SUM
        fh.write(json.dumps({"report_code": frame.report_code.iloc[0], "fight_id": 1, "dungeon": ALTAR, "exec": True,
                             "int_spells": {"1": {"name": "Hiss", "begun": 7, "completed": 1, "interrupted": 6}},
                             "dispel_spells": None}) + "\n")
        fh.write(json.dumps({"report_code": "NotInWindow00000", "fight_id": 1, "dungeon": ALTAR, "exec": True,
                             "int_spells": {"1": {"begun": 100, "completed": 100, "interrupted": 100}}}) + "\n")
        fh.write("{torn")
    d1, d2 = tp / "site", tp / "docs"
    for d in (d1, d2):
        (d / "runs").mkdir(parents=True)
        (d / "runs" / "Z.json.gz").write_bytes(b"stale")
        (d / "build_health.txt").write_text("built=earlier\n")
    res = bb.build(csv, journal, [d1, d2], now=NOW)
    for d in (d1, d2):
        files = sorted(p.name for p in (d / "runs").glob("*.json.gz"))
        check((d / "baselines.json.gz").exists() and files == [f"{c}.json.gz" for c in bb.all_shards()],
              f"{d.name}/: sidecar + exactly the 256 shards written, the stale file removed")
    doc = json.load(gzip.open(d1 / "baselines.json.gz"))
    check(set(doc) >= {"built", "season", "window", "population", "lists_version", "quantiles", "measures",
                       "levels", "cells", "priority", "dispellable"}
          and doc["quantiles"] == [5, 10, 25, 50, 75, 90, 95] and doc["levels"] == {"min": 10, "max": 30, "band": 2}
          and doc["window"]["resets"] == 2 and doc["window"]["to"] == "2026-09-27" and doc["built"] == "2026-09-27T06:00:00Z"
          and doc["measures"]["deaths_30m"] == {"unit": "per_30m", "better": "low"},
          "baselines.json.gz: the contract's keys, quantiles, levels, window, measures")
    # 25 from `frame` + the 2 timed Warrior-Arms +18 rows `small` adds
    check(doc["cells"]["Warrior-Arms|Altar of Fangs|18"]["n"] == 27 and "Mage-Arcane|Altar of Fangs|9" not in doc["cells"]
          and "Mage-Arcane|*|b8" not in doc["cells"],
          "cells: from timed runs at +10 and up (the +9 depleted rows are not a cell)")
    # the two in-window records SUM (3+7 begun, 2+6 interrupted); the third is out of the window
    check(doc["priority"] == {ALTAR: {"1": {"name": "Hiss", "begun": 10, "completed": 2, "interrupted": 8}}}
          and doc["dispellable"] == {ALTAR: {"7": {"name": "Sting", "applied": 4, "dispelled": 3, "expired": 1}}},
          "priority / dispellable: summed over the window's journal records only (the out-of-window run ignored)")
    # 27 values sorted by i (small's i=0,1 duplicate frame's): the median is frame's i=11
    kp = doc["cells"]["Warrior-Arms|Altar of Fangs|18"]["kick_prio"]
    check(abs(kp[3] - (0.8 * (6 + 11) + 0.5 * 4) / 20) < 1e-3, f"kick_prio uses this build's priority table (p50 {kp[3]})")
    first = frame.report_code.iloc[0]
    sh = json.load(gzip.open(d2 / "runs" / f"{bb.shard_of(first)}.json.gz"))
    lvl = pd.to_numeric(both.key_level)
    same = both[(both.report_code.map(bb.shard_of) == bb.shard_of(first)) & (lvl >= 10)]
    n_same = same[["report_code", "fight_id"]].drop_duplicates().shape[0]
    check(sh["built"] == doc["built"] and len(sh["runs"]) == n_same >= 1
          and "int_spells" not in sh["runs"][f"{first}:1"] and "dispel_spells" not in sh["runs"][f"{first}:1"],
          f"runs/{bb.shard_of(first)}.json.gz: built stamp, every run hashed to it ({n_same}); a journal record "
          f"with dispel_spells null adds nothing")
    stored = {k for c in bb.all_shards() for k in json.load(gzip.open(d1 / "runs" / f"{c}.json.gz"))["runs"]}
    want_keys = {f"{c}:{f}" for c, f in zip(both.report_code[lvl >= 10], both.fight_id[lvl >= 10])}
    check(stored == want_keys and any(k.startswith("-") for k in stored),
          f"the 256 shards together hold exactly the +10-and-up runs ({len(stored)}), a '-' code included")
    health = (d1 / "build_health.txt").read_text()
    check(health.startswith("built=earlier\n") and "baselines.rows=" in health and "baselines.cells_exact=" in health
          and "baselines.runs_shards=256" in health and "baselines.runs_largest_shard_gz=" in health
          and "baselines.runs_shards_empty=" in health
          and "baselines.total_size_gz=" in health and "baselines.bundled_share=" in health,
          "build_health.txt: the baselines lines are APPENDED after the site build's")
    want_rows = int(((both.medal == "gold") & (lvl >= 10)).sum())
    # 109 gold rows in `frame` + small's two +18 and one +12 gold rows; the depleted +10 is a run but not a timed row
    check(res["rows"] == want_rows == 112 and res["run_stats"]["runs"] == len(want_keys) == 113
          and res["run_stats"]["unsharded"] == 0 and res["run_stats"]["below_level"] == 2
          and res["sizes"]["largest_shard"] in bb.all_shards() and res["sizes"]["largest_shard_gz"] > 0,
          f"summary: {res['rows']} timed rows at +10 and up; {res['run_stats']['runs']} runs stored, 2 +9 rows left out")
    # size-budget flag is a health line, never a failure
    saved = bb.SIZE_BUDGET_GZ
    bb.SIZE_BUDGET_GZ = 1
    res2 = bb.build(csv, journal, [d1], now=NOW)
    bb.SIZE_BUDGET_GZ = saved
    check("baselines.size_over_budget=1" in (d1 / "build_health.txt").read_text() and res2["cells"],
          "over the size budget: flagged in build_health.txt, still shipped")

print()
if fails:
    sys.exit(f"FAILED ({fails} failures)")
print("PASS")
