export const meta = {
  name: 'build-one',
  description: 'Reconstruct one podium blueprint, build it as a complete site, audit and fix',
  phases: [
    { title: 'Blueprint', detail: 'full build blueprint from thesis + judge grafts/vetoes + checklist' },
    { title: 'Build', detail: 'implement in an isolated worktree with the Candlelit Ledger language' },
    { title: 'QA', detail: 'preservation + design/Archon-distance audits, up to 2 fix rounds' },
  ],
}

// args: {n: 1|2|3, id: 'E01'|'E27'|'E19'}
const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const N = args.n, ID = args.id
const BP = `${FLEET}/blueprints/build_bp_v${N}.md`

const INPUTS = `AUTHORITATIVE INPUTS (read ALL before touching code):
1. ${BP} — the structural blueprint for THIS version. Implement it faithfully; its migration map is the contract.
2. ${FLEET}/design_language.md — "Candlelit Ledger" v2 visual language, shared by all versions. Copy token values verbatim; its five hard rules (nothing rotates; centered bounded measure ≤1200px main / ≤960px chart; content-hugging triggers with a docked click-pin inspector instead of cursor tooltips; calm color-only hover; accent budget, never purple) fail the build if broken. ${FLEET}/design_language_essence.md is the condensed authority if the full doc is ambiguous.
3. ${FLEET}/user_prefs.md — owner preferences; they override anything conflicting.
4. ${FLEET}/checklist.md — the preservation checklist. Every item must survive.
5. The current page /home/user/wowlogs/site/index.html is both the logic donor AND the styling base (it already carries redesign v1 + the owner's calm-UI hotfixes).
ARCHON DISTANCE: archon.gg today = pure black ground + purple/violet accent + heavy bold geometric sans (screenshot ${SP}/archon_page.png). Inspired-at-most, never lookalike; no purple/violet anywhere.`

const VERIFY = `SELF-VERIFICATION (mandatory before returning): serve your worktree's site/ with the real payload (copy ${SP}/livesite/data.json.gz beside index.html; python3 -m http.server on a free port in 89${N}0-89${N}9) and drive it with playwright (executablePath '/opt/pw-browsers/chromium'; run node scripts from ${SP} so the playwright package resolves; the 540k-row payload needs 2-5s after load). Zero console errors.`

// ---------- Blueprint ----------
phase('Blueprint')
const bp = await agent(`You are the blueprint architect for tournament winner ${ID}, to be built as VERSION ${N} of three competing sites. The original ~450-line blueprint was lost; its thesis, page map, refine decisions, judge grafts, and judge vetoes are preserved in ${FLEET}/podium_notes.md (read your ${ID} section AND the "Shared build rules" section closely — the grafts are demands and the vetoes are absolute).

Also read: ${FLEET}/checklist.md (the preservation contract), ${FLEET}/user_prefs.md, ${FLEET}/design_language_essence.md (structure must assume this skin), and skim /home/user/wowlogs/site/index.html for what exists today.

Write the full build blueprint to ${BP} (create dirs as needed): 1) Design thesis (5 lines); 2) Page map — every region top-to-bottom with the sections/controls each contains and default state; 3) Control architecture — every existing control in a purposeful group with placement, plus the transient/LAB home and lifecycle; 4) Migration map — a table covering EVERY checklist item -> new location/behavior (or "unchanged"), nothing dropped; 5) Interaction contracts — control-change visibility, compare mode, Archon entry/exit, percentile relabeling, defaults (NEVER change a current default; park flagged deviations as reversible options in Risks); 6) What's new (each item justified by the judge notes, client-side implementable from the existing payload); 7) Risks & parked owner questions. Under ~450 lines, decisive and concrete. Return a 10-line summary.`,
  { label: `blueprint:${ID}`, phase: 'Blueprint', effort: 'high' })

// ---------- Build ----------
phase('Build')
const build = await agent(`You are the implementation lead rebuilding a WoW Mythic+ dashboard's single site/index.html (vanilla JS, no framework, no build step — one file, same data payload). You work in a git worktree branched from the current HEAD of claude/wow-mythic-dashboard-jv235l.

THIS IS VERSION ${N} of three (tournament winner ${ID}). Rebuild site/index.html to the blueprint's structure with the design language's skin. Reuse the existing battle-tested JS logic wherever behavior is unchanged — especially the aggregation core, aggregateElite/Archon-mode snapshot-restore contract (archonPrev, applyArchonState, __archonMatches), percentile machinery (qp/dpsLabel/TABS label mutation), Set Bonus computation+sorting, tier filters and hasTier gating, compare mode, trend rendering, week-chip builders, and the update-toast script. Restructuring where things LIVE is the job; rewriting proven algorithms is not. Preserve every checklist item and keep existing element ids wherever the element survives.

Blueprint summary for orientation:
${bp || '(blueprint agent returned nothing — read the blueprint file directly)'}

${INPUTS}

${VERIFY} Screenshot full page to ${SP}/v${N}_full.png and top viewport to ${SP}/v${N}_top.png at 1600x1000.

When the build passes your own verification: commit in the worktree, AND copy your finished site/index.html to ${FLEET}/builds/v${N}.html in the MAIN checkout /home/user/wowlogs (create the dir; do NOT run git commands in the main checkout — a checkpoint loop commits it). Return (plain text): worktree path, branch, commit hash, a section-by-section structural summary, and any checklist item you could not place (should be none).`,
  { label: `build:v${N}`, phase: 'Build', isolation: 'worktree', effort: 'high' })

