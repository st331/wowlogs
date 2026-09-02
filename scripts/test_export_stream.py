"""Streaming export() (partitioned_payload.md §7.4, PR-1 stage A).

export() used to build its frame from list(_iter_journal(PLAYERS_FILE)) --
every player row as a dict. It now reads the journal in chunks and builds
the ONE frame chunk by chunk (load_players_frame). Pinned here against the
literal previous expression as the oracle:

  * frame equality -- same columns in the same order, same dtypes, same
    to_csv bytes -- on a journal whose shape exercises every place chunked
    inference can diverge from whole-list inference: a column absent from
    the first chunk (added to the schema later), a column absent from a
    later chunk, a chunk in which a string column is entirely null, a
    chunk in which a numeric column is entirely null, blank lines and a
    torn trailing line;
  * keep="last" across chunk boundaries -- duplicate identities whose
    copies sit in different chunks (and one pair straddling a boundary
    exactly) resolve to the LAST copy, identical to the whole-frame dedup;
  * export() end to end: the CSV written with 500-row chunks equals the CSV
    written with the whole journal in one chunk, byte for byte after
    decompression; export.wall_s / export.rss_mb / export.rows land in
    fetch_health.txt, and a later write_outputs() call keeps them.
"""
import gzip
import json
import pathlib
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_data as fd                        # noqa: E402


class _Hero:
    def resolve(self, tree):
        return "Hero"


def run_rows(i: int, code: str, fid: int, *, server="Srv", n=5):
    fight = {"code": code, "fid": fid, "dungeon": "Halls", "key_level": 12 + i % 6,
             "region": "US" if i % 2 else "EU", "score": 400.0 + i,
             "medal": "gold" if i % 3 else "none", "affixes": [9, 10],
             "start_time": 1_787_000_000_000 + i * 60_000, "rank_duration_ms": 1_500_000}
    ps = [{"id": k + 1, "name": f"P{i}_{k}", "server": server, "type": "Mage",
           "specs": ["Arcane"], "icon": "Mage-Arcane", "maxItemLevel": 700 + k,
           "combatantInfo": {"gear": [{"id": 1, "itemLevel": 700, "setID": "1729"}]}}
          for k in range(n)]
    table = {"data": {"totalTime": 1_500_000, "playerDetails": {"dps": ps},
                      "damageDone": [{"id": p["id"], "total": 1_000_000 * (k + 1) + i}
                                     for k, p in enumerate(ps)],
                      "deathEvents": [{"id": 1}] if i % 4 == 0 else []}}
    rows, _ = fd.parse_summary(fight, table, _Hero())
    return rows


CHUNK = 500
journal: list[dict] = []
for i in range(520):                     # 2,600 rows -> 6 chunks of 500
    journal.extend(run_rows(i, f"C{i:05d}xxxxxxxxxx", 1 + i % 2))
# shape hazards, by row position (chunk k = rows [500k, 500k+500)):
for r in journal[:500]:
    del r["score"]                       # column absent from chunk 0 entirely
for r in journal[1000:1500]:
    r["server"] = None                   # a string column all-null in chunk 2
    r["item_level"] = None               # a numeric column all-null in chunk 2
for r in journal[2000:2500]:
    del r["set_counts"]                  # column absent from a later chunk
# duplicate identities across chunks -- the LATER copy carries new values
def dup(src_idx: int, dst_idx: int, tag: int):
    c = dict(journal[src_idx])
    c["dps"] = 99999.0 + tag
    c["set_counts"] = f"1729:{tag % 5}"
    c["item_level"] = 750 + tag
    journal[dst_idx] = c
dup(10, 1700, 1)         # chunk 0 -> chunk 3
dup(10, 2550, 2)         # ... and a third copy in chunk 5 (triple)
dup(499, 500, 3)         # straddling the 500 boundary exactly
dup(1200, 2599, 4)       # from the all-null chunk to the last row
dup(300, 301, 5)         # adjacent within one chunk (control)
tail = json.dumps(journal[7])[:40]      # torn trailing line


def write(path: pathlib.Path):
    with path.open("w", encoding="utf-8") as fh:
        for n, r in enumerate(journal):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if n == 1234:
                fh.write("\n")           # blank line mid-journal
        fh.write(tail)                   # no newline: torn


