#!/usr/bin/env python3
"""Recover a parse's hero talent from the abilities it actually cast.

WCL resolves hero talents from `combatantInfo.talentTree`, but some logs carry
no combatantInfo at all — those parses land in the dataset as
hero_talent="Unknown".  They are not evenly spread: they cluster by report, so
whole groups go missing at once, and any hero-gated analysis silently treats
them as "not that hero" rather than "unknown".

Hero trees grant abilities no other tree in the same spec has, and those
abilities show up by name in the damage breakdown.  So the tree can be read
straight off the damage done.  Markers are learned from the parses whose hero
talent IS known — no hand-maintained ability list to drift out of date — and
a parse is only labelled when the evidence points at exactly one tree.

    from hero_from_abilities import HeroResolver
    hr = HeroResolver.learn(df, ability_records)
    hero, n_markers = hr.classify("Blood DeathKnight", {"Vampiric Strike", ...})
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

# An ability counts as a marker when nearly every parse of one tree casts it
# and nearly no parse of a sibling tree does.
MIN_IN_TREE = 0.85
MAX_OUT_TREE = 0.10
MIN_TREE_PARSES = 3      # below this a "tree" is too thin to learn from


@dataclass
class HeroResolver:
    # spec -> ability -> hero it marks
    markers: dict = field(default_factory=dict)
    # spec -> the only hero ever seen for it (lets a lone tree be assigned)
    sole: dict = field(default_factory=dict)

    @classmethod
    def learn(cls, pairs) -> "HeroResolver":
        """pairs: iterable of (specname, hero_talent, set_of_ability_names)."""
        seen = collections.defaultdict(lambda: collections.defaultdict(
            collections.Counter))
        n = collections.defaultdict(collections.Counter)
        for spec, hero, abilities in pairs:
            if hero == "Unknown" or not abilities:
                continue
            n[spec][hero] += 1
            for a in abilities:
                seen[spec][hero][a] += 1
        markers, sole = {}, {}
        for spec, per_hero in n.items():
            heroes = [h for h, c in per_hero.items() if c >= MIN_TREE_PARSES]
            if len(heroes) == 1 and len(per_hero) == 1:
                sole[spec] = heroes[0]
            m = {}
            for h in heroes:
                for a, c in seen[spec][h].items():
                    p_in = c / per_hero[h]
                    # every sibling tree vetoes, including ones too thin to
                    # learn markers OF. Otherwise a rare tree's parses match
                    # the dominant tree's "markers" and get mislabelled as it.
                    p_out = max((seen[spec][o][a] / per_hero[o]
                                 for o in per_hero if o != h), default=0.0)
                    if p_in >= MIN_IN_TREE and p_out <= MAX_OUT_TREE:
                        m[a] = h
            if m:
                markers[spec] = m
        return cls(markers=markers, sole=sole)

    def classify(self, spec, abilities):
        """-> (hero, marker_count). hero is None when the evidence is not
        unanimous, so an ambiguous parse stays Unknown rather than guessed."""
        if not abilities:
            # no breakdown at all -> no evidence. Never fall through to the
            # sole-tree shortcut here; that would label a parse we know
            # nothing about.
            return None, 0
        m = self.markers.get(spec)
        if not m:
            return (self.sole.get(spec), 0) if self.sole.get(spec) else (None, 0)
        votes = collections.Counter(m[a] for a in abilities if a in m)
        if not votes:
            # no marker at all: a lone-tree spec can still be assigned safely
            return (self.sole.get(spec), 0) if self.sole.get(spec) else (None, 0)
        top, count = votes.most_common(1)[0]
        if len(votes) > 1 and votes.most_common(2)[1][1] >= count:
            return None, 0                      # tie -> refuse to guess
        return top, count
