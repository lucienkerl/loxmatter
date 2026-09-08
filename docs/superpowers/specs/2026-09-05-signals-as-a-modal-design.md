# Signals: from their own tab into the device tile's modal

Design, September 5, 2026. Dissolves the "Signals" view and brings the
editing of individual signals to where the device stands — reachable via
the kebab menu from
[the kebab design](2026-09-05-tile-kebab-menu-design.md).

## 1. The problem

The signal view is a second directory of the same devices. It renders a
separate card with a heading for every device, even though right next to
it, in the "Devices" tab, a tile for the same device already stands — with
the same name, the same live values, the same signals in the preview.
Anyone who wants to change a signal's title therefore leaves the view in
which the device is visible, searches for it again in a second, differently
sorted list, and comes back afterward.

The `+ N weitere Signale` link on the tile makes this detour visible: it
promises the remaining signals of *this* device and delivers a tab switch
into a list of **all** devices, in which one has to search for one's own
again.

Then there's the "Show expert" toggle. It sits globally above all cards and
switches the expert group in every one of them at once — a setting without
an object, since it is only ever interesting for the one device you are
currently looking at.

## 2. What stays unchanged

- **The API.** `GET /api/devices/{id}/signals`, `PATCH /api/signals/{key}`
  and `POST /api/signals/{key}/write` remain untouched. No new route, no
  new field is created.
- **The signal row itself.** Key, title field, Loxone path, live value,
  export and resend checkboxes, raw-value writing on attributes — all of it
  moves over unchanged. No loss of functionality.
- **`signalGroupsFor` (app.js).** The helper stays as it is; its field
  `collapsible` already distinguishes the functional group from the expert
  group today. The modal only reads it differently (section 4).
- **The loading.** `startApp` fetches the signals of every device at
  startup; the tile shows them without a click. The modal therefore needs
  no loading path of its own.
- **The tile's header, value grid, control bar, and footer.**

## 3. What disappears

| Location | What |
| --- | --- |
| `index.html:242` | nav button `t('web.nav.signals')` |
| `index.html:802–930` | the entire `<section x-show="view === 'signals'">` |
| `app.js:761–770` | the `if (view === "signals")` branch in `selectView` |
| `app.js:404` | `showExpertSignals` |
| `style.css:485–491` | `.signal-group-toggle` |
| `strings.yaml` | `web.nav.signals`, `web.signals.show_expert`, `web.signals.expert_collapsed_hint` |

The `selectView` branch loaded the signals of devices for which no entry
existed yet. It is dropped with no replacement: for the normal case
`startApp` has long since loaded them, and for the error case the retry
button (`web.signals.load_button`) sits in the modal.

`expert_collapsed_hint` is dropped with no replacement rather than moving
over: "12 expert signals hidden" says the same thing as "Expert (12)" in
the `<summary>` — just not at the place you click.

### The deliberate loss

The cross-device signal list is lost. Today you can scroll across all
devices in one go and compare export checkboxes; going forward, signals
are visible only per device. The export preview only replaces this by
half — it counts per device (`inputs`, `skipped`, `hidden_count`), but does
not list individual signals.

That is the price, and it is paid deliberately: comparing across all
devices is the rare task, editing a single signal the frequent one. Should
the need for an overall overview surface later, it belongs in the export
preview, where comparing the export checkboxes belongs anyway — not in its
own tab.

## 4. The modal

**A single `<dialog>`** behind `</main>`, outside every `x-for`.

```
<dialog x-ref="signalsModal" class="signals-modal"
        @close="signalsModalDevice = null"
        @click.self="$el.close()">
```

The gain over a `<dialog>` per tile is not economy, but the same care that
the kebab design already had to spend on `aria-labelledby`: markup inside
`x-for` is delivered once **per device**. With thirty devices, thirty
complete signal tables would sit in the document, and every `id` inside
them thirty times over.

### State and opening

