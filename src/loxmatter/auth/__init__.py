"""Zugang zur Oberflaeche: Passwort, Sitzung, Drosselung.

Drei Module, absichtlich getrennt und absichtlich ohne FastAPI-Bezug:

- `passwords` rechnet Hashes und prueft sie. Kennt weder Datenbank noch HTTP.
- `sessions` legt Sitzungen an und prueft sie. Kennt den `AuthStore`, kein HTTP.
- `throttle` zaehlt Fehlversuche. Kennt gar nichts ausser der Uhr.

Der HTTP-Teil liegt in `loxmatter.api.auth`, der Waechter in
`loxmatter.loxone.server`. Diese Trennung ist der Grund, warum die Logik
hier ohne ASGI-Testclient pruefbar ist - und warum ein Geheimnis nur an den
Stellen auftauchen kann, die es wirklich brauchen.
"""
