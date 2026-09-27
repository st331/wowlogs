#!/usr/bin/env python3
"""The execution bundle's wiring through fetch_data.py, on the real fixture.

The parsers are pinned in test_execution_bundle.py; this suite pins the
CALLER'S path -- the same seam the 2026-08-27 arity outage taught
(parse_node), plus the request, the cost estimate, the journal, the export
and the cold-start seed:

  * batch_query emits the six-table bundle for a run the gate admitted and
    the Summary alone for the rest, in ONE aliased request; the casts
    filter is the roster's kit, DamageTaken appears only when the lists
    carry an avoidable list for the dungeon; SUMMARY_BATCH is still 8;
  * batch_est_cost reserves 7.5 per bundled run and 2.6 as before otherwise;
  * parse_node on a bundled node writes exec = 1 and the per-player columns
    AFTER keystone_s, and fills the run-level record; on a plain node
    exec = 0 with the bundle columns None and pots / hs / deaths_chain
    present; a bundle whose aliases all errored (null tables) is exec = 0;
    the legacy 3-argument call still works;
  * export(): a journal holding pre-bundle rows (no keys), Summary-only rows
    and bundled rows writes a CSV whose first 22 columns are the original
    ones in the original order, the new columns after them, NaN where a row
    never carried them; kicks_by round-trips through pack/unpack;
  * seed_from_csv() rebuilds a journal the gate's rebuild() counts exactly;
  * load_fights() carries the ranking's roster specs on the fight.
"""
import gzip
import json
import os
import pathlib
import sys
import tempfile
import time

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import execution as ex                       # noqa: E402
import fetch_data as fd                      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "bundle_shape_f.json"
ORIGINAL_COLUMNS = ["character", "server", "region", "class", "spec", "hero_talent", "role",
                    "dungeon", "key_level", "duration_s", "damage_done", "dps", "deaths",
                    "item_level", "set_counts", "score", "medal", "affixes", "report_code",
                    "fight_id", "started_at", "keystone_s"]
NOW_MS = int(time.time() * 1000) - 3_600_000
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


class _Hero:
    def resolve(self, tree):
        return "Hero"


rd = json.loads(FIXTURE.read_text())["data"]["reportData"]
lists = ex.load_lists(fetch=False, log=lambda *_: None)


def fight_for(alias, i, bundle):
    node = rd[alias]
    comp = node["table"]["data"]["composition"]
    specs = [ex.spec_key(c["type"], c["specs"][0]["spec"]) for c in comp]
    return {"code": f"{alias}Code{i:02d}xxxxxxxx", "fid": 8, "dungeon": "Altar of Fangs",
            "key_level": 14 + i, "region": "EU", "score": 400.0 + i, "medal": "gold",
            "affixes": [9, 10], "start_time": NOW_MS - i * 600_000,
            "rank_duration_ms": node["table"]["data"]["totalTime"] + 20_000,
            "specs": specs, "_bundle": bundle}


# --- 1. the request ----------------------------------------------------------------
fb = fight_for("a1", 0, True)
fp = fight_for("a0", 1, False)
q = fd.batch_query([fb, fp], lists)
kit = ",".join(str(i) for i in ex.kit_ids(lists, fb["specs"]))
check(q.startswith("{ reportData { a0: report(code: \"a1Code00xxxxxxxx\") { table: table(fightIDs: [8], dataType: Summary) interrupts: table(fightIDs: [8], dataType: Interrupts) dispels: table(fightIDs: [8], dataType: Dispels) casts: table(fightIDs: [8], dataType: Casts, filterExpression: \"ability.id in (" + kit + ")\") healing: table(fightIDs: [8], dataType: Healing) } a1: report(code: \"a0Code01xxxxxxxx\") { table(fightIDs: [8], dataType: Summary) } } }"),
      "batch_query: bundle for the gated run (kit filter from the roster), Summary alone for the other")
check("dmgTaken" not in q, "batch_query: provisional lists have no avoidable list -> no DamageTaken table")
full = json.loads(json.dumps(lists))
full["dungeons"] = {"Altar of Fangs": {"avoidable": [1306338, 1308518]}}
q2 = fd.batch_query([fb], full)
check('dmgTaken: table(fightIDs: [8], dataType: DamageTaken, filterExpression: "ability.id in (1306338,1308518)")' in q2,
      "batch_query: with an avoidable list the DamageTaken table is filtered to it")
