#!/usr/bin/env python3
"""The game's talent loadout import string, written and read.

The format is Blizzard's own (Blizzard_ClassTalentImportExport.lua, read on
2026-11-21 at game build 12.1.5.70961, "unchanged since 12.0.1"):

  header   8 bits   serialization version
          16 bits   specialization id (ChrSpecialization)
         128 bits   tree hash, 16 bytes; ALL ZERO is allowed and tells the
                    game to skip the check ("allow third-party sites to
                    generate loadout strings with an empty tree hash")
  then, for EVERY node of the class tree in ascending node id order -- the
  nodes of the class's other specs and of every hero tree included --
           1 bit    the node is selected (purchased or granted)
           1 bit    ...and purchased            (only when selected; a node
                                                 the game grants for free is
                                                 selected and NOT purchased)
           1 bit    ...partially ranked         (only when purchased)
           6 bits   ......ranks purchased       (only when partially ranked)
           1 bit    ...it is a choice node      (only when purchased)
           2 bits   ......zero-based position of the picked option in the
                          node's own option order (only on a choice node)

Every value is written least significant bit first into one bit stream, and
the stream is cut into 6-bit characters, first bit lowest, over the alphabet
A-Z a-z 0-9 + /. The last character is padded with zero bits.

Nothing here knows about the site: `encode` takes the tree's node order and
what was picked, `decode` gives it back. build_site_data.py turns a logged
talent tree into that, and test_talent_string.py holds both against strings
the game itself exported.
"""
from __future__ import annotations

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
VERSION = 2                 # C_Traits.GetLoadoutSerializationVersion, 11.0 on
_BITS_VERSION, _BITS_SPEC, _BITS_HASH_BYTE, _HASH_BYTES = 8, 16, 8, 16
_BITS_RANKS, _BITS_CHOICE = 6, 2


class _Writer:
    def __init__(self):
        self.bits: list[int] = []

    def add(self, width: int, value: int) -> None:
        if value < 0 or value >= (1 << width):
            raise ValueError(f"{value} does not fit in {width} bits")
        for i in range(width):
            self.bits.append((value >> i) & 1)

    def text(self) -> str:
        out = []
        for i in range(0, len(self.bits), 6):
            chunk = self.bits[i:i + 6]
            out.append(ALPHABET[sum(b << j for j, b in enumerate(chunk))])
        return "".join(out)


class _Reader:
    def __init__(self, text: str):
        self.bits: list[int] = []
        for ch in text:
            v = ALPHABET.index(ch)          # ValueError on a foreign character
            self.bits.extend((v >> j) & 1 for j in range(6))
        self.at = 0

    def take(self, width: int) -> int:
        if self.at + width > len(self.bits):
            raise ValueError("the string ends inside a value")
        v = sum(self.bits[self.at + i] << i for i in range(width))
        self.at += width
        return v

    def rest_is_padding(self) -> bool:
        return len(self.bits) - self.at < 6 and not any(self.bits[self.at:])


def encode(spec_id: int, order: list[int], picked: dict[int, dict],
           version: int = VERSION, tree_hash: bytes = bytes(_HASH_BYTES)) -> str:
    """order: every node id of the class tree, ascending.
    picked: node id -> {"granted": bool, "ranks": purchased ranks,
    "max": the node's maximum ranks, "choice": zero-based option position or
    None}. A node absent from `picked` is not selected."""
    if list(order) != sorted(order) or len(set(order)) != len(order):
        raise ValueError("the node order must be ascending and unique")
    missing = set(picked) - set(order)
    if missing:
        raise ValueError(f"picked nodes are not in the tree: {sorted(missing)[:5]}")
    if len(tree_hash) != _HASH_BYTES:
        raise ValueError("the tree hash is 16 bytes")
    w = _Writer()
    w.add(_BITS_VERSION, version)
    w.add(_BITS_SPEC, spec_id)
    for b in tree_hash:
        w.add(_BITS_HASH_BYTE, b)
    for nid in order:
        p = picked.get(nid)
        if p is None:
            w.add(1, 0)
            continue
        w.add(1, 1)
        if p.get("granted"):
            w.add(1, 0)                      # selected, not purchased: nothing more
            continue
        ranks, mx = int(p["ranks"]), int(p["max"])
        if ranks < 1 or ranks > mx:
            raise ValueError(f"node {nid}: {ranks} ranks of {mx}")
        w.add(1, 1)
        partial = ranks != mx
        w.add(1, 1 if partial else 0)
        if partial:
            w.add(_BITS_RANKS, ranks)
        choice = p.get("choice")
        w.add(1, 0 if choice is None else 1)
        if choice is not None:
            w.add(_BITS_CHOICE, int(choice))
    return w.text()


