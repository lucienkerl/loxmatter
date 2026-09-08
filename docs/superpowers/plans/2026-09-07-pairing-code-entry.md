# Pairing-Code-Eingabe: Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Einlern-Feld schreibt den Pairing-Code so, wie er auf dem Gerät steht (`1234-567-8901`), benennt was es erkannt hat, und die Brücke schneidet die Trenner vor dem Matter-Stack selbst weg.

**Architecture:** Drei reine Funktionen auf Modulebene in `web/app.js` tragen die ganze Frontend-Logik (formatieren, normalisieren, beschreiben); die Alpine-Komponente ruft sie aus zwei Ereignis-Handlern auf. Im Backend spiegelt ein `field_validator` auf `CommissionRequest.code` dieselbe Normalisierungsregel, damit sie für jeden Aufrufer der Route gilt und nicht nur für unsere Oberfläche.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / pytest · Alpine.js (kein Build-Schritt, kein JS-Testframework) · Playwright nur für die Screenshots

**Entwurf:** [`docs/superpowers/specs/2026-09-07-pairing-code-entry-design.md`](../specs/2026-09-07-pairing-code-entry-design.md)

## Global Constraints

- **Kommentare und Docstrings auf Deutsch, ohne Umlaute im Quelltext** (`Geraet`, `zurueck`) — wie im gesamten Repo. Nutzertexte in `strings.yaml` tragen dagegen echte Umlaute.
- **Alle `web.*`-Schlüssel in `en` UND `de`.** `tests/test_i18n.py::test_web_namespace_has_no_missing_english_fallback_gaps` erzwingt mindestens `en`.
- **Keine typografischen Anführungszeichen als äußerste Zeichen eines `strings.yaml`-Werts** — `tests/test_i18n.py::test_no_value_is_wrapped_in_typographic_quotes` bricht sonst.
- **Farben nur aus den vorhandenen CSS-Variablen** (`--ok`, `--warn`, `--danger`, `--off`, `--border`, `--bg`, `--text`, `--text-muted`, `--accent`). Keine neue Farbe.
- **Nach jeder Aufgabe grün:** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
- **Die Regel steht zweimal** — einmal in JS, einmal in Python. Beide Fassungen müssen zeichengleich dasselbe tun; wer eine ändert, ändert die andere.

## File Structure

| Datei | Verantwortung | Aufgabe |
|---|---|---|
| `src/loxmatter/api/models.py` | `field_validator` auf `CommissionRequest.code` — die Normalisierung für jeden Aufrufer | 1 |
| `tests/api/test_devices.py` | Nachweis, dass Trenner den Stack nicht erreichen | 1 |
| `src/loxmatter/web/app.js` (Modulebene) | Die drei reinen Funktionen: formatieren, normalisieren, beschreiben | 2 |
| `src/loxmatter/web/app.js` (in `app()`) | Zwei Ereignis-Handler und der Chip-Zustand | 3 |
| `src/loxmatter/web/index.html` | Feld, Chip, Beispielzeile, Skizze | 3, 4 |
| `src/loxmatter/i18n/strings.yaml` | Neun neue Schlüssel, einer geändert | 3, 4 |
| `src/loxmatter/web/style.css` | `.code-detect`, `.code-examples`, `.code-sticker` | 3, 4 |
| `scripts/capture_screenshots.py` | Selektor, der heute am Platzhalter hängt | 5 |
| `docs/screenshots/commissioning.png` | Das Bild dieser Karte | 5 |

---

### Task 1: Die Normalisierung im Backend

Steht zuerst, weil sie allein schon wirkt: danach kann ein von Hand abgesetzter Aufruf den abgetippten Code tragen, unabhängig von jeder Oberfläche.

**Files:**
- Modify: `src/loxmatter/api/models.py:26` (Import), `src/loxmatter/api/models.py:198-217` (`CommissionRequest`)
- Test: `tests/api/test_devices.py` (ans Ende der Einlern-Tests, nach `test_commissioning_a_device_registers_it` bei Zeile 258)

**Interfaces:**
- Consumes: nichts
- Produces: `CommissionRequest.code` ist nach der Validierung getrimmt und beim Zahlencode ziffernrein. `api/devices.py:419` reicht es unverändert weiter — dort ändert sich kein Zeichen.

- [ ] **Step 1: Die vier Tests schreiben**

An `tests/api/test_devices.py` anhängen:

```python
async def test_a_pairing_code_with_dashes_reaches_the_stack_without_them(api):
    """Der Fall, um den es geht: so steht der Code auf dem Geraet, und so
    tippt ihn jeder ab. Bis hierher schnitt die Trenner niemand weg - auch
    `MatterClient.commission_with_code` nicht, das den String unveraendert
    in den WebSocket-Befehl setzt."""
    client, _, _, fake_client = api
    response = await client.post("/api/devices/commission", json={"code": "1234-567-8901"})
    assert response.status_code == 200
    assert fake_client.commissioned == ["12345678901"]


async def test_a_qr_code_reaches_the_stack_untouched(api):
    """Der MT:-Text ist Base38-kodiert - ein Bindestrich darin traegt
    Bedeutung. Die Normalisierung muss ihn deshalb in Ruhe lassen."""
    client, _, _, fake_client = api
    response = await client.post(
        "/api/devices/commission", json={"code": " MT:Y.K90SO527JA0648G00 "}
    )
    assert response.status_code == 200
    assert fake_client.commissioned == ["MT:Y.K90SO527JA0648G00"]


async def test_spaces_inside_a_pairing_code_are_removed_as_well(api):
    """Wer aus einer Anleitung kopiert, bringt oft Leerzeichen statt
    Bindestriche mit."""
    client, _, _, fake_client = api
    response = await client.post("/api/devices/commission", json={"code": "3497 011 2332"})
    assert response.status_code == 200
    assert fake_client.commissioned == ["34970112332"]


async def test_an_overlong_code_is_passed_on_rather_than_rejected(api):
    """Der Validator normalisiert, er validiert NICHT (Entwurf Abschnitt 8):
    ueber die Bauformen der Setup-Codes entscheidet der Matter-Stack, nicht
    diese Bruecke. Ein zu langer Code geht deshalb durch und scheitert dort,
    wo er hingehoert."""
    client, _, _, fake_client = api
    response = await client.post(
        "/api/devices/commission", json={"code": "1234-567-8901-2345-678-9012"}
    )
    assert response.status_code == 200
    assert fake_client.commissioned == ["1234567890123456789012"]
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
uv run pytest tests/api/test_devices.py -k "pairing_code or qr_code_reaches or spaces_inside or overlong" -v
```

