# Internationalisierung, Phase B+C: API, WebUI und Export-Vorlagen

Entwurf, 4. September 2026. Setzt [Phase A](2026-09-03-i18n-phase-a-sprachwahl-cli-design.md)
fort (Grundsprache Englisch, umschaltbar auf Deutsch; dort bereits gebaut:
`loxmatter.i18n.t()`/`strings.yaml`, die gemeinsame Spracheinstellung in
`LocaleStore`, die vollständig übersetzte CLI). Phase A hatte API+WebUI
und die Vorlagentexte ausdrücklich zurückgestellt — dieser Entwurf holt
beides nach, zusammen, auf ausdrücklichen Wunsch: Phase C (Vorlagentexte)
ist mit rund neun kurzen Feldern klein genug, um an Phase B (API+WebUI)
dranzuhängen, statt einen eigenen Entwurfszyklus zu rechtfertigen.

Die Auflösung von drei offenen Fragen aus Phase A, geklärt vor diesem
Entwurf: die Vorlagentexte folgen der gemeinsamen Spracheinstellung (nicht
fest verdrahtet); die WebUI bekommt ihre Übersetzungen über einen neuen
API-Endpunkt als JSON, nicht über ein neu eingeführtes Templating (die
WebUI hat keins, siehe Abschnitt 3); ein Sprachwechsel in der WebUI lädt
die Seite neu, statt reaktiv ohne Neuladen umzuschalten.

## 1. Ziel

Dieselbe eine, gemeinsame Spracheinstellung aus Phase A steuert jetzt auch:
die Fehlermeldungen der API (`HTTPException`-`detail`-Texte), die gesamte
WebUI (statischer HTML-Text und die von `app.js` erzeugten Texte), und die
Titel-/Kommentarfelder in exportierten Loxone-Vorlagen. Derselbe
Mechanismus (`i18n.t()`, dieselbe `strings.yaml`) wird um drei
Namensräume erweitert — keine zweite Infrastruktur.

## 2. Nicht-Ziele

- Kein Übersetzungsworkflow jenseits von Handarbeit — wie Phase A, bei
  diesem Zuwachs an Zeichenketten (rund 120) weiterhin vertretbar.
- Keine Sprache pro Browser/Nutzer — unverändert eine Einstellung für die
  ganze Installation (Phase A, Abschnitt 1).
- Kein Templating-Motor (Jinja2 o. ä.) für `index.html` — siehe Abschnitt 3
  für die Begründung.
- Keine rückwirkende Übersetzung bereits exportierter, in Loxone Config
  importierter Vorlagen — nur neu erzeugte Exporte sind betroffen, exakt
  wie bei einer Änderung der Signalauswahl (Hauptdokument, README).

## 3. Namensräume in `strings.yaml`

Drei neue punktierte Präfixe neben dem bestehenden `cli.*`:

| Präfix | Umfang | Herkunft |
|---|---|---|
| `api.*` | `HTTPException(detail=...)` in `api/control.py`, `api/devices.py`, `api/auth.py`, `api/diagnostics.py`, `api/export.py` — rund 29 Aufrufstellen, plus `api.export.readme_text` für den Text der `Import-Anleitung.txt` im Export-ZIP | Python, server-seitig |
| `web.*` | Statischer Text in `web/index.html` (~40) und von `web/app.js` erzeugter Text (~40) | JavaScript/Alpine, client-seitig |
| `export.*` | Titel-/Kommentarfelder in `export/documents.py` und `export/signals.py` (rund neun Zeichenketten) | Python, server- UND CLI-seitig (beide rufen dieselben Funktionen, siehe Hauptdokument Abschnitt 4.2 und Phase-A-Erfahrung mit `_load_snapshot`) |

`index.html` wird heute als reine, unveränderte Datei ausgeliefert
(`FileResponse`, kein Jinja2, kein serverseitiges Rendern) — ein
Templating-Motor für die HTML-Datei würde deshalb eine neue Abhängigkeit
bedeuten UND nur die Hälfte des Problems lösen, weil die von `app.js` zur
Laufzeit erzeugten Texte (Toasts, berechnete Beschriftungen) davon
unberührt blieben und ohnehin einen eigenen Client-Mechanismus bräuchten.
Deshalb bleibt die Auslieferung unverändert statisch, und `web.*` erreicht
den Client stattdessen über einen JSON-Endpunkt (Abschnitt 5).

