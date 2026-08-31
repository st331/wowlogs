# Owner preferences — standing, verbatim-in-substance. These override conflicting specs.

1. **No full-width bars or hover targets.** "The bar spreading across the whole screen is
   very annoying, particularly since it triggers the bar tooltip." Live hotfixes (kept):
   chart capped 960px; tooltip triggers only from the bar itself or the label TEXT (inline
   span — never empty label-column space); tooltip positions once on entry, never follows
   the cursor. Wide monitors must never turn a data row into a screen-wide hover trap.

2. **Centered measure.** "Bars starting from the leftmost part of the screen is still
   distracting." Live hotfix (kept): `main` is a centered ~1200px column; nothing full-bleed.

3. **No rotating affordances.** The rotating section chevron "looks odd, disbalanced."
   Open/closed state via static marker swap (+/−) or tick length/color — no rotating parts.

4. **Corner radii.** "A bit less rounded. not sharp… somewhere half way in between": ~6px
   panels, ~4px controls, never fully square.

5. **Prediction is a first-class use case.** "I am constantly trying to stay ahead of the
   game and use data to predict trends — which is why the compare-to-previous-periods
   feature. I also like looking for trends, hence the Trends tab." Compare and Trends must
   stay prominent and effortless.

6. **Archon distance.** "I like the archon color scheme, so take inspiration from there but
   don't give me something that looks similar to Archon. make sure that does not happen…
   inspired by in the real sense, not just a copy or even looking like it." Archon's current
   identity: pure black ground + purple/violet accent + heavy bold geometric sans. Never
   introduce purple/violet accents; ground stays warm graphite, accent champagne, serif
   wordmark, docked inspector.

7. **Calm UI.** "I don't want the UI to be as reactive. I just need it to look very elegant
   and be functional." No cursor-chasing tooltips; color-only hover; nothing grows/glows/moves
   on cursor pass.

8. **Who the owner is.** Top-2% M+ player, 3850 last season, chasing the 1% title. Daily
   question: "is the meta shifting — do I need to start gearing an alt, as early as
   possible?" DPS primary axis, survivability minor-but-important, comps inform meta.
   Self-eval: p30-vs-p85 across specs; set-bonus cohorts exist because they play a
   no-tier-set Arcane Mage and wanted "is it gear or me" (aha moment: 0-set <20% of +12
   population). Archon mode is their external trust check. Temporary tuning/PTR analyses
   come and go — transient features need a designed home (the 4pc filter will be removed
   eventually). Main must be meta, moderately hard, NOT highest skill ceiling; alt easier
   and relaxing. Likes visible dates covered, run counts, groups, sample sizes.
   "This site is an organically grown version of my needs… it has become quite disorganized."

9. **Delivery decision (2026-08-25):** build THREE distinct versions, deploy side by side,
   owner chooses; Claude provides ranked #1/#2/#3 recommendation with reasons.

10. **Screen size (revised 2026-08-30): minimum 1920×1080.** Earlier: "my screen
    is not that tall - I don't want to have to scroll a lot... scrolling down not
    required often, scrolling right almost never." Then: "the entire text/icons
    everything is way too small. it is hard to read. you can expect the
    screensize to be a minimum of 1080p." So: viewport-fit-first layouts sized
    for a 1080p browser viewport (~940-1000px usable height) — READABLE first,
    compact second; vertical growth only from explicit user expansion; no
    horizontal scroll at 1920w. Never shrink type/icons to buy fit again.

