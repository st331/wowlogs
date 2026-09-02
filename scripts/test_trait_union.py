"""test_trait_union (partitioned_payload.md §7.4, §9.1).

The sample prefilter in gear_journal_pass() is exact for sets/stats/meta and
NOT for the talent material: talents_doc draws a spec's tree from the union
of entries its players EVER allocated and its hero panes from every subtree
seen, journal-wide. Stage A computed that union over the sampled records
only, and on a journal where an entry, a hero subtree or a whole spec occurs
only in unsampled reports the talents doc lost them (40/40 specs a node,
Retribution the Lightsmith pane). TraitUnion keeps the union COMPLETE and
INCREMENTAL: persisted under data/processed with a checkpoint, only the bytes
appended since are parsed, a checkpoint that no longer describes the file
triggers one whole-journal rebuild.

Pinned here, on a journal written by the REAL collector (parse_summary),
built so that an entry, a hero subtree (Lightsmith), a class node, a spec
(Priest|Shadow) and the modal blob of a string-identified build exist ONLY
in unsampled reports:

  * the stage-A path (sampled material) really does lose them -- the test
    would have failed on HEAD;
  * talents.json.gz AND builds.json.gz are byte-identical three ways:
    the HEAD-style whole-journal walk, the new path from a cold checkpoint,
    the new path after two incremental appends; the usage dict the
    consumers read equals the whole walk's exactly (counts included);
  * an incremental run parses exactly the appended lines and nothing else;
    a run with nothing appended parses nothing;
  * a journal shorter than the offset, a rewritten head, and a rewritten
    body under an unchanged head each trigger a whole rebuild that says so;
  * a torn trailing line (half a record; a whole record missing its
    newline) is merged for this run's consumers when it parses, never
    committed to the checkpoint, and read again -- correctly -- after
    fetch_data's _repair_tail closes it;
  * the committed .gz export as the source: rebuilt once, then nothing
    to do until it is rewritten; an absent journal yields nothing.
"""
import contextlib
import gzip
import io
import json
import pathlib
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import parse_summary          # noqa: E402
import build_site_data as bsd                  # noqa: E402


class _Hero:
    def resolve(self, tree):
        return "Hero"


# --- geometry: three trees, Paladin with TWO hero subtrees --------------------
GEO = {
    "specs": {"70": 900, "62": 901, "258": 902},
    "subtrees": {"48": "Templar", "49": "Lightsmith"},
    "trees": {
        "900": {"nodes": {"1001": [3000, 1200, 0, 0], "1002": [3600, 1200, 0, 0],
                          "1003": [4200, 1800, 0, 0],
                          "2001": [9900, 1200, 0, 0], "2002": [10500, 1800, 2, 0],
                          "2003": [11100, 1200, 0, 0],
                          "3001": [7800, 9000, 0, 48], "3002": [7800, 9600, 0, 49]},
                "edges": [[1001, 1002], [1002, 1003], [1001, 2001], [2001, 2002],
                          [2002, 2003], [3001, 1001], [3002, 1001]]},
        "901": {"nodes": {"1101": [3000, 1200, 0, 0], "2101": [9900, 1200, 0, 0],
                          "2102": [10500, 1200, 0, 0], "2103": [11100, 1200, 0, 0]},
                "edges": [[1101, 2101], [2101, 2102]]},
        "902": {"nodes": {"1201": [3000, 1200, 0, 0], "2201": [9900, 1200, 0, 0]},
                "edges": [[1201, 2201]]}},
    "entries": {"50001": [1001, 1, 111111, None], "50002": [1002, 2, 222222, None],
                "50003": [1003, 1, 233333, None],
                "60001": [2001, 1, 333333, None], "60002": [2002, 1, 444444, None],
                "60003": [2002, 1, 555555, None], "60004": [2003, 1, 566666, None],
                "70001": [3001, 1, 666666, None], "70002": [3002, 1, 677777, None],
                "51101": [1101, 1, 777777, None], "52101": [2101, 1, 888888, None],
                "52102": [2102, 1, 899999, None], "52103": [2103, 1, 890000, None],
                "51201": [1201, 1, 900001, None], "52201": [2201, 1, 900002, None]},
}
SPELLS = {"111111": {"n": "Spell One", "ic": "a"}, "333333": {"n": "Three", "ic": "c"},
          "666666": {"n": "Templar Spell", "ic": "e"}, "677777": {"n": "Lightsmith Spell", "ic": "l"},
          "777777": {"n": "M One", "ic": "f"}, "900001": {"n": "P One", "ic": "p"}}