`signalsModalDevice` holds the device **ID**, not the object: `loadDevices`
replaces the list wholesale, so a held-onto object would afterward be a
corpse with a stale name and room. A helper `signalsModalDeviceObject()`
resolves the ID against `devices`.

`openSignalsModal(device)` sets the ID and calls `showModal()` **only in
`$nextTick`**. The order is mandatory, not style: `showModal()` sets the
initial focus on the first focusable element in the dialog, and that only
exists after Alpine has rendered the content.

`x-ref` is permitted here — unlike in the kebab menu, whose finding 3
explicitly advises against it. The objection there hits a registration that
runs *per tile*: all tiles share the one `x-data` on the `<body>`, and the
entry of the most recently rendered tile overwrites every one before it.
This `<dialog>` sits exactly once in the document; there is no one who
could overwrite it. **The comment at that spot must name this difference
explicitly**, or the next person to touch it will read it as a violation of
the existing rule.

### Closing

`@close` is the **only** reset point. The event fires on every path —
Escape, close button, backdrop, `close()` from JavaScript — so there is no
path on which the Alpine state and the visible state can drift apart. The
same role that `@toggle` plays for the kebab `<details>`.

The coupling of "JavaScript state ↔ native state" that the kebab design
warns against is unavoidable here: a `<dialog>` **must** be opened
imperatively; an `open` attribute alone does not make it modal. But it is
squeezed into this one spot instead of being spread across four handlers.

A `<dialog>` does **not** close on a click on the backdrop by itself.
`@click.self="$el.close()"` adds that: the content sits in a wrapper, so an
event with the `<dialog>` itself as the target is necessarily the backdrop.

Focus returns without any extra effort: `close()` gives it back to where it
stood before `showModal()` — the kebab's `<summary>`, because
`closeTileMenu` placed it exactly there immediately beforehand (see
section 5).

### Content

From top to bottom:

1. **Header** with `t('web.signals.modal_heading', { device: label })` and
   a close button (`aria-label` from `web.signals.modal_close`).
2. **The `signalsError` banner inside the modal**, not above it: an error
   banner behind the overlay is an invisible error, and an invisible error
   is worse than none per spec 8.1.
3. `t('web.signals.key_hint')` as a hint line.
4. The retry button `t('web.signals.load_button')`, visible only when
   `!signalsByDevice[id]`.
5. The groups from `signalGroupsFor(signalsModalDevice)`, **both as
   `<details>`** with a `<summary>` of "Functional (3)" or "Expert (12)"
   respectively. The open/closed state thus lives in the DOM instead of in
   Alpine — exactly the pattern with which the kebab design avoided the six
   review rounds of the room selection field, and the reason
   `showExpertSignals` is dropped with no replacement.

   **Why both groups get the same element** and not, as would seem
   natural, the functional one staying a plain block: two forms would mean
   two branches, and in each branch its own copy of the signal-row
   template. That very duplication is what `signalGroupsFor` abolished
   (see its comment in `app.js`: 51 duplicate lines that had to be kept in
   sync at both places on every change). One form for both groups keeps it
   to one template.

   The initial state — functional open, expert closed — is set **once**
   via `x-init="$el.open = !group.collapsible"`, not via a bound `:open`. A
   bound `:open` would reintroduce the coupling from section 1: Alpine
   re-evaluates bindings whenever their dependencies change, and
   `signalGroupsFor` depends on `signalsByDevice` — a saved signal title
   would rewrite the binding and silently close the expert group right
   after it had been opened. `x-init` runs once per element; since
   `:key="group.key"` is stable, a user's click survives every re-render.

   The state does not survive closing the modal; that is acceptable,
   because the expert group is of varying interest per device.
   `functional_vs_expert_explanation` sits inside the expert `<details>` —
   where it is needed, instead of globally above everything. The empty
   hint `none_functional` stays on the functional group.

6. The signal rows, unchanged from the existing `.device-controls`
   template.

