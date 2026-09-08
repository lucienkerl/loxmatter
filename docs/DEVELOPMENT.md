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
   auf die vorherige Version nicht folgenlos ist (siehe [docs/superpowers/specs/2026-09-08-webui-updates-design.md, Abschnitt 8](superpowers/specs/2026-09-08-webui-updates-design.md)).
4. `version` in `pyproject.toml` auf dieselbe Nummer anheben. Zur
   Laufzeit liest das nichts — CI leitet die Imageversion aus dem
   Git-Ref ab, nicht aus dieser Datei —, daher fällt ein vergessener
   Schritt hier nirgends automatisiert auf: ein Image mit Tag `0.3.0`
   ließe sich klaglos aus einem Paket bauen, das noch `version =
   "0.2.0"` trägt.
5. Commit, dann `git tag -a v0.3.0 -m "0.3.0"` und `git push --tags`.
6. Die CI baut daraus `:0.3.0` und `:stable`. **Erst wenn beide in der
   Registry stehen**, ist die Version veröffentlicht — vorher zeigt die
   Oberfläche sie an, und ein Einspielen liefe ins Leere.
7. GitHub-Release anlegen, dessen Text der Changelog-Abschnitt ist. Die
   Oberfläche liest genau diesen Text.
