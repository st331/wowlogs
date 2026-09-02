"""test_legacy_single_pass (partitioned_payload.md §9.1, §7.4).

gear_journal_pass() -- one walk over the gear journal, parsing each line
once and feeding the four consumers, with a byte-level prefilter on the
sampled report codes -- must produce the SAME sets/stats/meta dicts as the
four original readers (sets_from_gear_journal, stats_from_gear_journal,
meta_from_gear_journal, _trait_journal_pass), and must parse at most as
many lines as the sample has records.

Pinned here, on a journal written by the REAL collector (parse_summary):

  * with no prefilter, all four outputs are identical, key for key, value
    for value, including the trait material for an empty, a partial and a
    full `wanted`, and the modal specID tie-break;
  * with the sample prefilter, sets/stats/meta equal the originals
    restricted to the sampled codes; lines parsed <= sampled records;
    unsampled records are skipped WITHOUT parsing;
  * every consumer -- tier_pieces, spec_stats_block, stats_sidecar,
    spec_meta_block, builds_sidecar, talents_doc -- emits byte-identical
    output on the sampled payload from either source;
  * the one deliberate change §7.4 states: the trait material (entry
    union, modal specID, selection blobs) is computed over the sampled
    records. It equals what the original walk yields over a journal
    holding only those records -- and nothing else changes;
  * lines the prefilter cannot classify (a null report_code, an escaped
    code) are parsed, never dropped; a torn trailing line and blank lines
    are tolerated exactly as before.
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


# --- geometry so the talents doc and §1.7 sel pairs are non-trivial ---------
GEO = {
    "specs": {"70": 900, "62": 901},
    "subtrees": {"48": "Templar"},
    "trees": {
        "900": {"nodes": {"1001": [3000, 1200, 0, 0], "1002": [3600, 1200, 0, 0],
                          "2001": [9900, 1200, 0, 0], "2002": [10500, 1800, 2, 0],
                          "3001": [7800, 9000, 0, 48]},
                "edges": [[1001, 1002], [1001, 2001], [2001, 2002], [3001, 1001]]},
        "901": {"nodes": {"1101": [3000, 1200, 0, 0], "2101": [9900, 1200, 0, 0]},
                "edges": []}},
    "entries": {"50001": [1001, 1, 111111, None], "50002": [1002, 2, 222222, None],
                "60001": [2001, 1, 333333, None], "60002": [2002, 1, 444444, None],
                "60003": [2002, 1, 555555, None], "70001": [3001, 1, 666666, None],
                "51101": [1101, 1, 777777, None], "52101": [2101, 1, 888888, None]},
}
SPELLS = {"111111": {"n": "Spell One", "ic": "a"}, "222222": {"n": "Spell Two", "ic": None},
          "333333": {"n": "Three", "ic": "c"}, "444444": {"n": "Choice A", "ic": "d"},
          "666666": {"n": "Hero Spell", "ic": "e"}, "777777": {"n": "M One", "ic": "f"},
          "888888": {"n": "M Two", "ic": "g"}}

TREES = {
    "Paladin|Retribution": [
        [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 2}, {"id": 60001, "rank": 1}, {"id": 70001, "rank": 1}],
        [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 1}, {"id": 60002, "rank": 1}, {"id": 70001, "rank": 1}],
        [{"id": 50001, "rank": 1}, {"id": 60003, "rank": 1}, {"id": 70001, "rank": 1}],
    ],
    "Mage|Arcane": [
        [{"id": 51101, "rank": 1}, {"id": 52101, "rank": 1}],
        [{"id": 51101, "rank": 1}],
    ],
    "Priest|Shadow": [
        [{"id": 52101, "rank": 1}, {"id": 51101, "rank": 2}],
    ],
}
SPECS = [("Paladin", "Retribution", 70), ("Mage", "Arcane", 62),
         ("Priest", "Shadow", 258)]
TIER, OLD = "1729", "1600"
BASE_MS = 1_787_000_000_000


def gear_for(i: int):
    """16 slots; set ids vary so tier cohorts are real."""
    g = []
    for s in range(16):
        it = {"id": 5000 + s + (i % 7), "itemLevel": 700 + (i % 30)}
        if s in (0, 2, 4, 6, 9) and i % 5:
            it["setID"] = TIER if (i % 5) != 4 else OLD
        if s == 15:
            it["permanentEnchant"] = 7008
        if s == 0 and i % 3 == 0:
            it["gems"] = [2001]
        if s == 8 and i % 4 == 0:
            it["bonusIDs"] = [8960, 12001]
        g.append(it)
    g[3] = {"id": 0}
    return g


def player(i: int, k: int, cls, spec, spec_id, *, server="Srv"):
    """One combatant with a deliberately varied combatantInfo."""
    sk = f"{cls}|{spec}"
    ci = {}
    variant = (i + k) % 9
    if variant != 0:                       # 0: gear absent (talents only)
        ci["gear"] = gear_for(i + k)
    if variant not in (1, 2):              # 1,2: no tree
        t = TREES[sk][(i + k) % len(TREES[sk])]
        ci["talentTree"] = [t[(j + i) % len(t)] for j in range(len(t))]
    if variant == 2:
        ci["talentImportString"] = f"IMPORT_{sk}_{i % 2}"   # string build
    if variant in (3, 4):
        ci["specID"] = spec_id
    if variant == 4:
        ci["specID"] = spec_id + 1000      # a minority spec id (modal tie-break)
    if variant not in (5, 6):
        st = {"Crit": {"min": 1000 + i}, "Haste": {"min": 2000},
              "Mastery": {"min": 3000}, "Versatility": {"min": 4000 + k},
              "Intellect": {"min": 90000}}
        if variant == 7:
            del st["Versatility"]          # torn capture
        ci["stats"] = st
    return {"id": k + 1, "name": f"{cls[:3]}{i}_{k}", "server": server,
            "type": cls, "specs": [spec], "icon": f"{cls}-{spec}",
            "maxItemLevel": 720, "combatantInfo": ci}


def make_run(i: int, code: str, fid: int):
    fight = {"code": code, "fid": fid, "dungeon": "Halls", "key_level": 12 + i % 6,
             "region": "US" if i % 2 else "EU", "score": 400.0,
             "medal": "gold" if i % 3 else "none", "affixes": [9],
             "start_time": BASE_MS + i * 3_600_000, "rank_duration_ms": 1_500_000}
    ps = []
    for k in range(5):
        cls, spec, sid = SPECS[(i + k) % len(SPECS)]
        ps.append(player(i, k, cls, spec, sid,
                         server=None if (i + k) % 11 == 0 else "Srv"))
    table = {"data": {"totalTime": 1_500_000,
                      "playerDetails": {"dps": ps},
                      "damageDone": [{"id": p["id"], "total": 9_000_000 + 1000 * i + p["id"]}
                                     for p in ps],
                      "deathEvents": []}}
    return parse_summary(fight, table, _Hero())


def code_of(i: int) -> str:
    return f"R{i:04d}abcdefghijk"[:16].ljust(16, "x")


N_RUNS = 120
rows, lines = [], []
for i in range(N_RUNS):
    code, fid = code_of(i), 1 + (i % 3)
    rw, rc = make_run(i, code, fid)
    rows.extend(rw)
    lines.extend(json.dumps(r, ensure_ascii=False) for r in rc)
    if i % 10 == 0:                       # refetched later with new gear: last copy wins
        rw2, rc2 = make_run(i + 7, code, fid)
        for r in rc2:
            r["character"] = rc[0]["character"] if rc else r["character"]
        lines.extend(json.dumps(r, ensure_ascii=False) for r in rc2)
    if i == 40:
        lines.append("")                  # blank line mid-journal
# lines the prefilter must PARSE rather than classify: a null code and an
# escaped code (neither can be a sampled key, both must still be read)
odd = json.loads(lines[3])
odd["report_code"] = None
lines.append(json.dumps(odd))
odd2 = json.loads(lines[4])
odd2["report_code"] = 'we\\"ird'
lines.append(json.dumps(odd2))
torn = lines[5][:len(lines[5]) // 2]      # torn trailing line

df_all = pd.DataFrame(rows)
sampled = {code_of(i) for i in range(N_RUNS) if i % 10 < 6}   # 60% of runs
df_s = df_all[df_all["report_code"].isin(sampled)].reset_index(drop=True)
assert len(df_s) < len(df_all)


def write_journal(tp: pathlib.Path, only_codes=None, gz=False) -> pathlib.Path:
    keep = []
    for ln in lines:
        if only_codes is not None and ln:
            try:
                if json.loads(ln).get("report_code") not in only_codes:
                    continue
            except ValueError:
                pass
        keep.append(ln)
    body = "\n".join(keep) + "\n" + torn
    p = tp / ("gear.jsonl.gz" if gz else "gear.jsonl")
    if gz:
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write(body)
    else:
        p.write_text(body, encoding="utf-8")
    return p


def point_caches(tp: pathlib.Path):
    for attr in ("NAMES_ITEMS", "NAMES_ENCHANTS", "CRAFTED_IDS", "EMB_IDENTITY",
                 "EMB_ITEMS", "EMB_OVERRIDES", "EMB_MARKERS", "NAMES_ICONS"):
        setattr(bsd, attr, tp / f"absent_{attr}.json")
    bsd.TRAIT_GEOMETRY = tp / "trait_geometry.json"
    bsd.NAMES_SPELLS = tp / "names_spells.json"
    bsd.TRAIT_GEOMETRY.write_text(json.dumps(GEO))
    bsd.NAMES_SPELLS.write_text(json.dumps(SPELLS))


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def consumers(df, sets, stats, meta, traits):
    """Every consumer's output from the given dicts (traits None = the
    original journal walk inside builds_sidecar / talents_doc)."""
    started = pd.to_datetime(pd.to_numeric(df["started_at"]), unit="ms")
    timed = df["medal"].map(bsd.MEDAL_TIMED).fillna(-1).astype(int)
    out = {}
    out["tier"] = quiet(bsd.tier_pieces, df, "t", journal=sets).tolist()
    out["specstats"] = quiet(bsd.spec_stats_block, df, started, timed, "t", journal=stats)
    out["stats_sidecar"] = quiet(bsd.stats_sidecar, df, stats, "t")
    out["specmeta"] = quiet(bsd.spec_meta_block, df, started, timed, "t", journal=meta)
    out["builds"] = quiet(bsd.builds_sidecar, df, meta, "t", traits=traits)
    usage = bsd.builds_sidecar.usage
    out["talents"] = quiet(bsd.talents_doc, "t", usage=usage, traits=traits)
    out["usage"] = usage
    return out


def count_lines(path, pred):
    n = 0
    with open(path, "rb") as fh:
        for raw in fh:
            if pred(raw):
                n += 1
    return n


for gz in (False, True):
    with tempfile.TemporaryDirectory() as tmp:
        tp = pathlib.Path(tmp)
        point_caches(tp)
        j = write_journal(tp, gz=gz)
        if gz:
            bsd.GEAR_JOURNAL = tp / "absent.jsonl"
            bsd.GEAR_EXPORT = j
        else:
            bsd.GEAR_JOURNAL = j
            bsd.GEAR_EXPORT = tp / "absent.jsonl.gz"

        # ---- the originals (oracle)
        L_sets = bsd.sets_from_gear_journal()
        L_stats = bsd.stats_from_gear_journal()
        L_meta = quiet(bsd.meta_from_gear_journal)
        assert L_sets and L_stats and L_meta
        all_builds: dict[str, set] = {}
        for (code, fid, ch, sv), m in L_meta.items():
            if m["build"]:
                row = df_all[(df_all.report_code == code) & (df_all.fight_id == fid)
                             & (df_all.character == ch)]
                if len(row):
                    sk = f"{row.iloc[0]['class']}|{row.iloc[0]['spec']}"
                    all_builds.setdefault(sk, set()).add(m["build"])
        partial = {sk: set(sorted(b)[:2]) for sk, b in all_builds.items()}
        wanteds = ({}, partial, all_builds)

        # ---- 1. no prefilter: identical, key for key
        G = quiet(bsd.gear_journal_pass, None)
        assert G.sets == L_sets, "sets differ without a prefilter"
        assert G.stats == L_stats, "stats differ without a prefilter"
        assert G.meta == L_meta, "meta differs without a prefilter"
        for w in wanteds:
            assert bsd._trait_journal_pass(w, G.traits) == bsd._trait_journal_pass(w), \
                f"trait material differs for wanted={sorted(w)}"
        if not gz:
            assert G.lines == count_lines(j, lambda r: True)   # every raw line seen
        n_records = sum(1 for ln in lines if ln) + 1   # + the torn line attempt
        assert G.parsed == n_records - 1, (G.parsed, n_records)   # torn line fails to parse
        assert G.prefiltered == 0

        # ---- 2. with the sample prefilter
        codes = set(df_s["report_code"].astype(str).unique())
        S = quiet(bsd.gear_journal_pass, codes)
        # the originals restricted to the sample -- plus the two records the
        # byte test cannot classify (null code, escaped code), which are
        # parsed like any other line rather than dropped; no payload row can
        # ever look them up, so their presence changes nothing downstream
        unclassifiable = {"", odd2["report_code"]}
        want = lambda d: {k: v for k, v in d.items()
                          if k[0] in codes or k[0] in unclassifiable}
        assert S.sets == want(L_sets), "prefiltered sets != originals restricted to the sample"
        assert S.stats == want(L_stats), "prefiltered stats != originals restricted"
        assert S.meta == want(L_meta), "prefiltered meta != originals restricted"
        sampled_records = sum(1 for ln in lines if ln and json.loads(ln).get("report_code") in codes)
        # + the two unclassifiable lines (null / escaped code) that must be parsed
        assert S.parsed <= sampled_records + 2, (S.parsed, sampled_records)
        assert S.parsed >= sampled_records, (S.parsed, sampled_records)
        assert S.parsed < S.lines and S.prefiltered > 0
        assert S.lines == G.lines
        assert S.prefiltered == S.lines - S.parsed - 1 - 1, \
            (S.prefiltered, S.lines, S.parsed)   # -1 blank, -1 torn (parsed, failed)
        # the odd lines were parsed (present in the dicts under their odd keys)
        assert any(k[0] == "" for k in S.meta), "null-code record was dropped, not parsed"
        assert any(k[0] == odd2["report_code"] for k in S.meta), \
            "escaped-code record was dropped"
        # every key any consumer can look up is present and identical
        for _, r in df_s.iterrows():
            k = bsd._gear_key(r["report_code"], r["fight_id"], r["character"], r["server"])
            assert S.sets.get(k) == L_sets.get(k) and S.stats.get(k) == L_stats.get(k) \
                and S.meta.get(k) == L_meta.get(k), k

        # ---- 3. every consumer, byte-identical from either source
        legacy = consumers(df_s, L_sets, L_stats, L_meta, None)
        single = consumers(df_s, S.sets, S.stats, S.meta, S.traits)
        for key in ("tier", "specstats", "stats_sidecar", "specmeta", "builds"):
            assert legacy[key] == single[key], f"{key} differs on the sampled payload"
        assert legacy["builds"] is not None and legacy["talents"] is not None
        assert legacy["specstats"] and legacy["specmeta"]
        assert sum(1 for t in legacy["tier"] if t >= 0) > 0
        # the talents doc / trait material: the §7.4 change, and only that.
        # From the whole journal the docs may legitimately differ (entries
        # allocated only by unsampled players); from a journal holding only
        # the sampled records the original walk must agree exactly.
        (tp / "s").mkdir()
        j_s = write_journal(tp / "s", only_codes=codes | unclassifiable | {None})
        keep_j, keep_e = bsd.GEAR_JOURNAL, bsd.GEAR_EXPORT
        bsd.GEAR_JOURNAL, bsd.GEAR_EXPORT = j_s, tp / "absent2.jsonl.gz"
        L_meta_s = quiet(bsd.meta_from_gear_journal)
        assert L_meta_s == S.meta
        legacy_s = consumers(df_s, bsd.sets_from_gear_journal(), bsd.stats_from_gear_journal(),
                             L_meta_s, None)
        bsd.GEAR_JOURNAL, bsd.GEAR_EXPORT = keep_j, keep_e
        assert legacy_s["talents"] == single["talents"], \
            "talents doc != the original walk over the sampled records"
        assert legacy_s["usage"] == single["usage"]
        assert legacy_s["builds"] == single["builds"]
        # ... and the union really is the only thing that can move: with the
        # sample = every run, the doc from the whole journal is identical too
        F = quiet(bsd.gear_journal_pass, set(df_all["report_code"].astype(str)))
        full = consumers(df_all, F.sets, F.stats, F.meta, F.traits)
        legacy_full = consumers(df_all, L_sets, L_stats, L_meta, None)
        for key in ("tier", "specstats", "stats_sidecar", "specmeta", "builds", "talents", "usage"):
            assert legacy_full[key] == full[key], f"{key} differs with every run sampled"
        # the escaped/null-code lines are not sampled keys, so F still prefiltered nothing real
        assert F.prefiltered == 0
        print(f"{'gz' if gz else 'jsonl'}: {G.lines} lines, {G.parsed} parsed unfiltered; "
              f"sample of {len(codes)}/{N_RUNS} codes -> {S.parsed} parsed, "
              f"{S.prefiltered} skipped on the byte test; 6 consumers identical")

# --- an empty / absent journal behaves as before -----------------------------
with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    bsd.GEAR_JOURNAL = tp / "none.jsonl"
    bsd.GEAR_EXPORT = tp / "none.jsonl.gz"
    E = bsd.gear_journal_pass({"abc"})
    assert E.sets == {} and E.stats == {} and E.meta == {} and E.traits == {} \
        and E.lines == 0 and E.parsed == 0
    assert bsd._trait_journal_pass({}, E.traits) == bsd._trait_journal_pass({}) == {}
    (tp / "none.jsonl").write_text("")
    E = bsd.gear_journal_pass(None)
    assert E.sets == bsd.sets_from_gear_journal() == {} and E.lines == 0
print("absence : missing / empty journal -> empty dicts, zero counters, as before")

print("PASS")
