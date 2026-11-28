#!/usr/bin/env python3
"""The part-filled oldest reset, from the builder's retention block to the sentence the page prints, and what
period B is under Compare: Time. Both are the page's own functions, run in node.

THE SENTENCE. Since 2026-11-28 the policy keeps three resets, and the journals, pruned to 16 days until then,
begin inside the oldest one for some regions. The builder ships where they begin (retention.held_from) while a
region's cut is earlier, and the page prints the hours short per region. test_site_contract.py pinned the SOURCE
of that sentence; a review of 2026-11-28 showed every pin passing while the sentence named regions the view did
not hold and could print "168 h" for a gap it defines as under a week. So this file runs retentionShort() and
retentionWarnHTML() and requires the sentence itself:

  * the fixture is the retention block the build of 2026-11-28 05:56 UTC published, and, while
    retention.HELD_FROM stands, the builder's own apply_retention() must still produce its cuts and held_from;
  * the sentence appears only while period A or B reads the oldest kept reset, never under the Archon replica,
    names only regions the Region filter holds, and never a region that is whole, unknown or absent;
  * a gap of a week or more is not this state, and one just under a week is never rounded up to a week;
  * it stands beside the page's other warnings (a late newest run, a stale page, parses past the window);
  * the date in it is UTC whatever zone the reader's machine is in: the harness runs in four zones and the
    output must be identical (the owner reads the page in IST, where 19:35 UTC is already the next day).

COMPARE: TIME never shows a period against itself; every badge would read +0.0%. Two reviews of 2026-11-28
found it doing so: from A = "Last reset" (B's default was fixed on last reset), and, after that was fixed in
setCompare() alone, by clicking a period chip while Compare was on, which nothing looked at. The rule is one
function now, settleB(), used by the switch and by the chips (periodChipClick()):

  * a B the owner picked stands while it is not the period A shows; a B chip that names A's period is refused;
  * any other B is the reset next to A: the one before it, or the one after it when A is the oldest;
  * with no second period Compare refuses, or switches off, and says which.

Beside a table of cases the harness walks EVERY sequence of up to four clicks (the switch, every A chip, every B
chip) from the state the page opens in, for six shapes of the window, and requires that Compare is never on with
A and B the same period.

Needs node on the PATH; without it the test says so and exits with status 3 (SKIP), never 0.
"""
import copy
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site_data as B  # noqa: E402
import retention as R        # noqa: E402

NODE = shutil.which("node")
if not NODE:
    print("SKIP: node is not installed, so the page's warning and its Compare rule were NOT executed")
    sys.exit(3)

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


# the lines of initData() that open the two periods on a new payload, as they stand in the page
m = re.search(r"^  state\.weeksA\.clear\(\);\n.*?^  if\(wa===0&&weekCounts\.has\(1\)\) state\.weeksB\.add\(1\);\n", html, re.S | re.M)
assert m, "initData()'s period defaults not found in the page"
INIT_PERIODS = m.group(0)
assert "state.bPicked=false" in INIT_PERIODS, "a new payload must start with nobody having picked B"

# The retention block published by the build of 2026-11-28 05:56:23Z (refresh run #6071), cut to what the page
# reads. The first three-reset build, 04:55:39Z, carried the same cuts and no held_from: the builder did not
# write one yet.
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
    anchor = pd.Timestamp("2026-11-28T05:35:57Z")
    rows = [("US", anchor), ("EU", anchor), ("KR", anchor), ("CN", anchor), ("US", pd.Timestamp(HELD) + pd.Timedelta(seconds=41)),
            ("US", pd.Timestamp("2026-11-04T07:57:18Z"))]           # the oldest row of that build: before every cut
    df = pd.DataFrame({"region": [r for r, _ in rows], "started_at": [ms(t) for _, t in rows],
                       "report_code": [f"W{i}" for i in range(len(rows))], "fight_id": 1})
    B.apply_retention(df.copy(), "warn", now=pd.Timestamp("2026-11-28T05:56:23Z"))
    info = copy.deepcopy(B.RETENTION_INFO)
    check(info["held_from"] == HELD == R.HELD_FROM and {k: info["cut"][k] for k in ("CN", "EU", "KR", "US")} == {k: CUT[k] for k in ("CN", "EU", "KR", "US")}
          and info["resets"] == 3 and info["oldest_seen"] == FIX["oldest_seen"] and info["window_before_data"] is False,
          f"the builder ships the block the page is tested on: held_from {info['held_from']}, cuts {info['cut']}")
    # the two numbers in the sentence, from what the builder returned
    for reg, hrs in (("EU", 16), ("US", 29)):
        h = (pd.Timestamp(info["held_from"]) - pd.Timestamp(info["cut"][reg])).total_seconds() / 3600
        check(int(h + 0.5) == hrs, f"{reg}: the builder's cut is {h:.2f} h before the instant it ships, the {hrs} h of the sentence")