Erwartet: 4 FAILED. Der erste mit `AssertionError: assert ['1234-567-8901'] == ['12345678901']` — der Code kommt heute ungefiltert an.

- [ ] **Step 3: Den Validator schreiben**

In `src/loxmatter/api/models.py`, Zeile 26, den Import erweitern:

```python
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
```

Über `class CommissionRequest` die beiden Muster setzen:

```python
# Alles, was NICHT Ziffer, Leerraum oder Bindestrich ist, macht den Wert zu
# einem QR-Inhalt (`MT:...`, Base38). Ein Bindestrich DARIN traegt Bedeutung
# und darf nicht wegfallen - deshalb entscheidet dieses Muster zuerst, bevor
# ueberhaupt etwas geschnitten wird.
_QR_PAYLOAD = re.compile(r"[^0-9\s-]")
_MANUAL_CODE_SEPARATORS = re.compile(r"[\s-]")
```

Und in `CommissionRequest`, hinter `code: str`:

```python
    @field_validator("code")
    @classmethod
    def _strip_separators(cls, value: str) -> str:
        """Nimmt den Code so entgegen, wie er auf dem Geraet steht.

        Dort steht er gruppiert - `1234-567-8901` - und genau so tippt ihn
        jeder ab. Auf dem Weg zum Matter-Stack schneidet die Trenner sonst
        niemand weg: `api.devices` reicht den Wert unveraendert an
        `BridgeMatterClient.commission_with_code` weiter, und
        `MatterClient.commission_with_code` setzt ihn ebenso unveraendert in
        den WebSocket-Befehl (geprueft gegen die installierte Fassung,
        `matter_server/client/client.py:140`).

        Der Validator NORMALISIERT NUR, er validiert nicht (Entwurf
        Abschnitt 8): ueber die gueltigen Bauformen entscheidet der
        Matter-Stack. Laege die Regel hier, koennte diese Bruecke einen Code
        ablehnen, den der Stack angenommen haette - ohne einen Weg daran
        vorbei. Trenner zu schneiden ist verlustfrei, eine Laengenregel
        waere eine Wette.
        """
        text = value.strip()
        if _QR_PAYLOAD.search(text):
            return text
        return _MANUAL_CODE_SEPARATORS.sub("", text)
```

Zusätzlich den Klassen-Docstring ab `src/loxmatter/api/models.py:199` an die neue Lage anpassen — der erste Satz behauptet heute, der 21-stellige sei der `MT:`-Code; das sind zwei verschiedene Dinge:

```python
    """`POST /api/devices/commission` - der Pairing-Code vom Geraet oder
    seiner Verpackung (Spec 7.1). Zwei Bauformen: der Zahlencode (11-stellig,
    auf dem Geraet als `1234-567-8901` aufgedruckt, seltener 21-stellig) oder
    der Text hinter dem QR-Code (`MT:...`).

    `code` wird beim Eintreffen normalisiert, siehe `_strip_separators`.
```

- [ ] **Step 4: Tests laufen lassen, Erfolg bestätigen**

```bash
uv run pytest tests/api/test_devices.py -v
```

Erwartet: alle PASSED. Besonders die bestehenden Einlern-Tests bleiben grün — sie schicken `MT:ABC123`, einen QR-Inhalt, den der Validator nicht anfasst.

- [ ] **Step 5: Die ganze Suite und die Prüfer**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Erwartet: alles grün.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/models.py tests/api/test_devices.py
git commit -m "fix(api): Trenner aus dem Pairing-Code schneiden

Bis hierher reichte die Route den Code ungefiltert bis in den
WebSocket-Befehl von matter-server durch - wer ihn so abtippte, wie er
auf dem Geraet steht, scheiterte ohne deutbare Meldung.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Die reinen Funktionen in app.js

Drei Funktionen ohne DOM-Bezug, damit sie sich einzeln prüfen lassen. `app.js` hat **keine Nebenwirkungen auf Modulebene** (die Datei endet mit `function app() {...}`, jedes `addEventListener` steht innerhalb einer Funktion) — sie lässt sich deshalb in Node laden und die Funktionen echt durchrechnen, statt sie nur im Browser durchzuklicken.

**Files:**
- Modify: `src/loxmatter/web/app.js` — neuer Block direkt vor `const VIEWS = [...]` (Zeile 317)
- Test: Wegwerf-Prüfskript im Scratchpad, kommt nicht ins Repo

**Interfaces:**
- Consumes: nichts
- Produces:
  - `isPairingQrCode(raw: string) -> boolean`
  - `formatPairingCode(raw: string) -> string` — für die Anzeige
  - `normalizePairingCode(raw: string) -> string` — für die Übertragung
  - `describePairingCode(raw: string) -> { key: string, values: object, tone: "ok"|"warn"|"bad"|"idle" }` — `key` ist ein `strings.yaml`-Schlüssel, `values` sind seine Platzhalter; die Übersetzung geschieht erst in Aufgabe 3, damit diese Funktionen ohne geladene Sprachtabelle prüfbar bleiben

- [ ] **Step 1: Das Prüfskript schreiben**

Nach `/tmp/pairing-probe.mjs` (Wegwerf, nicht ins Repo):

