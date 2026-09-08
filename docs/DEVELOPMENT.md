# Development

[Back to the README](../README.md)

## Running the test suite

```bash
uv sync
uv run pytest
```

The test suite runs without hardware and without network access.

## Checks that CI runs

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
```

## Eine Version veröffentlichen

Ab 0.2.0 verlassen sich fremde Installationen auf Versionsnummern: die
Oberfläche vergleicht die laufende Version mit dem letzten Release, und
der Updater spielt genau das ein, was hier veröffentlicht wurde. Diese
Kette wird nicht von Code getragen, sondern von Disziplin — deshalb steht
sie hier.

1. `CHANGELOG.md`: den Abschnitt `[Unveröffentlicht]` auf die neue Nummer
   umschreiben, mit Datum. **Für Leute schreiben, die den Code nicht
   kennen** — dieser Text steht im Bestätigungsdialog vor dem Update.
2. Nummer wählen: `PATCH` für Fehlerbehebungen, `MINOR` für neue
   Funktionen, `MAJOR` für alles, was eine bestehende Installation
   von Hand nachziehen muss.
3. **Steigt `_SCHEMA_VERSION` in `model/store.py`, gehört das in die
   Notizen.** Ein Schemasprung ist der einzige Fall, in dem ein Rückfall
   auf die vorherige Version nicht folgenlos ist (siehe Spec-Abschnitt 8).
4. Commit, dann `git tag -a v0.3.0 -m "0.3.0"` und `git push --tags`.
5. Die CI baut daraus `:0.3.0` und `:stable`. **Erst wenn beide in der
   Registry stehen**, ist die Version veröffentlicht — vorher zeigt die
   Oberfläche sie an, und ein Einspielen liefe ins Leere.
6. GitHub-Release anlegen, dessen Text der Changelog-Abschnitt ist. Die
   Oberfläche liest genau diesen Text.
