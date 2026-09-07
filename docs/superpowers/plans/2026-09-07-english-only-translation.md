# English-Only Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every German developer-facing text in the repository and on GitHub with English, without changing behaviour anywhere except one deliberate i18n fix.

**Architecture:** A detector script and a verification harness are built first, because they are what turns "I translated it" into "I can show nothing German is left and nothing broke". Four disjoint file areas are then translated in parallel, each verified and committed on its own. The one behavioural change — routing user-facing strings through i18n — lands separately afterwards, followed by the GitHub description and a final whole-tree sweep.

**Tech Stack:** Python 3.12, `uv`, `pytest` (asyncio auto mode), `ruff`, `mypy --strict`, `gh` CLI, GitHub Actions.

**Spec:** [2026-09-07-english-only-translation-design.md](../specs/2026-09-07-english-only-translation-design.md). The binding glossary is **section 3 of that spec** — every task that translates prose must follow it.

## Global Constraints

- **Glossary is binding.** Use spec section 3 for every term. `Gerät`→device, `Einlernen`→commissioning, `Brücke`→bridge, `Leitsignal`→primary signal, `Kachel`→tile, `Vorlage`→template, `Signalschlüssel`→signal key. Do not invent synonyms.
- **Never touch:** `src/loxmatter/web/vendor/**` (third-party), `LICENSE`, `uv.lock`, and the `de:` *values* in `src/loxmatter/i18n/strings.yaml`.
- **Preserve the GPL header** at the top of every `.py` file byte-for-byte. It is already English.
- **No behaviour change** except Task 7. If a translation forces a code change, stop and report it rather than making it.
- **Line length is 100** (`[tool.ruff] line-length = 100`). Translated comments must respect it; English is usually shorter than German, so rewrap rather than overflow.
- **Umlaut transliterations go away.** The codebase writes `ae oe ue ss` instead of `ä ö ü ß`. English has no reason to carry them.
- **Checks that must pass** before any commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
- **Baselines to compare against:** `uv run pytest --collect-only -q` reports **1236** collected tests. `git rev-parse HEAD` at plan start is the reference for AST comparison; capture it once as `$BASE`.
- **Commit messages are English from now on**, Conventional-Commits style as the repo already uses (`feat(web):`, `docs(specs):`, `refactor(i18n):`).

---

## File Structure

| Path | Responsibility | Task |
| --- | --- | --- |
| `scripts/check_language.py` | Create. Scans tracked files for residual German; the permanent guard. | 1 |
| `tests/devtools/test_check_language.py` | Create. Proves the detector finds German and spares the allowlist. | 1 |
| `.github/workflows/ci.yml` | Modify. Adds the detector as a CI step. | 1 |
| `$SCRATCH/verify_translation.py` | Create (not committed). Normalised-AST and YAML equality harness. | 2 |
| `src/**` except `web/vendor/` | Modify. Comments, docstrings, developer-facing string literals, WebUI prose, YAML comments. | 3 |
| `tests/**` | Modify. Comments, docstrings, 67 test renames, 2 fixture renames. | 4 |
| `docs/**` | Modify + rename. 37 documents, 26 filenames, all cross-references. | 5 |
| `install.sh`, `scripts/*.sh`, `scripts/*.py`, `Dockerfile`, `deploy/**`, `pyproject.toml`, `.gitignore` | Modify. Comments. | 6 |
| `src/loxmatter/api/devices.py`, `src/loxmatter/projectsync/index.py`, `src/loxmatter/i18n/strings.yaml`, their tests | Modify. Route user-facing strings through i18n. | 7 |
| GitHub repository description | Modify via `gh repo edit`. | 8 |

Tasks 3, 4, 5 and 6 touch disjoint paths and may run in parallel. Task 7 must
wait for 3 and 4, because it edits files they own.

---

### Task 1: The residual-German detector

**Files:**
- Create: `scripts/check_language.py`
- Create: `tests/devtools/test_check_language.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: `scan_text(text: str, path: str) -> list[Finding]` where `Finding` is a `NamedTuple` with fields `line: int`, `word: str`, `text: str`. Also `main(argv: list[str] | None = None) -> int` returning 0 when clean, 1 when German is found. Tasks 3–8 all end by running `uv run python scripts/check_language.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/devtools/test_check_language.py`. Copy the GPL header from any existing test file verbatim into the top, then:

```python
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_language", Path(__file__).parents[2] / "scripts" / "check_language.py"
)
assert _SPEC and _SPEC.loader
check_language = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_language)


def test_a_german_sentence_is_reported():
    findings = check_language.scan_text("# Das Geraet wird nicht gefunden\n", "a.py")
    assert findings
    assert findings[0].line == 1


def test_an_english_sentence_is_clean():
    text = "# The device could not be found, so the export is skipped.\n"
    assert check_language.scan_text(text, "a.py") == []


def test_english_words_that_look_german_are_not_reported():
    # "die", "war", "hat", "bald", "gift", "also", "an", "in" are English too.
    text = "# The die is cast; also, an in-flight war does not hat a gift.\n"
    assert check_language.scan_text(text, "a.py") == []


def test_english_words_containing_ae_oe_ue_ss_are_not_reported():
    # This is why the transliteration check is a word list, not a pattern.
    text = "# It does go across the queue, and a true value passes the class.\n"
    assert check_language.scan_text(text, "a.py") == []


