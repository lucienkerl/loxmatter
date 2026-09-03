# Loxone-Vorlagen als Prüfsteine

Drei Dateien, mit unterschiedlicher Beweiskraft — das ist der Grund für diese
Notiz.

## `VO_Funktionierend.xml` — der Goldstandard für virtuelle Ausgänge

**Von Loxone Config selbst geschrieben**, nach einem Import, der nachweislich
funktioniert hat (Anwender, 3. September 2026). Was hier steht, ist keine
Ableitung und keine Vermutung.

Daraus stammen zwei Regeln, die wir vorher zweimal falsch hatten:

- `Analog="false"` genau dann, wenn ein **Aus-Befehl** gesetzt ist. Das ist der
  digitale Ausgang, bei dem Config den Haken „Als Digitalausgang verwenden"
  setzt und das Feld für den Aus-Befehl überhaupt erst anbietet. Ein Ausgang
  mit nur einem Befehl trägt `Analog="true"` — auch ohne Wert.
- Die vier Skalierungsattribute (`SourceValLow`, `DestValLow`,
  `SourceValHigh`, `DestValHigh`) schreibt Config **nur** bei den Ausgängen
  ohne Aus-Befehl.

BOM und CRLF sind nachträglich hergestellt: der Inhalt kam als Text durch die
Zwischenablage. Der Inhalt selbst ist unverändert.

## `VO_Referenz.xml` — nur noch für Aufbau und Attributnamen

Eine **von Hand bereinigte Ableitung** aus einer echten Installation (Phase 3).
Sie ist nützlich für Attributnamen, ihre Reihenfolge im Dokument und den
Aufbau — aber ihr `Analog`-Wert widerspricht dem, was Config oben schreibt.
Im Zweifel gilt Config.

Für den `Analog`-Wert **nicht** heranziehen. Genau daran ist am 3. September
2026 eine Korrektur in die falsche Richtung gegangen.

## `VIU_Referenz.xml` — virtuelle UDP-Eingänge

Ebenfalls eine bereinigte Ableitung. Enthält kein digitales Beispiel; was über
digitale Eingänge bekannt ist, stammt aus Beobachtungen am Miniserver und
steht in `src/loxmatter/export/signals.py` bei `LoxoneInput`.
