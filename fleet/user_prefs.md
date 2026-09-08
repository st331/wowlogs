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
    affordance is the static §GG one — accent ink plus a 2px accent rule on the
    header's own baseline, ALWAYS underneath the label. Direction is texture:
    SOLID = descending, SEGMENTED = ascending. No arrows, no carets, nothing
    rotates (see #3).
    REVISED 2026-08-31, from live use. The first build used an OVERLINE for
    ascending. The owner rejected it on sight: "the line that is supposed to be
    underneath the text to show the sorted column, is instead above", and
    earlier, "I still see that weird two lines." A rule above a header reads as
    that header's underline gone astray, and on a table whose first row sits
    under a section title's own accent rule it produced two short champagne bars
    stacked ~20px apart that looked like a rendering fault. Enchants was the
    only table defaulting to ascending, which is why it was the one that looked
    broken. Do not reintroduce an overline anywhere: a horizontal accent rule
    above a text label is not available as an affordance on this site.
    Mechanism note, load-bearing: `box-shadow` REPLACES rather than adds, so the
    direction mark cannot live in that property without erasing the 1px baseline
    rule under exactly the sorted column. Descending overrides it deliberately
    (same edge, 2px accent instead of 1px line); ascending paints a
    `repeating-linear-gradient` background at `bottom left`, `100% 2px`, and
    leaves the box-shadow alone.
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

15. **Newest runs first — standing (2026-09-02).** "prioritize getting new runs
    first." The summary stage fetches strictly newest-first (`order_pending`),
    and nothing else -- not region tag, not dungeon balance -- decides the
    order. A run that stops at the budget ceiling must have spent it on the
    most recent play, because "this reset" is the period the site opens on and
    the daily question is whether the meta is moving NOW. Any future
    prioritisation (a regear pass, a backfill) is added BEHIND this rule, never
    in front of it.

16. **No LLM export, no crawler welcome mat — standing (2026-09-02).** "remove the llm
    export feature." build_llms(), the /llms/ tree, llms.txt, robots.txt/sitemap.xml
    and the footer links are gone and stay gone. The site publishes the payload and
    sidecars for its own page and nothing else.

17. **Key-level drop-off is a first-class question (2026-09-03).** "I want to be able
    to see how much the unique characters of a spec drops off as the key level
    increases ... compare which specs are dropping off faster as the keys go up."
    Lives in Trajectory as a second AXIS (Over: resets | Key level) plus a Retention
    normalisation, because the panel already compares per-spec series with a gate,
    top-N and a slope sort.
    REVISED 2026-09-04, same day, by the owner: "I want to be able to zoom into the
    retention by key level; not just starting from 10. starting from 10 makes it very
    difficult to see the actual differences between the specs in the higher keys."
    The key axis was built to IGNORE the key slider so the whole ladder showed at once.
    That was my call and it was wrong for how this is read: indexed from +10 the high
    keys are squeezed into a corner where the specs cannot be told apart. The chart now
    spans the keys the slider selects, so narrowing it zooms and re-indexes every line
    from the new low key. Do not put the "whole ladder always" rule back.
    Two rules that DO stand: it floors at +10 whatever the slider says, because the
    leaderboards below that are swept ~5x shallower and a character count there cannot
    sit on the same curve; and retention indexes every line to the SAME bucket, since
    per-series baselines would compare a spec measured from +14 against one measured
    from +16 and present that as a comparison.

18. **The page opens on the Overview alone — standing (2026-09-04).** "start with all
    sections collapsed except the overview." Top Comps, Data Table, Pulse and Trajectory (and, until its removal on
    2026-09-07, Set Bonus Gain) all start closed; each is one click away and the choice then
    persists per browser. The storage key is VERSIONED (wowlogs.collapsed.v2): the
    default changed, so a browser holding the old preference had to be migrated once
    or it would keep opening everything forever. An explicit "everything open" choice
    still survives. Any new section starts closed unless it is added to
    SEC_OPEN_FIRST. Load-bearing consequence: a hidden section has ZERO width while it
    renders, so anything sized from clientWidth (the Trajectory chart, the comps rail)
    would draw to a fallback -- opening a section re-renders.

19. **A sort must measure the line that is drawn — standing (2026-09-04).** Trajectory's
    "Sort: Slope" took least-squares over the RAW metric while the chart drew a
    normalised line, so under Retention it ranked by absolute change; absolute change in
    a headcount is spec size (measured: correlation -1.00). One helper, drawn(key,
    bucket), is now the single definition of the plotted value and both the sort and the
    series read it. Any future normalisation goes in there, never as a pass over the
    series afterwards.


