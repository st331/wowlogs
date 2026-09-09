"""test_names_scan (fleet/checklist.md 191).

fetch_names.scan_journal() walked the whole 4.4 GB gear journal every run
(~80 s of every refresh) to learn item and enchant ids the caches have never
seen. The journal is append-only, so the scan is now incremental: the ids
already seen and the byte offset they cover live in data/processed/
names_scan.json, and only the appended lines are parsed. Pinned here:

  * a cold scan equals the whole-walk result and leaves a checkpoint at the
    file size;
  * an incremental scan parses exactly the appended lines and the result
    still equals a whole walk;
  * a torn trailing line counts for this run's result, NOT for the
    checkpoint, and is counted once when the writer completes it;
  * a rewritten journal (shorter, head changed, body changed with the head
    kept) triggers one whole rebuild -- no stale ids survive;
  * a NaN literal (json.dumps writes them; orjson refuses them) costs no
    record: the line falls back to json and its ids are collected.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_names as fn  # noqa: E402


def rec(*items, ench=None):
    gear = [{"id": i, "ilvl": 720, "slot": n} for n, i in enumerate(items)]
    if ench is not None:
        gear[0]["ench"] = ench
    return json.dumps({"report_code": "AbCd", "fight_id": 1, "character": "x",
                       "server": "y", "gear": gear, "talents": {}})


def whole(path):
    items, enchs = set(), set()
    with open(path, "rb") as fh:
        for raw in fh:
            fn._scan_line(raw, items, enchs)
    return items, enchs


def state():
    return json.loads(fn.SCAN_STATE.read_text())


fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    fn.GEAR_FILE = tp / "gear.jsonl"
    fn.GEAR_CSV = tp / "gear.jsonl.gz"
    fn.SCAN_STATE = tp / "state" / "names_scan.json"

    # ---- cold scan
    fn.GEAR_FILE.write_text(rec(1, 2, ench=7) + "\n" + rec(2, 3) + "\n")
    items, enchs = fn.scan_journal()
    check((items, enchs) == ({1, 2, 3}, {7}), f"cold scan ids {sorted(items)} / {sorted(enchs)}")
    st = state()
    check(st["offset"] == fn.GEAR_FILE.stat().st_size and st["lines"] == 2,
          f"cold checkpoint at the file size, 2 lines ({st['offset']}, {st['lines']})")

    # ---- incremental append + a torn trailing line
    with fn.GEAR_FILE.open("a") as fh:
        fh.write(rec(4, ench=8) + "\n")
        fh.write(rec(5)[:-3])                     # torn: no closing brace, no newline
    size_torn = fn.GEAR_FILE.stat().st_size
    items, enchs = fn.scan_journal()
    check((items, enchs) == ({1, 2, 3, 4}, {7, 8}), f"incremental ids {sorted(items)} / {sorted(enchs)}")
    st = state()
    check(st["lines"] == 3 and st["offset"] < size_torn,
          f"checkpoint stops before the torn line (lines {st['lines']}, offset {st['offset']} < {size_torn})")
    off_before = st["offset"]

    # ---- the writer completes the torn line, then appends another
    with fn.GEAR_FILE.open("a") as fh:
        fh.write(rec(5)[len(rec(5)) - 3:] + "\n" + rec(6) + "\n")
    items, enchs = fn.scan_journal()
    check(items == {1, 2, 3, 4, 5, 6} and items == whole(fn.GEAR_FILE)[0],
          f"completed torn line counted once, equals a whole walk {sorted(items)}")
    st = state()
    check(st["lines"] == 5 and st["offset"] == fn.GEAR_FILE.stat().st_size,
          f"checkpoint advanced from {off_before} to the file size, 5 lines ({st['lines']})")

    # ---- a torn line that is parseable JSON (newline missing only)
    with fn.GEAR_FILE.open("a") as fh:
        fh.write(rec(60))
    items, _ = fn.scan_journal()
    check(60 in items, "parseable tail without newline counts for this run")
    check(state()["lines"] == 5, "...but not for the checkpoint")
    with fn.GEAR_FILE.open("a") as fh:
        fh.write("\n")
    items, _ = fn.scan_journal()
    check(60 in items and state()["lines"] == 6, "and once for the checkpoint when the newline lands")

    # ---- NaN literal: json.dumps writes it, orjson refuses it
    with fn.GEAR_FILE.open("a") as fh:
        fh.write(json.dumps({"gear": [{"id": 77, "ench": float("nan")}]}) + "\n")
    items, enchs = fn.scan_journal()
    check(77 in items, f"NaN line still yields its item id (orjson={'orjson' in sys.modules})")

    # ---- rewritten journals -> rebuild, no stale ids
    fn.GEAR_FILE.write_text(rec(9) + "\n")                     # shorter
    items, enchs = fn.scan_journal()
    check((items, enchs) == ({9}, set()), f"shorter journal -> rebuild {sorted(items)}")
    big = "".join(rec(100 + i) + "\n" for i in range(3000))    # > SCAN_HEAD bytes
    fn.GEAR_FILE.write_text(big)
    items, _ = fn.scan_journal()
    check(len(items) == 3000 and items == whole(fn.GEAR_FILE)[0], "big journal cold")
    # same size, head changed
    fn.GEAR_FILE.write_text(big.replace('"id": 100,', '"id": 900,', 1))
    items, _ = fn.scan_journal()
    check(900 in items and 100 not in items and items == whole(fn.GEAR_FILE)[0], "head changed -> rebuild")
    # same size, head kept, body rewritten near the end
    body = fn.GEAR_FILE.read_text().replace('"id": 3099,', '"id": 3999,', 1)
    fn.GEAR_FILE.write_text(body)
    items, _ = fn.scan_journal()
    check(3999 in items and 3099 not in items and items == whole(fn.GEAR_FILE)[0], "body changed with the head kept -> rebuild")
    # corrupt state file -> rebuild, not a crash
    fn.SCAN_STATE.write_text("{not json")
    items, _ = fn.scan_journal()
    check(items == whole(fn.GEAR_FILE)[0] and 3999 in items, "corrupt state -> rebuild")

    # ---- the committed .gz export (cold start) is walked whole
    fn.GEAR_FILE.unlink()
    import gzip
    with gzip.open(fn.GEAR_CSV, "wt") as fh:
        fh.write(rec(11, ench=12) + "\n")
    items, enchs = fn.scan_journal()
    check((items, enchs) == ({11}, {12}), "gz export walked whole")

print("FAILED" if fails else "PASS", f"({fails} failures)")
sys.exit(1 if fails else 0)
