# Minimales Image fuer `loxmatter run` (Phase 4, Review-Fix I5, 2026-09-02).
#
# Bewusst schlank gehalten: dies ist der erste Dockerfile-Entwurf des Projekts,
# noch nicht das gehaertete Produktions-Image aus Spec 4.1 (das ist Phase 6 -
# Nicht-root-User, minimale Basis, gepinnte Digests, o.ae.). Ungeprueft, weil
# in dieser Umgebung weder Netzwerk noch Docker-Build zur Verfuegung stehen
# (siehe Review-Fix-Report): kein `docker build` wurde tatsaechlich
# ausgefuehrt. Insbesondere die Systemabhaengigkeiten von
# `python-matter-server`/dem chip-SDK (natives Binaerpaket) sind nur so weit
# nachgezogen, wie das Upstream-Referenz-Image
# (ghcr.io/home-assistant-libs/python-matter-server) sie dokumentiert
# (libavahi fuer mDNS-Discovery, D-Bus fuer BLE) - eine vollstaendige Liste
# ist erst am echten Build-Log zu belegen.
#
# NACHTRAG (8. September 2026): DIESE BEGRUENDUNG IST ENTFALLEN. loxmatter
# haengt seither nicht mehr an `python-matter-server`, sondern an
# `matter-python-client`, und das ist ein reines Python-Wheel
# (`py3-none-any`, Abhaengigkeiten aiohttp/dacite/orjson) ohne native
# Chip-Komponente. Ein natives Binaerpaket, dessen Systemabhaengigkeiten hier
# nachzuziehen waeren, gibt es nicht mehr, und das genannte Referenz-Image ist
# archiviert. Was das fuer libavahi-client3 heisst, steht unten bei der
# apt-Zeile.
FROM python:3.12-slim

WORKDIR /app

# libavahi-client3: mDNS-Discovery, von python-matter-server fuer die
# Kommissionierung genutzt (wie im Referenz-Image oben). loxmatter selbst
# braucht kein BLE (das macht ausschliesslich matter-server), deshalb fehlt
# hier bewusst alles rund um Bluetooth/D-Bus.
#
# NACHTRAG (8. September 2026): DER GRUND OBEN IST WEG, DAS PAKET BLEIBT.
# `matter-python-client` ist ein reines Python-Wheel und bringt kein natives
# Chip-SDK mehr mit, das gegen libavahi gebaut waere - die Zeile begruendet
# sich also nicht mehr aus der Abhaengigkeit. Sie steht trotzdem weiter hier:
# ob der Dienst ohne das Paket startet, ist an dieser Maschine nicht zu
# klaeren (kein Docker-Build, siehe oben), und ein Umzug, der nebenbei eine
# Systemabhaengigkeit zieht, waere genau die Vermischung, die dieser Branch
# sonst vermeidet. ZU PRUEFEN am ersten echten Build: die Zeile entfernen,
# Image bauen, `loxmatter run` starten - laeuft es, kann sie weg.
#
# Zu erwarten ist genau das, denn die verbliebene Begruendung ist duenn:
# loxmatter selbst loest keine mDNS-Namen auf, es spricht matter-server ueber
# eine feste Websocket-Adresse an. Erwartung ist aber keine Messung.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libavahi-client3 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir uv==0.6.* \
    && uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:${PATH}"

# Die Bau-Identitaet (Entwurf "Updates ueber die Oberflaeche einspielen",
# 2026-09-08, Abschnitt 4). Gesetzt von der CI, gelesen von
# `loxmatter/version.py` und - fuer LOXMATTER_SCHEMA_VERSION - vom Updater
# aus Stufe 2, der sie mit `docker inspect` aus einem Image liest, das er
# noch gar nicht gestartet hat. Genau deshalb steht sie hier als ENV und
# nicht nur im Code: ein `docker inspect` sieht keine Python-Konstante.
#
# Die Vorgaben unten machen einen Bau von Hand (`docker compose build`)
# moeglich, ohne dass jemand vier Argumente kennen muss - er ergibt dann
# ein Image, das sich ehrlich als "dev" ausgibt, statt eine Version zu
# behaupten, die es nicht ist.
ARG LOXMATTER_VERSION=dev
ARG LOXMATTER_COMMIT=""
ARG LOXMATTER_BUILT_AT=""
ARG LOXMATTER_SCHEMA_VERSION=""
ENV LOXMATTER_VERSION=${LOXMATTER_VERSION} \
    LOXMATTER_COMMIT=${LOXMATTER_COMMIT} \
    LOXMATTER_BUILT_AT=${LOXMATTER_BUILT_AT} \
    LOXMATTER_SCHEMA_VERSION=${LOXMATTER_SCHEMA_VERSION}

# Nur Dokumentation - `network_mode: host` im Compose-File (siehe
# deploy/testhost/docker-compose.yml) macht diesen Port direkt erreichbar,
# ohne dass Compose ihn extra veroeffentlichen muesste.
EXPOSE 8080

ENTRYPOINT ["loxmatter"]
CMD ["--help"]
