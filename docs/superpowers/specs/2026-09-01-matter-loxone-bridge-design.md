# Matter → Loxone Bridge — Design

**Datum:** 2026-09-01
**Status:** Design abgestimmt, bereit für Implementierungsplanung

---

## 1. Ziel

Ein selbst gehosteter Container-Dienst, der Matter-Geräte (over Thread und WiFi) an einen
Loxone Miniserver anbindet. Geräte werden über eine WebUI eingelernt; sämtliche Werte,
die ein Gerät liefert — inklusive Events wie Tastendrücke —, werden an Loxone
weitergereicht. Die Loxone-seitigen Objekte entstehen per generierter Vorlagendatei,
nicht per Handarbeit.

Zielgruppe: Open Source, fremde Installationen. Es darf keine Annahme über Miniserver-
Generation, Loxone-Config-Version oder Gerätebestand getroffen werden.

## 2. Nicht-Ziele in v1

- Patchen der Loxone-Config-Projektdatei (siehe 3.2 — als optionales Modul später)
- Matter-OTA-Updates der Geräte
- Mehrere Miniserver an einer Bridge
- Richtung Loxone → Matter für Szenen/Gruppen (nur Einzelgeräte-Kommandos)
- Loxone als Matter-Gerät exponieren
- Szenen, Zeitpläne oder Automatisierung in der WebUI (siehe 8.2)

---

## 3. Entscheidungen

### 3.1 Loxone-Transport: virtuelle UDP-Eingänge + virtuelle HTTP-Ausgänge

Gewählt gegenüber zwei Alternativen:

**MQTT (nativer Loxone-Client) — verworfen.** Miniserver Gen 1 wird nicht unterstützt,
und die Grenzen sind zu eng: max. 16 Subscriptions und 16 Publish-Ein-/Ausgänge,
Auswertung von Wertänderungen höchstens alle 2 Sekunden. Für einige Dutzend Geräte mit
je mehreren Attributen unbrauchbar.
Quelle: <https://www.loxone.com/enen/kb/mqtt/>

**Modbus TCP — verworfen.** Loxone Config hat zwar einen nativen Modbus-TCP-Treiber,
aber Loxone ist Master und pollt (Latenz), und Matter-Semantik (Farbe, Events, Strings)
lässt sich nicht sinnvoll auf ein 16-Bit-Registermodell abbilden. Für Zähler und
Wechselrichter richtig, für Leuchten und Taster ein Rückschritt.

**Gewählt: UDP-Eingang (push, niedrige Latenz) + HTTP-Ausgang (Kommandos).**
Funktioniert auf allen Miniserver-Generationen, ist dokumentiert und stabil.

### 3.2 Loxone-Import: Vorlagendateien, kein direktes Schreiben

Der ursprünglich gewünschte Weg — Tool verbindet sich mit dem Miniserver und legt die
IOs selbst an — ist **nicht möglich**. Die Miniserver-API (`/dev/sps/io/<name>/<wert>`,
`LoxAPP3.json`) kann Werte auf *existierenden* IOs setzen und die Struktur lesen, aber
keine IO-Objekte anlegen. Das Programm wird von Loxone Config kompiliert und als Binär
hochgeladen; das Anlegen von IOs ist eine Compiler-Funktion von Config.

Den Upload-Weg nachzubauen hieße, Compiler und proprietäres Upload-Protokoll zu
reverse-engineeren — für ein Tool, das in fremden Häusern läuft, disqualifizierend.

**Projektdatei-Patching** (die Config-Projektdatei ist XML-basiert, „LoxPLAN") bleibt als
späteres, optionales Modul offen. Es ist nicht XML-valide (doppelte Attribute), braucht
einen toleranten Parser und ist versionsabhängig. Der Gewinn gegenüber dem Vorlagen-Weg
ist zudem gering: beide erfordern null Aufwand *pro Gerät*, und das Verdrahten der IOs
auf Funktionsbausteine bleibt in beiden Fällen Handarbeit.
Quelle: <https://loxwiki.atlassian.net/wiki/spaces/LOX/pages/1852243969>

### 3.3 Stack: Python durchgehend

`python-matter-server` (basiert auf dem offiziellen CHIP-SDK) als Matter-Engine,
FastAPI-Backend, schlanke SPA. Begründung: ausgereifteste Controller-Implementierung
außerhalb von Home Assistant, liefert per WebSocket genau das benötigte Modell —
vollständiger Attributbaum plus Events über Subscriptions. Eine Sprache im Projekt.

`matter.js` (TypeScript) wurde erwogen: ein Prozess statt zwei, geteilte Typen zwischen
Backend und Frontend. Verworfen, weil die Controller-Seite weniger erprobt ist und
Commissioning und Thread deutlich mehr Low-Level-Arbeit erfordern.

### 3.4 Thread: eigener OTBR im Stack

Der Container-Stack liefert einen eigenen OpenThread Border Router mit (USB-Funkmodul,
z. B. ZBT-1 / nRF52840). Vollständig eigenständig, kein Fremd-Border-Router nötig,
keine manuelle Beschaffung von Thread-Credentials. Preis: USB-Passthrough, Host-
Networking und IPv6/radvd im Deployment.

### 3.5 Abbildung: generisch statt kuratiert

Kein Profil pro Gerätetyp. Stattdessen wird der Endpoint-/Cluster-/Attribut-Baum des
Geräts ausgelesen und **jedes lesbare Attribut und jedes Event** zu einem Loxone-Signal.
Eine YAML-Tabelle reichert bekannte Cluster an (Kurzname, Skalierung, Einheit);
unbekannte Cluster werden trotzdem roh exportiert.

Konsequenz: neue Geräte funktionieren am Tag null, nur mit hässlicheren Namen. Die
Tabelle ist eine Anreicherungsschicht, kein Gatekeeper.

**Validierung (Phase 1, 2026-09-01).** Geprüft an 2 realen IKEA-Geräten am
laufenden matter-server (`ws://10.0.1.56:5580/ws`): Node 3 „IKEA of Sweden
GRILLPLATS Plug" (messende Steckdose, Cluster 144 ElectricalPowerMeasurement,
145 ElectricalEnergyMeasurement) und Node 4 „IKEA of Sweden BILRESA dual
button" (zweikanaliger Taster, Switch-Cluster 59 auf Endpoint 1 und 2).
Aufgenommene Abbilder liegen unter `tests/fixtures/nodes/`.

Was trägt: Bei beiden Geräten war jeder Attributpfad parsebar
(`find_unparsable_paths` leer), und kein vom Gerät in seiner `AttributeList`
gelistetes Attribut fehlte im gelieferten Snapshot (`find_unreported_attributes`
leer). Unbekannte Cluster wurden unverändert mitextrahiert. Für Attribute trägt
die generische Zerlegung damit uneingeschränkt.

Was nicht trägt, und warum das ein echter Befund ist statt einer Randnotiz:
**keins der beiden Geräte führt die `EventList` (0xFFFA)**. Beim Taster fehlt
sie schlicht in der `AttributeList` des Switch-Clusters (`1/59/65531` =
`[0, 1, 2, 65528, 65529, 65531, 65532, 65533]` — 65530 ist nicht dabei); das
globale Attribut ist im Matter-Standard optional, und IKEA implementiert es
nicht. Ein Gerät, das nachweislich Tastendrücke sendet, lieferte über die
reine EventList-Ableitung **null** Events. Attribut-Zerlegung bleibt
generisch — sie braucht kein Cluster-Wissen und übersieht nichts.
**Event-Zerlegung kann das nicht mehr uneingeschränkt sein**: welche Events
ein Cluster erzeugt, muss aus der FeatureMap abgeleitet werden, und diese
Ableitung ist zwangsläufig Cluster-spezifisches Wissen (Korrektur in 6.3,
umgesetzt in `discovery.FEATURE_MAP_EVENTS`). Das ist eine Grenze der
Aussage „generisch statt kuratiert" oben, kein Detail am Rand.

Zweiter Befund derselben Aufnahme: **45 der 159 extrahierten Attributsignale
der Steckdose sind nicht skalar** — Listen, Structs oder Strings, z. B.
`0/29/1 = [29, 31, 40, ...]`, `0/31/0 = [{...}]`, `0/40/1 = 'IKEA of Sweden'`.
Die generische Zerlegung übersieht davon nichts — sie liefert alle 159 als
Signal —, aber gut ein Viertel des Gefundenen (28,3 %) lässt sich nicht 1:1
auf einen virtuellen UDP-Eingang abbilden, der nur Zahlen und digitale Werte
kennt (für Strings gibt es immerhin einen virtuellen Text-Eingang; für Listen
und Structs nichts). Konsequenz für den Exporter (6.6) und die WebUI (8).

---

## 4. Systemarchitektur

### 4.1 Container-Stack

```
docker compose
├── otbr            OpenThread Border Router
│                   network_mode: host, USB-Dongle, IPv6 + radvd
├── matter-server   python-matter-server (CHIP-SDK)
│                   network_mode: host (mDNS + IPv6 zwingend)
│                   Volume: Fabric-Credentials
└── loxmatter       FastAPI + WebUI + SQLite
                    Bridge-Networking, Port 8080/tcp

Profil "dev" ergänzt (siehe 10.2)
├── virtual-devices  CHIP-Beispielgeräte, echt einlernbar
└── fake-miniserver  UDP-Mitschnitt + Kommando-Sender statt Loxone
```

Nur `otbr` und `matter-server` benötigen Host-Networking. `loxmatter` spricht
WebSocket nach innen, UDP und HTTP nach Loxone.