def test_a_transliterated_german_word_is_reported():
    findings = check_language.scan_text("# Uebersetzung missing\n", "a.py")
    assert findings


def test_a_literal_umlaut_is_reported():
    findings = check_language.scan_text("# Größe\n", "a.py")
    assert findings


def test_the_de_values_in_the_string_table_are_exempt():
    text = '  de: "Das Geraet ist nicht erreichbar"\n'
    assert check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml") == []


def test_the_en_values_in_the_string_table_are_still_checked():
    text = '  en: "Das Geraet ist nicht erreichbar"\n'
    assert check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml")


def test_vendored_files_are_exempt():
    text = "// Das ist fremder Code\n"
    assert check_language.scan_text(text, "src/loxmatter/web/vendor/alpine.min.js") == []


def test_the_readme_is_already_clean():
    readme = Path(__file__).parents[2] / "README.md"
    assert check_language.scan_text(readme.read_text(), "README.md") == []
```

- [ ] **Step 2: Run it to make sure it fails**

```bash
uv run pytest tests/devtools/test_check_language.py -v
```

Expected: collection error — `scripts/check_language.py` does not exist.

- [ ] **Step 3: Write the detector**

Create `scripts/check_language.py` with the GPL header, then:

```python
"""Report German text left in tracked files.

The project is English-only (see
docs/superpowers/specs/2026-09-07-english-only-translation-design.md).
This script is the enforcer for that rule: without it the translation holds
only until the next feature branch.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# German function words that no English sentence produces. Deliberately
# excludes words English shares or spells the same ("die", "war", "hat",
# "bald", "gift", "also", "an", "in", "so", "was") - a detector that cries
# wolf gets switched off, which is worse than one that misses a word.
GERMAN_WORDS = frozenset(
    """
    aber auch aus bei beim dass dem den der des diese diesem diesen dieser dieses
    durch ein eine einem einen einer eines fuer gegen ihre kann kein keine muss
    nach nicht noch nur oder sich sind ueber und vom von vor werden wird wurde
    wurden zum zur zwischen ohne weil wenn damit dann schon immer jede jeder
    jedes alle allen etwa statt sowie bereits mehrere andere weitere
    """.split()
)

# Words that only German produces. Generated once from the pre-translation
# tree (see Step 3a) and then frozen: transliterations like "Geraet" cannot
# be found by pattern, because `ae`, `oe` and `ue` are ordinary English
# ("does", "goes", "value", "true", "across"). A pattern for those would fire
# on almost every English file, and a detector that cries wolf gets switched
# off - which is worse than one that misses a word.
GERMAN_STEMS = frozenset(
    """
    geraet geraete geraets uebersetzung uebersetzungen schluessel bruecke
    oberflaeche laeuft faellt haelt traegt ueber fuer koennen koennte muessen
    waere naechste naechsten zurueck aenderung loesung groesse gemaess
    schliesst heisst weiss draussen aussen ausserhalb spaeter spaeteren
    haengt haengen zaehler zaehlt erklaerung erklaert vollstaendig
    tatsaechlich urspruenglich zusaetzlich moeglich moeglichkeit
    """.split()
)

# Literal umlauts never appear in English. This is the one pattern that can
# be trusted without a word list.
UMLAUT_CHARS = re.compile(r"[ÄÖÜäöüß]")

WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")

# Exemptions, each one a decision recorded in section 2.2 of the spec.
EXEMPT_PREFIXES = ("src/loxmatter/web/vendor/",)
EXEMPT_PATHS = frozenset({"LICENSE", "uv.lock", "docs/LICENSING.md"})
# The de: values in the string table are product content, not developer
# German - removing them would delete the language switcher.
DE_VALUE = re.compile(r"^\s*de:\s")


class Finding(NamedTuple):
    line: int
    word: str
    text: str


def _is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def scan_text(text: str, path: str) -> list[Finding]:
    if _is_exempt(path):
        return []
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if DE_VALUE.match(line):
            continue
        for match in WORD.finditer(line):
            word = match.group(0)
            lowered = word.lower()
            if lowered in GERMAN_WORDS or lowered in GERMAN_STEMS:
                findings.append(Finding(number, word, line.strip()))
                break
            if UMLAUT_CHARS.search(word):
                findings.append(Finding(number, word, line.strip()))
                break
    return findings


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv else tracked_files()
    total = 0
    for path in paths:
        file = Path(path)
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for finding in scan_text(text, path):
            print(f"{path}:{finding.line}: German word {finding.word!r}: {finding.text}")
            total += 1
    if total:
        print(f"\n{total} line(s) still contain German.", file=sys.stderr)
        return 1
    print("No German found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 3a: Ground the stem list in the actual tree**

The `GERMAN_STEMS` list above was written by hand. Confirm it covers what is
really there, and extend it with anything frequent that it misses:

```bash
git ls-files '*.py' '*.md' '*.js' '*.css' '*.html' '*.sh' '*.yaml' '*.yml' \
  | grep -v vendor/ | xargs cat \
  | grep -oiE '[a-z]*(ae|oe|ue)[a-z]*' | tr 'A-Z' 'a-z' \
  | sort | uniq -c | sort -rn | head -60
```

Read the list and add the German entries to `GERMAN_STEMS`. Ignore the
English ones — `value`, `true`, `queue`, `does`, `goes`, `issue`, `unique`,
`argue` and friends will be near the top, and that is exactly why this is a
word list rather than a pattern.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/devtools/test_check_language.py -v
```

Expected: 10 passed. If `test_the_readme_is_already_clean` fails, the word list
is too aggressive — remove the offending word rather than exempting the README.

- [ ] **Step 5: Confirm the detector actually sees the current German**

```bash
uv run python scripts/check_language.py | tail -1
```

Expected: a count in the thousands, and exit status 1. **A detector reporting
zero here is broken, not finished** — the tree is still German at this point.
Record the number; it is the burn-down baseline for Tasks 3–6.

- [ ] **Step 6: Wire it into CI**

In `.github/workflows/ci.yml`, after the `uv run mypy` step, add:

```yaml
      # The project is English-only. Without this step the rule holds only
      # until the next feature branch - see
      # docs/superpowers/specs/2026-09-07-english-only-translation-design.md
      - run: uv run python scripts/check_language.py
```

Leave it there even though it fails today; Task 8 is what turns CI green
again, and a step that is added only once it passes never proves it works.

- [ ] **Step 7: Verify lint and types**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all three clean. `scripts/` is covered by `mypy --strict`
(`pyproject.toml:78`), so the annotations above are required, not decorative.

- [ ] **Step 8: Commit**

```bash
git add scripts/check_language.py tests/devtools/test_check_language.py .github/workflows/ci.yml
git commit -m "$(cat <<'MSG'
feat(scripts): add a detector for residual German text

The project is switching to English throughout. A one-off sweep would hold
only until the next feature branch, so the rule gets an enforcer: the script
scans tracked files and runs in CI. It fails today by design - the tree is
still German - and goes green with the last translation commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 2: The no-behaviour-change harness

**Files:**
- Create: `$SCRATCH/verify_translation.py` (scratchpad, **not** committed — it is a migration aid, not an ongoing tool)

Set `SCRATCH` once, to this session's scratchpad directory:

```bash
export SCRATCH=/private/tmp/claude-501/-Users-lucienkerl-Development-matter-loxone--claude-worktrees-german-to-english-translation-f84003/b35b1211-9c04-475e-85f8-b76c9996082c/scratchpad
```

**Interfaces:**
- Consumes: `scripts/check_language.py` is unrelated; this is independent.
- Produces: a command `uv run python $SCRATCH/verify_translation.py <git-ref>` that exits 0 when every `src/**/*.py` file has an unchanged normalised AST versus that ref and `strings.yaml` parses to an identical dict. Tasks 3 and 7 run it.

- [ ] **Step 1: Write the harness**

```python
"""Prove a translation changed only prose, not code.

