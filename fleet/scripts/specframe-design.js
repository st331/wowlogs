export const meta = {
  name: 'specframe-design',
  description: 'Design the click-activated spec frame (comps + stats + flask filter)',
  phases: [
    { title: 'Investigate', detail: 'Archon stats reference, current surfaces, integration points' },
    { title: 'Design', detail: 'three competing frame designs' },
    { title: 'Judge', detail: 'persona + coherence verdicts -> winning blueprint' },
  ],
}
const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const ENV = `Contract: ${FLEET}/feature_specframe.md (read FIRST, including the flask addition). Persona/prefs: ${FLEET}/user_prefs.md, ${FLEET}/feedback_round2.md. Skin: ${FLEET}/design_language.md (§GG governs). Current site source: /home/user/wowlogs/site/index.html — NOTE: it already contains the freshly built "top comps for this spec" feature (possibly not yet deployed); read it to see what exists. Comp-model facts: ${SP}/comps_facts.md. Live mirror (older build, for feel only): http://127.0.0.1:8901/index.html (playwright chromium '/opt/pw-browsers/chromium', run node from ${SP}).`

phase('Investigate')
const facts = await agent(`${ENV}

You are the investigator for the SPEC FRAME. Produce verified facts:
1. ARCHON REFERENCE: fetch https://www.archon.gg/wow/builds/arcane/mage/mythic-plus/overview/high-keys/all-dungeons/this-week with curl (the proxy allows it; use a browser User-Agent) and extract the __NEXT_DATA__ JSON; document exactly what their #stats section shows (stat names, aggregation kind — averages/histograms/ranges, cohort wording) and roughly how it is laid out. This is inspiration only — our rendering must NOT look like Archon (their identity: black + purple + heavy sans).
2. CURRENT SITE: read site/index.html and map — the comps feature just built into it (surface, gestures, functions); the current bar-click gesture chain; where a frame could dock per the design language's inspector-rail recipe (the doc specs an "inert fixed positioner + bounded box" pattern — quote its exact spec); ESC/close/focus conventions already present (popovers); what per-spec data the client can already assemble for the frame (comps list, percentile spreads, deaths, rating, set-bonus standing).
3. PAYLOAD FUTURE: the stats pipeline (in progress, see the contract) will add a "specstats"-style block keyed per spec with quantiles + cohort descriptor + (later) per-flask variants; the frame must feature-detect it. State the integration assumption crisply so designers rely on one shape — read ${FLEET}/wip/ if the pipeline agent has already mirrored code there and derive the REAL emitted shape from it; otherwise define the assumed interface explicitly as the thing I must reconcile later.
Write to ${SP}/specframe_facts.md and return in full.`,
  { label: 'investigate', phase: 'Investigate', effort: 'high' })

phase('Design')
const ANGLES = [
  { key: 'docked-rail', brief: `DOCKED RAIL: the frame is a bottom-docked, measure-aligned panel per the design language's inspector recipe — click a bar opens it for that spec, click another bar switches it, ESC/x closes. Blocks laid horizontally (identity+key numbers | top comps | stats with flask chips).` },
  { key: 'side-panel', brief: `SIDE PANEL: the frame slides in as a right-side bounded column (within the centered measure, not full-bleed) that coexists with the chart — the user can scan bars and the frame together; the active spec's bar row gets a subtle persistent highlight. Blocks stacked vertically.` },
  { key: 'in-flow-card', brief: `IN-FLOW CARD: clicking a bar expands a full-width (measure-bounded) card directly beneath the Overview section — no overlay, no dock; the page simply grows. The card carries the blocks side by side and collapses via its header or clicking the bar again. Reconcile with the existing bar-click jump.` },
]
const designs = await parallel(ANGLES.map(a => () => agent(`${ENV}

FACTS:\n${facts}

You are one of three competing designers for the SPEC FRAME. ${a.brief}

Deliver an implementable design (<=140 lines): exact placement/markup sketch; open/switch/close interactions incl. keyboard; the three content blocks now (identity summary, top comps — absorb or link the just-built comps feature coherently, character stats with the flask chips INSIDE the stats block only); extensibility slots for future blocks (talents/trinkets); feature-detect behavior when specstats/flask data is absent (no placeholders); cohort/provenance lines; Gilded Glass styling; edge cases (rare spec, Archon mode, compare modes, 1366px, spec filtered out); gesture reconciliation with today's bar-click; what you deliberately did NOT add. Persona reaches comps+stats for a spec in ONE click from the overview.`,
  { label: 'design:' + a.key, phase: 'Design' })))

phase('Judge')
const JUDGE_SCHEMA = { type: 'object', properties: {
  scores: { type: 'array', items: { type: 'object', properties: {
    design: { type: 'integer' }, score: { type: 'integer' }, notes: { type: 'string' } },
    required: ['design', 'score', 'notes'] } },
  winner: { type: 'integer' }, grafts: { type: 'string' }, vetoes: { type: 'string' },
}, required: ['scores', 'winner', 'grafts', 'vetoes'] }
const verdicts = (await parallel([
  `Judge AS THE PERSONA (top-2% player, daily meta checks, main/alt evaluation): replay real flows — morning scan then "who runs with Ele Sham?"; alt-vetting Arcane's stats and crit-vs-vers flask splits; quick in-and-out. Weigh: one-click access, not losing your place on the page, calm (no layout jank on open), and whether you would actually keep using it in month three.`,
  `Judge as the COHERENCE auditor: design-language fit (the doc's inspector recipe and hard rules), gesture conflicts with the existing comps feature and bar-click, feature-detect correctness (no dormant UI), flask scoping (must be impossible to mistake for a global filter), single-file implementation risk, extensibility that will not rot.`,
].map((lens, i) => () => agent(`${ENV}\n\nFACTS:\n${facts}\n\n${lens}\n\nTHE THREE DESIGNS:\n${designs.map((d, j) => `### DESIGN ${j + 1} (${ANGLES[j].key})\n${d}`).join('\n\n')}\n\nScore each 0-100, pick a winner, name must-graft ideas and vetoes.`,
  { label: 'judge:' + (i ? 'coherence' : 'persona'), phase: 'Judge', schema: JUDGE_SCHEMA, effort: 'high' })))).filter(Boolean)
const tally = [0, 0, 0]
for (const v of verdicts) for (const s of v.scores) if (s.design >= 1 && s.design <= 3) tally[s.design - 1] += s.score
const win = tally.indexOf(Math.max(...tally))
log('Frame design scores: ' + tally.join(' / ') + ' -> ' + ANGLES[win].key)
const synth = await agent(`${ENV}\n\nWrite the FINAL spec-frame blueprint to ${FLEET}/blueprints/specframe.md: the winning design below with every judge graft folded in (unless it breaks coherence — state why if rejected) and every veto honoured; include the integration plan with the already-built comps feature and the exact feature-detect interface for specstats/flask. Self-contained for a build agent.\n\nWINNER (${ANGLES[win].key}):\n${designs[win]}\n\nVERDICTS:\n${verdicts.map((v, i) => `Judge ${i + 1}: ${JSON.stringify(v)}`).join('\n')}\n\nReturn a 12-line summary.`,
  { label: 'synthesize', phase: 'Judge', effort: 'high' })
return { winner: ANGLES[win].key, tally, blueprint: FLEET + '/blueprints/specframe.md', summary: synth }
