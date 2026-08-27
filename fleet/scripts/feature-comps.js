export const meta = {
  name: 'feature-comps',
  description: 'Design and implement "top comps for this spec" end to end',
  phases: [
    { title: 'Investigate', detail: 'map the comp model, tooltip, and candidate surfaces' },
    { title: 'Design', detail: 'three competing designs against the persona' },
    { title: 'Judge', detail: 'persona + coherence judges pick the winner' },
    { title: 'Build', detail: 'implement in site/index.html with browser verification' },
    { title: 'QA', detail: 'persona usability + regression audits, fix rounds' },
  ],
}

const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const SRC = '/home/user/wowlogs/site/index.html'
const ENV = `Site source: ${SRC} (single-file, ~3,300 lines). Live mirror with the real payload: http://127.0.0.1:8901/index.html (playwright chromium at '/opt/pw-browsers/chromium'; run node scripts from ${SP} so the playwright package resolves; allow 2-5s after load). Contract: ${FLEET}/feature_comps.md. Persona and standing prefs: ${FLEET}/user_prefs.md, ${FLEET}/feedback_round2.md. Skin: ${FLEET}/design_language.md (§GG at the end governs).`

phase('Investigate')
const facts = await agent(`${ENV}

You are the investigator for a new feature: "top comps that a given spec runs with" (top = most-run). Produce a FACTS file other agents will rely on — read the code and verify in the browser; no speculation.

Map precisely: 1) the comp model — where comps are computed, their data shape (members, run counts, strength, timer margin), the min-runs gate (#compmin), and the all-qualifying denominator used by Pulse's "in K of Q comps" column, with function/variable names and line numbers; 2) cost/feasibility of "top N comps containing spec X sorted by runs" from the existing model (no recomputation drift); 3) the tooltip today — exact content inventory, layout, the 450ms/position-once/trigger contract, where tipHTML builds it; 4) every candidate surface: tooltip, Top Comps section (its header controls, sorting, how the sidebar Spec filter currently affects which comps show — verify in browser with a spec filter applied), the Data Table row (what bar-click jump shows today), Pulse comps column, section subs; 5) interaction inventory: what click/hover on bars, labels, table rows, comp rows currently do — so a design cannot double-book a gesture; 6) how many comps typically contain one spec at defaults (measure for 3 specs: a popular one, a mid one, a rare one) and at keys 17-19, so designs know real cardinalities.

Write the facts to ${SP}/comps_facts.md and return it in full as your final text (both).`,
  { label: 'investigate', phase: 'Investigate', effort: 'high' })

phase('Design')
const ANGLES = [
  { key: 'tooltip-reorg', brief: `TOOLTIP-FIRST: the answer lives in the hover tooltip, which you REORGANIZE into scannable groups (e.g. two-column stat grid + a compact "Top comps" block: top 3 by runs, each comp as compact class-colored spec abbreviations + run count + "of Q"). The tooltip must get BETTER organized than today while gaining content — grouping, alignment, hierarchy. Also state where the answer lives for someone who never hovers.` },
  { key: 'comps-filter', brief: `COMPS-SECTION-FIRST: the Top Comps section gains a "containing spec" filter — e.g. a compact select or class-colored chip row in the section header (next to the min-runs slider), plus a direct gesture from the overview (e.g. a small affordance on a bar row or its data-table row that jumps to Top Comps pre-filtered, with a visible active-filter chip + one-click clear). The full ranked list of that spec's comps becomes visible, sortable, with the standard denominator line. State what (if anything) the tooltip gains.` },
  { key: 'inline-panel', brief: `IN-PLACE-FIRST: the answer appears where the user already is — e.g. clicking a bar/label (which today jumps to the Data Table row) instead/additionally reveals a compact inline detail strip for that spec (top 3-5 comps by runs, with counts and "of Q"), rendered as a calm, bounded row under the bar or beside the Data Table row highlight — no floating UI, no pinning. Reconcile with the existing bar-click jump so no gesture is double-booked.` },
]
const designs = await parallel(ANGLES.map(a => () => agent(`${ENV}

FACTS (verified by the investigator — trust these):
${facts}

You are one of three competing designers for the "top comps for this spec" feature. ${a.brief}

Deliver a concrete, implementable design (markdown, <=120 lines): exact placement and markup sketch, interaction flow from a cold page (count the interactions to the answer for the persona), how numbers stay identical to the existing comp model/denominator, sorting behavior, Gilded Glass styling notes, edge cases (spec in 0 comps; spec below comp gate; Archon mode; compare modes; narrow viewport), and what you deliberately did NOT add. The persona must reach the answer in <=2 interactions and discover the feature without instructions. Return the design only.`,
  { label: 'design:' + a.key, phase: 'Design' })))

