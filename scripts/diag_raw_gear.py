#!/usr/bin/env python3
"""What does a RAW Warcraft Logs gear entry carry for a tier piece?

Diag v1/v2 (2026-09-08) established that the bonus ids the collector keeps
for set items never include an ItemBonus type-2 secondary-stat allocation:
every id decodes to upgrade track, item-level delta, drop-context tag
(Mythic+/Heroic), Catalyst slot marker (type 38), tertiary or socket. Before
concluding that the 12.1 inherited-stat pair is simply not in the data, this
looks at the one place not yet inspected: the raw Summary-table gear entry,
with EVERY key, in case Warcraft Logs relays something compact_gear() drops
(item modifiers, crafted stats, a stats block on the entry).

Costs three Summary-table requests (~8 points). Reads the tail of the gear
journal to pick three recent fights whose wearers have a Mythic+-context
(bonus 13440, i.e. Catalyst-converted) tier piece equipped.
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from wcl_client import WCLClient  # noqa: E402

GEAR = ROOT / "data" / "processed" / "gear.jsonl"
CRAFTED = set(json.loads((ROOT / "data" / "crafted_ids.json").read_text()))
TAIL_BYTES = 4_000_000


def recent_fights(n: int) -> list[tuple[str, int, str]]:
    """(report code, fight id, character) for the newest journal records that
    equip a set item carrying the Mythic+ context tag."""
    out, seen = [], set()
    with GEAR.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        chunk = fh.read().decode("utf-8", "replace")
    lines = chunk.split("\n")[1:]                 # first line is torn
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        gear = rec.get("gear") or []
        hit = any(isinstance(it, dict) and it.get("set")
                  and 13440 in (it.get("bonus") or []) for it in gear)
        code = rec.get("report_code")
        if hit and code and code not in seen:
            seen.add(code)
            out.append((code, int(rec["fight_id"]), rec.get("character")))
            if len(out) >= n:
                break
    return out


def main() -> None:
    picks = recent_fights(3)
    if not picks:
        sys.exit("[diag] no recent Mythic+-context set wear in the journal tail")
    print(f"[diag] fights: {picks}")
    parts = [f'a{i}: report(code: "{c}") '
             f'{{ table(fightIDs: [{f}], dataType: Summary) }}'
             for i, (c, f, _) in enumerate(picks)]
    client = WCLClient(verbose=True)
    data = client.query("{ reportData { " + " ".join(parts) + " } }",
                        est_cost=2.6 * len(parts))
    rd = data.get("reportData") or {}
    shown_set = shown_craft = shown_plain = 0
    for i, (code, fid, who) in enumerate(picks):
        table = (rd.get(f"a{i}") or {}).get("table") or {}
        d = table.get("data") if isinstance(table, dict) else None
        if not isinstance(d, dict):
            print(f"[diag] {code}#{fid}: no summary data")
            continue
        print(f"\n[diag] {code}#{fid} summary keys: {sorted(d)}")
        details = d.get("playerDetails") or {}
        for role in ("tanks", "healers", "dps"):
            for p in details.get(role) or []:
                ci = p.get("combatantInfo")
                if not isinstance(ci, dict):
                    continue
                if p.get("name") == who or shown_set == 0:
                    print(f"  player keys: {sorted(p)}")
                    print(f"  combatantInfo keys: {sorted(ci)}")
                    for k, v in ci.items():
                        if k not in ("gear", "talentTree", "talents",
                                     "customPowerSet", "secondaryCustomPowerSet",
                                     "tertiaryCustomPowerSet", "pvpTalents"):
                            print(f"    ci.{k} = {json.dumps(v)[:400]}")
                for it in ci.get("gear") or []:
                    if not isinstance(it, dict) or not it.get("id"):
                        continue
                    if it.get("setID") and shown_set < 6:
                        shown_set += 1
                        print(f"  SET item raw: {json.dumps(it)}")
                    elif it.get("id") in CRAFTED and shown_craft < 3:
                        shown_craft += 1
                        print(f"  CRAFTED item raw: {json.dumps(it)}")
                    elif shown_plain < 2:
                        shown_plain += 1
                        print(f"  plain item raw: {json.dumps(it)}")
                # union of every key any gear entry carries, so a rare
                # field cannot hide behind the six examples above
                keys = set()
                for it in ci.get("gear") or []:
                    if isinstance(it, dict):
                        keys |= set(it)
                print(f"  {p.get('name')}: union of gear-entry keys = {sorted(keys)}")
    print(f"\n[diag] points spent this hour: {client.spent:.0f}/{client.limit:.0f}")


if __name__ == "__main__":
    main()