check(fd.batch_est_cost([fb, fp]) == 7.5 + 2.6 and fd.batch_est_cost([fp] * 8) == 2.6 * 8
      and fd.SUMMARY_BATCH == 8, "batch_est_cost: 7.5 per bundled run, 2.6 per Summary-only; 8 per request")

# --- 2. the caller's path --------------------------------------------------------------
run = {}
rows, gear = fd.parse_node(fb, rd["a1"], _Hero(), run)
keys = list(rows[0].keys())
check(len(rows) == 5 and keys[:22] == ORIGINAL_COLUMNS and keys[22:] == list(ex.EXEC_COLUMNS),
      "parse_node (bundled): the 22 original keys in order, then the bundle columns")
check(all(r["exec"] == 1 and isinstance(r["kicks"], int) and isinstance(r["kicks_by"], str)
          and isinstance(r["avoid_dmg"], int) and isinstance(r["heal_over"], int)
          and r["pots"] is not None and r["deaths_chain"] == 0 for r in rows),
      "parse_node (bundled): exec 1, ints, kicks_by packed, pots present")
check(run["exec"] is True and run["report_code"] == fb["code"] and run["fight_id"] == 8
      and run["dungeon"] == "Altar of Fangs" and run["key_level"] == 14 and run["started_at"] == fb["start_time"]
      and len(run["int_spells"]) == 6 and len(run["dispel_spells"]) == 3
      and run["dispel_spells"]["1294569"] == {"name": "Paralyzing Shots", "applied": 19, "expired": 11, "dispelled": 8},
      "parse_node (bundled): run record with the per-spell sums")
run2 = {}
rows2, _ = fd.parse_node(fp, {"table": rd["a0"]["table"]}, _Hero(), run2)
check(all(r["exec"] == 0 and all(r[c] is None for c in ex.BUNDLE_COLUMNS) and r["pots"] is not None
          and r["deaths_chain"] == 0 for r in rows2) and run2["exec"] is False and run2["int_spells"] is None,
      "parse_node (plain): exec 0, bundle columns None, Summary-derived columns present")
dead = {"table": rd["a1"]["table"], **{k: None for k in ex.BUNDLE_TABLES}}
rows3, _ = fd.parse_node(fb, dead, _Hero())
check(all(r["exec"] == 0 and r["kicks"] is None for r in rows3),
      "parse_node: a bundle whose five aliases all errored is exec 0 (legacy 3-arg call)")
rows4, _ = fd.parse_summary(fb, rd["a1"]["table"], _Hero())
check(rows4[0]["exec"] == 0 and "kicks" in rows4[0], "parse_summary(fight, table, hero): still works, exec 0")

# --- 3. journal -> export -> CSV -----------------------------------------------------
journal = []
old_rows, _ = fd.parse_node(fight_for("a2", 2, False), {"table": rd["a2"]["table"]}, _Hero())
for r in old_rows:
    journal.append({k: v for k, v in r.items() if k not in ex.EXEC_COLUMNS})   # pre-bundle shape
journal.extend(rows2)                                                            # Summary-only
bundled = []
for i, alias in enumerate(("a1", "a3", "a4", "a5", "a6", "a7")):
    rr, _ = fd.parse_node(fight_for(alias, 10 + i, True), rd[alias], _Hero())
    bundled.extend(rr)
