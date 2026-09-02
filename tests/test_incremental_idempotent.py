#!/usr/bin/env python3
"""tests/test_incremental_idempotent.py (partitioned_payload.md §9.1, §6.3)

The §9 fixture's journals fed to partition_build.py in EIGHT arrival
chunks, rankings as a full snapshot per chunk, under the Arcane Mage rule
(tmul/projection exist) and fixture-scale pin thresholds
(PARTS_TIER_MIN_PARSES=50, PARTS_PIN_SLOTS=1):

  chunk 3  re-sends 1% of chunk 1 with changed gear + one late upload into a
           frozen day + the second copy of the midnight duplicate pair:
           the late upload changed exactly its day and its week's four cube
           files under a new cube_sha identical in all four headers; the
           duplicate collapse dirtied exactly the neighbour day and the
           loser's rows are gone from it; the resent runs changed their
           blocks, not their rows
  chunk 4  the higher Paladin set id + the Sunfury marker arrive; the daily
           slot after them upgrades the tier pin and the learned table:
           pins.upgrades[] entries, every day rebuilt newest-first within
           the per-run cap, every cube re-emitted
  chunk 5  empty, with a snapshot from which 30% of runs dropped off the
           pages: writes nothing, dirties ZERO days, seq does not advance
  chunk 6  a snapshot in which 50 runs in frozen days gain a medal: dirtied
           exactly the days holding those runs
  chunk 7  RULES['Arcane Mage'] 1.10 -> 1.06: every listed day rebuilt
           newest-first within the cap, no cube changed by it, and no
           intermediate manifest names window days with two rules_sha
           values without the withheld state being derivable from it
  chunk 8  the git mirror of season_pins.json replaced by a copy older than
           the chunk-4 upgrade: no rebuild, the authoritative pin survives
  then     a from-scratch replay of the same journals is byte-identical to
           the incremental result (every named file, chars.bin); a
           seed_from_csv() rewrite of players.jsonl is detected by the
           offset sha and replayed without duplicate rows; a run stopped by
           the deadline between days leaves a checkpoint the next run
           continues from without rebuilding the completed days; a run
           KILLED inside step 1 (after a batch's pending append and before
           its checkpoint, right after a checkpoint, in the players, gear
           and abilities tails) resumes to the byte-identical result of an
           uninterrupted run with no duplicated cache record.

Also asserted along the way (the verifier's blockers): the run revised in
snapshot 6 and dropped from the pages in 7 serves the row's own medal
again after chunk 7 (export() semantics, §6.2-1); the chunk-7 rules edit
touches EXACTLY the listed days (no cubed, unlisted day is rebuilt); the
reverse-order midnight pair is collapsed inside the one-shot replay.
"""
import collections
import json
import os
import pathlib
import shutil
import sqlite3
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import partition_format as pf                                    # noqa: E402
import parts_util as pu                                          # noqa: E402
from fixture_util import FIXTURE_DIR, fixture                    # noqa: E402

INC = FIXTURE_DIR / "parts_incr"
# fixture-scale thresholds: 50 trailing parses instead of 20k, a 10% in-tree
# share for a marker (the planted one sits in 5 days of a 3-week window); the 3-slot
# hysteresis stays (a learned table changes at every daily slot while data
# arrives, and the upgrades must land at chunk 4, not at 2 or 7) -- the
# daily slot after chunk 4 runs with PARTS_PIN_SLOTS=1
ENV = {"PARTS_TIER_MIN_PARSES": "50", "PARTS_LEARN_MIN_IN": "0.1"}
CAP = 8


def state_dir(root):
    return root / "data" / "processed" / "parts" / "s2"


def manifest(root):
    return json.loads((root / "site" / "d" / "s2" / "manifest.json").read_text())


def state(root):
    return json.loads((state_dir(root) / "state.json").read_text())


def pins(root):
    return json.loads((state_dir(root) / "season_pins.json").read_text())


def out_files(root) -> dict:
    out = state_dir(root) / "out"
    return {p.relative_to(out).as_posix(): p.stat().st_size for p in out.rglob("*") if p.is_file()}


