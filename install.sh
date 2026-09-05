#!/bin/sh
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


# One-command installer for the loxmatter Docker stack.
#
#   curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
#
# Clones the repository, writes deploy/testhost/.env, starts the containers
# and then reports what still needs a human - it never silently repairs the
# host. Design: docs/superpowers/specs/2026-09-05-install-oneliner-design.md
#
# POSIX sh on purpose, not bash: the one-liner above ends in `| sh`, and
# /bin/sh is dash on Raspberry Pi OS - a bash script piped into sh dies at
# the first `[[`. Everything lives in a function and `main` runs on the very
# last line, so a download that is cut short defines functions and does
# nothing at all.
set -eu

REPO_URL="https://github.com/lucienkerl/loxmatter.git"
RAW_URL="https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh"

DRY_RUN=0
TARGET_DIR=""
STEP="starting up"
STACK_STARTED=0
HAVE_TTY=0
SUDO=""
MISSING_PACKAGES=""
NEED_DOCKER=0
MODE=""
DETECTED_RADIO=""
DOCKER_INSTALL_URL="https://get.docker.com"
DOCKER_SUDO=0
TEMP_FILE=""
CHECKOUT_EXISTED=0
STACK_DIR=""
ENV_FILE=""
ENV_IS_NEW=0
FINDINGS=""
PORT=8080

# ---------------------------------------------------------------- output --

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  %s\033[0m\n' "$*"; }

# Records what is being attempted, so the EXIT trap can say where it stopped.
step() { STEP="$1"; }

# An expected, fully explained stop. Exit code 2 tells the trap not to add
# its own "Failed while" noise on top.
die() {
  printf '\n\033[31mAborted: %s\033[0m\n' "$*" >&2
  exit 2
}

state_summary() {
  if [ "$STACK_STARTED" -eq 1 ]; then
    printf 'The stack in %s was started; run docker compose ps there to see it.\n' \
      "$TARGET_DIR/deploy/testhost"
  elif [ -d "$TARGET_DIR" ]; then
    printf 'The checkout at %s exists; nothing was started.\n' "$TARGET_DIR"
  else
    printf 'Nothing was created; %s does not exist.\n' "$TARGET_DIR"
  fi
}

on_exit() {
  code=$?
  if [ -n "$TEMP_FILE" ]; then
    rm -f "$TEMP_FILE"
  fi
  if [ "$code" -ne 0 ] && [ "$code" -ne 2 ]; then
    printf '\n\033[31mFailed while: %s\033[0m\n' "$STEP" >&2
    state_summary >&2
  fi
}

# ----------------------------------------------------------------- usage --

usage() {
  cat <<'EOF'
loxmatter installer - sets up the Docker stack in deploy/testhost.

Usage:
  curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
  curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh -s -- --dry-run
  sh install.sh [--dir PATH] [--dry-run]

Options:
  --dir PATH   where to clone the repository (default: $HOME/loxmatter)
  --dry-run    print every step without changing anything
  --help       show this text

These environment variables skip the matching question:
  LOXMATTER_DIR       where to clone
  LOXMATTER_MODE      thread | wifi
  MINISERVER_IP       address of the Loxone Miniserver
  RADIO_DEVICE        Thread radio, e.g. /dev/ttyUSB0 (thread mode only)
  RADIO_BAUDRATE      Thread radio baud rate (thread mode only)
  BACKBONE_IF         network interface for the border router (thread mode only)
  BLUETOOTH_ADAPTER   Bluetooth adapter id, e.g. 0
  LOXMATTER_API_TOKEN token for scripts and curl; generated when unset
EOF
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --dir)
        if [ $# -lt 2 ]; then die "--dir needs a path"; fi
        TARGET_DIR="$2"
        shift
        ;;
      --dir=*) TARGET_DIR="${1#--dir=}" ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown argument: $1 (allowed: --dir, --dry-run, --help)" ;;
    esac
    shift
  done
  if [ -z "$TARGET_DIR" ]; then
    TARGET_DIR="${LOXMATTER_DIR:-$HOME/loxmatter}"
  fi
}