else:
    print("note retention.HELD_FROM is unset or the reset count moved: the builder half is skipped, the page half stands on its fixture")

# ---- the page -------------------------------------------------------------------------------------------
HARNESS = """
const P=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));
let D={regions:P.regions, retention:null}, KEEP_BUCKETS=3, outWindowRows=0;
let weekCounts=new Map(), availWeeks=[];
const state={elite:false, compare:false, skill:false, gear:false, bPicked:false, dayCut:0, weeksA:new Set(), weeksB:new Set(), reg:new Set()};
let notices=[]; const box={checked:false};
function $(id){ return box }
function setNotice(s){ notices.push(s) }
function refreshPeriodUIs(){} function syncAxisUI(){} function render(){} function gearOff(){ state.gear=false }
%s
function initPeriods(){
%s
}
function setup(c){
  D.retention=c.ret===undefined?P.fix:c.ret;
  KEEP_BUCKETS=D.retention&&+D.retention.resets||3;
  outWindowRows=c.out||0;
  const bk=c.buckets||[0,1,2];
  weekCounts=new Map(bk.map(w=>[w,1])); availWeeks=bk.slice().sort((a,b)=>a-b);
  state.elite=!!c.elite; state.compare=!!c.compare; state.skill=false; state.gear=false; state.bPicked=false;
  state.weeksA=new Set(c.A||[]); state.weeksB=new Set(c.B||[]); state.reg=new Set(c.reg||[]);
  notices=[]; box.checked=!!c.compare;
}
function open(bk){              // the page as a new payload opens it
  setup({buckets:bk}); initPeriods();
}
function act(s){
  if(s[0]==="on"){ box.checked=true; setCompare(true); }
  else if(s[0]==="off"){ box.checked=false; setCompare(false); }
  else if(s[0]==="A") periodChipClick("A", state.weeksA, new Set(s[1]));
  else if(s[0]==="B") periodChipClick("B", state.weeksB, new Set(s[1]));
  else throw new Error("unknown step "+s[0]);
}
const snap=()=>({A:[...state.weeksA].sort(), B:[...state.weeksB].sort(), compare:state.compare, box:box.checked, picked:state.bPicked, notices:notices.slice()});
const out={warn:{}, short:{}, cmp:{}, walk:[]};
for(const c of P.warn){ setup(c); out.warn[c.name]=retentionWarnHTML(); }
for(const c of P.short){ setup(c); out.short[c.name]=retentionShort(D.retention, c.only?new Set(c.only):null); }
for(const c of P.cmp){ open(c.buckets||[0,1,2]); for(const s of c.steps) act(s); out.cmp[c.name]=snap(); }
// every sequence of up to DEPTH clicks, from the page as it opens, for each shape of the window
for(const bk of P.shapes){
  const chips=[[]].concat(bk.map(w=>[w]));
  const acts=[["on"],["off"]].concat(chips.map(c=>["A",c]), chips.map(c=>["B",c]));
  let seqs=0, bad=[], onStates=0;
  const rec=(seq)=>{
    if(seq.length){
      open(bk);
      let prev=null;
      for(let i=0;i<seq.length;i++){
        const before=snap(); notices=[];
        act(seq[i]);
        const now=snap(), s=seq[i];
        let why=null;
        if(now.compare&&setsEqual(state.weeksA,state.weeksB)) why="Compare is on with A and B the same period";
        else if(now.compare!==now.box) why="the checkbox and the state disagree";
        else if(s[0]==="A"&&JSON.stringify(now.A)!==JSON.stringify(s[1])) why="an A chip did not set A";
        else if(s[0]==="B"&&now.notices.length===0&&JSON.stringify(now.B)!==JSON.stringify(s[1])) why="a B chip was accepted and did not set B";
        else if(s[0]==="B"&&now.notices.length&&JSON.stringify(now.B)!==JSON.stringify(before.B)) why="a refused B chip changed B";
        else if(s[0]==="B"&&now.notices.length&&!(before.compare&&JSON.stringify(before.A)===JSON.stringify(s[1]))) why="a B chip was refused though it does not name A's period";
        else if(s[0]==="off"&&(now.compare||JSON.stringify(now.B)!==JSON.stringify(before.B)||now.notices.length)) why="switching off did more than switch off";
        else if(s[0]==="on"&&!now.compare&&now.notices.length!==1) why="a refusal with no reason, or more than one";
        else if(s[0]==="on"&&now.compare&&now.notices.length) why="Compare switched on and printed a notice";
        else if(s[0]==="A"&&before.compare&&!now.compare&&now.notices.length!==1) why="Compare switched itself off without saying so";
        else if(s[0]==="A"&&before.compare&&now.compare&&before.picked&&JSON.stringify(before.B)!==JSON.stringify(now.A)&&JSON.stringify(now.B)!==JSON.stringify(before.B)) why="a picked B that is not A was replaced by an A chip";
        else if(s[0]==="on"&&now.compare&&before.picked&&JSON.stringify(before.B)!==JSON.stringify(before.A)&&JSON.stringify(now.B)!==JSON.stringify(before.B)) why="a picked B that is not A was replaced at switch-on";
        if(why&&bad.length<5) bad.push({bk, seq:seq.slice(0,i+1), why, now});
        if(why) break;
        if(i===seq.length-1&&now.compare) onStates++;
      }
      seqs++;
    }
    if(seq.length<P.depth) for(const a of acts) rec(seq.concat([a]));
  };
  rec([]);
  out.walk.push({bk, seqs, onStates, bad});
}
console.log(JSON.stringify(out));
""" % ("".join([line("retInfo"), line("fmtInt"), line("setsEqual"), block("retentionShort"), block("retentionWarnHTML"),
                block("nextToA"), block("settleB"), block("periodChipClick"), block("setCompare")]), INIT_PERIODS)


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
BOTH = "EU 16 h · US 29 h"
WARN = [
    # (name, view, the tail after "short by " or None for no part-filled sentence at all, other text the note must carry)
    ("the page as it opens: A = this reset", {"A": [0], "B": [1]}, None, []),
    ("A = last reset", {"A": [1], "B": [1]}, None, []),
    ("A = the oldest kept reset", {"A": [2], "B": [1]}, BOTH, []),
    ("A = everything kept", {"A": [], "B": [1]}, BOTH, []),
    ("Compare: Time with B on the oldest reset", {"A": [0], "B": [2], "compare": True}, BOTH, []),
    ("B on the oldest reset, Compare off", {"A": [0], "B": [2], "compare": False}, None, []),
    ("the Archon replica, A = the oldest reset", {"A": [2], "elite": True}, None, []),
    ("Region = CN only: its oldest reset is whole", {"A": [2], "reg": ["CN"]}, None, []),
    ("Region = CN + KR: both whole", {"A": [], "reg": ["CN", "KR"]}, None, []),
    ("Region = US only", {"A": [2], "reg": ["US"]}, "US 29 h", []),
    ("Region = EU + KR: only EU is short", {"A": [2], "reg": ["EU", "KR"]}, "EU 16 h", []),
    ("the builder ships no held_from (null)", {"A": [2], "ret": ret(held_from=None)}, None, []),
    ("a payload from before 2026-11-28: no held_from key", {"A": [], "ret": ret(held_from="ABSENT")}, None, []),
    ("held_from is not a date", {"A": [2], "ret": ret(held_from="soon")}, None, []),
    ("no retention block at all", {"A": [2], "ret": None}, None, []),
    ("the oldest kept reset holds no rows", {"A": [], "buckets": [0, 1]}, None, []),
    ("the unknown region is never named", {"A": [2], "ret": ret(cut={"Unknown": "2026-11-10T00:00:00Z"})}, BOTH, []),
    ("a region the payload does not hold is never named", {"A": [2], "ret": ret(cut={"TW": "2026-11-10T00:00:00Z"})}, BOTH, []),
    ("a gap of exactly a week is not a part-filled reset", {"A": [2], "ret": ret(cut={"US": "2026-11-04T19:35:01Z"})}, "EU 16 h", []),
    ("167.6 hours is printed as 167, never as a week", {"A": [2], "ret": ret(cut={"US": "2026-11-04T19:59:01Z"})}, "EU 16 h · US 167 h", []),
    ("under an hour short is not said", {"A": [2], "ret": ret(cut={"EU": "2026-11-11T19:00:00Z"})}, "US 29 h", []),
    ("a week on, every cut has passed the instant", {"A": [], "ret": ret(cut=WEEK_ON)}, None, []),
    # beside the page's other warnings: none of them replaces it, it replaces none of them
    ("with a newest run 9.4 h before the build", {"A": [2], "ret": ret(lag_h=9.4)}, BOTH, ["Newest run 9.4 h before this build."]),
    ("on a stale page", {"A": [2], "ret": ret(stale=True)}, BOTH, ["⚠ Stale: the newest run held is older than 23 days."]),
    ("with 5 parses past the window", {"A": [2], "out": 5}, BOTH, ["⚠ 5 parses past the kept resets, not shown."]),
    ("all four at once", {"A": [], "out": 1, "ret": ret(stale=True, lag_h=4.0)}, BOTH,
     ["⚠ Stale: the newest run held is older than 23 days.", "Newest run 4.0 h before this build.", "⚠ 1 parse past the kept resets, not shown."]),
    ("the other three without it: A = this reset", {"A": [0], "out": 2, "ret": ret(stale=True, lag_h=4.0)}, None,
     ["⚠ Stale: the newest run held is older than 23 days.", "Newest run 4.0 h before this build.", "⚠ 2 parses past the kept resets, not shown."]),
]
SHORT = [("all regions", {}), ("US only", {"only": ["US"]}), ("CN only", {"only": ["CN"]})]
ONE = "Compare needs two resets — only one reset has data inside the window right now."
BOTH_ALL = "Compare needs a reset in A or B — both are on everything kept."
SAME = "B is the period A already shows."
OFF = "Compare off — A and B are both everything kept."
ON, OFFSW = ["on"], ["off"]
def A(*w): return ["A", list(w)]
def Bc(*w): return ["B", list(w)]
CMP = [
    # (name, window shape, the clicks from the page as it opens, A, B, Compare on?, the one notice of the last click or None)
    ("the page as it opens, then Compare", [0, 1, 2], [ON], [0], [1], True, None),
    ("A = Last reset, then Compare (the first review: it compared the reset with itself)", [0, 1, 2], [A(1), ON], [1], [2], True, None),
    ("A = 2 resets ago, then Compare: the reset next to the oldest", [0, 1, 2], [A(2), ON], [2], [1], True, None),
    ("Compare, then A onto Last reset (the second review: the chip route)", [0, 1, 2], [ON, A(1)], [1], [2], True, None),
    ("Compare, then A onto the oldest reset", [0, 1, 2], [ON, A(2)], [2], [1], True, None),
    ("Compare, then A to last reset and back", [0, 1, 2], [ON, A(1), A(0)], [0], [1], True, None),
    ("Compare, then the B chip of A's own period", [0, 1, 2], [ON, Bc(0)], [0], [1], True, SAME),
    ("Compare, then B = 2 resets ago: the owner's pick", [0, 1, 2], [ON, Bc(2)], [0], [2], True, None),
    ("a picked B stands when A moves elsewhere", [0, 1, 2], [ON, Bc(2), A(1)], [1], [2], True, None),
    ("a picked B gives way when A lands on it", [0, 1, 2], [ON, Bc(2), A(2)], [2], [1], True, None),
    ("a picked B survives Compare off and on", [0, 1, 2], [ON, Bc(2), OFFSW, A(1), ON], [1], [2], True, None),
    ("a B that Compare chose does not stick to the next use", [0, 1, 2], [A(1), ON, OFFSW, A(0), ON], [0], [1], True, None),
    ("B = everything kept, picked, survives off and on", [0, 1, 2], [ON, Bc(), OFFSW, ON], [0], [], True, None),
    ("B = everything kept, then A = everything kept", [0, 1, 2], [ON, Bc(), A()], [], [], False, OFF),
    ("A = everything kept, then Compare: against last reset, as before", [0, 1, 2], [A(), ON], [], [1], True, None),
    ("A = everything kept, Compare, then B = everything kept", [0, 1, 2], [A(), ON, Bc()], [], [1], True, SAME),
    ("switching Compare off leaves B alone", [0, 1, 2], [ON, Bc(2), OFFSW], [0], [2], False, None),
    ("after a rollover (no rows this reset yet): A opens on last reset", [1, 2], [ON], [1], [2], True, None),
    ("after a rollover, A = everything kept: no second period", [1, 2], [A(), ON], [], [], False, BOTH_ALL),
    ("a reset with no rows between two that have them", [0, 2], [ON], [0], [2], True, None),
    ("two resets: Compare, then A onto last reset", [0, 1], [ON, A(1)], [1], [0], True, None),
    ("one reset holds data", [1], [ON], [1], [], False, ONE),
    ("one reset holds data, A = everything kept", [0], [A(), ON], [], [], False, ONE),
]
SHAPES = [[0, 1, 2], [0, 1], [1, 2], [0, 2], [0], [1]]
DEPTH = 4
payload = {"regions": REGIONS, "fix": FIX, "shapes": SHAPES, "depth": DEPTH,
           "warn": [{"name": n, **c} for n, c, _, _ in WARN],
           "short": [{"name": n, **c} for n, c in SHORT],
           "cmp": [{"name": n, "buckets": bk, "steps": steps} for n, bk, steps, *_ in CMP]}
