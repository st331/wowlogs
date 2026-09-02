#!/usr/bin/env python3
"""Project an announced-but-unreleased class tuning onto recorded runs.

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
  compensating aura  an aura buff Blizzard states is there to OFFSET a set-bonus
                   reduction that a damage table cannot see - a resource-
                   generation cut, say.  Applying the compensation without the
                   thing it compensates counts the buff twice and turns a
                   designed-neutral change into a large fake buff, so the pair
                   is modelled as designed: net neutral, swept as a band.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "mythic_runs.csv.gz"
ABIL = ROOT / "data" / "raw" / "abilities.jsonl"
TUNING = ROOT / "data" / "tuning_patches.json"

# partitioned_payload.md §5: the rule tables are an input like any other.
# Bump RULES_VERSION by convention when RULES change; rules_digest() catches
# an unbumped edit anyway and every day file / the manifest carry it.
RULES_VERSION = "2026-09-02"
PROJECTION_LABEL = "Aug 14 hotfix + Aug 18 class tuning"
PROJECTION_DATE = "2026-08-18"
PROJECTION_URL = ("https://us.forums.blizzard.com/en/wow/t/"
                  "class-tuning-incoming-%E2%80%93-august-18/2336820")

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
# The Aug 18 2026 pass, kept as a worked reference for the rule vocabulary.
# It is NOT active: that tuning shipped with Season 2, so every recorded run
# already contains it. To project a future pass, add its cutoff to
# data/tuning_patches.json, write its rules here and assign them to RULES.
RULES: dict = {}

# Test hook (partitioned_payload.md §9.1, test_incremental_idempotent chunk
# 7 "an edit of RULES['Arcane Mage']"): WOWLOGS_RULES names a JSON file whose
# object REPLACES RULES for this process, so a rule-table edit can be staged
# between two builder runs without editing this module. Production never
# sets it; the digest below covers whatever RULES ends up being.
if __import__("os").environ.get("WOWLOGS_RULES"):
    with open(__import__("os").environ["WOWLOGS_RULES"], "r", encoding="utf-8") as _fh:
        RULES = {k: dict(v) for k, v in json.load(_fh).items()}
    for _rule in RULES.values():
        for _k in ("set_bonus", "share_scale"):
            if _k in _rule:
                _rule[_k] = [tuple(x) for x in _rule[_k]]
        if "attack_speed" in _rule:
            _rule["attack_speed"] = tuple(_rule["attack_speed"])
        if "strength" in _rule:
            _rule["strength"] = tuple(_rule["strength"])

RULES_AUG18_2026 = {
    "Frost DeathKnight": dict(
        aura=1.09, aura_scope="ability",
        set_bonus=[("2pc Freezing Tempest", ["Icy Death Torrent"], 0.04, 0.02)],
        # The 2pc's other half halves the attack-speed buff. That is not just
        # auto-attack damage: Icy Death Torrent procs off auto-attack CRITS, so
        # slower swings cut its proc count too, and IDT is ~17% of the spec.
        # Swings scale by (1+0.01*S)/(1+0.02*S) for S Freezing Tempest stacks.
        attack_speed=("2pc Freezing Tempest", 0.02, 0.01,
                      ["Icy Death Torrent"]),
        caveats=["Attack speed is the melee-swing aura only - it does not "
                 "touch the GCD or rune regeneration - so the modelled cost is "
                 "auto-attack damage plus the Icy Death Torrent proc rate.",
                 "Freezing Tempest has no documented stack cap. Stacks are "
                 "modelled at 6.8, the floor from Remorseless Winter pressed "
                 "on its 20s cooldown (8s duration, 0.8s ticks with the 4pc, "
                 "10s buff). Gathering Storm extending RW past ~10.4s lets "
                 "stacks run higher, which the band covers."],
    ),
    "Devourer DemonHunter": dict(
        aura=1.14, aura_scope="ability",
        # "Devourer's 4-piece set bonus is performing significantly above
        # expectations, so we are reducing its power. To compensate for this
        # set bonus reduction, we are increasing all ability damage."
        # The reduction is soul-fragment generation (8 -> 2 per Soulburst),
        # which no damage table can show. Banking the +14% while ignoring what
        # it pays for turns a designed-neutral swap into a fake +9% buff, so
        # the pair is treated as Blizzard describes it.
        compensates=("4pc power reduction (soul fragments 8 -> 2)",
                     "Devourer compensation offset"),
        abilities={"Reap": 0.88, "Cull": 0.88, "Eradicate": 0.88},
        set_bonus=[("4pc Reap bonus", ["Reap"], 0.20, 0.10)],
        # Eradicate's AoE portion goes 85% -> 90% of base, worth up to +5.88%
        # on the line. Parameterised by how much of the line is AoE.
        share_scale=[("Eradicate AoE share", ["Eradicate"], 90 / 85)],
        caveats=["The +14% aura is stated compensation for the 4pc reduction, "
                 "so the two are modelled as cancelling. What remains is the "
                 "separately-announced -12% on Reap/Cull/Eradicate, i.e. a "
                 "small net nerf. How exactly the compensation lands is swept "
                 "as a band.",
                 "Eradicate's AoE component goes 85%->90% of base damage. The "
                 "log reports one Eradicate line, so the ST/AoE split is not "
                 "observable and is parameterised; Eradicate is 29.9% of the "
                 "spec, so this is worth +0.4 to +1.4pp overall."],
    ),
    "Arcane Mage": dict(
        aura=1.03, aura_scope="ability",
        set_bonus=[("2pc Arcane Missiles bonus", ["Arcane Missiles"], 0.20, 0.05),
                   ("4pc Cumulative Power",
                    ["Arcane Blast", "Arcane Pulse", "Prismatic Bolt"], 0.05, 0.03)],
    ),
    "Fury Warrior": dict(
        aura=1.06, aura_scope="all",
        # The 4pc's Bloodthirst +10% is UNCHANGED; only the Recklessness crit
        # bonus moves, 5%->3% per stack and 10%->6% at cap. That is 4pp of crit
        # across everything, but only inside Recklessness windows (~90s
        # cooldown, ~12s duration), so it is small - roughly 0.5% of total
        # damage, not the several percent a compensating aura would imply.
        flat=("4pc Recklessness crit bonus",),
        caveats=["Slayer Fury also carries the Aug 14 Executioner fix, worth "
                 "about -1.0% here (Execute is 5.0% of the spec versus 12.0% "
                 "for Arms), so the two are shown together.",
                 "Blizzard say the +6% baseline compensates the Executioner "
                 "fix and the set-bonus change and that they are 'happy with "
                 "where Fury has been', which implies net-neutral. The changes "
                 "as written do not sum to that: the Executioner fix and the "
                 "4pc crit change together measure about -1.5%, well short of "
                 "the +6%. Unlike Devourer, whose hidden nerf is a resource "
                 "cut plausibly worth the whole aura, nothing here supports "
                 "cancelling it, so the aura stands and the gap is flagged "
                 "rather than assumed away."],
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
    # No announced tuning line; Arms moves purely because of the Aug 14
    # Executioner hotfix, which every parse before that date predates.
    "Arms Warrior": dict(aura=1.0, aura_scope="all"),
    # Mistweaver's healing buff does not touch DPS, but its 4pc does: the
    # bonus resets Rising Sun Kick, so a 33% higher activation rate means more
    # RSK casts. A damage table shows that only as a bigger line, never as the
    # proc it came from. Measured: RSK is 6.94 casts/min below the set versus
    # 10.38 at 4pc, so ~33% of casts are set-driven.
    "Mistweaver Monk": dict(
        aura=1.0, aura_scope="all",
        share_scale=[("4pc RSK reset share", ["Rising Sun Kick"], 1.33)],
        caveats=["4pc 'Activation rate increased by 33%' is a proc-rate "
                 "change on a cooldown reset, not a damage change. Modelled "
                 "from the observed cast-rate gap between set and non-set "
                 "parses.",
                 "The sub-4pc control group is 6 parses at a lower item level, "
                 "so part of that cast-rate gap is haste rather than the set. "
                 "The band runs down to half the measured share."],
    ),
    "Assassination Rogue": dict(aura=1.04, aura_scope="all"),
    "Enhancement Shaman": dict(aura=1.05, aura_scope="all"),
    "Restoration Druid": dict(aura=1.20, aura_scope="all"),
    "Discipline Priest": dict(
        aura=0.70, aura_scope="all",
        abilities={"Entropic Rift": 1.20},
        caveats=["The bulleted 'Entropic Rift damage increased by 20%' maps to "
                 "no ability line in this data - no Disc parse reports one - "
                 "so that rule is inert and the projection is the bare -30% "
                 "aura. The true figure is slightly less negative for the ~32% "
                 "of Disc players on Voidweaver, by an amount this data cannot "
                 "measure."],
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
        # The 4pc bullet contains TWO changes and the cleave half is logged
        # under its own name. "Cobra Cleave" is 0.00% at 3pc, 4.8% at 4pc, and
        # absent from all 107 Marksmanship and 29 Survival parses, so the whole
        # line is set-created; 20% -> 30% effectiveness scales all of it.
        share_scale=[("4pc Cobra Cleave", ["Cobra Cleave"], 30 / 20)],
        caveats=["The 4pc changes both halves of its bonus. The single-target "
                 "half rides the Cobra Shot line; the cleave half is a "
                 "separate 'Cobra Cleave' line, which the data shows exists "
                 "only with the set.",
                 "If cleave effectiveness caps at 100%, four stacks at 30% "
                 "would overflow and the gain is smaller - the band covers it."],
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
    rows = [json.loads(l) for l in ABIL.open() if l.strip()]
    return df, rows


def tier_sets(rows):
    """The class tier set is the setID worn as 4+ pieces most often."""
    cnt = defaultdict(Counter)
    for r in rows:
        for sid, n in r["sets"].items():
            if n >= 4:
                cnt[r["class"]][sid] += 1
    return {c: s.most_common(1)[0][0] for c, s in cnt.items() if s}


# Changes that are ALREADY live but post-date some of the data. A parse
# recorded before one of these still contains the old behaviour, so the
# projection has to correct it; a parse recorded after already reflects it and
# must be left alone. Unlike the announced tuning, the size of these is
# measured from the data either side of the cutoff rather than read off a
# patch note - see HOTFIX_CALIBRATION.
HOTFIXES = {
    "Slayer Executioner double-value fix": dict(
        instant="2026-08-14T00:00:00Z", hero="Slayer",
        specs=["Arms Warrior", "Fury Warrior"], abilities=["Execute"],
        note="Executioner grants 3% Execute damage/crit per stack; a bug "
             "doubled it to 6%. Blizzard's Aug 18 notes describe the fix as "
             "already hotfixed, and it is the reason Arms was brought down "
             "and Fury's baseline raised to compensate.",
    ),
}
# Measured, not assumed: Arms Slayer Execute share fell 11.99% -> 9.94% across
# the cutoff (Mann-Whitney p=4.4e-4) while Slayer's Strike and Bladestorm held
# steady, which pins the bugged bonus at +62% of the Execute line.
HOTFIX_CALIBRATION = {"Slayer Executioner double-value fix": 0.621}
HOTFIX_BAND = {"Slayer Executioner double-value fix": (0.40, 0.95)}


def _needs(label):
    """Pieces a bonus requires, read off its '2pc ...' / '4pc ...' label."""
    return 4 if label.startswith("4pc") else 2


def hotfix_factors(specname, hero, started_ms, B):
    """{ability: multiplier} for live hotfixes this parse predates."""
    out = {}
    for label, h in HOTFIXES.items():
        if specname not in h["specs"]:
            continue
        if h.get("hero") and hero != h["hero"]:
            continue
        cut = pd.Timestamp(h["instant"]).value // 10 ** 6
        if started_ms is None or started_ms >= cut:
            continue                      # already reflects the fix
        b = B.get(label)
        if b is None:
            continue
        ratio = (1 + b / 2) / (1 + b)     # the bonus is halved
        for a in h["abilities"]:
            out[a] = out.get(a, 1.0) * ratio
    return out


def multiplier(abilities, rule, items, B, pieces=99, extra=None):
    """Projected/current damage ratio for one parse."""
    aura, scope = rule.get("aura", 1.0), rule.get("aura_scope", "all")
    swing = {}
    asp = rule.get("attack_speed")
    if asp and pieces >= 2:
        label, old_pc, new_pc, procs = asp
        b = B.get(label)
        if b is not None:
            stacks = b / 0.04                     # B is 4% per stack
            ratio = ((1 + new_pc * stacks) / (1 + old_pc * stacks))
            for n in list(AUTO_ATTACK) + list(procs):
                swing[n] = swing.get(n, 1.0) * ratio
    named = rule.get("abilities", {})
    sb = {}
    for label, names, old, new in rule.get("set_bonus", []):
        b = B.get(label)
        if b is None or pieces < _needs(label):
            continue
        # bonus was adding fraction b of the line; it scales by new/old
        ratio = (1 + b * (new / old)) / (1 + b)
        for n in names:
            sb[n] = sb.get(n, 1.0) * ratio
    for label, names, new_over_old in rule.get("share_scale", []):
        f = B.get(label)
        if f is None or pieces < _needs(label):
            continue
        for n in names:                       # only share f of the line moves
            sb[n] = sb.get(n, 1.0) * (1 - f * (1 - new_over_old))
    flat = rule.get("flat")
    if flat:
        f = B.get(flat[0])
        if f is not None and pieces >= _needs(flat[0]):
            aura *= f
    comp = rule.get("compensates")
    if comp:
        off = B.get(comp[1])
        if off is not None:
            aura = 1.0 + (aura - 1.0) * (1 - off)
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
        m *= swing.get(n, 1.0)
        if extra:
            m *= extra.get(n, 1.0)
        cur += d
        new += d * m
    return (new / cur) if cur else 1.0


# Central estimates for the unobservable set-bonus shares, and the band swept
# for the sensitivity table.  B = how much of the ability's damage the bonus
# was contributing, i.e. line damage = base * (1 + B).
B_CENTRAL = {
    **HOTFIX_CALIBRATION,
    # 1.0 = the aura exactly offsets the unobservable set-bonus reduction, as
    # the developer note says it is meant to. Swept 0.7-1.3 for the band.
    "Devourer compensation offset": 1.00,
    # 6.8 average stacks x 4%/stack - the mechanical floor, see the caveat
    "2pc Freezing Tempest": 0.28,
    # how much of the Eradicate line is AoE (it is a frontal cone)
    "Eradicate AoE share": 0.77,
    # 4pp of crit lost across ~13% Recklessness uptime
    "4pc Recklessness crit bonus": 0.9948,
    "4pc Reap bonus": 0.20,
    "2pc Arcane Missiles bonus": 0.20,
    "4pc Cumulative Power": 0.25,
    "2pc Implode effectiveness": 1.50,
    "4pc Cobra Shot bonus": 0.45,
    # share of the Lingering Shadow line that comes from the 4pc extension
    # measured: the Lingering Shadow line is 0.7% of damage without the set
    # and 13.9% with it, so the 4pc supplies ~95% of it
    "4pc Lingering Shadow extension": 0.95,
    "4pc Cobra Cleave": 1.00,          # the whole line is set-created
    "4pc RSK reset share": 0.331,      # 4pc adds 3.44 of 10.38 casts/min
}
B_BAND = {k: (v * 0.5, v * 1.5) for k, v in B_CENTRAL.items()}
B_BAND.update(HOTFIX_BAND)          # this one has a measured interval
B_BAND["Devourer compensation offset"] = (0.20, 1.30)   # log floor .. designed
B_BAND["2pc Freezing Tempest"] = (0.27, 0.60)           # floor .. Gathering Storm
B_BAND["Eradicate AoE share"] = (0.35, 1.00)
B_BAND["4pc Lingering Shadow extension"] = (0.85, 1.00)   # measured, tight
B_BAND["4pc Cobra Cleave"] = (0.66, 1.00)                 # 100%-cap floor
B_BAND["4pc RSK reset share"] = (0.165, 0.40)             # haste confound
B_BAND["4pc Recklessness crit bonus"] = (0.9928, 0.9960)


def rules_digest() -> str:
    """sha256(RULES_VERSION || canonical JSON of RULES, B_CENTRAL,
    HOTFIX_BAND, B_BAND, PROJECTION_DATE, PROJECTION_LABEL) (§5/§6.3)."""
    import hashlib
    body = json.dumps({"RULES": RULES, "B_CENTRAL": B_CENTRAL,
                       "HOTFIX_BAND": HOTFIX_BAND, "B_BAND": B_BAND,
                       "PROJECTION_DATE": PROJECTION_DATE,
                       "PROJECTION_LABEL": PROJECTION_LABEL},
                      sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256((RULES_VERSION + "\x00" + body).encode("utf-8")).hexdigest()


def project(df, rows, B, items=None, tier=None):
    """Per-parse projected/current damage ratio, indexed like df.

    items / tier: the learned item set and the class -> set id table. None
    learns both from `rows` (the legacy path); the partition builder and the
    equivalence tests inject the pinned tables (§5)."""
    items = classify_abilities(rows) if items is None else set(items)
    tier = tier_sets(rows) if tier is None else dict(tier)
    abil = {(r["report_code"], r["fight_id"], r["name"]): r for r in rows}
    idx, out = [], []
    for i, t in zip(df.index, df.itertuples()):
        rule = RULES.get(t.specname)
        rec = abil.get((t.report_code, t.fight_id, t.character))
        if rule is None or rec is None or not rec["abilities"]:
            continue
        if rule.get("hero_only") and t.hero_talent != rule["hero_only"]:
            continue
        # Missing gear is NOT evidence of a missing tier set. Some logs carry
        # no combatantInfo at all (the same parses show hero_talent="Unknown"),
        # and among parses that DO report gear, 99.5% wear 4pc+. Treating an
        # empty gear list as "no tier" silently skipped the set-bonus nerfs and
        # handed those parses a bare aura buff, so assume the dominant state.
        pieces = rec["sets"].get(tier.get(rec["class"], ""), 0)
        eff = pieces if rec["sets"] else 4
        hx = hotfix_factors(t.specname, t.hero_talent,
                            getattr(t, "started_at", None), B)
        idx.append(i)
        out.append({"specname": t.specname, "dps": t.dps, "pieces": pieces,
                    "gear_known": bool(rec["sets"]),
                    "mult": multiplier(rec["abilities"], rule, items, B, eff,
                                       hx)})
    return pd.DataFrame(out, index=idx)


def spec_table(df, rows, B):
    """Median DPS now vs projected, per spec, over all post-tuning keys."""
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
    print("\n=== projected median DPS, all post-tuning keys ===")
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