20. **A degradation that silences a live surface must reach build_health.txt —
    standing (2026-09-06).** "the character pinned screen is not working correctly.
    as i adjust key levels, the stats don't seem to change at all." The Spec Frame's
    Character stats block had two modes: LIVE off stats.json.gz, and a fixed
    build-time cohort when that file is absent. On 2026-09-02 the packer breached
    its 4 MB gz cap, returned None, and the caller unlinked the file. The block fell
    back and stayed there for four days. Every run was green, every test passed, and
    the only record was a job log, because stats_sidecar() reported through print()
    while its sibling builds_sidecar() reported through health().
    Three rules stand from this:
    (a) Any ladder that can silently drop a feature reports EVERY rung on the
        published health channel, and shouts (::warning:: degraded, ::error:: omitted).
        A cap with no telemetry is a trapdoor.
    (b) Shipping something beats shipping nothing. The ladder now has five rungs and
        degrades losslessly first (drop the tertiary stats, then shorten the window)
        before it quantises, because a quantised rung visibly lumps every printed
        number. Omission is the last resort, not the second.
    (c) A cap is a measurement, not a number someone liked once. 4 MB was set against
        a 3.2 MB document and never revisited while the payload grew 71%; meanwhile
        the same build happily shipped a 6.12 MB builds sidecar. Sizes in the
        blueprints are stale by default -- read build_health.txt.
    Also standing: when a fallback surface cannot follow the filters, it says so
    ABOVE its numbers, and it withholds them entirely when the live view is empty.
    A block printing a full distribution under an Overview that reads "no parses
    match the current filters" is a contradiction, not a disclaimer.

21. **70% cap waived for 2026-09-06 (IST) only — "ignore the 70% limit for today".**
    Context: the refresh chain had been dead 68 h (a queued run held the concurrency
    group); the first run back at 70% cleared ~14k of a ~29k-run backlog. The waiver
    is the per-operation relaxation pref #-standing allows, and it is scoped to the
    day: drain mode (fresh 1.0 / backfill 0.8, alternating, chained at the quota
    window) runs until the backlog is gone and then reverts to 70% on its own. Nothing
    about the standing cap changes tomorrow.
    Learned the same hour: a push to the branch while a run is between its checkout
    and its "Commit the export" step makes that step's `git push` non-fast-forward,
    which fails the run -- and a failed run does not chain. Do not push mid-run while
    a drain is in flight; batch pushes into the gap after a run's commit step.

22. **The 4-piece feature is gone — standing (2026-09-07).** "get rid of the 4 pc feature
    and all related code." Removed in one commit: the Set Bonus Gain section, the
    Set-bonus-cohorts Lab card (t0/t2/t4 + tier hint), tierPass/setBucket and the
    anyTier escape in rowPass, the tier fields in the Archon snapshot/presets/predicate,
    every scope-line/period-note/pulse-note/strip-chip tier fragment, the Spec Frame's
    4pc standing row, hasTier, and on the pipeline side tier_pieces, unpack_sets,
    sets_from_gear_journal, SEASON_SET_MIN_SHARE, the journal pass's per-parse set
    counts and the payload's `rows.tier` column. Kept deliberately: fetch_data.py's
    gear_sets/pack_sets and the CSV's `set_counts` column, because project_tuning.py's
    set-bonus scalars (the tuning-projection feature) read set counts, and they are raw
    capture rather than the feature. An old client tab still reads a payload without
    `rows.tier` cleanly (hasTier was feature-detected); the new client ignores a payload
    that still has it. Do not reintroduce a tier filter outside the LAB manifest.

