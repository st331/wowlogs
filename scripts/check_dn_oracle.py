#!/usr/bin/env python3
"""The figures the page PRINTS, checked against the payload: step 2 of 2 (see check_dn_oracle.js).

  python3 scripts/check_dn_oracle.py <data.json.gz> <dump.json>

Recomputes, from the payload and the rule alone, everything the page computed for every spec in every view
the browser step walked: par per role x key level x dungeon (median of the rows the window keeps, counted
only with 30+ parses and a 95% bracket within +-4%), the factors (mean par of the dungeons in view / own
dungeon's par, nearest full level, a tie goes down, a role with no full level as logged), then the adjusted
and the logged average and percentiles, average key, dungeon mix, the flag (largest share, as printed, at
least 2.5x an even split, never under 35% nor over 80%), the there / elsewhere split and,
in the Archon replica, the key floor, the kept share and the every-dungeon pool. No page code is used.
Exits 1 on any mismatch.
"""
import collections, gzip, json, math, sys
if len(sys.argv) != 3: sys.exit("usage: python3 scripts/check_dn_oracle.py <data.json.gz> <dump.json>")
D = json.load(gzip.open(sys.argv[1])); R = D["rows"]; N = len(R["dps"])
X = json.load(open(sys.argv[2]))
if X["n"] != N or X["built"] != D.get("built"):
    sys.exit(f'the dump was written from another payload (dump: {X["n"]} rows built {X["built"]}; file: {N} rows built {D.get("built")})')
