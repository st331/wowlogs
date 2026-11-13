#!/usr/bin/env python3
"""The page's dungeon-adjustment maths, EXECUTED: site/index.html's own functions against an
independent reference.

test_site_contract.py pins the adjustment as text, and text can hold while the numbers break (a
mean where the median was, a tie that goes up, a par pooled across roles all pass a substring
check). This test cuts buildDnPar, dnView, refreshDnf and dnSpread out of the page, together with
the two helpers they stand on (q50, the median, and idxSet, the Dungeon filter), runs them under
node on a synthetic payload built to hit every branch, and compares every figure with a reference
written here from the rule in the README. The views are walked in one page session, so the page's
own signature decides when the factors are rebuilt:

  par(role, key, dungeon) = median DPS of the rows the window keeps; it counts only when the cell
  has 30+ parses and a distribution-free 95% bracket of the median within +-4%; a dungeon with no
  such par at all is left out of the average; a key level is "full" when every dungeon in view has
  a par; any other level borrows the nearest full level's factors (a tie goes DOWN); a role with no
  full level stays as logged; factor = mean par of the dungeons in view / own dungeon's par; one
  dungeon in view = 1; the Archon replica's pool counts the adjusted parse once at every dungeon's
  par (adjusted value / that dungeon's factor).

It does NOT run the consumers of the factors (aggregate, aggregateElite, dnFields): the figures the
page prints are checked by scripts/check_dn_oracle.js + check_dn_oracle.py, which need a browser.

Needs node on the PATH; without it the test says so and exits with status 3 (SKIP), never 0.
Set DN_PAGE=<path> to run it against another copy of the page.
"""
import json, math, os, pathlib, random, re, shutil, statistics, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = pathlib.Path(os.environ.get("DN_PAGE") or ROOT / "site" / "index.html")
NODE = shutil.which("node")
if not NODE:
    print("SKIP: node is not installed, so the page's adjustment maths was NOT executed")
    sys.exit(3)
html = PAGE.read_text(encoding="utf-8")

def js_function(name):
    m = re.search(r"^function " + name + r"\([^\n]*\n.*?^\}\n", html, re.S | re.M)
    assert m, f"function {name} not found in the page"
    return m.group(0)
def js_block(pattern, what):
    m = re.search(pattern, html, re.S | re.M)
    assert m, f"{what} not found in the page"
    return m.group(0)
def js_line(start):
    m = re.search(r"^" + re.escape(start) + r"[^\n]*\n", html, re.M)
    assert m, f"line starting {start!r} not found in the page"
    return m.group(0)

OUTW, DN_K, DN_MIN, DN_CI = 1000, 40, 30, 0.04
for line, want in (("const DN_MIN=", "const DN_MIN=30;"), ("const DN_CI=", "const DN_CI=0.04;"),
                   ("const DN_K=", "const DN_K=40;"), ("const OUTW=", "const OUTW=1000;")):
    assert js_line(line).startswith(want), f"the page's constant moved: {js_line(line).strip()} (this test assumes {want})"

# ---------------------------------------------------------------- a payload that hits every branch
rng = random.Random(20261113)
ROLES = ["DPS", "Healer", "Tank"]
DUNS = ["Altar", "Burrow", "Crypt", "Depths", "Stray", "Tomb"]  # Stray: a handful of rows, no par anywhere
BASE = {"Altar": 300000.0, "Burrow": 240000.0, "Crypt": 410000.0, "Depths": 330000.0, "Stray": 280000.0, "Tomb": 360000.0}
FOUR = DUNS[:4]                                                 # the four dungeons every role is measured in
ROLE_MUL = {"DPS": 1.0, "Healer": 0.18, "Tank": 0.55}
rows = []                                                       # (role, key, dungeon, dps, bucket)
def add(role, key, dun, n, sd, bucket=0):
    for _ in range(n):
        rows.append((role, key, dun, round(BASE[dun] * ROLE_MUL[role] * 1.06 ** key * math.exp(rng.gauss(0, sd))), bucket))
