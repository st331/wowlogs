#!/usr/bin/env python3
"""tests/test_reset_rule_tables_match.py (partitioned_payload.md §3.1, §9.1)

`RESET_RULES` / `RESET_DEFAULT` parsed out of `site/index.html` (and
`site/next/index.html` once it exists) equal `season.json.reset_rules`, and
the two Python mirrors (sitecalc, build_site_data) equal them too -- so the
builder's table cannot drift from the client's.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CLIENTS = [ROOT / "site" / "index.html", ROOT / "site" / "next" / "index.html"]


def parse_client_rules(html: str) -> dict:
    """{'US': [1, 15], 'EU': [2, 4], '*': [2, 22]} from
    `const RESET_RULES = {US:[1,15], EU:[2,4]}, RESET_DEFAULT=[2,22];`"""
    m = re.search(r"const\s+RESET_RULES\s*=\s*\{([^}]*)\}\s*,\s*RESET_DEFAULT\s*=\s*\[([^\]]*)\]", html)
    assert m, "RESET_RULES / RESET_DEFAULT not found in the client"
    rules = {}
    for reg, wd, hh in re.findall(r"(\w+)\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]", m.group(1)):
        rules[reg] = [int(wd), int(hh)]
    wd, hh = [int(x) for x in m.group(2).split(",")]
    rules["*"] = [wd, hh]
    return rules


def test_client_tables_match_season_json():
    season = json.loads((ROOT / "data" / "season.json").read_text())
    want = {k: list(v) for k, v in season["reset_rules"].items()}
    assert "*" in want and "US" in want and "EU" in want
    checked = 0
    for path in CLIENTS:
        if not path.exists():
            continue
        got = parse_client_rules(path.read_text(encoding="utf-8"))
        assert got == want, (path, got, want)
        checked += 1
    assert checked >= 1, "site/index.html must exist"


def test_python_mirrors_match_season_json():
    season = json.loads((ROOT / "data" / "season.json").read_text())
    want = {k: tuple(v) for k, v in season["reset_rules"].items()}
    import sitecalc as sc
    got = dict(sc.RESET_RULES)
    got["*"] = tuple(sc.RESET_DEFAULT)
    assert got == want, (got, want)
    # the legacy builder / llms export keep their own copy (B:2957)
    src = (ROOT / "scripts" / "build_site_data.py").read_text(encoding="utf-8")
    m = re.search(r'RESET_RULES\s*=\s*\{([^}]*)\}', src)
    d = re.search(r'RESET_DEFAULT\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', src)
    assert m and d
    legacy = {reg: (int(a), int(b)) for reg, a, b in
              re.findall(r'"(\w+)"\s*:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', m.group(1))}
    legacy["*"] = (int(d.group(1)), int(d.group(2)))
    assert legacy == want, (legacy, want)


def test_season_json_is_well_formed():
    season = json.loads((ROOT / "data" / "season.json").read_text())
    for k in ("slug", "name", "zone", "epoch", "start_utc", "reset_rules", "vocab",
              "spec_class", "spec_pairs", "spec_role", "keep_previous", "tuning_patches",
              "encounters"):
        assert k in season, k
    V = season["vocab"]
    for k in ("classes", "specs", "heroes", "dungeons", "regions", "roles"):
        assert "Unknown" in V[k], k
        assert len(V[k]) == len(set(V[k])), k
    assert V["roles"] == ["DPS", "Healer", "Tank", "Unknown"]
    assert len(season["spec_pairs"]) == 40
    assert len(season["spec_class"]) == len(V["specs"]) == len(season["spec_role"])
    # spec_pairs is exactly the union of the client's melee + ranged tables
    import sitecalc as sc
    pairs = {(V["classes"].index(k.split("|")[0]), V["specs"].index(k.split("|")[1]))
             for k in sc.MELEE | sc.RANGED}
    assert pairs == {tuple(p) for p in season["spec_pairs"]}
    for si, owners in enumerate(season["spec_class"]):
        assert owners == sorted(c for c, s in season["spec_pairs"] if s == si) or \
            (V["specs"][si] == "Unknown" and owners == [V["classes"].index("Unknown")]), si
    # the fetcher's zone and encounters are the ones season.json names
    import fetch_data as fd
    assert season["zone"] == fd.ZONE_ID
    assert {int(k): v for k, v in season["encounters"].items()} == fd.ENCOUNTERS
    assert set(season["encounters"].values()) <= set(V["dungeons"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_reset_rule_tables_match: all green")
