# Working on loxmatter

## Language

**Everything in this repository is written in English** — code, comments,
docstrings, test names, design documents, filenames, and commit messages.

This was not always so. Until September 2026 the rule was English identifiers
and German prose; the whole project was translated in one branch, and the git
history before that point keeps its German commit subjects. Do not translate
history, and do not translate a quotation of it.

### Commit messages

English, Conventional Commits, in the form this repository already uses:

```
feat(web): add a search field to the device view
fix(store): new commands reach devices commissioned earlier
docs(specs): record why the resend interval has a floor
refactor(tests): translate test prose to English
chore(lock): pull uv.lock up to 0.2.0
```

Say what changed and why it changed. A subject that only names the file it
touched tells a later reader nothing they could not get from `git show --stat`.

### The three places German is still correct

1. **`de:` values in `src/loxmatter/i18n/strings.yaml`.** Shipped product
   content behind the language switcher. Never translate them, never edit them
   while translating the comments around them. English text belongs under `en:`.
2. **Quotations.** A fenced code block or a backtick span in a design document
   records what the code or a command actually contained at the time. In
   `tests/`, a quoted string is data — the German the product emits under the
   `de` locale, a fixture's room name, a value an assertion compares against.
3. **German that other software wrote.** `projectsync/schema.py`'s
   `"Virtuelle Eingänge"` / `"Virtuelle Ausgänge"` are the titles Loxone Config
   itself gives caption folders in a German installation; the transliteration
   table in `export/documents.py` has German letters for keys by definition;
   everything under `tests/fixtures/` is captured data.

### User-facing text goes through i18n

A string a user can see — in the web UI, or as an `HTTPException` detail the
UI displays — belongs in `strings.yaml` with an `en` and a `de` value, resolved
at call time with `i18n.t(...)`. Writing it in English directly does not solve
the problem, it moves it: a German user then reads an English sentence inside a
German frame. `api/devices.py` and `projectsync/` show the pattern.

### The rule has an enforcer

`scripts/check_language.py` scans tracked files and runs in CI. It knows about
the exemptions above. If it reports a line you believe is fine, fix the word
list or add a narrow, commented exemption — do not exempt a whole file to make
it quiet, and do not disable the CI step.

```bash
uv run python scripts/check_language.py
```

Its green light is a vocabulary result, not a proof: it can only find words it
knows. Read what you write.

## Checks

The same set CI runs, in `docs/DEVELOPMENT.md`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run python scripts/check_language.py
```

`ruff format` also formats fenced Python inside Markdown, so a reflowed code
sample in a document can break the check.

## Further reading

- `docs/DEVELOPMENT.md` — running the tests, releasing a version
- `docs/superpowers/specs/` — design documents, one per feature
- `docs/superpowers/specs/2026-09-07-english-only-translation-design.md` —
  the translation itself, including the binding glossary in section 3