```js
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync("src/loxmatter/web/app.js", "utf8");
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(
  src + "\n;globalThis.probe = { isPairingQrCode, formatPairingCode, normalizePairingCode, describePairingCode };",
  sandbox,
);
const { isPairingQrCode, formatPairingCode, normalizePairingCode, describePairingCode } = sandbox.probe;

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    console.error(`FAIL ${label}\n  erwartet ${e}\n  bekommen ${a}`);
    failures++;
  } else {
    console.log(`ok   ${label}`);
  }
}

// --- formatPairingCode: die Gruppierung 4-3-4 ---
check("leer", formatPairingCode(""), "");
check("vier Ziffern", formatPairingCode("1234"), "1234");
check("fuenfte Ziffer oeffnet die zweite Gruppe", formatPairingCode("12345"), "1234-5");
check("siebte Ziffer schliesst die zweite Gruppe", formatPairingCode("1234567"), "1234-567");
check("achte Ziffer oeffnet die dritte", formatPairingCode("12345678"), "1234-567-8");
check("elf Ziffern", formatPairingCode("12345678901"), "1234-567-8901");
check("schon gruppiert bleibt gleich", formatPairingCode("1234-567-8901"), "1234-567-8901");
check("Leerzeichen werden zu Bindestrichen", formatPairingCode("3497 011 2332"), "3497-011-2332");

// --- ab der zwoelften Ziffer wird NICHT weiter gruppiert ---
check("zwoelfte Ziffer haengt an", formatPairingCode("123456789012"), "1234-567-89012");
check(
  "einundzwanzig Ziffern",
  formatPairingCode("123456789012345678901"),
  "1234-567-89012345678901",
);
check(
  "zweiundzwanzig Ziffern werden NICHT abgeschnitten",
  formatPairingCode("1234567890123456789012"),
  "1234-567-890123456789012",
);

// --- QR-Inhalt bleibt unangetastet ---
check("MT-Code", formatPairingCode("MT:Y.K90SO527JA0648G00"), "MT:Y.K90SO527JA0648G00");
check("erstes M schaltet schon um", formatPairingCode("M"), "M");
check("MT ohne Doppelpunkt", formatPairingCode("MT"), "MT");
check("Bindestrich im QR-Inhalt bleibt", formatPairingCode("MT:A-B"), "MT:A-B");
check("isPairingQrCode bei Ziffern", isPairingQrCode("1234-567"), false);
check("isPairingQrCode beim ersten Buchstaben", isPairingQrCode("M"), true);

// --- normalizePairingCode: was uebertragen wird ---
check("Trenner raus", normalizePairingCode("1234-567-8901"), "12345678901");
check("Leerraum aussen raus", normalizePairingCode("  1234-567-8901  "), "12345678901");
check("QR getrimmt, sonst gleich", normalizePairingCode(" MT:A-B "), "MT:A-B");
check("leer bleibt leer", normalizePairingCode("   "), "");

// --- describePairingCode: der Chip ---
check("leer ist unsichtbar", describePairingCode("").tone, "idle");
check("sieben Ziffern", describePairingCode("1234567"), {
  key: "web.devices.code_detect_remaining_many",
  values: { n: 4 },
  tone: "warn",
});
check("zehn Ziffern, Einzahl", describePairingCode("1234567890"), {
  key: "web.devices.code_detect_remaining_one",
  values: {},
  tone: "warn",
});
check("elf Ziffern", describePairingCode("1234-567-8901"), {
  key: "web.devices.code_detect_manual",
  values: {},
  tone: "ok",
});
check("zwoelf Ziffern zaehlen auf 21", describePairingCode("123456789012"), {
  key: "web.devices.code_detect_remaining_many",
  values: { n: 9 },
  tone: "warn",
});
check("einundzwanzig Ziffern", describePairingCode("123456789012345678901"), {
  key: "web.devices.code_detect_manual_long",
  values: {},
  tone: "ok",
});
check("zweiundzwanzig Ziffern", describePairingCode("1234567890123456789012"), {
  key: "web.devices.code_detect_too_long",
  values: {},
  tone: "bad",
});
check("MT-Code", describePairingCode("MT:Y.K90SO527JA0648G00"), {
  key: "web.devices.code_detect_qr",
  values: {},
  tone: "ok",
});
check("Buchstabensalat", describePairingCode("hallo"), {
  key: "web.devices.code_detect_invalid",
  values: {},
  tone: "bad",
});

console.log(failures === 0 ? "\nAlles gruen." : `\n${failures} Fehlschlaege.`);
process.exit(failures === 0 ? 0 : 1);
```

- [ ] **Step 2: Prüfskript laufen lassen, Fehlschlag bestätigen**

```bash
node /tmp/pairing-probe.mjs
```

Erwartet: Abbruch mit `ReferenceError: isPairingQrCode is not defined` — die Funktionen gibt es noch nicht.

- [ ] **Step 3: Die Funktionen schreiben**

In `src/loxmatter/web/app.js`, unmittelbar **vor** `const VIEWS = ["devices", ...]` (Zeile 317) einfügen:

