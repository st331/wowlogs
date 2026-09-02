#!/usr/bin/env python3
"""make_eq_fixture -- the equivalence fixture of partitioned_payload.md §9.

    python scripts/make_eq_fixture.py --out data/processed/fixtures/eq [--runs-per-day 300] [--seed 1]

Cuts three reset weeks plus two days (buckets 0–2, the row window) out of the
committed CSV's shape -- rows are SYNTHESISED from that CSV's marginals
(class/spec/hero mix, dungeons, key levels, regions, DPS per spec, deaths,
keystone clocks, medals), because the real file is shorter than the fixture
-- with matching gear / abilities / rankings journals built through the real
collector (`fetch_data.parse_summary`, the record shapes
tests/test_builds_sidecar.py uses), and every edge case §9 lists:

  * a region-boundary day (US Tuesday 15:00 / EU Wednesday 04:00 both fall
    inside a UTC day of the window; buckets differ by the hour);
  * ONE duplicate-upload pair whose copies straddle UTC midnight and whose
    smaller-code copy arrives in a later chunk than its twin (chunk 3, after
    the twin's day is frozen) -- the export's signature collapse keeps the
    smaller code, so the frozen neighbour day loses a run;
  * one same-spec-twice run and one six-member roster (both clocked, week b3);
  * clock-less runs, 15% of runs, spread uniformly so many comp cells mix
    clocked and unclocked runs;
  * one tuning cutoff inside a week (bucket 1, per-region instants);
  * one character whose only runs are in the oldest week and who is
    registered LAST (its run is the final record of the final players chunk);
  * a fourth, cube-served week (bucket 3 = absolute week 33) so mixed periods
    exist, and a fifth week older than the window (bucket 4 = week 32) whose
    cube is deliberately withheld -- the cube gap, row-served.

Journals arrive in EIGHT chunks (§9.1 test_incremental_idempotent), rankings
as a FULL snapshot per chunk:
  1  days 229–243 (weeks b4, b3, part of b2)
  2  days 244–253 (rest of b2, most of b1)
  3  1% of chunk 1's rows re-sent with changed gear + one late upload into
     the frozen day 231 + the second (smaller-code) copy of the midnight pair
  4  days 254–257, in which Paladins start wearing a strictly higher set id
     (the tier-set upgrade) and Sunfury Arcane Mages gain a new marker
     ability (the learned-table upgrade)
  5  no journal rows; a rankings snapshot from which 30% of runs have
     dropped off the pages, no triple changed
  6  no journal rows; a rankings snapshot in which 50 runs in frozen days
     gain a medal (none -> gold, score revised)
  7  days 258–259 (the two days of the current reset) and, last of all, the
     late character's single run in day 230
  8  nothing (the test replaces the git mirror of season_pins.json)

Written under --out:
  fixture.json                 everything a test needs to know (see FIXTURE)
  chunks/NN/players.jsonl      append part of the players journal (chunk NN)
  chunks/NN/gear.jsonl         append part of the gear journal
  chunks/NN/abilities.jsonl    append part of the abilities journal
  chunks/NN/rankings.jsonl     the FULL rankings snapshot after chunk NN
  chunks/NN/now.txt            the frozen clock (WOWLOGS_NOW) for that run
  legacy/mythic_runs.csv.gz    what fetch_data.export() produces from all
                               chunks (dedup keep=last, keystone overlay,
                               signature collapse, score/medal overlay)
  legacy/keystone_times.json   the accumulated keystone-clock map
  legacy/rio_scores.csv.gz     Raider.IO scores (60% of characters rated)
  tuning_patches.json          the one patch with the mid-week cutoff
  pins.json                    tier sets / hero markers / tuning items as
                               WOWLOGS_PINS injects them into the legacy path
  payload.json.gz              the legacy-shaped payload of the CSV (the
                               oracle input of test_sitecalc_matches_js)
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_data as fd                                          # noqa: E402
from hero_from_abilities import HeroResolver                     # noqa: E402
import sitecalc as sc                                            # noqa: E402

SEASON = json.loads((ROOT / "data" / "season.json").read_text())
EPOCH = SEASON["epoch"]
EPOCH_MS = sc.parse_iso_ms(EPOCH + "T00:00:00Z")
DAY_MS = 86_400_000
NOW_ISO = "2026-09-17T14:20:00Z"          # Thursday: 2 days into the US reset
NOW_MS = sc.parse_iso_ms(NOW_ISO)
FIRST_DAY, LAST_DAY = 229, 259            # 2026-08-18 .. 2026-09-17
CUBE_WEEK, GAP_WEEK = 33, 32              # absolute weeks (§3.1): b3 cubed, b4 withheld
KEYSTONE_PARS = json.loads((ROOT / "data" / "keystone_pars.json").read_text())
ENC_BY_NAME = {v: int(k) for k, v in SEASON["encounters"].items()}
CLASSES = [c for c in SEASON["vocab"]["classes"] if c != "Unknown"]
TIER_SET = {c: 2055 + i for i, c in enumerate(CLASSES)}
OLD_SET = {c: 1978 + i for i, c in enumerate(CLASSES)}
NEW_SET = {c: 2080 + i for i, c in enumerate(CLASSES)}   # the chunk-4 upgrade (Paladin)
ESLOTS = [0, 4, 6, 7, 8, 10, 11, 14, 15, 16]              # §4.2 pinned enchant slots
STATS = ("Intellect", "Agility", "Strength", "Crit", "Haste", "Mastery",
         "Versatility", "Leech", "Avoidance", "Speed")
PRIMARY = {"Mage": "Intellect", "Warlock": "Intellect", "Priest": "Intellect",
           "Druid": "Intellect", "Shaman": "Intellect", "Evoker": "Intellect",
           "Paladin": "Strength", "Warrior": "Strength", "DeathKnight": "Strength",
           "Hunter": "Agility", "Rogue": "Agility", "Monk": "Agility", "DemonHunter": "Agility"}
TUNING_PATCH = {
    "label": "Sep 9 class tuning", "date": "2026-09-09",
    "note": "fixture: a cutoff inside week b1, per region",
    "regions": {"US": "2026-09-09T10:00:00Z", "EU": "2026-09-09T02:00:00Z",
                "default": "2026-09-09T06:00:00Z"},
}
RESET_RULES_ALL = {k: tuple(v) for k, v in SEASON["reset_rules"].items()}


def region_rule(reg):
    return RESET_RULES_ALL.get(reg, RESET_RULES_ALL["*"])


# ------------------------------------------------------------- marginals
class Marginals:
    """Distributions measured on the committed seed CSV, or the built-in
    fallback when it is absent. Everything the generator draws comes from
    here, so the fixture has the CSV's shape at any scale."""

    def __init__(self, csv_path: pathlib.Path | None):
        self.specs = []          # [(class, spec, role, weight)]
        self.heroes = {}         # (class, spec) -> [(hero, weight)]
        self.dungeons = []       # [(name, weight)]
        self.keys = []           # [(key, weight)]
        self.regions = []        # [(reg, weight)]
        self.dps_by_spec = {}    # (class, spec) -> (log-mean, log-sd) at key 12
        self.deaths = []         # [(deaths, weight)]
        self.dur_by_dun = {}     # dungeon -> (mean, sd) keystone seconds
        self.timed_by_key = {}   # key -> P(timed)
        if csv_path and csv_path.exists():
            self._from_csv(csv_path)
        else:
            self._fallback()

    def _from_csv(self, path):
        cols = ["class", "spec", "hero_talent", "role", "dungeon", "key_level",
                "region", "dps", "deaths", "keystone_s", "medal"]
        df = pd.read_csv(path, usecols=cols)
        df = df[df["class"].isin(CLASSES) & df["spec"].isin(SEASON["vocab"]["specs"])
                & df["role"].isin(["Tank", "Healer", "DPS"])]
        g = df.groupby(["class", "spec", "role"]).size()
        V = SEASON["vocab"]
        pairs = {(V["classes"][c], V["specs"][s]) for c, s in SEASON["spec_pairs"]}
        for (c, s, r), n in g.items():
            if (c, s) not in pairs:
                continue
            self.specs.append((c, s, r, int(n)))
        hg = df.groupby(["class", "spec", "hero_talent"]).size()
        for (c, s, h), n in hg.items():
            if h == "Unknown":
                continue
            self.heroes.setdefault((c, s), []).append((h, int(n)))
        self.dungeons = [(d, int(n)) for d, n in df["dungeon"].value_counts().items()
                         if d in KEYSTONE_PARS]
        self.keys = [(int(k), int(n)) for k, n in df["key_level"].value_counts().sort_index().items()]
        self.regions = [(r, int(n)) for r, n in df["region"].value_counts().items()
                        if r in SEASON["vocab"]["regions"] and r != "Unknown"]
        ok = df["dps"] > 0
        ld = np.log(df.loc[ok, "dps"])
        # DPS ~ lognormal per spec; keys shift it ~2%/level around key 12
        adj = ld - 0.02 * (df.loc[ok, "key_level"] - 12)
        for (c, s), grp in adj.groupby([df.loc[ok, "class"], df.loc[ok, "spec"]]):
            self.dps_by_spec[(c, s)] = (float(grp.mean()), float(max(grp.std(), 0.05)))
        self.deaths = [(int(k), int(n)) for k, n in df["deaths"].value_counts().sort_index().items()]
        ks = df.dropna(subset=["keystone_s"])
        for d, grp in ks.groupby("dungeon")["keystone_s"]:
            if d in KEYSTONE_PARS:
                self.dur_by_dun[d] = (float(grp.mean()), float(grp.std()))
        timed = df["medal"].isin(["gold", "silver", "bronze", "timed"])
        for k, grp in timed.groupby(df["key_level"]):
            self.timed_by_key[int(k)] = float(grp.mean())
        if not self.specs:
            self._fallback()

    def _fallback(self):
        V = SEASON["vocab"]
        roles = SEASON["spec_role"]
        for c, s in SEASON["spec_pairs"]:
            cn, sn = V["classes"][c], V["specs"][s]
            self.specs.append((cn, sn, roles[s], 100))
        self.dungeons = [(d, 100) for d in KEYSTONE_PARS]
        self.keys = [(k, max(1, 40 - abs(k - 12) * 4)) for k in range(2, 20)]
        self.regions = [("EU", 47), ("US", 33), ("CN", 18), ("KR", 2), ("TW", 1)]
        for c, s, _, _ in self.specs:
            self.dps_by_spec[(c, s)] = (11.9, 0.35)
        self.deaths = [(0, 70), (1, 20), (2, 6), (3, 2), (4, 1), (5, 1)]
        for d in KEYSTONE_PARS:
            self.dur_by_dun[d] = (1380.0, 290.0)
        self.timed_by_key = {k: 0.97 - 0.02 * max(0, k - 10) for k in range(2, 20)}