# ------------------------------------------------------------- phase one --

check_platform() {
  step "checking the operating system"
  install_os="$(uname -s)"
  if [ "$install_os" != "Linux" ]; then
    die "This installer sets up the Docker stack, which needs Linux (found: $install_os).
On macOS, use the development path instead:
  git clone $REPO_URL && cd loxmatter && uv sync"
  fi
  install_arch="$(uname -m)"
  case "$install_arch" in
    aarch64|arm64|x86_64|amd64) : ;;
    *) die "Unsupported architecture: $install_arch (supported: aarch64, arm64, x86_64, amd64)" ;;
  esac
  note "Linux on $install_arch"
}

have() { command -v "$1" >/dev/null 2>&1; }

# stdin is the pipe when this runs as `curl ... | sh`, so every question has
# to go to the controlling terminal instead. Opening it in a subshell is the
# portable way to find out whether there is one at all - `test -r /dev/tty`
# can succeed on a device node that then refuses to open.
check_tty() {
  if ( exec </dev/tty ) 2>/dev/null; then
    HAVE_TTY=1
  else
    HAVE_TTY=0
    note "No terminal available; every value has to come from the environment."
  fi
}

# Asks on the terminal and echoes the answer. Without a terminal, or in a dry
# run, it echoes the default and asks nothing.
ask() {
  ask_prompt="$1"
  ask_default="$2"
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    printf '%s' "$ask_default"
    return 0
  fi
  if [ -n "$ask_default" ]; then
    printf '%s [%s]: ' "$ask_prompt" "$ask_default" >/dev/tty
  else
    printf '%s: ' "$ask_prompt" >/dev/tty
  fi
  if ! read -r ask_answer </dev/tty; then
    ask_answer=""
  fi
  if [ -z "$ask_answer" ]; then
    ask_answer="$ask_default"
  fi
  printf '%s' "$ask_answer"
}

check_privileges() {
  step "checking privileges"
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    warn "Running as root. The checkout and ~/loxmatter-backups will belong to"
    warn "root, and scripts/update.sh will need root from then on."
  elif have sudo; then
    SUDO="sudo"
  else
    SUDO=""
  fi
}

# Collects everything that is missing instead of stopping at the first gap -
# being told about git, then about curl, then about Docker on three separate
# runs is the opposite of a one-liner.
collect_missing() {
  step "checking which tools are present"
  MISSING_PACKAGES=""
  for tool in git curl openssl; do
    if ! have "$tool"; then
      MISSING_PACKAGES="$MISSING_PACKAGES $tool"
    fi
  done
  MISSING_PACKAGES="${MISSING_PACKAGES# }"
  NEED_DOCKER=0
  if ! have docker; then
    NEED_DOCKER=1
  elif ! docker compose version >/dev/null 2>&1; then
    die "docker is installed but the compose plugin is not.
On Debian and Ubuntu: apt-get install docker-compose-plugin
Then run this again."
  fi
}

check_can_install() {
  step "checking whether missing tools can be installed"
  if [ -z "$MISSING_PACKAGES" ] && [ "$NEED_DOCKER" -eq 0 ]; then
    return 0
  fi
  wanted="$MISSING_PACKAGES"
  if [ "$NEED_DOCKER" -eq 1 ]; then
    wanted="$wanted docker"
  fi
  wanted="${wanted# }"
  if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then
    die "Missing: $wanted
Installing these needs root, but this is not root and sudo is not available.
Install them yourself, then run this again."
  fi
  if ! have apt-get; then
    die "Missing: $wanted
This installer only knows apt-get (Debian, Ubuntu, Raspberry Pi OS).
Install them with your package manager, then run this again."
  fi
  note "Will install: $wanted"
}

