"""Diagnostic (diagnose.yml): how many completed public keystone runs exist per hour on
WCL versus how many the leaderboard sweep delivered to the export?

Lists reportData.reports(zoneID: 55) for a sample hour, fetches each report's keystone
fights (capped), counts completed keys, and compares with the export's runs that
started in the same hour. Also prints per-report the fights the export has vs lacks.
Cost: ~1 pt per page + ~1 pt per report; capped by REPORT_CAP.
"""
import gzip, json, os, pathlib, sys, time, collections
import pandas as pd
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from wcl_client import WCLClient
START = os.environ.get("CRAWL_START", "2026-09-06T15:00:00Z")
HOURS = float(os.environ.get("CRAWL_HOURS", "1"))
REPORT_CAP = int(os.environ.get("REPORT_CAP", "150"))
t0 = int(pd.Timestamp(START).timestamp() * 1000); t1 = int(t0 + HOURS * 3600_000)
client = WCLClient(verbose=False)
reports = []; page = 1
while True:
    q = ('{ reportData { reports(zoneID: 55, startTime: %d, endTime: %d, limit: 100, page: %d) '
         '{ has_more_pages current_page total data { code title startTime endTime owner { name } region { slug } } } } }' % (t0, t1, page))
    data = client.query(q, est_cost=1.5)
    rs = ((data or {}).get("reportData") or {}).get("reports") or {}
    reports += rs.get("data") or []
    print(f"[crawl] page {page}: {len(rs.get('data') or [])} reports, total={rs.get('total')} more={rs.get('has_more_pages')}", flush=True)
    if not rs.get("has_more_pages") or page >= 20: break
    page += 1
print(f"[crawl] {len(reports)} public reports overlapping {START} +{HOURS}h (WCL total {rs.get('total')})")
# fights per report (aliased, 10 per request)
fights = {}
sample = reports[:REPORT_CAP]
for i in range(0, len(sample), 10):
    batch = sample[i:i + 10]
    parts = [f'r{j}: report(code: "{r["code"]}") {{ code fights(killType: Encounters) {{ id keystoneLevel keystoneTime kill startTime encounterID }} }}' for j, r in enumerate(batch)]
    data = client.query("{ reportData { " + " ".join(parts) + " } }", est_cost=1.0 * len(batch))
    rd = (data or {}).get("reportData") or {}
    for j, r in enumerate(batch):
        rep = rd.get(f"r{j}") or {}
        fights[r["code"]] = [(f, r) for f in (rep.get("fights") or []) if f.get("keystoneLevel")]
print(f"[crawl] fights fetched for {len(fights)} of {len(reports)} reports ({client.spent:.0f} pts spent)")
# export side
df = pd.read_csv(ROOT / "data" / "mythic_runs.csv.gz", usecols=["report_code", "fight_id", "key_level", "started_at"])
have = set(zip(df.report_code, df.fight_id))
runs_in_hour = df.drop_duplicates(["report_code", "fight_id"])
runs_in_hour = runs_in_hour[(runs_in_hour.started_at >= t0) & (runs_in_hour.started_at < t1)]
completed = []; by_key = collections.Counter(); have_by_key = collections.Counter(); in_window = 0
for code, fl in fights.items():
    for f, r in fl:
        abs_start = r["startTime"] + f["startTime"]
        if not (t0 <= abs_start < t1): continue
        in_window += 1
        if f.get("kill"):
            completed.append((code, f["id"], f["keystoneLevel"]))
            by_key[f["keystoneLevel"]] += 1
            if (code, f["id"]) in have: have_by_key[f["keystoneLevel"]] += 1
print(f"\n== sample hour {START} +{HOURS}h: {len(fights)} reports inspected -> {in_window} keystone fights started in the hour, "
      f"{len(completed)} completed; the export holds {sum(have_by_key.values())} of those completed ({100*sum(have_by_key.values())/max(1,len(completed)):.0f}%)")
print(f"   export runs (any report) started in the hour: {len(runs_in_hour)}")
print("   by key level (completed on WCL / in export):")
for k in sorted(by_key):
    print(f"     +{k:<2} {by_key[k]:4d} / {have_by_key[k]:4d}  ({100*have_by_key[k]/by_key[k]:.0f}%)")
scale = (rs.get("total") or len(reports)) / max(1, len(fights))
print(f"\n   scaled to all {rs.get('total')} reports in the hour: ~{len(completed)*scale:.0f} completed public keys/hour vs the sweep's {len(runs_in_hour)} -> "
      f"the leaderboard sweep sees ~{100*len(runs_in_hour)/max(1,len(completed)*scale):.0f}% of public completed runs in this hour")