# trees per spec: [S] = seen in SAMPLED reports, [U] = only in UNSAMPLED ones
RET_S = [
    [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 2}, {"id": 60001, "rank": 1}, {"id": 70001, "rank": 1}],
    [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 1}, {"id": 60002, "rank": 1}, {"id": 70001, "rank": 1}],
]
RET_U = [
    # 50003 (a class node), 60004 (a spec node), 70002 (the Lightsmith subtree)
    # and the choice entry 60003 exist only here
    [{"id": 50001, "rank": 1}, {"id": 50003, "rank": 1}, {"id": 60001, "rank": 1}, {"id": 70002, "rank": 1}],
    [{"id": 50001, "rank": 1}, {"id": 60003, "rank": 1}, {"id": 60004, "rank": 1}, {"id": 70002, "rank": 1}],
    [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 2}, {"id": 60001, "rank": 1}, {"id": 70001, "rank": 1}],
]
MAGE_A = [{"id": 51101, "rank": 1}, {"id": 52101, "rank": 1}]
MAGE_B = [{"id": 51101, "rank": 1}, {"id": 52102, "rank": 1}]
SHADOW = [[{"id": 51201, "rank": 1}, {"id": 52201, "rank": 1}]]
TIER, OLD = "1729", "1600"
BASE_MS = 1_787_000_000_000
N_RUNS = 60


def is_sampled(i: int) -> bool:
    return i % 3 == 0                     # one report in three


def gear_for(i: int):
    g = []
    for s in range(16):
        it = {"id": 5000 + s + (i % 7), "itemLevel": 700 + (i % 30)}
        if s in (0, 2, 4, 6, 9) and i % 5:
            it["setID"] = TIER if (i % 5) != 4 else OLD
        if s == 15:
            it["permanentEnchant"] = 7008
        g.append(it)
    g[3] = {"id": 0}
    return g


