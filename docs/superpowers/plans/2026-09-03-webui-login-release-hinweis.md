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

**Passwort vergessen.** `uv run loxmatter set-password` auf dem Host setzt
es neu und meldet alle offenen Sitzungen ab.

**Ein Hinweis zum Passwort.** Der Dienst spricht HTTP ohne Verschlüsselung;
das Passwort geht beim Anmelden im Klartext über das Netz. Nimm eines, das
du nirgendwo sonst benutzt.
