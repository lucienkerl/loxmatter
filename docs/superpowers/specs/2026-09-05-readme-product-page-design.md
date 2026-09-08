# README as a product page — design

**Date:** 2026-09-05
**Status:** agreed, waiting on an implementation plan

## 1. Starting point

Today's `README.md` (404 lines) is a manual: German prose, technically
correct, grown chronologically. It answers "how do I run this" well and "why
should I care" not at all. There is not a single picture of the UI, even
though the UI has meanwhile become the main product.

Two concrete triggers:

- The application has had **English as the default language** since i18n
  phase B/C; the README is purely German — it no longer matches what someone
  sees after the first start.
- The README of
  [`lucienkerl/mdb-esp32-cashless`](https://github.com/lucienkerl/mdb-esp32-cashless)
  serves as the model: centered hero, badges, feature table with emoji,
  screenshot gallery, ASCII architecture, quickstart further down, detail
  documents alongside.

## 2. Agreed decisions

| Question | Decision |
|---|---|
| Language | **English only.** One file, no drift. Matches the app's English default and the model README. |
| Screenshots | **Self-generated** from a seeded demo instance (fixture devices), committed under `docs/screenshots/`. No real data. |
| Structure | **Split.** README = showcase; operations and developer details move to `docs/`. |

It follows that the moved sections are **translated** as part of the move.
Roughly 300 lines of dense German technical prose. Code comments and the
`docs/superpowers/` specifications stay untouched in German — only the
user-facing documentation becomes English.

## 3. Structure of the new README

1. **Hero** — icon, `# loxmatter`, tagline, one-sentence pitch, badges
   (GPL-3.0, Python 3.12+, CI), anchor-link row.
2. **Why loxmatter** — three to four sentences: Loxone doesn't speak Matter;
   this bridge closes the gap, self-hosted, without a cloud and without
   hand-editing XML.
3. **✨ What you can do** — two-column HTML table, six cells with emoji:
   commission devices · select signals precisely · generate Loxone templates
   · project-file sync · live diagnostics · access protection and language.
4. **🖥 The web interface** — screenshot gallery, two images per row, each
   with a bold caption plus one line of explanation.
5. **🏗 How it works** — **deviation from the agreed design:** instead of a
   new ASCII diagram, the **existing Mermaid diagram** stays, only with
   English labels. Reasoning: GitHub renders Mermaid natively, and the
   existing diagram shows more with `otbr`, `matter-server`, browser and all
   the protocols than an ASCII redrawing could fit legibly. A paragraph on
   the data flow follows below it.
6. **🚀 Quickstart** — three steps, compact, with a reference to
   `docs/SETUP.md` for the full path.
7. **📚 Documentation** — table with the four new `docs/` files.
8. **🧰 Tech stack** — small table.
9. **🗺 Status** — the honest state, see section 6 of this design.
10. **🤝 Contributing** and **📄 License** — brief, license details linked.

## 4. Split into `docs/`

| Old section | New home |
|---|---|
| Prerequisites, First steps (both paths), Viewing a device | `docs/SETUP.md` |
| Running it permanently (`run`), access protection, language, export behavior, project-file sync details | `docs/OPERATIONS.md` |
| Developing | `docs/DEVELOPMENT.md` |
| Third-party software, notices in the source files | `docs/LICENSING.md` |

The README links to all four. Existing links to
`docs/superpowers/specs/…` are preserved; they move along with their
section.

## 5. Screenshots

Seven images under `docs/screenshots/`:

| File | Shows |
|---|---|
| `dashboard.png` | Device list with live values, tiles per device |
| `signals.png` | Signal view, functional vs. expert, export checkboxes |
| `export.png` | Export tab with preview table |
| `project-sync.png` | Project-file sync with diff plan |
| `system.png` | Live diagnostics: log lines, UDP capture, command log |
| `settings.png` | Settings including language switcher |
| `commissioning.png` | Commissioning card at the top of the Devices tab, with code field |

Generated from a demo instance: a temporary store, seeded from
`tests/fixtures/nodes/*.json` (four devices), password set, bridge IP and
ports pre-filled, some signals marked as exported. For `project-sync.png`,
the example project file from `tests/projectsync/conftest.py` is sent
through the real endpoint, so the plan shows real entries.

The seed script is **committed** as `scripts/demo_instance.py`: the UI has
changed three times in a week; without a reproducible path the images go
stale silently. The script starts the app via `build_app()` without a
Matter client — it needs no hardware.

## 6. Hard constraint: the warnings stay

A more promotional README must not paper over the uncomfortable spots.
These statements must be preserved verbatim in substance — in the README's
`Status` block or in the respective `docs/` file, linked:

- The **integration test against a real Miniserver is missing** — the
  generated templates have never been imported into Loxone Config.
- **No TLS.** Password and token travel in plaintext over the network.
- **Trust on first use:** between service start and the first password
  being set, anyone on the network can take over the bridge.
- `/cmd` is a **GET without origin checking** and can therefore be triggered
  by any web page opened on the same network.
- For project-file sync, **new device containers are experimental** and the
  ID scheme is unverified.
- `deploy/testhost/` is **not a hardened production image**.
- The schema migration **resets export checkboxes that were set**.
- A language switch only affects templates generated **from then on**.

The first three belong visibly in the README itself, not only in a linked
document.

## 7. Out of scope

Not part of this work:

- No change to application code, behavior, or tests of the app.
- Code comments and the `docs/superpowers/` specifications stay German.
- No new logo, no CI change, no GitHub Pages site.
- **The one-liner install script is built in its own session** (as
  requested). The quickstart initially describes manual installation;
  whoever builds the script updates step 1 afterward.

## 8. Risks

| Risk | Handling |
|---|---|
| Screenshots go stale | Commit the seed script, document generation in the script |
| Warnings get lost during the rework | Section 6 as a checklist; acceptance checks each point individually |
| Translation errors in dense technical prose | Move section by section, not freely retold |
| Dead links after the move | Check all relative links mechanically at the end |