def _choice(rng, items):
    """Weighted choice over [(value, weight)]."""
    vals = [v for v, _ in items]
    w = np.array([max(float(x), 0) for _, x in items])
    return vals[int(rng.choice(len(vals), p=w / w.sum()))]


# ------------------------------------------------------------- hero map
class HeroTalents:
    """Talent-tree entries that resolve to each hero through the REAL
    HeroResolver (data/hero_talent_map.json), so the players journal carries
    exactly what the collector would have written."""

    def __init__(self):
        m = json.loads((ROOT / "data" / "hero_talent_map.json").read_text())
        names = {int(k): v for k, v in m["subtree_names"].items()}
        self.entries = {}
        for e, sub in sorted(m["entry_to_subtree"].items(), key=lambda kv: int(kv[0])):
            self.entries.setdefault(names.get(int(sub)), []).append(int(e))
        self.resolver = fd.HeroResolver()

    def tree(self, hero, variant=0):
        ids = self.entries.get(hero)
        if not ids:
            return None
        picks = [ids[(variant + i) % len(ids)] for i in range(min(3, len(ids)))]
        return [{"id": e, "rank": 1} for e in picks] + [{"id": 900000 + variant % 5, "rank": 1}]


# ------------------------------------------------------------- generator
class Factory:
    """Synthetic runs with the CSV's marginals, emitted through
    fetch_data.parse_summary so every journal record has the collector's
    shape. Deterministic under its seed."""

    def __init__(self, marg: Marginals, seed: int = 1):
        self.m = marg
        self.rng = np.random.default_rng(seed)
        self.hero = HeroTalents()
        self.run_seq = 0
        self.by_role = {}
        for c, s, r, w in marg.specs:
            self.by_role.setdefault(r, []).append(((c, s), w))
        self.char_pool = {}      # (class, spec) -> list of (name, server, region)
        self.item_pool = {slot: [200000 + slot * 100 + i for i in range(30)] for slot in range(16)}

    # -- identity ----------------------------------------------------------
    def character(self, cs, reg):
        pool = self.char_pool.setdefault((cs, reg), [])
        # a heavy-tailed population: a few characters log a lot
        if pool and self.rng.random() < 0.6:
            return pool[int(self.rng.integers(0, len(pool)))]
        n = len(pool)
        name = f"{cs[1][:4]}{cs[0][:3]}{reg}{n:04d}"
        server = f"Realm{int(self.rng.integers(0, 12)):02d}"
        ch = (name, server, reg)
        pool.append(ch)
        return ch

    def code(self):
        self.run_seq += 1
        h = hashlib.md5(f"run{self.run_seq}".encode()).hexdigest()[:12]
        return f"R{self.run_seq:05d}{h}"[:16]

    # -- combatant material -----------------------------------------------
    def gear(self, cls, tier_kind, variant, item_shift=0):
        """16 slots; tier_kind in {'4pc','2pc','old','none'}; set pieces on
        slots 0, 2, 4, 6, 9 (head, shoulder, chest, hands, legs)."""
        set_id = {"4pc": TIER_SET[cls], "2pc": TIER_SET[cls], "old": OLD_SET[cls],
                  "new": NEW_SET[cls]}.get(tier_kind)
        n_set = {"4pc": 4 if variant % 3 else 5, "2pc": 2 if variant % 2 else 3,
                 "old": 4, "new": 4, "none": 0}[tier_kind]
        tier_slots = [0, 2, 4, 6, 9][:n_set]
        out = []
        for slot in range(16):
            if slot == 3:                       # shirt: always empty
                out.append({"id": 0})
                continue
            pool = self.item_pool[slot]
            it = {"id": pool[(variant + item_shift + slot) % len(pool)],
                  "itemLevel": 700 + (variant % 30), "quality": 4}
            if slot in tier_slots:
                it["setID"] = set_id
                it["id"] = 300000 + set_id * 10 + slot
            if slot in ESLOTS and variant % 4 != 1:
                it["permanentEnchant"] = 7000 + slot * 10 + (variant % 3)
            if slot == 8 and variant % 5 == 0:
                it["bonusIDs"] = [8960, 12001]
            if slot == 15 and variant % 7 == 0:
                it["bonusIDs"] = [8960, 13001]
            out.append(it)
        return out

    def stats(self, cls, variant):
        st = {PRIMARY[cls]: {"min": 80000 + variant * 7 % 9000},
              "Crit": {"min": 9000 + variant * 13 % 5000}, "Haste": {"min": 12000 + variant * 17 % 6000},
              "Mastery": {"min": 15000 + variant * 19 % 7000},
              "Versatility": {"min": 8000 + variant * 23 % 4000}}
        if variant % 3 == 0:
            st["Leech"] = {"min": 2000 + variant % 900}
        return st

    def abilities(self, cls, spec, hero, chunk_tag):
        """Three spec lines, one hero marker, one cross-class item proc."""
        base = [{"guid": 100000 + hash((cls, spec, i)) % 5000, "name": f"{spec} Strike {i}",
                 "total": int(self.rng.integers(2_000_000, 9_000_000)), "uses": int(self.rng.integers(20, 90))}
                for i in range(3)]
        if hero and hero != "Unknown":
            base.append({"guid": 200000 + hash(hero) % 5000, "name": f"{hero} Marker",
                         "total": int(self.rng.integers(500_000, 3_000_000)), "uses": 12})
            if hero == "Sunfury" and spec == "Arcane" and chunk_tag >= 4:
                base.append({"guid": 200999, "name": "Sunfury Nova",
                             "total": int(self.rng.integers(300_000, 900_000)), "uses": 6})
        if self.rng.random() < 0.5:
            base.append({"guid": 300001, "name": "Ravenous Swarm",
                         "total": int(self.rng.integers(100_000, 700_000)), "uses": 9})
        return base

    # -- one run ------------------------------------------------------------
    def run(self, day: int, *, reg=None, start_ms=None, roster=None, clocked=None,
            code=None, fid=1, chunk_tag=1, tier_override=None, same_spec_twice=False,
            six=False, forced_chars=None):
        rng, m = self.rng, self.m
        reg = reg or _choice(rng, m.regions)
        dun = _choice(rng, m.dungeons)
        key = _choice(rng, m.keys)
        if start_ms is None:
            # hours peak in the evening of the region; uniform is close enough
            start_ms = EPOCH_MS + day * DAY_MS + int(rng.integers(0, DAY_MS))
        mu, sd = m.dur_by_dun[dun]
        kdur_s = max(500.0, float(rng.normal(mu, sd)))
        p_timed = m.timed_by_key.get(key, 0.9)
        par = KEYSTONE_PARS[dun]
        timed = rng.random() < p_timed
        if timed:
            kdur_s = min(kdur_s, par - float(rng.uniform(5, 400)))
        else:
            kdur_s = max(kdur_s, par + float(rng.uniform(5, 300)))
        kdur_s = round(kdur_s, 1)
        combat_s = round(kdur_s - float(rng.uniform(10, 45)), 1)
        if clocked is None:
            clocked = rng.random() >= 0.15
        medal = ("gold" if rng.random() < 0.3 else "silver" if rng.random() < 0.5 else "bronze") if timed else "none"
        score = round(float(rng.uniform(150, 420)), 5)
        if roster is None:
            roster = [_choice(rng, self.by_role["Tank"]), _choice(rng, self.by_role["Healer"])]
            roster += [_choice(rng, self.by_role["DPS"]) for _ in range(3)]
            if same_spec_twice:
                roster[3] = roster[2]
            if six:
                roster.append(_choice(rng, self.by_role["DPS"]))
        code = code or self.code()
        fight = {"code": code, "fid": fid, "dungeon": dun, "key_level": int(key),
                 "region": reg, "score": score, "medal": medal,
                 "affixes": [9, 10, 162] if key >= 10 else [9, 10],
                 "start_time": int(start_ms),
                 "rank_duration_ms": int(round(kdur_s * 1000)) if clocked else None}
        details = {"tanks": [], "healers": [], "dps": []}
        damage, deaths_ev, gear_recs_extra = [], [], []
        abil_recs = []
        pid = 0
        roles_of = {}
        for c, s, r, _ in m.specs:
            roles_of[(c, s)] = r
        for k, cs in enumerate(roster):
            pid += 1
            cls, spec = cs
            role = roles_of[cs]
            if forced_chars and k < len(forced_chars):
                name, server, creg = forced_chars[k]
            else:
                name, server, creg = self.character(cs, reg)
            heroes = m.heroes.get(cs) or []
            hero = _choice(rng, heroes) if heroes else "Unknown"
            unknown_hero = rng.random() < 0.03
            variant = int(rng.integers(0, 10_000))
            ci = {"specID": 1000 + hash(cs) % 900}
            has_gear = rng.random() < 0.6
            if has_gear:
                if tier_override and cls in tier_override:
                    tk = tier_override[cls]
                else:
                    x = rng.random()
                    tk = "4pc" if x < 0.55 else "2pc" if x < 0.70 else "old" if x < 0.90 else "none"
                ci["gear"] = self.gear(cls, tk, variant)
                ci["stats"] = self.stats(cls, variant)
            if not unknown_hero:
                tree = self.hero.tree(hero, variant)
                if tree:
                    ci["talentTree"] = tree
            if variant % 9 == 0:
                ci["talentImportString"] = f"IMPORT_{cls}_{spec}_{variant % 3}"
            details[{"Tank": "tanks", "Healer": "healers", "DPS": "dps"}[role]].append(
                {"id": pid, "name": name, "server": server, "type": cls, "specs": [spec],
                 "icon": f"{cls}-{spec}", "maxItemLevel": 700 + variant % 30,
                 "combatantInfo": ci, "region": creg if creg != reg else None})
            lm, ls = m.dps_by_spec.get(cs, (11.9, 0.35))
            dps = float(np.exp(rng.normal(lm + 0.02 * (key - 12), ls)))
            if role == "Tank":
                dps *= 0.55
            elif role == "Healer":
                dps *= 0.12
            damage.append({"id": pid, "total": int(dps * kdur_s)})
            nd = _choice(rng, m.deaths)
            deaths_ev.extend({"id": pid} for _ in range(nd))
            if rng.random() < 0.8:
                sets = {}
                for it in ci.get("gear") or []:
                    if it.get("setID"):
                        sets[str(it["setID"])] = sets.get(str(it["setID"]), 0) + 1
                abil_recs.append({"report_code": code, "fight_id": fid, "actor_id": pid,
                                  "name": name, "class": cls, "total": int(dps * kdur_s),
                                  "ilvl": 700 + variant % 30, "sets": sets,
                                  "abilities": self.abilities(cls, spec, None if unknown_hero and rng.random() < 0.5 else hero, chunk_tag)})
        table = {"data": {"totalTime": int(round(combat_s * 1000)),
                          "playerDetails": details, "damageDone": damage,
                          "deathEvents": deaths_ev}}
        # region on the player detail is not something WCL returns; parse_summary
        # falls back to the fight's region, which is what we want
        for lst in details.values():
            for p in lst:
                p.pop("region", None)
        rows, gear_rows = fd.parse_summary(fight, table, self.hero.resolver)
        ranking = {"report": {"code": code, "fightID": fid},
                   "server": {"region": reg} if rng.random() < 0.34 else {},
                   "bracketData": int(key), "score": score, "medal": medal,
                   "affixes": fight["affixes"], "startTime": int(start_ms),
                   "name": rows[0]["character"], "class": rows[0]["class"], "spec": rows[0]["spec"]}
        if clocked:
            ranking["duration"] = int(round(kdur_s * 1000))
        return {"code": code, "fid": fid, "day": day, "reg": reg, "dun": dun, "key": int(key),
                "start_ms": int(start_ms), "kdur_s": kdur_s if clocked else None,
                "clocked": bool(clocked), "rows": rows, "gear": gear_rows,
                "abil": abil_recs, "ranking": ranking, "enc": ENC_BY_NAME[dun],
                "roster": roster, "medal": medal, "score": score}