for d in DUNS[:4]:
    for k in range(6, 13): add("DPS", k, d, 120, 0.06)          # well measured: sets its own factors
    add("DPS", 5, d, 40, 0.45)                                  # 30+ parses but the median is NOT known to 4%
    add("DPS", 4, d, 12, 0.06)                                  # under 30 parses
    add("DPS", 14, d, 20, 0.06)
    if d != "Crypt": add("DPS", 13, d, 120, 0.06)               # a level with one dungeon missing is not full
    for k in (6, 10): add("Tank", k, d, 120, 0.06)              # two full levels four apart ...
    for k in (7, 8, 9, 11): add("Tank", k, d, 35, 0.45)         # ... +8 sits exactly between them: tie goes DOWN
    for k in range(6, 12): add("Healer", k, d, 40, 0.45)        # no level is known: the role stays as logged
    add("DPS", 8, d, 25, 0.06, OUTW)                            # rows outside the window never set a par
# +3: three dungeons well measured, the fourth with a median known on ONE side only (tight above it, wide below):
# the bracket has two sides and both must hold
for d in DUNS[:3]: add("DPS", 3, d, 120, 0.06)
m3 = BASE["Depths"] * 1.06 ** 3
for j in range(61):                                             # 31 parses within 1% above the median, 30 far below it
    rows.append(("DPS", 3, "Depths", round(m3 * (1 + 0.01 * rng.random()) if j < 31 else m3 * (0.92 - 0.42 * rng.random())), 0))
# +1: the mirror image, known on its LOWER side only
for d in DUNS[:3]: add("DPS", 1, d, 120, 0.06)
m1 = BASE["Depths"] * 1.06
for j in range(61):                                             # 31 parses within 1% below the median, 30 far above it
    rows.append(("DPS", 1, "Depths", round(m1 * (1 - 0.01 * rng.random()) if j < 31 else m1 * (1.08 + 0.42 * rng.random())), 0))
# +2: 60 evenly spaced parses, 0.7% apart: the 95% bracket (0.98 x sqrt(n) ranks either side) is about +-5.5%,
# a bracket half that wide would be +-3% and would wrongly let the cell count
for d in DUNS[:3]: add("DPS", 2, d, 120, 0.06)
m2 = BASE["Depths"] * 1.06 ** 2
for j in range(60):
    rows.append(("DPS", 2, "Depths", round(m2 * (1 + 0.007 * (j - 29.5))), 0))
add("DPS", 8, "Stray", 5, 0.06); add("Tank", 6, "Stray", 3, 0.06)
# Tomb: a par for TANKS only. It is part of the average (it has a par), so with it in view no DPS level is full
for k in (6, 10): add("Tank", k, "Tomb", 120, 0.06)
for k in (7, 8, 9, 11): add("Tank", k, "Tomb", 35, 0.45)
add("DPS", 8, "Tomb", 5, 0.06)
# +15..+20: three dungeons well measured, Depths built by hand to sit on each edge of the rule
for k in range(15, 21):
    for d in DUNS[:3]: add("DPS", k, d, 120, 0.06)
def shaped(key, vals):
    m = BASE["Depths"] * 1.06 ** key
    for v in vals: rows.append(("DPS", key, "Depths", round(m * v), 0))
near = lambda j: 1 + 0.0001 * (j - 50)                          # 100 parses within 0.5% of the median
shaped(15, [1 + 0.0002 * (j - 15) for j in range(30)])          # exactly 30 tight parses: counts
shaped(16, [1 + 0.0002 * (j - 15) for j in range(29)])          # 29: does not
# n=100: the bracket's ranks are floor(50-9.8)=40 and ceil(50+9.8)=60
shaped(17, [0.95 - 0.0001 * (40 - j) if j <= 40 else near(j) for j in range(100)])   # rank 40 is 5% low, rank 41 is not
shaped(18, [near(j) if j <= 59 else 1.05 + 0.0001 * (j - 60) for j in range(100)])   # rank 60 is 5% high, rank 59 is not
shaped(19, [0.955 - 0.0001 * (40 - j) if j <= 40 else near(j) for j in range(100)])  # 4.5% low: outside +-4%
shaped(20, [0.965 - 0.0001 * (40 - j) if j <= 40 else near(j) for j in range(100)])  # 3.5% low: inside
add("DPS", 45, "Altar", 1, 0.0)                                 # a key level past the table's last slot
rng.shuffle(rows)
N = len(rows)
R = {"role": [ROLES.index(r[0]) for r in rows], "key": [r[1] for r in rows], "dun": [DUNS.index(r[2]) for r in rows],
     "dps": [r[3] for r in rows], "rbucket": [r[4] for r in rows]}