The content hangs off `x-if="signalsModalDeviceObject()"`, so that an
intermediate render after a device is removed does not run against
`undefined`.

Live values continue to work unchanged: the websocket does not know about
the modal; `liveValueOf` and `signalIsFresh` bind as before. An open modal
thus updates itself automatically.

### When the device disappears

`removeDevice` closes the modal if it shows exactly this device. Without
that, a dialog would remain open over a device that no longer exists — and
the `x-if` guard would turn it into an empty box with no discernible
reason.

## 5. The two entry points

**Kebab menu.** A new entry after the divider line, **above**
"Export":

```
@click="closeTileMenu($el); openSignalsModal(device)"
```

The order carries the focus: `closeTileMenu` closes the `<details>` and
sets focus on its `<summary>`; the `showModal()` that immediately follows
remembers exactly this focus as the return point. The same order as for
"Export" and "Remove" next to it.

**The `+ N weitere Signale` link** (`index.html:462`) changes its target
from `selectView('signals')` to `openSignalsModal(device)`. That leads it
to where the promised signals actually are — instead of into a list in
which the device has to be searched for again.

## 6. Translation

New:

| Key | en | de |
| --- | --- | --- |
| `web.devices.menu_signals` | Edit signals… | Signale bearbeiten… |
| `web.signals.modal_heading` | Signals — {device} | Signale — {device} |
| `web.signals.modal_close` | Close | `Schließen` |

The placeholder in `modal_heading` is unproblematic: `_web_strings()`
(`api/language.py:56`) delivers unresolved templates via `raw_template()`,
precisely so that `web.*` keys may carry placeholders. The browser fills
them in `t()`.

Dropped: `web.nav.signals`, `web.signals.show_expert`,
`web.signals.expert_collapsed_hint`.

## 7. Appearance

`.signals-modal` with `width: min(46rem, 92vw)`, `max-height: 85vh` and
`overflow: auto` — a device with forty attributes is allowed to scroll, not
to grow past the edge. Plus a `::backdrop`.

The signal row inherits `.device-controls` unchanged; the expert `<details>`
inherits the `<summary>` pattern of the project-file-sync disclosure
widgets. No new color, no new font size, no new primitive.

## 8. Tests

**To adjust** in `tests/api/test_web.py`:

- `:108` and `:796` — the five-tuples of views become four-tuples.
- `:1051` — `device_section_end` anchors on `x-show="view === 'signals'"`;
  this anchor disappears and moves to `'export'`.
- `:1249–1270` — the translation test of the signal view is redirected at
  the modal; the assertions on `show_expert` and `expert_collapsed_hint`
  are dropped.

**New:**

- No more `view === 'signals'` in the markup, no `showExpertSignals` in
  `app.js`.
- **Exactly one** `<dialog` in the delivered document — the proof that it
  stays a single instance and does not become one per tile.
- The kebab entry carries `closeTileMenu($el); openSignalsModal(device)`
  and `t('web.devices.menu_signals')`.
- The `+ N weitere Signale` link points to `openSignalsModal`, no longer to
  `selectView('signals')`.
- `@close` sets `signalsModalDevice = null` (the one reset point).
- `openSignalsModal` calls `showModal()` in `$nextTick`.
- `removeDevice` closes a modal that shows the removed device.

**Browser verification in addition, not as a substitute.** These tests read
the delivered text; they prove *that* something is delivered, not that it
works. Whether `showModal()`, focus trapping, Escape, backdrop click, and
the `@close` reset actually work together is only shown by a throwaway
harness with Alpine running. That is a task of its own in the plan, not a
footnote.

## 9. Documentation

`docs/screenshots/signals.png` shows a tab that no longer exists. The
screenshot is retaken — the open modal over the device grid — and the
caption in `README.md:115–117` names the path via the kebab menu instead of
the tab. The product page keeps its six tiles and its two-column grid.
