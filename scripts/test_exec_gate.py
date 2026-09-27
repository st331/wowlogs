#!/usr/bin/env python3
"""The execution bundle's quota gate (execution.BundleGate).

Design doc §4: fetch the bundle only while any of the run's five (spec,
dungeon, 2-level band) cells has fewer than 100 bundled player-rows in the
trailing 14 days. Pinned:

  * a fresh counter admits every rostered run; a cell at the quota closes,
    and a run is admitted iff ANY of its cells is still open;
  * the window is trailing: rows older than 14 days stop counting without
    a rebuild, undated rows count as today;
  * a class-only roster entry (no spec on the ranking) reads the class's
    spec cells summed; no roster at all is never admitted and is counted;
  * rebuild() recounts from the players journal and sees rows the CSV seed
    wrote back as 1.0, ignores exec 0 / null / absent, tolerates torn lines;
  * save()/load() round-trip, dropping days outside the window;
  * record_rows() counts only bundled rows, by the parsed spec.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import execution as ex                       # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


DAY = ex.DAY_MS
NOW = 1_790_000_000_000                       # a fixed instant, epoch ms
ROSTER = ["Warrior-Arms", "Rogue-Assassination", "Hunter-Marksmanship",
          "Shaman-Restoration", "DeathKnight-Blood"]
DUN = "Altar of Fangs"

g = ex.BundleGate(None, now_ms=NOW)
check(g.admits(ROSTER, DUN, 18) is True and g.stats["admitted"] == 1, "fresh counter: admitted")
check(ex.band_of(18) == 18 and ex.band_of(19) == 18 and ex.band_of(20) == 20 and ex.band_of(11) == 10,
      "band_of: 2-level bands anchored on even levels (b18 = 18-19)")
check(ex.cell_key("Warrior-Arms", DUN, 19) == "Warrior-Arms|Altar of Fangs|b18", "cell_key format")

# fill four of the five cells to the quota, from both levels of the band
for sk in ROSTER[:4]:
    for i in range(ex.QUOTA_ROWS):
        g.record(sk, DUN, 18 + (i % 2), NOW - i * 3_600_000)
check(all(g.count(sk, DUN, 18) == 100 and g.count(sk, DUN, 19) == 100 for sk in ROSTER[:4])
      and g.count(ROSTER[4], DUN, 18) == 0, "record/count: 100 rows per cell, both levels of the band")
check(g.admits(ROSTER, DUN, 19) is True, "one open cell (DeathKnight-Blood) still admits the run")
for i in range(ex.QUOTA_ROWS):
    g.record(ROSTER[4], DUN, 18, NOW)
check(g.admits(ROSTER, DUN, 18) is False and g.stats["full"] == 1, "every cell at the quota: gated out")
check(g.admits(ROSTER, DUN, 20) is True, "the next band is a different cell: admitted")
check(g.admits(ROSTER, "Murder Row", 18) is True, "another dungeon is a different cell: admitted")
check(g.admits(ROSTER[:1] + ["Mage-Arcane"], DUN, 18) is True, "a roster with one rare spec: admitted")
check(g.cells_full() == 5 and g.cells_open() == 0, f"cells_full={g.cells_full()}, cells_open={g.cells_open()}")

# --- the trailing window ------------------------------------------------------
g2 = ex.BundleGate(None, now_ms=NOW)
for i in range(ex.QUOTA_ROWS):
    g2.record("Mage-Arcane", DUN, 12, NOW - 13 * DAY)           # 13 days ago: in the window
check(g2.count("Mage-Arcane", DUN, 12) == 100, "rows 13 days old count")
g3 = ex.BundleGate(None, now_ms=NOW)
for i in range(ex.QUOTA_ROWS):
    g3.record("Mage-Arcane", DUN, 12, NOW - 15 * DAY)           # 15 days ago: out
check(g3.count("Mage-Arcane", DUN, 12) == 0 and g3.admits(["Mage-Arcane"] * 5, DUN, 12),
      "rows 15 days old do not count: the cell reopened without a rebuild")
g3.record("Mage-Arcane", DUN, 12, None)
g3.record("Mage-Arcane", DUN, 12, 0)
g3.record("Mage-Arcane", DUN, 12, NOW + 30 * DAY)
check(g3.count("Mage-Arcane", DUN, 12) == 3, "undated / implausible rows count as today")
later = ex.BundleGate(None, now_ms=NOW + 2 * DAY)
later.cells = g2.cells
check(later.count("Mage-Arcane", DUN, 12) == 0, "the same counts read two days later have slid out")

# --- class-level fallback ---------------------------------------------------------
g4 = ex.BundleGate(None, now_ms=NOW)
for i in range(60):
    g4.record("Warrior-Arms", DUN, 14, NOW)
for i in range(50):
    g4.record("Warrior-Fury", DUN, 15, NOW)
check(g4.count("Warrior", DUN, 14) == 110 and g4.count("Warrior-Arms", DUN, 14) == 60,
      "class-only key: the class's spec cells summed (110 = 60 Arms + 50 Fury)")
check(g4.admits(["Warrior"], DUN, 14) is False and g4.admits(["Warrior"], DUN, 16) is True,
      "class-only roster gated on the summed count")
check(g4.admits([], DUN, 14) is False and g4.admits(None, DUN, 14) is False
      and g4.admits(["Warrior-Arms"], None, 14) is False and g4.stats["no_roster"] == 3,
      "no roster / no dungeon: never admitted, counted as no_roster")

# --- rebuild from the players journal ----------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    jp = pathlib.Path(tmp) / "players.jsonl"
    base = {"class": "Warrior", "spec": "Arms", "dungeon": DUN, "key_level": 18, "started_at": NOW - DAY}
    rows = [dict(base, exec=1)] * 7                        # written by the collector
    rows += [dict(base, exec=1.0, key_level=19)] * 3       # seeded back from the CSV (float)
    rows += [dict(base, exec=True, spec="Fury")] * 2       # a bool, for good measure
    rows += [dict(base, exec=0)] * 4                       # Summary-only rows
    rows += [dict(base, exec=None)] * 2                    # CSV NaN
    rows += [dict(base)] * 5                               # pre-bundle rows: no key
    rows += [dict(base, exec=1, started_at=NOW - 20 * DAY)]   # bundled but out of the window
    with jp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        fh.write("\n")                                     # blank line
        fh.write(json.dumps(dict(base, exec=1))[:30])      # torn tail
    g5 = ex.BundleGate(pathlib.Path(tmp) / "exec_quota.json", now_ms=NOW)
    n = g5.rebuild(jp)
    check(n == 13 and g5.count("Warrior-Arms", DUN, 18) == 10 and g5.count("Warrior-Fury", DUN, 18) == 2,
          f"rebuild: {n} bundled rows counted (1, 1.0, true), exec 0/null/absent and out-of-window ignored, "
          f"torn tail tolerated")
    g5.save()
    doc = json.loads(g5.path.read_text())
    check(doc["v"] == 1 and doc["quota"] == ex.QUOTA_ROWS and doc["days"] == ex.QUOTA_DAYS
          and set(doc["cells"]) == {"Warrior-Arms|Altar of Fangs|b18", "Warrior-Fury|Altar of Fangs|b18"},
          "save: the out-of-window day is not written")
    g6 = ex.BundleGate.load(g5.path, now_ms=NOW)
    check(g6.count("Warrior-Arms", DUN, 19) == 10 and g6.count("Warrior-Fury", DUN, 19) == 2 and not g6.dirty,
          "load: counts round-trip")
    check(ex.BundleGate.load(pathlib.Path(tmp) / "missing.json", now_ms=NOW).cells == {},
          "load: a missing file is an empty counter")
    # record_rows: only exec rows, by the PARSED class/spec
    g7 = ex.BundleGate(None, now_ms=NOW)
    n = g7.record_rows([dict(base, exec=1), dict(base, exec=0), dict(base, exec=1, spec="Fury"),
                        dict(base, exec=1, spec=None)])
    check(n == 3 and g7.count("Warrior-Arms", DUN, 18) == 1 and g7.count("Warrior", DUN, 18) == 3,
          "record_rows: bundled rows only, spec-keyed (a row without a spec lands on the class)")

print()
if fails:
    sys.exit(f"FAILED ({fails} failures)")
print("PASS")