```js
// --- Pairing-Code (Entwurf vom 2026-09-07) ----------------------------------
//
// Auf dem Geraet steht der Zahlencode gruppiert: `1234-567-8901`. Genau so
// tippt ihn jeder ab - also nimmt ihn das Feld auch so entgegen und schreibt
// die Bindestriche beim Tippen selbst.
//
// Die Regel steht ZWEIMAL: hier und als `_strip_separators` in
// `api/models.py`. Das ist Absicht - die Oberflaeche formatiert, das Backend
// normalisiert fuer JEDEN Aufrufer der Route. Wer eine der beiden Fassungen
// aendert, aendert die andere.

// Alles ausser Ziffern, Leerraum und Bindestrich macht den Wert zu einem
// QR-Inhalt. Die Pruefung greift damit beim ersten getippten `M` von `MT:`,
// nicht erst beim Doppelpunkt: eine Regel, die auf `MT:` wartet, wuerde die
// zwei Zeichen davor als Zifferneingabe behandeln und wegwerfen.
const PAIRING_QR_PAYLOAD = /[^0-9\s-]/;

// Die belegte Schreibweise gibt es nur fuer die elf Stellen. Fuer den
// 21-stelligen Code gibt es keine - eine erfundene Gruppierung saehe anders
// aus als der Aufdruck, das Feld formatierte den Code also WEG vom Vorbild
// statt hin. Ab der zwoelften Ziffer bleibt er deshalb ungruppiert.
const PAIRING_GROUPS = [4, 7, 11];

function isPairingQrCode(raw) {
  return PAIRING_QR_PAYLOAD.test(raw);
}

function formatPairingCode(raw) {
  if (isPairingQrCode(raw)) {
    return raw;
  }
  const digits = raw.replace(/\D/g, "");
  const parts = [];
  let start = 0;
  for (const end of PAIRING_GROUPS) {
    if (digits.length <= start) {
      break;
    }
    parts.push(digits.slice(start, end));
    start = end;
  }
  // Es wird NICHTS abgeschnitten: eine Kuerzung liesse Zeichen still
  // verschwinden, und `describePairingCode`s "zu lang" waere unerreichbar -
  // eine Regel, die nie greift. Wer sich vertippt, soll das lesen koennen.
  if (digits.length > start) {
    parts.push(digits.slice(start));
  }
  return parts.join("-");
}

function normalizePairingCode(raw) {
  const text = raw.trim();
  return isPairingQrCode(text) ? text : text.replace(/\D/g, "");
}

// Was der Chip im Feld sagt. Gibt einen Schluessel statt eines Textes
// zurueck, damit diese Funktion ohne geladene Sprachtabelle prueffaehig
// bleibt - uebersetzt wird erst beim Anzeigen.
//
// Der Chip BESCHREIBT, er verbietet nicht: auch bei `bad` bleibt der
// Einlern-Knopf bedienbar und der Wert geht unveraendert an die Route.
// Dieselbe Haltung wie beim Validator im Backend - die Bruecke sagt, was sie
// sieht, und laesst den Matter-Stack entscheiden.
function describePairingCode(raw) {
  const text = raw.trim();
  if (!text) {
    return { key: "", values: {}, tone: "idle" };
  }
  if (isPairingQrCode(text)) {
    return /^MT:/i.test(text)
      ? { key: "web.devices.code_detect_qr", values: {}, tone: "ok" }
      : { key: "web.devices.code_detect_invalid", values: {}, tone: "bad" };
  }
  const count = text.replace(/\D/g, "").length;
  if (count === 11) {
    return { key: "web.devices.code_detect_manual", values: {}, tone: "ok" };
  }
  if (count === 21) {
    return { key: "web.devices.code_detect_manual_long", values: {}, tone: "ok" };
  }
  if (count > 21) {
    return { key: "web.devices.code_detect_too_long", values: {}, tone: "bad" };
  }
  // Gezaehlt wird gegen die naechste gueltige Laenge - erst 11, dann 21.
  const missing = count < 11 ? 11 - count : 21 - count;
  return missing === 1
    ? { key: "web.devices.code_detect_remaining_one", values: {}, tone: "warn" }
    : { key: "web.devices.code_detect_remaining_many", values: { n: missing }, tone: "warn" };
}
```

- [ ] **Step 4: Prüfskript laufen lassen, Erfolg bestätigen**

```bash
node /tmp/pairing-probe.mjs
```

Erwartet: jede Zeile `ok`, am Ende `Alles gruen.`, Exit-Code 0.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/web/app.js
git commit -m "feat(web): Formatier- und Erkennungsregel fuer Pairing-Codes

Drei reine Funktionen auf Modulebene - verdrahtet wird im naechsten
Schritt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Das Feld verdrahten

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml:618` (`code_placeholder` ändern, acht Schlüssel ergänzen)
- Modify: `src/loxmatter/web/app.js` — Handler in `app()`, hinter `commissionDevice()` (Zeile 1653); dazu `commissionRunCode` bei Zeile 1663
- Modify: `src/loxmatter/web/index.html:325-341` (Feld) und `:412` (Ablaufanzeige)
- Modify: `src/loxmatter/web/style.css:470` (nach `.code-field .code-input`)

**Interfaces:**
- Consumes: `formatPairingCode`, `normalizePairingCode`, `describePairingCode` aus Aufgabe 2
- Produces: `app().formatCommissionCode(input)`, `app().commissionCodeKeydown(event)`, `app().commissionCodeBadge()` — letztere gibt `{ text: string, tone: string }` mit fertig übersetztem Text

- [ ] **Step 1: Die Texte eintragen**

In `src/loxmatter/i18n/strings.yaml` den vorhandenen Eintrag bei Zeile 618 ersetzen:

```yaml
web.devices.code_placeholder:
  # Zeigt die FORM statt sie zu beschreiben. Der fruehere Klammerzusatz
  # ("11-stellig oder MT:…") nannte die Bauform - wer den Aufkleber in der
  # Hand haelt, musste das erst in "die untere der beiden Zahlen"
  # uebersetzen. Diese Auskunft tragen jetzt die Beispielzeile unter dem
  # Feld und die Aufkleber-Skizze daneben. Beide Sprachen tragen denselben
  # Wert - eine Ziffernfolge uebersetzt sich nicht.
  en: "1234-567-8901"
  de: "1234-567-8901"
```

Und direkt hinter `web.devices.code_label` (Zeile 638) einfügen:

```yaml
# Der Chip rechts im Eingabefeld (Entwurf vom 2026-09-07, Abschnitt 6). Er
# benennt, was das Feld erkannt hat - und damit lernt man den Namen des
# Codes an genau der Stelle, an der man ihn eintippt. Er BESCHREIBT nur:
# auch "zu lang" sperrt den Einlern-Knopf nicht.
web.devices.code_detect_manual:
  en: "Numeric code"
  de: "Zahlencode"
web.devices.code_detect_manual_long:
  en: "Numeric code, long"
  de: "Zahlencode, lang"
web.devices.code_detect_qr:
  en: "QR code"
  de: "QR-Code"