23. **Tier pieces cannot be split by inherited stat pair — the data does not exist
    (2026-09-08).** Asked for: group each tier item by the secondary pair it inherited
    through the 12.1 Catalyst ("go ahead with just tier items. crafted gear shouldn't be
    separated by missives"). Established with three read-only diagnostics against the
    4 GB gear journal and one live API call: of 3,663,022 set-item wears over 265 items,
    zero carry an ItemBonus type-2 secondary-stat allocation; every bonus id a tier
    piece carries decodes to upgrade track (128xx), item-level delta (15xx), drop-context
    tag (13440 Mythic+, 13334 Heroic, 13333/13439), the Catalyst slot marker (type-38
    ids 13690-13694, one per slot), a tertiary (40-43) or a socket; and the raw
    Summary-table gear entry has exactly these keys -- id, slot, quality, icon, name,
    itemLevel, bonusIDs, gems, permanent/temporary/onUse enchant (+Name), setID -- no
    modifiers, no per-item stats. The combat log's COMBATANT_INFO item tuple is
    (id, ilvl, enchants, bonusIDs, gems); the inherited pair is an item modifier and is
    never logged, so Warcraft Logs cannot expose it. Crafted missives are the same
    mechanism, which is why they were never decodable either. The stat-bonus table
    refresh added on 2026-09-08 for this was removed the same day; diag_tier_pairs.py
    and diag_raw_gear.py stay as the evidence. Only a second data source (Blizzard's
    profile API, current gear only, own credentials and quota) could answer it; that
    is the owner's call, not a default.

24. **⚗ Lightspire Core "in the light" (2026-09-08) — a Lab feature, off by default.**
    Owner's metric, verbatim: "of the time that the trinket was active, what percentage
    of time did the player stay in the buff to get its effect ... the uptime of the buff
    on the player as a percentage of when the buff was actually available." NOT classic
    uptime. What the log records (two read-only diagnostics, 50 procs): the area-trigger
    spell 1263762 is never logged; 1263768 "Light's Blessing" is applied to the wearer
    the instant a beam spawns (cast and applybuff coincide to the ms, 50/50) and drops
    when they step out or at the 12 s expiry (bands 0.1–12.0 s, capped). So a band's
    START is a spawn, the beam is available 12 s from there, and per wearer-fight
    benefit = |bands ∩ ∪[start, start+12 s]| / |∪[start, start+12 s]|, overlapping beams
    merged; a spawn is an apply/refresh whose source is the wearer (own beams only in the
    denominator; a teammate's beam counts as buff time only inside those windows).
    Journal records carry "v": 2 -- v1 (table-era) records are redone. Reference: 41.4 % and
    35.9 % against classic uptimes of 8.2 % / 8.7 %. Zero beams in a run = no evidence
    (255 in the sidecar), counted separately, never 0 %.
    Pipeline: fetch_procs.py after Fetch, ONE buff-events sub-query per wearer-fight
    (the aura's apply/refresh/remove on the wearer, any source) plus the fight clock,
    newest first, four concurrent requests, ≤1,500 pts and ≤8 min a run under the STANDING
    70 % ceiling (raised from 400/4 min on 2026-09-08, "backfill the runs first": three runs
    an hour at Fetch ~2k + 1.5k stay under the 12.6k ceiling; the ~73k backlog clears in
    about a day instead of a week, and once it is gone a run costs ~60 pts). Scope: `--since-days 8`
    (owner, 2026-09-08: "just 1 reset of data is enough") -- one reset plus a margin, newest
    first; the ~40k older wearer-fights stay in the gear journal and are never fetched, so one
    reset (~30k wearer-fights, ~66k pts) is collected in ~12-15 h at the standing cap. The record,
    corrected: run 827 asked the Buffs table with abilityID+targetID -- the shape the
    diagnostic read 23/27 bands with -- and got 240 of 240 empty because the collector's
    table parser kept only auras with guid == 1263768, which an abilityID-filtered table
    does not key by; the sourceID+targetID variant blamed at the time never ran. Events
    sidestep the table. Measured on run 829 (first events run): 192 wearer-fights, 430
    points (2.2 each blended with masterData look-ups for pre-actor-id records), "126 of
    46,678 wearer rows covered, 0 with no beam". The point budget counts only positive
    spend deltas (rollover-safe); a run whose first 20+ results are ≥50 % no-beam
    journals nothing, warns and stops (systemic stop); regear runs skip the step
    (never the drain fraction); ~73k wearer-fights in the journal, ~30k/week new (7.6 %
    of gear-known parses). Journal data/processed/procs.jsonl keeps the bands; the
    sidecar site/procs.json.gz is row-aligned, tens of KB, no ladder; procs_spec.py is
    the one tracked-trinket table (adding a trinket = one entry, after its log signature
    is established the same way). Gear rows now carry the actor id.
    Client: the LAB entry "beam" (card "✨ Lightspire Core · in the light", badge LIGHT)
    exists only while the sidecar decoded; stamp:false — it adds numbers and changes none,
    so it never badges scope lines. The words carry the denominator so it can never be
    read as classic uptime: "in the light 38 % of beam time · n=143 of 312". Surfaces, all
    on the lens window (liveIdxMulti → lensWindow), so the same figure prints everywhere:
    PRIMARY the Spec Frame's Overview row (one click from every bar; skill compare shows
    "38 % · 51 %"; hidden with the frame while the Character screen is open), the
    Character screen's identity line (the pooled trinket tiles show only the #1/#2
    trinket and on the default window Lightspire is neither for any of its big wearer
    specs), the trinket tile + its fold-out row when the item is present, and, while the toggle is on, a
    per-spec table (median, p25-p75, time-weighted, n; every spec with a measured parse) in
    `#light-card`, a card under the KPIs in the main column like the Archon card, with a
    "measured so far" hint from the sidecar's cov block. Placement history, same day: the
    table first lived in the sidebar's ⚗ card ("on the left, completely scrolled off
    screen"), then briefly as a top-level section ("do not overcorrect!"); the toggle
    itself stays in the sidebar's ⚗ Lab card ("its a labs feature").
    Median of per-parse ratios is the headline; p25/p75/time-weighted (Σinside/Σavailable,
    the sidecar's b column is the INSIDE seconds) and the no-beam count ride the tooltip
    with the definition. Floor n=10 (CS_THIN echo): below it "thin", never a number.
    The toggle IS remembered across reloads (owner, 2026-09-08: "Keep the toggle
    remembered across reload") in localStorage "wowlogs.lab.beam" -- the one exception to
    queue.md's scrapped session persistence, asked for by name; a remembered ON renders
    every surface the moment the sidecar lands. Deliberately not built (design panel
    2026-09-08): a Data Table column, A·B compare columns on the card, a per-row status
    byte, an N-trinket decoder.
    DURABILITY, as audited 2026-09-08 (three lenses, each adversarially checked): the procs
    journal is cache-only AND its work list is enumerated from gear.jsonl, which is also
    cache-only with no working restore (gear.jsonl.gz was never committed; the Release-assets
    home is unbuilt). So "an eviction restarts the backfill" is WRONG for a whole-cache loss:
    gear goes too, the ~73.7k historical wearer-fights can never be enumerated again, and the
    feature restarts from post-loss fights only (rankings survive via the daily CSV; the
    Character screen self-disables). The cache holds ~24 entries of ~416 MB = ~7 h of history;
    the documented loss mode is an empty/failed restore followed by a successful save of an
    impoverished key (happened once, run 32625724812, 2026-08-23), and no guard checks
    cache-matched-key or journal presence. A bands-stripped seed carrying v>=2 and
    report_code/fight_id/character/server/key/actor/f/n/a/i/r would restore the SHIPPED
    feature fully (the sidecar joins to CSV-seeded player rows): measured 54 B/record gz,
    4.0 MB today, +1.6 MB/week, ~30 MB at season end; it must be its own git add in the daily
    list (the panel's "Monday slot beside gear.jsonl.gz" is gated on a file that never exists
    on the scheduled path). Not built; the owner has the numbers.

25. **Journal guard (owner, 2026-09-08: "go ahead with the feature then").** Every
    collection journal lives only in the Actions cache, and actions/cache/restore never
    fails a job -- an empty restore used to be fetched over, built over and SAVED as the
    newest key (run 32625724812, 2026-08-23). `scripts/journal_guard.sh` runs right after
    the restore: matched key AND non-empty gear.jsonl + players.jsonl ⇒ `ok`; nothing
    restored but `fresh_start=true` dispatched ⇒ `fresh`; otherwise `missing`. Fetch, the
    trinket collector, names, traits, Build, Publish, Pages deploy, Raider.IO, Save and the
    export commit are all gated on ok-or-fresh (fail-CLOSED: no verdict = shut); "Chain the
    next run" is not, so a tripped run costs one 20-minute cycle and the successor retries
    the restore. Persistent trips stop deploys, which the watchdog already reports as a
    stalled build; only then, and only if the journals are truly gone, dispatch with
    `fresh_start=true` (it seeds players from the committed CSV; gear and trinket history
    cannot come back). A drain in flight ends on a tripped run (empty BACKLOG reads as
    done) -- re-dispatch drain by hand if it mattered. The guard is unit-tested over all
    eight key/files/fresh combinations plus the matched-key-but-empty-file case.

26. **Trinket drain window (owner, 2026-09-08 08:38 UTC): "pause fresh runs, remove all
    limits and drain as much as you can over the next 3 hours. keep backfilling as the
    data keeps landing."** The per-operation relaxation the standing 70 % cap allows,
    scoped by a clock: `data/procs_drain.json` carries `until` = 2026-09-08 11:38:05 UTC
    (17:08 IST). While the clock is before it, every refresh run skips Fetch (fresh runs
    paused), and the collector runs with WCL_QUOTA_FRACTION 1.0, margin 20, up to 18,000
    pts / 20 min a run -- i.e. whatever the hour has left -- newest first inside the
    one-reset window. The file is inert once `until` passes: Fetch resumes and the
    collector returns to 1,500 pts / 8 min under 70 % on its own, with the backfill
    continuing at that pace ("keep backfilling"). Nothing to revert by hand; the watchdog's
    6-hour data-staleness rule is not reached by a 3-hour pause.

27. **No minimum-n floor on the Lightspire surfaces (owner, 2026-09-08): "I don't want data
    hidden from me, even if it is not statsig."** BEAM_MIN_N is gone: every spec with at
    least one measured wearer-parse is listed, and every tile/line/row prints its percent
    with its n beside it at any n. The reader judges significance from n; the page never
    withholds a number.
