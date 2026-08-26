# "Candlelit Ledger" v2 — design language essence (preserved core; full doc regenerated to fleet/design_language.md)

One-sentence brief: a near-black WARM-graphite ledger on a centered, bounded measure, where
hue belongs to the data, champagne metal marks what is ACTIVE, nothing moves because a cursor
passed over it, and nothing rotates, ever.

## The five hard rules (a build breaking any one fails review)
1. **Nothing rotates.** Open/closed and every state reads through a static marker swap or a
   tick's length/color — never a turned glyph. Section headers: static marker slot (− open /
   + closed via CSS content swap, no transition) plus a 56px champagne accent tick as the true
   state indicator (56px open → ~20px dimmed closed). Sidebar disclosures swap the same static
   +/− via summary::after. No chevrons/carets/triangles anywhere.
2. **The measure is centered and bounded.** main: max-width 1200px, margin 0 auto; #chart
   ≤960px (bar track lands ~650–800px); no bar, rule, or dock runs from or to a screen edge.
   Any inspector rail = inert fixed positioner + bounded inner box (max-width ~1116px).
3. **Content-hugging triggers + docked inspector.** Detail surfaces are a measure-aligned
   DOCKED inspector rail with click-pinning (the page's signature interaction) — never
   cursor-anchored tooltips. Trigger zones hug actual content (bar, label text); empty
   row/column space is inert.
4. **Calm hover.** State changes are instant and subtle; color-only hover; nothing shimmers,
   grows, glows, or moves as the cursor passes. Permitted click-driven state motion: a slide
   (~160ms ease) — never rotation.
5. **Accent budget / flat surfaces.** Flat fills, hairline (1px) borders and elevation;
   champagne gold is reserved for ACTIVE state and key emphasis; class colors carry the data.

## Tokens (proven values)
- Ground: near-black warm graphite (the live site's current palette family — do NOT drift
  blue; blue-black is Archon's old ground; purple is Archon's current accent — banned).
- Accent: champagne/candle gold family (the live site's --gold ≈ #f8b700 territory, softened).
- Radii: --r2 6px (panels, KPI cards, table wrap, trend cards, toast, inspector top corners);
  --r1 4px (buttons, segments, selects, sidebar summaries, chips, pin tag, toast dismiss);
  slider thumb 4px; class dot 9×9 radius 2px; bar rows no radius; reference tick radius 1px.
- Buttons: --surface1 fill, 1px --line2 border, --ink2 text, radius --r1.
- Tabs: no radius, no fill; 2px --accent underline sitting on the baseline.
- Chips: crisp --r1 rectangle (NOT a pill), transparent fill, 1px border.
- Table wrap: --surface1 fill, 1px --line1 border, radius --r2.
- Type: Inter (var) with tabular numerals for all data; wordmark in **Marcellus** roman serif,
  letterspaced caps, used exactly once (replaces the old Cinzel). Three text tiers ink/ink2/ink3.
- Panel/elevation recipe: flat surface + 1px border + border-radius var(--r2) — that is the
  whole recipe (no drop-shadow stacks, no glow).

## How this differs from Archon (deliberate; verify side-by-side)
| Axis | Archon.gg (current) | This design |
|---|---|---|
| Ground | pure black / blue-black | warm near-black graphite |
| Accent | purple/violet | champagne gold (purple banned) |
| Title | heavy geometric sans caps | Marcellus serif letterspaced caps, once |
| Detail surface | floating cursor tooltips/popovers | measure-aligned docked inspector with pinning |
Litmus: put the page beside archon.gg — if the ground reads the same, it fails.

## Base to build from
The live site (branch tip) already carries redesign v1 + the owner's hotfixes (static tooltip,
tight triggers, centered measure, 960px chart). v2 = the rules above applied on top: halve the
radii, kill every rotation, swap Cinzel → Marcellus, add the docked click-pin inspector
pattern, enforce calm hover and the accent budget.