ZONES = ["UTC", "Asia/Kolkata", "America/Los_Angeles", "Pacific/Kiritimati"]
outs = {}
with tempfile.TemporaryDirectory() as td:
    pj, js = pathlib.Path(td) / "p.json", pathlib.Path(td) / "h.js"
    pj.write_text(json.dumps(payload)); js.write_text(HARNESS)
    for tz in ZONES:
        run = subprocess.run([NODE, str(js), str(pj)], capture_output=True, text=True, timeout=120, env={**os.environ, "TZ": tz})
        assert run.returncode == 0, f"the page's functions threw in node (TZ={tz}):\n" + run.stderr[-1500:]
        outs[tz] = run.stdout
got = json.loads(outs["UTC"])
# a guard on the harness itself: the zone must really have changed, or the comparison below proves nothing
probe = subprocess.run([NODE, "-e", "console.log(new Date(Date.parse('2026-11-11T19:35:01Z')).getDate())"], capture_output=True, text=True,
                       env={**os.environ, "TZ": "Asia/Kolkata"}).stdout.strip()
check(probe == "12", f"node honours TZ: 19:35 UTC on 11 November is the {probe}th in Asia/Kolkata")
check(all(outs[tz] == outs["UTC"] for tz in ZONES), "every sentence is the same in " + ", ".join(ZONES) + ": the date printed is UTC, not the reader's")