# Zwei Schluessel statt einer mit Plural-Regel: i18n.t kennt keine
# Pluralformen, und "noch 1 Ziffern" waere in beiden Sprachen falsch.
web.devices.code_detect_remaining_one:
  en: "1 digit to go"
  de: "noch 1 Ziffer"
web.devices.code_detect_remaining_many:
  en: "{n} digits to go"
  de: "noch {n} Ziffern"
web.devices.code_detect_too_long:
  en: "too long"
  de: "zu lang"
web.devices.code_detect_invalid:
  en: "not a valid code"
  de: "kein gültiger Code"
# Die Beispielzeile unter dem Feld. Die BESCHRIFTUNGEN darin sind dieselben
# Schluessel wie die des Chips (code_detect_manual/_qr) - es ist derselbe
# Begriff, und dass er an beiden Stellen gleich lautet, ist der Zweck der
# Uebung. Nur die beiden Beispielwerte stehen hier, und sie uebersetzen sich
# nicht.
web.devices.code_example_manual:
  en: "1234-567-8901"
  de: "1234-567-8901"
web.devices.code_example_qr:
  en: "MT:Y.K90SO527JA0648G00"
  de: "MT:Y.K90SO527JA0648G00"
```

- [ ] **Step 2: Die Handler in `app()` schreiben**

In `src/loxmatter/web/app.js`, unmittelbar **vor** `async commissionDevice()` (Zeile 1653) einfügen:

```js
    // Schreibt den Zahlencode beim Tippen so, wie er auf dem Geraet steht.
    //
    // `commissionCode` wird hier AUSDRUECKLICH nachgezogen, statt sich auf
    // x-model zu verlassen: beide haengen am selben `input`-Ereignis, und
    // welcher Zuhoerer zuerst laeuft, haengt an der Reihenfolge der
    // Attribute im Markup. Ein Zustand, der von einer Attributreihenfolge
    // abhaengt, ist ein Fehler, der erst beim Umsortieren auffaellt.
    formatCommissionCode(input) {
      const before = input.value;
      const formatted = formatPairingCode(before);
      if (formatted !== before) {
        // Ziffern LINKS vom Cursor zaehlen, nicht Zeichenpositionen: sonst
        // verschoebe jeder neu gesetzte Bindestrich den Cursor um eins.
        const caret = input.selectionStart ?? before.length;
        const digitsLeft = before.slice(0, caret).replace(/\D/g, "").length;
        input.value = formatted;
        let seen = 0;
        let position = 0;
        while (position < formatted.length && seen < digitsLeft) {
          if (/\d/.test(formatted[position])) {
            seen += 1;
          }
          position += 1;
        }
        input.setSelectionRange(position, position);
      }
      this.commissionCode = input.value;
    },

    // Rueckschritt DIREKT hinter einem Bindestrich loescht die Ziffer davor.
    //
    // Ohne diesen Zweig loescht der Tastendruck den Trenner, den
    // `formatCommissionCode` unmittelbar danach wieder setzt: der Wert
    // aendert sich nicht, der Cursor bleibt stehen, und die Taste wirkt tot.
    // Das ist der eine Punkt, an dem eine mitformatierende Eingabe
    // ueblicherweise scheitert.
    commissionCodeKeydown(event) {
      if (event.key !== "Backspace") {
        return;
      }
      const input = event.target;
      if (input.selectionStart !== input.selectionEnd) {
        return;
      }
      const caret = input.selectionStart;
      if (caret < 2 || input.value[caret - 1] !== "-") {
        return;
      }
      event.preventDefault();
      input.value = input.value.slice(0, caret - 2) + input.value.slice(caret);
      input.setSelectionRange(caret - 2, caret - 2);
      this.formatCommissionCode(input);
    },

    // Text und Farbe des Chips im Feld.
    commissionCodeBadge() {
      const state = describePairingCode(this.commissionCode);
      return {
        text: state.key ? t(state.key, state.values) : "",
        tone: state.tone,
      };
    },