detect_radio_device() {
  for candidate in /dev/ttyUSB* /dev/ttyACM*; do
    if [ -e "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
}

decide_mode() {
  step "deciding the operating mode"
  DETECTED_RADIO="$(detect_radio_device)"
  if [ -n "${LOXMATTER_MODE:-}" ]; then
    MODE="$LOXMATTER_MODE"
  else
    if [ -n "$DETECTED_RADIO" ]; then
      note "Found a possible Thread radio at $DETECTED_RADIO."
      mode_default="thread"
    else
      note "No Thread radio found at /dev/ttyUSB* or /dev/ttyACM*."
      mode_default="wifi"
    fi
    MODE="$(ask "Operating mode - 'thread' for Thread and WiFi, 'wifi' for WiFi and Ethernet only" "$mode_default")"
  fi
  case "$MODE" in
    thread|wifi) : ;;
    *) die "Operating mode must be 'thread' or 'wifi' (got: $MODE)" ;;
  esac
  note "Operating mode: $MODE"
}

# Strict IPv4 check. Octets are shape-checked with `case` BEFORE any numeric
# comparison: `[ n -gt 255 ]` on a number too large for the shell's integer
# type does not return false, it errors - and an errored test inside `if`
# reads as "not greater", which let 1.2.3.999999999999999999999 through as a
# valid address. Peeling with parameter expansion also avoids splitting on
# IFS, so the positional parameters stay untouched and no deliberate word
# splitting has to be suppressed.
valid_ipv4() {
  ipv4_rest="$1"
  ipv4_seen=0
  while [ "$ipv4_seen" -lt 4 ]; do
    if [ "$ipv4_seen" -eq 3 ]; then
      ipv4_octet="$ipv4_rest"
    else
      case "$ipv4_rest" in
        *.*)
          ipv4_octet="${ipv4_rest%%.*}"
          ipv4_rest="${ipv4_rest#*.}"
          ;;
        *) return 1 ;;
      esac
    fi
    # Rejects the empty string, non-digits, anything longer than three
    # digits, and leading zeros - all in one pattern list.
    case "$ipv4_octet" in
      0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) : ;;
      *) return 1 ;;
    esac
    if [ "$ipv4_octet" -gt 255 ]; then
      return 1
    fi
    ipv4_seen=$((ipv4_seen + 1))
  done
  return 0
}

# Reads a key out of an existing .env, so a second run does not ask again for
# something that is already configured.
env_file_value() {
  if [ -f "$TARGET_DIR/deploy/testhost/.env" ]; then
    awk -F= -v key="$1" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' \
      "$TARGET_DIR/deploy/testhost/.env"
  fi
}

# Anything that has no default and cannot be asked for has to stop the run
# HERE - before a single file is written.
check_config_source() {
  step "checking that the configuration can be obtained"
  if [ -z "${MINISERVER_IP:-}" ] && [ -z "$(env_file_value MINISERVER_IP)" ] &&
     [ "$HAVE_TTY" -eq 0 ]; then
    die "MINISERVER_IP is not set and there is no terminal to ask on.
Pass it in instead:
  curl -fsSL $RAW_URL | MINISERVER_IP=10.0.1.99 sh"
  fi
  # A malformed address has to stop the run here too - noticing it after the
  # clone would be exactly the "aborted halfway" this phase exists to prevent.
  if [ -n "${MINISERVER_IP:-}" ] && ! valid_ipv4 "$MINISERVER_IP"; then
    die "MINISERVER_IP is not a valid IPv4 address: '$MINISERVER_IP'"
  fi
  if [ "$MODE" = "thread" ] && [ -z "${RADIO_DEVICE:-}" ] &&
     [ -z "$(env_file_value RADIO_DEVICE)" ] && [ -z "$DETECTED_RADIO" ] &&
     [ "$HAVE_TTY" -eq 0 ]; then
    die "Thread mode was requested, but no radio was found at /dev/ttyUSB* or
/dev/ttyACM* and there is no terminal to ask on. Either plug the radio in,
pass RADIO_DEVICE=/dev/ttyUSB0, or use LOXMATTER_MODE=wifi."
  fi
}

# ------------------------------------------------------------- phase two --

# Runs a command as root, or prints it in a dry run. Everything that needs
# root goes through here, so a dry run cannot slip past by accident.
run_root() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: $*"
    return 0
  fi
  if [ -n "$SUDO" ]; then
    sudo "$@"
  else
    "$@"
  fi
}