**Kritisch:** Das Volume mit den Fabric-Credentials von `matter-server` ist der einzige
unersetzliche Zustand. Verlust bedeutet, alle Geräte neu einlernen zu müssen. Muss im
Deployment-Guide und in der WebUI prominent stehen; die WebUI bietet einen Backup-Export.

**Sicherheitsstatus dieses Backup-Exports (geschützt seit Task 8, 2026-09-02 — siehe
9.1 für die vollständige Entscheidung):** `GET /api/diagnostics/fabric-backup` (10.5)
verlangt seit Task 8 `Authorization: Bearer <Token>`, sobald `--api-token`/
`LOXMATTER_API_TOKEN` gesetzt ist (`loxone.server.build_api_guard`). `loxmatter` läuft
mit `network_mode: host` (siehe oben), damit der Miniserver ihn erreicht — dieselbe
Erreichbarkeit gilt fürs gesamte LAN, deshalb ist ein gesetztes Token hier keine
Option, sondern die Voraussetzung für einen sicheren Betrieb: ohne Token bleibt die
Route unverändert offen (mit einer Warnung im Log beim Start). Der Read-only-Mount
des matter-server-Datenverzeichnisses (`./data:/matter-data:ro`) und die zugehörige
`--matter-data-dir`-Option in `deploy/testhost/docker-compose.yml` sind seit Task 8
wieder aktiv, zusammen mit einem in `.env` gesetzten `LOXMATTER_API_TOKEN` — ohne
diese Einhängung lieferte die Route ohnehin nur einen 503, statt echte Schlüssel
auszuliefern.

### 4.2 Module in `loxmatter`

| Modul | Aufgabe | Abhängigkeiten |
|---|---|---|
| `matter/` | WS-Client zu matter-server: Commissioning, Subscriptions, Kommandos. Normalisiert auf `Node → Endpoint → Cluster → Attribut/Event` | matter-server |
| `model/` | SQLite: Geräte, Signale, Mappings, Export-Zustand | — |
| `profiles/` | YAML-Tabellen: Cluster/Attribut → Kurzname, Skalierung, Einheit, Loxone-Typ | — |
| `loxone/out` | UDP-Sender: Entprellung, Impulse, Full-Resend, Rate-Limit | model, profiles |
| `commands/` | Übersetzt „gewünschter Zustand → Matter-Kommando": Level-Skalierung, Farbraum, Cover-Position, Setpoints. **Von `loxone/in` und `web/` gemeinsam genutzt** | matter, model, profiles |
| `loxone/in` | HTTP-Endpoints für virtuelle Ausgänge; delegiert an `commands/` | commands |
| `export/` | Generiert `VIU_*.xml` und `VO_*.xml` | model, profiles |
| `web/` | SPA, spricht ausschließlich REST gegen das Backend | — |

`commands/` existiert genau deshalb als eigenes Modul: Loxone-HTTP-Ausgang und WebUI-Bedienung
sind zwei Aufrufer derselben Logik. Läge sie in `loxone/in`, gäbe es die Farbraum- und
Level-Umrechnung zweimal — mit garantiert divergierendem Verhalten.

Jedes Modul ist ohne die anderen testbar. `matter/` und `loxone/*` sind die einzigen
Module mit I/O nach außen.

### 4.3 Datenfluss

```
Sensor:   Matter-Gerät ──Subscription──► matter-server ──WS──► loxmatter
                                          Mapping + Skalierung  │
                                                                ▼
                                UDP "d12_1_temp:21.5" ──► Miniserver (virt. UDP-Eingang)

Aktor:    Loxone-Baustein ──► virt. Ausgang ──HTTP GET──► loxmatter
                                /cmd/d12_1_level/85              │
                                                        Matter-Kommando
                                                                 ▼
                                              matter-server ──► Matter-Gerät
```

---

## 5. Datenmodell

```
Device
  id            int, fortlaufend, stabil
  node_id       Matter Node ID
  unique_id     Matter Unique ID (überlebt Neuvergabe der Node ID)
  vendor, product, label
  online        bool

Signal
  id            int
  device_id     → Device
  endpoint      int
  cluster       int
  kind          'attribute' | 'event'
  element       int (Attribute ID oder Event ID)
  key           str, UNIQUE, unveränderlich  ← die Loxone-Verdrahtung
  title         str, frei änderbar
  analog        bool
  scale         float
  unit          str
  exported      bool
```

Das Gerät trägt zusätzlich die Loxone-Export-Metadaten:

```
Device (Fortsetzung)
  udp_port      int, Default aus globaler Einstellung (7000)
  exported_at   nullable — wann zuletzt eine Vorlage erzeugt wurde
```

`key` ist der einzige Wert, der niemals geändert wird. `title` darf jederzeit geändert
werden und wirkt sich nur auf den nächsten Export aus.

---

## 6. Loxone-Integration

### 6.1 Verifiziertes Vorlagen-Schema

Gemessen an 26 Vorlagen aus einer echten Loxone-Config-Installation — bereinigte Auszüge
liegen unter `tests/fixtures/loxone/VIU_Referenz.xml` und `VO_Referenz.xml`,
`loxmatter.export.documents` baut dieses Schema nach:

```xml
<?xml version="1.0" encoding="utf-8"?>
<VirtualInUdp Title="Matter — Wohnzimmerlampe" Comment="erzeugt von loxmatter" Address="192.168.1.50" Port="7000">
	<Info templateType="1" minVersion="14040925"/>
	<VirtualInUdpCmd Title="Wohnzimmer Temperatur" Comment="Wohnzimmerlampe · 1/1026/0" Address="" Check="d12_1_temp:\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483647" MaxVal="2147483647" Unit="&lt;v.1&gt; °C" HintText=""/>
</VirtualInUdp>
```

```xml
<?xml version="1.0" encoding="utf-8"?>
<VirtualOut Title="Matter — Wohnzimmerlampe" Comment="erzeugt von loxmatter" Address="http://192.168.1.50:8080" CmdInit="" HintText="" CloseAfterSend="true" CmdSep="">
	<Info templateType="3" minVersion="14040925"/>
	<VirtualOutCmd Title="Wohnzimmer Licht Helligkeit" Comment="d12_1_level" CmdOnMethod="GET" CmdOffMethod="GET" CmdOn="/cmd/d12_1_level/&lt;v&gt;" CmdOnHTTP="" CmdOnPost="" CmdOff="" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="true" Repeat="0" RepeatRate="0"/>
</VirtualOut>
```

**Achtung Escaping:** Der Loxone-Wertplatzhalter `<v>` in `CmdOn` steht in einem
XML-Attribut und muss als `&lt;v&gt;` geschrieben werden. Ein unescaptes `<v>` macht die
Datei unlesbar für Loxone Config. Der Platzhalter `\v` in `Check` ist davon nicht betroffen.

**Bedeutung von `Address`:** Im `VirtualInUdp` ist `Address` der *Absender-Filter* —
die IP der Bridge, von der Datagramme akzeptiert werden. `Port` ist der Port, auf dem
der Miniserver lauscht. Im `VirtualOut` ist `Address` dagegen die Ziel-Basis-URL der
Bridge.