# ------------------------------------------------------------- the fixture
def day_ms(day):
    return EPOCH_MS + day * DAY_MS


def region_window_start(reg):
    """The start instant of that region's bucket 4 (the oldest generated
    week) under NOW."""
    return sc.week_of(NOW_MS, reg, EPOCH, sc.RESET_RULES, sc.RESET_DEFAULT) - 4


def build_fixture(out: pathlib.Path, runs_per_day: int = 300, seed: int = 1,
                  csv_path: pathlib.Path | None = None) -> dict:
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    marg = Marginals(csv_path if csv_path is not None else ROOT / "data" / "mythic_runs.csv.gz")
    f = Factory(marg, seed)
    rng = f.rng
    runs: list[dict] = []          # every generated run, in generation order
    chunk_of: dict[str, int] = {}  # code -> chunk that carries it
    notes: dict = {"dup_pair": None, "same_spec_twice": None, "six_roster": None,
                   "late_upload": None, "late_character": None, "resent": [],
                   "medal_gain": [], "dropped_in_5": [], "clockless": 0,
                   "tier_upgrade_class": "Paladin", "tier_upgrade_set": NEW_SET["Paladin"],
                   "learned_upgrade": {"spec": "Arcane Mage", "hero": "Sunfury", "ability": "Sunfury Nova"}}

    def day_chunk(day):
        if day <= 243:
            return 1
        if day <= 253:
            return 2
        if day <= 257:
            return 4
        return 7

    # every region's rows begin at its own bucket-4 reset instant
    w4_start = {}
    for reg, _ in marg.regions:
        W = sc.week_of(NOW_MS, reg, EPOCH) - 4
        w4_start[reg] = sc.anchor_ms(reg, EPOCH) + W * sc.WEEK_MS
    for day in range(FIRST_DAY, LAST_DAY + 1):
        n = int(rng.poisson(runs_per_day))
        chunk = day_chunk(day)
        for _ in range(n):
            reg = _choice(rng, marg.regions)
            start = day_ms(day) + int(rng.integers(0, DAY_MS))
            if start < w4_start[reg] or start >= NOW_MS - 600_000:
                continue
            tier_override = None
            if chunk >= 4 and rng.random() < 0.3:
                tier_override = {"Paladin": "new"}
            r = f.run(day, reg=reg, start_ms=start, chunk_tag=chunk, tier_override=tier_override)
            runs.append(r)
            chunk_of[r["code"]] = chunk
            if not r["clocked"]:
                notes["clockless"] += 1
    # -- planted runs -----------------------------------------------------
    # same-spec-twice and six-member rosters, clocked, in the cube week (b3)
    r = f.run(238, reg="EU", start_ms=day_ms(238) + 40_000_000, clocked=True, same_spec_twice=True)
    runs.append(r); chunk_of[r["code"]] = 1
    notes["same_spec_twice"] = [r["code"], r["fid"]]
    r = f.run(239, reg="US", start_ms=day_ms(239) + 50_000_000, clocked=True, six=True)
    runs.append(r); chunk_of[r["code"]] = 1
    notes["six_roster"] = [r["code"], r["fid"]]
    # the midnight duplicate pair: twin A (larger code) at 23:59:40 of day 238
    # in chunk 1; copy B (smaller code) at 00:00:20 of day 239 in chunk 3.
    # Same dungeon/key/clock/roster -> export() keeps the smaller code.
    twin_start = day_ms(238) + DAY_MS - 20_000
    twin = f.run(238, reg="US", start_ms=twin_start, clocked=True, code="Zdup" + "9" * 12)
    runs.append(twin); chunk_of[twin["code"]] = 1
    chars = [(row["character"], row["server"], row["region"]) for row in twin["rows"]]
    seed_state = rng.bit_generator.state
    copy = f.run(239, reg="US", start_ms=twin_start + 40_000, clocked=True, code="Adup" + "0" * 12,
                 roster=twin["roster"], forced_chars=chars)
    rng.bit_generator.state = seed_state
    # force the copy to agree with the twin on everything the signature reads
    copy["dun"], copy["key"], copy["kdur_s"] = twin["dun"], twin["key"], twin["kdur_s"]
    copy["enc"], copy["medal"], copy["score"] = twin["enc"], twin["medal"], twin["score"]
    copy["ranking"]["duration"] = twin["ranking"]["duration"]
    copy["ranking"]["bracketData"] = twin["key"]
    copy["ranking"]["medal"], copy["ranking"]["score"] = twin["medal"], twin["score"]
    for row in copy["rows"]:
        row["dungeon"], row["key_level"] = twin["dun"], twin["key"]
        row["medal"], row["score"] = twin["medal"], twin["score"]
    runs.append(copy); chunk_of[copy["code"]] = 3
    notes["dup_pair"] = {"twin": [twin["code"], twin["fid"], 238], "copy": [copy["code"], copy["fid"], 239],
                         "survivor": copy["code"]}
    # a late upload into the frozen day 231 (chunk 3)
    late = f.run(231, reg="EU", start_ms=day_ms(231) + 30_000_000, clocked=True)
    runs.append(late); chunk_of[late["code"]] = 3
    notes["late_upload"] = [late["code"], late["fid"], 231]
    # the late character: only run in day 230, the last record of chunk 7
    lc = ("Latecomer", "RealmLate", "US")
    latec = f.run(230, reg="US", start_ms=day_ms(230) + 60_000_000, clocked=True,
                  forced_chars=[lc])
    runs.append(latec); chunk_of[latec["code"]] = 7
    notes["late_character"] = {"key": "Latecomer@RealmLate@US", "run": [latec["code"], latec["fid"], 230]}
    # -- chunk contents ----------------------------------------------------
    by_chunk = {k: [] for k in range(1, 9)}
    for r in runs:
        by_chunk[chunk_of[r["code"]]].append(r)
    # chunk 3 also re-sends 1% of chunk 1's rows with changed gear
    c1 = [r for r in by_chunk[1] if r["code"] not in (twin["code"],)]
    resend_n = max(1, len(c1) // 100)
    resend = [c1[int(i)] for i in rng.choice(len(c1), size=resend_n, replace=False)]
    resent_records = []
    for r in resend:
        rows2, gear2 = [], []
        for row, g in zip(r["rows"], r["gear"] + [None] * len(r["rows"])):
            rows2.append(dict(row))
        for g in r["gear"]:
            g2 = json.loads(json.dumps(g))
            if g2.get("gear"):
                for it in g2["gear"]:
                    if it and it.get("id") and not it.get("set"):
                        it["id"] += 7                # changed gear
            gear2.append(g2)
        resent_records.append((r, rows2, gear2))
        notes["resent"].append([r["code"], r["fid"]])
    # order inside a chunk: by start instant (arrival ~ time), planted last
    for k in by_chunk:
        by_chunk[k].sort(key=lambda r: (r["start_ms"], r["code"]))
    by_chunk[7] = [r for r in by_chunk[7] if r is not latec] + [latec]
    # rankings: chunk 5 drops 30% of runs (no triple changes); chunk 6 gives
    # 50 runs in frozen days (<= day 243) a medal
    all_codes_by_4 = [r for k in (1, 2, 3, 4) for r in by_chunk[k]]
    drop = set(int(i) for i in rng.choice(len(all_codes_by_4), size=int(0.3 * len(all_codes_by_4)), replace=False))
    notes["dropped_in_5"] = [[all_codes_by_4[i]["code"], all_codes_by_4[i]["fid"]] for i in sorted(drop)]
    frozen_none = [r for r in all_codes_by_4 if r["day"] <= 243 and r["medal"] == "none"]
    gain = [frozen_none[int(i)] for i in rng.choice(len(frozen_none), size=min(50, len(frozen_none)), replace=False)]
    gain_codes = {(r["code"], r["fid"]) for r in gain}
    notes["medal_gain"] = [[r["code"], r["fid"], r["day"]] for r in gain]
    # -- write chunks -------------------------------------------------------
    chunk_now = {1: day_ms(244) + 12 * 3_600_000, 2: day_ms(254) + 6 * 3_600_000,
                 3: day_ms(254) + 7 * 3_600_000, 4: day_ms(258) + 3_600_000,
                 5: day_ms(258) + 2 * 3_600_000, 6: day_ms(258) + 3 * 3_600_000,
                 7: NOW_MS, 8: NOW_MS + 1_200_000}
    known: list[dict] = []          # runs on the pages so far (snapshot content)
    chunk_meta = []
    for k in range(1, 9):
        d = out / "chunks" / f"{k:02d}"
        d.mkdir(parents=True, exist_ok=True)
        players, gear, abil = [], [], []
        for r in by_chunk[k]:
            players.extend(r["rows"])
            gear.extend(r["gear"])
            abil.extend(r["abil"])
        if k == 3:
            for r, rows2, gear2 in resent_records:
                players.extend(rows2)
                gear.extend(gear2)
        _write_jsonl(d / "players.jsonl", players)
        _write_jsonl(d / "gear.jsonl", gear)
        _write_jsonl(d / "abilities.jsonl", abil)
        known.extend(by_chunk[k])
        snapshot = known
        if k == 5:
            snapshot = [r for i, r in enumerate(known) if i not in drop]
        if k == 8:
            snapshot = known
        ranks = []
        for r in snapshot:
            rk = dict(r["ranking"])
            if k >= 6 and (r["code"], r["fid"]) in gain_codes:
                rk["medal"] = "gold"
                rk["score"] = round(r["score"] + 25.0, 5)
            ranks.append((r["enc"], r["key"], rk))
        _write_rankings(d / "rankings.jsonl", ranks)
        (d / "now.txt").write_text(_iso(chunk_now[k]) + "\n")
        chunk_meta.append({"chunk": k, "now": _iso(chunk_now[k]), "players": len(players),
                           "gear": len(gear), "abilities": len(abil), "runs": len(by_chunk[k]),
                           "rankings_runs": len(snapshot)})
    # -- the legacy truth: what export() writes from all chunks ----------------
    csv_rows, ks_map = legacy_export(out / "chunks", gain_codes, runs)
    legacy = out / "legacy"
    legacy.mkdir(exist_ok=True)
    df = pd.DataFrame(csv_rows, columns=CSV_COLUMNS)
    with gzip.open(legacy / "mythic_runs.csv.gz", "wt", newline="") as fh:
        df.to_csv(fh, index=False)
    (legacy / "keystone_times.json").write_text(json.dumps(ks_map, separators=(",", ":")))
    # Raider.IO scores for 60% of characters
    chars = sorted({f"{r['character']}@{r['server']}@{r['region']}" for r in csv_rows})
    rio_rows = []
    for c in chars:
        h = int(hashlib.md5(c.encode()).hexdigest()[:8], 16)
        if h % 10 < 6:
            name, realm, reg = c.split("@")
            rio_rows.append([name, realm, reg, f"{1500 + h % 2400:.1f}", "2026-09-17"])
    with gzip.open(legacy / "rio_scores.csv.gz", "wt", newline="") as fh:
        csv.writer(fh).writerows(rio_rows)
    (out / "tuning_patches.json").write_text(json.dumps({"patches": [TUNING_PATCH]}, indent=1))
    # pins: the season sets, learned hero markers over the whole fixture
    abil_all = []
    for k in range(1, 9):
        abil_all.extend(_read_jsonl(out / "chunks" / f"{k:02d}" / "abilities.jsonl"))
    hero_by_key = {(r["report_code"], r["fight_id"], r["character"]): (r["spec"] + " " + r["class"], r["hero_talent"])
                   for r in csv_rows}
    pairs = []
    for a in abil_all:
        sp = hero_by_key.get((a["report_code"], a["fight_id"], a["name"]))
        if sp and a["abilities"]:
            pairs.append((sp[0], sp[1], frozenset(x["name"] for x in a["abilities"])))
    hr = HeroResolver.learn(pairs)
    pins = {"tier_sets": {c: {"id": TIER_SET[c]} for c in CLASSES},
            "hero_markers": {sp: dict(sorted(m.items())) for sp, m in hr.markers.items()},
            "hero_sole": dict(hr.sole),
            "tuning_items": sorted({"Ravenous Swarm"}), "eslots": ESLOTS,
            "pars": KEYSTONE_PARS}
    (out / "pins.json").write_text(json.dumps(pins, indent=1, sort_keys=True))
    # the legacy-shaped payload for the oracle test
    payload = legacy_payload(df, pins, hr, abil_all, rio_rows)
    with gzip.open(out / "payload.json.gz", "wt") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    fixture = {
        "now": NOW_ISO, "now_ms": NOW_MS, "epoch": EPOCH, "slug": SEASON["slug"],
        "days": [FIRST_DAY, LAST_DAY], "cube_weeks": [CUBE_WEEK], "gap_weeks": [GAP_WEEK],
        "window_weeks": [34, 35, 36], "tuning": TUNING_PATCH, "seed": seed,
        "runs_per_day": runs_per_day, "runs": len(runs), "rows": len(csv_rows),
        "chunks": chunk_meta, "notes": notes,
        "csv_marginals": "data/mythic_runs.csv.gz" if (ROOT / "data" / "mythic_runs.csv.gz").exists() else "fallback",
    }
    (out / "fixture.json").write_text(json.dumps(fixture, indent=1))
    return fixture


CSV_COLUMNS = ["character", "server", "region", "class", "spec", "hero_talent", "role",
               "dungeon", "key_level", "duration_s", "damage_done", "dps", "deaths",
               "item_level", "score", "medal", "affixes", "report_code", "fight_id",
               "started_at", "set_pieces", "set_id", "set_counts", "keystone_s"]


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def _write_rankings(path, ranks):
    """(enc, key, ranking) triples -> the sweep's page records, 100 per page,
    sorted by score within a (dungeon, key) leaderboard like the API."""
    pages = collections.defaultdict(list)
    for enc, key, rk in ranks:
        pages[(enc, key - 1)].append(rk)
    with path.open("w", encoding="utf-8") as fh:
        for (enc, bracket) in sorted(pages):
            lst = sorted(pages[(enc, bracket)], key=lambda r: -r["score"])
            for p in range(0, len(lst), 100):
                fh.write(json.dumps({"enc": enc, "bracket": bracket, "page": p // 100 + 1,
                                     "rankings": lst[p:p + 100]}) + "\n")


def legacy_export(chunks_dir: pathlib.Path, gain_codes, runs):
    """fetch_data.export() over the concatenated journals: dedup keep=last
    on (report, fight, character, server), the keystone-clock map
    accumulated over every snapshot, the duplicate-upload signature
    collapse, then the score/medal overlay from the newest snapshot."""
    rows = []
    for k in range(1, 9):
        rows.extend(_read_jsonl(chunks_dir / f"{k:02d}" / "players.jsonl"))
    last = {}
    for i, r in enumerate(rows):
        last[(r["report_code"], r["fight_id"], r["character"], r["server"])] = i
    keep_idx = sorted(last.values())
    rows = [rows[i] for i in keep_idx]
    # the clock map: union over snapshots (a run that dropped off keeps its clock)
    ks = {}
    overlay = {}
    for k in range(1, 9):
        for rec in _read_jsonl(chunks_dir / f"{k:02d}" / "rankings.jsonl"):
            for r in rec["rankings"]:
                code, fid = r["report"]["code"], r["report"]["fightID"]
                if r.get("duration"):
                    ks[f"{code}:{fid}"] = round(r["duration"] / 1000, 1)
                overlay[(code, fid)] = (r.get("score"), r.get("medal"))
    for r in rows:
        r["keystone_s"] = ks.get(f"{r['report_code']}:{r['fight_id']}", "")
    per_run = collections.OrderedDict()
    for r in rows:
        per_run.setdefault((r["report_code"], r["fight_id"]), []).append(r)
    canon = set()
    best = {}
    for (code, fid), rs in per_run.items():
        ksv = rs[0]["keystone_s"]
        if ksv in ("", None):
            canon.add((code, fid))
            continue
        sig = f"{rs[0]['dungeon']}/{rs[0]['key_level']}/{ksv}/" + "|".join(sorted(str(x["character"]) for x in rs))
        cand = (-len(rs), code, fid)
        if sig not in best or cand < best[sig][0]:
            best[sig] = (cand, (code, fid))
    canon |= {v[1] for v in best.values()}
    rows = [r for r in rows if (r["report_code"], r["fight_id"]) in canon]
    for r in rows:
        ov = overlay.get((r["report_code"], r["fight_id"]))
        if ov:
            if ov[0] is not None:
                r["score"] = ov[0]
            if ov[1] is not None:
                r["medal"] = ov[1]
    out = []
    for r in rows:
        d = {c: r.get(c, "") for c in CSV_COLUMNS}
        d["set_pieces"] = d["set_id"] = ""
        out.append(d)
    return out, ks


def legacy_payload(df: pd.DataFrame, pins: dict, hr: HeroResolver, abil_all: list,
                   rio_rows: list) -> dict:
    """The legacy payload for the CSV, following build() (B:2685–2842) --
    the shape both the browser and sitecalc consume. `tmul` is a synthetic
    per-parse multiplier (x10000, 0 = unprojectable) so the projection paths
    are exercised; `post` is the fixture's mid-week cutoff per region."""
    df = df.copy()
    df["keystone_s"] = pd.to_numeric(df["keystone_s"], errors="coerce")
    ok = df["keystone_s"].notna() & (df["keystone_s"] > 0) & df["damage_done"].notna()
    df.loc[ok, "dps"] = (df.loc[ok, "damage_done"] / df.loc[ok, "keystone_s"]).round(1)
    df.loc[ok, "duration_s"] = df.loc[ok, "keystone_s"].round(1)
    for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    # hero recovery from abilities with the learned markers (B:142–178)
    abil = {(r["report_code"], r["fight_id"], r["name"]): frozenset(a["name"] for a in r["abilities"])
            for r in abil_all if r["abilities"]}
    heroes = list(df["hero_talent"])
    for i, (c, f, ch, sp, cl, h) in enumerate(zip(df["report_code"], df["fight_id"], df["character"],
                                                  df["spec"], df["class"], heroes)):
        if h != "Unknown":
            continue
        got, _ = hr.classify(sp + " " + cl, abil.get((c, f, ch)))
        if got:
            heroes[i] = got
    df["hero_talent"] = heroes
    started = pd.to_datetime(pd.to_numeric(df["started_at"], errors="coerce"), unit="ms", errors="coerce")
    day = ((started - pd.Timestamp(EPOCH)).dt.days).fillna(-1).astype(int)
    hr_ = started.dt.hour.fillna(-1).astype(int)

    def enc(col):
        cats = sorted(df[col].unique())
        idx = {c: i for i, c in enumerate(cats)}
        return cats, df[col].map(idx).astype(int).tolist()
    classes, cls_arr = enc("class")
    specs, spec_arr = enc("spec")
    heroes_v, hero_arr = enc("hero_talent")
    dungeons, dun_arr = enc("dungeon")
    regions, reg_arr = enc("region")
    roles, role_arr = enc("role")
    run_ids = df["report_code"].astype(str) + ":" + df["fight_id"].astype(str)
    run_arr = pd.factorize(run_ids)[0].tolist()
    char_ids = (df["character"].fillna("?").astype(str) + "@" + df["server"].fillna("?").astype(str)
                + "@" + df["region"])
    char_codes, char_keys = pd.factorize(char_ids)
    rio = {f"{n}@{r}@{g}": float(s) for n, r, g, s, _ in rio_rows}
    charscore = [int(round(rio[k])) if k in rio else -1 for k in char_keys]
    timed = df["medal"].map({"gold": 1, "silver": 1, "bronze": 1, "timed": 1, "none": 0}).fillna(-1).astype(int)
    cut = df["region"].map(lambda r: TUNING_PATCH["regions"].get(r, TUNING_PATCH["regions"]["default"]))
    cut = pd.to_datetime(cut, utc=True)
    post = (started.dt.tz_localize("UTC") >= cut).astype(int)
    # tier pieces against the pinned season set (B:424–500 with the pin)
    tier = []
    for cl, sc_ in zip(df["class"], df["set_counts"]):
        if sc_ is None or (isinstance(sc_, float) and pd.isna(sc_)) or str(sc_) == "":
            tier.append(-1)
            continue
        sc_ = str(sc_)
        if sc_ == "none":
            tier.append(0)
            continue
        counts = dict(p.split(":") for p in sc_.split("|"))
        tier.append(min(int(counts.get(str(pins["tier_sets"].get(cl, {}).get("id")), 0)), 5))
    # a synthetic projection: tuned specs get a multiplier, 2% unprojectable
    rng = np.random.default_rng(99)
    tuned = df["spec"].isin(["Arcane", "Frost", "Havoc", "Shadow"]) & (post == 1)
    mult = np.where(tuned, np.round(rng.uniform(0.93, 1.08, len(df)), 4), 1.0)
    unproj = tuned & (rng.random(len(df)) < 0.02)
    mult = np.where(unproj, 0.0, mult)
    tmul = [int(round(x * 10000)) for x in mult]
    pars = [KEYSTONE_PARS.get(d, 0) for d in dungeons]
    payload = {
        "built": _iso(NOW_MS - 600_000).replace("T", " ")[:16] + " UTC",
        "season": SEASON["name"], "epoch": EPOCH,
        "tuning": {"label": TUNING_PATCH["label"], "date": TUNING_PATCH["date"],
                   "regions": TUNING_PATCH["regions"], "note": TUNING_PATCH["note"],
                   "runs": int((post == 1).sum())},
        "classes": classes, "specs": specs, "heroes": heroes_v, "dungeons": dungeons,
        "regions": regions, "roles": roles, "pars": pars,
        "rows": {
            "cls": cls_arr, "spec": spec_arr, "hero": hero_arr, "dun": dun_arr, "reg": reg_arr,
            "role": role_arr, "key": df["key_level"].astype(int).tolist(),
            "deaths": df["deaths"].astype(int).tolist(),
            "dps": df["dps"].round(0).astype(int).tolist(),
            "dur": pd.to_numeric(df["duration_s"], errors="coerce").fillna(0).round(0).astype(int).tolist(),
            "kdur": df["keystone_s"].fillna(0).round(0).astype(int).tolist(),
            "timed": timed.tolist(), "post": post.tolist(), "day": day.tolist(), "hr": hr_.tolist(),
            "run": run_arr, "char": char_codes.tolist(), "tier": tier, "tmul": tmul,
        },
        "charscore": charscore,
        "projection": {"label": "fixture projection", "date": TUNING_PATCH["date"],
                       "parses": int(tuned.sum()), "unprojectable": int(unproj.sum()),
                       "specs": ["Arcane Mage", "Frost Mage", "Havoc DemonHunter", "Shadow Priest"]},
    }
    return payload


def load_payload(out: pathlib.Path) -> dict:
    with gzip.open(pathlib.Path(out) / "payload.json.gz", "rt") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "data" / "processed" / "fixtures" / "eq"))
    ap.add_argument("--runs-per-day", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--csv", default=None, help="marginals source (default data/mythic_runs.csv.gz)")
    a = ap.parse_args(argv)
    fx = build_fixture(pathlib.Path(a.out), a.runs_per_day, a.seed,
                       pathlib.Path(a.csv) if a.csv else None)
    print(json.dumps({k: fx[k] for k in ("now", "runs", "rows", "days", "cube_weeks", "gap_weeks")}))
    for c in fx["chunks"]:
        print(c)
    print("notes:", json.dumps({k: (v if not isinstance(v, list) or len(v) < 6 else f"{len(v)} items")
                                for k, v in fx["notes"].items()}))


if __name__ == "__main__":
    main()
