# Minimal image for `loxmatter run` (Phase 4, Review-Fix I5, 2026-09-02).
#
# Deliberately kept lean: this is the project's first Dockerfile draft, not
# yet the hardened production image from Spec 4.1 (that is Phase 6 -
# non-root user, minimal base, pinned digests, etc.). Unverified, because
# this environment has neither network access nor a Docker build available
# (see the review-fix report): no `docker build` was actually run. In
# particular, the system dependencies of `python-matter-server`/the chip SDK
# (native binary package) are only pulled in as far as the upstream
# reference image (ghcr.io/home-assistant-libs/python-matter-server)
# documents them (libavahi for mDNS discovery, D-Bus for BLE) - a complete
# list can only be confirmed against a real build log.
FROM python:3.12-slim

WORKDIR /app

# libavahi-client3: mDNS discovery, used by python-matter-server for
# commissioning (as in the reference image above). loxmatter itself doesn't
# need BLE (matter-server handles that exclusively), so everything around
# Bluetooth/D-Bus is deliberately left out here.
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