install_packages() {
  if [ -z "$MISSING_PACKAGES" ]; then
    return 0
  fi
  step "installing $MISSING_PACKAGES"
  say "Installing missing tools: $MISSING_PACKAGES"
  note "This uses apt-get and needs root."
  run_root apt-get update ||
    die "apt-get update failed. Is this machine online?"
  # Deliberate word splitting: one package per argument.
  # shellcheck disable=SC2086
  run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y $MISSING_PACKAGES ||
    die "Installing $MISSING_PACKAGES failed. Nothing else was changed."
}

# How this script runs Docker commands that start or change things. The
# read-only probe in collect_missing calls `docker compose version` directly
# instead - it must still run during a dry run, and going through dk would
# suppress it.
dk() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: docker $*"
    return 0
  fi
  if [ "$DOCKER_SUDO" -eq 1 ]; then
    sudo docker "$@"
  else
    docker "$@"
  fi
}

install_docker() {
  if [ "$NEED_DOCKER" -eq 0 ]; then
    return 0
  fi
  step "installing Docker"
  say "Docker is not installed"
  note "Installing it from $DOCKER_INSTALL_URL."
  note "This needs root and adds Docker's package repository to this machine."
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: download $DOCKER_INSTALL_URL to a temporary file, then run it"
    return 0
  fi
  # Downloaded to a file first, NOT piped straight into sh. Without pipefail
  # a pipeline reports the status of its LAST command, and an `sh` reading the
  # empty stdin left by a failed download exits 0 - so `curl ... | sh || die`
  # reads a network failure as a successful install and walks on into
  # usermod, having promised it would not. Writing the file makes curl's own
  # status observable.
  TEMP_FILE="$(mktemp)" || die "Could not create a temporary file."
  if ! curl -fsSL "$DOCKER_INSTALL_URL" -o "$TEMP_FILE"; then
    rm -f "$TEMP_FILE"
    TEMP_FILE=""
    die "Could not download the Docker installer from $DOCKER_INSTALL_URL.
Is this machine online? Nothing was changed."
  fi
  # curl can exit 0 and still have delivered nothing - an empty body behind a
  # flaky proxy or CDN. `sh` on an empty script also exits 0, so without this
  # check the run would walk on into usermod and end with the same false
  # "compose does not work" diagnosis the exit-status check above exists to
  # prevent.
  if [ ! -s "$TEMP_FILE" ]; then
    rm -f "$TEMP_FILE"
    TEMP_FILE=""
    die "The download from $DOCKER_INSTALL_URL was empty. Nothing was changed."
  fi
  if ! run_root sh "$TEMP_FILE"; then
    rm -f "$TEMP_FILE"
    TEMP_FILE=""
    die "The Docker installer failed. Nothing else was changed."
  fi
  rm -f "$TEMP_FILE"
  TEMP_FILE=""
  # Checking the outcome, not the download: a truncated but syntactically
  # valid script runs cleanly and installs nothing, and no amount of
  # inspecting the file beforehand catches every such case.
  if ! have docker; then
    die "The installer from $DOCKER_INSTALL_URL ran but left no 'docker'
command. The download was probably incomplete. Nothing else was changed."
  fi
  if [ -n "$SUDO" ]; then
    docker_user="$(id -un)"
    run_root usermod -aG docker "$docker_user" ||
      warn "Could not add $docker_user to the 'docker' group."
    # The new group only takes effect after a new login session, so this run
    # cannot use plain `docker` - it would fail with a permission error right
    # after reporting success.
    DOCKER_SUDO=1
    warn "You are not in the 'docker' group in this session yet."
    note "This run continues with 'sudo docker'; log out and back in afterwards."
  fi
  # collect_missing could not probe for the compose plugin - docker was not
  # there to ask. It is now, and the stack cannot start without it.
  if ! dk compose version >/dev/null 2>&1; then
    die "Docker was installed, but 'docker compose' does not work.
Install the compose plugin (on Debian and Ubuntu: apt-get install docker-compose-plugin),
then run this again."
  fi
  note "docker compose is available"
}

# ----------------------------------------------------------- phase three --

