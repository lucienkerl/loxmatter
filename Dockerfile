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
FROM python:3.12-slim

WORKDIR /app

# libavahi-client3: mDNS-Discovery, von python-matter-server fuer die
# Kommissionierung genutzt (wie im Referenz-Image oben). loxmatter selbst
# braucht kein BLE (das macht ausschliesslich matter-server), deshalb fehlt
# hier bewusst alles rund um Bluetooth/D-Bus.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libavahi-client3 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir uv==0.6.* \
    && uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:${PATH}"

# Nur Dokumentation - `network_mode: host` im Compose-File (siehe
# deploy/testhost/docker-compose.yml) macht diesen Port direkt erreichbar,
# ohne dass Compose ihn extra veroeffentlichen muesste.
EXPOSE 8080

ENTRYPOINT ["loxmatter"]
CMD ["--help"]
