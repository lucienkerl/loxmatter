# Search field of the device view: its own shape instead of a system box

Design, September 6, 2026. Concerns the room bar from
[the devices-tab design](2026-09-05-devices-tab-rooms-and-tile-grid-design.md),
section 6.3 — the search field was introduced there, but never styled.

## 1. The problem

The search field falls through the CSS grid. The form rule in `style.css`
lists `input[type="text"]`, `[type="number"]`, `[type="password"]` and
`select` — `search` is not among them. `.device-search` only sets
`min-width` and `font-size`. Everything else is drawn by the browser
itself: square corners, its own border, its own font.

In dark mode, therefore, **none** of the project's colors apply. The box
takes its background and text color from the system style, and
`color-scheme: light dark` only rescues the rough contrast, not the sense
of belonging — the field visibly does not belong to the UI it sits in.

On the dashboard screenshot you can see it immediately: a boxy system
element sits at top right in a row that otherwise consists of rounded
chips.

On top of that, there is no sign whatsoever *that* it is a search. No
magnifying glass. No dedicated clear button — only the browser-dependent
one that WebKit shows and Firefox does not. No focus ring from the
palette. And no feedback on whether the search is even taking effect: with
many devices, you have to scroll to see that nothing was left.

## 2. The constraint

The header comment of `style.css` records that this UI is **deliberately
unadorned**: "Clarity over impact - colour is only used where it carries
meaning, not as decoration."

This design changes nothing about that. "Nicer" here means *clean and
self-explanatory*, not *decorated*. No color is added that does not
already denote a state: the accent color marks focus, everything else is
border, surface, and muted text from the existing variables.

## 3. What stays unchanged

- **The search logic.** `matchesSearch()`, `visibleDevices()` and
  `hitsOutsideRoom()` get no changes. The counter only reads what is
  already being computed anyway.
- **The room chips.** Their numbers stay room sizes and do not follow the
  search. A chip answers "how big is this room", the counter answers "how
  many matches are shown below" — two questions, two numbers, in two
  places.
- **The note "N more matches in other rooms"** below the bar, along with
  its link to all rooms.
- **`deviceSearch`** as the one field everything hangs off.

## 4. The structure

A flex container with four children in a row:

```
┌──────────────────────────────────────────────┐
│ 🔍  Search by name, category, room   3 found ✕│
└──────────────────────────────────────────────┘
```

**The border sits on the container, not on the field.** That is the
load-bearing decision of this design. The `<input>` inside it is
borderless and background-less; the magnifying glass, counter, and cross
are its siblings in the same flex flow. Three things follow from that
without any further effort:

- None of the three additions needs absolute positioning, and therefore no
  padding number that has to match the icon size. Any such number would be
  a coupling that breaks on the next font-size change.
- The focus ring hangs off the container via `:focus-within` and therefore
  encloses the whole group. If it sat on `input:focus`, it would enclose
  only their middle — visibly wrong as soon as the magnifying glass and
  cross are present.
- The group grows with the font size, because all measurements are in
  `em`/`rem` and `.icon` already measures `1.1em`.

**A `<div>`, not a `<label>`.** A `<button>` inside a label also triggers
that label's forwarding to the labeled control; clearing should be one
click, not two events. The price is a narrow strip of padding that does
not focus into the field — four pixels, traded for an event duplication.

The magnifying glass gets `pointer-events: none`, so it does not punch a
click hole into the left edge.

## 5. The icons

The magnifying glass arrives as a new `#i-search` in the existing inline
sprite in `index.html` — the same stroke technique as `#i-rename` and the
category icons: paths only, `fill: none`, `currentColor`. Still inline and
without an icon library, for the same reason as the checked-in
`vendor/alpine.min.js`: the UI runs offline.

The clear cross needs **no** new symbol. `#i-close` already exists, it
shows exactly this shape, and placing a second cross next to it would mean
remembering two places on the next stroke-weight change.

## 6. Counter and cross

