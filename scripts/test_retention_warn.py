#!/usr/bin/env python3
"""The part-filled oldest reset, from the builder's retention block to the sentence the page prints, and the
default of Compare: Time. Both are the page's own functions, run in node.

Since 2026-11-28 the policy keeps three resets, and the journals, pruned to 16 days until then, begin inside the
oldest one for some regions. The builder ships where they begin (retention.held_from) while a region's cut is
earlier, and the page prints the hours short per region. test_site_contract.py pinned the SOURCE of that sentence;
a review of 2026-11-28 showed every pin passing while the sentence named regions the view did not hold and could
print "168 h" for a gap it defines as under a week. So this file runs retentionShort() and retentionWarnHTML()
and requires the sentence itself:

  * the fixture is the retention block of the first three-reset builds, and, while retention.HELD_FROM stands,
    the builder's own apply_retention() must still produce its cuts and its held_from;
  * the sentence appears only while period A or B reads the oldest kept reset, never under the Archon replica,
    names only regions the Region filter holds, and never a region that is whole, unknown or absent;
  * a gap of a week or more is not this state, and one just under a week is never rounded up to a week.

Compare: Time opened on period B = [1] whatever A was, so from A = "Last reset" it compared that reset with
itself and every badge read +0.0%. setCompare() now takes the reset before A when B is empty or is A itself; an
explicit B that differs from A is the owner's choice and stands.
"""
import copy
import json
import pathlib
import re
import subprocess
import sys
import tempfile

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site_data as B  # noqa: E402
import retention as R        # noqa: E402

html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def block(name):
    m = re.search(r"^function %s\(.*?^\}\n" % re.escape(name), html, re.S | re.M)
    assert m, f"{name}() not found in the page"
    return m.group(0)


def line(name):
    m = re.search(r"^function %s\([^\n]*\n" % re.escape(name), html, re.M)
    assert m, f"{name}() not found in the page"
    return m.group(0)


# the retention block of the published builds of 2026-11-28 (04:55 and 05:55 UTC), cut to what the page reads
REGIONS = ["CN", "EU", "KR", "US", "Unknown"]
CUT = {"CN": "2026-11-11T22:00:00Z", "EU": "2026-11-11T04:00:00Z", "KR": "2026-11-11T22:00:00Z",
       "US": "2026-11-10T15:00:00Z", "Unknown": "2026-11-11T22:00:00Z"}
HELD = "2026-11-11T19:35:01Z"
FIX = {"resets": 3, "max_age_days": 23, "cut": CUT, "held_from": HELD, "stale": False, "lag_h": 0.3,
       "oldest_seen": "2026-11-04T07:57:18Z", "window_before_data": False}
SENT = "⚠ Oldest reset held from Nov 11 19:35 UTC: short by EU 16 h · US 29 h."

