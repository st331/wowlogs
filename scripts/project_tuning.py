#!/usr/bin/env python3
"""Project the August 18 class tuning onto the post-tuning PTR population.

Method
------
Every parse's damage is decomposed into its constituent abilities (collected by
fetch_abilities.py).  Each tuning line becomes a multiplier on the abilities it
touches, so a parse's projected damage is

    D' = sum_over_abilities  d_a * m_a

and its projected DPS is D' / fight duration.  Medians are then recomputed over
the same population, which is what makes this a projection of the *observed*
runs rather than a guess.

Three kinds of tuning line, handled differently:

  spec-wide aura   exact.  "All ability damage increased by 9%" multiplies
                   every ability the spec itself casts.  Item procs and, for
                   "ability damage" wordings, auto-attacks are excluded - see
                   `classify_abilities`.
  named ability    exact.  "Reap/Cull/Eradicate damage reduced by 12%" hits
                   exactly those damage lines.
  set-bonus scalar the set bonus is a *buff on top of* an ability that the log
                   reports as one number, so the split is not observable.  The
                   bonus is parameterised by B (how much of that ability's
                   damage the bonus was adding) and reported as a band.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "mythic_runs_ptr.csv.gz"
ABIL = ROOT / "data" / "raw" / "abilities_ptr.jsonl"
TUNING = ROOT / "data" / "tuning_patches.json"

# ---------------------------------------------------------------- ability kind
# An ability that shows up under many different classes is gear (a trinket,
# embellishment or enchant proc), not something a class aura scales.  Deciding
# this from the data beats hand-maintaining a trinket list.
ITEM_CLASS_THRESHOLD = 3
AUTO_ATTACK = {"Melee", "Auto Shot", "Shoot", "Auto Attack"}


def classify_abilities(rows) -> set[str]:
    seen = defaultdict(set)
    for r in rows:
        for a in r["abilities"]:
            seen[a["name"]].add(r["class"])
    # auto-attacks show up under every class but are NOT gear - whether an
    # aura scales them is decided by its wording, via aura_scope.
    return {n for n, cs in seen.items()
            if len(cs) >= ITEM_CLASS_THRESHOLD and n not in AUTO_ATTACK}


# ------------------------------------------------------------------ tune rules
# aura       : spec-wide multiplier
# aura_scope : "ability" excludes auto-attacks, "all" includes them
# abilities  : {ability name: multiplier} applied on top of the aura
# set_bonus  : (ability names, old_per_stack, new_per_stack) - reported as a
#              band over B, the fraction of that line the bonus was providing
RULES = {
    "Frost DeathKnight": dict(
        aura=1.09, aura_scope="ability",
        set_bonus=[("2pc Freezing Tempest", ["Icy Death Torrent"], 0.04, 0.02)],
        caveats=["2pc also halves the attack-speed buff (2%->1% per stack), "
                 "which raises auto-attack and rotational throughput; not "
                 "observable from a damage table, so excluded (makes this "
                 "projection mildly optimistic)."],
    ),
    "Devourer DemonHunter": dict(
        aura=1.14, aura_scope="ability",
        abilities={"Reap": 0.88, "Cull": 0.88, "Eradicate": 0.88},
        set_bonus=[("4pc Reap bonus", ["Reap"], 0.20, 0.10)],
        caveats=["Eradicate's AoE component goes 85%->90% of base damage; the "
                 "log reports one Eradicate line, so the ST/AoE split is not "
                 "observable. Modelled as no change (conservative).",
                 "4pc soul-fragment generation drops 8->2, which slows the "
                 "Consume/Soulburst loop. Rotational, not modelled."],
    ),
    "Arcane Mage": dict(
        aura=1.03, aura_scope="ability",
        set_bonus=[("2pc Arcane Missiles bonus", ["Arcane Missiles"], 0.20, 0.05),
                   ("4pc Cumulative Power",
                    ["Arcane Blast", "Arcane Pulse", "Prismatic Bolt"], 0.05, 0.03)],
    ),
    "Fury Warrior": dict(
        aura=1.06, aura_scope="all",
        caveats=["4pc Recklessness crit bonus 5%->3% per stack (cap 10%->6%). "
                 "That is a crit-damage buff on everything during Recklessness "
                 "windows; uptime and crit share are not observable from a "
                 "damage table. Modelled separately as a band."],
    ),
    "Subtlety Rogue": dict(
        aura=1.06, aura_scope="all",
        share_scale=[("4pc Lingering Shadow extension",
                      ["Lingering Shadow"], 0.60)],
        caveats=["4pc effectiveness 100%->60%. The 4pc extends Lingering "
                 "Shadow to Eviscerate and Black Powder; that damage is "
                 "reported inside the Lingering Shadow line, so the share "
                 "coming from the 4pc is not separable. Modelled as a band."],
    ),
    "Assassination Rogue": dict(aura=1.04, aura_scope="all"),
    "Enhancement Shaman": dict(aura=1.05, aura_scope="all"),
    "Restoration Druid": dict(aura=1.20, aura_scope="all"),
    "Discipline Priest": dict(
        aura=0.70, aura_scope="all",
        abilities={"Entropic Rift": 1.20},
    ),
    "Demonology Warlock": dict(
        aura=1.0, aura_scope="all",
        set_bonus=[("2pc Implode effectiveness",
                    ["Implosion", "Isolated Implosion"], 2.50, 3.50)],
        caveats=["Only the Implode effectiveness numbers changed; this is a "
                 "buff to the proc-driven share of Implosion damage."],
    ),
    "BeastMastery Hunter": dict(
        aura=1.0, aura_scope="all",
        set_bonus=[("4pc Cobra Shot bonus", ["Cobra Shot"], 0.15, 0.20)],
    ),
    "Blood DeathKnight": dict(
        aura=1.0, aura_scope="all",
        hero_only="San'layn",
        abilities={"Dancing Rune Weapon": 1.05 / 1.10},
        strength=(0.10, 0.06),
        caveats=["San'layn only. Visceral Strength 10%->6% strength is modelled "
                 "as a flat 1.06/1.10 on all damage, i.e. assuming damage "
                 "scales linearly with strength."],
    ),
}


def load():
    cut = pd.Timestamp(json.loads(TUNING.read_text())["patches"][0]
                       ["regions"]["default"]).value // 10 ** 6
    df = pd.read_csv(CSV)
    df = df[df["started_at"] >= cut].copy()
    df["specname"] = df["spec"] + " " + df["class"]
    rows = [json.loads(l) for l in ABIL.open()]
    return df, rows


def tier_sets(rows):
    """The class tier set is the setID worn as 4+ pieces most often."""
    cnt = defaultdict(Counter)
    for r in rows:
        for sid, n in r["sets"].items():
            if n >= 4:
                cnt[r["class"]][sid] += 1
    return {c: s.most_common(1)[0][0] for c, s in cnt.items() if s}


def multiplier(abilities, rule, items, B):
    """Projected/current damage ratio for one parse."""
    aura, scope = rule.get("aura", 1.0), rule.get("aura_scope", "all")
    named = rule.get("abilities", {})
    sb = {}
    for label, names, old, new in rule.get("set_bonus", []):
        b = B.get(label)
        if b is None:
            continue
        # bonus was adding fraction b of the line; it scales by new/old
        ratio = (1 + b * (new / old)) / (1 + b)
        for n in names:
            sb[n] = sb.get(n, 1.0) * ratio
    for label, names, new_over_old in rule.get("share_scale", []):
        f = B.get(label)
        if f is None:
            continue
        for n in names:                       # only share f of the line moves
            sb[n] = sb.get(n, 1.0) * (1 - f * (1 - new_over_old))
    if rule.get("strength"):
        old_s, new_s = rule["strength"]
        aura *= (1 + new_s) / (1 + old_s)
    cur = new = 0.0
    for a in abilities:
        d, n = a["total"], a["name"]
        m = 1.0
        if n not in items and not (scope == "ability" and n in AUTO_ATTACK):
            m *= aura
        m *= named.get(n, 1.0) * sb.get(n, 1.0)
        cur += d
        new += d * m
    return (new / cur) if cur else 1.0


# Central estimates for the unobservable set-bonus shares, and the band swept
# for the sensitivity table.  B = how much of the ability's damage the bonus
# was contributing, i.e. line damage = base * (1 + B).
B_CENTRAL = {
    "2pc Freezing Tempest": 0.30,
    "4pc Reap bonus": 0.20,
    "2pc Arcane Missiles bonus": 0.20,
    "4pc Cumulative Power": 0.25,
    "2pc Implode effectiveness": 1.50,
    "4pc Cobra Shot bonus": 0.45,
    # share of the Lingering Shadow line that comes from the 4pc extension
    "4pc Lingering Shadow extension": 0.50,
}
B_BAND = {k: (v * 0.5, v * 1.5) for k, v in B_CENTRAL.items()}


def project(df, rows, B):
    """Per-parse projected/current damage ratio, indexed like df."""
    items = classify_abilities(rows)
    tier = tier_sets(rows)
    abil = {(r["report_code"], r["fight_id"], r["name"]): r for r in rows}
    idx, out = [], []
    for i, t in zip(df.index, df.itertuples()):
        rule = RULES.get(t.specname)
        rec = abil.get((t.report_code, t.fight_id, t.character))
        if rule is None or rec is None or not rec["abilities"]:
            continue
        if rule.get("hero_only") and t.hero_talent != rule["hero_only"]:
            continue
        pieces = rec["sets"].get(tier.get(rec["class"], ""), 0)
        r = rule if pieces >= 2 else {k: v for k, v in rule.items()
                                      if k not in ("set_bonus", "share_scale")}
        idx.append(i)
        out.append({"specname": t.specname, "dps": t.dps, "pieces": pieces,
                    "mult": multiplier(rec["abilities"], r, items, B)})
    return pd.DataFrame(out, index=idx)


def spec_table(df, rows, B):
    """Median DPS now vs projected, per spec, over all post-tuning PTR keys."""
    per = project(df, rows, B)
    pop = df[df["specname"].isin(RULES)]
    out = []
    for s_, g in per.groupby("specname"):
        allp = pop[pop["specname"] == s_]
        if RULES[s_].get("hero_only"):
            allp = allp[allp["hero_talent"] == RULES[s_]["hero_only"]]
        mm = g["mult"].median()
        # each covered parse scales by its own multiplier; parses with no
        # ability record fall back to the spec's median multiplier
        mult = g["mult"].reindex(allp.index).fillna(mm)
        proj = allp["dps"] * mult
        out.append(dict(
            spec=s_, chars=allp["character"].nunique(), parses=len(allp),
            covered=len(g), cur=allp["dps"].median(), new=proj.median(),
            mult_med=mm, mult_p10=g["mult"].quantile(.10),
            mult_p90=g["mult"].quantile(.90),
            kind=("exact" if not RULES[s_].get("set_bonus")
                  and not RULES[s_].get("share_scale")
                  and not RULES[s_].get("caveats") else "modelled")))
    r = pd.DataFrame(out)
    r["pct"] = 100 * (r["new"] / r["cur"] - 1)
    return r.sort_values("pct", ascending=False)


def main():
    df, rows = load()
    pop = df[df["specname"].isin(RULES)]
    print(f"post-tuning parses {len(df):,} | ability records {len(rows):,} | "
          f"tuned-spec parses {len(pop):,}")
    r = spec_table(df, rows, B_CENTRAL)
    pd.set_option("display.width", 220)
    print("\n=== projected median DPS, all post-tuning PTR keys ===")
    print(r[["spec", "kind", "chars", "parses", "covered", "cur", "new",
             "pct", "mult_med", "mult_p10", "mult_p90"]]
          .to_string(index=False,
                     formatters={"cur": "{:,.0f}".format,
                                 "new": "{:,.0f}".format,
                                 "pct": "{:+.1f}%".format,
                                 "mult_med": "{:.4f}".format,
                                 "mult_p10": "{:.4f}".format,
                                 "mult_p90": "{:.4f}".format}))
    # sensitivity: sweep every unobservable set-bonus share across its band
    print("\n=== sensitivity to the unobservable set-bonus share B ===")
    for lbl, (lo, hi) in B_BAND.items():
        for tag, val in (("low", lo), ("high", hi)):
            Bx = dict(B_CENTRAL, **{lbl: val})
            rx = spec_table(df, rows, Bx)
            for _, row in rx.iterrows():
                base = r.loc[r["spec"] == row["spec"], "pct"].iloc[0]
                if abs(row["pct"] - base) > 0.05:
                    print(f"  {lbl:28} B={val:5.2f} ({tag:4}) -> "
                          f"{row['spec']:22} {row['pct']:+6.2f}% "
                          f"(central {base:+6.2f}%)")
    return r


if __name__ == "__main__":
    main()