# Either takes an existing checkout as-is, or clones a fresh one. Never pulls
# or otherwise touches an existing checkout: an update goes through
# scripts/update.sh and only after the human agrees, so this step cannot be
# the thing that quietly rewrites a checkout the human is relying on.
ensure_checkout() {
  step "getting the repository"
  STACK_DIR="$TARGET_DIR/deploy/testhost"
  if [ -d "$TARGET_DIR" ]; then
    CHECKOUT_EXISTED=1
    say "Using the existing checkout"
    note "$TARGET_DIR"
    if [ ! -f "$TARGET_DIR/Dockerfile" ] || [ ! -f "$STACK_DIR/docker-compose.yml" ]; then
      die "$TARGET_DIR exists but does not look like a loxmatter checkout
(no Dockerfile, or no deploy/testhost/docker-compose.yml).
Move it aside, or pass --dir with a different path."
    fi
    return 0
  fi
  say "Cloning the repository"
  note "$REPO_URL -> $TARGET_DIR"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: git clone --branch main $REPO_URL $TARGET_DIR"
    return 0
  fi
  git clone --branch main "$REPO_URL" "$TARGET_DIR" ||
    die "git clone failed. Is this machine online, and is $TARGET_DIR writable?"
}

# ------------------------------------------------------------ phase four --

env_file_has() { grep -q "^$1=" "$ENV_FILE" 2>/dev/null; }

# Replaces the line instead of appending a second definition. Compose would
# honour the last one either way, but whoever edits the file later would then
# be changing the wrong line - the reasoning is spelled out in
# deploy/testhost/README.md.
#
# Deliberately no awk: `awk -v value=...` runs the value through escape
# processing, so a backslash in it is silently eaten and `\t` becomes a real
# tab. Reading the file line by line passes every byte through untouched.
#
# Writes through $ENV_FILE.new and renames it into place so .env is either
# fully the old content or fully the new content, never half-written - `mv`
# on the same filesystem is atomic. That temporary file is tracked in the
# same TEMP_FILE the EXIT trap already cleans up: if the run is interrupted
# between the write and the rename, nothing is left lying next to .env.
env_set() {
  env_key="$1"
  env_value="$2"
  if ! env_file_has "$env_key"; then
    printf '%s=%s\n' "$env_key" "$env_value" >> "$ENV_FILE"
    return 0
  fi
  TEMP_FILE="$ENV_FILE.new"
  env_seen=0
  {
    while IFS= read -r env_line || [ -n "$env_line" ]; do
      case "$env_line" in
        "$env_key"=*)
          if [ "$env_seen" -eq 0 ]; then
            printf '%s=%s\n' "$env_key" "$env_value"
            env_seen=1
          else
            printf '%s\n' "$env_line"
          fi
          ;;
        *) printf '%s\n' "$env_line" ;;
      esac
    done
  } < "$ENV_FILE" > "$TEMP_FILE"
  mv "$TEMP_FILE" "$ENV_FILE"
  TEMP_FILE=""
}

ensure_env_value() {
  value_key="$1"
  value_prompt="$2"
  value_default="$3"
  value_required="$4"
  if [ "$ENV_IS_NEW" -eq 0 ]; then
    value_kept="$(env_file_value "$value_key")"
    if [ -n "$value_kept" ]; then
      note "$value_key=$value_kept (kept)"
      return 0
    fi
  fi
  # An environment variable of the same name skips the question entirely.
  value_override=""
  eval "value_override=\${$value_key:-}"
  if [ -n "$value_override" ]; then
    value_new="$value_override"
  else
    value_new="$(ask "$value_prompt" "$value_default")"
  fi
  if [ -z "$value_new" ] && [ "$value_required" -eq 1 ]; then
    die "$value_key needs a value and none could be obtained."
  fi
  env_set "$value_key" "$value_new"
  note "$value_key=$value_new"
}