## 4. Sprachauflösung im langlebigen Serverprozess

Anders als die CLI (jeder Aufruf ein neuer Prozess, Sprache einmal beim
Modulimport aufgelöst, siehe Phase A Abschnitt 4-5) läuft `loxmatter run`
als ein einziger, langlebiger Prozess. Eine einmalige Auflösung beim
Start (`build_app()`) hätte eine Lücke: ändert jemand die gespeicherte
Einstellung über die CLI (`loxmatter set-language`), während `loxmatter
run` bereits läuft, sieht der laufende Serverprozess das nicht — `LocaleStore`
und `BridgeSettingsStore` liegen in **derselben** Datenbank, aber
`i18n.t()`s aktuelle Sprache ist ein Zustand **dieses einen Prozesses**.

**Deshalb liest eine neue Middleware die gespeicherte Einstellung bei
JEDER eingehenden Anfrage frisch** — vor dem Routing, vor der
Anmeldeprüfung, für jede Anfrage gleichermaßen (`/`, `/static/*`, `/cmd`,
`/resync`, `/api/*`):

```python
@app.middleware("http")
async def sync_language(request: Request, call_next):
    i18n.set_language(store.locale.get_language())
    return await call_next(request)
```

Das ist keine neue Idee, sondern folgt einem bereits etablierten Muster
dieses Projekts: `BridgeSettingsStore.get()` liest ebenfalls bei jedem
Aufruf frisch aus derselben Tabelle, ohne eigenen Zwischenspeicher (siehe
`settings_store.py`) — ein Cache mit eigener Invalidierung wäre hier eine
zusätzliche, unnötige Fehlerquelle. Die Kosten sind eine zusätzliche,
sehr kleine SQLite-Abfrage pro Anfrage; `LocaleStore.get_language()` wirft
nie (Phase A, Abschnitt 4), ein Datenbankfehler an dieser Stelle blockiert
also keine Anfrage.

**Konsequenz für `PATCH /api/language` (Abschnitt 5):** die Route selbst
muss `i18n.set_language(...)` NICHT aufrufen — sie schreibt nur in die
Datenbank, die nächste Anfrage (einschließlich der eigenen Antwort dieser
Route) liest die neue Einstellung bereits über die Middleware. Das
vereinfacht die Route gegenüber dem in der Diskussion zunächst skizzierten
Weg (expliziter `i18n.set_language()`-Aufruf direkt in der Route, analog zu
Phase As Nachbesserung an `set-language` in der CLI) — dort war ein
expliziter Aufruf nötig, weil die CLI keine Middleware hat und jeder
Aufruf ohnehin ein neuer Prozess ist; hier übernimmt die Middleware diese
Rolle für JEDE Anfrage, nicht nur für die eine, die die Einstellung
ändert.

## 5. Neue Routen

Neues, kleines Modul `api/language.py`, im Zuschnitt analog zu
`api/settings.py`:

- **`GET /api/i18n`** — liefert `{"language": "en", "strings": {"web.xyz": "...", ...}}`
  (nur die `web.*`-Teilmenge von `strings.yaml`, in der aktuell
  eingestellten Sprache aufgelöst). **Bewusst ausgenommen von der
  Anmeldeprüfung** — die dritte, ausdrücklich benannte Ausnahme neben
  `/cmd` und `/resync` (WebUI-Login-Entwurf, Abschnitt dort zu den beiden
  ersten Ausnahmen), aber aus einem anderen Grund: nicht weil ein Client
  keinen Header mitschicken kann, sondern weil die Ersteinrichtungs- und
  Anmeldeseite selbst diese Texte braucht, um sich überhaupt anzuzeigen —
  vor jeder Anmeldung. Kein Sicherheitsrisiko: die Antwort enthält keine
  Geheimnisse, nur Übersetzungstexte und die aktuell eingestellte Sprache.
