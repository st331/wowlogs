"""The tracked-trinket table, shared by the collector (fetch_procs.py), the
builder's sidecar (build_site_data.procs_sidecar) and their tests, so the
three can never disagree about which aura marks which trinket.

One entry per tracked trinket. `buff` is the aura whose bands mark both the
beam spawn (band start) and the wearer's time inside it; `window_ms` is the
beam's lifetime from spawn. Adding a trinket = one more entry, once its log
signature has been established the way fetch_procs.py's docstring describes
for Lightspire Core.
"""
TRACKED = [
    {"key": "lscore", "item": 250214, "name": "Lightspire Core",
     "buff": 1263768, "buff_name": "Light's Blessing", "window_ms": 12_000},
]