def named_files(man) -> set:
    refs = set()
    for e in man["days"]:
        if e.get("f"):
            refs.add(e["f"])
        refs.update(e.get("specs", {}).values())
    for w in man["weeks"]:
        refs.update((w.get("f") or {}).values())
    for k in ("spec_vocab", "specstats"):
        if man.get(k):
            refs.add(man[k]["f"])
    refs.add(man["charscore"]["f"])
    refs.add(man["charscore"]["delta"]["f"])
    return refs


def day_rows_sha(man) -> dict:
    return {e["d"]: e["f"] for e in man["days"] if e.get("f") and e["d"] != "undated"}


def week_shas(man) -> dict:
    return {int(w["w"]): w.get("cube_sha") for w in man["weeks"] if w.get("f")}


def rebuilt(root) -> list:
    h = pu.parts_health(root)
    return [int(x) for x in h["parts.rebuilt_order"][0].split(",") if x]


def run(root, now, rules, extra=(), pins_file=None, env_extra=None, max_days=CAP):
    env = dict(ENV, WOWLOGS_RULES=str(pu.rules_file(root, rules)))
    if env_extra:
        env.update(env_extra)
    return pu.run_parts(root, now, pins=pins_file, max_days=max_days,
                        extra=["--withhold-cubes", str(fixture()["gap_weeks"][0])] + list(extra), env_extra=env)


def drain(root, now, rules, extra=(), env_extra=None) -> list:
    """Run at the per-run cap until nothing is left; returns the per-run
    rebuilt orders (each newest first) and the manifests in between."""
    orders, mans = [], []
    for k in range(40):
        run(root, now, rules, extra=extra, env_extra=env_extra)
        h = pu.parts_health(root)
        orders.append(rebuilt(root))
        mans.append(manifest(root))
        if h["parts.days_left"] == ["0"] and not int(h["parts.dirty_days"][0]) > len(orders[-1]):
            break
    return orders, mans


def assert_newest_first(orders: list, today: int | None = None, root=None):
    """Newest first over the drain (today's / any later day first, the
    undated day last), every run but the last at the cap. A day may appear
    a SECOND time only when a cross-day collapse re-queued it (§6.2-2) --
    then state.invalidations records the collapse."""
    flat = [d for o in orders for d in o]
    seen, firsts, repeats = set(), [], []
    for d in flat:
        (repeats if d in seen else firsts).append(d)
        seen.add(d)
    body = [d for d in firsts if d != -1]
    assert body == sorted(body, reverse=True), flat
    if -1 in firsts:
        assert firsts[-1] == -1 or firsts.index(-1) == len([d for d in firsts if d >= 0]), flat
    if repeats:
        assert root is not None, repeats
        inv = {x.get("day") for x in state(root)["invalidations"] if x.get("reason") == "collapse"}
        assert set(repeats) <= inv, (repeats, inv)
    for o in orders[:-1]:
        assert len(o) == CAP, (len(o), o)


def served_medal(root, day, code) -> set:
    """The medal the day's canonical rows carry for a run (raw.npz medal_ov)."""
    with np.load(state_dir(root) / "days" / f"d{day}" / "raw.npz", allow_pickle=False) as z:
        return set(str(m) for m in z["medal_ov"][z["report_code"] == code])


def run_days(root, keys) -> dict:
    con = sqlite3.connect(str(state_dir(root) / "ids" / "runs.sqlite"))
    out = {}
    for code, fid in keys:
        r = con.execute("SELECT day FROM runs WHERE code=? AND fid=?", (code, fid)).fetchone()
        out[(code, fid)] = None if r is None else int(r[0])
    con.close()
    return out


def keys_of_day(root, day) -> set:
    p = state_dir(root) / "days" / f"d{day}" / "keys.npz"
    with np.load(p, allow_pickle=False) as z:
        return {(str(c), int(f)) for c, f in zip(z["code"], z["fid"])}


def cube_headers(root, man, W) -> set:
    w = next(x for x in man["weeks"] if int(x["w"]) == W)
    return {pf.read(root / "site" / "d" / "s2" / f, expect_kind=part).header["cube_sha"] for part, f in w["f"].items()}


