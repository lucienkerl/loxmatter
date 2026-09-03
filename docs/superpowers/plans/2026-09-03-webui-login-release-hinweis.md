# Release-Hinweis: Login statt Token-Eingabe

**Was sich ändert.** Die Oberfläche hat jetzt eine Anmeldung mit Passwort.
Das Feld für das API-Token ist verschwunden.

**Was zu tun ist — sofort nach dem Ausrollen.** Öffne die Oberfläche
(`http://<Host>:8080/`) und vergib ein Passwort. Bis das geschehen ist,
liefert keine `/api`-Route Daten aus, und die Oberfläche zeigt nichts als
den Einrichtungsbildschirm.

**Warum sofort.** Die Ersteinrichtung verlangt keinen weiteren Nachweis —
wer zuerst kommt, vergibt das Passwort. Zwischen dem Update und deiner
Anmeldung kann also jeder, der die Brücke im Netz erreicht, sie übernehmen.
Bewusst so entschieden, damit die Einrichtung ohne Shell auf dem Host
möglich ist; der Preis ist dieses Fenster, und es sollte Minuten dauern und
nicht Tage.

**Was gleich bleibt.** `LOXMATTER_API_TOKEN` gilt weiter — als Weg für
Skripte und `curl`, nicht mehr für den Browser. Bestehende
Automatisierungen brechen durch dieses Update nicht ab, auch nicht vor der
Passwortvergabe. `/cmd` und `/resync` für den Miniserver bleiben wie immer
ohne jede Absicherung erreichbar.

**Passwort vergessen.** Im Referenz-Deployment (Docker) setzt `docker
compose exec loxmatter loxmatter set-password` **im laufenden Container**
es neu; bei einer Installation aus dem Quellcode entsprechend `uv run
loxmatter set-password` auf dem Host. Beides meldet dabei alle offenen
Sitzungen ab. **Wichtig bei einer containerisierten Installation:** die
Datenbank liegt dort typischerweise in einem benannten Docker-Volume und
ist über `LOXMATTER_STORE` nur *innerhalb* des Containers erreichbar —
`set-password` auf dem Host träfe dort eine andere, leere Datenbank und
meldete fälschlich Erfolg, ohne die eigentliche Brücke zu entsperren; der
Befehl bricht seit dem entsprechenden Fund deshalb mit einem klaren Fehler
ab, statt eine neue Datenbank anzulegen.

**Ein Hinweis zum Passwort.** Der Dienst spricht HTTP ohne Verschlüsselung;
das Passwort geht beim Anmelden im Klartext über das Netz. Nimm eines, das
du nirgendwo sonst benutzt — und lass es dir erzeugen, statt dir eines
auszudenken. Hinter der Anmeldung liegt auch die Fabric-Sicherung; acht
Zeichen tragen die nur, solange sie nicht zu raten sind.
