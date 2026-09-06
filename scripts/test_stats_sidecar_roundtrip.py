#!/usr/bin/env python3
"""The stats sidecar's Python -> JavaScript contract, executed end to end.

WHY THIS EXISTS
    On 2026-09-02 the packer breached its gz cap, returned None, and the
    caller unlinked site/stats.json.gz. The Spec Frame's "Character stats"
    block fell back to a build-time cohort that ignores every filter, and
    the owner found it four days later: "as i adjust key levels, the stats
    don't seem to change at all."

    Every suite stayed green throughout, because the tests pinned the
    packer's OWN view of its output and nothing ever fed a real document to
    the real client decoder. The fix (2026-09-06) changed the wire format --
    column-major sparse bodies, delta-coded row indices, an optional
    quantisation scale -- which is exactly the class of change that can
    decode to plausible-looking garbage. So this test runs the actual
    decodeStatsSidecar() lifted out of site/index.html, under node, against
    documents produced by the actual stats_sidecar(), and compares every
    value to the ratings that went in.

    It also pins BACK-COMPATIBILITY with the pre-2026-09-06 layout
    (row-major, absolute indices, no scale/layout keys), because a client
    that has not reloaded is still asking for that shape.

Run: python3 scripts/test_stats_sidecar_roundtrip.py     (needs node)
"""
import base64
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site_data as bsd            # noqa: E402

INDEX = ROOT / "site" / "index.html"


def extract_js(name: str) -> str:
    """Lift one top-level `function name(...)  { ... }` out of index.html.

    Brace-matched rather than regex-terminated: the function body contains
    braces inside strings and comments, and a lazy match would truncate it
    into something that still parses but does the wrong thing.
    """
    src = INDEX.read_text()
    m = re.search(r"^function %s\(" % re.escape(name), src, re.M)
    assert m, f"{name} not found in {INDEX}"
    i = src.index("{", m.start())
    depth, j, in_s, in_c, esc = 0, i, "", "", False
    while j < len(src):
        ch, nx = src[j], src[j + 1:j + 2]
        if in_c:
            if in_c == "//" and ch == "\n":
                in_c = ""
            elif in_c == "/*" and ch == "*" and nx == "/":
                in_c, j = "", j + 1
        elif in_s:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_s:
                in_s = ""
        elif ch in "\"'`":
            in_s = ch
        elif ch == "/" and nx in "/*":
            in_c, j = "/" + nx, j + 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces while extracting {name}")


def js_decode(doc: dict, n_rows: int, probes: list[int], nst: int):
    """Decode `doc` with the REAL client function; return probes' stat rows.

    Returns None when the client rejects the document, which is itself a
    meaningful outcome (feature detection must reject, never guess).
    """
    harness = f"""
import {{readFileSync}} from "node:fs";
const N = {n_rows};
console.warn = () => {{}};
{extract_js('decodeStatsSidecar')}
const doc = JSON.parse(readFileSync(process.argv[2], "utf8"));
const C = decodeStatsSidecar(doc);
if (!C) {{ console.log(JSON.stringify({{ok: false}})); process.exit(0); }}
const nst = {nst};
const out = {{}};
for (const i of {json.dumps(probes)}) {{
  const r = C.map[i];
  out[i] = (r < 0) ? null
    : Array.from({{length: nst}}, (_, s) => C.at(r, s));
}}
console.log(JSON.stringify({{ok: true, rows: out, scale: C.scale}}));
"""
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "h.mjs"
        f.write_text(harness)
        # the document goes through a FILE, not argv: a real one is megabytes
        d = pathlib.Path(tmp) / "doc.json"
        d.write_text(json.dumps(doc))
        p = subprocess.run(["node", str(f), str(d)],
                           capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    r = json.loads(p.stdout.strip().splitlines()[-1])
    return r if r.get("ok") else None


# --- a frame the packer will accept, with ratings we can predict ------------
STATS = list(bsd.SIDECAR_STATS)
NROW = 240
rng = np.random.default_rng(3)
rows, recs, truth = [], {}, {}
now_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000)
for i in range(NROW):
    code, fid, ch, sv = f"RC{i}", i, f"Char{i}", "Realm"
    rows.append({"report_code": code, "fight_id": fid, "character": ch,
                 "server": sv, "class": "Mage", "spec": "Arcane",
                 "region": "US", "key_level": 15, "timed": 1,
                 "dps": 100000 + i, "deaths": 0,
                 "started_at": now_ms - i * 3_600_000})
    if i % 4 == 3:                      # a quarter of rows carry no stats
        continue
    vals = {nm: int(rng.integers(1, 9000)) for nm in STATS}
    if i % 7 == 0:                      # some stats genuinely absent -> 0
        vals.pop("Leech", None)
        vals.pop("Speed", None)
    truth[i] = [vals.get(nm, 0) for nm in STATS]
    recs[bsd._gear_key(code, fid, ch, sv)] = {"stats": vals}