check(SENT in got["warn"]["A = the oldest kept reset"],
      f"the sentence, to the letter: {got['warn']['A = the oldest kept reset'].strip()!r}")
for name, view, want, also in WARN:
    h = got["warn"][name]
    if want is None:
        ok = "Oldest reset" not in h and "short by" not in h
    else:
        ok = ("⚠ Oldest reset held from Nov 11 19:35 UTC: short by " + want + ".</b>") in h and h.count("Oldest reset") == 1 and "168 h" not in h
    ok = ok and all(a in h for a in also) and h.count("<b ") == (0 if want is None else 1) + len(also)
    check(ok, f"{name}: " + ("nothing about the oldest reset" if want is None else f"short by {want}") + (f", beside {len(also)} other warning(s)" if also else "")
          + f" ({re.sub('<[^>]+>', '', h).strip()[:150]!r})")
s = got["short"]
check(s["all regions"] == {"from": "Nov 11 19:35 UTC", "parts": ["EU 16 h", "US 29 h"]}, f"retentionShort(): {s['all regions']}")
check(s["US only"] == {"from": "Nov 11 19:35 UTC", "parts": ["US 29 h"]} and s["CN only"] is None,
      f"retentionShort() under the Region filter: US {s['US only']}, CN {s['CN only']}")