ask_miniserver() {
  if [ "$ENV_IS_NEW" -eq 0 ]; then
    ms_kept="$(env_file_value MINISERVER_IP)"
    if [ -n "$ms_kept" ]; then
      note "MINISERVER_IP=$ms_kept (kept)"
      return 0
    fi
  fi
  ms_value="${MINISERVER_IP:-}"
  while [ -z "$ms_value" ] || ! valid_ipv4 "$ms_value"; do
    if [ "$HAVE_TTY" -eq 0 ]; then
      die "MINISERVER_IP is not a valid IPv4 address: '$ms_value'"
    fi
    ms_value="$(ask "IPv4 address of the Loxone Miniserver" "")"
  done
  env_set MINISERVER_IP "$ms_value"
  note "MINISERVER_IP=$ms_value"
}

detect_backbone_if() {
  ip route show default 2>/dev/null |
    awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }'
}

detect_bt_adapter() {
  for candidate in /sys/class/bluetooth/hci*; do
    if [ -e "$candidate" ]; then
      printf '%s' "${candidate##*/hci}"
      return 0
    fi
  done
  printf '0'
}

# The .env.example explains why the token has to be plain [0-9a-f]: it travels
# in an HTTP header and in a WebSocket subprotocol. /dev/urandom produces the
# same shape, so a missing openssl cannot sink an otherwise healthy install.
gen_token() {
  if have openssl; then
    token_value="$(openssl rand -hex 32 2>/dev/null || true)"
    if [ -n "$token_value" ]; then
      printf '%s' "$token_value"
      return 0
    fi
  fi
  od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
}

# An installation that predates the profiles has no COMPOSE_PROFILES line but
# does have an otbr container. Writing an empty value there would silently
# take its border router away on the next `compose up`.
configure_mode() {
  if [ "$ENV_IS_NEW" -eq 0 ] && env_file_has COMPOSE_PROFILES; then
    if [ -n "$(env_file_value COMPOSE_PROFILES)" ]; then
      MODE="thread"
    else
      MODE="wifi"
    fi
    note "COMPOSE_PROFILES kept, mode: $MODE"
    return 0
  fi
  if [ "$ENV_IS_NEW" -eq 0 ] && dk ps -a --format '{{.Names}}' 2>/dev/null | grep -qx otbr; then
    env_set COMPOSE_PROFILES thread
    MODE="thread"
    note "COMPOSE_PROFILES=thread (this installation already runs otbr)"
    return 0
  fi
  if [ "$MODE" = "thread" ]; then
    env_set COMPOSE_PROFILES thread
    note "COMPOSE_PROFILES=thread"
  else
    env_set COMPOSE_PROFILES ""
    note "COMPOSE_PROFILES= (WiFi and Ethernet only, no Thread border router)"
  fi
}

configure() {
  step "writing the configuration"
  ENV_FILE="$STACK_DIR/.env"
  say "Configuration"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would write $ENV_FILE for mode '$MODE'"
    return 0
  fi
  ENV_IS_NEW=0
  if [ ! -f "$ENV_FILE" ]; then
    cp "$STACK_DIR/.env.example" "$ENV_FILE" || die "Could not create $ENV_FILE"
    ENV_IS_NEW=1
  fi
  configure_mode
  if [ "$MODE" = "thread" ]; then
    ensure_env_value RADIO_DEVICE "Thread radio device" "$DETECTED_RADIO" 1
    ensure_env_value RADIO_BAUDRATE "Thread radio baud rate" "460800" 1
    ensure_env_value BACKBONE_IF "Network interface for the border router" \
      "$(detect_backbone_if)" 1
  fi
  ensure_env_value BLUETOOTH_ADAPTER "Bluetooth adapter id for BLE commissioning" \
    "$(detect_bt_adapter)" 0
  ask_miniserver
  if [ "$ENV_IS_NEW" -eq 1 ] || [ -z "$(env_file_value LOXMATTER_API_TOKEN)" ]; then
    env_set LOXMATTER_API_TOKEN "${LOXMATTER_API_TOKEN:-$(gen_token)}"
    note "LOXMATTER_API_TOKEN generated"
  else
    note "LOXMATTER_API_TOKEN kept"
  fi
}

# ------------------------------------------------------------ phase five --

