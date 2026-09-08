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

# Build identity (draft "Deploy updates via the UI",
# 2026-09-08, section 4). Set by CI, read by
# `loxmatter/version.py` and - for LOXMATTER_SCHEMA_VERSION - by the updater
# from stage 2, who reads it with `docker inspect` from an image that hasn't
# even started yet. That's exactly why it stands here as ENV and
# not only in the code: `docker inspect` doesn't see a Python constant.
#
# The defaults below make a manual build (`docker compose build`)
# possible without anyone needing to know four arguments - it then produces
# an image that honestly reports itself as "dev", instead of claiming
# a version it doesn't have.
ARG LOXMATTER_VERSION=dev
ARG LOXMATTER_COMMIT=""
ARG LOXMATTER_BUILT_AT=""
ARG LOXMATTER_SCHEMA_VERSION=""
ENV LOXMATTER_VERSION=${LOXMATTER_VERSION} \
    LOXMATTER_COMMIT=${LOXMATTER_COMMIT} \
    LOXMATTER_BUILT_AT=${LOXMATTER_BUILT_AT} \
    LOXMATTER_SCHEMA_VERSION=${LOXMATTER_SCHEMA_VERSION}

# Documentation only - `network_mode: host` in the Compose file (see
# deploy/testhost/docker-compose.yml) makes this port directly reachable,
# without Compose needing to publish it separately.
EXPOSE 8080

ENTRYPOINT ["loxmatter"]
CMD ["--help"]
