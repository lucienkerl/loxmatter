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

Bestätigt gegen die Referenzimplementierung des LoxBerry-Template-Builders
(<https://github.com/mschlenstedt/Loxberry/blob/master/libs/phplib/loxberry_loxonetemplatebuilder.php>):

```xml
<?xml version="1.0" encoding="utf-8"?>
<VirtualInUdp Title="Matter — Wohnzimmerlampe" Comment="Node 12" Address="192.168.1.50" Port="7000">
  <VirtualInUdpCmd Title="Wohnzimmer Temperatur" Comment="d12/ep1/TemperatureMeasurement"
    Address="" Check="d12_1_temp:\v"
    Signed="true" Analog="true"
    SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100"
    DefVal="0" MinVal="-2147483647" MaxVal="2147483647"/>
</VirtualInUdp>
```

```xml
<?xml version="1.0" encoding="utf-8"?>
<VirtualOut Title="Matter — Wohnzimmerlampe" Comment="Node 12" Address="http://192.168.1.50:8080"
            CmdInit="" CloseAfterSend="true" CmdSep="">
  <VirtualOutCmd ID="0" Title="Wohnzimmer Licht Helligkeit" Comment=""
    CmdOnMethod="GET" CmdOn="/cmd/d12_1_level/<v>" CmdOnHTTP="" CmdOnPost=""
    CmdOffMethod="GET" CmdOff="" CmdOffHTTP="" CmdOffPost=""
    Analog="true" Repeat="0" RepeatRate="0"/>
</VirtualOut>
```

**Achtung Escaping:** Der Loxone-Wertplatzhalter `<v>` in `CmdOn` steht in einem
XML-Attribut und muss als `&lt;v&gt;` geschrieben werden. Die Referenzimplementierung
tut das über `htmlspecialchars(..., ENT_XML1)`. Ein unescaptes `<v>` macht die Datei
unlesbar für Loxone Config. Der Platzhalter `\v` in `Check` ist davon nicht betroffen.

**Bedeutung von `Address`:** Im `VirtualInUdp` ist `Address` der *Absender-Filter* —
die IP der Bridge, von der Datagramme akzeptiert werden. `Port` ist der Port, auf dem
der Miniserver lauscht. Im `VirtualOut` ist `Address` dagegen die Ziel-Basis-URL der
Bridge.

Dateiformat: **UTF-8 mit BOM, CRLF-Zeilenenden.**
Dateinamen: `VIU_<name>.xml` und `VO_<name>.xml`.
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
Paar `VIU_Matter_System.xml` / `VO_Matter_System.xml`, das einmalig importiert wird.

Dateinamen: `VIU_Matter_<label>.xml` und `VO_Matter_<label>.xml`, mit auf ASCII
normalisiertem Gerätelabel.

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

**Folge für die Zahlenformatierung.** Von mW nach kW sind sechs Größenordnungen. Ein
Standby-Verbraucher mit 300 mW wird zu `0.0003` kW. Der UDP-Sender darf Werte deshalb
**nicht auf zwei Nachkommastellen runden** — sonst verschwindet alles unter 10 W in der
Null, und genau diese kleinen Dauerverbraucher will man in Loxone ja sehen. Festlegung:
Ausgabe mit bis zu **6 Nachkommastellen**, nachlaufende Nullen abgeschnitten. Das ist
ein eigener Testfall in der Skalierungs-Testsuite.

Farbe: Loxone liefert in Lumitech- bzw. RGB-Notation, Matter erwartet Hue/Saturation
oder CIE xy. Die Umrechnung liegt in `commands/` und ist beidseitig zu testen.

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
