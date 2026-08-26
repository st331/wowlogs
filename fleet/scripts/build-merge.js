export const meta = {
  name: 'build-merge',
  description: 'Build V4: the owner-directed merge of the three bake-off versions',
  phases: [
    { title: 'Blueprint', detail: 'merge contract -> concrete build blueprint' },
    { title: 'Build', detail: 'v2 chassis + v1 grafts + fixes, Gilded Glass skin' },
    { title: 'QA', detail: 'feedback-compliance + preservation + design audits, fix rounds' },
  ],
}

const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const BP = `${FLEET}/blueprints/build_bp_v4.md`

const INPUTS = `AUTHORITATIVE INPUTS, in priority order:
1. ${FLEET}/feedback_round2.md — the owner's merge contract. EVERY numbered point is mandatory; where it conflicts with anything else, it wins. It also SANCTIONS specific checklist deviations (tooltip removed, pinning removed, Trend tab removed, dormant features removed, section reorder, pulse demoted).
2. ${FLEET}/design_language.md — read the whole doc AND §GG "Gilded Glass" at the end, which overrides the flat-only rules: layered shadows, bar gloss, glass sticky bars, metallic gold gradients on active elements, Cinzel display face. The calm-interaction rules (nothing rotates, color-only hover, content-hugging triggers, centered measure ≤1200/≤960, radii 6/4, no purple, no cursor-following surfaces) still hold.
3. ${FLEET}/checklist.md — the preservation contract for everything the feedback does not explicitly change.
4. ${FLEET}/user_prefs.md — standing owner preferences.
5. Source material (all committed on this branch): ${FLEET}/builds/v2.html (the CHASSIS — start from this file), ${FLEET}/builds/v1.html (donor: sidebar groups, comps-header slider, complete Data Table with fixed p30-p85 spread column, active-modifier badges), ${FLEET}/builds/v3.html (reference only).
ARCHON DISTANCE: archon.gg = pure black + purple/violet + heavy bold sans (${SP}/archon_page.png). Never purple; never lookalike.`

const VERIFY = `SELF-VERIFICATION (mandatory): serve your worktree's site/ with the real payload (copy ${SP}/livesite/data.json.gz beside index.html; free port 8985-8989) and drive with playwright (executablePath '/opt/pw-browsers/chromium'; run node scripts from ${SP}; allow 2-5s post-load). Zero console errors (the sandbox's Google Fonts reset is exempt). Explicitly test: section order and Overview-first default; every table's every column sorts both ways (Pulse included); Skill compare with a CUSTOM B percentile; deaths tab under Skill compare shows the A/B quantile read; top-bar buttons hold position across Off/Time/Skill; no tooltip appears on any hover; no pin UI exists; no dormant rows render; Archon round-trip (on -> divergence auto-uncheck -> restore).`

// ---------- Blueprint ----------
phase('Blueprint')
const bp = await agent(`You are the merge architect for VERSION 4 of a WoW Mythic+ dashboard — the owner reviewed three complete versions and dictated a precise merge. Read ${FLEET}/feedback_round2.md first and treat it as scripture, then the other inputs.

${INPUTS}

Write the build blueprint to ${BP}: 1) the exact section order and defaults; 2) the top-bar layout with the layout-stability mechanism for Off/Time/Skill (reserved slots / fixed sub-row) and the custom-B-percentile control; 3) the sidebar plan (v1 groups, minus removed items; comp-min relocation to the Comps header); 4) the deaths-under-Skill-compare design (per-group deaths distributions, qp at pA vs pB, honest captions; state what happens on the Deathless tab); 5) universal-sortability plan for every table incl. Pulse and Comps; 6) removals (tooltip, pinning, dormant rendering, Trend tab) and what replaces each need (details live in the complete v1-style Data Table); 7) Lab/modifier design: active-only rendering, v1-style active badges, one-deletion retirement of 4pc with zero residue; 8) Gilded Glass application notes; 9) migration map covering every checklist item -> unchanged / relocated / sanctioned-removed (cite the feedback line for each removal); 10) risks. Under 350 lines, concrete. Return a 10-line summary.`,
  { label: 'blueprint:v4', phase: 'Blueprint', effort: 'high' })

// ---------- Build ----------
phase('Build')
const build = await agent(`You are the implementation lead for VERSION 4 (single-file site/index.html, vanilla JS, no framework, same payload). You work in a git worktree branched from the current branch HEAD.

START: overwrite site/index.html in your worktree with ${FLEET}/builds/v2.html (the chassis), then implement ${BP} — graft the v1 pieces from ${FLEET}/builds/v1.html (its sidebar structure, comps-header slider, complete Data Table incl. the fixed p30-p85 spread column, active-modifier badges), apply every feedback item, and restyle per §GG Gilded Glass. Reuse proven logic; do not rewrite algorithms that both donors share (aggregation, aggregateElite, qp, set-bonus math, comps model). New logic needed: per-group deaths distributions for the Skill-compare deaths read; custom B percentile; Pulse/Comps column sorting.

Blueprint summary:
${bp || '(read the blueprint file directly)'}

${INPUTS}

${VERIFY} Screenshots to ${SP}/v4_full.png and ${SP}/v4_top.png at 1600x1000.

Commit in the worktree, then copy the finished site/index.html to ${FLEET}/builds/v4.html in the MAIN checkout /home/user/wowlogs (no git commands in the main checkout — a checkpoint loop commits it). Return: worktree path, branch, commit, structural summary, and a feedback-item-by-item compliance list.`,
  { label: 'build:v4', phase: 'Build', isolation: 'worktree', effort: 'high' })

