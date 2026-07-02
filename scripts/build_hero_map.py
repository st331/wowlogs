#!/usr/bin/env python3
"""Build a hero-talent lookup table from SimulationCraft's open-source DBC dumps.

Warcraft Logs reports expose combatant talents only as raw trait node IDs
(`combatantInfo.talentTree[].nodeID`).  There is no WCL endpoint that
translates those into hero-talent names, so we derive an offline mapping from
SimulationCraft's generated game data (`engine/dbc/generated/trait_data.inc`,
`midnight` branch), which contains:

  * __trait_data_data      - every trait node, including its sub-tree id
  * __trait_sub_tree_data  - sub-tree id -> hero talent tree name

Output: data/hero_talent_map.json
  {
    "build":           "<wow build the dump was generated from>",
    "subtree_names":   {"59": "Diabolist", ...},
    "node_to_subtree": {"99832": 44, ...},   # trait nodeID -> subtree id
    "entry_to_subtree": {"123347": 44, ...}  # trait node *entry* id -> subtree id
  }
"""
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIMC_URL = (
    "https://raw.githubusercontent.com/simulationcraft/simc/"
    "midnight/engine/dbc/generated/trait_data.inc"
)
CACHE = ROOT / "data" / "raw" / "trait_data.inc"
OUT = ROOT / "data" / "hero_talent_map.json"

# One __trait_data_data row, e.g.:
# { 1, 1, 112112, 90261, 1, 0, 117117, 386164, 0, 0, 1, 2, 200, "Battle Stance",
#   { 73, 0, 0, 0 }, { 73, 0, 0, 0 }, 0, 0 },
# Scalar order (simc trait_data_t): tree_index, id_class, id_trait_node_entry,
# id_node, max_ranks, req_points, id_trait_definition, id_spell,
# id_replace_spell, id_override_spell, row, col, selection_index, name,
# id_spec[4], id_spec_starter[4], id_sub_tree, node_type
ROW_RE = re.compile(
    r"\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),"
    r"\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*\"([^\"]*)\","
    r"\s*\{[^}]*\},\s*\{[^}]*\},\s*(\d+),\s*(\d+)\s*\}"
)
SUBTREE_RE = re.compile(r"\{\s*(\d+),\s*\"([^\"]+)\",\s*(\d+)\s*\}")


def fetch_inc() -> str:
    if CACHE.exists():
        return CACHE.read_text()
    text = urllib.request.urlopen(SIMC_URL, timeout=60).read().decode()
    if "__trait_sub_tree_data" not in text:
        sys.exit("downloaded trait_data.inc looks truncated; not caching")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".inc.tmp")
    tmp.write_text(text)
    tmp.replace(CACHE)
    return text


def main() -> None:
    text = fetch_inc()
    build = "unknown"
    m = re.search(r"wow build ([\d.]+)", text)
    if m:
        build = m.group(1)

    # Sub-tree names live in the tuple array at the end of the file.
    subtree_block = text[text.index("__trait_sub_tree_data"):]
    subtree_names = {int(i): name for i, name, _cls in SUBTREE_RE.findall(subtree_block)}

    trait_block = text[: text.index("__trait_definition_effect_data")]
    node_to_subtree: dict[int, int] = {}
    entry_to_subtree: dict[int, int] = {}
    rows = ROW_RE.findall(trait_block)
    for row in rows:
        entry_id, node_id = int(row[2]), int(row[3])
        sub_tree = int(row[14])
        if sub_tree and sub_tree in subtree_names:
            node_to_subtree[node_id] = sub_tree
            entry_to_subtree[entry_id] = sub_tree

    if len(rows) < 3000 or not node_to_subtree:
        sys.exit(f"parse failure: {len(rows)} rows, {len(node_to_subtree)} hero nodes")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "build": build,
        "subtree_names": {str(k): v for k, v in sorted(subtree_names.items())},
        "node_to_subtree": {str(k): v for k, v in sorted(node_to_subtree.items())},
        "entry_to_subtree": {str(k): v for k, v in sorted(entry_to_subtree.items())},
    }, indent=1))
    tmp.replace(OUT)
    print(f"wow build {build}: {len(rows)} traits parsed, "
          f"{len(node_to_subtree)} hero-tree nodes, {len(subtree_names)} sub-trees -> {OUT}")


if __name__ == "__main__":
    main()
