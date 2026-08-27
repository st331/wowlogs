export const meta = {
  name: 'specframe-build',
  description: 'Build the Ledger Rail spec frame per blueprint, audit, fix',
  phases: [
    { title: 'Build', detail: 'implement in site/index.html with browser verification' },
    { title: 'QA', detail: 'persona + regression audits, fix rounds' },
  ],
}
const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const ENV = `Blueprint (the contract — every §0 veto is a build gate): ${FLEET}/blueprints/specframe.md. Feature contracts: ${FLEET}/feature_specframe.md, ${FLEET}/feature_comps.md. Prefs: ${FLEET}/user_prefs.md. Skin: ${FLEET}/design_language.md (§GG). Edit /home/user/wowlogs/site/index.html IN PLACE; do NOT run git. Serve /home/user/wowlogs/site/ (payload present) on a port in 8996-8999; playwright chromium '/opt/pw-browsers/chromium', node from ${SP}.`

phase('Build')
const build = await agent(`${ENV}

Implement the Ledger Rail exactly per the blueprint. The live payload does NOT yet carry specstats, so verify BOTH modes: (1) real payload — frame opens with identity + comps blocks, no stats block, no placeholder; (2) synthetic mode — copy the payload, inject a synthetic "specstats" block exactly matching the blueprint §9 interface (a few specs incl. Mage|Arcane, two flask variants for one spec), serve that copy separately, and verify the stats block renders quantile rows p50-descending with the cohort line verbatim, flask chips slice correctly with the "n=X of Y (flask-known)" phrasing, chips absent for specs without flask variants, and the whole frame obeys the vetoes (no layout shift on open — measure document heights; no focus steal; ESC closes; arrow keys step the ladder; same-bar click closes; sortable mini comps table cap-5-after-sort matching the Top Comps numbers; no Cinzel inside the frame). Zero console errors both modes (Google Fonts reset exempt). Screenshots: ${SP}/frame_1.png (frame open, comps), ${SP}/frame_2.png (synthetic stats + flask chips), ${SP}/frame_3.png (1366x768). Return exact edits (line ranges) + verification results.`,
  { label: 'build', phase: 'Build', effort: 'high' })

phase('QA')
const QA_SCHEMA = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['pass', 'fail'] },
  blockers: { type: 'array', items: { type: 'string' } },
  minors: { type: 'array', items: { type: 'string' } },
  notes: { type: 'string' } }, required: ['verdict', 'blockers', 'minors', 'notes'] }
let report = build || '(build returned nothing — audit site/index.html directly)'
let finalQA = []
for (let round = 1; round <= 3; round++) {
  const results = (await parallel([
    { key: 'persona', brief: `OWNER-PERSONA auditor: cold-load the real-payload site and, before reading any docs, click a bar as the owner would — is the frame instantly understood? Then audit against ${FLEET}/blueprints/specframe.md and ${FLEET}/feature_specframe.md: one click to comps+identity; ladder arrow-stepping; the "all K comps" link lands on the filtered Top Comps view with matching numbers; frame never traps you; ALSO build the synthetic-specstats copy as the build agent did and audit the stats/flask experience for usefulness and honest cohort labeling. Unmet contract = blocker.` },
    { key: 'regression', brief: `REGRESSION auditor: on the real payload verify nothing broke: tooltip contract (450ms, position-once, bar+label triggers, inert empty space) COEXISTS with the new bar-click frame; compspec filter + feeders still work; compare on/off roster identical at 17-19; Archon round-trip incl. the frame greying/behavior per blueprint; percentile relabel; sorting sweeps (comps mini table + all existing tables); section order; sidebar order; zero console errors; no layout shift on frame open/close (scrollHeight delta 0 apart from the frame's own reserved padding rule); ESC only closes the frame, never breaks popovers. Any regression = blocker.` },
  ].map(a => () => agent(`BUILD REPORT:\n${report}\n\n${ENV}\n\n${a.brief}\n\nReturn verdict/blockers/minors/notes; 'fail' only if blockers non-empty.`,
    { label: `qa:${a.key}:r${round}`, phase: 'QA', schema: QA_SCHEMA })))).filter(Boolean)
  finalQA = results
  const blockers = results.flatMap(r => r.blockers)
  log(`QA round ${round}: ${blockers.length} blockers`)
  if (!blockers.length || round === 3) break
  const fix = await agent(`${ENV}\n\nFix lead. Build report:\n${report}\n\nFix every blocker (no git):\n${blockers.map((b, i) => (i + 1) + '. ' + b).join('\n')}\n\nRe-verify as the build agent did. Return edits + verification.`,
    { label: 'fix:r' + round, phase: 'QA', effort: 'high' })
  report += '\n\nFIX ROUND ' + round + ':\n' + (fix || '(fixer returned nothing)')
}
return { build: (build || '').slice(0, 3000),
  qa: finalQA.map(q => ({ verdict: q.verdict, blockers: q.blockers, minors: q.minors.slice(0, 6) })),
  clean: finalQA.length > 0 && finalQA.every(q => q.verdict === 'pass') }
