# Der Updater-Beiwagen (Entwurf "Updates ueber die Oberflaeche einspielen",
# 2026-09-08, Abschnitt 6).
#
# Dieser Container haelt den Docker-Socket und ist damit root-gleichwertig
# auf dem Host. Abgesichert wird das durch Enge, nicht durch Rechte: keine
# Ports, kein Host-Netz (siehe Compose-Datei), ein einziges festes
# Arbeitsprogramm - und diese Basis hier gepinnt statt `latest`, damit ein
# Neubau nie eine Ueberraschung ist.
#
# Bewusst NICHT das loxmatter-Image mit anderem Einstiegspunkt: der
# Beiwagen darf nicht dasselbe Image sein wie das, das er austauscht, sonst
# ersetzt ein Update den Prozess, der es gerade ausfuehrt.
FROM alpine:3.20

# docker-cli-compose bringt das `docker compose`-Unterkommando mit - der
# Beiwagen ruft ausschliesslich Compose auf, nie ein nacktes `docker run`
# oder `docker build`. Der Grund steht seit 2026-09-03 in scripts/update.sh:
# der Dienst baut ueber seinen `build:`-Block sein eigenes Image, und ein
# daneben gebautes benutzt niemand.
#
# coreutils wegen `sort -V`: die Pruefung "nur vorwaerts" (Spec-Abschnitt
# 10, Regel 3) vergleicht semantische Versionen, und busybox' sort kann
# das nicht verlaesslich.
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