- **`PATCH /api/language`** — Body `{"language": "de"}`, verlangt eine
  gültige Sitzung oder ein gültiges Token wie jede andere `/api`-Route
  (analog zu `PATCH /api/settings`). Ruft `store.locale.set_language(...)`
  auf; wirft `ValueError` (→ 400) für einen nicht unterstützten Wert,
  genau wie `LocaleStore.set_language` es aus Phase A bereits tut.

`build_api_guard` (`loxone/server.py`) bekommt eine dritte Ausnahme neben
`/cmd`/`/resync`: `GET /api/i18n`.

## 6. API-Fehlermeldungen (`api.*`)

Jede `HTTPException(..., detail="...")`-Zeichenkette in `control.py`,
`devices.py`, `auth.py`, `diagnostics.py`, `export.py` wandert hinter
`i18n.t("api.<modul>.<zweck>", **werte)` — mechanisch identisch zu Phase
As CLI-Migration (Abschnitte 4-5 dort), nur ein anderer Aufrufort. Diese
Texte sind KEINE zweite Übersetzung für die WebUI: `app.js`s
`readErrorDetail` (Phase-A-Untersuchung) zeigt `detail` bereits heute
unverändert als Meldung an — die WebUI übersetzt diese Texte also nicht
noch einmal client-seitig, sie zeigt einfach, was die API in der gerade
gültigen Sprache zurückgibt (Middleware, Abschnitt 4, sorgt dafür, dass
das dieselbe Sprache ist wie die der übrigen Anfrage).

## 7. WebUI (`web.*`)

**Ladevorgang.** `app.js`s `init()` ruft `GET /api/i18n` als ALLERERSTES
auf (vor allem anderen, was heute dort passiert), befüllt einen
Alpine-Store (`language`, `strings`) und erst danach wird die Oberfläche
sichtbar — verdeckt bis dahin über `x-cloak` (eine CSS-Regel `[x-cloak] {
display: none }`, Alpine entfernt das Attribut selbst sobald
initialisiert), damit kein kurzes Aufblitzen von rohem `{key}`-Text oder
englischem Text vor der eigentlich eingestellten Sprache sichtbar wird.

**Übersetzungshelfer.** Eine Funktion `t(key, values = {})` im
Alpine-Store, die den Wert aus dem geladenen `strings`-Objekt liest und
`{platzhalter}` durch `values` ersetzt (dieselbe `.format()`-Konvention
wie Python-seitig, hier von Hand als einfache String-Ersetzung
nachgebaut — keine neue Abhängigkeit für so wenig Aufwand). Jeder
statische Text in `index.html` wird zu `x-text="t('web.xyz')"` bzw. für
Attribute (`placeholder`, `title`) zu `:placeholder="t('web.xyz')"`. Jede
in `app.js` erzeugte Zeichenkette (Toasts, berechnete Beschriftungen)
ruft denselben `t()` auf.

**Sprachumschalter.** Die bereits leere Platzhalterkarte „Weitere
Einstellungen" im Settings-Tab (`index.html`) wird zum EN/DE-Umschalter —
zwei Schaltflächen statt eines Dropdowns (binäre Wahl). Ein Klick ruft
`PATCH /api/language` auf und lädt danach die Seite neu
(`window.location.reload()`) — die bestätigte, einfachere der beiden
erwogenen Varianten (siehe Entwurfsgespräch): kein Sonderfall für bereits
angezeigte Toasts oder WebSocket-Zustände, die sonst in der alten Sprache
stehen blieben.

## 8. Export-Vorlagen (`export.*`) — Phase C

Die rund neun Titel-/Kommentarfelder in `export/documents.py` und
`export/signals.py` (z. B. `"erzeugt von loxmatter"`, `"Bridge
erreichbar"`, `"Alle Werte neu senden"`, `f"{signal.title} Zähler"`)
wandern hinter `i18n.t("export.<zweck>", **werte)`. Da CLI und WebUI
denselben `export/*.py`-Code aufrufen (Phase-A-Untersuchung, bestätigt:
keine Duplikation), gilt für beide dieselbe, zum Exportzeitpunkt aktuell
eingestellte Sprache — für die CLI die beim Prozessstart aufgelöste
(Phase A), für die WebUI die von der Middleware (Abschnitt 4) für die
jeweilige Anfrage gelesene.

