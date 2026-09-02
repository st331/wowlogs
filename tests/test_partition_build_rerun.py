#!/usr/bin/env python3
"""tests/test_partition_build_rerun.py (partitioned_payload.md §6.3)

Idempotence and determinism of the stage-B builder on the §9 fixture:
a rerun over unchanged journals writes nothing new (seq does not advance,
zero dirty days, the manifest is byte-identical); and a two-step run
(chunks 1-2, then 3-8, rankings as the per-run snapshot) reproduces the
one-shot run's day files, shard blocks, vocab, charscore, specstats,
window block, week counts and character registry byte for byte -- with
the cross-day duplicate collapse recorded on the neighbour day.
"""
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import parts_util as pu                                          # noqa: E402
from fixture_util import FIXTURE_DIR, fixture                    # noqa: E402


def _manifest(root):
    return json.loads((root / "site" / "d" / "s2" / "manifest.json").read_text())


def test_rerun_is_a_noop():
    fx = fixture()
    root = pu.parts_root()
    before = (root / "site" / "d" / "s2" / "manifest.json").read_bytes()
    pu.run_parts(root, fx["now"], pins=FIXTURE_DIR / "pins.json")
    h = pu.parts_health(root)
    assert (root / "site" / "d" / "s2" / "manifest.json").read_bytes() == before
    assert h["parts.dirty_days"] == ["0"] and h["parts.rebuilt_days"] == ["0"]
    assert h["parts.rankings"] == ["unchanged"] and h["parts.manifest"] == ["unchanged"]
    assert h["parts.chars_new"] == ["0"] and h["parts.status"] == ["ok"]
    print("rerun: no-op, seq", _manifest(root)["seq"])


def test_two_step_matches_one_shot():
    fx = fixture()
    one = _manifest(pu.parts_root())
    inc = FIXTURE_DIR / "parts_inc"
    if inc.exists():
        shutil.rmtree(inc)
    pu.common_root(inc)
    pu.concat_journals(inc, upto=2)
    pu.run_parts(inc, fx["chunks"][1]["now"], pins=FIXTURE_DIR / "pins.json")
    m1 = _manifest(inc)
    assert m1["seq"] == 1 and m1["window"]["rows"] < one["window"]["rows"]
    pu.concat_journals(inc, upto=8)
    pu.run_parts(inc, fx["now"], pins=FIXTURE_DIR / "pins.json")
    h = pu.parts_health(inc)
    two = _manifest(inc)
    assert two["seq"] == 2
    # the midnight duplicate's twin sits in a day built in step 1: the copy
    # arriving in step 2 dirtied that neighbour day and the loser is gone
    twin_day = fx["notes"]["dup_pair"]["twin"][2]
    assert any(l.startswith(f"{twin_day}:") for l in h.get("parts.collapse.neighbour", [])), h.get("parts.collapse.neighbour")
    assert h["parts.invalidated_days"][0].startswith("1/")
    for k in ("weeks", "window", "spec_vocab", "specstats", "char_max", "emb", "pars", "flags", "tuning"):
        assert one[k] == two[k], k
    # `frozen` follows the arrival history (72 h quiescence, §6.2-4): the
    # two-step run saw its step-1 days go quiet, the one-shot run did not.
    # Everything else about a day is a pure function of its inputs.
    strip = lambda e: {k: v for k, v in e.items() if k != "frozen"}
    assert [strip(e) for e in one["days"]] == [strip(e) for e in two["days"]]
    aged = {e["d"] for e in one["days"] if e["frozen"]}
    assert aged <= {e["d"] for e in two["days"] if e["frozen"]}
    assert one["charscore"]["f"] == two["charscore"]["f"]
    reg = "data/processed/parts/s2/ids/chars.bin"
    assert (pu.parts_root() / reg).read_bytes() == (inc / reg).read_bytes()
    print(f"two-step == one-shot: {len(two['days'])} day entries, seq {two['seq']}")


if __name__ == "__main__":
    test_rerun_is_a_noop()
    test_two_step_matches_one_shot()
    print("test_partition_build_rerun: all green")