def decode(text: str, order: list[int]) -> dict:
    """{"version", "spec", "hash", "nodes": {node id: {"granted": bool,
    "ranks": purchased ranks or None when the node is at its maximum,
    "choice": position or None}}, "clean": the string ends exactly where the
    node list does, with only zero padding after it}."""
    r = _Reader(text)
    out = {"version": r.take(_BITS_VERSION), "spec": r.take(_BITS_SPEC),
           "hash": bytes(r.take(_BITS_HASH_BYTE) for _ in range(_HASH_BYTES)),
           "nodes": {}}
    for nid in order:
        if not r.take(1):
            continue
        if not r.take(1):
            out["nodes"][nid] = {"granted": True, "ranks": None, "choice": None}
            continue
        ranks = r.take(_BITS_RANKS) if r.take(1) else None
        choice = r.take(_BITS_CHOICE) if r.take(1) else None
        out["nodes"][nid] = {"granted": False, "ranks": ranks, "choice": choice}
    out["clean"] = r.rest_is_padding()
    return out


# --- from a logged talent tree to the string ----------------------------------
# Everything below was checked on 2026-11-21 against 160 armory loadouts, four
# per spec over all 40 specs (Raider.IO's public profile API returns the
# game's own string beside its decoded nodes; scripts/fixtures/
# talent_strings.json keeps them, collect_talent_vectors.py collects them,
# test_talent_string.py runs them): each string is reproduced character for
# character from nothing but the list of (entry id, rank) pairs, which is what
# a Warcraft Logs talent tree is.
#
# 83 more strings published by seven guide and statistics sites (35 specs)
# were read the same day: all 83 end exactly where the live tree's node list
# does and re-encode to themselves. 14 of them leave some free nodes unmarked
# where the game marks them -- calculators differ there, and the game's import
# gives a free node regardless -- and none marks a node the rule below does
# not. One, a Fury guide, takes two talents the live game shows only to Arms
# and Protection: a string from an older tree.

class SpecTree:
    """What the encoder has to know about one spec's class tree, read from
    fetch_traits.py's geometry cache (v2). `SpecTree.of` returns None when the
    cache cannot answer (a v1 cache, an unknown spec): no string is better
    than a guessed one."""

    def __init__(self, geo: dict, spec_id: int, tid: str):
        tg = geo["trees"][tid]
        self.spec_id = spec_id
        self.order = sorted(int(n) for n in tg["nodes"])
        self.kind = {int(n): g[2] for n, g in tg["nodes"].items()}      # 0 single 1 tiered 2 choice 3 hero selector
        self.subtree = {int(n): g[3] for n, g in tg["nodes"].items()}
        # class and spec talents the game does not show this spec. A hero
        # talent is judged by its TREE instead (below): the conditions on
        # hero nodes do not name every spec that can take them (they leave
        # Augmentation out of both its hero trees).
        self.hidden = {int(n) for n, who in tg["vis"].items()
                       if spec_id not in who and not self.subtree[int(n)]}
        # a choice node's options in the game's own order (_Index ascending:
        # 1,858 of 1,858 choice nodes in the armory loadouts), NOT entry id order
        opts: dict[int, list] = {}
        self.node_of: dict[int, int] = {}
        self.max_of: dict[int, int] = {}
        entry_sub: dict[int, int] = {}
        for eid, e in geo["entries"].items():
            nid = int(e[0])
            if nid not in self.kind or len(e) < 6:
                continue
            opts.setdefault(nid, []).append((int(e[4]), int(eid)))
            self.node_of[int(eid)] = nid
            self.max_of[int(eid)] = int(e[1])
            entry_sub[int(eid)] = int(e[5])
        self.options = {n: [eid for _i, eid in sorted(v)] for n, v in opts.items()}
        # the spec's own hero trees: the ones its hero selector offers
        self.selectors = [n for n in self.order
                          if self.kind[n] == 3 and n not in self.hidden]
        self.hero = {entry_sub[e]: e for n in self.selectors
                     for e in self.options.get(n, []) if entry_sub.get(e)}
        # what the game hands this spec for free. The string marks every one
        # of them, picked or not -- including the first node of the hero tree
        # the build did NOT take, and one node the game grants a spec without
        # showing it (Restoration Druid, 82241) -- but never a node of a hero
        # tree the spec cannot take (160 of 160 loadouts).
        self.granted = set()
        for n, who in tg["grant"].items():
            n = int(n)
            if who and spec_id not in who:
                continue
            if self.subtree[n] and self.subtree[n] not in self.hero:
                continue
            self.granted.add(n)

    @classmethod
    def of(cls, geo: dict, spec_id: int):
        try:
            tid = str(geo["specs"][str(spec_id)])
            tg = geo["trees"][tid]
            if "vis" not in tg or "grant" not in tg:
                return None
            return cls(geo, int(spec_id), tid)
        except (KeyError, TypeError, ValueError, IndexError):
            return None