VIEWS = [("every dungeon", []), ("the four measured dungeons", FOUR), ("two dungeons", ["Altar", "Crypt"]),
         ("one dungeon", ["Burrow"]), ("three, one of them the stray", ["Altar", "Depths", "Stray"]),
         ("one measured dungeon and the stray", ["Altar", "Stray"]),
         ("two measured dungeons and the tanks-only one", ["Altar", "Burrow", "Tomb"]),
         ("the four measured dungeons, again", FOUR), ("one dungeon, again", ["Burrow"])]

# ---------------------------------------------------------------- the page's own code under node
harness = "const D=" + json.dumps({"roles": ROLES, "dungeons": DUNS}) + ";\nconst R=" + json.dumps(R) + ";\nconst N=" + str(N) + ";\n" + """
const state={dun:new Set(), dnorm:true, role:new Set(), elite:false};
const OUTW=1000;
""" + js_block(r"^function idxSet\(sel,names\)\{\n.*?\n\}\n", "function idxSet") \
    + js_block(r"^function q50\(sorted\)\{[^\n]*\n[^\n]*\}\n", "function q50") \
    + "".join(js_line(s) for s in ("const DN_MIN=", "const DN_CI=", "const DN_FLAG=", "const DN_K=", "let DNP=", "let DNF=")) \
    + "".join(js_function(f) for f in ("buildDnPar", "dnView", "refreshDnf", "dnSpread")) + """
buildDnPar();
const out={par:Array.from(DNP.par), ok:Array.from(DNP.ok), cnt:Array.from(DNP.cnt), views:[]};
for(const sel of """ + json.dumps([v[1] for v in VIEWS]) + """){
  state.dun=new Set(sel);      // DNSIG is left alone: the page's own signature decides when factors are rebuilt
  refreshDnf();
  const spread=[];
  for(let i=0;i<N;i+=7){const o=[]; dnSpread(o,i,R.dps[i]*DNF[i]); spread.push(o)}
  out.views.push({S:DNI.S, thin:DNI.thin, on:DNI.on, any:Array.from(DNI.any), src:Array.from(DNI.src),
                  fac:Array.from(DNI.fac), dnf:Array.from(DNF), spread});
}
process.stdout.write(JSON.stringify(out));
"""
with tempfile.TemporaryDirectory() as tmp:
    f = pathlib.Path(tmp) / "dn_harness.js"
    f.write_text(harness, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=300)
assert r.returncode == 0, "the page's functions did not run under node:\n" + r.stderr[-3000:]
page = json.loads(r.stdout)

# ---------------------------------------------------------------- the reference, from the rule
nd, nr = len(DUNS), len(ROLES)
cells = {}
for i in range(N):
    if R["rbucket"][i] == OUTW: continue
    cells.setdefault((R["role"][i], min(R["key"][i], DN_K - 1), R["dun"][i]), []).append(R["dps"][i])
par, okc = {}, {}
for c, v in cells.items():
    v.sort(); n = len(v); m = statistics.median(v); h = 0.98 * math.sqrt(n)
    par[c] = m
    okc[c] = (n >= DN_MIN and m > 0 and m - v[max(0, math.floor(n / 2 - h))] <= DN_CI * m
              and v[min(n - 1, math.ceil(n / 2 + h))] - m <= DN_CI * m)
def reference(sel):
    S0 = [d for d in range(nd) if not sel or DUNS[d] in sel]
    S = [d for d in S0 if any(ok for (r, k, dd), ok in okc.items() if dd == d)]
    thin = [d for d in S0 if d not in S]; on = len(S) > 1
    fac, src, any_ = {}, {}, []
    for r in range(nr):
        good = [k for k in range(DN_K) if all(okc.get((r, k, d), False) for d in S)] if on else []
        any_.append(1 if good else 0)
        for k in range(DN_K):
            if not any((r, k, d) in cells for d in S): src[(r, k)] = -2; continue
            if not on: src[(r, k)] = k; continue
            use = min(good, key=lambda g: (abs(g - k), g)) if good else -1
            src[(r, k)] = use
            if use < 0: continue
            bar = sum(par[(r, use, d)] for d in S) / len(S)
            for d in S: fac[(r, k, d)] = bar / par[(r, use, d)]
    return S, thin, on, any_, src, fac

