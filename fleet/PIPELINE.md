# Fleet pipeline state — survives container recycles. Read this first on any resume.

## Where we are
A 30-blueprint IA tournament completed (2026-08-25 ~23:00 UTC). Podium, with full judge
notes preserved in `podium_notes.md`:
1. **E01** (268) — Command Center synthesis (decision ladder, sticky Command Bar, LAB manifest)
2. **E27** (255) — Skill Lens (percentile as global instrument, two-axis compare)
3. **E19** (254) — Evidence Ledger (trust is architecture, generated scope lines)
(E15 248, E17 242, E06 233 were the other finalists.)

The plan the owner approved: build all three as complete sites sharing the "Candlelit
Ledger" v2 design language (`design_language_essence.md`), QA each (preservation +
design/Archon-distance), run a comparative owner-task battery, deploy side by side at
/v1 /v2 /v3 on GitHub Pages (root site untouched), and deliver Claude's ranked
1-2-3 recommendation with reasons. Prior owner instructions: percentile ghost-compare
and pin tray must ship in v1 (judge veto); any blueprint deviation flagged
"needs owner sign-off" (trend Overlay→Grid default, Best/Worst sort relabel) resolves
CONSERVATIVELY — keep current defaults, list as reversible options.

## What a fresh container loses (and the fix)
/tmp scratchpad, session workflow scripts/journals, and git worktrees are ephemeral.
The original 30 blueprints, rubric, persona digest, and full design_language.md were
lost to a recycle on 2026-08-26 ~00:40 UTC. Everything needed to rebuild is in this
fleet/ directory. COMMIT AND PUSH every meaningful intermediate to this branch.

## Standing constraints (owner)
- Push ONLY to branch claude/wow-mythic-dashboard-jv235l. No PRs unless asked.
- 70% hourly WCL quota cap (collection side; irrelevant to this client-only work).
- Times to the owner in IST; UTC in site/data.
- Site: single-file site/index.html, vanilla JS, same payload, all 92 checklist items
  preserved (see reconstructed checklist in this dir once regenerated).
- Deploys: pushing site/** triggers .github/workflows/deploy-site.yml (~30s to live).
- deploy-site ships site/** only, excluding data files — fleet/ never reaches the site.

## Remaining steps
1. Regenerate inventory checklist (agent vs site/index.html) -> fleet/checklist.md
2. Regenerate full design_language.md from essence + live page -> fleet/design_language.md
3. Per winner: reconstruct build blueprint from thesis+judge notes -> build -> QA
   (builds saved as fleet/builds/v{1,2,3}.html, committed as they land)
4. Comparative task battery across the three; assemble site/v1|v2|v3 (patch data fetch
   to ../data.json.gz), push once, verify live
5. Report to owner: three links + ranked recommendation + parked sign-off questions