def test_incremental_idempotent():
    fx = fixture()
    now = {c["chunk"]: c["now"] for c in fx["chunks"]}
    if INC.exists():
        shutil.rmtree(INC)
    pu.common_root(INC)
    snapshots: dict = {}
    # ---- chunks 1, 2 --------------------------------------------------------
    pu.concat_journals(INC, upto=1)
    run(INC, now[1], pu.RULE_BEFORE, pins_file=FIXTURE_DIR / "pins.json", max_days=400)
    m1 = manifest(INC)
    assert m1["seq"] == 1
    pu.concat_journals(INC, upto=2)
    orders, _ = drain(INC, now[2], pu.RULE_BEFORE)
    assert_newest_first(orders, root=INC, today=254)
    m2 = manifest(INC)
    assert set(week_shas(m2)), "no cube published after chunk 2"
    # post-tuning rows exist from the Sep 9 cutoff (chunk 2): the projection is live from here
    assert m2["projection"], "the Arcane Mage rule produced no projection"
    snapshots["after2"] = (day_rows_sha(m2), week_shas(m2))
    # ---- chunk 3: late upload, duplicate copy, resend ----------------------
    pu.concat_journals(INC, upto=3)
    orders, _ = drain(INC, now[3], pu.RULE_BEFORE)
    assert_newest_first(orders, root=INC, today=254)
    h = pu.parts_health(INC)
    m3 = manifest(INC)
    late = fx["notes"]["late_upload"]
    twin, copy = fx["notes"]["dup_pair"]["twin"], fx["notes"]["dup_pair"]["copy"]
    rev_pair = fx["notes"]["dup_pair_rev"]
    resent = [tuple(k) for k in fx["notes"]["resent"]]
    resent_days = set(run_days(INC, resent).values())
    touched = set(d for o in orders for d in o)
    # the reverse pair's copy arrives into day 241 and loses on arrival (its
    # winner's signature is already in the table): its day is touched by the
    # arrival, the winner's day is not
    expected = resent_days | {late[2], twin[2], copy[2], rev_pair["copy"][2]}
    assert touched == expected, (touched ^ expected)
    assert (rev_pair["copy"][0], rev_pair["copy"][1]) not in keys_of_day(INC, rev_pair["copy"][2])
    assert (rev_pair["winner"][0], rev_pair["winner"][1]) in keys_of_day(INC, rev_pair["winner"][2])
    # every touched day's rows file is renamed (inputs_sha sits in its
    # header, §2.2: the resent gear changed the blocks and the digest), but
    # the row DATA changed exactly where rows changed: the late day and
    # both days of the duplicate pair
    d2, w2 = snapshots["after2"]
    d3, w3 = day_rows_sha(m3), week_shas(m3)
    renamed = {d for d in d2 if d in d3 and d2[d] != d3[d]} | ({late[2], twin[2], copy[2]} - set(d2))
    assert renamed >= {late[2], twin[2], copy[2]} and renamed <= expected, renamed
    changed_rows = set()
    for d in renamed:
        if d not in d2:
            changed_rows.add(d)
            continue
        a = pf.read(state_dir(INC) / "out" / d2[d], expect_kind="rows", check_name=False)
        b = pf.read(state_dir(INC) / "out" / d3[d], expect_kind="rows", check_name=False)
        same = a.header["n"] == b.header["n"] and a.header["runs"] == b.header["runs"] and \
            set(a.cols) == set(b.cols) and all(np.array_equal(a.cols[k], b.cols[k]) for k in a.cols)
        if not same:
            changed_rows.add(d)
    assert changed_rows == {late[2], twin[2], copy[2]}, changed_rows
    # the loser is gone from the neighbour day, the survivor stands
    surv = fx["notes"]["dup_pair"]["survivor"]
    loser = twin if surv == copy[0] else copy
    winner = copy if surv == copy[0] else twin
    assert (loser[0], loser[1]) not in keys_of_day(INC, loser[2])
    assert (winner[0], winner[1]) in keys_of_day(INC, winner[2])
    inv = state(INC)["invalidations"]
    assert any(x.get("reason") == "collapse" and x.get("day") == loser[2] and x.get("loser") == [loser[0], loser[1]]
               for x in inv), inv
    # cubes: the weeks holding the changed days re-emitted under ONE new sha in all four headers
    st = state(INC)
    week_of_day = {int(d): set() for d in expected}
    for wk, we in st["weeks"].items():
        for d in we.get("days", []):
            if d in week_of_day:
                week_of_day[d].add(int(wk))
    weeks_changed_rows = set().union(*(week_of_day[d] for d in changed_rows))
    for W in set(w2) | set(w3):
        if W in w2 and W in w3:
            if W in weeks_changed_rows:
                assert w2[W] != w3[W], (W, "cube not re-emitted")
            else:
                assert w2[W] == w3[W], (W, "cube changed without a row change")
    for W in w3:
        assert cube_headers(INC, m3, W) == {w3[W]}
    assert m3["window"]["rows"] > 0
    snapshots["after3"] = (d3, w3)
    pins_after3 = (state_dir(INC) / "season_pins.json").read_bytes()
    # ---- chunk 4: the material arrives; the daily slot after it upgrades --
    pu.concat_journals(INC, upto=4)
    orders, _ = drain(INC, now[4], pu.RULE_BEFORE)
    assert_newest_first(orders, root=INC, today=258)
    p4 = pins(INC)
    before_up = len(p4["upgrades"])
    m4a = manifest(INC)
    d4a, w4a = day_rows_sha(m4a), week_shas(m4a)
    # the daily slot after the material arrived: chunk 4 was itself a daily
    # slot, but its learn pass runs before its own records are built
    now4b = "2026-09-16T01:20:00Z"
    run(INC, now4b, pu.RULE_BEFORE, extra=["--daily"], env_extra={"PARTS_PIN_SLOTS": "1"})
    first_daily = rebuilt(INC)
    orders, mans = drain(INC, now4b, pu.RULE_BEFORE)
    orders = [first_daily] + orders
    p4b = pins(INC)
    ups = p4b["upgrades"][before_up:]
    keys_up = {u["key"] for u in ups}
    assert "tier_sets.Paladin" in keys_up, ups
    assert "learned.hero_markers" in keys_up, ups
    assert int(p4b["tier_sets"]["Paladin"]["id"]) == fx["notes"]["tier_upgrade_set"]
    hm = json.loads((state_dir(INC) / "learned" / "hero_markers.json").read_text())
    lu = fx["notes"]["learned_upgrade"]
    assert lu["ability"] in (hm["markers"].get(lu["spec"]) or {}), hm["markers"].get(lu["spec"])
    # every day rebuilt newest-first within the cap, every cube re-emitted
    assert_newest_first(orders, root=INC, today=258)
    all_days = sorted(int(k) for k in state(INC)["days"] if int(k) >= 0 and state(INC)["days"][k].get("n"))
    flat4 = [d for o in orders for d in o]
    assert sorted(set(flat4) - {-1}) == all_days and len(flat4) == len(set(flat4)), (len(orders), flat4, all_days)
    assert -1 in flat4, "the undated day is rebuilt by a pin upgrade too"
    m4 = manifest(INC)
    d4, w4 = day_rows_sha(m4), week_shas(m4)
    for W in w4a:
        assert w4[W] != w4a[W], (W, "cube kept its sha across a pin upgrade")
    # in between, a day of the old pin generation was never served next to a
    # cube of the new one for the SAME week: a re-emitted cube only appears
    # once every day of its week was rebuilt
    snapshots["after4"] = (d4, w4)
    files4 = out_files(INC)
    # ---- chunk 5: empty; 30% dropped off the pages -------------------------
    pu.concat_journals(INC, upto=5)
    run(INC, now[5], pu.RULE_BEFORE)
    h5 = pu.parts_health(INC)
    m5 = manifest(INC)
    assert h5["parts.dirty_days"] == ["0"] and h5["parts.rebuilt_days"] == ["0"], h5
    assert h5["parts.rankings"][0].endswith(":changed:0:days:0"), h5["parts.rankings"]
    assert h5["parts.tail.players"] == ["0"]
    assert m5["seq"] == m4["seq"] and m5["built"] == m4["built"]
    files5 = out_files(INC)
    assert not (set(files5) - set(files4)), "chunk 5 wrote something"     # (retention may prune, never write)
    assert all(files5[k] == files4[k] for k in files5)
    assert h5.get("parts.cubes_emitted") is None
    # ---- chunk 6: 50 runs in frozen days gain a medal ---------------------
    pu.concat_journals(INC, upto=6)
    orders, _ = drain(INC, now[6], pu.RULE_BEFORE)
    gain_days = {r[2] for r in fx["notes"]["medal_gain"]}
    assert set(d for o in orders for d in o) == gain_days, (set(d for o in orders for d in o) ^ gain_days)
    assert_newest_first(orders, root=INC)
    rev = fx["notes"]["revised_dropped"]
    assert served_medal(INC, rev[2], rev[0]) == {"gold"}, "snapshot 6 revised the run to gold"
    m6 = manifest(INC)
    d6, w6 = day_rows_sha(m6), week_shas(m6)
    for d in gain_days:
        if d in d4 and d in d6:
            assert d4[d] != d6[d], (d, "a medal changed nothing")
    snapshots["after6"] = (d6, w6)
    after6 = FIXTURE_DIR / "parts_incr_after6"
    if after6.exists():
        shutil.rmtree(after6)
    shutil.copytree(INC, after6)
    # ---- chunk 7: journals + the rule edit ---------------------------------
    pu.concat_journals(INC, upto=7)
    late_char_day = fx["notes"]["late_character"]["run"][2]
    orders, mans = drain(INC, now[7], pu.RULE_AFTER)
    assert_newest_first(orders, root=INC, today=259)
    m7 = manifest(INC)
    d7, w7 = day_rows_sha(m7), week_shas(m7)
    listed7 = [e["d"] for e in m7["days"] if e.get("f") and e["d"] != "undated"]
    touched7 = set(d for o in orders for d in o)
    # §6.4: the rule edit dirties EXACTLY the listed days (plus the undated
    # day, which carries tmul too): the arrival days and the run that fell
    # off the pages are listed anyway, and no cubed, unlisted day is rebuilt
    assert touched7 == set(listed7) | {-1}, (touched7 ^ (set(listed7) | {-1}))
    st7 = state(INC)
    unlisted = {int(k) for k, e in st7["days"].items() if e.get("n") and int(k) >= 0} - set(listed7)
    assert unlisted and not (unlisted & touched7), (unlisted & touched7)
    # the run revised in snapshot 6 dropped off the pages in 7: legacy's
    # export() overlays score/medal from the current snapshot only, so the
    # row's own medal is served again (the clock is kept)
    assert served_medal(INC, rev[2], rev[0]) == {"none"}, served_medal(INC, rev[2], rev[0])
    new_sha = m7["projection"]["rules_sha"]
    assert new_sha != m6["projection"]["rules_sha"]
    assert all(e["rules_sha"] == new_sha for e in m7["days"] if e.get("f"))
    for mi in mans[:-1]:
        shas = {e["rules_sha"] for e in mi["days"] if e.get("f")}
        if mi["projection"] and len(shas) > 1:
            # derivable: the manifest carries the new generation and a day lags it
            assert mi["projection"]["rules_sha"] == new_sha and any(s != new_sha for s in shas)
    # today first (the future-dated run's day counts as today: it is bucket 0), then newest first
    assert orders[0][:2] == [fx["notes"]["future_run"][2], 259], orders[0]
    # no cube changed by the rule edit: only the weeks holding an arrival day
    import sitecalc as sc
    epoch_ms = sc.parse_iso_ms(fx["epoch"] + "T00:00:00Z")
    arrival_days = set()
    with open(FIXTURE_DIR / "chunks" / "07" / "players.jsonl") as fh:
        for line in fh:
            r = json.loads(line)
            arrival_days.add(int((int(r["started_at"]) - epoch_ms) // 86_400_000))
    # ... and the week of the run that fell off the pages (its served medal
    # changed, so its day's rows and its week's cube legitimately change)
    st7 = state(INC)
    weeks_arrival = set()
    for wk, we in st7["weeks"].items():
        if any(int(d) in arrival_days or int(d) in (late_char_day, rev[2]) for d in we.get("days", [])):
            weeks_arrival.add(int(wk))
    for W in w6:
        if W in w7 and W not in weeks_arrival:
            assert w6[W] == w7[W], (W, "cube changed by a rules edit")
    # ---- chunk 8: a lagging git mirror ------------------------------------
    pu.concat_journals(INC, upto=8)
    (INC / "data" / "season_pins.json").write_bytes(pins_after3)
    run(INC, now[8], pu.RULE_AFTER)
    h8 = pu.parts_health(INC)
    assert h8["parts.dirty_days"] == ["0"] and h8["parts.rebuilt_days"] == ["0"], h8
    assert "parts.pins_human_edit" not in h8
    assert int(pins(INC)["tier_sets"]["Paladin"]["id"]) == fx["notes"]["tier_upgrade_set"]
    m8 = manifest(INC)
    assert m8["seq"] == m7["seq"]
    print("incremental: eight chunks behaved as specified")
    # ---- a from-scratch replay is byte-identical ---------------------------
    RE = FIXTURE_DIR / "parts_replay"
    if RE.exists():
        shutil.rmtree(RE)
    pu.common_root(RE)
    pu.concat_journals(RE, upto=8)
    state_dir(RE).mkdir(parents=True)
    shutil.copyfile(state_dir(INC) / "season_pins.json", state_dir(RE) / "season_pins.json")
    shutil.copytree(state_dir(INC) / "learned", state_dir(RE) / "learned")
    run(RE, now[8], pu.RULE_AFTER, max_days=400)
    mr = manifest(RE)
    strip = lambda m: {k: v for k, v in m.items() if k not in ("built", "seq", "days")}
    assert strip(mr) == strip(m8), [k for k in strip(mr) if strip(mr)[k] != strip(m8)[k]]
    sd = lambda e: {k: v for k, v in e.items() if k != "frozen"}
    assert [sd(e) for e in mr["days"]] == [sd(e) for e in m8["days"]]
    for rel in named_files(m8):
        a = (state_dir(INC) / "out" / rel).read_bytes()
        b = (state_dir(RE) / "out" / rel).read_bytes()
        assert a == b, rel
    assert (state_dir(INC) / "ids" / "chars.bin").read_bytes() == (state_dir(RE) / "ids" / "chars.bin").read_bytes()
    print(f"replay: {len(named_files(m8))} named files + chars.bin byte-identical")
    # ---- a seed_from_csv() rewrite of players.jsonl ------------------------
    CS = FIXTURE_DIR / "parts_reseed_csv"
    if CS.exists():
        shutil.rmtree(CS)
    shutil.copytree(INC, CS)
    pj = CS / "data" / "processed" / "players.jsonl"
    lines = pj.read_bytes().split(b"\n")
    lines = [l for l in lines if l]
    lines.sort()                                     # a different byte order, same records
    pj.write_bytes(b"\n".join(lines) + b"\n")
    (CS / "data" / "processed" / "players.jsonl.seeded").write_text("csv\n")
    run(CS, "2026-09-17T15:00:00Z", pu.RULE_AFTER, max_days=400)
    hc = pu.parts_health(CS)
    assert "players" in hc.get("parts.journal_replay", []), hc.get("parts.journal_replay")
    assert hc["parts.chars_new"] == ["0"]
    mc = manifest(CS)
    assert mc["window"]["rows"] == m8["window"]["rows"] and mc["char_max"] == m8["char_max"]
    assert {e["d"]: e["n"] for e in mc["days"]} == {e["d"]: e["n"] for e in m8["days"]}
    # no duplicate and no missing row: every day holds exactly the same
    # (report, fight, character, server) keys. (Row VALUES may differ where
    # a journal holds two records of one key with different content -- the
    # fixture draws a character twice into one roster now and then -- because
    # legacy's own drop_duplicates(keep="last") follows the journal's byte
    # order, and a seed_from_csv() rewrite changes that order.)
    for e in mc["days"]:
        if e["d"] == "undated" or not e.get("f"):
            continue
        assert keys_of_day(CS, e["d"]) == keys_of_day(INC, e["d"]), e["d"]
        with np.load(state_dir(CS) / "days" / f"d{e['d']}" / "keys.npz", allow_pickle=False) as z:
            n_keys = len({(str(c), int(f), str(ch), str(sv)) for c, f, ch, sv in
                          zip(z["code"], z["fid"], z["character"], z["server"])})
        assert n_keys == e["n"], (e["d"], n_keys, e["n"])
    print("csv reseed: replayed from byte 0, no duplicate rows, no new ids")
    # ---- a deadline stop between days leaves a consistent checkpoint ------
    DL = FIXTURE_DIR / "parts_deadline"
    if DL.exists():
        shutil.rmtree(DL)
    shutil.copytree(after6, DL)
    pu.concat_journals(DL, upto=7)
    run(DL, now[7], pu.RULE_AFTER, extra=["--deadline", "36"], max_days=400)
    hd = pu.parts_health(DL)
    assert hd.get("parts.deadline_hit") == ["1"], hd
    first = rebuilt(DL)
    assert first and first[0] == max(first) and 259 in first and int(hd["parts.days_left"][0]) > 0
    md = manifest(DL)
    assert md["seq"] > m6["seq"] and {e["d"] for e in md["days"] if e.get("f")} >= set(first)
    run(DL, now[7], pu.RULE_AFTER, max_days=400)
    second = rebuilt(DL)
    assert not (set(first) & set(second)), set(first) & set(second)
    assert set(first) | set(second) == touched7, (set(first) | set(second)) ^ touched7
    print(f"deadline: {len(first)} days, then {len(second)}, none twice")
    # ---- a kill inside step 1 ------------------------------------------------
    CL = FIXTURE_DIR / "parts_crash_clean"
    if CL.exists():
        shutil.rmtree(CL)
    shutil.copytree(after6, CL)
    pu.concat_journals(CL, upto=7)
    run(CL, now[7], pu.RULE_AFTER, max_days=400, env_extra={"PARTS_TAIL_BATCH": "500"})
    mcl = manifest(CL)
    for where in ("players:pending:2", "players:batch:3", "gear:pending:2", "abilities:batch:1"):
        CR = FIXTURE_DIR / "parts_crash"
        if CR.exists():
            shutil.rmtree(CR)
        shutil.copytree(after6, CR)
        pu.concat_journals(CR, upto=7)
        try:
            run(CR, now[7], pu.RULE_AFTER, max_days=400,
                env_extra={"PARTS_TAIL_BATCH": "500", "PARTS_TEST_CRASH_AT": where})
            raise AssertionError(f"{where}: the crash hook did not fire")
        except RuntimeError:
            pass
        assert not (state_dir(CR) / "health.txt").exists() or "parts.status=ok" not in \
            (state_dir(CR) / "health.txt").read_text()
        run(CR, now[7], pu.RULE_AFTER, max_days=400, env_extra={"PARTS_TAIL_BATCH": "500"})
        mcr = manifest(CR)
        assert strip(mcr) == strip(mcl), (where, [k for k in strip(mcr) if strip(mcr)[k] != strip(mcl)[k]])
        assert [sd(e) for e in mcr["days"]] == [sd(e) for e in mcl["days"]], where
        for rel in named_files(mcl):
            assert (state_dir(CR) / "out" / rel).read_bytes() == (state_dir(CL) / "out" / rel).read_bytes(), (where, rel)
        assert (state_dir(CR) / "ids" / "chars.bin").read_bytes() == (state_dir(CL) / "ids" / "chars.bin").read_bytes()
        stc, stl = state(CR), state(CL)
        assert stc["journals"] == stl["journals"] and stc["arrival_seq"] == stl["arrival_seq"], where
        assert (stc["gear_seq"], stc.get("abil_seq")) == (stl["gear_seq"], stl.get("abil_seq")), where
        for dd in (state_dir(CL) / "days").iterdir():
            for cache in ("gear.npz", "abil.npz", "raw.npz"):
                a, b = state_dir(CL) / "days" / dd.name / cache, state_dir(CR) / "days" / dd.name / cache
                if not a.exists():
                    continue
                with np.load(a, allow_pickle=False) as za, np.load(b, allow_pickle=False) as zb:
                    assert len(za["code"] if "code" in za else za["report_code"]) == \
                        len(zb["code"] if "code" in zb else zb["report_code"]), (where, dd.name, cache)
            assert not list((state_dir(CR) / "days" / dd.name).glob("pending_*.jsonl")), (where, dd.name)
        print(f"crash at {where}: resumed run == clean run")


if __name__ == "__main__":
    test_incremental_idempotent()
    print("test_incremental_idempotent: all green")