def player(i: int, k: int, cls, spec, spec_id):
    ci = {}
    variant = (i + k) % 9
    if variant != 0:                       # 0: gear absent (talents only)
        ci["gear"] = gear_for(i + k)
    samp = is_sampled(i)
    if spec == "Retribution":
        if variant not in (1,):            # 1: no tree
            pool = RET_S if samp else RET_U
            # (i // 2 + k): (i + k) % len(pool) would alias with the spec
            # rotation below and pick the same tree every time
            ci["talentTree"] = pool[(i // 2 + k) % len(pool)]
        # modal specID: 70 in the sample, 1070 journal-wide
        if variant in (3, 4, 5):
            ci["specID"] = 70 if samp else 1070
    elif spec == "Arcane":
        # ONE string-identified build whose modal blob differs between the
        # sample (A) and the whole journal (B)
        ci["talentTree"] = (MAGE_A if (i % 9) else MAGE_B) if samp else MAGE_B
        ci["talentImportString"] = "IMPORT_MAGE"
        if variant == 4:
            ci["specID"] = 62
    else:                                  # Shadow: unsampled reports only
        ci["talentTree"] = SHADOW[0]
        ci["specID"] = 258
    if variant not in (5, 6):
        ci["stats"] = {"Crit": {"min": 1000 + i}, "Haste": {"min": 2000},
                       "Mastery": {"min": 3000}, "Versatility": {"min": 4000 + k},
                       "Intellect": {"min": 90000}}
    return {"id": k + 1, "name": f"{cls[:3]}{i}_{k}", "server": "Srv",
            "type": cls, "specs": [spec], "icon": f"{cls}-{spec}",
            "maxItemLevel": 720, "combatantInfo": ci}


SPECS_S = [("Paladin", "Retribution", 70), ("Mage", "Arcane", 62)]
SPECS_U = SPECS_S + [("Priest", "Shadow", 258)]


def make_run(i: int, code: str, fid: int):
    fight = {"code": code, "fid": fid, "dungeon": "Halls", "key_level": 12 + i % 6,
             "region": "US" if i % 2 else "EU", "score": 400.0,
             "medal": "gold" if i % 3 else "none", "affixes": [9],
             "start_time": BASE_MS + i * 3_600_000, "rank_duration_ms": 1_500_000}
    pool = SPECS_S if is_sampled(i) else SPECS_U
    ps = [player(i, k, *pool[(i + k) % len(pool)]) for k in range(5)]
    table = {"data": {"totalTime": 1_500_000,
                      "playerDetails": {"dps": ps},
                      "damageDone": [{"id": p["id"], "total": 9_000_000 + 1000 * i + p["id"]}
                                     for p in ps],
                      "deathEvents": []}}
    return parse_summary(fight, table, _Hero())


def code_of(i: int) -> str:
    return f"R{i:04d}abcdefghijk"[:16].ljust(16, "x")


rows: list[dict] = []
parts: list[list[str]] = [[], [], []]     # the journal in three appends
for i in range(N_RUNS):
    rw, rc = make_run(i, code_of(i), 1 + (i % 3))
    rows.extend(rw)
    parts[i // (N_RUNS // 3)].extend(json.dumps(r, ensure_ascii=False) for r in rc)
    if i == 7:
        parts[0].append("")                # a blank line mid-journal
df_all = pd.DataFrame(rows)
sampled = {code_of(i) for i in range(N_RUNS) if is_sampled(i)}
df_s = df_all[df_all["report_code"].isin(sampled)].reset_index(drop=True)
codes = set(df_s["report_code"].astype(str).unique())
assert not (df_s["spec"] == "Shadow").any(), "Shadow must be absent from the sample"
assert (df_all["spec"] == "Shadow").any()


def body(part) -> bytes:
    return ("\n".join(part) + "\n").encode("utf-8")


def point_caches(tp: pathlib.Path):
    for attr in ("NAMES_ITEMS", "NAMES_ENCHANTS", "CRAFTED_IDS", "EMB_IDENTITY",
                 "EMB_ITEMS", "EMB_OVERRIDES", "EMB_MARKERS", "NAMES_ICONS"):
        setattr(bsd, attr, tp / f"absent_{attr}.json")
    bsd.TRAIT_GEOMETRY = tp / "trait_geometry.json"
    bsd.NAMES_SPELLS = tp / "names_spells.json"
    bsd.TRAIT_GEOMETRY.write_text(json.dumps(GEO))
    bsd.NAMES_SPELLS.write_text(json.dumps(SPELLS))
    bsd.TRAIT_UNION = tp / "state" / "trait_union.json.gz"


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def gz_bytes(doc: str) -> bytes:
    """talents.json.gz / builds.json.gz as bytes with the gzip header's
    mtime pinned -- the only build stamp these two documents carry."""
    return gzip.compress(doc.encode("utf-8"), 9, mtime=0)


def consumers(df, gj, traits):
    """builds + talents docs as build() emits them. traits=None is the
    HEAD-style whole-journal walk inside builds_sidecar / talents_doc."""
    out = {}
    out["builds"] = quiet(bsd.builds_sidecar, df, gj.meta, "t", traits=traits)
    usage = bsd.builds_sidecar.usage
    out["talents"] = quiet(bsd.talents_doc, "t", usage=usage, traits=traits)
    out["usage"] = usage
    assert out["builds"] and out["talents"]
    assert '"built"' not in out["builds"] and '"built"' not in out["talents"]
    out["builds_gz"], out["talents_gz"] = gz_bytes(out["builds"]), gz_bytes(out["talents"])
    return out


def panes(talents_doc: str, sk="Paladin|Retribution") -> dict:
    d = json.loads(talents_doc)
    t = d["trees"][sk]
    cls = d["classes"][t["classRef"]] if "classRef" in t else t["class"]
    return {"class": sorted(n["id"] for n in cls["nodes"]),
            "spec": sorted(n["id"] for n in t["spec"]["nodes"]),
            "hero": sorted(t["hero"])}


def update():
    return quiet(bsd.trait_union_update, bsd.GEAR_JOURNAL
                 if bsd.GEAR_JOURNAL.exists() else bsd.GEAR_EXPORT)


def oracle(gj):
    """The HEAD-style answer: the original whole-journal walk, for the
    wanted set builds_sidecar derives from the sampled records."""
    quiet(bsd.builds_sidecar, df_s, gj.meta, "t", traits=None)
    return bsd.builds_sidecar.usage


with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    point_caches(tp)
    j = tp / "gear.jsonl"
    bsd.GEAR_JOURNAL, bsd.GEAR_EXPORT = j, tp / "absent.jsonl.gz"

    # ------------------------------------------------------------------ 1/2
    j.write_bytes(body(parts[0]) + body(parts[1]) + body(parts[2]))
    S = quiet(bsd.gear_journal_pass, codes)
    assert S.prefiltered > 0 and S.parsed < S.lines
    head = consumers(df_s, S, None)                 # WAY 1: whole walk (HEAD)
    stage_a = consumers(df_s, S, S.traits)          # stage A: sampled material
    p_head, p_a = panes(head["talents"]), panes(stage_a["talents"])
    assert p_head["hero"] == ["Lightsmith", "Templar"] and p_a["hero"] == ["Templar"], (p_head, p_a)
    assert 1003 in p_head["class"] and 1003 not in p_a["class"]
    assert 2003 in p_head["spec"] and 2003 not in p_a["spec"]
    assert "Priest|Shadow" in json.loads(head["talents"])["trees"]
    assert "Priest|Shadow" not in json.loads(stage_a["talents"])["trees"]
    assert head["builds"] != stage_a["builds"], "the modal blob of IMPORT_MAGE must differ"
    assert head["usage"]["Paladin|Retribution"]["specid"] == 1070
    assert stage_a["usage"]["Paladin|Retribution"]["specid"] == 70
    print("stage A : sampled material loses Lightsmith, a class node, a spec node, Priest|Shadow "
          "and IMPORT_MAGE's modal blob -- the adversarial journal bites")

    tu = update()                                   # WAY 2: cold checkpoint
    assert tu.mode == "rebuild" and tu.reason == "no_state", (tu.mode, tu.reason)
    assert bsd.TRAIT_UNION.exists()
    n_lines = sum(len(p) for p in parts)
    assert tu.lines == n_lines and tu.parsed == n_lines - 1, (tu.lines, tu.parsed)   # -1 blank
    cold = consumers(df_s, S, tu.complete(S.traits))
    assert cold["usage"] == head["usage"], "usage from the cold rebuild != the whole walk"
    assert cold["talents_gz"] == head["talents_gz"] and cold["builds_gz"] == head["builds_gz"]
    cp_full = dict(tu.checkpoint)
    assert cp_full["offset"] == j.stat().st_size == cp_full["size"]
    print(f"cold    : rebuild ({tu.reason}) over {tu.parsed} records -> talents.json.gz and "
          "builds.json.gz byte-identical to the whole walk")

    # -------------------------------------------------------------------- 3
    bsd.TRAIT_UNION.unlink()
    j.write_bytes(body(parts[0]))
    df_1 = df_s[df_s.report_code.isin({code_of(i) for i in range(20)})]
    S1 = quiet(bsd.gear_journal_pass, set(df_1.report_code))
    t1 = update()
    assert t1.mode == "rebuild" and t1.reason == "no_state"
    assert t1.lines == len(parts[0]) and t1.parsed == len(parts[0]) - 1
    assert consumers(df_1, S1, t1.complete(S1.traits))["usage"] == oracle(S1)
    with open(j, "ab") as fh:
        fh.write(body(parts[1]))
    t2 = update()
    assert t2.mode == "incremental", (t2.mode, t2.reason)
    assert t2.lines == len(parts[1]) and t2.parsed == len(parts[1]), (t2.lines, t2.parsed)
    with open(j, "ab") as fh:
        fh.write(body(parts[2]))
    t3 = update()
    assert t3.mode == "incremental" and t3.lines == len(parts[2]) == t3.parsed
    assert t3.checkpoint == cp_full, (t3.checkpoint, cp_full)
    inc = consumers(df_s, S, t3.complete(S.traits))  # WAY 3: two increments
    assert inc["usage"] == head["usage"], "usage after two appends != the whole walk"
    assert inc["talents_gz"] == head["talents_gz"] == cold["talents_gz"]
    assert inc["builds_gz"] == head["builds_gz"] == cold["builds_gz"]
    assert t3.checkpoint["records"] == sum(1 for p in parts for ln in p
                                           if ln and json.loads(ln).get("talents") is not None)
    # a run with nothing appended parses nothing
    t4 = update()
    assert t4.mode == "incremental" and t4.lines == 0 and t4.parsed == 0
    assert t4.checkpoint == cp_full
    # the union reloaded from disk answers the same (nothing lives only in memory)
    t5, why = bsd.TraitUnion.load()
    assert t5 is not None and why == ""
    assert consumers(df_s, S, t5.complete(S.traits))["usage"] == head["usage"]
    print(f"three   : whole walk == cold rebuild == {len(parts[1])}+{len(parts[2])}-line increments, "
          "byte-identical .gz for talents and builds; an idle run parses 0 lines")

    # ------------------------------------------------- rewritten journals
    full = j.read_bytes()
    # (a) shorter than the offset: a reseed from a smaller export
    j.write_bytes(body(parts[0]) + body(parts[1]))
    Sab = quiet(bsd.gear_journal_pass, codes)
    ta = update()
    assert ta.mode == "rebuild" and ta.reason == "journal_shorter", (ta.mode, ta.reason)
    assert ta.lines == len(parts[0]) + len(parts[1])
    ab = consumers(df_s, Sab, ta.complete(Sab.traits))
    assert ab["usage"] == oracle(Sab) and ab["talents"] == consumers(df_s, Sab, None)["talents"]
    # (b) same length, different head: the first record rewritten
    mutated = bytearray(full)
    at = full.index(b'"character": "') + len(b'"character": "')
    mutated[at] = ord("Z") if mutated[at] != ord("Z") else ord("Y")
    j.write_bytes(full)
    tb0 = update()          # the old prefix is intact: indistinguishable from an append, and correct
    assert tb0.mode == "incremental" and tb0.lines == len(parts[2]), (tb0.mode, tb0.lines)
    assert tb0.checkpoint == cp_full
    j.write_bytes(bytes(mutated))
    tb = update()
    assert tb.mode == "rebuild" and tb.reason == "head_changed", (tb.mode, tb.reason)
    # (c) same length, same head, a byte changed in the body before the offset
    mutated2 = bytearray(bytes(mutated))
    at2 = bytes(mutated).rindex(b'"character": "') + len(b'"character": "')
    assert at2 > 65536, "the fixture must be longer than the hashed head"
    mutated2[at2] = ord("Q") if mutated2[at2] != ord("Q") else ord("W")
    j.write_bytes(bytes(mutated2))
    tc = update()
    assert tc.mode == "rebuild" and tc.reason == "body_changed", (tc.mode, tc.reason)
    Sc = quiet(bsd.gear_journal_pass, codes)
    assert consumers(df_s, Sc, tc.complete(Sc.traits))["usage"] == oracle(Sc)
    print("rewrite : shorter -> rebuild(journal_shorter); head byte changed -> rebuild(head_changed); "
          "body byte changed under the same head -> rebuild(body_changed); outputs == whole walk")

    # ----------------------------------------------------- torn tails
    j.write_bytes(full)
    t0 = update()
    assert t0.mode == "rebuild"
    base_off = t0.checkpoint["offset"]
    assert base_off == len(full)
    extra = json.loads(parts[2][-1])            # a real record to tear
    extra["fight_id"] = 9
    extra["talents"] = {"tree": [{"id": 51101, "rank": 1}, {"id": 52103, "rank": 1}],
                        "talentImportString": "IMPORT_TORN"}
    extra["class"], extra["spec"] = "Mage", "Arcane"
    line = json.dumps(extra).encode("utf-8")
    # (i) half a record, no newline: parsed by nobody, checkpoint stays put
    with open(j, "ab") as fh:
        fh.write(line[: len(line) // 2])
    ti = update()
    assert ti.mode == "incremental" and ti.parsed == 0 and ti.lines == 1, (ti.mode, ti.parsed, ti.lines)
    assert ti.checkpoint["offset"] == base_off, "a torn tail must not move the checkpoint"
    Si = quiet(bsd.gear_journal_pass, codes)
    assert consumers(df_s, Si, ti.complete(Si.traits))["usage"] == oracle(Si)
    assert 52103 not in ti.specs["Mage|Arcane"]["entries"]
    # the fetcher's _repair_tail closes it with "\n"; the join is garbage
    # for the whole walk and for the union alike, later lines are fine
    with open(j, "ab") as fh:
        fh.write(b"\n" + line + b"\n")
    tj = update()
    assert tj.mode == "incremental" and tj.lines == 2 and tj.parsed == 1, (tj.lines, tj.parsed)
    assert tj.checkpoint["offset"] == j.stat().st_size
    Sj = quiet(bsd.gear_journal_pass, codes)
    assert consumers(df_s, Sj, tj.complete(Sj.traits))["usage"] == oracle(Sj)
    assert 52103 in tj.specs["Mage|Arcane"]["entries"]
    # (ii) a whole record missing only its newline: this run's consumers see
    # it (the whole walk does), the saved checkpoint does not; after the
    # repair it is read once more and committed
    extra["fight_id"] = 10
    extra["talents"]["tree"] = [{"id": 52103, "rank": 1}, {"id": 51201, "rank": 1}]
    line2 = json.dumps(extra).encode("utf-8")
    with open(j, "ab") as fh:
        fh.write(line2)
    tk = update()
    assert tk.mode == "incremental" and tk.lines == 1 and tk.parsed == 1
    assert tk.checkpoint["offset"] == j.stat().st_size - len(line2)
    assert 51201 in tk.specs["Mage|Arcane"]["entries"]          # in memory
    disk, _ = bsd.TraitUnion.load()
    assert 51201 not in disk.specs["Mage|Arcane"]["entries"]    # not on disk
    Sk = quiet(bsd.gear_journal_pass, codes)
    assert consumers(df_s, Sk, tk.complete(Sk.traits))["usage"] == oracle(Sk)
    with open(j, "ab") as fh:
        fh.write(b"\n")
    tl = update()
    assert tl.mode == "incremental" and tl.lines == 1 and tl.parsed == 1
    assert tl.checkpoint["offset"] == j.stat().st_size
    disk, _ = bsd.TraitUnion.load()
    assert 51201 in disk.specs["Mage|Arcane"]["entries"]
    assert consumers(df_s, Sk, tl.complete(Sk.traits))["usage"] == oracle(Sk)
    print("torn    : half a record -> 0 parsed, offset unchanged, garbage join skipped like the whole walk; "
          "record without newline -> served this run, committed only after the repair")

    # ------------------------------------------------ the .gz export source
    exp = tp / "gear.jsonl.gz"
    with gzip.open(exp, "wb") as fh:
        fh.write(full)
    j.unlink()
    bsd.GEAR_EXPORT = exp
    Sx = quiet(bsd.gear_journal_pass, codes)
    assert Sx.src == exp
    tx = update()
    assert tx.mode == "rebuild" and tx.reason == "source_changed", (tx.mode, tx.reason)
    assert consumers(df_s, Sx, tx.complete(Sx.traits))["usage"] == head["usage"]
    ty = update()
    assert ty.mode == "incremental" and ty.lines == 0 and ty.parsed == 0
    with gzip.open(exp, "wb") as fh:
        fh.write(full + line + b"\n")
    tz = update()
    assert tz.mode == "rebuild" and tz.reason == "export_changed", (tz.mode, tz.reason)
    assert 52103 in tz.specs["Mage|Arcane"]["entries"]
    print("export  : .gz source -> rebuild(source_changed) once, idle until rewritten -> rebuild(export_changed)")

    # ------------------------------------------------ absent journal / state
    tn = quiet(bsd.trait_union_update, None)
    assert tn.mode == "rebuild" and tn.reason == "no_journal" and tn.complete({}) == {}
    bsd.TRAIT_UNION.write_bytes(b"not gzip")
    tb = update()
    assert tb.mode == "rebuild" and tb.reason == "corrupt_state", (tb.mode, tb.reason)
    print("absence : no journal -> empty material; unreadable state -> rebuild(corrupt_state)")

print("PASS")
