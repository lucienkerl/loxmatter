# Design: Make the project speak English

Date: 2026-09-07

## 1. Problem

loxmatter was written with a split language rule: English for identifiers,
German for prose. Comments, docstrings, test function names, design documents
and the GitHub repository description are German. The README is already
English, and so are all identifiers in `src/`, so the project presents an
English face to a reader who never opens a file — and a German one to anyone
who does.

That split makes the project hard to contribute to and hard to read for anyone
outside German-speaking countries. This work removes it: developer-facing
prose becomes English everywhere.

Developer-facing is the operative word. German as a *product* language stays.

## 2. Scope

### 2.1 Translated

| Area | Files | What changes |
| --- | --- | --- |
| `src/**/*.py` | 75 | Comments, docstrings, and ~60 developer-facing German string literals (log messages, OpenAPI/CLI descriptions, internal exception text, attribute docstrings) |
| `tests/**/*.py` | 82 | Comments, docstrings, and 67 German test function names |
| `docs/superpowers/specs` + `plans` | 37 | Full prose, plus 24 German filenames renamed via `git mv` |
| WebUI | `index.html`, `app.js`, `style.css` | Header comments and inline prose |
| i18n tables | `strings.yaml`, `clusters.yaml` | YAML **comments** only |
| Build and ops | `install.sh`, `scripts/*.sh`, `scripts/*.py`, `Dockerfile`, `deploy/`, `.github/workflows/ci.yml`, `pyproject.toml` | Comments |
| Dotfiles | `.gitignore`, `deploy/testhost/.gitignore`, `deploy/testhost/.env.example` | Comments |
| Remaining docs | `docs/LICENSING.md`, `deploy/testhost/README.md`, `tests/fixtures/loxone/README.md` | Full prose |
| GitHub | Repository description | Replaced with an English one |

German test names are far less widespread than a first pass suggested. The
subdirectories under `tests/` (`api`, `matter`, `model`, `projectsync`, …)
are already English throughout — 1034 of the 1195 test functions needed no
change. All 67 German names sit in two files: `tests/test_install_script.py`
(64) and `tests/test_compose_profiles.py` (3).

### 2.2 Deliberately untouched

Each of these is a decision, not an oversight:

- **`de:` values in `src/loxmatter/i18n/strings.yaml`.** This is product
  content delivered to users through the language switcher built in i18n
  phases A–C, not developer German. Removing it would delete a shipped
  feature. The YAML comments around it are translated; the values are not.
- **`src/loxmatter/web/vendor/`** (`alpine.min.js`, its `LICENSE`, its
  `README.md`). Third-party code is not ours to rewrite.
- **`LICENSE`.** Legal text.
- **Git history.** All 529 existing commit subjects stay German. Rewriting
  them would change every SHA and force a push over `main`, breaking every
  other worktree and open branch for a cosmetic gain. New commits are English
  from here on.
- **Data values in test fixtures.** `tests/fixtures/nodes/synthetic_color_light.json`
  carries the device name `"Farblampe (synthetisch)"`, which golden-file
  exports are compared against. The explanatory note beside it is translated;
  the value is not, because changing it would silently move test expectations.
- **`uv.lock`.** Generated.

### 2.3 Routed through i18n instead of translated

A survey of German string literals in `src/` found 66. About 60 are
developer-facing and are simply translated. The remainder — roughly a dozen —
reach the user without passing through i18n at all:

- `_UNEXPORTABLE_REASONS` in `api/devices.py`, rendered by
  `index.html:1835` as `x-text="signal.reason"`.
- The project-file error sentences built in `projectsync/index.py`
  (`_describe`, and the messages at lines 169 and 175), which surface in the
  project-sync flow.
- Whatever populates `new_devices_unavailable_reason`, displayed at
  `index.html:1277`.

This is a pre-existing inconsistency, not one this work introduces. Six lines
above `_UNEXPORTABLE_REASONS`, the same file explains why
`_MANUAL_DATASET_ORIGIN_KEY` holds an i18n key rather than a German sentence:
a hard German chunk inside an English sentence is half a translation. These
constants do not follow that rule.