```

- [ ] **Step 3: Den übertragenen Wert normalisieren**

In `commissionDevice()` die drei Stellen ersetzen, die heute `this.commissionCode.trim()` benutzen (Zeilen 1655, 1663, 1665):

```js
    async commissionDevice() {
      this.commissionMessage = null;
      // Normalisiert, nicht nur getrimmt: die Trenner, die das Feld beim
      // Tippen selbst gesetzt hat, gehoeren nicht in den Matter-Stack. Das
      // Backend schneidet sie ohnehin ein zweites Mal weg
      // (`CommissionRequest._strip_separators`) - hier stehen sie draussen,
      // damit die Oberflaeche nicht etwas anderes abschickt, als sie zeigt.
      const code = normalizePairingCode(this.commissionCode);
      if (!code) {
        this.commissionMessage = t("web.devices.commission_code_required");
        this.commissionMessageIsError = true;
        return;
      }
      this.commissionBusy = true;
      this.commissionStep = 0;
      this.commissionFailed = false;
      // Die Ablaufanzeige zeigt den FORMATIERTEN Code, nicht den
      // uebertragenen: wer zwanzig bis sechzig Sekunden wartet, soll den
      // Code wiedererkennen, den er eingetippt hat.
      this.commissionRunCode = formatPairingCode(this.commissionCode.trim());
      try {
        const body = { code };
```

Der Rest von `commissionDevice()` bleibt unverändert.

- [ ] **Step 4: Das Markup verdrahten**

In `src/loxmatter/web/index.html` den Block ab Zeile 325 ersetzen:

```html
            <div class="code-field">
              <input
                type="text"
                id="commission-code"
                class="code-input"
                spellcheck="false"
                autocomplete="off"
                x-ref="commissionCode"
                x-model="commissionCode"
                :placeholder="t('web.devices.code_placeholder')"
                @input="formatCommissionCode($event.target)"
                @keydown="commissionCodeKeydown($event)"
                @keydown.enter="commissionDevice()"
              />
              <!--
                Kein `inputmode="numeric"`: auf dem Telefon gaebe es die
                Zifferntastatur, und dort steht man beim Ablesen. Es sperrte
                aber das Tippen eines QR-Textes hinter eine Umschalttaste,
                und diese Karte hat fuer beide Bauformen genau ein Feld.

                Der Chip bleibt bei leerem Feld sichtbar-aber-leer
                (`visibility: hidden` in style.css) statt entfernt, damit das
                Feld beim ersten Tastendruck nicht seine Breite aendert.
              -->
              <span
                class="code-detect"
                :class="'tone-' + commissionCodeBadge().tone"
                x-text="commissionCodeBadge().text"
                aria-live="polite"
              ></span>
              <button
                class="primary"
                @click="commissionDevice()"
                :disabled="commissionBusy"
                x-text="t('web.devices.commission_submit')"
              ></button>
          </div>
          <p class="code-examples">
            <span><b x-text="t('web.devices.code_detect_manual')"></b>
              <code x-text="t('web.devices.code_example_manual')"></code></span>
            <span><b x-text="t('web.devices.code_detect_qr')"></b>
              <code x-text="t('web.devices.code_example_qr')"></code></span>
          </p>
```

- [ ] **Step 5: Die Gestalt**

In `src/loxmatter/web/style.css` hinter `.code-field .code-input:focus` (Zeile 488) einfügen:

```css
/* Der Chip im Eingabefeld (Entwurf vom 2026-09-07). Er benennt, was das Feld
 * erkannt hat. Bei leerem Feld bleibt er stehen und wird nur unsichtbar -
 * entfernt aenderte er die Breite des Feldes beim ersten Tastendruck.
 *
 * Die drei Farben tragen dieselbe Bedeutung wie ueberall sonst in dieser
 * Oberflaeche (--ok gelungen, --warn unfertig, --danger falsch); es kommt
 * keine neue hinzu. */
.code-field .code-detect {
  display: inline-flex;
  align-items: center;
  align-self: center;
  flex-shrink: 0;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  white-space: nowrap;
}

.code-field .code-detect.tone-idle {
  visibility: hidden;
}

.code-field .code-detect.tone-ok {
  background: var(--ok-bg);
  color: var(--ok);
}

.code-field .code-detect.tone-warn {
  background: var(--warn-bg);
  color: var(--warn);
}

.code-field .code-detect.tone-bad {
  background: var(--danger-bg);
  color: var(--danger);
}

/* Die beiden Bauformen als Beispiel. Ersetzt den frueheren Klammerzusatz im
 * Platzhalter: der nannte die Bauform, das hier zeigt sie. */
.code-examples {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem 1.1rem;
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  color: var(--text-muted);
}

.code-examples b {
  font-weight: 600;
}

.code-examples code {
  font-family: var(--mono);
  font-size: 0.95em;
  color: var(--text);
}
```

**An der `@media (max-width: 480px)`-Regel bei Zeile 501 ist nichts zu tun.** Sie gibt dem Feld dort `flex: 1 1 100%` und dem Knopf `width: 100%`; der Chip fällt damit von selbst in eine eigene Zeile zwischen beide. Ein `order` einzuführen wäre nicht nur überflüssig, es schöbe ihn hinter den Knopf. Nachgesehen wird das trotzdem — Schritt 8, Punkt 8.

- [ ] **Step 6: Die Sprachtabelle prüfen**

```bash
uv run pytest tests/test_i18n.py -v
```

Erwartet: alle PASSED — besonders `test_web_namespace_has_no_missing_english_fallback_gaps` und `test_no_value_is_wrapped_in_typographic_quotes`.

- [ ] **Step 7: Die reinen Funktionen erneut prüfen**

```bash
node /tmp/pairing-probe.mjs
```

Erwartet: `Alles gruen.` — der Verdrahtungsschritt darf sie nicht verändert haben.

- [ ] **Step 8: Die Bindung im Browser ansehen**

Das Prüfskript belegt die Regel, nicht die Bindung. Dafür die Anwendung starten:

```bash
uv run python scripts/dev_web_server.py --demo
```

Auf der Geräteansicht durchspielen und **jeweils hinsehen**, statt es anzunehmen:

1. `12345678901` Ziffer für Ziffer tippen — die Bindestriche erscheinen bei der 5. und der 8. Ziffer, der Cursor bleibt hinter der zuletzt getippten Ziffer.
2. Cursor zwischen `1234-` und `567` setzen, eine `9` tippen — sie landet an der Cursorstelle, nicht am Ende.
3. Cursor direkt hinter einen Bindestrich setzen, Rückschritt — die Ziffer davor verschwindet, der Trenner rutscht nach.
4. `1234-567-8901` einfügen — Feld zeigt es unverändert, Chip sagt „Zahlencode".
5. `MT:Y.K90SO527JA0648G00` einfügen — unverändert, Chip sagt „QR-Code".
6. Feld leeren — der Chip verschwindet, das Feld ändert seine Breite nicht.
7. Auf Deutsch umschalten — der Chip wechselt mit.
8. Fenster auf 400px verschmälern — Feld, Chip und Knopf stehen untereinander.

- [ ] **Step 9: Die ganze Suite und die Prüfer**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 10: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml
git commit -m "feat(web): Pairing-Code mitformatieren und benennen

Das Feld schreibt den Zahlencode als 1234-567-8901, wie er auf dem Geraet
steht, und sagt rechts im Feld, was es erkannt hat. Der Klammerzusatz im
Platzhalter weicht zwei Beispielen darunter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Die Aufkleber-Skizze

**Files:**
- Modify: `src/loxmatter/web/index.html` (Skizze neben das Feld, um beides ein `.code-with-sticker`)
- Modify: `src/loxmatter/web/style.css` (`.code-with-sticker`, `.code-sticker`)
- Modify: `src/loxmatter/i18n/strings.yaml` (`sticker_alt`, `code_where_hint`)

**Interfaces:**
- Consumes: die Feldstruktur aus Aufgabe 3
- Produces: nichts, worauf spätere Aufgaben zugreifen

- [ ] **Step 1: Die beiden Texte eintragen**

Hinter `web.devices.code_example_qr` in `src/loxmatter/i18n/strings.yaml`:

```yaml
# Die Aufkleber-Skizze neben dem Feld (Entwurf Abschnitt 7). Sie ist eine
# Verdeutlichung, keine Informationsquelle - die Beispielzeile darueber
# traegt denselben Inhalt als Text. Das aria-label beschreibt deshalb, was
# zu SEHEN ist, statt den Inhalt ein zweites Mal vorzulesen.
web.devices.sticker_alt:
  en: "Sketch of a Matter label: the QR code on the left, the numeric code highlighted below it on the right."
  de: "Skizze eines Matter-Aufklebers: links der QR-Code, rechts darunter der hervorgehobene Zahlencode."
web.devices.code_where_hint:
  en: "Printed on the device, its packaging, or in the manual. The text behind the QR code works too."
  de: "Steht auf dem Gerät, seiner Verpackung oder im Handbuch. Statt der Zahl geht auch der Text hinter dem QR-Code."
```

- [ ] **Step 2: Die Skizze ins Markup**

In `src/loxmatter/web/index.html` das in Aufgabe 3 entstandene `<div class="code-field">…</div>` samt der Beispielzeile in einen Rahmen setzen und die Skizze daneben stellen. Aus

```html
            <label class="field-label" for="commission-code" …></label>
            <div class="code-field"> … </div>
            <p class="code-examples"> … </p>
```

wird der folgende Block. Er zeigt das Markup vollständig, damit er sich ohne Rückgriff auf Aufgabe 3 anwenden lässt — **die beiden HTML-Kommentare aus Aufgabe 3 (zum fehlenden `inputmode` und zum stehenbleibenden Chip) wandern unverändert mit**, sie sind hier nur der Länge wegen nicht wiederholt:

```html
            <div class="code-with-sticker">
              <div class="code-col">
                <label class="field-label" for="commission-code"
                       x-text="t('web.devices.code_label')"></label>
                <div class="code-field">
                  <input
                    type="text"
                    id="commission-code"
                    class="code-input"
                    spellcheck="false"
                    autocomplete="off"
                    x-ref="commissionCode"
                    x-model="commissionCode"
                    :placeholder="t('web.devices.code_placeholder')"
                    @input="formatCommissionCode($event.target)"
                    @keydown="commissionCodeKeydown($event)"
                    @keydown.enter="commissionDevice()"
                  />
                  <span
                    class="code-detect"
                    :class="'tone-' + commissionCodeBadge().tone"
                    x-text="commissionCodeBadge().text"
                    aria-live="polite"
                  ></span>
                  <button
                    class="primary"
                    @click="commissionDevice()"
                    :disabled="commissionBusy"
                    x-text="t('web.devices.commission_submit')"
                  ></button>
                </div>
                <p class="code-examples">
                  <span><b x-text="t('web.devices.code_detect_manual')"></b>
                    <code x-text="t('web.devices.code_example_manual')"></code></span>
                  <span><b x-text="t('web.devices.code_detect_qr')"></b>
                    <code x-text="t('web.devices.code_example_qr')"></code></span>
                </p>
                <p class="hint" x-text="t('web.devices.code_where_hint')"></p>
              </div>
              <!--
                Das QR-Quadrat ist eine Andeutung aus Rechtecken - drei
                Suchmuster plus Rauschen -, kein lesbarer Code. Ein echter QR
                im Bild waere eine Einladung, ihn zu scannen, und fuehrte
                nirgendwohin.

                Alle Farben kommen aus den vorhandenen Variablen, damit die
                Skizze in beiden Themes traegt; --warn hebt die Zahl hervor
                und traegt damit dieselbe Bedeutung wie ueberall sonst
                ("sieh hier hin"), statt eine neue einzufuehren.
              -->
              <svg class="code-sticker" width="188" height="112"
                   viewBox="0 0 188 112" role="img"
                   :aria-label="t('web.devices.sticker_alt')">
                <rect x="1" y="1" width="186" height="110" rx="6"
                      fill="var(--bg)" stroke="var(--border)" stroke-width="1" />
                <g fill="var(--text)">
                  <rect x="16" y="16" width="22" height="22" rx="1" />
                  <rect x="20" y="20" width="14" height="14" rx="1" fill="var(--bg)" />
                  <rect x="24" y="24" width="6" height="6" />
                  <rect x="62" y="16" width="22" height="22" rx="1" />
                  <rect x="66" y="20" width="14" height="14" rx="1" fill="var(--bg)" />
                  <rect x="70" y="24" width="6" height="6" />
                  <rect x="16" y="62" width="22" height="22" rx="1" />
                  <rect x="20" y="66" width="14" height="14" rx="1" fill="var(--bg)" />
                  <rect x="24" y="70" width="6" height="6" />
                  <rect x="46" y="18" width="4" height="4" /><rect x="54" y="26" width="4" height="4" />
                  <rect x="46" y="34" width="4" height="4" /><rect x="46" y="46" width="4" height="4" />
                  <rect x="62" y="46" width="4" height="4" /><rect x="70" y="54" width="4" height="4" />
                  <rect x="78" y="46" width="4" height="4" /><rect x="54" y="54" width="4" height="4" />
                  <rect x="62" y="62" width="4" height="4" /><rect x="78" y="70" width="4" height="4" />
                  <rect x="70" y="78" width="4" height="4" /><rect x="46" y="70" width="4" height="4" />
                  <rect x="16" y="46" width="4" height="4" /><rect x="30" y="46" width="4" height="4" />
                  <rect x="62" y="78" width="4" height="4" /><rect x="54" y="70" width="4" height="4" />
                </g>
                <rect x="98" y="52" width="76" height="20" rx="4"
                      fill="var(--warn-bg)" stroke="var(--warn)" stroke-width="1" />
                <text x="104" y="66" font-family="var(--mono)" font-size="11"
                      fill="var(--text)">1234-567-8901</text>
                <text x="98" y="44" font-size="8.5" letter-spacing="0.6"
                      fill="var(--text-muted)">MATTER</text>
              </svg>
            </div>
```

- [ ] **Step 3: Die Gestalt**

In `src/loxmatter/web/style.css` vor `.code-field` (Zeile 450) einfügen:

```css
/* Feld und Aufkleber-Skizze nebeneinander (Entwurf vom 2026-09-07,
 * Abschnitt 7). Die Skizze steht dauerhaft da: sie kostet keinen
 * Lesevorgang, wer sie nicht braucht ueberliest sie, und sie ersetzt den
 * frueheren Klammerzusatz im Platzhalter - der Text wird also nicht mehr,
 * sondern weniger. */
.code-with-sticker {
  display: flex;
  gap: 1.25rem;
  align-items: flex-start;
}

.code-with-sticker .code-col {
  flex: 1 1 auto;
  min-width: 0;
}

.code-sticker {
  flex: 0 0 auto;
}

/* Unter dieser Breite wandert die Skizze unter das Feld - dieselbe Schwelle
 * wie beim Umbruch von Feld und Knopf weiter unten, und aus demselben
 * Grund: dort steht man mit dem Telefon vor dem Geraet. */
@media (max-width: 480px) {
  .code-with-sticker {
    flex-wrap: wrap;
    gap: 0.75rem;
  }
}
```

- [ ] **Step 4: Sprachtabelle prüfen**

```bash
uv run pytest tests/test_i18n.py -v
```

Erwartet: alle PASSED.

- [ ] **Step 5: Im Browser ansehen**

```bash
uv run python scripts/dev_web_server.py --demo
```

Prüfen, jeweils mit Hinsehen:

1. Die Skizze steht rechts vom Feld, oben bündig mit der Beschriftung.
2. Im Dunkelmodus (Systemeinstellung umschalten) sind Rand, QR-Muster und Zahl lesbar und die Hervorhebung sichtbar.
3. Auf 400px Breite steht die Skizze unter dem Feld, nichts läuft waagerecht davon.
4. Die Zahl in der Skizze steht in Monospace — trägt `font-family="var(--mono)"` im SVG nicht, stattdessen `font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"` schreiben (SVG-Attribute lösen CSS-Variablen nicht überall auf; **diesen Punkt tatsächlich ansehen**, nicht annehmen).

- [ ] **Step 6: Die ganze Suite und die Prüfer**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml
git commit -m "feat(web): Aufkleber-Skizze neben das Pairing-Code-Feld

Zeigt, wo auf dem Geraet die Zahl steht, die hier hingehoert - wer den
Aufkleber in der Hand haelt, muss keinen Satz lesen, um die richtige der
beiden Zahlen zu finden.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Screenshot-Skript und Bild

Das Skript wählt das Eingabefeld heute über seinen Platzhaltertext aus — **dieser Selektor bricht durch Aufgabe 3**, weil dort genau dieser Text ersetzt wird.

**Files:**
- Modify: `scripts/capture_screenshots.py:275`
- Replace: `docs/screenshots/commissioning.png`

**Interfaces:**
- Consumes: `id="commission-code"` aus `index.html` (steht dort schon seit dem Umbau „Code zuerst")
- Produces: nichts

- [ ] **Step 1: Den Selektor lösen**

In `scripts/capture_screenshots.py` Zeile 275 ersetzen:

```python
    # Der Selektor haengt am `id`, nicht mehr am Platzhaltertext: der lautete
    # frueher "Pairing-Code (11-stellig oder MT:…)" und ist seit dem Entwurf
    # vom 2026-09-07 die Ziffernfolge selbst - `input[placeholder*="MT:"]`
    # fand danach nichts mehr. Ein `id` aendert sich seltener als ein Text,
    # der uebersetzt wird.
    #
    # Eingesetzt wird jetzt der ZAHLENCODE statt eines MT:-Codes: er ist die
    # Bauform, die das Bild erklaeren soll, und nur an ihm sind Gruppierung
    # und Chip ueberhaupt zu sehen. `fill()` loest das `input`-Ereignis aus,
    # an dem `formatCommissionCode` haengt - im Bild steht die Zahl deshalb
    # gruppiert, so wie nach dem Tippen.
    page.fill("#commission-code", "34970112332")
```

- [ ] **Step 2: Bilder neu aufnehmen**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

- [ ] **Step 3: Den Diff prüfen**

```bash
git status --short docs/screenshots/
```

Erwartet: `commissioning.png` geändert, `system.png` geändert (das ist laut Kopfkommentar des Skripts **nicht** reproduzierbar — es zeigt das Protokoll des Laufs selbst). **Die übrigen fünf müssen unverändert sein.** Sind sie es nicht, hat diese Änderung etwas berührt, das sie nicht berühren sollte — dann nachsehen, nicht wegwinken.

`system.png` verwerfen:

```bash
git checkout docs/screenshots/system.png
```

- [ ] **Step 4: Das neue Bild ansehen**

`docs/screenshots/commissioning.png` öffnen und prüfen: der Code steht als `3497-011-2332` im Feld, der Chip daneben sagt „Numeric code" (die Bilder entstehen auf Englisch), und die Aufkleber-Skizze ist im Bild.

- [ ] **Step 5: Commit**

```bash
git add scripts/capture_screenshots.py docs/screenshots/commissioning.png
git commit -m "docs(screenshots): Einlern-Karte mit Codeformat neu aufnehmen

Der Selektor haengt nicht mehr am Platzhaltertext - der ist jetzt die
Ziffernfolge selbst, und input[placeholder*=\"MT:\"] fand nichts mehr.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Abschluss

Nach Aufgabe 5 einmal vollständig:

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy && node /tmp/pairing-probe.mjs
```

Dann `superpowers:finishing-a-development-branch` für den Weg zurück nach `main`.

Das Prüfskript unter `/tmp` ist Wegwerf und kommt **nicht** ins Repo — es gibt hier kein JS-Testframework, und ein einzelnes Node-Skript, das in keiner Pipeline läuft, wäre toter Ballast. Wer die Regel später ändert, schreibt es aus Aufgabe 2, Schritt 1 neu.
