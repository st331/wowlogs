"""Diagnostic (diagnose.yml): does fightRankings(leaderboard: LogsOnly) drop the
entries that carry no report code (anonymous / rank-only), giving the sweep a
deeper fetchable window for free? One page per board, with and without the
argument (fleet finding F3, 2026-09-08)."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from wcl_client import WCLClient
BOARDS = [(12993, 17, "Altar +18 (saturated)"), (12825, 18, "Den +19"), (12993, 9, "Altar +10 (saturated, 4-page cap)")]
client = WCLClient(verbose=False)
def page(enc, br, extra):
    q = '{ worldData { encounter(id: %d) { fightRankings(metric: score, bracket: %d, page: 1%s) } } }' % (enc, br, extra)
    try:
        data = client.query(q, est_cost=1.5)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:300]}
    fr = (((data or {}).get("worldData") or {}).get("encounter") or {}).get("fightRankings") or {}
    rk = fr.get("rankings")
    if rk is None:
        return {"error": "no rankings in response", "raw": json.dumps(data)[:300]}
    codes = [((r.get("report") or {}).get("code")) for r in rk]
    return {"n": len(rk), "no_code": sum(1 for c in codes if not c), "more": fr.get("hasMorePages"),
            "first_codes": [c for c in codes if c][:3], "scores": (rk[0].get("score"), rk[-1].get("score")) if rk else None}
for enc, br, label in BOARDS:
    a = page(enc, br, "")
    b = page(enc, br, ", leaderboard: LogsOnly")
    print(f"\n== {label} (enc {enc}, bracket {br})")
    print("   default        :", json.dumps(a)[:300])
    print("   LogsOnly       :", json.dumps(b)[:300])
    if "n" in a and "n" in b:
        same = set(a["first_codes"]) & set(b["first_codes"])
        print(f"   -> LogsOnly page holds {b['n']} entries, {b['no_code']} without a code (default: {a['n']} / {a['no_code']}); "
              f"{'the anonymous entries are gone' if b['no_code'] == 0 and a['no_code'] > 0 else 'no change in the no-code share'}")
