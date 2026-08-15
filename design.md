# Design — Trainyze

A locked design system for this app. Every page redesign reads this file before
emitting code. Do not regenerate per page — extend or amend this file when the
system needs to grow.

Produced by `hallmark redesign` on 2026-08-15. Genre and theme were chosen for an
**instrument panel**, not a marketing page: Trainyze is read at 06:00 on a phone,
half-awake, to answer one question — *what do I do today?*

## Genre

`modern-minimal` — the restrained product-UI register. Confident sans display,
calm ground, hairline structure, exactly one signal accent, no ornament.

## Macrostructure family

- **App pages** (home, health, sleep, analysis, calendar, strength, journal,
  climate, settings): **Bento Grid** — modular blocks of varying span. Rhythm comes
  from size variation, not card uniformity. No enrichment; function carries the page.
- **Marketing page** (`landing.html`): **Stat-Led** — untouched by this pass, but
  when it is redesigned it must adopt the tokens below.

## Theme

**Cobalt**, adapted to app-scope and extended with a dark counterpart. The dark
ground is Cobalt's own graphite band value, so dark mode is *inside* the system
rather than invented alongside it.

### Light (default)

- `--color-paper`    oklch(98.5% 0.004 250)
- `--color-paper-2`  oklch(96.6% 0.005 252)
- `--color-surface`  oklch(99.6% 0.002 250)
- `--color-ink`      oklch(24% 0.02 258)
- `--color-ink-2`    oklch(34% 0.018 257)
- `--color-ink-3`    oklch(52% 0.015 256)
- `--color-rule`     oklch(90% 0.008 254)
- `--color-accent`   oklch(58% 0.20 256)
- `--color-focus`    oklch(58% 0.20 256)

### Dark

- `--color-paper`    oklch(20% 0.016 260)
- `--color-paper-2`  oklch(17% 0.014 260)
- `--color-surface`  oklch(23.5% 0.016 260)
- `--color-ink`      oklch(95% 0.006 250)
- `--color-ink-2`    oklch(84% 0.010 252)
- `--color-ink-3`    oklch(64% 0.014 254)
- `--color-rule`     oklch(30% 0.016 258)
- `--color-accent`   oklch(70% 0.16 256)

### Semantic colour is NOT the accent

`--color-good` / `--color-warn` / `--color-crit` carry state (a strain warning, a
low CNS, a missed session). They are separate from `--color-accent`, which only
ever marks *interaction*: the active nav item, focus rings, the one primary button,
a hovered link. A dashboard that paints its accent on data has no accent left for
its controls.

## Typography

- **Display:** Space Grotesk 500/600/700, tracking `-0.02em`. Slightly mechanical —
  reads as instrument, not editorial.
- **Body:** Inter 400/500/600.
- **Mono:** JetBrains Mono 400/500/600 — **every number in the app**, plus all
  UPPERCASE eyebrows, units, and status chips. `font-variant-numeric: tabular-nums`
  so digits never shift as values update.
- All headings roman. No italic display anywhere.

The previous build set numerals in Inter. In a dashboard where values refresh in
place, tabular mono is not a style preference — it stops the layout twitching.

## Spacing

4-point named scale, `--space-3xs` … `--space-3xl`. Pages use named tokens only,
never raw px.

## Radii

Ruler-drawn, not soft: **6px** controls (buttons, inputs, chips), **10px** cards.
No pills, no 0px brutalism.

## Depth

**Hairlines, not shadows.** Every surface is defined by a 1px `--color-rule`
border. The single permitted lift is `0 1px 2px` on an overlay/dialog. No
glassmorphism, no blur panels, no glow.

## Motion

- Easings: `--ease-out` `cubic-bezier(0.16, 1, 0.3, 1)`, `--ease-in-out`
  `cubic-bezier(0.65, 0, 0.35, 1)`. Never the browser default `ease`, never bounce.
- Durations: `--dur-fast` 120ms, `--dur-base` 200ms, `--dur-slow` 340ms.
- Animate `transform` and `opacity` only.
- Ring/bar fills animate their own geometry once on load, then hold.
- `prefers-reduced-motion: reduce` → all spatial motion collapses to a ≤150ms
  opacity crossfade; focus rings never animate at all.

## Microinteractions stance

- **Silent success.** A saved journal entry updates its own status chip; it does
  not toast.
- Hover tooltips delay 800ms; focus tooltips 0ms.
- Destructive actions confirm inline, in place — no modal for a delete.
- Focus is always visible: 2px `--color-focus` ring at 2px offset, instant.

## CTA voice

- **Primary:** solid `--color-accent`, 6px radius, `--color-accent-ink` label.
  One per view. Names the destination or the verb — "Spara dagbok", never "Skicka".
- **Secondary:** 1px `--color-rule` border on transparent, same radius.
- **Quiet:** typographic, `--color-ink-3`, underline on hover.

## Navigation

**N3 Side-rail** on ≥1024px — a 232px left rail, wordmark top, nine destinations,
active item marked by a 2px accent bar plus accent label. Below 1024px the rail
becomes a **bottom tab bar** with the five primary destinations and an overflow
sheet for the rest. A training app is used on a phone; a horizontally-scrolling
top nav is not a phone pattern.

## Per-page allowances

- App pages: **no enrichment.** No illustration, no hero art, no decorative SVG.
- Data visualisation (rings, bars, sparklines, charts) is content, not enrichment,
  and is encouraged — but must use the semantic palette, never the accent.

## What pages MUST share

- The wordmark and the rail.
- The accent colour and its restriction to interaction only (< 5% of any viewport).
- Space Grotesk / Inter / JetBrains Mono in their assigned roles.
- Card voice: 1px rule border, 10px radius, `--space-md` padding, mono eyebrow
  in UPPERCASE above a display title.
- Every number in mono, tabular.

## What pages MAY differ on

- Bento span rhythm — the home page runs a 2×2 lead tile; settings runs a flat
  two-column list. Both are Bento.
- Whether a page opens with a hero score tile (home, sleep, analysis do; calendar,
  journal, settings don't).

## Legacy compatibility layer

`styles.css` maps the **old** variable names (`--bg`, `--bg2`, `--text`, `--muted`,
`--accent`, `--green`, `--red`, `--amber`, `--font-sans`, `--font-num`,
`--font-mono`, …) onto the new tokens. This is deliberate and must be kept:
`app.js` injects markup with inline styles referencing those names (goal modal,
users modal, activity detail). The alias layer is what lets the visual rebuild
land without touching 6 300 lines of JavaScript. **Do not delete the aliases until
`app.js` stops emitting inline styles.**

## Exports

### tokens.css

Emitted at `public/tokens.css` and imported by `public/styles.css`. It carries
every `--color-*`, `--font-*`, `--space-*`, `--text-*`, `--radius-*`, `--ease-*`
and `--dur-*` token in the system, in both themes.
