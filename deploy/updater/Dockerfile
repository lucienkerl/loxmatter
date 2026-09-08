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

ENTRYPOINT ["/opt/loxmatter/entrypoint.sh"]