fails = 0
def check(ok, msg):
    global fails
    print(("ok   " if ok else "FAIL ") + msg)
    fails += 0 if ok else 1
close = lambda a, b: abs(a - b) <= 2e-6 * max(1.0, abs(a), abs(b))       # the page keeps factors in Float32

ci = lambda r, k, d: (r * DN_K + k) * nd + d
bad = [c for c in cells if not close(page["par"][ci(*c)], par[c]) or page["cnt"][ci(*c)] != len(cells[c]) or bool(page["ok"][ci(*c)]) != okc[c]]
check(not bad, f"par is the MEDIAN of the rows the window keeps, per role x key x dungeon, and counts only when known to +-4%: "
               f"{len(cells)} cells, {sum(okc.values())} with a par that counts, {len(bad)} wrong" + (f" (first: {bad[0]})" if bad else ""))
check(sum(1 for x in page["par"] if x) == len(cells), "no cell holds a par from rows outside the window or from another role")
for (name, sel), pv in zip(VIEWS, page["views"]):
    S, thin, on, any_, src, fac = reference(sel)
    check(pv["S"] == S and pv["thin"] == thin and pv["on"] == on,
          f"[{name}] dungeons in the average {[DUNS[d] for d in pv['S']]}, left out {[DUNS[d] for d in pv['thin']]}, adjusting: {pv['on']}")
    check(pv["any"] == any_, f"[{name}] roles with factors {[ROLES[r] for r in range(nr) if pv['any'][r]]} (a role with no known level stays as logged)")
    wrong = [(r, k) for r in range(nr) for k in range(DN_K) if pv["src"][r * DN_K + k] != src[(r, k)]]
    check(not wrong, f"[{name}] every key level takes its factors from the right level (nearest full one, a tie goes down): {len(wrong)} wrong"
                     + (f" (first: {ROLES[wrong[0][0]]} +{wrong[0][1]} page {pv['src'][wrong[0][0] * DN_K + wrong[0][1]]} reference {src[wrong[0]]})" if wrong else ""))
    wrong = [c for c in ((r, k, d) for r in range(nr) for k in range(DN_K) for d in range(nd))
             if not close(pv["fac"][ci(*c)], fac.get(c, 1.0))]
    check(not wrong, f"[{name}] factor = mean par of the dungeons in view / own dungeon's par: {len(fac)} factors, {len(wrong)} wrong")
    wrong = [i for i in range(N) if not close(pv["dnf"][i], fac.get((R["role"][i], min(R["key"][i], DN_K - 1), R["dun"][i]), 1.0))]
    check(not wrong, f"[{name}] every row carries the factor of its own role, key level and dungeon: {N} rows, {len(wrong)} wrong")
    wrong = 0
    for j, i in enumerate(range(0, N, 7)):
        r, k, d = R["role"][i], min(R["key"][i], DN_K - 1), R["dun"][i]
        v = R["dps"][i] * fac.get((r, k, d), 1.0)
        want = [v] * len(S) if d not in S else [v / fac.get((r, k, dd), 1.0) for dd in S]
        got = pv["spread"][j]
        if len(got) != len(want) or any(not close(a, b) for a, b in zip(got, want)): wrong += 1
    check(not wrong, f"[{name}] the replica's pool counts a parse once at every dungeon's par, and leaves a left-out dungeon's parse as logged: {wrong} wrong")
S, thin, on, any_, src, fac = reference(FOUR)
check(src[(2, 8)] == 6 and src[(2, 7)] == 6 and src[(2, 9)] == 10, "fixture: Tank +8 sits between two full levels and takes the LOWER one's factors")
check(src[(0, 5)] == 6 and src[(0, 13)] == 12 and src[(0, 4)] == 6 and src[(0, 39)] == 20, "fixture: a noisy level, a level missing a dungeon, a thin level and the clamped top slot all borrow")
def bracket(c):
    v = sorted(cells[c]); n = len(v); h = 0.98 * math.sqrt(n); m = statistics.median(v)
    return (m - v[max(0, math.floor(n / 2 - h))]) / m, (v[min(n - 1, math.ceil(n / 2 + h))] - m) / m