Translating them to English would move the inconsistency rather than remove
it — German users would see English at these points while the language
switcher promises German. So they get i18n keys with `en` and `de` values,
resolved at call time, following the `_MANUAL_DATASET_ORIGIN_KEY` pattern
already established in the same file.

This is the one part of this work that changes behaviour. It is therefore
sequenced separately, carries its own tests, and is excluded from the
no-behaviour-change guarantee in section 4.

## 3. Glossary

Seven agents translating in parallel will invent seven different words for
`Gerät` unless they are held to one table. This glossary is binding; it was
derived from a frequency count of the actual vocabulary in the repository, not
invented.

| German | English | Note |
| --- | --- | --- |
| Gerät / Geraet | device | 455 occurrences, the single most common domain noun |
| Gerätetyp | device type | |
| Brücke / Bruecke | bridge | the project itself |
| Miniserver | Miniserver | Loxone product name, unchanged |
| Einlernen | commissioning | Matter's own term for the pairing flow |
| Bindung | binding | |
| Signal | signal | |
| Signalschlüssel | signal key | |
| Leitsignal | primary signal | |
| Eingang / Ausgang | input / output | Loxone virtual input/output |
| Vorlage | template | the importable Loxone template |
| Kachel | tile | device dashboard |
| Raum | room | |
| Ansicht | view | |
| Oberfläche | interface / UI | prefer "UI" in short comments |
| Knopf | button | |
| Menü / Menue | menu | |
| Modal | modal | unchanged |
| Sitzung | session | |
| Anmeldung | login | |
| Datei | file | |
| Projektdatei | project file | the Loxone `.Loxone` project |
| Schlüssel / Schluessel | key | |
| Eintrag | entry | |
| Datenbank | database | |
| Zustand | state | |
| Laufzeit | runtime | |
| Dienst | service | |
| Beobachter | observer | |
| Aufrufer | caller | |
| Aufruf | call | |
| Abschnitt | section | |
| Entwurf | design | as in the design spec |
| Aufgabe | task | plan tasks |
| Schritt | step | |
| Regel | rule | |
| Prüfung / Pruefung | check | |
| Meldung | message | |
| Fehlermeldung | error message | |
| Hinweis | note | |
| Antwort | response | HTTP context |
| Quelle | source | |
| Reihenfolge | order | |
| Sprache | language | |
| Rauchtest | smoke test | |
| Zeile | line | |
| Endpunkt | endpoint | Matter endpoint |
| Steckdose | plug | device type |
| Ausnahme | exception | |
| Einstellungen | settings | |

Umlaut transliterations (`ae`, `oe`, `ue`, `ss`) appear throughout because the
codebase avoided non-ASCII in comments. They disappear with the German.

## 4. Correctness argument

A translation must not change behaviour. That is provable rather than
hopeful, and each area gets the strongest check available to it:

- **`src/**/*.py` — normalised AST equality.** Parse each file before and
  after, strip every docstring node, and replace every remaining string
  constant with a fixed placeholder. Compare `ast.dump()` of the result.
  Equality proves that no statement, branch, call, name or operator moved —
  only comments, docstrings and string contents changed.

  Plain AST equality would be stronger but is not available here, because
  section 2.1 translates string literals too (log messages, OpenAPI
  descriptions) and those are AST nodes. Normalising string constants is the
  strongest check that remains true, and it still catches the failure that
  matters: an edit that changed logic while claiming to change prose.

  Because string *contents* are outside this check, the translated log and
  exception text is additionally covered by the existing suite — several
  tests assert on message text (`tests/matter/test_client_error_messages.py`,
  `tests/model/test_store_error_messages.py`), so a careless rewording turns
  those red rather than passing silently.
