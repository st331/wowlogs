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

10. **Short screen — minimize scrolling.** "My screen is not that tall - I don't
    want to have to scroll a lot... scrolling down not required often, scrolling
    right almost never." Viewport-fit-first layouts; vertical growth only from
    explicit user expansion; no horizontal scroll at desktop widths.

11. **Wowhead item links + hover tooltips (2026-08-30).** "item ids are completely
    useless to me, remove them. instead insert wowhead links to the items. when I
    hover over an item, the wowhead tooltip shows (pulled directly from wowhead)."
    This is a SCOPED exception to pref #7's no-cursor-tooltips rule and to the
    no-third-party-scripts stance: wowhead's official tooltips.js is sanctioned,
    for item (and later talent-spell) links only. Raw numeric ids must never
    render anywhere in the UI.
