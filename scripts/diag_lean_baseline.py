"""Diagnostic (diagnose.yml): what the slot baseline does to upgrade lean on
the real journal.

Rebuilds the builds sidecar twice from the restored gear journal -- once with
each piece as its own baseline (the rule until 2026-09-09) and once with the
slot's modal item level -- and reports, per spec and slot, the lean the client
would print: sum(wearers_e * iup_e) / sum(wearers_e) over the shipped entries.
No network. Owner, 2026-09-09: "upgrade lean should be slot based, not item
based."
"""
import json, os, pathlib, sys
os.environ.setdefault("WOWLOGS_DEBUG_TALLIES", "1")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd
import build_site_data as bsd

csv = ROOT / "data" / "mythic_runs.csv.gz"
if not csv.exists():
    csv = csv.with_suffix("")
df = pd.read_csv(csv)
df = bsd.use_keystone_clock(df, "diag")
df = bsd.sample_runs(df, "diag")
for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
    df[col] = df[col].fillna("Unknown").replace("", "Unknown")
codes = set(df["report_code"].dropna().astype(str).unique())
gj = bsd.gear_journal_pass(codes)
print(f"[lean] {len(df):,} rows, gear journal {gj.parsed:,} records parsed", flush=True)


# the shipped document carries no per-entry wearer counts, so weight each
# entry the way the client does -- by the tallies the builder itself used.
def lean_table(slot_based: bool):
    bsd._SLOT_BASELINE = slot_based
    doc = json.loads(bsd.builds_sidecar(df, gj.meta, "diag", enc="dense") or "{}")
    counts = bsd._DEBUG_TALLIES or {}     # {(spec, slot idx): {(id, emb): wearers}}
    slots = doc.get("slots") or []
    rows = {}
    for sk, sv in (doc.get("specs") or {}).items():
        for k, s in enumerate(slots):
            col = (sv.get("items") or [])[k]
            tally = counts.get((sk, k)) or {}
            W = num = den = 0
            for e in col:
                c = tally.get((e["id"], e.get("emb"))) or 0
                den += c
                if e.get("iup") is None:
                    continue
                W += c
                num += c * e["iup"]
            rows[(sk, s)] = {"lean": (num / W) if W else None, "W": W, "den": den,
                             "ibase": (sv.get("ibase") or [None] * len(slots))[k]}
    return rows, doc


old, doc_old = lean_table(False)
new, doc_new = lean_table(True)
big = sorted({sk for sk, _ in new}, key=lambda sk: -sum(
    v["den"] for (a, _), v in new.items() if a == sk))[:8]
print(f"\n{'spec':28s} {'slot':>4s} {'ibase':>6s} {'old':>7s} {'new':>7s} {'wearers':>8s} {'cov':>5s}")
for sk in big:
    for s in (doc_new.get("slots") or []):
        o, n = old.get((sk, s)), new.get((sk, s))
        if not n or not n["den"]:
            continue
        f = lambda v: "  none" if v is None else f"{v:6.1f}"
        print(f"{sk[:28]:28s} {s:>4d} {str(n['ibase']):>6s} {f(o['lean'] if o else None)} "
              f"{f(n['lean'])} {n['W']:8,d} {100*n['W']/max(1,n['den']):4.0f}%")
ov = [v["lean"] for v in old.values() if v["lean"] is not None]
nv = [v["lean"] for v in new.values() if v["lean"] is not None]
print(f"\n[lean] cells with a value: old {len(ov):,} new {len(nv):,}")
if ov and nv:
    print(f"[lean] mean lean: old {sum(ov)/len(ov):.1f}%  new {sum(nv)/len(nv):.1f}%")
    z_old = sum(1 for v in ov if v < 0.5); z_new = sum(1 for v in nv if v < 0.5)
    print(f"[lean] cells reading ~0: old {z_old:,} new {z_new:,}")
covo = [v["W"] / v["den"] for v in old.values() if v["den"]]
covn = [v["W"] / v["den"] for v in new.values() if v["den"]]
if covo and covn:
    print(f"[lean] entry coverage of the slot: old {100*sum(covo)/len(covo):.1f}% "
          f"new {100*sum(covn)/len(covn):.1f}%")