// ---------- QA ----------
const QA_SCHEMA = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['pass', 'fail'] },
  blockers: { type: 'array', items: { type: 'string' } },
  minors: { type: 'array', items: { type: 'string' } },
  notes: { type: 'string' },
}, required: ['verdict', 'blockers', 'minors', 'notes'] }

const audits = [
  { key: 'feedback', brief: `You are the FEEDBACK-COMPLIANCE auditor. Serve the build (copy ${SP}/livesite/data.json.gz beside its index.html; port 8985-8989; playwright from ${SP}) and walk ${FLEET}/feedback_round2.md item by item IN THE BROWSER: removals actually gone (hover anything — no tooltip ever; no pin affordance; no dormant/greyed placeholder rows; no Trend tab); section order exact with Overview first/expanded; v2-style Pulse demoted to slot 5 with EVERY column sortable both ways; comp-min in the Comps header; custom B percentile works (set e.g. 42); deaths tab responds to Skill compare with labeled quantiles; top-bar clickable positions identical across Off/Time/Skill (measure getBoundingClientRect before/after); v1-style Data Table completeness incl. spread column; active-modifier badges present when 4pc filter on and FULLY absent when off. Any unmet feedback item = blocker.` },
  { key: 'preserve', brief: `You are the PRESERVATION auditor. Serve the build (as above) and walk ${FLEET}/checklist.md, SKIPPING items ${FLEET}/feedback_round2.md explicitly sanctions away (tooltips, pinning, Trend tab, dormant rendering, section order). Spot-check ~35 surviving items: percentile lens relabels everything; Archon exact snapshot/restore/auto-uncheck + elite path; tier filters + hasTier gating; Set Bonus sorting NaN-parked; Time compare defaults current-vs-last; merge hero, timed-only, role/region/class filters, reset, toast, llms/data links, KPI/date visibility. Broken surviving item = blocker.` },
  { key: 'design', brief: `You are the DESIGN auditor. Serve the build (as above) and check §GG Gilded Glass of ${FLEET}/design_language.md: layered panel shadows present, bar gloss present, glass (translucent+blur) sticky bars, metallic gradient on active elements, Cinzel display face — AND the unchanged hard rules: zero rotation (computed-style sweep incl. pseudo-elements), radii 6/4, calm hover (nothing grows/moves/glows on cursor pass), content-hugging triggers, centered bounded measure, no full-bleed data surfaces. ARCHON DISTANCE vs ${SP}/archon_page.png: any purple or lookalike impression = blocker. Missing glass/metal treatment = blocker (the owner explicitly asked); token drift = minor.` },
]

phase('QA')
let report = build || 'BUILD RETURNED NOTHING — inspect the newest worktree and fleet/builds/v4.html'
let finalQA = []
for (let round = 1; round <= 3; round++) {
  const results = (await parallel(audits.map(a => () => agent(
    `BUILD REPORT (locate the worktree here):\n${report}\n\n${a.brief}\n\nReturn verdict/blockers/minors/notes; 'fail' only if blockers non-empty; each blocker names element, action, expected vs observed.`,
    { label: `qa:${a.key}:r${round}`, phase: 'QA', schema: QA_SCHEMA })))).filter(Boolean)
  finalQA = results
  const blockers = results.flatMap(r => r.blockers)
  log(`v4 QA round ${round}: ${blockers.length} blockers / ${results.flatMap(r => r.minors).length} minors`)
  if (!blockers.length || round === 3) break
  const fix = await agent(`You are the fix lead on VERSION 4. Build report:\n${report}\n\nFix every BLOCKER in the worktree (quick-win minors too):\n${blockers.map((b, i) => (i + 1) + '. ' + b).join('\n')}\n\n${INPUTS}\n\n${VERIFY}\n\nCommit in the worktree and re-copy to ${FLEET}/builds/v4.html. Return: commit + per-blocker fix + verification.`,
    { label: `fix:r${round}`, phase: 'QA', effort: 'high' })
  report = report + '\n\nFIX ROUND ' + round + ':\n' + (fix || '(fixer returned nothing)')
}

return {
  blueprint: (bp || '').slice(0, 1000),
  buildReport: report.slice(0, 4000),
  qa: finalQA.map(q => ({ verdict: q.verdict, blockers: q.blockers, minors: q.minors.slice(0, 8) })),
  clean: finalQA.length > 0 && finalQA.every(q => q.verdict === 'pass'),
}