lo1, hi1 = bracket((0, 1, DUNS.index("Depths"))); lo2, hi2 = bracket((0, 2, DUNS.index("Depths"))); lo3, hi3 = bracket((0, 3, DUNS.index("Depths")))
check(src[(0, 3)] == 6 and hi3 <= DN_CI < lo3, "fixture: a median known on its upper side only does not count, so that level borrows too")
check(src[(0, 1)] == 6 and lo1 <= DN_CI < hi1, "fixture: a median known on its lower side only does not count either")
check(src[(0, 2)] == 6 and DN_CI < lo2 < 2 * DN_CI and DN_CI < hi2 < 2 * DN_CI,
      f"fixture: a cell whose 95% bracket is -{100 * lo2:.1f}% / +{100 * hi2:.1f}% does not count (a bracket half as wide would have let it)")
check(any_ == [1, 0, 1] and thin == [], "fixture: healers have no known level")
dep = DUNS.index("Depths")
def ranks(c, hmul=0.98, lo=math.floor, hi=math.ceil):
    v = sorted(cells[c]); n = len(v); h = hmul * math.sqrt(n); m = statistics.median(v)
    return (m - v[max(0, lo(n / 2 - h))]) / m, (v[min(n - 1, hi(n / 2 + h))] - m) / m
check(len(cells[(0, 15, dep)]) == 30 and okc[(0, 15, dep)] and src[(0, 15)] == 15, "fixture: a tight cell of exactly 30 parses counts, so that level is full")
check(len(cells[(0, 16, dep)]) == 29 and not okc[(0, 16, dep)] and max(ranks((0, 16, dep))) < 0.01 and src[(0, 16)] == 15,
      "fixture: the same cell with 29 parses does not count, however tight")
l17, _ = ranks((0, 17, dep)); l17n, _ = ranks((0, 17, dep), 0.70); l17c, _ = ranks((0, 17, dep), lo=math.ceil)
check(not okc[(0, 17, dep)] and l17 > DN_CI and l17n < 0.01 and l17c < 0.01 and src[(0, 17)] == 15,
      f"fixture: the lower rank of a 95% bracket (rank 40 of 100) is {100 * l17:.1f}% low, so the cell does not count; "
      "a narrower bracket, or the next rank up, would have let it")
_, h18 = ranks((0, 18, dep)); _, h18f = ranks((0, 18, dep), hi=math.floor)
check(not okc[(0, 18, dep)] and h18 > DN_CI and h18f < 0.01 and src[(0, 18)] == 20,
      f"fixture: the upper rank (rank 60 of 100) is {100 * h18:.1f}% high, so the cell does not count; the rank below would have let it")
l19, _ = ranks((0, 19, dep)); l20, _ = ranks((0, 20, dep))
check(not okc[(0, 19, dep)] and DN_CI < l19 < 0.05 and okc[(0, 20, dep)] and 0.03 < l20 <= DN_CI and src[(0, 19)] == 20 and src[(0, 20)] == 20,
      f"fixture: a bracket of {100 * l19:.1f}% is outside +-4% and one of {100 * l20:.1f}% is inside")
S, thin, on, any_, src, fac = reference([])
check(any_ == [0, 0, 1] and thin == [DUNS.index("Stray")] and DUNS.index("Tomb") in S,
      "fixture, every dungeon: the stray is left out; the tanks-only dungeon is part of the average, so no DPS level is full and DPS stays as logged")
S, thin, on, any_, src, fac = reference(["Altar", "Stray"])
check(S == [0] and thin == [DUNS.index("Stray")] and not on, "fixture: one measured dungeon and the stray leave ONE dungeon in the average, so nothing is adjusted")
print("\nPASS  the page's adjustment maths, executed" if not fails else f"\nFAILED ({fails} failures)")
sys.exit(1 if fails else 0)
