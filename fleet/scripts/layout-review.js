export const meta = {
  name: 'layout-review',
  description: 'Mock up and judge the character-screen layout for ease of use and clutter',
  phases: [
    { title: 'Mockups', detail: 'two rendered variants with fake data' },
    { title: 'Judge', detail: 'ease-of-use + clutter lenses on the screenshots' },
    { title: 'Synthesize', detail: 'winning layout rewrites blueprint §C' },
  ],
}
const SP = '/tmp/claude-0/-home-user-wowlogs/d88fa09d-d702-5fc9-a68b-5463628b34a8/scratchpad'
const FLEET = '/home/user/wowlogs/fleet'
const ENV = `Blueprint: ${FLEET}/blueprints/builds_tab.md (§C is the layout under review; §1 data interface is FIXED). Contract: ${FLEET}/feature_builds.md. Prefs: ${FLEET}/user_prefs.md. Skin: ${FLEET}/design_language.md (§GG). Owner's review directive, verbatim-in-substance: "have the layout reviewed for ease of use and intuitiveness. I don't want much clutter. if needed, you can have sub-navs and other panes/pages/tabs within the character page to make the data not feel cluttered and still easy to navigate." Owner context: top-2% player; daily use; the two control bars (sidebar + top bar) are the only external controls; deliberate-exit; total takeover; no rotation, calm, Gilded Glass.`

phase('Mockups')
const mocks = await agent(`${ENV}

Build TWO throwaway static mockups of the character screen (self-contained HTML files with hardcoded realistic fake data for Retribution Paladin — real-sounding item names, shares, ilvls, three talent builds with hero tags, enchant lists, crafted/embellishment entries; steal the page's actual CSS tokens from /home/user/wowlogs/site/index.html so they look native):
- VARIANT A "single scroll": blueprint §C as written — ladder strip, identity band, gear overview grid, per-slot distributions, crafted/embellishments, enchants, talent builds, one page flow.
- VARIANT B "sub-navigated": same identity band + ladder, then a calm sub-nav (e.g. Gear | Talents — pick the grouping YOU judge cleanest, 2-3 panes max) where each pane holds a focused subset; per-slot detail folded behind the overview grid (e.g. click a slot to expand in place).
Write them to ${SP}/mock_a.html and ${SP}/mock_b.html, serve, screenshot BOTH at 1600x1000 AND 1366x768 (full page): ${SP}/mock_a_1600.png, mock_a_1366.png, mock_b_1600.png, mock_b_1366.png. Return a 6-line note on what each variant emphasizes.`,
  { label: 'mockups', phase: 'Mockups' })

phase('Judge')
const JUDGE_SCHEMA = { type: 'object', properties: {
  winner: { type: 'string', enum: ['A', 'B', 'hybrid'] },
  score_a: { type: 'integer' }, score_b: { type: 'integer' },
  hybrid: { type: 'string', description: 'if hybrid: exactly what to take from each' },
  fixes: { type: 'string', description: 'concrete clutter/usability fixes regardless of winner' },
  notes: { type: 'string' } }, required: ['winner', 'score_a', 'score_b', 'hybrid', 'fixes', 'notes'] }
const verdicts = (await parallel([
  `Judge AS THE OWNER-PERSONA using it daily: walk the tasks — "what are Ret toons wearing?", "which build do I copy?", "how does the top-25% lens change the gear?" — through BOTH screenshot sets (Read the four PNGs). Count effort, scan-ability, where your eye lands first. Punish scroll fatigue in A and orientation cost / hidden-data feel in B honestly.`,
  `Judge as a CLUTTER-AND-INTUITIVENESS critic: information density per viewport, visual hierarchy, whether a first-time viewer knows where they are and what is clickable, whether sub-navs feel like navigation or fragmentation, 1366px behavior, Gilded Glass calm (no busy-ness). The owner's words: "I don't want much clutter" but it must not feel restrictive.`,
].map((lens, i) => () => agent(`${ENV}\n\nMOCKUP NOTES:\n${mocks}\n\nScreenshots to Read: ${SP}/mock_a_1600.png ${SP}/mock_a_1366.png ${SP}/mock_b_1600.png ${SP}/mock_b_1366.png\n\n${lens}\n\nScore A and B 0-100, pick A, B, or hybrid (with exact hybrid recipe), and list concrete fixes.`,
  { label: 'judge:' + (i ? 'clutter' : 'persona'), phase: 'Judge', schema: JUDGE_SCHEMA, effort: 'high' })))).filter(Boolean)
log('Verdicts: ' + verdicts.map(v => `${v.winner} (A=${v.score_a} B=${v.score_b})`).join(' | '))

phase('Synthesize')
const synth = await agent(`${ENV}

Rewrite §C of ${FLEET}/blueprints/builds_tab.md IN PLACE to the judged layout — verdicts below. If judges split or say hybrid, synthesize the hybrid faithfully. Fold in every listed fix that fits the calm rules. Keep all locked interaction decisions intact (total takeover, two-bars-only controls, deliberate exit, live re-slicing, hero logic, lossless return) and keep §1 and the pipeline sections untouched. The client task list must be updated to match the new §C exactly.

VERDICTS:\n${verdicts.map((v, i) => `Judge ${i + 1}: ${JSON.stringify(v)}`).join('\n')}

Return a 10-line summary of the final layout.`,
  { label: 'synthesize', phase: 'Synthesize', effort: 'high' })
return { verdicts: verdicts.map(v => ({ winner: v.winner, a: v.score_a, b: v.score_b })), layout: synth }