phase('Judge')
const JUDGE_SCHEMA = { type: 'object', properties: {
  scores: { type: 'array', items: { type: 'object', properties: {
    design: { type: 'integer' }, score: { type: 'integer' }, notes: { type: 'string' } },
    required: ['design', 'score', 'notes'] } },
  winner: { type: 'integer' },
  grafts: { type: 'string' }, vetoes: { type: 'string' },
}, required: ['scores', 'winner', 'grafts', 'vetoes'] }
const verdicts = (await parallel([
  `Judge AS THE PERSONA: replay the daily flow ("is my alt viable in real comps?", "who does Arcane actually run with?") through each design step by step, counting interactions and attention cost. Discoverability without instructions weighs heavily; so does not adding hover noise to a page the owner sweeps daily.`,
  `Judge as the COHERENCE auditor: single comp model (no drifting numbers), gesture conflicts, tooltip contract survival, sortability rule, no-dormant/no-pinning rules, Gilded Glass fit, implementation risk in a single-file page, and whether the design stays clean when the comps meta consolidates mid-season.`,
].map((lens, i) => () => agent(`${ENV}

FACTS:\n${facts}\n\n${lens}\n\nTHE THREE DESIGNS:\n${designs.map((d, j) => `### DESIGN ${j + 1} (${ANGLES[j].key})\n${d}`).join('\n\n')}\n\nScore each 0-100, pick a winner, name must-graft ideas from the losers and vetoes.`,
  { label: 'judge:' + (i ? 'coherence' : 'persona'), phase: 'Judge', schema: JUDGE_SCHEMA, effort: 'high' })))).filter(Boolean)
const tally = [0, 0, 0]
for (const v of verdicts) for (const s of v.scores) if (s.design >= 1 && s.design <= 3) tally[s.design - 1] += s.score
const win = tally.indexOf(Math.max(...tally))
log('Design scores: ' + tally.join(' / ') + ' -> winner: ' + ANGLES[win].key)

phase('Build')
const build = await agent(`${ENV}

You implement the winning design for "top comps for this spec" directly in ${SRC} (edit in place; do NOT run any git commands).

FACTS:\n${facts}

WINNING DESIGN (${ANGLES[win].key}):\n${designs[win]}

JUDGE GRAFTS AND VETOES (apply grafts unless they break the winner's coherence; vetoes are absolute):\n${verdicts.map((v, i) => `Judge ${i + 1}: grafts: ${v.grafts} | vetoes: ${v.vetoes}`).join('\n')}

Honour every rule in ${FLEET}/feature_comps.md. Reuse the existing comp model verbatim — one computation, one denominator. VERIFY in the browser before returning: serve ${'/home/user/wowlogs/site/'} on a port in 8996-8999 with the payload already in site/ (if you copy a payload in, delete it after); confirm the persona flow end to end (cold load -> the answer for Arcane Mage in <=2 interactions), the feature's numbers equal the Top Comps section's for the same comp, tooltip contract intact (450ms delay, position-once, bar/label-only triggers, no growth in trigger area), all new columns/lists sortable if tabular, zero console errors (Google Fonts reset exempt), and screenshots ${SP}/comps_feat_1.png (the feature in use) + ${SP}/comps_feat_2.png (the surface at rest). Return: exact edits (line ranges), the verification results, and screenshots list.`,
  { label: 'build', phase: 'Build', effort: 'high' })

phase('QA')
const QA_SCHEMA = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['pass', 'fail'] },
  blockers: { type: 'array', items: { type: 'string' } },
  minors: { type: 'array', items: { type: 'string' } },
  notes: { type: 'string' } }, required: ['verdict', 'blockers', 'minors', 'notes'] }
let report = build || '(build agent returned nothing — audit site/index.html directly)'
let finalQA = []
for (let round = 1; round <= 3; round++) {
  const results = (await parallel([
    { key: 'persona', brief: `You are the OWNER-PERSONA auditor. Serve ${'/home/user/wowlogs/site/'} (payload is in site/; port 8996-8999; playwright from ${SP}) and, WITHOUT reading the design docs first, try to answer "what are the top comps Arcane Mage is running with?" from a cold load as the owner would. Then read ${FLEET}/feature_comps.md and the build report and audit: <=2 interactions, discoverable, numbers match the Top Comps section, useful for a popular AND a rare spec, works at keys 17-19, no hover noise added to casual page sweeps. Unmet contract line = blocker.` },
    { key: 'regression', brief: `You are the REGRESSION auditor. Serve the site (as above) and verify the feature broke nothing: tooltip contract (450ms delay, position-once, bar+label-text triggers only, empty space inert, hides on leave); bar-click behavior consistent with the build report's stated design; Top Comps section sorting + min-runs slider; Pulse "in K of Q" column unchanged; compare on/off roster identical at keys 17-19; Archon round-trip (on -> divergence auto-uncheck -> restore); percentile relabel at p85; section order; zero console errors. Any regression = blocker.` },
  ].map(a => () => agent(`BUILD REPORT:\n${report}\n\n${a.brief}\n\nReturn verdict/blockers/minors/notes; 'fail' only if blockers non-empty; each blocker names element, action, expected vs observed.`,
    { label: `qa:${a.key}:r${round}`, phase: 'QA', schema: QA_SCHEMA })))).filter(Boolean)
  finalQA = results
  const blockers = results.flatMap(r => r.blockers)
  log(`QA round ${round}: ${blockers.length} blockers`)
  if (!blockers.length || round === 3) break
  const fix = await agent(`${ENV}\n\nYou are the fix lead. Build report:\n${report}\n\nFix every blocker in ${SRC} (no git):\n${blockers.map((b, i) => (i + 1) + '. ' + b).join('\n')}\n\nRe-verify in the browser as the build agent did. Return commit-free edit summary + verification.`,
    { label: 'fix:r' + round, phase: 'QA', effort: 'high' })
  report += '\n\nFIX ROUND ' + round + ':\n' + (fix || '(fixer returned nothing)')
}

return { winner: ANGLES[win].key, tally,
  build: (build || '').slice(0, 3000),
  qa: finalQA.map(q => ({ verdict: q.verdict, blockers: q.blockers, minors: q.minors.slice(0, 6) })),
  clean: finalQA.length > 0 && finalQA.every(q => q.verdict === 'pass') }
