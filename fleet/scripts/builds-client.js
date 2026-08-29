export const meta = {
  name: 'builds-client',
  description: 'Build the character-screen Builds mode per blueprint, audit, fix',
  phases: [
    { title: 'Build', detail: 'implement the takeover character screen in site/index.html' },
    { title: 'QA', detail: 'persona + regression audits, fix rounds' },
  ],
}
const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const ENV = `Blueprint (the contract; its §1 sidecar interface is change-controlled — code EXACTLY to it): ${FLEET}/blueprints/builds_tab.md. Feature contract: ${FLEET}/feature_builds.md. Prefs: ${FLEET}/user_prefs.md. Skin: ${FLEET}/design_language.md (§GG governs). Edit /home/user/wowlogs/site/index.html IN PLACE; NO git commands. Serve /home/user/wowlogs/site/ (payload present) on a port in 8990-8994; playwright chromium '/opt/pw-browsers/chromium', run node scripts from ${SP}. Archon evidence for inspiration-not-copying: ${SP}/archon/overview_next.json, ${SP}/archon/gear_next.json.`

phase('Build')
const build = await agent(`${ENV}

Implement the client side of the Builds deep-dive exactly per the blueprint's client task list: the Performance rail's "Character screen" affordance; the TOTAL main-column takeover in page flow (ladder strip, identity band absorbing the rail content, gear overview grid, per-slot distributions, crafted/embellishments, enchants, talent builds with copy-import-string); hero logic (merged: hero-share line + per-build hero tags from rows.hero; unmerged or hero-zoomed: suppressed with the "builds differ in class/spec trees only" note); the shared frameLensSlice() refactor so stats and builds use one lens/row-pass; deliberate-exit discipline (ONLY the back affordance/wordmark leaves the mode; Esc and click-away are inert in-screen; the rail keeps its light dismissal); lossless scroll/state restore on exit; lazy builds.json.gz fetch on first entry with the §7 loading treatment and the no-dormant failure path; name rendering with the vocab annotations and #id+wowhead-link fallback.

The real sidecar does not exist yet — generate a synthetic site-external builds.json.gz EXACTLY per §1 (dense and sparse variants; per-spec vocabs with cr/emb/ilvl/name annotations; realistic correlated loadouts derived from the real payload) in a scratch serve dir (NEVER write into site/), and verify with playwright: mode enter/exit discipline (Esc inert, click-away inert, back restores scroll+state); every section renders and re-slices under key-range, dungeon, region, timed changes AND lens drags; hero logic in all three states (merged / unmerged / hero-zoomed via the sidebar hero filter); thin-n honesty lines; spec-hop via ladder strip and arrow keys; 1366x768 grid; sidecar-missing path (both affordances gone for the session, zero errors); Performance mode and all existing behaviors unchanged (rail, stats live mode, comps, tooltip, Archon round-trip). Zero console errors (Google Fonts reset exempt). Screenshots: ${SP}/builds_1.png (character screen, gear overview), builds_2.png (talents + hero tags), builds_3.png (1366px). Return exact edits (line ranges) + full verification results.`,
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
    { key: 'persona', brief: `OWNER-PERSONA auditor: you are a top-2% player evaluating specs. Cold-load, click into a spec's character screen WITHOUT reading docs: is entry obvious, is the screen immersive (no boxed scrolling, controls stay), can you answer "what are Ret toons wearing and which builds do they run" fast? Then audit against ${FLEET}/feature_builds.md and the blueprint §C: total takeover (nothing of the dashboard's main column remains), the two bars fully live, re-slicing on filters+lens, hero logic in all three states, deliberate exit only, lossless return, copy-import-string works. Build the synthetic sidecar per §1 as the build agent did. Unmet contract line = blocker.` },
    { key: 'regression', brief: `REGRESSION auditor: on the real payload verify nothing broke: Performance rail identical (open/switch/close, click-away, pin, stats live mode with lens window, comps numbers); tooltip contract; compare on/off roster; Archon round-trip incl. entering/leaving character mode while the replica is on; percentile relabel; all table sorting; section order; zero console errors; no layout shift outside the mode transition itself; the mode transition never fires network fetches beyond the one lazy sidecar. Any regression = blocker.` },
  ].map(a => () => agent(`BUILD REPORT:\n${report}\n\n${ENV}\n\n${a.brief}\n\nReturn verdict/blockers/minors/notes; 'fail' only if blockers non-empty; each blocker names element, action, expected vs observed.`,
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