ND, NR, K = len(D["dungeons"]), len(D["roles"]), 40
dps, key, dun, role, cls, spec, hero, day, reg = (R[k] for k in ("dps", "key", "dun", "role", "cls", "spec", "hero", "day", "reg"))
outw = set(X["outw"]); E = X["elite"]
def med(a):
    n = len(a); return a[(n - 1) // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2
def qp(a, f):
    x = (len(a) - 1) * f; i = math.floor(x)
    return a[i] + (x - i) * (a[i + 1] - a[i]) if i + 1 < len(a) else a[i]
cells = collections.defaultdict(list)
for i in range(N):
    if i not in outw: cells[(role[i], min(key[i], K - 1), dun[i])].append(dps[i])
par, known = {}, {}
for c, a in cells.items():
    a.sort(); n = len(a); m = med(a); par[c] = m; h = 0.98 * math.sqrt(n)
    known[c] = n >= 30 and m > 0 and m - a[max(0, math.floor(n / 2 - h))] <= 0.04 * m and a[min(n - 1, math.ceil(n / 2 + h))] - m <= 0.04 * m
def factors(S0):
    S = [d for d in S0 if any(known.get((r, k, d)) for r in range(NR) for k in range(K))]
    F = {}; anyr = [False] * NR
    if len(S) < 2: return S, F, anyr
    for r in range(NR):
        full = [k for k in range(K) if all(known.get((r, k, d), False) for d in S)]
        anyr[r] = bool(full)
        if not full: continue
        for k in range(K):
            if not any((r, k, d) in cells for d in S): continue
            use = min(full, key=lambda g: (abs(g - k), g))          # nearest full level; a tie goes down
            bar = sum(par[(r, use, d)] for d in S) / len(S)
            for d in S: F[(r, k, d)] = bar / par[(r, use, d)]
    return S, F, anyr
bad = 0; checked = 0
def chk(view, who, name, page, exp, tol):
    global bad, checked
    checked += 1
    if page is None and exp is None: return
    if page is None or exp is None or abs(page - exp) > tol:
        bad += 1; print(f"   MISMATCH [{view}] {who} {name}: page {page} oracle {exp}")
def elite_groups(v):
    """The replica's recipe: the last ELITE_DAYS days, per spec the top ELITE_KEEP of key levels, ELITE_MIN+ kept parses."""
    S = set(v["dungeons"]); roles = {D["roles"].index(r) for r in v["roles"]} or set(range(NR))
    regs = {D["regions"].index(r) for r in v["regs"]} or set(range(len(D["regions"])))
    day_lo = max(day) - (E["days"] - 1)
    el = [i for i in range(N) if day[i] >= day_lo and dun[i] in S and role[i] in roles and reg[i] in regs and i not in outw]
    hist = collections.defaultdict(collections.Counter)
    for i in el: hist[cls[i] * 100 + spec[i]][min(key[i], K - 1)] += 1
    floor = {}
    for g, h in hist.items():
        n = sum(h.values())
        if n < E["min"]: continue
        cum = 0
        for lv in range(K - 1, -1, -1):
            cum += h.get(lv, 0)
            if cum >= E["keep"] * n: floor[g] = lv; break
    kept = collections.defaultdict(list)
    for i in el:
        g = cls[i] * 100 + spec[i]
        if g in floor and key[i] >= floor[g]: kept[g].append(i)
    return {g: r for g, r in kept.items() if len(r) >= E["min"]}, floor, {g: sum(h.values()) for g, h in hist.items()}
for v in X["views"]:
    S, F, anyr = factors(v["dungeons"]); on = len(S) > 1; inS = set(S)
    roles_in_view = [D["roles"].index(r) for r in v["roles"]] or list(range(NR))
    dn_on = on and any(anyr[r] for r in roles_in_view)
    if dn_on != v["dnOn"]: bad += 1; print(f'   MISMATCH [{v["name"]}] adjusting: page {v["dnOn"]} oracle {dn_on}')
    hot_at = min(0.8, max(0.35, 2.5 / len(v["dungeons"]))) if on else None      # an even split counts every dungeon in view
    pct = lambda x: math.floor(100 * x + 0.5)                                      # the share as printed (Math.round)
    p = v["pctl"] / 100
    fa = lambda i: F.get((role[i], min(key[i], K - 1), dun[i]), 1.0)
    # the replica's pool: the adjusted parse once at every dungeon's par; a parse from a left-out dungeon as logged
    spread = lambda rows: sorted(x for i in rows for x in ([dps[i] * fa(i) / F.get((role[i], min(key[i], K - 1), d), 1.0) for d in S]
                                                             if dun[i] in inS else [dps[i]] * len(S)))
    nspec = nhot = nsplit = 0
    for sname, side in v["sides"].items():
        if v["elite"]: groups, floors, pools = elite_groups(v)
        else:
            groups = collections.defaultdict(list)
            for i in side["rows"]:
                groups[(cls[i] * 100 + spec[i]) if v["merge"] else (cls[i] * 100 + spec[i]) * 200 + hero[i]].append(i)
        if len(groups) != len(side["groups"]): bad += 1; print(f'   MISMATCH [{v["name"]}] {sname}: page {len(side["groups"])} specs, oracle {len(groups)}')
        for g in side["groups"]:
            ix = groups.get(g["k"]); who = f'{sname} {g["cls"]} {g["spec"]} {g["hero"] or ""}'.strip(); nspec += 1
            if not ix: bad += 1; print(f'   MISSING [{v["name"]}] {who}'); continue
            adjd = dn_on and anyr[role[ix[0]]]                      # this spec's figures are adjusted at all
            adj = sorted(dps[i] * fa(i) for i in ix); log = sorted(dps[i] for i in ix)
            pool = spread(ix) if v["elite"] and dn_on else adj
            tol = lambda x: max(1.0, 3e-6 * abs(x))                  # the page keeps its factors in Float32
            chk(v["name"], who, "parses", g["n"], len(ix), 0)
            chk(v["name"], who, "average", g["avg"], sum(adj) / len(adj), tol(g["avg"]))
            chk(v["name"], who, "lens percentile", g["med"], qp(pool, p), tol(g["med"]))
            chk(v["name"], who, "p30", g["q30"], qp(pool, .30), tol(g["q30"]))
            chk(v["name"], who, "p85", g["q85"], qp(pool, .85), tol(g["q85"]))
            chk(v["name"], who, "second lens", g["qb"], qp(pool, v["pctlB"] / 100), tol(g["qb"]))
            if bool(g["adj"]) != bool(adjd): bad += 1; print(f'   MISMATCH [{v["name"]}] {who} adjusted mark: page {g["adj"]} oracle {adjd}')
            chk(v["name"], who, "logged average", g["lavg"], sum(log) / len(log) if adjd else None, 1e-6 * sum(log) / len(log) + 0.01)
            chk(v["name"], who, "logged percentile", g["lmed"], qp(log, p) if adjd else None, 0.01)
            chk(v["name"], who, "average key", g["akey"], sum(key[i] for i in ix) / len(ix), 1e-9)
            cnt = collections.Counter(dun[i] for i in ix); top = max(range(ND), key=lambda d: (cnt.get(d, 0), -d))
            chk(v["name"], who, "top dungeon", g["dtop"], top, 0)
            chk(v["name"], who, "top dungeon share", g["dshare"], cnt[top] / len(ix), 1e-12)
            hot = bool(on and pct(cnt[top] / len(ix)) >= pct(hot_at))                 # tested on the printed share
            if hot != g["hot"]: bad += 1; print(f'   MISMATCH [{v["name"]}] {who} flag: page {g["hot"]} oracle {hot}')
            checked += 2; nhot += hot
            ein = eout = None; nin = nout = 0
            if adjd and hot:
                a = [i for i in ix if dun[i] == top]; b = [i for i in ix if dun[i] != top]; nin, nout = len(a), len(b)
                sp = spread if v["elite"] else (lambda rows: sorted(dps[i] * fa(i) for i in rows))
                if a: ein = qp(sp(a), p)
                if b: eout = qp(sp(b), p)
                nsplit += 1
            chk(v["name"], who, "there", g["din"], ein, tol(ein or 0)); chk(v["name"], who, "elsewhere", g["dout"], eout, tol(eout or 0))
            chk(v["name"], who, "parses there", g["nin"], nin, 0); chk(v["name"], who, "parses elsewhere", g["nout"], nout, 0)
            if v["elite"]:
                chk(v["name"], who, "key floor", g["floorK"], floors[g["k"]], 0)
                chk(v["name"], who, "spec's parses in the window", g["kpool"], pools[g["k"]], 0)
                chk(v["name"], who, "share kept", g["kshare"], len(ix) / pools[g["k"]], 1e-12)
    print(f'{v["name"]}: {len(S)} dungeons in the average, adjusting {dn_on}; {nspec} specs, {nhot} flagged, {nsplit} with a split')
print(f"ORACLE: {checked} values checked, {bad} mismatches (payload built {D.get('built')}, {N} rows)")
sys.exit(1 if bad else 0)