start_stack() {
  step "starting the stack"
  say "Starting the containers"
  if [ "$MODE" = "thread" ]; then
    note "otbr, matter-server, loxmatter"
  else
    note "matter-server, loxmatter - no Thread border router in this mode"
  fi
  note "The first build takes several minutes on a Raspberry Pi. It is not stuck."
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: docker compose up -d --build in $STACK_DIR"
    return 0
  fi
  mkdir -p "$STACK_DIR/data"
  # No --profile here on purpose: COMPOSE_PROFILES lives in .env, which Compose
  # reads by itself. Every later call in this directory - by hand, or from
  # scripts/update.sh - then picks the same services without a flag to remember.
  ( cd "$STACK_DIR" && dk compose up -d --build ) ||
    die "Could not start the stack in $STACK_DIR. The checkout and .env are in place; fix the
cause and run this again. The logs are in:
  cd $STACK_DIR && docker compose logs"
  STACK_STARTED=1
}

# ------------------------------------------------------------- phase six --

# Findings are reported, never repaired: unblocking rfkill needs a privileged
# container, and the OTBR workaround is kernel specific and would kill working
# processes on hosts that do not need it.
add_finding() {
  if [ -z "$FINDINGS" ]; then
    FINDINGS="$1"
  else
    FINDINGS="$FINDINGS

$1"
  fi
}

# Reads the port from the line after `- --listen`, and only when there is
# exactly one such place in the file. A heuristic over YAML that silently
# returns the WRONG number is worse than one that gives up: check_health
# would probe it for its whole budget and then blame the service for not
# answering.
stack_port() {
  port_value="$(awk '
    /^[[:space:]]*-[[:space:]]*--listen[[:space:]]*$/ { want = 1; next }
    want { gsub(/[^0-9]/, ""); if ($0 != "") { print; found++ }; want = 0 }
    END { exit (found != 1) }
  ' "$STACK_DIR/docker-compose.yml")" || port_value=""
  case "$port_value" in
    ""|*[!0-9]*)
      warn "Could not read a single listen port from docker-compose.yml; assuming 8080."
      port_value=8080
      ;;
  esac
  printf '%s' "$port_value"
}

check_health() {
  step "waiting for the bridge to answer"
  PORT="$(stack_port)"
  health_url="http://127.0.0.1:$PORT/health"
  health_ok=0
  health_tries=0
  while [ "$health_tries" -lt 20 ]; do
    if curl -fsS -m 3 "$health_url" >/dev/null 2>&1; then
      health_ok=1
      break
    fi
    health_tries=$((health_tries + 1))
    sleep 1
  done
  if [ "$health_ok" -ne 1 ]; then
    printf '\n\033[31m%s does not answer. Last lines from the log:\033[0m\n' "$health_url"
    dk logs --tail 30 loxmatter 2>&1 || true
    die "The containers are up but the bridge does not report healthy."
  fi
  note "$health_url answers"
}

check_containers() {
  step "checking the containers"
  expected="matter-server loxmatter"
  if [ "$MODE" = "thread" ]; then
    expected="otbr $expected"
  fi
  running="$( ( cd "$STACK_DIR" && dk compose ps --services ) 2>/dev/null || true)"
  for service in $expected; do
    if ! printf '%s\n' "$running" | grep -qx "$service"; then
      add_finding "Service '$service' is not running. Look at:
  cd $STACK_DIR && docker compose logs $service"
    fi
  done
}

check_rfkill() {
  step "checking the Bluetooth adapter"
  for entry in /sys/class/rfkill/rfkill*; do
    if [ ! -r "$entry/type" ]; then
      continue
    fi
    if [ "$(cat "$entry/type")" != "bluetooth" ]; then
      continue
    fi
    if [ "$(cat "$entry/soft" 2>/dev/null || echo 0)" = "1" ]; then
      rfkill_name="${entry##*/}"
      add_finding "Bluetooth is rfkill soft-blocked ($rfkill_name), so commissioning
over BLE will fail. This needs root, which is why it is not done here:
  docker run --rm --privileged -v /sys:/sys alpine \\
    sh -c 'echo 0 > /sys/class/rfkill/$rfkill_name/soft'
It only has to be done once; the unblock survives reboots."
    fi
    return 0
  done
}