Both hang off `deviceSearch.trim()` and carry `x-cloak`: with an empty
field they are gone, and the row stays quiet on the first paint, before
Alpine has initialized.

The counter shows `visibleDevices().length` — i.e. what is actually shown
below the bar, including an active room filter. It therefore answers the
question you have while typing ("is this taking effect?"), not a more
general one that the note from section 3 already covers.

It gets `font-variant-numeric: tabular-nums`. Without that, the right edge
wobbles going from 9 to 10, and the cross next to it jitters along —
the same jitter that already cost the age display on the signal row once
before.

**WebKit's own clear cross has to go.** An `input[type="search"]` gets
`::-webkit-search-cancel-button` shown there; ours would sit next to it a
second time. The rule switches it off, using both `-webkit-appearance`
*and* `appearance`, because the pseudo-element itself is vendor-specific.

## 7. Width and position

One rule, three behaviors, without special-case classes:

```css
.search-field { flex: 1 1 12rem; max-width: 22rem; }
```

The spacer that is currently fixed in the markup (`<span style="flex: 1 1
auto">`) moves for this into its own `<template x-if="hasAnyRoom()">`.

- **With room chips:** both grow, the field is capped at 22rem, the spacer
  swallows the rest. The field sits on the right as before, but
  considerably more generously than with today's 12rem.
- **Without room chips:** no spacer, so the field moves to the left edge —
  onto a sightline with the tile grid below it.
- **Narrow window:** the bar wraps as before (`flex-wrap: wrap`), the field
  sits alone on its row and practically fills it at common phone widths
  (375 px ≈ 23.4rem against the 22rem cap).

On the second row, explicitly: the field does **not** stretch across the
full row width there. A 1600 px wide search field over four tiles would be
a worse sight than the lonesome box it is meant to eliminate. Left
alignment solves the problem — stretching would only be a second one.

## 8. Language

Two new keys in `strings.yaml`:

```yaml
web.devices.search_count:
  en: "{count} found"
  de: "{count} Treffer"
web.devices.search_clear:
  en: "Clear search"
  de: "Suche leeren"
```

Without a plural special case — "1 found" and "1 Treffer" both read
correctly, and the table has no plural form anywhere. `t()` in `app.js`
resolves `{count}` via its `values` argument; `GET /api/i18n` delivers the
template unresolved, as for any placeholder key.

`search_clear` labels both `title` **and** `aria-label` of the cross: it
carries no text of its own, so it needs one.

The input field additionally gets an `aria-label` from the existing
`search_placeholder`. Until now it only carried the placeholder — which
disappears exactly when someone has typed something.

## 9. Verification

Four tests in `tests/api/test_web.py`, following the pattern already
established there: what is checked is the **delivered** markup and CSS,
not the picture in the browser — this suite runs no engine that applies
CSS or executes Alpine.

1. **One border, not two.** The container carries `border` and
   `border-radius`, the `input` inside it carries `border: none`. If both
   had one, a border would sit inside a border.
2. **The focus ring hangs off `:focus-within`.** Proves that it encloses
   the group and not just the field at its center.
3. **WebKit's cross is switched off, ours is there.** The
   `::-webkit-search-cancel-button` rule is present in the CSS, and the
   markup references `#i-close`.
4. **Counter and cross are bound to the input and cloaked.** Both hang off
   `deviceSearch` and carry `x-cloak`.

The new `#i-search` is automatically covered by
`test_the_inline_icon_symbols_are_well_formed_xml`.

After implementation, `docs/screenshots/dashboard.png` is recaptured —
the image currently shows the system box and would otherwise go stale
immediately. The capture path via `scripts/` is established, including a
pinned demo timestamp.

## 10. Open points

None. The focus keyboard shortcuts, match highlighting in the tiles, and a
styled empty state were deliberately cut from scope: they touch `app.js`
and the tile rendering, i.e. more than the search bar. Should they come
later, this design is their foundation, not an obstacle.