for name, bk, steps, wantA, wantB, wantOn, notice in CMP:
    g = got["cmp"][name]
    ok = g["A"] == wantA and g["B"] == wantB and g["compare"] is wantOn and g["box"] is wantOn
    last = g["notices"][-1:] if g["notices"] else []
    ok = ok and ((notice is None and not last) or (notice is not None and last == [notice]))
    check(ok, f"Compare: Time, {name}: A {g['A']}, B {g['B']}, on {g['compare']}" + (f", says {last[0]!r}" if last else ""))

total = 0
for w in got["walk"]:
    total += w["seqs"]
    check(not w["bad"] and w["seqs"] > 100 and ((w["onStates"] > 0) is (len(w["bk"]) >= 2)),
          f"window {w['bk']}: {w['seqs']:,} click sequences of up to {DEPTH}, {w['onStates']:,} ending with Compare on"
          + (", none against itself" if len(w["bk"]) >= 2 else " (one reset holds data: Compare never switches on)")
          + ("".join(f"\n       {b['why']}: {b['seq']} -> {b['now']}" for b in w["bad"])))

print()
if fails:
    print(f"FAIL  {fails} check(s) failed")
    sys.exit(1)
print(f"PASS  the part-filled sentence over {len(WARN)} views in {len(ZONES)} time zones; Compare: Time over {len(CMP)} cases and {total:,} click sequences")
