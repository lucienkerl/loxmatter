# Signalauswahl: relevante Werte statt vollständiger Datenblätter

Entwurf, 3. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md), insbesondere
dessen Abschnitte 3.5 (generische Zerlegung), 5 (Datenmodell), 6.2
(Schlüssel), 7.3 (Skalierung) und 8 (WebUI).

## 1. Das Problem

Eine IKEA-Steckdose liefert 159 Signale, von denen 109 technisch auf einen
Loxone-UDP-Eingang abbildbar sind. Davon sind **vier** das, wofür man eine
Steckdose kauft: Ein/Aus, Spannung, Strom, Leistung. 55 der 109 sind
Thread-Funkzähler, weitere acht Seriennummern und Firmwareversionen.

Ein Anwender bekommt damit eine Vorlage mit 110 virtuellen Eingängen für ein
Gerät mit einem Schalter. Das war die Beschwerde, mit der dieses Projekt
begann ("sonst hat man nachher 200 Eingänge und kann die nicht einem
einzelnen Gerät zuweisen") — sie ist durch den geräteweisen Export gelöst
worden, aber nur innerhalb eines Geräts, nicht für das einzelne Gerät selbst.

Zweitens fehlt der Wert, wegen dem man eine *messende* Steckdose kauft: der
kumulative Verbrauch in kWh. Matter liefert ihn als Struktur (Energiewert
plus Zeitstempel), und die generische Zerlegung verwirft Strukturen. Die
momentane Leistung kommt an, der Zählerstand nie.

## 2. Was dieser Entwurf nicht antastet

Die Grundwette aus Abschnitt 3.5 des Hauptdokuments: das Werkzeug kennt
Matter nicht im Detail, es zerlegt generisch, damit ein Gerätetyp
funktioniert, den beim Bau niemand vorhergesehen hat. In Phase 1 an zwei
echten Geräten bestätigt.

Eine Positivliste ("nur was ich kenne, kommt durch") wurde deshalb verworfen.
Sie ergäbe heute das sauberste Ergebnis und ließe morgen ein fremdes Gerät
stumm — ohne dass jemand merkte, dass etwas fehlt.

Ebenfalls unverändert: `bridge_alive`, `d<n>_online`, die
Kommando-Erlaubnisliste und die Ereignisse. Tastendrücke bleiben
ausdrücklich Standardausstattung; sie waren die erste Anforderung des
Projekts überhaupt.

## 3. Zwei Begriffe, die getrennt bleiben

| Begriff | Frage | heute |
|---|---|---|
| `Exportability` | Lässt sich der Wert überhaupt auf einen UDP-Eingang abbilden? | vorhanden |
| **`Relevance`** | Will ein Anwender ihn standardmäßig? | **neu** |

Ein Thread-Funkzähler ist exportierbar, aber nicht relevant. Eine Struktur
ohne benanntes Element ist nicht exportierbar, egal wie relevant sie wäre.
Die beiden zu vermischen wäre der Fehler, den später niemand mehr
auseinanderklamüsert.

`Relevance` entscheidet nur über den **Vorgabewert** der bereits
existierenden Spalte `exported`. Die Mechanik — pro Signal umschaltbar über
`PATCH /api/signals/{key}`, ausgewertet in `export.signals.to_inputs` —
bleibt, wie sie ist.

## 4. Die Auswahlregel

Drei Schichten, nach abnehmender Allgemeingültigkeit und zunehmendem
Pflegeaufwand.

### 4.1 Aufbau: der Gerätetyp je Endpunkt

Der Descriptor-Cluster (29) trägt auf jedem Endpunkt das Attribut
`DeviceTypeList` — eine Liste standardisierter Gerätetyp-Nummern aus der
Matter Device Library. Keine Herstellerangabe, kein Freitext: ein Gerät ohne
diese Angabe wird nicht zertifiziert.

An den beiden Prüfgeräten:

```
Steckdose   Endpunkt 0: OTA Requestor, Root Node
            Endpunkt 1: On/Off Plug-in Unit
            Endpunkt 2: Electrical Sensor

Taster      Endpunkt 0: OTA Requestor, Power Source, Root Node
            Endpunkt 1: Generic Switch
            Endpunkt 2: Generic Switch
```

Daraus die Regel:

> Ein Endpunkt, der **Root Node** oder **OTA Requestor** deklariert, ist
> Verwaltung — seine Signale sind standardmäßig aus.
> Jeder andere Endpunkt ist das Gerät — an.
>
> Ausnahme auf einem Verwaltungs-Endpunkt: die Cluster eines dort ebenfalls
> deklarierten Nutz-Gerätetyps. In der Praxis ist das **Power Source**, also
> der Batteriestand.
>
> Auf jedem Endpunkt aus: **Identify (3)**, **Groups (4)**, **Descriptor
> (29)** — Matter-Innereien ohne Bedeutung für eine Hausautomation.

Das erklärt die Ausnahme, statt sie zu setzen: der Batteriestand des Tasters
liegt nicht zufällig auf Endpunkt 0, sondern weil der Standard den
Gerätetyp Power Source dort vorsieht, und das Gerät sagt es selbst.

**Zu prüfen vor der Umsetzung:** die drei Gerätetyp-Nummern (Root Node, OTA
Requestor, Power Source) stehen in der Matter Device Library, **nicht** im
installierten SDK — dessen Katalog umfasst Cluster, keine Gerätetypen. Sie
sind gegen die Spezifikation zu belegen und nicht aus diesem Dokument zu
übernehmen. Die Zuordnung ist hier aus den Prüfgeräten erschlossen: der
Taster deklariert genau einen Typ mehr als die Steckdose, und der Taster ist
das batteriebetriebene Gerät.

**Grenzfall:** deklariert ein Endpunkt gar keinen Gerätetyp (nicht
konformes Gerät, leere Liste), gilt er als Nutz-Endpunkt. Im Zweifel ein
Eingang zu viel, nie ein fehlender Wert.

### 4.2 Namen: der Cluster-Katalog des SDK

`chip.clusters.Objects` — bereits Abhängigkeit dieses Projekts, schon in
Gebrauch für das Senden von Kommandos — enthält alle 140 Standardcluster mit
allen Attributnamen, generiert aus dem Datenmodell der CSA. `c47_a12` heißt
dort `BatPercentRemaining`.

Signale bekommen ihren Titel künftig von dort, wenn die eigene Profiltabelle
nichts Besseres weiß. Kosten: null Pflege, Wirkung: jedes Standardattribut
jedes Herstellers hat einen lesbaren Namen statt `cXX_aYY`.

Der Katalog liefert **Namen, keine Relevanz**. `StartUpOnOff` ist ein
ordentlicher Standardname für ein Attribut, das niemand in Loxone haben will.

### 4.3 Bedeutung: die eigene Profiltabelle

`profiles/clusters.yaml` bleibt für das, was das SDK prinzipiell nicht wissen
kann:

- die Loxone-seitige Einheit und Umrechnung (W → kW, Abschnitt 7.3),
- welches Element einer Struktur der Wert ist (Abschnitt 5 unten),
- die Feinauswahl innerhalb eines bekannten Clusters.

**Feinauswahl:** kennt die Tabelle einen Cluster, so sind von ihm
standardmäßig nur die dort **benannten** Attribute relevant. Kennt sie ihn
nicht, bleibt der ganze Cluster relevant (Abschnitt 2).

Die Tabelle benennt heute schon genau die richtigen:

| Cluster | benannt | ergibt |
|---|---|---|
| 6 onoff | 0 | `onoff`; die Konfigurationswerte 0x4000–0x4002 fallen weg |
| 144 power | 4, 5, 8 | `voltage`, `current`, `power`; 21 Messbereiche fallen weg |
| 145 energy | 1, 2 | Verbrauch rein und raus |
| 59 switch | 0, 1 + Ereignisse | `press`, `longpress`, `multipress` … |

Neu aufzunehmen ist Cluster 47 (PowerSource) mit `BatPercentRemaining` als
`battery` in Prozent. Matter zählt dort in halben Prozent — der Faktor
gehört in die Tabelle, nicht in den Kopf des Anwenders.

### 4.4 Ergebnis

| | heute | danach |
|---|---:|---:|
| Steckdose | 109 | **5** — Ein/Aus, Spannung, Strom, Leistung, Verbrauch |
| Taster | 122 | **17** — beide Wippen vollständig, Batteriestand |

An den echten Geräten durchgerechnet, nicht geschätzt: die Strukturregel
allein bringt 109 → 18 und 122 → 27; die Feinauswahl aus 4.3 den Rest.

Die 17 des Tasters sind je Wippe `positions`, `position` und die sechs
Ereignisse (`press`, `longpress`, `shortrelease`, `longrelease`,
`multipress_ongoing`, `multipress`), plus `battery`. Die Ereignisse
unterliegen der Feinauswahl aus 4.3 nicht — sie sind in der Tabelle ohnehin
namentlich geführt, und ein verworfenes Ereignis wäre ein Tastendruck, der
in Loxone nie ankommt.

## 5. Zahlen aus Strukturen

Die Profiltabelle bekommt ein optionales Feld `field`:

```yaml
145:
  name: energy
  attributes:
    # field: 0 = EnergyMeasurementStruct.energy, gegen das installierte SDK
    # belegt (chip.clusters.Objects.ElectricalEnergyMeasurement.Structs).
    1: {slug: energy_imported, field: 0, unit: "kWh", scale: 1.0e-6}
```

`field` ist eine **Feldnummer, kein Name**: matter-server liefert Strukturen
als Wörterbuch mit dem Feld-Tag als Schlüssel, und zwar als Zeichenkette —
der Descriptor-Cluster kommt etwa als `[{"0": 18, "1": 1}]` an. Eine
Implementierung, die auf `value["energy"]` zugreift, findet nichts.

Ist `field` gesetzt und der Wert eine Struktur, die dieses Feld als Zahl
enthält, so ist das Signal analog exportierbar; der Rest der Struktur
(Zeitstempel) bleibt weg. Fehlt das Element oder ist es keine Zahl, bleibt
das Signal nicht exportierbar — **es wird nicht geraten**. Eine erfundene
Zahl an einem echten Energiebaustein wäre schlimmer als ein fehlender Wert.

Nur ein Cluster, den die Tabelle kennt, darf das. Eine unbekannte Struktur
bleibt unbekannt.

Betroffen sind `to_loxone_value` (Laufzeit) und die Exportierbarkeits-
Einstufung in der Zerlegung — beide müssen dieselbe Entscheidung treffen,
sonst meldet die Oberfläche einen Wert, den der Export nicht kennt.

## 6. Bestand: Migration ohne Schlüsseländerung

Die Signalzeilen speichern Titel, Einheit und Exportierbarkeit **mit ab**.
Eine Tabellenerweiterung wirkt deshalb nicht von selbst rückwirkend.

Die Migration (Schema v3) leitet bei jedem bestehenden Signal Titel,
Einheit, Exportierbarkeit und den Vorgabewert von `exported` neu ab.

**Der Schlüssel bleibt unangetastet.** Ein Taster, der vor dem Update
eingelernt wurde, behält `d2_0_c47_a12` und heißt ab dann „battery" in
Prozent. Ein nach dem Update eingelernter bekommt `d2_0_battery`. Zwei
Schlüssel für denselben Wert ist hässlich; ein umbenannter Schlüssel wäre
ein stillschweigend toter Funktionsbaustein in einer fremden Config, und das
ist die eine Sache, die dieses Werkzeug niemals tun darf (Abschnitt 6.2 des
Hauptdokuments).

Die Migration hält je Gerät fest, wie viele Eingänge wegfallen. Die
Oberfläche zeigt das vor dem nächsten Export, mit dem Hinweis, dass die
entsprechenden Objekte in Loxone verwaisen — dieselbe Warnung wie beim
Entfernen eines Geräts.

Bewusst rückwirkend statt nur für Neugeräte: zwei Regelsätze nebeneinander —
alte Geräte so, neue anders — wären auf Dauer niemandem zu erklären, und der
Unterschied hinge am Einlerndatum, das niemand im Kopf hat.

## 7. Oberfläche

Die Signalliste bekommt zwei Blöcke: **Funktional** (offen) und **Experte**
(zugeklappt, mit Anzahl). Ein Schalter „Experten-Signale anzeigen" klappt
den zweiten auf. Jedes Signal behält seinen eigenen Exportieren-Haken; der
Experten-Block ist genau der Ort, an dem man einen bewusst anschaltet, etwa
einen Thread-Zähler zur Fehlersuche.

Die Gerätekachel zeigt künftig die funktionalen Signale statt der sechs mit
der kleinsten Cluster-Nummer (offener Punkt aus dem Abschluss-Review von
Phase 5: heute sind das NetworkCommissioning und BasicInformation, also
weder Ein/Aus noch Leistung).

Die Exportvorschau nennt zusätzlich, wie viele Signale als Experte
ausgeblendet sind, und warnt, wenn ein Export weniger Eingänge erzeugt als
der vorige.

## 8. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| `DeviceTypeList` fehlt oder ist leer | Endpunkt gilt als Nutz-Endpunkt (an) |
| `DeviceTypeList` nicht lesbar (Struktur unerwartet) | wie oben, plus Logeintrag |
| Gerätetyp unbekannt | kein Verwaltungstyp ⇒ an |
| Cluster unbekannt | vollständig an (Abschnitt 2) |
| `field` gesetzt, Element fehlt | nicht exportierbar, kein Raten |
| Migration schlägt bei einem Signal fehl | Zeile bleibt unverändert, Logeintrag; kein Abbruch |

## 9. Prüfung

Die beiden echten Geräte liegen als Abbilder im Repo und sind die
Prüfsteine. Alle Tests laufen ohne Hardware und ohne Netz.

- Die Steckdose ergibt **namentlich** `onoff`, `voltage`, `current`,
  `power`, `energy_imported` — nicht nur „weniger als vorher". Ein Test auf
  die Anzahl ginge bei der falschen Auswahl durch.
- Der Taster ergibt beide Wippen vollständig samt `multipress` und den
  Batteriestand. Der Fall, an dem sich zeigt, ob die Regel zu gierig ist.
- Ein unbekannter Cluster auf einem Nutz-Endpunkt bleibt vollständig
  erhalten. Der Test, der die Grundwette bewacht.
- Ein Endpunkt ohne `DeviceTypeList` gilt als Nutz-Endpunkt.
- Die Migration läuft gegen eine nach der alten Regel gebaute Datenbank und
  belegt: **kein einziger Schlüssel ändert sich**, wohl aber Titel und
  Einheit.
- Eine Struktur ohne das benannte Element bleibt nicht exportierbar.
- Laufzeit und Zerlegung treffen bei Strukturen dieselbe Entscheidung.

## 10. Offene Punkte

1. Die drei Gerätetyp-Nummern sind gegen die Matter Device Library zu
   belegen (Abschnitt 4.1), nicht aus diesem Dokument zu übernehmen.
2. Ob die Feinauswahl aus 4.3 auch für Cluster gelten soll, deren Namen aus
   dem SDK-Katalog stammen, ist offen. Vorschlag: nein — der Katalog kennt
   keine Relevanz, und „benannt" hieße dann „alle".
3. Ein Gerät mit mehreren Nutz-Gerätetypen auf einem Endpunkt (Brücken,
   Kombigeräte) ist ungeprüft; die Regel behandelt es korrekt als Nutz-
   Endpunkt, aber es lag keines vor.
4. Ob `Relevance` später vom Anwender überschreibbar sein soll (eigene
   Sperrliste), bleibt offen. Bis jemand danach fragt: nein.