# ---- the builder still produces that block --------------------------------------------------------------
if getattr(R, "HELD_FROM", None) and R.RETENTION_RESETS == 3:
    def ms(t):
        return int(pd.Timestamp(t).value // 1_000_000)
    anchor = pd.Timestamp("2026-11-28T04:36:18Z")
    rows = [("US", anchor), ("EU", anchor), ("KR", anchor), ("CN", anchor), ("US", pd.Timestamp(HELD) + pd.Timedelta(seconds=41)),
            ("US", pd.Timestamp("2026-11-04T07:57:18Z"))]           # the straggler of the first build: before every cut
    df = pd.DataFrame({"region": [r for r, _ in rows], "started_at": [ms(t) for _, t in rows],
                       "report_code": [f"W{i}" for i in range(len(rows))], "fight_id": 1})
    B.apply_retention(df.copy(), "warn", now=pd.Timestamp("2026-11-28T04:55:39Z"))
    info = copy.deepcopy(B.RETENTION_INFO)
    check(info["held_from"] == HELD == R.HELD_FROM and {k: info["cut"][k] for k in ("CN", "EU", "KR", "US")} == {k: CUT[k] for k in ("CN", "EU", "KR", "US")}
          and info["resets"] == 3,
          f"the builder ships the block the page is tested on: held_from {info['held_from']}, cuts {info['cut']}")
else:
    print("note retention.HELD_FROM is gone or the reset count moved: the builder half is skipped, the page half stands on its fixture")

# ---- the page -------------------------------------------------------------------------------------------
HARNESS = """
const P=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));
let D={regions:P.regions, retention:null}, KEEP_BUCKETS=3, outWindowRows=0;
let weekCounts=new Map(), availWeeks=[];
const state={elite:false, compare:false, skill:false, gear:false, weeksA:new Set(), weeksB:new Set(), reg:new Set()};
let notices=[]; const box={checked:false};
function $(id){ return box }
function setNotice(s){ notices.push(s) }
function refreshPeriodUIs(){} function syncAxisUI(){} function render(){} function gearOff(){ state.gear=false }
%s
function setup(c){
  D.retention=c.ret===undefined?P.fix:c.ret;
  KEEP_BUCKETS=D.retention&&+D.retention.resets||3;
  const bk=c.buckets||[0,1,2];
  weekCounts=new Map(bk.map(w=>[w,1])); availWeeks=bk.slice().sort((a,b)=>a-b);
  state.elite=!!c.elite; state.compare=!!c.compare; state.skill=false; state.gear=false;
  state.weeksA=new Set(c.A||[]); state.weeksB=new Set(c.B||[]); state.reg=new Set(c.reg||[]);
  notices=[]; box.checked=!!c.on;
}
const out={warn:{}, short:{}, cmp:{}};
for(const c of P.warn){ setup(c); out.warn[c.name]=retentionWarnHTML(); }
for(const c of P.short){ setup(c); out.short[c.name]=retentionShort(D.retention, c.only?new Set(c.only):null); }
for(const c of P.cmp){ setup(c); setCompare(c.on); out.cmp[c.name]={B:[...state.weeksB], A:[...state.weeksA], compare:state.compare, box:box.checked, notices}; }
console.log(JSON.stringify(out));
""" % "".join([line("retInfo"), line("fmtInt"), line("setsEqual"), block("retentionShort"), block("retentionWarnHTML"), block("setCompare")])


def ret(**kv):
    r = copy.deepcopy(FIX)
    for k, v in kv.items():
        if k == "cut":
            r["cut"] = {**r["cut"], **v}
        elif v == "ABSENT":
            r.pop(k, None)
        else:
            r[k] = v
    return r


WEEK_ON = {k: (pd.Timestamp(v) + pd.Timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ") for k, v in CUT.items()}
WARN = [
    # (name, case, expected: None = no part-filled sentence at all, or the exact tail after "short by ")
    ("the page as it opens: A = this reset", {"A": [0], "B": [1]}, None),
    ("A = last reset", {"A": [1], "B": [1]}, None),
    ("A = the oldest kept reset", {"A": [2], "B": [1]}, "EU 16 h · US 29 h"),
    ("A = everything kept", {"A": [], "B": [1]}, "EU 16 h · US 29 h"),
    ("Compare: Time with B on the oldest reset", {"A": [0], "B": [2], "compare": True}, "EU 16 h · US 29 h"),
    ("B on the oldest reset, Compare off", {"A": [0], "B": [2], "compare": False}, None),
    ("the Archon replica, A = the oldest reset", {"A": [2], "elite": True}, None),
    ("Region = CN only: its oldest reset is whole", {"A": [2], "reg": ["CN"]}, None),
    ("Region = CN + KR: both whole", {"A": [], "reg": ["CN", "KR"]}, None),
    ("Region = US only", {"A": [2], "reg": ["US"]}, "US 29 h"),
    ("Region = EU + KR: only EU is short", {"A": [2], "reg": ["EU", "KR"]}, "EU 16 h"),
    ("the builder ships no held_from (null)", {"A": [2], "ret": ret(held_from=None)}, None),
    ("a payload from before 2026-11-28: no held_from key", {"A": [], "ret": ret(held_from="ABSENT")}, None),
    ("held_from is not a date", {"A": [2], "ret": ret(held_from="soon")}, None),
    ("no retention block at all", {"A": [2], "ret": None}, None),
    ("the oldest kept reset holds no rows", {"A": [], "buckets": [0, 1]}, None),
    ("the unknown region is never named", {"A": [2], "ret": ret(cut={"Unknown": "2026-11-10T00:00:00Z"})}, "EU 16 h · US 29 h"),
    ("a region the payload does not hold is never named", {"A": [2], "ret": ret(cut={"TW": "2026-11-10T00:00:00Z"})}, "EU 16 h · US 29 h"),
    ("a gap of exactly a week is not a part-filled reset", {"A": [2], "ret": ret(cut={"US": "2026-11-04T19:35:01Z"})}, "EU 16 h"),
    ("167.6 hours is printed as 167, never as a week", {"A": [2], "ret": ret(cut={"US": "2026-11-04T19:59:01Z"})}, "EU 16 h · US 167 h"),
    ("under an hour short is not said", {"A": [2], "ret": ret(cut={"EU": "2026-11-11T19:00:00Z"})}, "US 29 h"),
    ("a week on, every cut has passed the instant", {"A": [], "ret": ret(cut=WEEK_ON)}, None),
]
SHORT = [("all regions", {}), ("US only", {"only": ["US"]}), ("CN only", {"only": ["CN"]})]
CMP = [
    # (name, case, expected B, expected compare, a phrase the notice must carry or None)
    ("A = last reset, B still on its default [1]", {"A": [1], "B": [1], "on": True}, [2], True, None),
    ("A = this reset, B on its default", {"A": [0], "B": [1], "on": True}, [1], True, None),
    ("A = this reset, B chosen two resets back", {"A": [0], "B": [2], "on": True}, [2], True, None),
    ("A = this reset, B = this reset", {"A": [0], "B": [0], "on": True}, [1], True, None),
    ("A = the oldest reset, B = the oldest reset", {"A": [2], "B": [2], "on": True}, [0], True, None),
    ("A = the oldest reset, B empty", {"A": [2], "B": [], "on": True}, [0], True, None),
    ("after a rollover (no rows this reset yet): A fell back to last reset, B empty", {"A": [1], "B": [], "buckets": [1, 2], "on": True}, [2], True, None),
    ("a reset with no rows between: A = this reset, B = A", {"A": [0], "B": [0], "buckets": [0, 2], "on": True}, [2], True, None),
    ("one reset holds data", {"A": [1], "B": [], "buckets": [1], "on": True}, [], False, "only one reset has data"),
    ("one reset holds data and B is A", {"A": [1], "B": [1], "buckets": [1], "on": True}, [], False, "only one reset has data"),
    ("A = everything kept, B empty", {"A": [], "B": [], "on": True}, [], False, "Compare needs one reset in A"),
    ("switching Compare off leaves B alone", {"A": [1], "B": [1], "on": False}, [1], False, None),
]
payload = {"regions": REGIONS, "fix": FIX,
           "warn": [{"name": n, **c} for n, c, _ in WARN],
           "short": [{"name": n, **c} for n, c in SHORT],
           "cmp": [{"name": n, **c} for n, c, _, _, _ in CMP]}
with tempfile.TemporaryDirectory() as td:
    pj, js = pathlib.Path(td) / "p.json", pathlib.Path(td) / "h.js"
    pj.write_text(json.dumps(payload)); js.write_text(HARNESS)
    run = subprocess.run(["node", str(js), str(pj)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, "the page's functions threw in node:\n" + run.stderr[-1200:]
    got = json.loads(run.stdout)

check(SENT in got["warn"]["A = the oldest kept reset"],
      f"the sentence, to the letter: {got['warn']['A = the oldest kept reset'].strip()!r}")
for name, case, want in WARN:
    h = got["warn"][name]
    if want is None:
        check("Oldest reset" not in h and "short by" not in h, f"{name}: nothing is said ({h.strip()[:90]!r})")
    else:
        full = "⚠ Oldest reset held from Nov 11 19:35 UTC: short by " + want + ".</b>"
        check(full in h and h.count("Oldest reset") == 1, f"{name}: short by {want} ({h.strip()[:140]!r})")
    check("168 h" not in h, f"{name}: never 168 h")
s = got["short"]
check(s["all regions"] == {"from": "Nov 11 19:35 UTC", "parts": ["EU 16 h", "US 29 h"]}, f"retentionShort(): {s['all regions']}")
check(s["US only"] == {"from": "Nov 11 19:35 UTC", "parts": ["US 29 h"]} and s["CN only"] is None,
      f"retentionShort() under the Region filter: US {s['US only']}, CN {s['CN only']}")
# the hours are the builder's own arithmetic, not a retyped pair
for reg, hrs in (("EU", 16), ("US", 29)):
    h = (pd.Timestamp(HELD) - pd.Timestamp(CUT[reg])).total_seconds() / 3600
    check(int(h + 0.5) == hrs, f"{reg}: its cut is {h:.2f} h before the journals begin")

for name, case, wantB, wantOn, phrase in CMP:
    g = got["cmp"][name]
    ok = sorted(g["B"]) == wantB and g["compare"] is wantOn and sorted(g["A"]) == sorted(case["A"])
    if phrase is None:
        ok = ok and not g["notices"]
    else:
        ok = ok and len(g["notices"]) == 1 and phrase in g["notices"][0] and g["box"] is False
    if wantOn:
        ok = ok and sorted(g["B"]) != sorted(g["A"])
    check(ok, f"Compare: Time, {name}: B {g['B']}, on {g['compare']}" + (f", says {g['notices'][0]!r}" if g["notices"] else ""))

print()
if fails:
    print(f"FAIL  {fails} check(s) failed")
    sys.exit(1)
print(f"PASS  the part-filled sentence over {len(WARN)} views and Compare: Time's default over {len(CMP)}")