df = pd.DataFrame(rows)
PROBES = sorted(list(truth)[:6] + [3, 7, 11])       # covered and uncovered


def pack(**kw):
    return json.loads(bsd.stats_sidecar(df, recs, "rt", **kw))


def check(label, doc, expect_scale=1):
    nst = len(doc["stats"])
    got = js_decode(doc, len(df), PROBES, nst)
    assert got is not None, f"{label}: the client REJECTED a document we ship"
    assert got["scale"] == expect_scale, (label, got["scale"])
    for i in PROBES:
        want = truth.get(i)
        have = got["rows"][str(i)]
        if want is None:
            # A stats-less row is "no entry" in sparse and an all-zero row in
            # dense -- both mean unknown, and the client's known() reads them
            # the same way. What must never happen is a NONZERO reading.
            assert have is None or not any(have), \
                f"{label}: stats-less row {i} decoded to {have}"
            continue
        assert have is not None, f"{label}: row {i} lost its coverage"
        for s in range(nst):
            w, h = want[s], have[s]
            if expect_scale == 1:
                assert h == w, (label, i, STATS[s], w, h)
            else:
                assert (h == 0) == (w == 0), (label, i, STATS[s], w, h)
                assert abs(h - w) <= expect_scale, (label, i, STATS[s], w, h)
    print(f"roundtrip   : {label} -- {len(PROBES)} probe rows x {nst} stats "
          f"decode identically in node")


if shutil.which("node") is None:                    # never a silent skip
    sys.exit("node is required for the sidecar's Python->JS contract test")

# 1. dense (row-major) -- the encoding auto mode picks at small scale
check("dense row-major", pack(enc="dense"))

# 2. sparse column-major + delta-coded indices -- the 2026-09-06 format
sp = pack(enc="sparse")
assert sp["layout"] == "col" and sp["idxdelta"] is True, sp
check("sparse column-major + delta idx", sp)

# 3. whatever auto mode actually ships
check("auto-selected encoding", pack())

# 4. a quantised rung: values within one step, 0 still means unknown
q = pack(enc="sparse", target=1, cap=10 ** 9)
assert q["scale"] > 1, q["scale"]
check(f"quantised /{q['scale']} rung", q, expect_scale=q["scale"])

# 5. BACK-COMPAT: the pre-2026-09-06 shape (row-major, absolute idx, and no
#    layout/scale/idxdelta keys at all) must still decode. A client tab that
#    has not reloaded is still built for this.
legacy_cov = sorted(truth)
legacy = {"stats": STATS, "flaskcol": False, "enc": "sparse", "n": len(df),
          "idx": base64.b64encode(
              np.array(legacy_cov, dtype="<u4").tobytes()).decode(),
          "data": base64.b64encode(
              np.array([truth[i] for i in legacy_cov],
                       dtype="<u2").tobytes()).decode()}
check("legacy row-major sparse (no layout/scale keys)", legacy)

# 6. feature detection must REJECT, not guess: a row count that disagrees with
#    the payload means the join is unsafe and fixed mode is the right answer
bad = dict(sp)
bad["n"] = len(df) + 1
assert js_decode(bad, len(df), PROBES, len(bad["stats"])) is None, \
    "a mismatched row count must be rejected, not decoded against"
truncated = dict(sp)
truncated["data"] = base64.b64encode(
    base64.b64decode(sp["data"])[:64]).decode()
assert js_decode(truncated, len(df), PROBES, len(sp["stats"])) is None, \
    "a truncated body must be rejected, not read past"
print("roundtrip   : mismatched row count and truncated body are REJECTED "
      "(fixed mode), never decoded against")

print("\nPASS")