- **`tests/**/*.py` — collection count.** Function names change, so the AST
  check cannot apply. Instead `uv run pytest --collect-only -q` must report
  exactly **1236** collected tests before and after, and the full suite must
  pass. (1236, not the 1195 test *functions* in the tree: parametrisation
  expands some of them.) A renamed test that silently stopped being collected
  shows up as a count drop.
- **`strings.yaml` — parsed equality.** Load the YAML before and after; the
  resulting dictionaries must be identical. This proves only comments were
  removed and that no `de:` or `en:` value was touched.
- **Every area** — `ruff check`, `ruff format --check`, `mypy --strict`.
- **Whole repository** — the residual-German detector reports zero hits.

## 5. The detector

`scripts/check_language.py` scans tracked files for German function words
(`der`, `die`, `das`, `und`, `nicht`, `wird`, `ohne`, `für`, …) and for umlaut
transliterations in word positions where English would not produce them.

It carries an explicit allowlist matching section 2.2: `de:` blocks in
`strings.yaml`, everything under `src/loxmatter/web/vendor/`, `LICENSE`,
`uv.lock`, and the named fixture data values. The allowlist lives in the
script with a comment naming this spec, so a future reader learns why an
entry is exempt rather than deleting it.

It runs as a step in `.github/workflows/ci.yml`. Without that step the
translation holds only until the next feature branch; the rule needs an
enforcer, not just an event.

## 6. Execution

Two foundation steps run first, because everything else depends on them:

1. Write the glossary (section 3) where the agents can read it.
2. Write `scripts/check_language.py` and confirm it currently reports the
   expected large number of hits — a detector that reports zero on German
   input is broken, and this is the moment to find that out.

Then five areas run in parallel, because their file sets are disjoint and no
agent needs another's output:

- `src/**/*.py` (75 files)
- `tests/**/*.py` (82 files, including the renames)
- `docs/superpowers/` (37 files, including `git mv` renames)
- Build and ops (`install.sh`, `scripts/`, `Dockerfile`, `deploy/`, `ci.yml`,
  `pyproject.toml`, dotfiles)
- Remaining docs (`docs/LICENSING.md` and the three nested READMEs)

Each area is verified per section 4 and lands as one commit.

Then the i18n routing from section 2.3 lands as its own commit. It runs
after the parallel wave rather than inside it, because it touches files that
two of the agents own (`api/devices.py`, `projectsync/index.py`) plus
`strings.yaml` and the tests, and because it is the one change with
behavioural risk — it deserves an isolated, reviewable diff instead of being
buried in a 75-file translation commit.

Two further items cross area boundaries and are done afterwards, by hand:

- The README links into a spec section anchor
  (`…-matter-loxone-bridge-design.md#35-abbildung-generisch-statt-kuratiert`).
  Both the filename and the anchor change when `docs/superpowers/` is
  translated, so the link is fixed once that commit exists.
- The GitHub repository description is set with `gh repo edit`.

A final commit runs the detector and the full suite over the whole tree.

## 7. Risks

- **Terminology drift across parallel agents.** Mitigated by the binding
  glossary and caught by review of each area's diff.
- **A renamed test stops being collected** (e.g. a name that no longer starts
  with `test_`). Caught by the collection count in section 4.
- **Broken cross-references between renamed design documents.** The 37 files
  link to each other. After the rename, a link check over `docs/` must find no
  dangling relative targets.
- **The i18n routing (section 2.3) changes behaviour.** It is the only such
  change here. It is isolated in its own commit, and each new key is covered
  by a test asserting both the `en` and the `de` resolution, so a key that
  resolves to the wrong language or is missing a translation fails loudly
  rather than shipping.

- **The detector produces false positives** on English words that look German
  (`die` in "die cast", `war`, `bald`, `hat`, `gift`). Tuned by running it
  against the already-English README, which must come back clean.

## 8. Out of scope

Rewriting git history; translating the `de:` product strings; removing the
language switcher; any refactoring beyond the language change.

With the single, deliberate exception of the i18n routing in section 2.3, no
behaviour change is intended, and section 4 exists to prove none occurred.