check_thread() {
  if [ "$MODE" != "thread" ]; then
    return 0
  fi
  step "checking the Thread network"
  # The same test scripts/otbr-watchdog.sh and the "System" view use: scope 00
  # means routed, wpan* is OTBR's Thread interface.
  if awk '$4 == "00" && $6 ~ /^wpan/ { found = 1 } END { exit !found }' \
      /proc/net/if_inet6 2>/dev/null; then
    note "Thread interface is up"
    return 0
  fi
  add_finding "No Thread interface (wpan*) yet. On a Raspberry Pi kernel the OTBR
image's start-stop-daemon never finishes and otbr-agent is never exec'd, so
this has to be re-applied after every 'docker compose up':
  docker exec otbr sh -c 'rm -f /var/run/otbr-agent.pid /var/run/otbr-web.pid'
  docker exec -d otbr /usr/sbin/otbr-agent -I wpan0 -B $(env_file_value BACKBONE_IF) -d7 \\
    --rest-listen-address 127.0.0.1 \\
    'spinel+hdlc+uart://$(env_file_value RADIO_DEVICE)?uart-baudrate=$(env_file_value RADIO_BAUDRATE)'
  docker exec otbr ot-ctl ifconfig up
  docker exec otbr ot-ctl thread start
The state goes from 'detached' to 'leader' after about 15 seconds. See
deploy/testhost/README.md, 'start-stop-daemon haengt auf dem Pi-Kernel'."
}

run_checks() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would check /health, the containers, Bluetooth and Thread"
    return 0
  fi
  say "Checking"
  check_health
  check_containers
  check_rfkill
  check_thread
  if [ -n "$FINDINGS" ]; then
    say "Findings"
    printf '%s\n' "$FINDINGS"
  fi
}

# Only offered, never done on the way past: scripts/update.sh backs up the
# signal database first, and those keys are the wiring in the Loxone
# configuration. An installer that updated in passing would skip that backup.
offer_update() {
  if [ "$CHECKOUT_EXISTED" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  step "checking for updates"
  if ! git -C "$TARGET_DIR" fetch --quiet origin main 2>/dev/null; then
    note "Could not reach GitHub; skipping the update check."
    return 0
  fi
  behind="$(git -C "$TARGET_DIR" rev-list --count HEAD..FETCH_HEAD 2>/dev/null || echo 0)"
  # A non-numeric value would make `[ -le ]` error rather than return false,
  # and an errored test inside `if` reads as false - the run would then
  # announce an update using whatever rev-list printed.
  case "$behind" in
    ""|*[!0-9]*) behind=0 ;;
  esac
  if [ "$behind" -le 0 ]; then
    return 0
  fi
  say "$behind new commits are available"
  if [ "$DOCKER_SUDO" -eq 1 ]; then
    # Docker was installed in this very run, so the docker group is not in
    # effect yet - and scripts/update.sh calls docker without sudo. Offering
    # something that cannot work is worse than not offering it.
    note "Log out and back in first, then run: $TARGET_DIR/scripts/update.sh"
    return 0
  fi
  if [ "$HAVE_TTY" -eq 0 ]; then
    note "Apply them with: $TARGET_DIR/scripts/update.sh"
    return 0
  fi
  update_answer="$(ask "Update now? It backs up the signal database first [y/N]" "N")"
  case "$update_answer" in
    y|Y|yes|Yes) "$TARGET_DIR/scripts/update.sh" ;;
    *) note "Left as it is. Run $TARGET_DIR/scripts/update.sh when you want it." ;;
  esac
}

# ------------------------------------------------------------------ main --

main() {
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  parse_args "$@"
  say "loxmatter installer"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "Dry run: every step is printed, nothing is changed."
  fi
  check_platform
  check_tty
  check_privileges
  collect_missing
  check_can_install
  decide_mode
  check_config_source
  install_packages
  install_docker
  ensure_checkout
  configure
  start_stack
  run_checks
  offer_update
}

main "$@"