Compares each src/**/*.py file against a git ref with docstrings stripped and
every string constant normalised to a placeholder. Equality proves no
statement, branch, call, name or operator moved. String *contents* are
outside this check on purpose - the translation rewrites log and exception
text, which are AST nodes (spec section 4).
"""

from __future__ import annotations

import ast
import subprocess
import sys

import yaml

STRINGS = "src/loxmatter/i18n/strings.yaml"


def at_ref(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


class Normalise(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, str):
            return ast.Constant(value="<str>")
        return node


def strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


def shape(source: str) -> str:
    tree = strip_docstrings(ast.parse(source))
    return ast.dump(ast.fix_missing_locations(Normalise().visit(tree)))


def main(ref: str) -> int:
    listed = subprocess.run(
        ["git", "ls-files", "src"], capture_output=True, text=True, check=True
    ).stdout.split()
    files = [p for p in listed if p.endswith(".py")]
    failures = 0
    for path in files:
        if "/vendor/" in path:
            continue
        before = at_ref(ref, path)
        if before is None:
            print(f"NEW  {path} (not at {ref}, skipped)")
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                after = handle.read()
        except OSError:
            continue
        if shape(before) != shape(after):
            print(f"FAIL {path}: code shape changed, not just prose")
            failures += 1
    before_yaml = at_ref(ref, STRINGS)
    if before_yaml is not None:
        with open(STRINGS, encoding="utf-8") as handle:
            if yaml.safe_load(before_yaml) != yaml.safe_load(handle.read()):
                print(f"FAIL {STRINGS}: parsed content changed, not just comments")
                failures += 1
    print(f"\n{len(files)} files checked, {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
```

- [ ] **Step 2: Prove it passes on an unchanged tree**

```bash
uv run python $SCRATCH/verify_translation.py HEAD
```

Expected: `0 failure(s).` — comparing the tree against itself must be clean.

- [ ] **Step 3: Prove it catches a real code change**

```bash
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/loxmatter/i18n/__init__.py")
s = p.read_text()
p.write_text(s.replace("import re", "import re\nimport os", 1))
PY
uv run python $SCRATCH/verify_translation.py HEAD
```

Expected: `FAIL src/loxmatter/i18n/__init__.py` and `1 failure(s).`
**If this reports 0 failures the harness is broken** and every later
verification in this plan is worthless.

- [ ] **Step 4: Prove it ignores a prose-only change**

```bash
git checkout src/loxmatter/i18n/__init__.py
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/loxmatter/i18n/__init__.py")
p.write_text("# a translated comment\n" + p.read_text())
PY
uv run python $SCRATCH/verify_translation.py HEAD
```

Expected: `0 failure(s).`

- [ ] **Step 5: Restore the tree**

```bash
git checkout src/loxmatter/i18n/__init__.py
git status --short
```

Expected: no modifications. Nothing to commit — the harness is scratchpad-only.

---

### Task 3: Translate `src/`

**Files:**
- Modify: all 75 tracked files under `src/`, **except** everything in `src/loxmatter/web/vendor/`

**Interfaces:**
- Consumes: the harness from Task 2, the detector from Task 1, glossary from spec section 3.
- Produces: nothing other tasks import. Task 7 edits two files this task touches, so Task 7 must run after this one lands.

- [ ] **Step 1: Record the baseline**

```bash
export BASE=$(git rev-parse HEAD)
uv run pytest --collect-only -q | tail -1
```

Expected: `1236 tests collected`.

- [ ] **Step 2: Translate the Python prose**

Across `src/**/*.py`: every comment and docstring becomes English, and so do
the ~60 developer-facing German string literals (log messages, `Field(...)` /
`Query(...)` / `help=` descriptions, internal exception text, attribute
docstrings). Follow the glossary. Keep the GPL header untouched.

Do **not** translate these — Task 7 owns them:
- `_UNEXPORTABLE_REASONS` in `src/loxmatter/api/devices.py:125-128`
- the twelve `ProjectFormatError` message strings in
  `src/loxmatter/projectsync/` (`index.py`, `scan.py`, `ids.py`, `sync.py`,
  `patch.py`) — they reach the WebUI as HTTP error details

Where a comment cites a design document by its German filename (8 documents
do), rewrite the citation using the new name from the Task 5 rename map.

- [ ] **Step 3: Translate the WebUI prose**

In `src/loxmatter/web/index.html`, `app.js` and `style.css`: header comments
and inline comments become English. Display text is already served through
i18n via `x-text` and `t()`, so leave markup alone. One real string literal
needs translating — `app.js:752`:

```javascript
      console.error("Translations could not be loaded:", error);
```

- [ ] **Step 4: Translate the YAML comments**

In `src/loxmatter/i18n/strings.yaml` and `src/loxmatter/profiles/clusters.yaml`,
translate comment lines only. **Every `en:` and `de:` value stays byte-identical.**

- [ ] **Step 5: Prove no code moved**

```bash
uv run python $SCRATCH/verify_translation.py $BASE
```

Expected: `0 failure(s).` A `FAIL` line names a file where prose editing
changed code — fix that file, do not weaken the harness.

- [ ] **Step 6: Run the checks**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

Expected: clean, and the suite passes. Tests asserting on message text
(`tests/matter/test_client_error_messages.py`, `tests/model/test_store_error_messages.py`)
will fail if a translated message no longer matches — update the **test's**
expected string to the new English text; that is a prose change, not a
behaviour change.

- [ ] **Step 7: Check the burn-down**

```bash
uv run python scripts/check_language.py $(git ls-files 'src/*') | tail -20
```

Expected: only `src/loxmatter/i18n/strings.yaml` findings for the `en:` lines
that legitimately contain German product terms, plus the two Task 7 files.
Everything else under `src/` must be clean.

- [ ] **Step 8: Commit**

```bash
git add src
git commit -m "$(cat <<'MSG'
refactor(src): translate comments, docstrings and log text to English

Prose only. Verified with a normalised-AST comparison against the previous
commit: docstrings stripped and string constants placeheld, every file's
dump is unchanged, so no statement, branch, call or name moved.

The de: product strings are untouched, and the two spots that send German
straight to the UI are left for the i18n fix that follows.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 4: Translate `tests/`

**Files:**
- Modify: all 82 tracked files under `tests/`
- Rename: `tests/fixtures/loxone/VIU_Referenz.xml` → `VIU_reference.xml`, `tests/fixtures/loxone/VO_Referenz.xml` → `VO_reference.xml`

**Interfaces:**
- Consumes: glossary from spec section 3, rename map from Task 5.
- Produces: nothing. Runs in parallel with Tasks 3, 5, 6.

- [ ] **Step 1: Record the baseline**

```bash
uv run pytest --collect-only -q | tail -1
```

Expected: `1236 tests collected`. Write the number down.

- [ ] **Step 2: Translate comments and docstrings**

Across `tests/**/*.py`. Same rules as Task 3: glossary, GPL header untouched,
100-column limit.

Leave assertion strings that check German *output* exactly as they are —
`tests/matter/test_client_commissioning.py` and `tests/test_cli_language.py`
assert that the `de` locale really produces German. Those strings are the
thing under test. Test names like `test_cli_reports_unreachable_server_in_german`
are already English and describe them correctly.

- [ ] **Step 3: Rename the 67 German test functions**

They sit in exactly two files: `tests/test_install_script.py` (64) and
`tests/test_compose_profiles.py` (3). Every other test file is already
English. Examples of the intended style:

```python
# before                                          # after
test_ohne_funkmodul_faellt_es_auf_wifi         -> test_without_a_radio_module_it_falls_back_to_wifi
test_macos_wird_abgewiesen                     -> test_macos_is_refused
test_zu_langes_oktett_wird_abgewiesen          -> test_an_over_long_octet_is_refused
test_nur_die_fehlenden_pakete_werden_installiert -> test_only_the_missing_packages_are_installed
test_thread_ohne_geraet_und_ohne_terminal_bricht_ab -> test_thread_without_a_device_and_without_a_terminal_aborts
test_root_wird_gewarnt_aber_nicht_gestoppt     -> test_root_is_warned_but_not_stopped
test_gesundheitspruefung_laeuft                -> test_the_health_check_runs
```

A test name is the sentence the run reads out. Keep them full sentences, not
abbreviations.

- [ ] **Step 4: Rename the two German fixtures**

```bash
git mv tests/fixtures/loxone/VIU_Referenz.xml tests/fixtures/loxone/VIU_reference.xml
git mv tests/fixtures/loxone/VO_Referenz.xml tests/fixtures/loxone/VO_reference.xml
grep -rln 'VIU_Referenz\|VO_Referenz' . --exclude-dir=.git
```

Update every file the `grep` lists (at minimum `tests/devtools/test_fake_miniserver.py`).

- [ ] **Step 5: Translate the fixture prose**

`tests/fixtures/nodes/synthetic_color_light.json` carries a long German
explanatory note. Translate the **note**. Leave the device name value
`"Farblampe (synthetisch)"` exactly as it is — golden-file exports compare
against it, and changing it would silently move test expectations.
Also translate `tests/fixtures/loxone/README.md`.

- [ ] **Step 6: Prove nothing stopped being collected**

```bash
uv run pytest --collect-only -q | tail -1
```

Expected: `1236 tests collected` — **exactly** the Step 1 number. A drop means
a rename broke discovery (a name that no longer starts with `test_`).

- [ ] **Step 7: Run the checks**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

Expected: all clean, suite passes.

- [ ] **Step 8: Commit**

```bash
git add tests
git commit -m "$(cat <<'MSG'
refactor(tests): translate test prose and names to English

A test name is the sentence the run reads out, so the 67 German ones are
renamed rather than left as identifiers. They sat in two files; the rest of
the suite was already English.

Collection still reports 1236 tests, so nothing fell out of discovery. The
assertions that check German output are untouched - there the German is the
thing under test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 5: Translate and rename the design documents

**Files:**
- Modify: 37 files under `docs/superpowers/`
- Rename: the 26 German filenames below
- Modify: `README.md:33`, `docs/OPERATIONS.md:27,42,63,107,139`, `docs/LICENSING.md`

**Interfaces:**
- Consumes: glossary from spec section 3.
- Produces: **the rename map below.** Tasks 3 and 4 cite design documents in comments and must use these new names.

- [ ] **Step 1: Rename the files**

```bash
cd "$(git rev-parse --show-toplevel)"
D=docs/superpowers
git mv $D/plans/2026-09-02-phase-4-laufzeit.md                        $D/plans/2026-09-02-phase-4-runtime.md
git mv $D/plans/2026-09-03-diagnose-livefeed.md                       $D/plans/2026-09-03-diagnostics-live-feed.md
git mv $D/plans/2026-09-03-geraete-dashboard-und-export.md            $D/plans/2026-09-03-device-dashboard-and-export.md
git mv $D/plans/2026-09-03-i18n-phase-a-sprachwahl-cli.md             $D/plans/2026-09-03-i18n-phase-a-language-selection-cli.md
git mv $D/plans/2026-09-03-projektdatei-sync.md                       $D/plans/2026-09-03-project-file-sync.md
git mv $D/plans/2026-09-03-signalauswahl.md                           $D/plans/2026-09-03-signal-selection.md
git mv $D/plans/2026-09-03-webui-login-release-hinweis.md             $D/plans/2026-09-03-webui-login-release-note.md
git mv $D/plans/2026-09-04-live-werte-neuer-geraete.md                $D/plans/2026-09-04-live-values-for-new-devices.md
git mv $D/plans/2026-09-04-periodischer-resend.md                     $D/plans/2026-09-04-periodic-resend.md
git mv $D/plans/2026-09-05-geraete-tab-raeume-und-kachelraster.md     $D/plans/2026-09-05-devices-tab-rooms-and-tile-grid.md
git mv $D/plans/2026-09-05-kachel-kebab-menue.md                      $D/plans/2026-09-05-tile-kebab-menu.md
git mv $D/plans/2026-09-05-readme-produktseite.md                     $D/plans/2026-09-05-readme-product-page.md
git mv $D/plans/2026-09-05-signale-als-modal.md                       $D/plans/2026-09-05-signals-as-a-modal.md
git mv $D/plans/2026-09-06-suchfeld-optik.md                          $D/plans/2026-09-06-search-field-appearance.md
git mv $D/specs/2026-09-03-diagnose-livefeed-design.md                $D/specs/2026-09-03-diagnostics-live-feed-design.md
git mv $D/specs/2026-09-03-geraete-dashboard-und-export-design.md     $D/specs/2026-09-03-device-dashboard-and-export-design.md
git mv $D/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md      $D/specs/2026-09-03-i18n-phase-a-language-selection-cli-design.md
git mv $D/specs/2026-09-03-projektdatei-sync-design.md                $D/specs/2026-09-03-project-file-sync-design.md
git mv $D/specs/2026-09-03-signalauswahl-design.md                    $D/specs/2026-09-03-signal-selection-design.md
git mv $D/specs/2026-09-04-live-werte-neuer-geraete-design.md         $D/specs/2026-09-04-live-values-for-new-devices-design.md
git mv $D/specs/2026-09-04-periodischer-resend-design.md              $D/specs/2026-09-04-periodic-resend-design.md
git mv $D/specs/2026-09-05-geraete-tab-raeume-und-kachelraster-design.md $D/specs/2026-09-05-devices-tab-rooms-and-tile-grid-design.md
git mv $D/specs/2026-09-05-kachel-kebab-menue-design.md               $D/specs/2026-09-05-tile-kebab-menu-design.md
git mv $D/specs/2026-09-05-readme-produktseite-design.md              $D/specs/2026-09-05-readme-product-page-design.md
git mv $D/specs/2026-09-05-signale-als-modal-design.md                $D/specs/2026-09-05-signals-as-a-modal-design.md
git mv $D/specs/2026-09-06-suchfeld-optik-design.md                   $D/specs/2026-09-06-search-field-appearance-design.md
```

The remaining 11 filenames are already English and stay: `2026-09-01-phase-1-matter-adapter.md`, `2026-09-01-roadmap.md`, `2026-09-02-phase-3-exporter.md`, `2026-09-02-phase-5-webui.md`, `2026-09-03-webui-login.md`, `2026-09-04-i18n-phase-bc-api-webui-export.md`, `2026-09-05-install-oneliner.md`, `2026-09-01-matter-loxone-bridge-design.md`, `2026-09-03-webui-login-design.md`, `2026-09-04-i18n-phase-bc-api-webui-export-design.md`, `2026-09-05-install-oneliner-design.md`.

- [ ] **Step 2: Translate the prose**

All 37 documents, following the glossary. These are historical records — translate
what they say, do not rewrite what they decided or bring them up to date.

- [ ] **Step 3: Fix the internal cross-references**

There are 49 markdown links inside `docs/superpowers/`, 25 of which point at
renamed files. Section anchors change too, because the headings are being
translated.

```bash
grep -rhoE '\]\([^)]*\.md[^)]*\)' docs/superpowers/ | sort -u
```

Update every one, then verify none dangles:

```bash
python3 - <<'PY'
import pathlib, re
bad = 0
for md in pathlib.Path("docs").rglob("*.md"):
    for target in re.findall(r"\]\((?!https?:)([^)#]+\.md)", md.read_text()):
        if not (md.parent / target).resolve().exists():
            print(f"DANGLING {md}: {target}")
            bad += 1
print(f"{bad} dangling link(s)")
PY
```

Expected: `0 dangling link(s)`.

- [ ] **Step 4: Fix the references from outside**

`README.md:33` points at a section anchor that changes with the heading:

```markdown
[section 3.5](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#35-mapping-generically-not-from-a-curated-list)
```

Set the anchor to match whatever `### 3.5 Abbildung: generisch statt kuratiert`
becomes, GitHub-slugified (lowercase, spaces to hyphens, punctuation dropped).
Then fix the five links in `docs/OPERATIONS.md` (lines 27, 42, 63, 107, 139) to
the new filenames, and translate `docs/LICENSING.md` in full.

- [ ] **Step 5: Verify**

```bash
uv run python scripts/check_language.py $(git ls-files 'docs/*') | tail -1
uv run pytest -q
```

Expected: docs clean; the suite still passes (some tests cite spec filenames).

- [ ] **Step 6: Commit**

```bash
git add docs README.md
git commit -m "$(cat <<'MSG'
docs: translate the design documents to English and rename them

All 37 specs and plans, plus the 26 German filenames. Cross-references and
section anchors are pulled along, inside docs/ and from the README and
OPERATIONS.

These are historical records, so this translates what they say rather than
revising what they decided.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 6: Translate build and ops files

**Files:**
- Modify: `install.sh`, `scripts/otbr-watchdog.sh`, `scripts/update.sh`, `scripts/capture_screenshots.py`, `scripts/dev_web_server.py`, `scripts/record_node.py`, `Dockerfile`, `deploy/testhost/docker-compose.yml`, `deploy/testhost/.env.example`, `deploy/testhost/.gitignore`, `deploy/testhost/README.md`, `pyproject.toml`, `.gitignore`

**Interfaces:**
- Consumes: glossary from spec section 3.
- Produces: nothing. Runs in parallel with Tasks 3, 4, 5.

- [ ] **Step 1: Translate the shell and config comments**

Comments only — no command, flag, path or default value changes. `install.sh`
is fetched and run via `curl | sh` on other people's machines, so treat it
with particular care: a stray edit outside a comment is a live hazard.

In `pyproject.toml`, this includes the `slow` marker description at line 60
and the three mypy override comments.

- [ ] **Step 2: Translate the script docstrings and comments**

`scripts/*.py` are covered by `mypy --strict`, so keep annotations intact.

- [ ] **Step 3: Verify `install.sh` still parses**

```bash
shellcheck -s sh install.sh && sh -n install.sh
```

Expected: both silent. This is the same check CI runs.

- [ ] **Step 4: Verify the install tests still pass**

```bash
uv run pytest tests/test_install_script.py tests/test_compose_profiles.py -q
```

Expected: pass. These tests read `install.sh` and the compose file, so a
mangled comment can genuinely break them.

- [ ] **Step 5: Run the full checks**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add install.sh scripts Dockerfile deploy pyproject.toml .gitignore
git commit -m "$(cat <<'MSG'
chore: translate build and ops comments to English

Comments only - no command, flag, path or default changed. install.sh gets
the same shellcheck and sh -n treatment CI applies, since it is fetched and
run with curl | sh on machines that are not ours.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 7: Route the user-facing strings through i18n

This is the one task that changes behaviour. It runs **after** Tasks 3 and 4.

**Files:**
- Modify: `src/loxmatter/api/devices.py:125-128`
- Modify: `src/loxmatter/projectsync/index.py`, `scan.py`, `ids.py`, `sync.py`, `patch.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_devices.py`, `tests/projectsync/` tests

**Interfaces:**
- Consumes: `i18n.t(key, **values)` from `src/loxmatter/i18n/__init__.py`, and the existing pattern at `src/loxmatter/api/devices.py:118` (`_MANUAL_DATASET_ORIGIN_KEY`).
- Produces: `api.devices.unexportable_text`, `api.devices.unexportable_none`, and one `projectsync.*` key per `ProjectFormatError` message (12 of them, listed in Step 6).

Every one of these reaches the user: `api/project_sync.py:118` and `:127`
catch `ProjectFormatError` and pass `str(exc)` as the `detail` of an
`HTTPException`, which the WebUI displays.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_devices.py`:

```python
async def test_the_unexportable_reason_follows_the_selected_language(client, monkeypatch):
    from loxmatter import i18n

    i18n.set_language("en")
    english = (await client.get("/api/devices")).json()
    i18n.set_language("de")
    german = (await client.get("/api/devices")).json()

    def reasons(payload: dict) -> set[str]:
        return {
            signal["reason"]
            for device in payload["devices"]
            for signal in device["signals"]
            if signal.get("reason")
        }

    assert reasons(english), "fixture must contain at least one unexportable signal"
    assert reasons(english) != reasons(german)
    assert any("virtual UDP input" in reason for reason in reasons(english))
    assert any("virtueller UDP-Eingang" in reason for reason in reasons(german))
```

Adjust the client fixture and payload shape to match the existing tests in
that file; the assertion that matters is *English and German differ, and both
are non-empty*.

- [ ] **Step 2: Run it to make sure it fails**

```bash
uv run pytest tests/api/test_devices.py -k unexportable_reason -v
```

Expected: FAIL — both languages currently return the same German string.

- [ ] **Step 3: Add the keys**

In `src/loxmatter/i18n/strings.yaml`:

```yaml
api.devices.unexportable_text:
  en: "text - a virtual UDP input only knows numbers and digital values"
  de: "Text - ein virtueller UDP-Eingang kennt nur Zahlen und digitale Werte"
api.devices.unexportable_none:
  en: "no mappable value - list, struct, or currently without a value (null)"
  de: "kein abbildbarer Wert - Liste, Struktur, oder derzeit ohne Wert (null)"
```

- [ ] **Step 4: Resolve at call time**

Replace the constant dict in `src/loxmatter/api/devices.py`:

```python
# Why a signal is not exportable (spec 6.6) - only for the two cases
# `Exportability` distinguishes from ANALOG/DIGITAL. `NONE` covers lists and
# structs as well as (silently, see spec 6.6) null values; the table cannot
# tell those apart because `classify()` does not either.
#
# Keys rather than finished sentences, resolved at call time, for the same
# reason as `_MANUAL_DATASET_ORIGIN_KEY` above: a hard German chunk inside an
# English sentence is half a translation, and the language is not settled at
# import time.
_UNEXPORTABLE_REASON_KEYS: dict[Exportability, str] = {
    Exportability.TEXT: "api.devices.unexportable_text",
    Exportability.NONE: "api.devices.unexportable_none",
}
```

And at the use site (was line 157):

```python
    reason_key = None if exportable else _UNEXPORTABLE_REASON_KEYS.get(signal.exportability)
    reason = i18n.t(reason_key) if reason_key else None
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/api/test_devices.py -k unexportable_reason -v
```

Expected: PASS.

- [ ] **Step 6: Do the same for the twelve `projectsync` messages**

These are the strings, all of them raised as `ProjectFormatError` (or its
subclass `AmbiguousMiniserverError`) and therefore displayed by the WebUI:

| Key | Site | German value (moved verbatim) |
| --- | --- | --- |
| `projectsync.no_ip_known` | `index.py:127` | `keine IP bekannt` |
| `projectsync.no_miniserver_configured` | `index.py:160` | `Diese Projektdatei enthaelt keinen einzigen konfigurierten Miniserver …` |
| `projectsync.miniserver_ip_not_found` | `index.py:168` | `Kein Miniserver mit der IP {ip} … Vorhanden: {candidates}.` |
| `projectsync.multiple_miniservers` | `index.py:174` | `Diese Projektdatei enthaelt mehrere Miniserver: {candidates}. …` |
| `projectsync.miniserver_without_configuration` | `index.py:190` | `Der Miniserver „{title}“ hat … noch keinerlei Konfiguration …` |
| `projectsync.no_uid_found` | `ids.py:56` | `Keine bestehende U-ID im erwarteten Format …` |
| `projectsync.unexpected_eof_attribute` | `scan.py:102` | `Unerwartetes Dateiende: ein Attribut oder Tag …` |
| `projectsync.unexpected_eof_control` | `scan.py:124` | `Unerwartetes Dateiende: <C> ohne schliessendes </C>.` |
| `projectsync.missing_control_list_open` | `scan.py:178` | `Keine gueltige Loxone-Projektdatei: <ControlList>-Wurzelelement fehlt.` |
| `projectsync.missing_control_list_close` | `scan.py:182` | `Keine gueltige Loxone-Projektdatei: </ControlList> fehlt.` |
| `projectsync.not_utf8` | `sync.py:65` | `Die hochgeladene Datei ist keine gueltige UTF-8-Textdatei …` |
| `projectsync.installation_suffix` | `patch.py` | the `Installations-Suffix` message |

Write the failing test first, in `tests/projectsync/test_index.py`:

```python
async def test_project_errors_follow_the_selected_language(tmp_path):
    from loxmatter import i18n
    from loxmatter.projectsync.index import AmbiguousMiniserverError, build_index

    text = "<ControlList></ControlList>"

    i18n.set_language("de")
    with pytest.raises(AmbiguousMiniserverError) as german:
        build_index(text)

    i18n.set_language("en")
    with pytest.raises(AmbiguousMiniserverError) as english:
        build_index(text)

    assert "keinen einzigen konfigurierten Miniserver" in str(german.value)
    assert "not a single configured Miniserver" in str(english.value)
    assert str(german.value) != str(english.value)
```

Run it and watch it fail — both languages return German today:

```bash
uv run pytest tests/projectsync/test_index.py -k selected_language -v
```

Then add the twelve keys to `strings.yaml` with the German values moved over
unchanged, and replace each literal with `i18n.t("projectsync.…", …)`,
passing the interpolated parts as named values rather than building f-strings:

```python
    if not loxlives:
        raise AmbiguousMiniserverError(i18n.t("projectsync.no_miniserver_configured"))
    if miniserver_ip:
        matches = [ll for ll in loxlives if ll.attrs.get("IntAddr") == miniserver_ip]
        if not matches:
            raise AmbiguousMiniserverError(
                i18n.t(
                    "projectsync.miniserver_ip_not_found",
                    ip=repr(miniserver_ip),
                    candidates=_describe(loxlives),
                ),
                _candidates(loxlives),
            )
        return matches[0]
```

Six existing tests assert on the German substrings and must move to the new
English text, since the suite runs under the default language:
`tests/projectsync/test_index.py:124,176`, `test_patch.py:470`,
`test_sync.py:113,166`. Leave `test_index.py:117,169` alone — they match on
the IP `10.0.0.99`, which is interpolated and language-independent.

- [ ] **Step 7: Run everything**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
uv run python scripts/check_language.py $(git ls-files 'src/*') | tail -1
```

Expected: all clean; `src/` reports no German outside `de:` values.

- [ ] **Step 8: Commit**

```bash
git add src tests
git commit -m "$(cat <<'MSG'
fix(i18n): route the last user-facing strings through the string table

The unexportable-signal reasons and the project-file errors were hardcoded
German that went straight to the UI, so the language switcher never reached
them. devices.py already explained the right pattern six lines above the
offending constant; this makes those two follow it.

German users see the same sentences as before - the values moved verbatim.
English users now get English where they previously got German.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 8: GitHub description and the final sweep

**Files:**
- Modify: GitHub repository description (no file)

**Interfaces:**
- Consumes: everything above.
- Produces: a green CI run.

- [ ] **Step 1: Set the repository description**

The current one is German: *"Bindet Matter-Geraete (Thread und WiFi) an einen
Loxone Miniserver an: erzeugt importierbare Loxone-Vorlagen je Geraet, bruecke
Werte per UDP und Befehle per HTTP, mit WebUI zum Einlernen und Bedienen."*

```bash
gh repo edit --description "Bridges Matter devices (Thread and WiFi) to a Loxone Miniserver: generates importable Loxone templates per device, bridges values over UDP and commands over HTTP, with a web UI for commissioning and control."
gh repo view --json description
```

There are no issues, pull requests or releases on the repository, so the
description is the whole GitHub surface.

- [ ] **Step 2: Run the detector over the whole tree**

```bash
uv run python scripts/check_language.py
```

Expected: `No German found.` and exit status 0. Any remaining line is either
real German to fix, or a false positive — decide which, and if it is a false
positive, fix the word list in `scripts/check_language.py` rather than adding
a path exemption.

- [ ] **Step 3: Run the full CI set locally**

```bash
shellcheck -s sh install.sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run pytest --collect-only -q | tail -1
```

Expected: everything passes, and collection reports **1236** — the same number
as before the translation started.

- [ ] **Step 4: Confirm the whole change is prose-only where it claims to be**

```bash
uv run python $SCRATCH/verify_translation.py $BASE
```

Expected: failures **only** in `src/loxmatter/api/devices.py` and
`src/loxmatter/projectsync/index.py` — the two files Task 7 deliberately
changed. Any third file named here is an accidental code change that slipped
through an earlier task's review.

- [ ] **Step 5: Commit if anything was fixed in Steps 2–4**

```bash
git add -A
git commit -m "$(cat <<'MSG'
chore: close out the English-only translation

Detector reports clean over the whole tree and CI's checks pass locally.
Collection still reports 1236 tests.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

- [ ] **Step 6: Push and confirm CI is green**

```bash
git push -u origin claude/german-to-english-translation-f84003
gh run watch
```

Expected: all steps green, including the new `check_language.py` step that was
red from Task 1 onwards.