// ---------- QA loop ----------
const QA_SCHEMA = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['pass', 'fail'] },
  blockers: { type: 'array', items: { type: 'string' } },
  minors: { type: 'array', items: { type: 'string' } },
  notes: { type: 'string' },
}, required: ['verdict', 'blockers', 'minors', 'notes'] }

const audits = [
  { key: 'preserve', brief: `You are the preservation auditor for VERSION ${N}. Locate the worktree from the build report and serve ITS site/index.html yourself (copy ${SP}/livesite/data.json.gz beside it; free port 89${N}0-89${N}9; playwright chromium '/opt/pw-browsers/chromium', scripts run from ${SP}; allow 2-5s post-load). Walk ${FLEET}/checklist.md against the page IN THE BROWSER — spot-check ~40 items across ALL regions, prioritizing behavior contracts: percentile slider 0-99 relabels every DPS reading (tab labels, captions, Set Bonus columns); Archon mode applies, restores exactly on toggle-off, auto-unchecks on divergence; tier filters gate on gear-visible parses; Set Bonus sorts every column both directions, NaN parked last; compare defaults current-vs-last reset; trend, merge-hero, timed-only, role/region/class filters, reset-filters, update toast. Missing or broken checklist item = blocker.` },
  { key: 'language', brief: `You are the design auditor for VERSION ${N}. Serve the build's site (as above) and check the five hard rules of ${FLEET}/design_language.md: computed-style sweep for ANY rotation incl. ::before/::after; centered bounded measure, nothing full-bleed; content-hugging triggers with empty row/label space inert and a docked/click-pin inspector rather than cursor-chasing tooltips; calm hover (nothing grows/glows/moves on cursor pass); accent budget. Check radii ~6px panels / ~4px controls and ${FLEET}/user_prefs.md compliance. Then ARCHON DISTANCE vs ${SP}/archon_page.png (pure black + purple + heavy bold sans): lookalike impression or ANY purple/violet accent = blocker. Hard-rule break = blocker; token drift = minor.` },
]

phase('QA')
let report = build || 'BUILD RETURNED NOTHING — inspect the newest worktree under /home/user/wowlogs/.claude/worktrees/ and fleet/builds/'
let finalQA = []
for (let round = 1; round <= 3; round++) {
  const results = (await parallel(audits.map(a => () => agent(
    `BUILD REPORT (find the worktree here):\n${report}\n\n${a.brief}\n\nReturn verdict/blockers/minors/notes; verdict 'fail' only if blockers non-empty. Each blocker names the element, action, expected vs observed.`,
    { label: `qa:${a.key}:r${round}`, phase: 'QA', schema: QA_SCHEMA })))).filter(Boolean)
  finalQA = results
  const blockers = results.flatMap(r => r.blockers)
  log(`v${N} QA round ${round}: ${blockers.length} blockers / ${results.flatMap(r => r.minors).length} minors`)
  if (!blockers.length || round === 3) break
  const fix = await agent(`You are the fix lead on VERSION ${N} (${ID}). Build report (locate worktree/branch):\n${report}\n\nFix every BLOCKER below in the worktree (and quick-win minors):\n${blockers.map((b, i) => (i + 1) + '. ' + b).join('\n')}\n\n${INPUTS}\n\n${VERIFY}\n\nCommit in the worktree and re-copy the fixed index.html to ${FLEET}/builds/v${N}.html (no git in the main checkout). Return: commit hash + per-blocker fix + verification.`,
    { label: `fix:r${round}`, phase: 'QA', effort: 'high' })
  report = report + '\n\nFIX ROUND ' + round + ':\n' + (fix || '(fixer returned nothing)')
}

return {
  version: N, entrant: ID,
  blueprint: (bp || '').slice(0, 1200),
  buildReport: report.slice(0, 4000),
  qa: finalQA.map(q => ({ verdict: q.verdict, blockers: q.blockers, minors: q.minors.slice(0, 8) })),
  clean: finalQA.length > 0 && finalQA.every(q => q.verdict === 'pass'),
}