**Bestätigt (Entwurfsgespräch):** die Vorlagentexte folgen der
gemeinsamen Einstellung, nicht einer festen Sprache. Wer die
Oberflächensprache wechselt und ein Gerät danach erneut exportiert,
bekommt Titel in der jeweils anderen Sprache — nur für NEU erzeugte
Vorlagen; eine bereits in Loxone Config importierte Vorlage bleibt
unberührt (dieselbe Eigenschaft wie bei einer Änderung der
Signalauswahl, siehe README-Abschnitt zum Update-Hinweis).

Der Text der `Import-Anleitung.txt` im Export-ZIP der WebUI
(`api/export.py`, ein Prosa-Block, existiert NICHT im CLI-Pfad — die CLI
bündelt diese Datei nicht) wird als ein Schlüssel `api.export.readme_text`
mit `en`/`de`-Fassungen geführt. Ob und welche Werte darin eingesetzt
werden (Platzhalter), klärt der Umsetzungsplan anhand des tatsächlichen,
heutigen Texts — hier nicht vorweggenommen.

## 9. Tests

Ergänzt Phase As Teststrategie um die beiden neuen Oberflächen:

- **API:** wie bei der CLI (Phase A, Abschnitt 6) — bestehende Tests, die
  auf wörtlichen deutschen `detail`-Text prüfen, wechseln auf den neuen
  englischen Standardtext; deutsche Gegenstücke ergänzt über
  `i18n.set_language("de")` vor dem jeweiligen Request.
- **Middleware:** ein eigener Test, der beweist, dass ein per
  `store.locale.set_language(...)` (direkt über die Datenbank, ohne die
  API) geänderter Wert von der NÄCHSTEN Anfrage an denselben laufenden
  Testserver/dieselbe In-Prozess-ASGI-Instanz gesehen wird — das ist genau
  die Lücke, die Abschnitt 4 schließt, und verdient einen Test, der sie
  gezielt prüft, nicht nur implizit über andere Tests mitläuft.
- **`GET /api/i18n` ohne Anmeldung:** ein Test, der explizit ohne Sitzung
  und ohne Token aufruft und einen 200 statt 401 erwartet — die neue,
  bewusste dritte Ausnahme von der sonst geltenden Regel (Abschnitt 5).
- **WebUI:** kein Browser-Test-Framework in diesem Projekt bislang (siehe
  `tests/api/test_web.py`, das den zurückgegebenen HTML-/JS-Text prüft,
  nicht rendert) — der Umsetzungsplan entscheidet, ob eine Prüfung auf
  Textebene ausreicht (kein `{key}`-Rohtext im ausgelieferten `index.html`/
  `app.js`, kein hartkodierter deutscher Text mehr in `app.js`) oder ob
  Playwright o. ä. neu eingeführt wird — eine neue Abhängigkeit dafür ist
  eher NICHT im Sinne dieses Projekts, wird aber nicht hier, sondern im
  Plan entschieden.
- **Export:** bestehende Snapshot-/Inhaltstests der erzeugten XML-Dateien
  (`tests/export/`) wechseln auf englischen Standardtext, deutsche
  Gegenstücke ergänzt — wie bei Phase As `_load_fixture`-Tests.

## 10. Ausblick

Nach dieser Phase ist jeder Nutzertext im Projekt zweisprachig — CLI
(Phase A), API und WebUI (Phase B), Exportvorlagen (Phase C). Was bewusst
offen bleibt: die Middleware aus Abschnitt 4 macht `i18n.t()` zur Laufzeit
konsistent innerhalb EINES Serverprozesses; mehrere gleichzeitig
laufende `loxmatter run`-Prozesse (heute ohnehin nicht vorgesehen, siehe
Hauptdokument Nicht-Ziele: „Mehrere Miniserver an einer Bridge") blieben
unberücksichtigt — dieselbe Grenze, die für `Store`s Verbindung ohnehin
schon gilt (`model/store.py`-Moduldocstring: eine Store-Instanz gehört
genau einem Thread).