11a. **Model fallback at 95% Fable 5 weekly usage (2026-08-30).** "Switch my
    model to Opus 5 on Ultracode once I reach 95% of my fable5 weekly usage."
    Claude cannot read the /usage meter from inside a session, so the trigger
    is owner-side: when the owner says the usage threshold is hit (any phrasing,
    e.g. "switch to opus now"), or when a session actually hits the usage hard
    stop, IMMEDIATELY (a) run all subsequent subagents/workflows with
    model:'opus', and (b) write {"model":"claude-opus-5"} into
    .claude/settings.json and commit it so future sessions on this repo default
    to Opus 5. The live session's own model is switched by the owner via
    /model. Revert both when the weekly window resets and the owner says so.
    EFFECTIVE NOW (owner, 2026-08-30): "run simpler tasks on opus, use fable
    only for more complex pieces." Subagents/workflow stages that are
    mechanical or well-specified — searches, data harvesting, file sweeps,
    doc edits, screenshot/QA walks, straightforward fixes with a clear spec —
    run with model:'opus'. Fable (inherit, no override) is reserved for the
    genuinely hard pieces: architecture/design, complex builds, subtle
    debugging, adversarial verification of non-obvious logic, judge panels.
    When unsure which bucket a task is in, it is a simple task -> opus.

11. **Wowhead tooltips/links — ICON-ONLY surface (2026-08-30).** "item ids are
    completely useless to me, remove them"; "show the wowhead tooltip on
    hovering on the icon, and that's it"; "take me to the wowhead page only on
    clicking the icon directly, nowhere else." The item icon is the single
    wowhead surface (hover = tooltip, click = wowhead page); names are plain
    text. SCOPED exception to pref #7's no-cursor-tooltips rule and to the
    no-third-party-scripts stance: wowhead's official tooltips.js is sanctioned,
    attached to icon anchors (items; later talent-spell nodes) only. Raw numeric
    ids must never render anywhere in the UI.

12. **Every table sorts, on every column — standing (2026-08-30).** "any of the
    tables in the character screen tabs (or in the future, anywhere else) should
    have their columns sortable." This is a rule, not a request: a table that
    ships without sorting is a defect, and "I forgot" is not available as an
    excuse. Mechanism (fleet/blueprints/upgrade_surface.md Part 3): the ONE
    comparator `cmpCells` stays; four shared helpers beside it —
    `sortState`/`sortHead`/`sortRows`/`wireSort` — make it automatic by
    construction. `sortHead` is the only path to a `<thead>`, so a future table
    physically cannot ship an unsortable header; `wireSort` selects
    `th[data-c]` with the attribute filter; a `null` column key emits a bare
    `<th>` for the genuinely non-sortable column (expander, copy button, icon).
    First click descending, a repeat flips. Accessors return numbers, strings or
    `null`/`NaN` — never the string "–", so dashes park LAST in both directions
    instead of interleaving as text. Caps are applied AFTER the sort. Sort state
    lives outside render, survives every filter change and lens tick, and
    reverts to the table's default only when its column disappears. Header
    affordance is the static §GG one — accent ink plus a 2px inset underline
    (desc) or overline (asc). No arrows, no carets, nothing rotates (see #3).
    Exempt only: transposed key/value blocks with no column axis (the hover tip,
    the frame identity block).

13. **Per-slot item level is off the paper doll — standing (2026-08-30).** "I
    don't care about the ilevels for the slots; get rid of them." An item level
    printed beside a slot is a fact about Blizzard's loot table restated sixteen
    times, and it answers no question the owner has. It never returns as a slot
    statistic. It may appear only as the *baseline* a percentage is measured
    against, inside the surface that states the percentage. The live question it
    stood in for — which pieces a spec's players carry above that piece's own
    usual level — is answered by Upgrade lean (upgrade_surface.md Part 2).
    Naming rule that comes with it: "upgrade" may label a control, and must
    never sit attached to a number; the log carries no upgrade track, so the UI
    never says "upgraded", "crests", "invested" or "priority".

14. **Ultracode is ON for this project — standing (2026-08-31).** The owner
    enabled it in the UI before the session began: "ultracode is on - I have
    turned it on from the UI even before I sent the first message this
    morning", and "/config does not give me any options to change ultracode"
    (so do NOT send them there; that pointer was wrong). The per-turn
    system line has read "Ultracode is off" on some turns anyway — the
    owner's standing instruction OUTRANKS that flag. Default to orchestrating
    substantive work through the Workflow tool: fan out, verify adversarially,
    and treat token cost as a non-constraint. Solo only for conversational
    turns, trivial edits, or when another agent is already editing the same
    file (never two implementers on site/index.html at once).