def loadout(tree: SpecTree, pairs) -> tuple[dict | None, str]:
    """A logged tree, [(entry id, rank), ...], as `encode`'s `picked`, or
    (None, why) when it is not a loadout of this spec that can be vouched for.
    A tiered node is logged one entry per tier and is one node with the sum."""
    by_node: dict[int, list] = {}
    for eid, rank in pairs:
        nid = tree.node_of.get(int(eid))
        if nid is None:
            return None, "an entry the trait tables do not know"
        by_node.setdefault(nid, []).append((int(eid), int(rank)))
    subs = {tree.subtree[n] for n in by_node if tree.subtree[n]}
    if len(subs) > 1:
        return None, "talents of two hero trees"
    if subs and not subs <= set(tree.hero):
        return None, "a hero tree this spec cannot take"
    picked: dict[int, dict] = {n: {"granted": True, "ranks": 0, "max": 1,
                                   "choice": None} for n in tree.granted}
    for nid, es in by_node.items():
        if nid in tree.granted:
            continue
        if nid in tree.hidden:
            return None, "a talent the game does not show this spec"
        kind = tree.kind[nid]
        if any(r < 1 or r > tree.max_of[e] for e, r in es):
            return None, "a rank outside the talent's range"
        if len({e for e, _r in es}) != len(es):
            return None, "an entry logged twice"
        if kind == 1:                              # tiered: every tier is an entry
            picked[nid] = {"granted": False, "ranks": sum(r for _e, r in es),
                           "max": sum(tree.max_of[e] for e in tree.options[nid]),
                           "choice": None}
            continue
        if len(es) != 1:
            return None, "two options of one choice"
        eid, rank = es[0]
        choice = None
        if kind in (2, 3):
            choice = tree.options[nid].index(eid)
            if choice > 3:
                return None, "a choice beyond the fourth option"
            if kind == 3 and subs and tree.hero.get(next(iter(subs))) != eid:
                return None, "the hero selector points at another tree"
        picked[nid] = {"granted": False, "ranks": rank,
                       "max": tree.max_of[eid], "choice": choice}
    if subs and not any(tree.kind[n] == 3 for n in by_node):
        return None, "hero talents without the hero selector"
    return picked, ""


def tree_stamp(geo: dict, tid) -> str:
    """A fingerprint of everything about one class tree that a string depends
    on: its nodes with their kinds and hero trees, its entries with their
    ranks and option order, who sees and who is granted what. Positions on
    the page are not part of it. A recorded string can only be held against
    the tree it was taken from; this says whether that is still the tree."""
    import hashlib
    import json
    tg = geo["trees"][str(tid)]
    ents = sorted((int(e), v[0], v[1], v[4], v[5])
                  for e, v in geo["entries"].items()
                  if len(v) >= 6 and str(v[0]) in tg["nodes"])
    blob = json.dumps([sorted((int(n), g[2], g[3])
                              for n, g in tg["nodes"].items()), ents,
                       sorted(tg.get("vis", {}).items()),
                       sorted(tg.get("grant", {}).items())],
                      separators=(",", ":"))
    return hashlib.md5(blob.encode()).hexdigest()[:12]


def loadout_string(tree: SpecTree | None, pairs) -> str | None:
    """The game's import string for a logged tree, or None."""
    if tree is None:
        return None
    picked, _why = loadout(tree, pairs)
    if picked is None:
        return None
    return encode(tree.spec_id, tree.order, picked)