with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    jp = tp / "players.jsonl"
    write(jp)

    # ---- 1. the frame: literal previous expression vs the streamed load
    ref = pd.DataFrame(list(fd._iter_journal(jp)))
    new = fd.load_players_frame(jp, chunk_rows=CHUNK)
    assert list(new.columns) == list(ref.columns), (list(new.columns), list(ref.columns))
    assert new.dtypes.astype(str).to_dict() == ref.dtypes.astype(str).to_dict(), \
        (new.dtypes.astype(str).to_dict(), ref.dtypes.astype(str).to_dict())
    assert len(new) == len(ref) == len(journal)
    assert new.to_csv(index=False) == ref.to_csv(index=False), "frame CSV differs"
    assert new.index.equals(ref.index)
    # dtype hazards really were exercised
    assert "score" not in pd.DataFrame(journal[:500]).columns
    assert pd.DataFrame(journal[1000:1500])["server"].dtype == object       # all-null chunk
    assert str(ref["server"].dtype) == "str" and str(ref["item_level"].dtype) == "float64"
    print(f"frame     : {len(new):,} rows in {-(-len(journal) // CHUNK)} chunks == whole-list "
          f"frame (columns, dtypes, CSV); all-null chunk cast back to "
          f"{new['server'].dtype}/{new['item_level'].dtype}")

    # ---- 2. keep="last" across chunk boundaries
    sub = ["report_code", "fight_id", "character", "server"]
    d_ref = ref.drop_duplicates(subset=sub, keep="last")
    d_new = new.drop_duplicates(subset=sub, keep="last")
    assert d_new.to_csv(index=False) == d_ref.to_csv(index=False)
    assert len(d_new) == len(journal) - 5, len(d_new)
    for src, tag in ((10, 2), (499, 3), (1200, 4), (300, 5)):
        r = journal[src]
        hit = d_new[(d_new.report_code == r["report_code"]) & (d_new.fight_id == r["fight_id"])
                    & (d_new.character == r["character"])]
        assert len(hit) == 1 and float(hit.iloc[0]["dps"]) == 99999.0 + tag, (src, tag, hit)
    print("dedup     : 5 duplicate identities (cross-chunk, boundary-straddling, "
          "a triple, from the all-null chunk) -> the LAST copy survives, as whole-frame")

    # ---- 3. export() end to end, one chunk vs 500-row chunks
    fd.ROOT = tp
    fd.PROCESSED = tp / "processed"
    fd.PROCESSED.mkdir()
    (tp / "data").mkdir()
    fd.PLAYERS_FILE = jp
    fd.RANKINGS_FILE = tp / "absent_rankings.jsonl"
    fd.GEAR_FILE = tp / "absent_gear.jsonl"
    fd.GEAR_CSV = tp / "gear.jsonl.gz"
    outs = {}
    for label, chunk in (("whole", 10 ** 9), ("chunked", CHUNK)):
        fd.CSV_FILE = tp / f"{label}.csv.gz"
        fd.EXPORT_CHUNK_ROWS = chunk
        fd._OUTPUTS.clear()
        fd.export()
        with gzip.open(fd.CSV_FILE, "rb") as fh:
            outs[label] = fh.read()
        health = (fd.PROCESSED / "fetch_health.txt").read_text()
        assert "export.wall_s=" in health and "export.rss_mb=" in health \
            and f"export.rows={len(journal) - 5}" in health, health
    assert outs["whole"] == outs["chunked"], "export() CSV differs between whole and chunked"
    csv = pd.read_csv(fd.CSV_FILE)
    assert len(csv) == len(journal) - 5 and "keystone_s" in csv.columns
    # write_outputs accumulates: the backlog line joins the export lines
    fd.write_outputs(backlog=7)
    health = (fd.PROCESSED / "fetch_health.txt").read_text()
    assert "export.rss_mb=" in health and "backlog=7" in health, health
    assert health.startswith("fetched_at=")
    # tripwires: forced low, they mark the health file and warn
    fd.EXPORT_WALL_TRIPWIRE_S, fd.EXPORT_RSS_TRIPWIRE_MB = -1, -1
    fd._OUTPUTS.clear()
    fd.export()
    health = (fd.PROCESSED / "fetch_health.txt").read_text()
    assert "export.wall_tripped=1" in health and "export.rss_tripped=1" in health, health
    print(f"export    : whole-journal CSV == 500-row-chunk CSV ({len(outs['whole']):,} bytes); "
          f"health lines present; tripwires fire when crossed")

    # ---- 4. empty journal
    fd.EXPORT_WALL_TRIPWIRE_S, fd.EXPORT_RSS_TRIPWIRE_MB = 300, 6144
    (tp / "empty.jsonl").write_text("")
    assert fd.load_players_frame(tp / "empty.jsonl", chunk_rows=CHUNK) is None
    fd.PLAYERS_FILE = tp / "empty.jsonl"
    fd.CSV_FILE = tp / "never.csv.gz"
    fd.export()
    assert not fd.CSV_FILE.exists()
    print("absence   : empty journal -> no frame, no CSV, as before")

print("PASS")
