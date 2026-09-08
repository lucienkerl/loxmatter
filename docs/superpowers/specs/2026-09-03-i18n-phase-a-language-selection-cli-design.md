# Internationalization, phase A: language-selection infrastructure + CLI

Design, September 3, 2026. First part of a three-part internationalization
(desired outcome: base language English, switchable to German — until now
every user-facing text in the project was exclusively German). This phase
builds the language-selection infrastructure and translates the CLI in
full; phase B (API + WebUI, together because of shared error text) and
phase C (text in the generated Loxone templates) follow as their own
designs and implementations and go on using the infrastructure built here
unchanged.

This design does **not** change the language of the project itself — source
comments, docstrings, and the design documents under `docs/superpowers/specs/`
stay German, as recorded in [[user-lucien-loxmatter]]. It is exclusively
about text that an operator or user of the bridge sees.

## 1. Goal

A single, persistent language setting per installation (not per CLI call,
not per browser) with the values `en` (default) and `de`, which later
controls both the CLI output and the WebUI. In this phase: the setting
itself, where it is stored, how it is resolved, and a reusable translation
mechanism — proven against the complete CLI (`src/loxmatter/cli.py`,
currently around 60 German strings: 25 `help=` texts, 15 `typer.echo`
messages, 20 `_fail` error messages).

## 2. Non-goals of this phase

- **WebUI** (`src/loxmatter/web/`) and **API error messages**
  (`HTTPException(..., detail=...)` in `src/loxmatter/api/*.py`) — follow in
  phase B. The two are linked: `app.js` shows `detail` text directly as a
  message (see `readErrorDetail` in `app.js`); splitting the two surfaces
  would not give a clean cut.
- **Text in exported Loxone templates** (`export/signals.py`,
  `export/xml.py` — `title`/`comment` fields that become visible in Loxone
  Config itself) — follows in phase C. Separate questions there: which
  language a user typically maintains their Loxone Config in, whether a
  retroactive language switch may touch existing templates.
- **The scaffolding that Typer/Click itself generates** for a `--help`
  output (`Usage:`, `Options:`, `Arguments:`, the word "Error" before a
  `_fail` message) stays English permanently — that is a limit of the
  framework in use (Click offers no practical translation hook for it),
  not an open task for a later phase.
- No automated translation workflow (no extraction from source, no
  translation service) — with around 60 strings in this phase, translation
  is done and maintained by hand.

## 3. Translation mechanism

New package `src/loxmatter/i18n/`:

```
i18n/
  __init__.py    t(), resolve_language(), SUPPORTED_LANGUAGES
  strings.yaml   translation table
```

`strings.yaml` is a flat table with dotted namespaces, one entry per
string:

```yaml
cli.inspect.help_node:
  en: "Node ID on the running matter-server"
  de: "Node-ID am laufenden matter-server"
cli.export.fail_matter_unreachable:
  en: "matter-server at {url} unreachable — is the service running?"
  de: "matter-server unter {url} nicht erreichbar — läuft der Dienst?"
```

The `cli.*` namespace covers this phase; phase B adds `api.*` and `web.*`
in the same file, without this phase having to restructure anything —
that is exactly why it is a flat, dotted table rather than a data
structure tied to the CLI.

**Reasoning for YAML over `gettext`:** `gettext` is the industry standard,
but requires `.po`/`.mo` catalogs and an extraction/compile tool
(typically `babel`) — extra machinery for two hand-maintained languages.
PyYAML is already a dependency (`pyproject.toml`), and
`profiles/clusters.yaml` already establishes the pattern "domain data in
YAML, no Python". No new package, no build step.

`t()`:

```python
def t(key: str, **values: object) -> str:
    entry = _STRINGS[key]  # KeyError = programming error, meant to surface
    template = entry.get(_current_language(), entry["en"])
    return template.format(**values)
```

If the German translation of an existing key is missing, `t()`
automatically returns the English one — never a crash for a missing
*translation*, only for a missing *key* (a typo at the call site), which a
test is meant to catch, and which no user ever sees. `.format(**values)`
covers every interpolation used today (paths, URLs, node IDs, exception
text) — none of the ~60 calls need more than named placeholders.

## 4. Storage location and resolution of the language setting

No new table schema: the generic `setting` table (`model/store.py`,
already the basis for `AuthStore` and `BridgeSettingsStore`) gets one more
key, `"language"`. New class `model/locale_store.py::LocaleStore`,
following the same pattern:

```python
class LocaleStore:
    def get_language(self) -> str: ...  # "en" if nothing is stored
    def set_language(self, language: str) -> None: ...
```

**Resolution** (every CLI call is a new process, the language is
determined once at module start of `cli.py` — including for `--help`):

1. `LOXMATTER_LANG` (environment variable, like `LOXMATTER_STORE` and
   `LOXMATTER_API_TOKEN`): a valid value (`en`/`de`, case-insensitive)
   applies to this one process and beats everything that follows. Does not
   change the stored setting.
2. Otherwise: the setting stored in the database, if the database file
   exists and is readable — path resolution identical to
   `_resolve_store_path` (the same precedence `--store-path` >
   `LOXMATTER_STORE` > default path).
3. Otherwise: `en`.

An invalid `LOXMATTER_LANG` environment variable (neither `en` nor `de`,
case-insensitive) produces a warning on stderr and falls back to step 2/3
— no abort, since a sensible default always exists.

For that, step 2 briefly opens the database at module import of `cli.py`
(read-only is enough) and reads exactly one key. If the file is missing,
corrupted, or unreadable — each of those cases silently falls back to `en`,
exactly as other places in `cli.py` already handle a missing or unreadable
database. `--help` therefore keeps working with no preparation at all,
even on a fresh installation with no database whatsoever.

## 5. CLI integration

All ~60 strings in `cli.py` move behind `t("cli.<command>.<purpose>")`. A
new command sets the setting:

```bash
loxmatter set-language en
loxmatter set-language de
```

Not a password-like "emergency exit" the way `set-password` is (no secret,
no confirmation entry) — until phase B gives the WebUI its own toggle,
this command is the only way to change the stored setting. Like
`set-password`, it requires an **existing** database (`_fail` otherwise) —
same reasoning: creating a new, empty database out of thin air on the host
would, for a containerized installation (`LOXMATTER_STORE` only reachable
inside the container), be a silent failure reported as a success.

## 6. Tests

Around ten existing assertions on literal German CLI text
(`tests/test_cli.py`, `tests/test_export_cli.py`) are adjusted to the new
English default text — English is the default output from this phase on,
these tests unchanged check *that* a particular message appears, just in
the new default language.

New: `tests/test_i18n.py` checks the mechanism itself, independent of the
CLI — interpolation, fallback to English when a translation is missing,
`LOXMATTER_LANG` override, behavior on an invalid value. In addition, a
small set of CLI tests that run specifically with `LOXMATTER_LANG=de` and
check at least one `help=`, `echo`, and `_fail` message each for German
text — as evidence that the translation actually arrives, not as full
coverage of all ~60 strings in both languages.

## 7. Outlook

Phase B (API + WebUI) and phase C (Loxone template text) are their own,
later designs. Both build exclusively on the `i18n` package and the
`LocaleStore` resolution built here — for the WebUI there is the added
question of how a long-lived server process delivers a setting changed at
runtime (unlike the CLI, which starts fresh per call); that is the subject
of phase B, not of this infrastructure.