journal.extend(bundled)
with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    (tp / "data").mkdir()
    fd.ROOT = tp
    fd.PROCESSED = tp / "processed"
    fd.PROCESSED.mkdir()
    fd.PLAYERS_FILE = fd.PROCESSED / "players.jsonl"
    fd.RANKINGS_FILE = tp / "absent_rankings.jsonl"
    fd.GEAR_FILE = tp / "absent_gear.jsonl"
    fd.GEAR_CSV = tp / "gear.jsonl.gz"
    fd.CSV_FILE = tp / "data" / "mythic_runs.csv.gz"
    fd.SUMMARIES_DONE = fd.PROCESSED / "summaries_done.txt"
    with fd.PLAYERS_FILE.open("w") as fh:
        for r in journal:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.environ["EXPORT_GEAR"] = "0"
    fd._OUTPUTS.clear()
    fd.export()
    csv = pd.read_csv(fd.CSV_FILE)
    cols = list(csv.columns)
    check(cols[:22] == ORIGINAL_COLUMNS and cols[22:] == list(ex.EXEC_COLUMNS) and len(csv) == len(journal),
          f"export: {len(csv)} rows; original 22 columns first and unchanged, the {len(ex.EXEC_COLUMNS)} new ones after")
    old = csv[csv.report_code == old_rows[0]["report_code"]]
    plain = csv[csv.report_code == rows2[0]["report_code"]]
    bun = csv[csv.report_code == bundled[0]["report_code"]]
    check(old["exec"].isna().all() and old["kicks"].isna().all() and old["pots"].isna().all(),
          "export: pre-bundle rows carry NaN in every new column")
    check((plain["exec"] == 0).all() and plain["kicks"].isna().all() and plain["pots"].notna().all(),
          "export: Summary-only rows exec 0, bundle columns NaN, pots present")
    check((bun["exec"] == 1).all() and bun["kicks"].notna().all()
          and [ex.unpack_by(v) for v in bun["kicks_by"]] == [ex.unpack_by(r["kicks_by"]) for r in bundled[:5]]
          and list(bun["kicks"].astype(int)) == [r["kicks"] for r in bundled[:5]],
          "export: bundled rows exec 1, kicks_by round-trips through pack/unpack")
    # --- 4. the cold-start seed and the gate's recount --------------------------------
    fd.PLAYERS_FILE.unlink()
    fd.SUMMARIES_DONE.unlink(missing_ok=True)
    fd.seed_from_csv()
    seeded = list(fd._iter_journal(fd.PLAYERS_FILE))
    check(len(seeded) == len(journal) and sum(1 for r in seeded if r.get("exec")) == len(bundled)
          and all(r.get("exec") in (None, 0.0, 1.0) for r in seeded),
          "seed_from_csv: the journal carries exec as 1.0 / 0.0 / None")
    gate = ex.BundleGate(fd.PROCESSED / "exec_quota.json")
    n = gate.rebuild(fd.PLAYERS_FILE)
    want_holy = sum(1 for r in bundled if r["class"] == "Paladin" and r["spec"] == "Holy"
                    and ex.band_of(r["key_level"]) == 24)
    check(n == len(bundled) and want_holy >= 1 and gate.count("Paladin-Holy", "Altar of Fangs", 24) == want_holy
          and gate.count("Paladin-Holy", "Altar of Fangs", 24) == gate.count("Paladin-Holy", "Altar of Fangs", 25),
          f"BundleGate.rebuild on the seeded journal: {n} bundled rows, cells keyed by the parsed spec "
          f"(Paladin-Holy b24 = {want_holy})")
    # --- 5. the roster rides the fight dict ---------------------------------------------
    fd.RANKINGS_FILE = tp / "rankings.jsonl"
    rec = {"enc": 12993, "bracket": 15, "page": 1, "more": False, "rankings": [
        {"report": {"code": "RosterCode000001", "fightID": 3}, "server": {"region": "US"},
         "duration": 1_500_000, "startTime": NOW_MS, "score": 400.0, "medal": "gold", "affixes": [9],
         "team": [{"class": "Warrior", "spec": "Arms", "role": "DPS"}, {"class": "Mage", "spec": "", "role": "DPS"}]},
        {"report": {"code": "NoTeamCode000002", "fightID": 1}, "server": {"region": "US"},
         "duration": 1_500_000, "startTime": NOW_MS, "score": 400.0, "medal": "gold", "affixes": [9]}]}
    fd.RANKINGS_FILE.write_text(json.dumps(rec) + "\n")
    fights = fd.load_fights(None)
    check(fights["RosterCode000001:3"]["specs"] == ["Warrior-Arms", "Mage"]
          and fights["NoTeamCode000002:1"]["specs"] == [],
          "load_fights: roster specs on the fight (class alone without a spec; [] without a team)")

print()
if fails:
    sys.exit(f"FAILED ({fails} failures)")
print("PASS")
