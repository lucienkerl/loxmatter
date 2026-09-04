# Periodischer Resend: Opt-in statt Rundumschlag

Entwurf, 4. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md) und knüpft an
die Unterscheidung aus
[der Signalauswahl](2026-09-03-signalauswahl-design.md#3-zwei-begriffe-die-getrennt-bleiben)
zwischen Exportierbarkeit und Relevanz an — hier kommt eine dritte,
unabhängige Signal-Eigenschaft dazu.

## 1. Das Problem

`Runtime._resend_loop` ([runtime.py:473-481](../../../src/loxmatter/loxone/runtime.py))
ruft alle `resend_seconds` (fix 300s) `resend_all()` auf, das *jeden*
bekannten Wert mit `force=True` erneut sendet — unabhängig davon, ob er sich
geändert hat. `UdpSender.send` ([sender.py:149-179](../../../src/loxmatter/loxone/sender.py))
sichert jeden Versand, echten wie erzwungenen, über **einen** gemeinsamen
`asyncio.Lock` und **einen** gemeinsamen Rate-Limiter (50/s) ab.

Bei vielen eingelernten Geräten wird der Voll-Resend dadurch zu einem
Burst, der mehrere Sekunden dauert (bei 300 Signalen: 6s). Ein echter
Steuerbefehl, der in diesem Fenster eintrifft, wartet auf denselben Lock und
kann sich damit um bis zu die volle Burst-Dauer verzögern. Das Problem
wächst linear mit der Geräteanzahl. Ein längeres Intervall würde den Burst
nur seltener, nicht kleiner machen.

Die meisten Signale brauchen den periodischen Resend vermutlich gar nicht.
Er existiert vermutlich als Schutz gegen unbemerkten Paketverlust / einen
Miniserver-Neustart zwischen zwei änderungsgetriebenen Sendungen — relevant
ist das typischerweise nur für wenige, gezielt ausgewählte Signale (z. B.
solche, auf die eine Loxone-Signalisierung mit Timeout reagiert), nicht für
alle.

## 2. Zwei Mechanismen, die getrennt bleiben

| Mechanismus | Zweck | betroffen von diesem Entwurf? |
|---|---|---|
| Heartbeat (`bridge_alive`, 30s, [runtime.py:448-471](../../../src/loxmatter/loxone/runtime.py)) | Lebenszeichen der Bridge selbst, ein globaler Schlüssel | nein, unverändert |
| Voll-Resend (`resend_all`, 300s) | Re-Sync einzelner *Werte* gegen Paketverlust | ja, wird auf Opt-in umgestellt |

Der Heartbeat ist kein `StoredSignal` und bleibt außen vor.

## 3. Die Lösung

Ein neues, drittes unabhängiges Flag pro Signal — `resend` — neben
`exported` und `functional`. Nur Signale mit `resend = true` werden noch
periodisch (zwangsweise) erneut gesendet; alle anderen ausschließlich bei
Änderung, wie heute schon für alle. Das Resend-Intervall selbst wird eine
zur Laufzeit über die WebUI änderbare Einstellung statt einer festen
Konstante.

Default für jedes Signal, bestehend wie neu: `resend = false`. Nach diesem
Update wird also zunächst **nichts** mehr automatisch periodisch resent, bis
der Nutzer bewusst welche markiert — bewusst analog zur Migrationsfrage in
der Signalauswahl (Abschnitt 6 dort), nur hier ohne Bestandsschutz-Problem,
weil `resend` ein komplett neues Feld ohne Vorgeschichte ist.

## 4. Datenmodell

`signal`-Tabelle ([store.py:109-123](../../../src/loxmatter/model/store.py)):
neue Spalte `resend INTEGER NOT NULL DEFAULT 0`, per `_add_column_if_missing`
wie die bestehenden Migrationen. `StoredSignal` bekommt ein Feld
`resend: bool`. Neue Methode `Store.set_resend(key, value)`, Geschwister von
`set_exported`.

Das Resend-Intervall ist keine Signal-Eigenschaft, sondern eine einzelne
globale Einstellung. Dafür existiert bereits die generische
`setting`-Tabelle ([store.py:135-138](../../../src/loxmatter/model/store.py)),
über die z. B. `LocaleStore` die Sprache ablegt
([locale_store.py](../../../src/loxmatter/model/locale_store.py)). Ein
analoger schmaler Wrapper (Arbeitstitel `RuntimeSettingsStore`) bekommt
`get_resend_interval() -> float` (Default 300.0, greift also identisch zum
heutigen Verhalten, solange niemand etwas ändert) und
`set_resend_interval(seconds: float)`.

## 5. API

`PATCH /api/signals/{key}` ([devices.py:239-240](../../../src/loxmatter/api/devices.py))
bekommt ein optionales Feld `resend: bool | None`, gleiches Muster wie
`exported`.

Neuer Endpunkt für das Intervall, z. B. `GET/PATCH /api/settings/resend-interval`
(oder eingehängt in einen bereits vorhandenen/künftigen generischeren
Settings-Endpunkt, falls einer entsteht — Detailentscheidung der
Umsetzung). Validierung: Zahl größer als ein sinnvolles Minimum (z. B.
≥ 10s), um ein versehentliches Lahmlegen durch ein zu kurzes Intervall zu
verhindern.

## 6. Runtime-Verhalten

**Korrektur gegenüber der ursprünglichen Fassung dieses Abschnitts:**
`resend_all()` ([runtime.py:362-396](../../../src/loxmatter/loxone/runtime.py))
wird nicht nur vom periodischen Timer aufgerufen, sondern auch beim
Bridge-Start (`cli.py`, direkt nach `seed_from_snapshot`) und vom
`/resync`-Endpunkt (`server.py`) — beides Fälle, die ausdrücklich *jeden*
bekannten Wert wiederherstellen müssen (Spec 6.4, Zustands-Wiederherstellung
nach einem Miniserver-Neustart). `resend_all()` selbst auf `resend = true`
zu filtern würde also nicht nur den periodischen Timer einschränken, sondern
auch `/resync` und den Bridge-Start — nach einem echten
Miniserver-Neustart blieben dann die meisten virtuellen Eingänge auf ihrem
Defaultwert stehen, genau das Problem, das Spec 6.4 verhindern soll.

**Deshalb bleibt `resend_all()` unverändert** (weiterhin ein voller Restore
aller bekannten Werte, benutzt von `/resync` und dem Bridge-Start). Eine neue
Methode `resend_marked()` filtert auf `resend = true` und wird
ausschließlich von `_resend_loop` aufgerufen; beide teilen sich intern die
bestehende, bereits gegen ein Race abgesicherte Sende-Logik (Wert je
Schlüssel erst unmittelbar vor dem Senden aus `_last_values` nachlesen,
siehe Kommentar an `resend_all()`), nur mit unterschiedlicher Schlüsselmenge.

`_resend_loop` liest die konfigurierte Intervalldauer nicht mehr einmalig
beim Start, sondern bei laufendem Betrieb wiederholt aus dem Store (kurzer
Polling-Takt, z. B. alle 5s prüfen, ob die konfigurierte Zeit seit dem
letzten `resend_marked()`-Lauf um ist). Eine Änderung über die WebUI wirkt
damit innerhalb weniger Sekunden, ohne Prozess-Neustart — kein
Event/Wecker-Mechanismus nötig, ein einfacher Poll reicht angesichts der
Größenordnung (Sekunden, nicht Millisekunden).

## 7. Ausdrücklich außerhalb des Zuschnitts

Synthetische, nicht in `StoredSignal` geführte Schlüssel — Erreichbarkeits-
Status `d<id>_online` und Pulszähler (`_n`-Suffix) — bekommen kein
`resend`-Flag. Sie werden weiterhin ausschließlich bei Änderung gesendet,
nie periodisch. Entscheidung bewusst getroffen, um den Eingriff klein zu
halten; kann bei Bedarf später nachgezogen werden.

Kein CLI-Flag für das Intervall — es lebt ausschließlich als
WebUI/API-Einstellung, analog zu Sprache und Passwort.

## 8. Oberfläche

Neue Checkbox „Resend" pro Signal-Zeile neben der bestehenden „Exported"-
Checkbox ([web/index.html:427](../../../src/loxmatter/web/index.html)),
gleiche PATCH-Interaktion beim Umschalten. Neues Eingabefeld für das
Intervall in Sekunden im Einstellungsbereich der WebUI, PATCH beim Ändern,
mit Anzeige/Validierung des Minimums aus Abschnitt 5.

## 9. Prüfung

- Ein Signal mit `resend = false` (Default) wird von `resend_marked()` nicht
  erfasst, auch wenn sein Wert seit Langem unverändert ist.
- Ein Signal mit `resend = true` erscheint bei jedem `resend_marked()`-Lauf,
  unabhängig vom Änderungsstatus.
- `resend_all()` bleibt davon unberührt: es erfasst weiterhin JEDEN
  bekannten Wert, unabhängig vom `resend`-Flag - `/resync` und der
  Bridge-Start dürfen sich darauf verlassen (siehe Abschnitt 6).
- `d<id>_online` und Pulszähler-Schlüssel tauchen nie in `resend_marked()`
  auf, selbst wenn (versehentlich) versucht wird, sie zu markieren.
- Eine Änderung des Intervalls über `PATCH /api/settings/resend-interval`
  wirkt sich innerhalb weniger Sekunden auf den Takt von `_resend_loop` aus,
  ohne Neustart.
- Migration: eine Bestandsdatenbank ohne `resend`-Spalte bekommt sie beim
  Öffnen automatisch hinzu, alle Zeilen mit `resend = false`.
- `PATCH /api/signals/{key}` mit `resend` gesetzt ändert ausschließlich
  dieses Feld, `exported`/`functional`/Schlüssel bleiben unberührt.

## 10. Offene Punkte

1. Ob Online-Status und Pulszähler später ebenfalls ein `resend`-Flag
   bekommen sollen, bleibt offen. Bis jemand danach fragt: nein (Abschnitt 7).
2. Der genaue Pfad/Name des neuen Settings-Endpunkts (Abschnitt 5) ist eine
   Umsetzungsdetail-Entscheidung, keine Design-Entscheidung dieses Entwurfs.
3. Ob ein zu niedrig gewähltes Intervall (z. B. 10s bei vielen markierten
   Signalen) serverseitig zusätzlich gegen die aktuelle Anzahl markierter
   Signale geprüft werden sollte (Schutz vor einem erneuten, nur kleineren
   Burst-Problem), ist nicht entschieden. Vorschlag für die Umsetzung: fürs
   Erste nur das feste Minimum aus Abschnitt 5, keine dynamische Prüfung —
   YAGNI, bis sich zeigt, dass es gebraucht wird.