**Herkunft und Korrekturhistorie.** Der erste Entwurf dieses Abschnitts übernahm das
Schema aus der Referenzimplementierung des LoxBerry-Template-Builders
(<https://github.com/mschlenstedt/Loxberry/blob/master/libs/phplib/loxberry_loxonetemplatebuilder.php>).
Am 2026-09-02, belegt an den 26 Vorlagen aus einer echten Installation
(91 `VirtualInUdpCmd`, 19 `VirtualOutCmd`), stellte sich heraus, dass dieses Schema in
vier Punkten von dem abwich, was Loxone Config tatsächlich schreibt. Die Blöcke oben
zeigen bereits die korrigierte, gemessene Form; die vier Korrekturen zur
Nachvollziehbarkeit:

1. **Jede Vorlage trägt ein `<Info>`-Element als erstes Kind** — in allen 26 Dateien,
   ohne Ausnahme:
   `<Info templateType="1" minVersion="14040925"/>`
   `templateType` ist **1** für `VirtualInUdp`, **2** für `VirtualInHttp`, **3** für
   `VirtualOut`. `minVersion` ist eine Loxone-Config-Version im Format `JJMMTTHH`.
2. **`VirtualInUdpCmd` hat 15 Attribute**, nicht 13 — es fehlten `Unit` und `HintText`:
   `Title, Comment, Address, Check, Signed, Analog, SourceValLow, DestValLow,
   SourceValHigh, DestValHigh, DefVal, MinVal, MaxVal, Unit, HintText`
3. **`VirtualOut` hat `HintText`** zwischen `CmdInit` und `CloseAfterSend`.
4. **`VirtualOutCmd` hat 15 Attribute und kein `ID`**, und die beiden Methodenfelder
   stehen zusammen statt verteilt:
   `Title, Comment, CmdOnMethod, CmdOffMethod, CmdOn, CmdOnHTTP, CmdOnPost, CmdOff,
   CmdOffHTTP, CmdOffPost, CmdAnswer, HintText, Analog, Repeat, RepeatRate`

Bestätigt haben sich dagegen von Anfang an: UTF-8 mit BOM (26 von 26), reines CRLF
(26 von 26) und die XML-Deklaration wörtlich (26 von 26).

Dateiformat: **UTF-8 mit BOM, CRLF-Zeilenenden.**
Dateinamen: `VIU_d<device_id>_<label>.xml` und `VO_d<device_id>_<label>.xml`, mit auf ASCII
normalisiertem Gerätelabel. Die `device_id` ist nicht Dekoration: die Normalisierung ist
verlustbehaftet und bildet unterschiedliche Labels auf denselben oder einen leeren String
ab — `"Lampe 1"`, `"Lampe-1"` und `"Lampe_1"` etwa alle auf `Lampe_1`, ein Label ohne
ASCII-Zeichen auf `""`. Trüge der Dateiname nur das Label, würden zwei Geräte mit
kollidierendem Label sich beim Export gegenseitig überschreiben, und ein Nutzer importierte
eine Vorlage im Glauben, es seien zwei. `device_id` vergibt `Store` unveränderlich und nie
doppelt (6.2), das macht den Namen tatsächlich eindeutig.
Ablage: `Dokumente\Loxone\Loxone Config\Templates\VirtualIn\` bzw. `...\VirtualOut\`.
Import in Config: Peripherie → Virtuelle Eingänge → Virtueller UDP-Eingang → Vorlage
importieren.

Ein `VirtualInUdp` trägt beliebig viele `VirtualInUdpCmd` — ein Import bringt damit
alle Signale *eines Geräts* auf einmal ins Projekt.

### 6.2 Schlüsselvergabe und Export-Granularität

**Eine Vorlagendatei pro Gerät.** Jedes Matter-Gerät wird zu genau einem
`VirtualInUdp`-Objekt (alle Sensorwerte und Events) und einem `VirtualOut`-Objekt
(alle Kommandos). In Loxone Config erscheint damit pro Gerät ein benannter Knoten mit
seinen Befehlen darunter — navigierbar und einem Gerät eindeutig zuordenbar. Ein
Sammel-Export von 200 Eingängen in einem Objekt wäre in der Config nicht mehr
handhabbar.

Damit löst sich das Re-Import-Problem von selbst: ein neu eingelerntes Gerät bedeutet
genau einen zusätzlichen Import; bestehende Objekte werden nie angefasst, die
Verdrahtung bleibt intakt.

**Alle Geräte teilen sich einen UDP-Port.** Die Miniserver-Grenze liegt bei
**max. 50 verschiedenen Eingangs-UDP-Ports** — sie zählt Ports, nicht Objekte. Da alle
Keys global eindeutig sind, greift jedes `VirtualInUdp`-Objekt ausschließlich seine
eigenen `Check`-Muster ab; ein gemeinsamer Port erzeugt kein Übersprechen. Default 7000,
Verbrauch: genau ein Port, unabhängig von der Gerätezahl.
Quelle: <https://www.loxone.com/enen/kb/communication-with-udp/>

Dass mehrere `VirtualInUdp`-Objekte denselben Port teilen können, ist **am realen
Miniserver bestätigt** (2026-09-01). Die Grenze von 50 Ports gilt zudem auf allen
Miniserver-Generationen gleichermaßen. Der Port bleibt dennoch pro Gerät
konfigurierbar — für getrennte Netzsegmente oder mehrere Bridges an einem
Miniserver.

**Keys sind opak und unveränderlich.** Format `d<device_id>_<endpoint>_<slug>`, z. B.
`d12_1_temp`. Vergeben beim Einlernen, danach eingefroren. Lesbare Namen leben
ausschließlich in `Title` und `Comment`. Umbenennen in der WebUI ändert nur die
Beschriftung im nächsten Export, nie die Verdrahtung. Der Key muss auch dann eindeutig
bleiben, wenn ein Gerät entfernt und neu eingelernt wird — `device_id` wird deshalb nie
wiederverwendet.

**Systemsignale** (`bridge_alive`, `/resync`, siehe 6.4 und 6.5) liegen in einem eigenen
Paar `VIU_Matter_System.xml` / `VO_Matter_System.xml`, das einmalig importiert wird — ein
fester Name, weil es kein Gerät und damit keine `device_id` gibt, die ihn eindeutig machen
müsste.

Dateinamen der Gerätevorlagen: siehe 6.1.

### 6.3 Events

**Event-Erkennung (korrigiert, Phase 1, 2026-09-01).** Ursprünglich war hier
angenommen, dass sich Events wie Attribute generisch aus der `EventList`
(0xFFFA) jedes Clusters lesen lassen. Die Validierung in 3.5 hat das
widerlegt: keins der geprüften Geräte führt dieses global optionale Attribut.
Event-Erkennung ist deshalb **FeatureMap-basiert** und cluster-spezifisch:
für den Switch-Cluster (59) steht in `discovery.FEATURE_MAP_EVENTS`, welches
Event welche FeatureMap-Bits voraussetzt — `SwitchLatched` ← LS, `InitialPress`
← MS, `LongPress`/`LongRelease` ← MSL, `ShortRelease` ← MSR,
`MultiPressOngoing` ← MSM ∧ ¬AS, `MultiPressComplete` ← MSM. Geprüft gegen
`data_model/1.4/clusters/Switch.xml` aus `project-chip/connectedhomeip`, der
maschinenlesbaren Transkription der Matter Application Cluster Specification
(`mandatoryConform`-Bedingung je Event). Die `EventList` bleibt als
**zusätzliche** Quelle bestehen — sie kostet nichts, und einzelne Geräte
implementieren sie durchaus —, ihre Treffer werden mit denen aus der
FeatureMap vereinigt und dedupliziert. Weitere Cluster mit Events kommen als
weitere Tabelleneinträge dazu, ohne den Algorithmus in `extract_signals`
anzufassen.

Der Matter-`Switch`-Cluster liefert `InitialPress`, `ShortRelease`, `LongPress`,
`MultiPressComplete`. Ein virtueller UDP-Eingang kennt nur Werte, kein Event-Konzept.

Pro Event-Typ werden **zwei** Signale exportiert:

- `<key>` — digitaler Impuls: `1`, nach 200 ms `0`. Erzeugt eine saubere Flanke.
- `<key>_n` — monotoner Zähler. Robuster, weil ein verlorenes UDP-Paket den Zähler nur
  springen lässt statt den Druck zu verschlucken.

Bei `MultiPressComplete` zusätzlich `_press2`, `_press3` als eigene Impulse sowie
`_presscount`.

### 6.4 Zustands-Wiederherstellung

UDP ist zustandslos. Nach einem Miniserver-Neustart stehen alle Eingänge auf `DefVal`,
bis das nächste Update eintrifft — bei einem Temperatursensor potenziell Stunden.

- **Periodischer Full-Resend** aller aktuellen Werte, Default alle 5 min, gestaffelt auf
  ca. 50 Datagramme/s.
- **`/resync`-Endpoint**, als fertiger `VirtualOutCmd` mitexportiert. Im Config-Projekt
  an den Systemstart-Baustein gehängt, sind nach jedem Neustart sofort alle Werte da.

**Befund (Phase 4, Live-Lauf 2026-09-02).** Ein Resend kann nur Werte verschicken, die
die Bridge schon selbst hält — er iteriert den zuletzt gesendeten Wert je Signal, nicht
den Gerätezustand. Dieser Cache entsteht ausschließlich über Subscriptions, die sich
*ändernde* Werte melden, und ist beim Start leer. Ein Live-Lauf mit einer
Matter-Steckdose ohne Last bestätigte das: über 40 s kamen genau drei Datagramme an
(Heartbeat, ein per HTTP ausgelöster Schaltbefehl), aber keines der 109 exportierbaren
Attributsignale — der Full-Resend beim Start lief leer, weil noch nichts im Cache stand,
und ohne eine sich ändernde Last hätte sich das auf unabsehbare Zeit nicht geändert.
Genau in diesem Moment — direkt nach einem Neustart der Bridge — ist der Mechanismus
also leer, obwohl er hier am nötigsten wäre. Die Bridge muss sich deshalb beim Start
selbst aus dem aktuellen Gerätezustand säen (`Runtime.seed_from_snapshot`, gefüttert aus
`BridgeMatterClient.snapshots()` — demselben Bild, aus dem auch `loxmatter export`
liest), bevor der erste Full-Resend läuft.

### 6.5 Zusätzliche Signale

- `d<id>_online` — digital, pro Gerät: erreichbar ja/nein.
- `bridge_alive` — global, toggelt alle 30 s. Als Watchdog in Loxone; deckt „Container
  tot" und „Netz weg" gleichermaßen ab.

### 6.6 Nicht exportierbare Werte

**Befund (Phase 1, 2026-09-01).** Gut ein Viertel der generisch extrahierten
Attributsignale ist nicht exportierbar, weil ein virtueller UDP-Eingang nur
Zahlen und digitale Werte annimmt (siehe 3.5): bei der geprüften Steckdose 45
von 159 (28,3 %). Strings lassen sich noch über einen virtuellen Text-Eingang
ausgeben; für Listen und Structs (`0/29/1 = [29, 31, 40, ...]`,
`0/31/0 = [{...}]`) gibt es in Loxone **keine** Entsprechung.

Der Exporter (Phase 3) braucht dafür eine explizite Regel statt eines
impliziten Verhaltens: Signale mit Listen- oder Struct-Werten werden beim
Export ausgelassen, Strings gehen an einen virtuellen Text-Eingang statt an
den numerischen `VirtualInUdpCmd`. Die generische Zerlegung selbst ändert
sich dadurch nicht — sie liefert weiterhin alles, was das Gerät anbietet; die
Auswahl „exportierbar oder nicht" entsteht erst beim Export, nicht bei der
Extraktion. Siehe 8 für die Konsequenz in der WebUI.

Eine vierte, stille Kategorie kommt dazu: bei der Steckdose tragen 5 der 159
Attributsignale den Wert `null` (z. B. `0/49/7`) — weder unreportiert (der
Pfad ist da), noch unparsebar, noch nicht-skalar im Sinne von oben, aber
genauso wenig ein Zahlen- oder Digitalwert; der Exporter muss auch für `null`
eine explizite Entscheidung treffen.

**Die Zahl, mit der der Exporter rechnet, ist deshalb nicht 45, sondern 50.**
Die 45 sind die nicht-skalaren Signale; nicht auf einen `VirtualInUdpCmd`
abbildbar sind darüber hinaus auch die Nullwerte. Aufschlüsselung der 159
Attributsignale der Steckdose, gemessen am 2026-09-01:

| Kategorie | Anzahl | exportierbar |
|---|---|---|
| Zahlen (analog) | 102 | ja |
| Wahrheitswerte (digital) | 7 | ja |
| Texte | 13 | nur über einen virtuellen Text-Eingang |
| Listen und Structs | 32 | nein |
| `null` | 5 | nein |
| **auf `VirtualInUdpCmd` abbildbar** | **109** | |

Wer die 45 als „nicht exportierbar" liest, verzählt sich um die fünf
Nullwerte.

### 6.7 Ausgangsbefehle: Erlaubnisliste

**Quelle ist `AcceptedCommandList` (0xFFF9), nicht die Attributliste.** Matter-Attribute
sind ganz überwiegend nur lesbar; ein Ausgangsbefehl je lesbarem Attribut wäre zu
über neunzig Prozent wirkungslos. An den Geräten aus Phase 1 gemessen (2026-09-01):
die Steckdose ist über `1/6` OnOff steuerbar, der Taster über gar nichts — er ist ein
Eingabegerät.

**Bei Kommandos gilt eine Erlaubnisliste, nicht die großzügige Durchreiche aus 3.5.**
Zu den akzeptierten Kommandos gehören Verwaltungscluster: `0/62` OperationalCredentials
enthält `RemoveFabric`, `0/48` GeneralCommissioning und `0/49` NetworkCommissioning die
Kommissionierung, `0/51` GeneralDiagnostics den `TestEventTrigger`. Ein Exporter, der
alles ausgibt, legt einem Loxone-Nutzer Befehle auf den Baustein, mit denen sich das
Gerät aus der Fabric werfen oder unbrauchbar machen lässt.

Nur Cluster mit einem `commands`-Eintrag in der Profiltabelle erzeugen Ausgangsbefehle.
Ein ausdrücklich einzuschaltender **Rohmodus** erweitert das auf unbekannte Cluster —
für Geräte, deren Cluster die Tabelle noch nicht kennt.

**Verwaltungscluster bleiben auch im Rohmodus gesperrt.** Das ist keine Vorsichtsmaßnahme,
die sich abschalten lässt:

```
31, 41, 42, 48, 49, 50, 51, 52, 53, 54, 55, 56, 60, 62, 63, 70
```

Die Asymmetrie ist beabsichtigt. Ein unbekanntes Attribut zu viel zu exportieren kostet
einen ungenutzten Eingang. Ein unbekanntes Kommando zu viel zu exportieren kann ein
Gerät aus dem Netz werfen.

---

## 7. Matter-Integration

### 7.1 Einlernen

WebUI nimmt Pairing-Code (11-/21-stellig) oder QR-Inhalt entgegen und reicht ihn an
`matter-server` durch. Bei Thread-Geräten liefert der eigene OTBR das
Operational Dataset.

Geräte, die bereits in einem anderen Ökosystem (Apple/Google/Amazon) sind, müssen dort
per **Multi-Admin** einen zusätzlichen Pairing-Code erzeugen. Die WebUI erklärt das
inline — es ist der häufigste Stolperstein.

### 7.2 Bridges

Eine IKEA DIRIGERA oder vergleichbare Matter-Bridge erscheint als *ein* Node mit vielen
Endpoints. Die WebUI muss Endpoints darum als eigenständige, benennbare Einheiten
darstellen, nicht als Unterpunkte eines Geräts. Das Datenmodell trägt das bereits
(`Signal.endpoint`).

**Fehlende UniqueID (Phase 1, 2026-09-01).** Der IKEA BILRESA-Taster (node 4)
liefert kein `UniqueID` (BasicInformation, `0/40/18`) — das Attribut fehlt
komplett, nicht nur der Wert ist leer. `NodeSnapshot.from_raw` liest es
bereits tolerant (leerer String statt Fehler), `loxmatter inspect` zeigt
entsprechend `Unique ID: —`. Das Datenmodell stützt sich in 5 auf `unique_id`,
weil sie eine Neuvergabe der Node-ID überlebt — für Geräte ohne UniqueID gilt
das nicht, dort bleibt `device_id` die einzig stabile Kennung. Kein
Randfall: Hersteller lassen dieses optionale Attribut real aus.

### 7.3 Werte und Skalierung

Beispiele aus der `profiles/`-Tabelle:

| Cluster | Attribut | Roh | Loxone |
|---|---|---|---|
| TemperatureMeasurement | MeasuredValue | 0,01 °C | ÷100, `°C` |
| RelativeHumidityMeasurement | MeasuredValue | 0,01 % | ÷100, `%` |
| ElectricalPowerMeasurement | ActivePower | mW | ÷1 000 000, `kW` |
| ElectricalPowerMeasurement | RMSVoltage | mV | ÷1000, `V` |
| ElectricalPowerMeasurement | RMSCurrent | mA | ÷1000, `A` |
| ElectricalEnergyMeasurement | CumulativeEnergyImported | mWh | ÷1 000 000, `kWh` |
| LevelControl | CurrentLevel | 0–254 | ×100/254, `%` |
| OnOff | OnOff | bool | digital |

**Zieleinheiten richten sich nach Loxone, nicht nach SI.** Loxone rechnet Leistung
durchgängig in **kW** — der Energiemanager, die Zähler- und Verbrauchsbausteine erwarten
kW am Eingang. Wir liefern deshalb kW, nicht W. Dieselbe Regel gilt für jeden künftigen
Eintrag in der Profiltabelle: maßgeblich ist die Einheit, die der Loxone-Baustein
erwartet, nicht die naheliegende SI-Einheit.

**`Unit` ist ein Formatstring, kein Einheitentext.** Loxone schreibt dort Muster wie
`<v.3> kW`, `<v.1> °C` oder `<v>%`: die Ziffer hinter dem Punkt ist die Zahl der
angezeigten Nachkommastellen. Gemessen an 26 realen Vorlagen ist `<v.3> kW` mit
Abstand die häufigste Form für Leistung.

**Das hebelt die Regel unten auf der Anzeigeebene aus.** Ein Wert von 0,0003 kW kommt
mit `<v.3> kW` als `0.000` auf der Oberfläche an — der Wert im Miniserver stimmt, aber
niemand sieht ihn. Der Exporter muss für Leistung deshalb **`<v.6> kW`** schreiben, nicht
das übliche `<v.3>`. Dasselbe gilt für jede Größe, deren interessanter Bereich mehrere
Größenordnungen umfasst.

**Folge für die Zahlenformatierung.** Von mW nach kW sind sechs Größenordnungen. Ein
Standby-Verbraucher mit 300 mW wird zu `0.0003` kW. Der UDP-Sender darf Werte deshalb
**nicht auf zwei Nachkommastellen runden** — sonst verschwindet alles unter 10 W in der
Null, und genau diese kleinen Dauerverbraucher will man in Loxone ja sehen. Festlegung:
Ausgabe mit bis zu **6 Nachkommastellen**, nachlaufende Nullen abgeschnitten. Das ist
ein eigener Testfall in der Skalierungs-Testsuite.

Farbe: Loxone liefert in Lumitech- bzw. RGB-Notation, Matter erwartet Hue/Saturation
oder CIE xy. Die Umrechnung liegt in `commands/` und ist beidseitig zu testen.

**Rechercheergebnis (Task 5, 2026-09-02).** Die RGB-Codierung ist offiziell belegt: der
Loxone-Baustein "RGB Lighting Controller" gibt Farbe auf einem einzelnen Analogausgang
als eine Dezimalzahl aus, die drei Prozentwerte (je 0-100) dezimal aneinanderreiht -
`AQa = rot% + gruen% * 1000 + blau% * 1_000_000` (z. B. 20040060 = 60 % Rot, 40 % Gruen,
20 % Blau). Quelle: Loxone Knowledge Base, "RGB Lighting Controller", Abschnitt
"Outputs" (https://www.loxone.com/enen/kb/rgb-scene-controller/, abgerufen 2026-09-02).

Fuer die Lumitech-Codierung (Helligkeit plus Farbtemperatur in einer Zahl) hat sich
**keine belastbare Quelle** finden lassen - weder auf der offiziellen
Beleuchtungsbaustein-Seite noch im Structure-File-PDF. Der einzige Treffer ist ein
Forumsbeitrag mit selbst mitgeloggten DMX-Werten (vermutetes Format "AABBBCCCC"), den
der Autor selbst als Vermutung kennzeichnet
(https://www.loxforum.com/forum/hardware-zubehoer-sensorik/143867-lumitech-ausgang-dmx-dimmer,
Beitrag #2). Task 5 implementiert deshalb nur die (unstrittige) Matter-seitige
Umrechnung Kelvin→Mired und RGB→Hue/Saturation in `commands/color.py`;
`to_matter_call` nimmt fuer Farbtemperatur einen bereits entpackten Kelvin-Wert
entgegen und dekodiert keine rohe Loxone-Zahl. Das Entpacken der rohen Loxone-Zahl
(RGB wie Lumitech) bleibt Aufgabe von Task 6 (HTTP-Endpoint) bzw. der WebUI, sobald fuer
Lumitech eine verlaessliche Quelle vorliegt - siehe Offene Punkte.

**Nicht an Hardware geprueft.** Fuer diese Aufgabe stand keine Matter-Leuchte zur
Verfuegung. `kelvin_to_mireds` und `rgb_to_hue_saturation` sind ausschliesslich gegen
Referenzwerte (Zigbee/Matter-Mired-Konvention bzw. HSV-Definition) getestet, nicht gegen
ein reales Geraet.

---

## 8. WebUI

Vier Ansichten, bewusst knapp gehalten.

**1. Geräte** — Liste mit Online-Status, Einlernen per Code/QR, Umbenennen, Entfernen.
Pro Gerät die wichtigsten Live-Werte und **direkte Bedienelemente** für die
naheliegenden Aktionen:

| Gerätetyp | Bedienung in der WebUI |
|---|---|
| Licht | Toggle, Helligkeitsregler, Farbtemperatur/Farbe |
| Steckdose | Toggle, daneben aktuelle Leistung |
| Rollo | Auf / Ab / Stopp, Positionsregler |
| Thermostat | Sollwert, Betriebsart |
| Sensor, Taster | nur Anzeige — nichts zu bedienen |

**2. Signale** — pro Gerät der vollständige Attribut- und Event-Baum mit Live-Wert.
Checkbox „nach Loxone exportieren", editierbarer Titel, Key sichtbar aber nicht
editierbar. Schreibbare Attribute lassen sich hier **roh setzen** — für alles, wofür
Ansicht 1 keinen Regler hat, und für unbekannte Cluster. Nicht-exportierbare Werte
(Listen und Structs, siehe 6.6) werden trotzdem angezeigt, mit einem Hinweis statt
der Export-Checkbox — gerade zur Diagnose sind sie nützlich, auch wenn sie nie ein
UDP-Datagramm werden.

**3. Export** — Miniserver-IP und UDP-Port eintragen. Vorlagen pro Gerät herunterladen,
einzeln oder als ZIP; Filter „nur noch nicht exportierte Geräte". Pro Gerät ist
sichtbar, wann zuletzt exportiert wurde und ob sich seither Signale geändert haben.
Enthält die Kurzanleitung und die einmaligen Systemvorlagen.

**4. System** — Status von matter-server und OTBR, Thread-Netz, Logs, Backup der
Fabric-Credentials.

### 8.1 Warum die Bedienung mehr ist als Komfort

Ansicht 1 ist das **Diagnosewerkzeug** des Projekts. Schaltet eine Lampe über Loxone
nicht, trennt ein Klick in der WebUI die beiden möglichen Ursachen sauber: reagiert das
Gerät hier, liegt der Fehler in der Loxone-Verdrahtung oder im Vorlagen-Export;
reagiert es nicht, in Matter, Thread oder am Gerät. Ohne das ist jede Fehlersuche
Raten — und bei einem Tool für fremde Installationen ist das der Unterschied zwischen
einem beantwortbaren und einem unbeantwortbaren Bug-Report.

Deshalb gehört die Bedienung in v1 und nicht in eine spätere Ausbaustufe.

### 8.2 Abgrenzung

Die WebUI ist ein **Inbetriebnahme- und Diagnosewerkzeug, keine Smart-Home-Oberfläche.**
Nicht enthalten und auch nicht geplant: Szenen, Zeitpläne, Automatisierungen,
Favoritenseiten, Räume, Nutzerverwaltung, App. Das ist alles Aufgabe von Loxone — die
Bridge dupliziert es nicht.

### 8.3 Live-Aktualisierung

Ein WebSocket vom Backend zur SPA schiebt Attribut- und Event-Änderungen sowie
Online-Status durch. Dieselbe Subscription, die den UDP-Sender speist — kein zweiter
Pfad, kein Polling.

### 8.4 Rohes Attributschreiben: Erlaubnisliste (Task 4, 2026-09-02; Befund berichtigt,
Review-Fix Important #2, 2026-09-02)

**Befund: die Schreibbarkeit eines Attributs steht in einer Tabelle, die diese
Installation nicht laden kann und die python-matter-server nirgends benutzt — nicht,
wie hier zuerst behauptet, in gar keiner Tabelle.** Geprüft gegen die installierten
Pakete (python-matter-server==8.1.2), nicht vermutet:

- `chip.clusters.ClusterObjects.ClusterAttributeDescriptor` — die Basisklasse jeder
  generierten Attribut-Klasse (z. B. `BasicInformation.Attributes.NodeLabel`) — trägt
  `cluster_id`, `attribute_id`, `attribute_type`, `must_use_timed_write`. Keine
  dieser Eigenschaften unterscheidet Lese- von Schreibzugriff;
  `must_use_timed_write` regelt nur, ob ein *erlaubter* Schreibzugriff einen
  Timed-Write-Envelope braucht.
- `matter_server.client.client.MatterClient.write_attribute(node_id, attribute_path,
  value)` prüft vorher nichts — der Aufruf geht ungeprüft an den Controller; eine
  Ablehnung käme, wenn überhaupt, als Fehler vom Gerät selbst zurück.
- **Eine Volltextsuche nach „writable“ ergab sehr wohl Treffer — hier stand vorher
  fälschlich das Gegenteil.** `chip/clusters/CHIPClusters.py`, Teil des installierten
  `chip`-Pakets, trägt eine eigene, von `ClusterObjects` unabhängige Tabelle mit genau
  dieser Information: `grep -c '"writable": True'
  .venv/lib/python3.12/site-packages/chip/clusters/CHIPClusters.py` liefert **250**
  Treffer, und für `BasicInformation` (Cluster 0x28 = 40) sind darin exakt die drei
  Attribut-IDs 5 (`NodeLabel`), 6 (`Location`) und 16 (`LocalConfigDisabled`) mit
  `"writable": True` markiert — genau die drei, auf die die Erlaubnisliste unten
  unabhängig davon schon gegen ein echtes Gerät kam.
- **Dieses Modul ist trotzdem nicht importierbar, und python-matter-server benutzt es
  nirgends.** `from chip.clusters.CHIPClusters import ChipClusters` scheitert in
  dieser Distribution mit `ImportError: cannot import name 'exceptions' from 'chip'`
  — das Paket `home_assistant_chip_clusters`, das hier `chip.clusters.CHIPClusters`
  bereitstellt, liefert die Datei ohne das dazugehörige `chip/exceptions.py`, das sie
  beim Laden voraussetzt. Eine Suche nach `CHIPClusters` im installierten
  `matter_server`-Paket ergibt außerdem keinen einzigen Treffer.

Die praktische Konsequenz ist dieselbe wie vorher — die Erlaubnisliste bleibt für
heute richtig —, nur ihre Begründung ist jetzt eine andere: nicht „die Information
existiert nicht“, sondern „die Information existiert in einer Tabelle, die diese
Installation nicht laden kann und die python-matter-server selbst nicht liest“. Das
ist ein Unterschied mit Konsequenz: die zweite Situation hat einen offensichtlichen
Weg in die Zukunft, den die erste nicht hätte — siehe Offene Punkte, Punkt 7.

**Konsequenz: dieselbe Asymmetrie wie bei Kommandos (6.7), diesmal für Attribute.**
`POST /api/signals/{key}/write` (`api/control.py`) lehnt jeden Schreibversuch auf ein
Attribut ab, das nicht auf einer expliziten Erlaubnisliste steht — eine großzügige
Durchreiche wie beim *Export* (3.5) wäre hier falsch: ein zu Unrecht exportiertes
Attribut kostet einen ungenutzten Eingang, ein zu Unrecht freigegebener Schreibzugriff
kann ein Gerät fehlkonfigurieren. Die Liste ist bewusst klein und enthält nur
`BasicInformation.NodeLabel` (0/40/5), `.Location` (0/40/6) und
`.LocalConfigDisabled` (0/40/16) — alle drei belegt gegen die eingecheckte
IKEA-GRILLPLATS-Vorlage (`tests/fixtures/nodes/ikea_grillplats_plug.json`), nicht nur
laut Spezifikation vermutet.

**Offener Punkt: selbst ein erlaubtes Attribut lässt sich heute noch nicht tatsächlich
schreiben.** `BridgeMatterClient` (`matter/client.py`) hat kein `write_attribute`, und
`build_control_router(store, invoke)` nimmt dafür auch keinen zweiten Aufrufer entgegen
— `invoke` ist ausschließlich für Kommandos typisiert
(`Callable[[MatterCall], Awaitable[None]]`), ein Attribut-Schreibzugriff ist keins.
`POST /api/signals/{key}/write` antwortet für ein erlaubtes Attribut deshalb mit 501
statt mit einem Erfolg, der nichts bewirkt — siehe Offene Punkte, Punkt 6.

---

## 9. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| matter-server nicht erreichbar | Reconnect mit exponentiellem Backoff; `bridge_alive` stoppt → Loxone-Watchdog schlägt an |
| Gerät offline | `d<id>_online` = 0, letzte Werte bleiben stehen (kein Zurücksetzen) |
| HTTP-Kommando an offline Gerät | HTTP 503, im Log sichtbar. Loxone wertet die Antwort eines virtuellen Ausgangs ohnehin nicht aus |
| UDP-Sendefehler | Log, kein Retry (fire and forget) |
| Unbekanntes Cluster | Roh-Export ohne Skalierung, Warnung in der WebUI |
| Gerät entfernt und neu eingelernt | Neue `device_id`, neue Keys. Die alten Loxone-Objekte werden verwaist — die WebUI weist darauf hin und benennt die zu löschenden Objekte |

### 9.1 Absicherung der `/api`-Routen (Task 8, 2026-09-02)

Bis Phase 4 bot dieser Dienst zwei Endpunkte für den Miniserver: `/cmd` und
`/resync`. Wer den Port erreichte, konnte damit höchstens ein Gerät schalten. Seit
Phase 5 (Task 1: Einlernen, Task 2: Entfernen, Task 6: Fabric-Sicherung als Download)
ist das Gewicht ein anderes: wer den Port erreicht, kann Geräte aus der Fabric werfen
oder die kompletten, unersetzlichen Fabric-Credentials herunterladen (siehe 4.1). Das
ist eine Änderung der Art des Risikos, nicht nur seines Grads.

**Die Entscheidung:** ein optionales Bearer-Token (`--api-token`/
`LOXMATTER_API_TOKEN`, siehe `loxone.server.build_api_guard`) schützt ab hier
ausnahmslos jede Route unter `/api` — lesend und schreibend, alle fünf Router
(Geräte, Steuerung, Export, Live-Werte inklusive der WebSocket-Route `/api/live`,
Diagnose inklusive der Fabric-Sicherung). Ist kein Token konfiguriert, bleiben diese
Routen unverändert offen — mit einer deutlichen Warnung im Log beim Start
(`cli._warn_if_missing_api_token`). Ein Dienst, der ohne Token gar nicht erst startet,
wäre für eine Testumgebung oder eine Erstinbetriebnahme ohne vorbereitetes Geheimnis
unbenutzbar; die Warnung ist der bewusst gewählte Ausgleich dafür.

**Warum `/cmd` und `/resync` bewusst ausgenommen bleiben:** der Miniserver ruft
virtuelle Ausgänge als einfachen HTTP-GET auf, ohne die Möglichkeit, einen Header
mitzuschicken — das ist eine Eigenschaft des Loxone-Vorlagenformats (6.1), keine
Wahl dieses Projekts. Ein Token auf diesem Pfad würde die Loxone-Integration schlicht
abschalten, nicht absichern. Das ist eine reale, dauerhafte Grenze und wird hier als
solche festgehalten, nicht als Unzulänglichkeit: **auch mit gesetztem Token kann jeder
im selben Netz weiterhin Geräte über `/cmd` schalten.** Was das Token verhindert, ist
ausschließlich die Veränderung des Bestands — Einlernen, Entfernen und der Download
der Fabric-Sicherung.

`--host` (Standard `0.0.0.0`) bindet weiterhin an alle Schnittstellen, weil der
Miniserver den Dienst erreichen muss — das Token ändert daran nichts, es schützt nur,
was hinter `/api` erreichbar ist, nicht die Erreichbarkeit selbst.

**Zwei Übertragungswege für das Token, und warum es zwei sein müssen** (Nachbesserung
nach Review, 2026-09-03). `Authorization: Bearer <Token>` ist der Hauptweg und gilt für
jede REST-Route. Für die WebSocket-Route `/api/live` ist er strukturell unmöglich: die
Browser-`WebSocket`-API (`new WebSocket(url, protocols)`) kennt überhaupt keinen
Parameter für eigene Kopfzeilen. Der einzige vom Browser beeinflussbare Kanal im
Handshake ist das Subprotokoll-Argument, das als `Sec-WebSocket-Protocol` auf die
Leitung geht. Die Oberfläche verbindet sich deshalb mit `new WebSocket(url, ["bearer",
token])`, und `build_api_guard` akzeptiert das Token zusätzlich aus diesem einen Header,
ausschließlich in der Form `bearer, <Token>`. Ein Query-Parameter wäre die naheliegende
Alternative und ist bewusst NICHT gewählt: er landet in Server-Logs, Proxy-Logs und der
Browser-History, ein Header nicht — dieselbe Überlegung, aus der `api/diagnostics.py`
das Kommando-Log ohne Query-Zeichenkette führt (10.5). `api/live.py` gibt im Accept den
Marker `bearer` zurück (nie das Token), weil der Browser den Handshake nach RFC 6455
sonst abbricht.

**Daraus folgt eine Anforderung an das Token selbst:** es muss als HTTP-Token
übertragbar sein — keine Leerzeichen, kein Komma, kein Nicht-ASCII. `openssl rand -hex
32`, der in `.env.example` und README empfohlene Weg, liefert nur `[0-9a-f]` und
erfüllt das von sich aus; die Anforderung steht dort ausdrücklich, statt eine
stillschweigende Annahme zu bleiben. Ein Token, das nur aus Leerraum besteht (ein
abgeschnittener Zeilenumbruch aus einer kopierten `.env`), gilt als „nicht gesetzt" —
`normalize_api_token` entscheidet das für Wächter UND Startwarnung gemeinsam, sonst
wäre der Dienst gesperrt, ohne dass die Warnung darauf hinwiese. Der Vergleich selbst
läuft über `secrets.compare_digest` auf UTF-8-Bytes, nicht über `!=` und nicht über
`str` (bei `str` wirft `compare_digest` `TypeError`, sobald Nicht-ASCII im Spiel ist —
ein Nicht-ASCII-Token im Header darf keinen 500er auslösen).

**Die eine Ausnahme von „ohne Token bleibt `/api` offen": die Fabric-Sicherung.** `GET
/api/diagnostics/fabric-backup` wird ohne konfiguriertes Token gar nicht erst
ausgeliefert (HTTP 403, mit einer Erklärung im `detail`). Alle übrigen Routen bleiben
unverändert offen. Der Grund ist der Unterschied im Schaden, nicht im Prinzip: eine
ungeschützte Geräteliste ist peinlich, eine ungeschützte Fabric-Sicherung ist die
irreversible Übernahme der Fabric (4.1). Die Referenz-Installation
(`deploy/testhost/`) hängt das matter-server-Datenverzeichnis ein und läuft mit
`network_mode: host` — ein Standard, der von Disziplin beim Lesen der README abhinge,
wäre für genau diese eine Route nicht vertretbar. 403 und nicht 401, weil es ohne
konfiguriertes Token gar nichts gibt, womit sich jemand authentifizieren KÖNNTE: eine
Wiederholung mit Zugangsdaten kann nicht helfen, und genau das unterscheidet 403 von
401 (RFC 9110). 503 bleibt dem bereits vorhandenen Fall „kein Datenverzeichnis
eingehängt" vorbehalten — drei Ursachen, drei unterscheidbare Codes.

---

## 10. Testen

### 10.1 Automatisierte Tests

- **Exporter — Golden-File-Tests.** Roundtrip: XML erzeugen → in Loxone Config
  importieren → dort wieder als Vorlage speichern → diffen. Die einzige Methode, die das
  echte Format verifiziert. Die Referenzdateien werden im Repo eingecheckt.
- **Matter-Adapter** — gegen aufgezeichnete WebSocket-Fixtures von `matter-server`.
- **Integration ohne Hardware** — `chip-all-clusters-app` als virtuelles Matter-Gerät im
  CI-Container. Deckt Einlernen, Subscription und Kommandos ab.
- **UDP** — Fake-Miniserver (Socket-Listener), der Datagramme mitschreibt; prüft
  Entprellung, Impulslänge, Rate-Limit und Full-Resend.
- **Skalierung** — Tabellentests pro Cluster-Eintrag, inklusive Farbraum-Umrechnung
  in beide Richtungen. Eigener Fall für kleine Leistungswerte: 300 mW muss als
  `0.0003` ankommen, nicht als `0`.
- **`commands/`** — dieselbe Testsuite deckt beide Aufrufer ab. Zusätzlich ein Test,
  der prüft, dass WebUI-Route und Loxone-HTTP-Route für dieselbe Eingabe dasselbe
  Matter-Kommando erzeugen. Das ist die Regression, die das Modul überhaupt
  rechtfertigt.

Die gesamte Suite läuft **ohne Hardware und ohne Netzwerkzugriff**. Das ist eine
Anforderung, keine Beobachtung: sobald ein Test ein echtes Gerät braucht, wird er
übersprungen und verrottet.

### 10.2 Von Hand testen ohne Hardware

`docker compose --profile dev up` startet zusätzlich zum normalen Stack zwei
Hilfscontainer. Damit ist die komplette Strecke ohne Miniserver, ohne Thread-Dongle
und ohne ein einziges echtes Matter-Gerät durchspielbar:

**`virtual-devices`** — mehrere Instanzen der CHIP-Beispielanwendungen
(`chip-all-clusters-app`, `chip-lighting-app`) als echte Matter-Geräte über WiFi. Sie
werden mit den Standard-Pairing-Codes ganz normal über die WebUI eingelernt — es ist
derselbe Codepfad wie bei echter Hardware, nicht ein Mock daneben. `all-clusters-app`
ist dabei besonders wertvoll, weil sie absichtlich exotische Cluster mitbringt und damit
den generischen Export unter Last setzt.

**`fake-miniserver`** — ersetzt den Loxone Miniserver in beide Richtungen:
- lauscht auf UDP 7000 und zeigt jedes Datagramm mit Zeitstempel in einer kleinen
  Weboberfläche. Damit sieht man unmittelbar, was der Miniserver bekommen *würde*.
- kann HTTP-GETs an die Bridge abfeuern wie ein virtueller Ausgang, inklusive
  `<v>`-Ersetzung. Damit ist die Kommandorichtung ohne Loxone testbar.
- kann eine erzeugte `VIU_*.xml` einlesen und daraus die erwarteten Keys ableiten, um
  zu melden, welche exportierten Signale **nie** ein Datagramm gesehen haben. Das findet
  Mapping-Fehler, die sonst erst in Loxone auffallen.

Thread ist der einzige Teil, der echte Hardware braucht. Der OTBR liegt deshalb in
einem eigenen Compose-Profil — ohne Dongle startet der Stack trotzdem, nur eben ohne
Thread.

### 10.3 Der Durchstich von null

Der Weg, der nach jeder Änderung in wenigen Minuten läuft und dokumentiert wird:

1. `docker compose --profile dev up`
2. WebUI öffnen, virtuelles Gerät mit dem angezeigten Pairing-Code einlernen
3. Signale sehen, Gerät in der WebUI schalten — bestätigt Matter-Richtung
4. Vorlagen erzeugen, `fake-miniserver` zeigt die Datagramme — bestätigt Loxone-Richtung
5. Im `fake-miniserver` einen Befehl abfeuern — bestätigt Kommando-Richtung

Erst wenn das durchläuft, lohnt sich der Test an echter Hardware.

### 10.4 Entwicklungsumgebung

Miniserver, Thread-Dongle und echte Matter-Geräte (IKEA) stehen zur Verfügung. Zwei
Konsequenzen für die Planung:

- Die **Golden-File-Referenzen für den Exporter können von Anfang an aus echtem Loxone
  Config kommen** statt aus Vermutungen. Das nimmt dem riskantesten Modul das Risiko.
- Der **generische Export wird früh an echten Cluster-Bäumen validiert**. Reale Geräte
  weichen erfahrungsgemäß von den CHIP-Beispielapps ab — genau dort entstehen die
  Lücken, die eine rein virtuelle Entwicklung übersieht.

Das dev-Profil aus 10.2 bleibt trotzdem Pflicht: es ist die Grundlage für CI und für
Beiträge von außen, wo diese Hardware nicht vorhanden ist.

### 10.5 Eingebaute Diagnose

Diese vier Dinge sind zum Entwickeln gebaut, aber im Betrieb genauso nützlich — sie
sind der Grund, warum ein Bug-Report aus einer fremden Installation beantwortbar wird:

- **UDP-Mitschnitt** in der WebUI: die letzten N gesendeten Datagramme mit Zeitstempel,
  filterbar pro Gerät. Beantwortet „sendet die Bridge überhaupt etwas?" ohne Wireshark.
- **Kommando-Log**: eingehende HTTP-Aufrufe vom Miniserver mit Ergebnis. Beantwortet
  die Gegenrichtung.
- **Vorlagen-Vorschau**: vor dem Download zeigt die WebUI, welche Objekte und Befehle
  entstehen und wie viele.
- **Systemcheck**: IPv6 vorhanden, mDNS erreichbar, Dongle da, matter-server verbunden,
  Miniserver erreichbar. Jede Zeile grün oder rot mit konkretem Hinweis.
- **Fabric-Sicherung** (`GET /api/diagnostics/fabric-backup`, siehe 4.1): Download des
  matter-server-Datenverzeichnisses als Archiv. **Geschützt durch das Token seit
  Task 8** (siehe 4.1, 9.1 und den Docstring der Route selbst) — ohne gesetztes Token
  bleibt sie wie jede andere `/api`-Route offen, mit Warnung im Log beim Start.

---

## 11. Risiken

| Risiko | Bewertung | Gegenmaßnahme |
|---|---|---|
| Deployment-Komplexität durch OTBR (USB, IPv6, Host-Networking) | hoch, sicher eintretend | Ausführlicher Guide, Compose-Profile, Diagnoseseite in der WebUI, die IPv6/mDNS/Dongle prüft |
| Verlust der Fabric-Credentials | mittel, katastrophal | Backup-Export in der WebUI, Warnung im Guide |
| Vorlagen-Schema ändert sich mit Config-Version | niedrig | Golden-File-Tests, Schema versioniert |
| Multi-Admin-Einlernen verwirrt Nutzer | hoch | Inline-Anleitung je Ökosystem in der WebUI |
| UDP-Last bei Full-Resend vieler Signale | mittel | Rate-Limit, konfigurierbares Intervall |

---

## 12. Offene Punkte

1. Konkretes Funkmodul für die Referenz-Compose-Datei.
2. **Loxone-Lumitech-Codierung ungeklärt** (Task 5, 2026-09-02). Wie Loxone Helligkeit
   und Farbtemperatur im Lumitech-Ausgabemodus als eine Zahl codiert, ist in der
   offiziellen Loxone-Dokumentation nicht auffindbar — nur ein als Vermutung
   gekennzeichneter Forumsbeitrag existiert (siehe 7.3). `commands/translate.py`
   erwartet deshalb für Farbtemperatur bereits einen entpackten Kelvin-Wert statt der
   rohen Loxone-Zahl. Vor Task 6 (HTTP-Endpoint) klären, sonst kann dieser die rohe
   Zahl nicht zuverlässig entpacken. Die RGB-Codierung ist dagegen belegt (7.3).
3. **`subscribe()` abonniert Attribute nur statisch — und das kompoundiert mit
   `Runtime.invalidate_index()` zu einer stillen Sackgasse** (Task 8, 2026-09-02).
   `BridgeMatterClient.subscribe()` (`matter/client.py`) registriert beim Aufruf genau
   eine Upstream-Subscription je (Node, Attributpfad)-Paar, das zu diesem Zeitpunkt
   bekannt ist — begründet im Moduldocstring dort: `attr_path_filter` steuert nur, OB
   ein registrierter Callback feuert, nicht WAS ihm übergeben wird, und für
   `EventType.ATTRIBUTE_UPDATED` ist die gelieferte `data` einzig der neue Wert, ohne
   Node-ID oder Pfad — eine einzelne Wildcard-Subscription könnte ein solches Update
   deshalb keinem Gerät zuordnen. Ein Attributpfad, den ein Gerät ERST NACH diesem
   Aufruf neu meldet — nach einem Firmware-Update, das einen Cluster freischaltet, oder
   weil ein Gerät nachträglich kommissioniert wird — bekommt dadurch nie eine
   Subscription und liefert folglich nie ein Update.

   Das verzahnt sich mit einer zweiten, für sich genommen unabhängig wirkenden Grenze:
   `Runtime.invalidate_index()` (`loxone/runtime.py`) existiert genau für den Fall, dass
   jemand `Store.register_signals()` erneut für ein bereits laufendes Gerät aufruft, um
   ein neu hinzugekommenes Signal bekannt zu machen. Sie verwirft dabei aber nur
   `Runtime`s eigenen Cache bereits ABONNIERTER Pfade (`_signal_for`s
   `_signals`/`_indexed`) — sie registriert keine neue Upstream-Subscription und kann es
   auch nicht, sie kennt `BridgeMatterClient` gar nicht. Hat `subscribe()` den Pfad nie
   kennengelernt, erzeugt matter-server dafür überhaupt kein Event; es gibt für
   `invalidate_index()` folglich nichts zu verpassen. Auch `_signal_for`s eigenes
   Debug-Log (das bei einem unbekannten Signal feuert) sieht diesen Fall nie, weil
   `on_attribute`/`on_event` für einen nie abonnierten Pfad schlicht nie aufgerufen
   werden — nicht einmal auf Debug-Ebene steht dazu etwas im Log.

   Wer also "ein Signal zur Laufzeit an ein laufendes Gerät anhängen" allein mit
   `Store.register_signals()` gefolgt von `Runtime.invalidate_index(device_id)` bauen
   will, landet in genau dieser stillen Sackgasse: der Aufruf läuft fehlerfrei durch,
   der Cache wird sauber neu aufgebaut — aber es kommt nie ein Wert an, weil die
   eigentliche Lücke eine Ebene tiefer liegt, bei `subscribe()`, nicht bei
   `invalidate_index()`. Ein korrekter Fix braucht deshalb beides: nach
   `Store.register_signals()` muss NEBEN `Runtime.invalidate_index(device_id)` auch
   `BridgeMatterClient.subscribe()` für den betroffenen Node erneut laufen (oder gezielt
   um den neuen Pfad erweitert werden) — sich auf die Cache-Invalidierung allein zu
   verlassen reicht nicht. Für die anvisierte Nutzung dieser Phase (`connect()` liest
   den vollen Node-Cache, danach einmalig `subscribe()`, keine Laufzeit-
   Rekommissionierung) ist die Lücke hinnehmbar; sie ist aber ein offener Punkt, keine
   erledigte Aufgabe.
4. **Event-Zähler (`<key>_n`) sind prozesslokal und überleben einen Bruecken-Neustart
   nicht** (`Runtime`, `loxone/runtime.py`; Review-Fix I7, 2026-09-02). Spec 6.3 verkauft
   diesen Zähler als monotonen Wert, dessen Vorzug ist, dass ein verlorenes UDP-Datagramm
   ihn nur *springen* lässt, statt den Tastendruck zu verschlucken — Loxone-Logik soll auf
   ihn achten können, ohne je einen Druck zu verpassen. `Runtime.__init__` setzt
   `self._counters: dict[str, int] = {}` aber ohne jede Seedung, und der Zähler existiert
   nirgends außerhalb dieses Prozessspeichers — kein Store-Feld, kein `seed_from_snapshot`,
   kein `/resync`-Pfad. Ein Neustart der Bridge (Deployment, Absturz, Container-Neustart)
   setzt ihn deshalb auf 0 zurück, und der nächste Tastendruck sendet wieder `1`. Das ist
   nicht dieselbe Fehlerklasse, die 6.3 adressiert: ein VERLORENES Paket lässt den Zähler
   *steigen* (springt von z. B. 4 auf 6, immer noch erkennbar als "es gab einen Druck"), ein
   NEUSTART lässt ihn *fallen* (von 47 zurück auf 1) — eine Loxone-Logik, die auf "Zähler hat
   sich erhöht" wartet, verpasst diesen einen Druck nach jedem Neustart der Bridge
   vollständig, das genaue Gegenteil dessen, wofür der Zähler eingeführt wurde. Ein Fix
   bräuchte eines von zwei Dingen: entweder der Zähler wird persistiert (z. B. im `Store`,
   analog zu den Signalschlüsseln selbst, mit derselben Sorgfalt bei nebenläufigem Zugriff)
   und beim Start aus der Datenbank statt bei 0 wieder aufgenommen, oder die Loxone-seitige
   Logik überwacht den Zähler auf *Änderung* statt auf *Erhöhung* — Letzteres ist die
   einfachere Änderung, verlangt aber, dass jedes Config-Projekt, das diesen Zähler nutzt,
   das auch tatsächlich so verdrahtet. Weder das eine noch das andere ist in dieser Phase
   umgesetzt; unangetastet gelassen, weil das Verhalten nicht ungefragt geändert werden
   sollte, aber hier festgehalten, weil 6.3 sonst mehr verspricht, als die Implementierung
   hält.
5. **`MultiPressComplete` liefert nur die zwei Basissignale, nicht die in 6.3 versprochenen
   `_press2`/`_press3`/`_presscount`** (`export/signals.py`, `discovery.py`; Review-Fix
   I6/M13, 2026-09-02). Spec 6.3 verspricht wörtlich: „Bei `MultiPressComplete` zusätzlich
   `_press2`, `_press3` als eigene Impulse sowie `_presscount`." Tatsächlich exportiert
   `export/signals.py`s `to_inputs` für JEDES Event — `MultiPressComplete` eingeschlossen —
   ausschließlich die beiden generischen Signale, die auch jedes andere Event bekommt:
   `<key>` (digitaler Impuls) und `<key>_n` (monotoner Zähler, siehe Punkt 4 oben zu dessen
   eigener Lücke). Es gibt weder eine Sonderbehandlung für den Switch-Cluster-Event Nr. 6
   (`MultiPressComplete`, siehe `discovery.FEATURE_MAP_EVENTS`) noch einen Weg, aus dem
   rohen `MultiPressComplete`-Ereignis (das laut Matter-Spezifikation die Anzahl der
   erkannten Presses als Nutzdaten trägt) eine Presszahl herauszulesen und in eigene
   Impulse/einen `_presscount`-Wert zu übersetzen — `matter/paths.py`s Event-Erkennung
   liefert ohnehin nur den Pfad (`endpoint/cluster/event`), keine Nutzdaten, und
   `Runtime.on_event` kennt entsprechend keinen Parameter dafür. Ein Gerät mit
   Mehrfachdruck-Erkennung (z. B. IKEA-Taster mit Doppel-/Dreifachklick) liefert also
   `MultiPressComplete` als denselben einzelnen Impuls wie `InitialPress` — ein Doppelklick
   sieht in Loxone genauso aus wie ein einzelner Druck, nur der `_n`-Zähler zählt weiter.
   Ein Fix bräuchte: (a) das rohe `MultiPressComplete`-Ereignis mit seinen Nutzdaten statt
   nur seinem Pfad an `Runtime.on_event` durchzureichen, (b) eine Interpretation dieser
   Nutzdaten als Presszahl, und (c) eine Erweiterung von `export/signals.py`, die für dieses
   eine Event drei zusätzliche `LoxoneInput`s erzeugt statt der generischen zwei. Nicht in
   dieser Phase umgesetzt — hier festgehalten, damit 6.3 nicht mehr verspricht, als
   `export/signals.py` tatsächlich liefert.
6. **Rohes Attributschreiben ist bis zur Erlaubnisliste abgesichert, aber nicht an
   matter-server angebunden** (Task 4, 2026-09-02; siehe 8.4). `POST
   /api/signals/{key}/write` (`api/control.py`) lehnt jedes Attribut ab, das nicht auf
   der (bewusst kleinen, gegen ein echtes Gerät belegten) Erlaubnisliste
   `_WRITABLE_ATTRIBUTES` steht — das ist getestet
   (`test_raw_write_of_a_non_writable_attribute_is_refused`). Für ein *erlaubtes*
   Attribut gibt es aber noch keinen Weg zum Gerät: `BridgeMatterClient` hat kein
   `write_attribute` (anders als `send_command`, das über `matter_server.client.client.
   MatterClient.send_device_command` läuft), und `build_control_router(store, invoke)`
   nimmt dafür auch keinen zweiten Aufrufer entgegen — `invoke` ist per Typ
   (`Callable[[MatterCall], Awaitable[None]]`) auf Kommandos beschränkt, ein
   Attribut-Schreibzugriff ist keins. Die Route antwortet für ein erlaubtes Attribut
   deshalb ehrlich mit 501, statt einen Erfolg vorzutäuschen, der nichts bewirkt. Ein
   Fix bräuchte: (a) `BridgeMatterClient.write_attribute(node_id, attribute_path,
   value)` als dünnen Wrapper um `MatterClient.write_attribute` — nach demselben Muster
   wie `remove_node`/`set_thread_dataset` (Task 1) —, und (b) eine zweite,
   attributförmige Aufrufer-Schnittstelle für `build_control_router`, analog zu
   `invoke` für Kommandos. Nicht in dieser Phase umgesetzt — hier festgehalten, damit
   Ansicht 2 (8, „Schreibbare Attribute lassen sich hier roh setzen") nicht mehr
   verspricht, als die WebUI heute tatsächlich kann.
7. **Die von Hand gepflegte Erlaubnisliste (8.4) skaliert nicht über eine Handvoll
   Geräte hinaus — und es gibt inzwischen einen belegten Weg, sie durch eine Tabelle
   zu ersetzen** (Review-Fix Important #2, 2026-09-02). `_WRITABLE_ATTRIBUTES`
   (`api/control.py`) braucht heute für jedes Attribut, das irgendjemand jemals
   beschreiben möchte, einen eigenen, gegen ein echtes Gerät oder die
   Matter-Spezifikation belegten Eintrag — bei einer Installation mit mehr als einer
   Handvoll unterschiedlicher Gerätetypen wird das schnell zur Wartungslast, die
   keiner mehr pflegt, und eine ungepflegte Erlaubnisliste ist entweder zu eng
   (fehlende Bedienmöglichkeiten) oder, schlimmer, wird aus Bequemlichkeit durch eine
   Sperrliste ersetzt (siehe 8.4, wieso das die falsche Asymmetrie wäre). Wie 8.4 jetzt
   festhält, existiert die Schreibbarkeits-Information tatsächlich, in
   `chip/clusters/CHIPClusters.py`, nur ist das Modul in dieser Distribution nicht
   importierbar (`ImportError: cannot import name 'exceptions' from 'chip'`) und
   python-matter-server liest es nicht. Zwei Wege könnten das ändern: (a) eine
   spätere Version von `home_assistant_chip_clusters`/`chip` liefert das fehlende
   `chip/exceptions.py` mit, wodurch `ChipClusters.py` importierbar würde und seine
   `"writable"`-Flags zur Laufzeit abfragbar wären, oder (b) die Datei wird nicht
   importiert, sondern als reine Textdaten geparst (sie ist ein großes, aber
   syntaktisch reguläres Python-Literal) — ein Weg mit eigenem Risiko, weil er ein
   internes, nicht als Schnittstelle gedachtes Format einer Fremdbibliothek
   nachbildet und bei einer künftigen Version brechen kann, ohne dass ein Fehlschlag
   beim Import das anzeigt. Keiner der beiden Wege ist in dieser Phase umgesetzt;
   festgehalten, weil die heutige Erlaubnisliste eine bewusste, aber nicht die
   einzig mögliche Antwort auf 8.4s Befund ist.
8. **Die Oberfläche schickt das Token mit — offen bleibt nur, dass es keine Rolle
   für „nur ansehen" gibt** (Task 8, 2026-09-02; nachgebessert 2026-09-03). Der
   ursprüngliche Punkt hier — ein gesetztes Token sperrte den Betreiber aus seiner
   eigenen Oberfläche aus, weil `app.js` nirgends einen `Authorization`-Header setzte
   und es kein Feld für ein Token gab — ist **erledigt**: `requestJson` und
   `requestDownload` setzen den Header (das Token liegt im `localStorage` des
   Browsers, siehe README), die Kopfzeile trägt ein Passwortfeld zum Eintragen,
   Ersetzen und Löschen, eine 401 führt mit einem verständlichen Hinweis dorthin, und
   `/api/live` überträgt das Token als Subprotokoll `bearer, <Token>` (9.1). Die
   beiden Downloads (Export-ZIP, Fabric-Sicherung) laufen aus demselben Grund nicht
   mehr über ein `<a href>` — ein Link kann keinen Header tragen.

   Was tatsächlich offen bleibt: **das Token kennt nur „alles oder nichts".** Wer es
   hat, kann ansehen, schalten, einlernen, entfernen und die Fabric-Sicherung
   herunterladen; wer es nicht hat, sieht von `/api` nichts. Es gibt keine
   Nur-Lese-Rolle für jemanden, der bloß den Zustand betrachten soll, und kein
   zweites, eingeschränktes Token. Für die anvisierte Nutzung (ein Haushalt, eine
   Person, die die Brücke betreibt) ist das angemessen — für eine Installation, in der
   mehrere Personen unterschiedlich weit dürfen sollen, wäre es zu grob. Ebenfalls
   offen und bewusst nicht in dieser Phase gebaut: das Token ist ein statisches,
   dauerhaftes Geheimnis ohne Ablauf, ohne Rotation und ohne Widerruf einzelner
   Browser — ein Wechsel bedeutet, es überall neu einzutragen.
