# The updater sidecar (design "Applying updates through the web UI",
# 2026-09-08, section 6).
#
# This container holds the Docker socket and is thereby root-equivalent
# on the host. That is secured through narrowness, not through privileges:
# no ports, no host network (see the Compose file), a single fixed
# work program - and this base pinned instead of `latest`, so a rebuild
# is never a surprise.
#
# Deliberately NOT the loxmatter image with a different entrypoint: the
# sidecar must not be the same image as the one it replaces, or an update
# would replace the process that is running it.
FROM alpine:3.20

# docker-cli-compose brings the `docker compose` subcommand along - the
# sidecar only ever invokes Compose, never a bare `docker run` or
# `docker build`. The reason has been in scripts/update.sh since 2026-09-03:
# the service builds its own image via its `build:` block, and one built
# alongside it would go unused.
#
# coreutils for `sort -V`: the "forward only" check (spec section
# 10, rule 3) compares semantic versions, and busybox's sort cannot
# do that reliably.
RUN apk add --no-cache \
      docker-cli \
      docker-cli-compose \
      git \
      curl \
      jq \
      coreutils \
      tar

COPY entrypoint.sh update-once.sh /opt/loxmatter/
RUN chmod +x /opt/loxmatter/entrypoint.sh /opt/loxmatter/update-once.sh

# Build identity, mirroring the bridge's own Dockerfile (see its
# LOXMATTER_VERSION ARG/ENV pair and the comment there) - until now this
# image carried NONE of that, so a running sidecar had no way to say what
# version of itself it was. That silence stopped being harmless once the
# sidecar's self-replacement was removed (update-once.sh, near the end of
# the success branch - measured on the maintainer's Pi to corrupt its own
# container instead of updating it): without a self-replacement, a
# sidecar can now drift arbitrarily far behind the bridge it serves with
# nothing to notice. ENV, not just ARG, for the same reason the bridge's
# own comment gives - `docker inspect`/the container's own environment
# can read an ENV, never a build-time-only ARG, and this sidecar reads
# its own version the same way (see `UPDATER_VERSION` in update-once.sh).
#
# The default "dev" is for a manual build of a current checkout with no
# build-arg supplied, same as the bridge - it honestly reports itself as
# unpublished rather than claiming a version it does not have. A sidecar
# built before this ARG existed at all carries no such ENV in the first
# place, and no default here can retroactively give it one - update-once.sh
# treats that absence as "unknown", never as "dev" or "current".
ARG LOXMATTER_UPDATER_VERSION=dev
ENV LOXMATTER_UPDATER_VERSION=${LOXMATTER_UPDATER_VERSION}

ENTRYPOINT ["/opt/loxmatter/entrypoint.sh"]
