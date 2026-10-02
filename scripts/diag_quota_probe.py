#!/usr/bin/env python3
"""One rateLimitData query with the collectors' own client (about 1 point),
to tell whether the collectors and the vetting site draw on the same hourly
quota. Prints the HTTP status, Retry-After and the reading; prints whether
the collectors' client id is the site's public one, never the id itself."""
import datetime
import os

import requests

SITE_CLIENT_ID = "019f5e9a-b2bd-71fd-957f-8dae7ba58c5b"   # public, in the site's config.js
cid, sec = os.environ["WCL_CLIENT_ID"], os.environ["WCL_CLIENT_SECRET"]
print("collectors' client is the site's client:", "YES" if cid.strip() == SITE_CLIENT_ID else "NO")
tok = requests.post("https://www.warcraftlogs.com/oauth/token", data={"grant_type": "client_credentials"},
                    auth=(cid, sec), timeout=30).json()["access_token"]
for i in range(2):
    r = requests.post("https://www.warcraftlogs.com/api/v2/client", timeout=30,
                      headers={"Authorization": f"Bearer {tok}"},
                      json={"query": "{rateLimitData{limitPerHour pointsSpentThisHour pointsResetIn}}"})
    now = datetime.datetime.utcnow().strftime("%H:%M:%SZ")
    print(f"probe {i + 1} at {now}: HTTP {r.status_code} retry-after={r.headers.get('Retry-After')} body={r.text[:200]}")
