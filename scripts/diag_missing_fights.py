"""Diagnostic (diagnose.yml): where did these report codes go?

For each code: rows in players.jsonl (per fight), rows in the rankings journal
(per fight), rows in gear.jsonl; then ONE WCL query per code for the report's
M+ fights (keystone level, kept, start) so a fight the journals never saw can
be told apart from one the sweep saw and something later dropped.

Owner, 2026-09-08: four +19/+20 hunter runs wearing Lightspire Core; three
were in the export but only a hash SAMPLE of runs was published (fixed:
MAX_RUNS=0); the fourth (QqKhJgCN3d2wHfb4) was not in the export at all.
"""
import collections, json, os, pathlib, re, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
CODES = [c for c in os.environ.get("DIAG_CODES", "QqKhJgCN3d2wHfb4,XPdBxtcjG6FygkVH,pTcwhgPD2Zxz1J46,wPc9JqkHrgmtWRZY").split(",") if c]
FILES = {"players": ROOT / "data/processed/players.jsonl",
         "rankings": ROOT / "data/raw/rankings.jsonl",
         "gear": ROOT / "data/processed/gear.jsonl"}
pat = re.compile("|".join(re.escape(c) for c in CODES).encode())
found = {k: collections.defaultdict(collections.Counter) for k in FILES}
for name, path in FILES.items():
    if not path.exists():
        print(f"[diag] {name}: {path} missing"); continue
    n = 0; t0 = time.time()
    with path.open("rb") as fh:
        for line in fh:
            n += 1
            if not pat.search(line):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            code = r.get("report_code") or r.get("code") or ((r.get("report") or {}).get("code"))
            fid = r.get("fight_id") if "fight_id" in r else (r.get("fid") if "fid" in r else (r.get("report") or {}).get("fightID"))
            if code in CODES:
                found[name][code][fid] += 1
    print(f"[diag] {name}: {n:,} lines scanned in {time.time()-t0:.0f}s")
for code in CODES:
    print(f"\n== {code} ==")
    for name in FILES:
        per = found[name][code]
        print(f"  {name:9s}: " + (", ".join(f"f{f}x{c}" for f, c in sorted(per.items(), key=lambda x: (x[0] is None, x[0]))) if per else "nothing"))
try:
    from wcl_client import WCLClient
    client = WCLClient(verbose=False)
    for code in CODES:
        q = ('{ reportData { report(code: "%s") { title startTime endTime zone { id name } owner { name } '
             'fights(killType: Encounters) { id name keystoneLevel keystoneTime kill startTime endTime encounterID friendlyPlayers } } } }' % code)
        try:
            data = client.query(q, est_cost=2)
        except Exception as e:  # noqa: BLE001
            print(f"\n[wcl] {code}: query failed: {e}"); continue
        rep = ((data or {}).get("reportData") or {}).get("report")
        if not rep:
            print(f"\n[wcl] {code}: report not visible to this API key (private/deleted?)"); continue
        print(f"\n[wcl] {code}: '{rep.get('title')}' zone={rep.get('zone')} owner={rep.get('owner')} "
              f"start={time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(rep['startTime']/1000))}")
        for f in rep.get("fights") or []:
            if f.get("keystoneLevel"):
                print(f"   fight {f['id']:>3} {f.get('name','')[:22]:22s} +{f['keystoneLevel']:<2} kill={f.get('kill')} "
                      f"ks={f.get('keystoneTime')} enc={f.get('encounterID')} players={len(f.get('friendlyPlayers') or [])} "
                      f"start={time.strftime('%H:%MZ', time.gmtime((rep['startTime']+f['startTime'])/1000))}")
except Exception as e:  # noqa: BLE001
    print(f"[wcl] client unavailable: {e}")
