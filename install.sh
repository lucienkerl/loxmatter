#!/bin/sh
# loxmatter - connects Matter devices to a Loxone Miniserver.
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
DOCKER_INSTALL_URL="https://get.docker.com"
DOCKER_SUDO=0
TEMP_FILE=""
STACK_DIR=""
ENV_FILE=""
ENV_IS_NEW=0
CONFIG_WRITTEN=0
FINDINGS=""
PORT=8080
HEALTHY=1
# Overridable so tests can point this at a fabricated directory instead of
# the real /sys, which does not exist off Linux and is not writable anyway.
RFKILL_DIR="${RFKILL_DIR:-/sys/class/rfkill}"
# Where questions are read from. LOXMATTER_TTY exists for the tests only:
# they point it at a file of answers, one per line. Not in --help on purpose.
TTY_PATH="${LOXMATTER_TTY:-/dev/tty}"
# Overridable for the same reason as RFKILL_DIR: the tests point these at
# fabricated directories instead of the host's real /dev and /sys.
SERIAL_BY_ID_DIR="${SERIAL_BY_ID_DIR:-/dev/serial/by-id}"
SERIAL_DEV_DIR="${SERIAL_DEV_DIR:-/dev}"
SERIAL_CANDIDATES=""
CHOICE=""
# What phase one decided, for phase four to write. Empty means "nothing to
# write": either an existing .env keeps its value, or the mode does not use it.
CHOSEN_RADIO=""
CHOSEN_BACKBONE=""
# Overridable so tests can point this at a fabricated directory instead of
# the real /sys/class/bluetooth, which does not exist off Linux.
BT_SYS_DIR="${BT_SYS_DIR:-/sys/class/bluetooth}"
BT_ADAPTERS=""
CHOSEN_BT=""
CHOSEN_MS=""
MS_PROBLEM=""
TAB="$(printf '\t')"

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
    printf 'The stack in %s was started; run %s compose ps there to see it.\n' \
      "$TARGET_DIR/deploy/testhost" "$(docker_cmd)"
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
  LOXMATTER_MODE      thread | wifi (wifi skips the Thread stick menu)
  MINISERVER_IP       address of the Loxone Miniserver
  RADIO_DEVICE        Thread stick, e.g. /dev/serial/by-id/usb-... (means thread mode)
  RADIO_BAUDRATE      Thread stick baud rate; 460800 when unset, never asked
  BACKBONE_IF         network interface for the border router (thread mode only)
  BLUETOOTH_ADAPTER   Bluetooth adapter, e.g. 0 for hci0
  LOXMATTER_API_TOKEN token for scripts and curl; generated when unset
EOF
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --dir)
        if [ $# -lt 2 ]; then die "--dir needs a path"; fi
        case "$2" in
          -*) die "--dir needs a path, got what looks like another option: $2" ;;
        esac
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
  if ( exec <"$TTY_PATH" ) 2>/dev/null; then
    HAVE_TTY=1
    # Opened once and kept open: ask() runs inside $(...), and a subshell
    # shares this descriptor's offset with its parent - so a file of answers
    # is read line after line, instead of its first line on every question.
    exec 3<"$TTY_PATH"
  else
    HAVE_TTY=0
    note "No terminal available; every value has to come from the environment."
  fi
}

# Prompts go to the terminal the answers come from - except in the tests,
# where that is a file of answers that must not be written to.
tty_prompt() {
  if [ -n "${LOXMATTER_TTY:-}" ]; then
    printf '%s' "$1" >&2
  else
    printf '%s' "$1" >/dev/tty
  fi
}

# Asks on the terminal and echoes the answer. Without a terminal, or in a dry
# run, it echoes the default and asks nothing. Returns 1 when the terminal
# gives no more input: echoing the default there would let a caller that
# rejects the default ask again forever.
ask() {
  ask_prompt="$1"
  ask_default="$2"
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    printf '%s' "$ask_default"
    return 0
  fi
  if [ -n "$ask_default" ]; then
    tty_prompt "$ask_prompt [$ask_default]: "
  else
    tty_prompt "$ask_prompt: "
  fi
  if ! read -r ask_answer <&3; then
    return 1
  fi
  if [ -z "$ask_answer" ]; then
    ask_answer="$ask_default"
  fi
  printf '%s' "$ask_answer"
}

# Asks for a number from a menu the caller has already printed, repeats on
# anything else, and leaves the number in CHOICE. Called directly, never in
# $(...) - a subshell would take CHOICE with it. $4 is 1 when 0 is a valid
# answer. At most two digits: a menu never has a hundred lines, and a longer
# number would reach `[ -le ]` as something the shell cannot compare.
choose() {
  choose_prompt="$1"
  choose_default="$2"
  choose_max="$3"
  choose_zero="$4"
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    note "Taking $choose_default - nothing is asked without a terminal or in a dry run."
  fi
  while :; do
    CHOICE="$(ask "$choose_prompt" "$choose_default")" ||
      die "The terminal closed before this was answered: $choose_prompt"
    case "$CHOICE" in
      [0-9]|[0-9][0-9])
        if [ "$CHOICE" -le "$choose_max" ]; then
          if [ "$CHOICE" -ge 1 ] || [ "$choose_zero" -eq 1 ]; then
            return 0
          fi
        fi
        ;;
    esac
    warn "Please answer with one of the numbers shown."
  done
}

count_lines() { printf '%s' "$1" | grep -c . || true; }

nth_line() { printf '%s' "$1" | sed -n "${2}p"; }

check_privileges() {
  step "checking privileges"
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    warn "Running as root. The checkout and ~/loxmatter-backups will belong to"
    warn "root."
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

# Sets SERIAL_CANDIDATES to the USB serial devices a Thread stick could be,
# one path per line. /dev/serial/by-id comes first: its names say what the
# stick is, and they survive a reboot that swaps ttyUSB0 and ttyUSB1 - the
# same reason the Radios card offers them. `-e` follows the link, so a
# by-id link left behind by a pulled stick is skipped.
list_serial_candidates() {
  SERIAL_CANDIDATES=""
  for candidate in "$SERIAL_BY_ID_DIR"/*; do
    if [ -e "$candidate" ]; then
      SERIAL_CANDIDATES="$SERIAL_CANDIDATES$candidate
"
    fi
  done
  if [ -n "$SERIAL_CANDIDATES" ]; then
    return 0
  fi
  for candidate in "$SERIAL_DEV_DIR"/ttyUSB* "$SERIAL_DEV_DIR"/ttyACM*; do
    if [ -e "$candidate" ]; then
      SERIAL_CANDIDATES="$SERIAL_CANDIDATES$candidate
"
    fi
  done
}

serial_label() {
  case "$1" in
    "$SERIAL_BY_ID_DIR"/*) printf '%s' "${1##*/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# No "Thread" or "Zigbee" label next to a stick: the firmware cannot be seen
# from the name. The maintainer's own Thread stick is a SONOFF Dongle Plus
# MG24 - a name radios/fingerprints.py knows as a Zigbee coordinator.
# $1 is 1 when "None" is offered; without it the default is the first stick.
show_thread_menu() {
  say "Thread stick"
  note "Thread devices need a USB stick running OpenThread RCP firmware."
  note "A Zigbee stick does not belong here - set that up on the Radios card"
  note "of the web interface after the installation."
  printf '\n'
  tm_count=0
  while IFS= read -r tm_path; do
    if [ -z "$tm_path" ]; then
      continue
    fi
    tm_count=$((tm_count + 1))
    note "  $tm_count) $(serial_label "$tm_path")"
  done <<EOF
$SERIAL_CANDIDATES
EOF
  tm_default=1
  if [ "$1" -eq 1 ]; then
    note "  0) None - WiFi and Ethernet only (Thread can be switched on later"
    note "     on the Radios card)"
    # Two sticks cannot be told apart by name, so the default never picks
    # one of them silently.
    if [ "$tm_count" -ne 1 ]; then
      tm_default=0
    fi
  fi
  printf '\n'
  choose "Which one is the Thread stick?" "$tm_default" "$tm_count" "$1"
}

decide_mode() {
  step "choosing the Thread stick"
  if [ -n "${LOXMATTER_MODE:-}" ]; then
    MODE="$LOXMATTER_MODE"
    case "$MODE" in
      thread|wifi) : ;;
      *) die "Operating mode must be 'thread' or 'wifi' (got: $MODE)" ;;
    esac
  elif [ -f "$TARGET_DIR/deploy/testhost/.env" ]; then
    # A second run keeps what is configured. configure_mode reads the mode
    # from COMPOSE_PROFILES, or from a running otbr container when that line
    # is missing, and would overrule an answer given here anyway.
    if [ -n "$(env_file_value COMPOSE_PROFILES)" ]; then
      MODE="thread"
    else
      MODE="wifi"
    fi
    note "Thread: kept from $TARGET_DIR/deploy/testhost/.env."
    note "Change the Thread stick on the Radios card of the web interface."
    note "Operating mode: $MODE"
    return 0
  elif [ -n "${RADIO_DEVICE:-}" ]; then
    MODE="thread"
  else
    list_serial_candidates
    if [ -z "$SERIAL_CANDIDATES" ]; then
      note "No USB stick found - installing for WiFi and Ethernet only."
      note "Thread can be switched on later on the Radios card."
      MODE="wifi"
    else
      show_thread_menu 1
      if [ "$CHOICE" -eq 0 ]; then
        MODE="wifi"
      else
        MODE="thread"
        CHOSEN_RADIO="$(nth_line "$SERIAL_CANDIDATES" "$CHOICE")"
      fi
    fi
  fi
  if [ "$MODE" = "thread" ] && [ -z "$CHOSEN_RADIO" ]; then
    if [ -n "${RADIO_DEVICE:-}" ]; then
      CHOSEN_RADIO="$RADIO_DEVICE"
    elif [ -z "$(env_file_value RADIO_DEVICE)" ]; then
      list_serial_candidates
      if [ -z "$SERIAL_CANDIDATES" ]; then
        die "Thread mode was requested, but no USB stick was found under
$SERIAL_BY_ID_DIR, $SERIAL_DEV_DIR/ttyUSB* or $SERIAL_DEV_DIR/ttyACM*.
Plug the stick in, pass RADIO_DEVICE=/dev/serial/by-id/..., or use
LOXMATTER_MODE=wifi."
      fi
      show_thread_menu 0
      CHOSEN_RADIO="$(nth_line "$SERIAL_CANDIDATES" "$CHOICE")"
      # Thread was requested, so "None" is no option here, and stopping would
      # be a new abort on the non-interactive path. Taking stick 1 is a guess
      # between names that cannot tell a Thread stick from a Zigbee stick -
      # on the maintainer's test Pi, alphabetical order puts the Zigbee
      # dongle first. Say so, and name the way out.
      if { [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; } &&
         [ "$(count_lines "$SERIAL_CANDIDATES")" -gt 1 ]; then
        warn "$(serial_label "$CHOSEN_RADIO") was taken as the Thread stick without asking."
        warn "Several sticks were found, and their names cannot tell a Thread stick"
        warn "from a Zigbee stick. If this is the wrong one, run the installer again"
        warn "with RADIO_DEVICE=/dev/serial/by-id/<the Thread stick>, or change it"
        warn "later on the Radios card of the web interface."
      fi
    fi
  fi
  note "Operating mode: $MODE"
}

decide_backbone() {
  if [ "$MODE" != "thread" ]; then
    return 0
  fi
  step "choosing the border router's network interface"
  if [ -n "$(env_file_value BACKBONE_IF)" ]; then
    return 0
  fi
  if [ -n "${BACKBONE_IF:-}" ]; then
    CHOSEN_BACKBONE="$BACKBONE_IF"
    return 0
  fi
  CHOSEN_BACKBONE="$(detect_backbone_if)"
  if [ -n "$CHOSEN_BACKBONE" ]; then
    note "Border router network interface: $CHOSEN_BACKBONE (from the default route)."
    note "The Thread border router reaches the rest of your network over it."
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would ask for the border router's network interface"
    return 0
  fi
  if [ "$HAVE_TTY" -eq 0 ]; then
    die "BACKBONE_IF: there is no default route to read the border router's
network interface from, and no terminal to ask on. Pass it in instead:
  curl -fsSL $RAW_URL | BACKBONE_IF=eth0 sh"
  fi
  say "Border router network interface"
  note "No default route was found, so the interface the Thread border router"
  note "should use could not be read. Usually eth0 (cable) or wlan0 (WiFi)."
  while [ -z "$CHOSEN_BACKBONE" ]; do
    CHOSEN_BACKBONE="$(ask "Network interface" "")" ||
      die "The terminal closed before the network interface was given."
  done
}

# Each predicate mirrors the early returns of its decide_* function: true
# when that function will show its question. The backbone question is left
# out - it appears only without a default route, and whether it is needed
# depends on the Thread answer that has not been given yet.
thread_menu_expected() {
  # An existing .env means no menu at all here, mirroring decide_mode's own
  # second-run branch, which returns before any menu whenever the .env exists.
  if [ -f "$TARGET_DIR/deploy/testhost/.env" ] || [ -n "${RADIO_DEVICE:-}" ]; then
    return 1
  fi
  case "${LOXMATTER_MODE:-}" in
    ""|thread) : ;;
    *) return 1 ;;
  esac
  list_serial_candidates
  [ -n "$SERIAL_CANDIDATES" ]
}

bluetooth_menu_expected() {
  if [ -n "${BLUETOOTH_ADAPTER:-}" ] || [ -n "$(env_file_value BLUETOOTH_ADAPTER)" ]; then
    return 1
  fi
  list_bt_adapters
  [ "$(count_lines "$BT_ADAPTERS")" -gt 1 ]
}

miniserver_question_expected() {
  [ -z "${MINISERVER_IP:-}" ] && [ -z "$(env_file_value MINISERVER_IP)" ]
}

# Appends $1 as the next announced question, without eval: aq_count picks
# which of the three fixed slots it lands in.
aq_add() {
  aq_count=$((aq_count + 1))
  case "$aq_count" in
    1) aq_1=$1 ;;
    2) aq_2=$1 ;;
    *) aq_3=$1 ;;
  esac
}

# Says up front what is coming, so the user knows how long to stay at the
# keyboard before the installation runs on its own.
announce_questions() {
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  aq_count=0
  aq_1=""
  aq_2=""
  aq_3=""
  if thread_menu_expected; then
    aq_add "the Thread stick"
  fi
  if bluetooth_menu_expected; then
    aq_add "the Bluetooth adapter"
  fi
  if miniserver_question_expected; then
    aq_add "the address of your Loxone Miniserver"
  fi
  case "$aq_count" in
    0) return 0 ;;
    1) aq_text="One question follows: $aq_1." ;;
    2) aq_text="Two questions follow: $aq_1 and $aq_2." ;;
    *) aq_text="Three questions follow: $aq_1, $aq_2, and $aq_3." ;;
  esac
  say "Questions"
  note "$aq_text"
}

# Every question, asked before anything is installed: answering once and
# walking away beats being called back to the keyboard minutes later, after
# the package and Docker installation.
ask_questions() {
  announce_questions
  decide_mode
  decide_backbone
  decide_bluetooth
  decide_miniserver
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

# The docker command to print in a hint for the person running this. After
# Docker was installed in this run, their shell is not in the 'docker' group
# until they log in again - plain `docker` would answer "permission denied"
# on the very command the hint suggests.
docker_cmd() {
  if [ "$DOCKER_SUDO" -eq 1 ]; then
    printf 'sudo docker'
  else
    printf 'docker'
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
# or otherwise touches an existing checkout: an update goes through the web
# interface (System -> Version), which backs up first and rolls back on
# failure, so this step cannot be the thing that quietly rewrites a checkout
# the human is relying on.
ensure_checkout() {
  step "getting the repository"
  STACK_DIR="$TARGET_DIR/deploy/testhost"
  if [ -d "$TARGET_DIR" ]; then
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
  # Every call to env_set only ever happens from inside configure() - this is
  # how an abort further down that same function knows to say .env was
  # already touched, instead of leaving that unsaid.
  CONFIG_WRITTEN=1
  if ! env_file_has "$env_key"; then
    # A hand-edited .env commonly has no trailing newline - $(...) in an
    # editor, or a plain `printf` without one, both leave the file that way.
    # Appending straight onto that merges the new "KEY=value" onto the end
    # of the last existing line instead of starting a line of its own,
    # destroying that line's value. `tail -c 1` reads the file's last byte
    # without loading the whole thing; a missing file or an empty one both
    # report something other than a lone newline, so the check only adds
    # one when there is a real last line to close off.
    if [ -s "$ENV_FILE" ] && [ "$(tail -c 1 "$ENV_FILE")" != "" ]; then
      printf '\n' >> "$ENV_FILE"
    fi
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

# Every abort message has to name what was and was not changed. An abort
# reached from inside configure() can follow env_set calls that already
# wrote earlier keys, so the die messages below add this sentence - naming
# only that $ENV_FILE was touched, nothing about which keys.
config_written_note() {
  if [ "$CONFIG_WRITTEN" -eq 1 ]; then
    printf ' %s was already partially written.' "$ENV_FILE"
  fi
}

# Writes what phase one decided. A second run keeps whatever the existing
# .env already holds. With $3 = 1 an empty value stops the run instead of
# writing a line otbr cannot start with.
write_env_value() {
  wv_key="$1"
  wv_value="$2"
  wv_required="$3"
  if [ "$ENV_IS_NEW" -eq 0 ]; then
    wv_kept="$(env_file_value "$wv_key")"
    if [ -n "$wv_kept" ]; then
      note "$wv_key=$wv_kept (kept)"
      return 0
    fi
  fi
  if [ -z "$wv_value" ] && [ "$wv_required" -eq 1 ]; then
    die "$wv_key needs a value and none could be obtained. Pass $wv_key=... to
this script.$(config_written_note)"
  fi
  env_set "$wv_key" "$wv_value"
  note "$wv_key=$wv_value"
}

# Asks the address for /jdev/cfg/api, which a Miniserver answers without
# signing in, with its serial number and firmware version. Returns 1 when no
# Miniserver answered and leaves the reason in MS_PROBLEM. The shape parsed
# here is the one captured in tests/fixtures/miniserver/jdev_cfg_api.json.
check_miniserver() {
  cm_ip="$1"
  MS_PROBLEM=""
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would check http://$cm_ip/jdev/cfg/api"
    return 0
  fi
  if ! have curl; then
    note "curl is not installed yet, so $cm_ip is not checked."
    return 0
  fi
  note "Checking $cm_ip ..."
  cm_status=0
  cm_body="$(curl -fsS -m 3 "http://$cm_ip/jdev/cfg/api" 2>/dev/null)" || cm_status=$?
  if [ "$cm_status" -ne 0 ]; then
    case "$cm_status" in
      28) cm_reason="timeout" ;;
      7) cm_reason="could not connect" ;;
      22) cm_reason="HTTP error" ;;
      *) cm_reason="curl exit status $cm_status" ;;
    esac
    MS_PROBLEM="No Miniserver answers at $cm_ip ($cm_reason)."
    warn "$MS_PROBLEM"
    return 1
  fi
  cm_snr="$(printf '%s\n' "$cm_body" | sed -n "s/.*'snr': *'\([^']*\)'.*/\1/p" | head -n 1)"
  cm_version="$(printf '%s\n' "$cm_body" | sed -n "s/.*'version': *'\([^']*\)'.*/\1/p" | head -n 1)"
  if [ -z "$cm_snr" ] && [ -z "$cm_version" ]; then
    MS_PROBLEM="Something answers at $cm_ip, but it is not a Miniserver."
    warn "$MS_PROBLEM"
    return 1
  fi
  note "Miniserver found at $cm_ip (serial ${cm_snr:-unknown}, firmware ${cm_version:-unknown})."
}

add_miniserver_finding() {
  add_finding "The Miniserver address $1 was written although no Miniserver answered
there. $MS_PROBLEM
Once the Miniserver is reachable, check the address in the web interface under
Settings -> Miniserver connection."
}

decide_miniserver() {
  step "asking for the Miniserver"
  # A second run keeps the address and does not check it again.
  if [ -n "$(env_file_value MINISERVER_IP)" ]; then
    return 0
  fi
  if [ -n "${MINISERVER_IP:-}" ]; then
    # check_config_source has already refused a malformed one.
    CHOSEN_MS="$MINISERVER_IP"
    if ! check_miniserver "$CHOSEN_MS"; then
      add_miniserver_finding "$CHOSEN_MS"
    fi
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would ask for the Miniserver's address and check it"
    return 0
  fi
  # check_config_source has already stopped a run with neither an address
  # nor a terminal, so from here on there is one to ask on.
  say "Loxone Miniserver"
  note "loxmatter signs in to the Miniserver and creates the devices there."
  note "You find its address in Loxone Config under the Miniserver's"
  note "properties, or in the Loxone app under Settings -> Miniserver."
  printf '\n'
  while :; do
    CHOSEN_MS="$(ask "IPv4 address of the Miniserver" "")" ||
      die "The terminal closed before the Miniserver's address was given."
    if ! valid_ipv4 "$CHOSEN_MS"; then
      warn "'$CHOSEN_MS' is not an IPv4 address like 192.168.1.10."
      continue
    fi
    if check_miniserver "$CHOSEN_MS"; then
      return 0
    fi
    note "  1) Enter a different address"
    note "  2) Use it anyway - the Miniserver is not reachable right now"
    choose "Choice" 1 2 0
    if [ "$CHOICE" -eq 2 ]; then
      add_miniserver_finding "$CHOSEN_MS"
      return 0
    fi
  done
}

detect_backbone_if() {
  ip route show default 2>/dev/null |
    awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }'
}

# The product string of the USB device a sysfs path belongs to. An adapter's
# device link points at a USB interface; the product file sits on the device
# above it. Walked up a few levels, like radios/inventory.py's _usb_device.
usb_product() {
  up_dir="$1"
  up_steps=0
  while [ "$up_steps" -le 4 ] && [ -n "$up_dir" ]; do
    if [ -r "$up_dir/product" ]; then
      cat "$up_dir/product"
      return 0
    fi
    up_dir="${up_dir%/*}"
    up_steps=$((up_steps + 1))
  done
}

# Sets BT_ADAPTERS to one line per adapter: its hci index, a tab, a label.
# Classified like radios/inventory.py's scan_bluetooth: a USB adapter's
# device link resolves below a usb bus, a built-in one below a serial port.
# Only the part after the last /devices/ is looked at, so a directory above
# sysfs whose name contains "usb" or "serial" cannot mislabel one.
# `pwd -P` resolves the link: readlink -f is not POSIX.
list_bt_adapters() {
  BT_ADAPTERS=""
  for bt_entry in "$BT_SYS_DIR"/hci*; do
    bt_index="${bt_entry##*/hci}"
    case "$bt_index" in
      ''|*[!0-9]*) continue ;;
    esac
    bt_target="$(cd "$bt_entry/device" 2>/dev/null && pwd -P)" || bt_target=""
    case "${bt_target##*/devices/}" in
      usb*|*/usb*)
        bt_product="$(usb_product "$bt_target")"
        bt_label="USB: ${bt_product:-unknown adapter}"
        ;;
      *serial*) bt_label="built in (UART)" ;;
      *) bt_label="other" ;;
    esac
    BT_ADAPTERS="$BT_ADAPTERS$bt_index$TAB$bt_label
"
  done
}

decide_bluetooth() {
  step "choosing the Bluetooth adapter"
  if [ -n "${BLUETOOTH_ADAPTER:-}" ]; then
    CHOSEN_BT="$BLUETOOTH_ADAPTER"
    return 0
  fi
  if [ -n "$(env_file_value BLUETOOTH_ADAPTER)" ]; then
    return 0
  fi
  list_bt_adapters
  bt_count="$(count_lines "$BT_ADAPTERS")"
  if [ "$bt_count" -eq 0 ]; then
    warn "No Bluetooth adapter found. New Matter devices can then only be"
    warn "commissioned if they are already on your network (for example"
    warn "through the manufacturer's app). An adapter can be chosen later on"
    warn "the Radios card of the web interface."
    CHOSEN_BT=0
    return 0
  fi
  if [ "$bt_count" -eq 1 ]; then
    bt_line="$(nth_line "$BT_ADAPTERS" 1)"
    CHOSEN_BT="${bt_line%%"$TAB"*}"
    note "Bluetooth: hci$CHOSEN_BT - ${bt_line#*"$TAB"}, used to commission Matter devices."
    return 0
  fi
  say "Bluetooth adapter"
  note "Most Matter devices are commissioned over Bluetooth. The built-in"
  note "adapter is usually enough; a USB adapter reaches further."
  printf '\n'
  bt_position=0
  while IFS="$TAB" read -r bt_index bt_label; do
    if [ -z "$bt_index" ]; then
      continue
    fi
    bt_position=$((bt_position + 1))
    note "  $bt_position) hci$bt_index - $bt_label"
  done <<EOF
$BT_ADAPTERS
EOF
  printf '\n'
  choose "Which adapter should be used?" 1 "$bt_count" 0
  bt_line="$(nth_line "$BT_ADAPTERS" "$CHOICE")"
  CHOSEN_BT="${bt_line%%"$TAB"*}"
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
  # decide_mode (phase one) already decided and printed this. Keeping the
  # existing .env is correct, but silently overwriting that decision here
  # is not - anything that changes MODE below has to say so.
  cm_requested_mode="$MODE"
  if [ "$ENV_IS_NEW" -eq 0 ] && env_file_has COMPOSE_PROFILES; then
    if [ -n "$(env_file_value COMPOSE_PROFILES)" ]; then
      MODE="thread"
    else
      MODE="wifi"
    fi
    note "COMPOSE_PROFILES kept, mode: $MODE"
    # Only LOXMATTER_MODE is a request. A mode decide_mode read out of this
    # same .env, or a provisional wifi for an old .env without the line, is
    # not something the user asked for and must not be "overruled" loudly.
    if [ -n "${LOXMATTER_MODE:-}" ] && [ "$MODE" != "$cm_requested_mode" ]; then
      warn "The existing $ENV_FILE wins over the requested '$cm_requested_mode' mode: mode is '$MODE'. Edit COMPOSE_PROFILES in $ENV_FILE to change it."
    fi
    return 0
  fi
  if [ "$ENV_IS_NEW" -eq 0 ] && dk ps -a --format '{{.Names}}' 2>/dev/null | grep -qx otbr; then
    env_set COMPOSE_PROFILES thread
    MODE="thread"
    note "COMPOSE_PROFILES=thread (this installation already runs otbr)"
    if [ -n "${LOXMATTER_MODE:-}" ] && [ "$MODE" != "$cm_requested_mode" ]; then
      warn "The existing $ENV_FILE wins over the requested '$cm_requested_mode' mode: mode is '$MODE'. Edit COMPOSE_PROFILES in $ENV_FILE to change it."
    fi
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
    write_env_value RADIO_DEVICE "$CHOSEN_RADIO" 1
    write_env_value RADIO_BAUDRATE "${RADIO_BAUDRATE:-460800}" 1
    if [ "$ENV_IS_NEW" -eq 1 ] && [ -z "${RADIO_BAUDRATE:-}" ]; then
      note "  (what the bundled border router image expects; set RADIO_BAUDRATE"
      note "  before running this script to use a different one)"
    fi
    write_env_value BACKBONE_IF "$CHOSEN_BACKBONE" 1
  elif [ "$ENV_IS_NEW" -eq 1 ]; then
    # Without Thread nothing reads BACKBONE_IF yet, but switching Thread on
    # later happens on the Radios card, which never asks for it: left at
    # .env.example's wlan0, an Ethernet-only host would get a border router
    # on the wrong interface. The detected one is written without a
    # question, and only into a fresh .env.
    detected_backbone="$(detect_backbone_if)"
    if [ -n "$detected_backbone" ]; then
      env_set BACKBONE_IF "$detected_backbone"
      note "BACKBONE_IF=$detected_backbone (for Thread, should you switch it on later)"
    fi
  fi
  write_env_value BLUETOOTH_ADAPTER "$CHOSEN_BT" 0
  write_env_value MINISERVER_IP "$CHOSEN_MS" 1
  if [ "$ENV_IS_NEW" -eq 1 ] || [ -z "$(env_file_value LOXMATTER_API_TOKEN)" ]; then
    token_value="${LOXMATTER_API_TOKEN:-$(gen_token)}"
    # gen_token's /dev/urandom fallback runs through a pipe with no pipefail,
    # so a failing od (missing or unreadable /dev/urandom) is invisible in
    # its own exit status - tr on an empty stdin still succeeds, and this
    # would otherwise write an empty token while printing "generated" below.
    if [ "${#token_value}" -ne 64 ]; then
      die "Could not generate LOXMATTER_API_TOKEN: expected 64 hex characters, got ${#token_value}.$(config_written_note)"
    fi
    case "$token_value" in
      *[!0-9a-f]*) die "Could not generate LOXMATTER_API_TOKEN: got non-hex output.$(config_written_note)" ;;
    esac
    env_set LOXMATTER_API_TOKEN "$token_value"
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
  note "Pulling the published image from ghcr.io. If that host cannot be"
  note "reached, Docker falls back to building from source instead - that"
  note "takes several minutes on a Raspberry Pi. It is not stuck."
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would run: docker compose up -d in $STACK_DIR"
    return 0
  fi
  mkdir -p "$STACK_DIR/data"
  # No --build: deploy/testhost/docker-compose.yml names a published image,
  # and `up` only falls back to building when no such image can be obtained
  # at all (see the comment above that file's `image:` line) - the exact
  # fallback a host without GHCR access needs. Building here on purpose
  # would tag the result with the service's `image:` value, so a fresh
  # install would end up with a LOCAL image called
  # ghcr.io/lucienkerl/loxmatter:stable that reports itself as `dev` -
  # the working-copy hint in the web UI would then fire on a brand-new
  # installation of a released version.
  #
  # No --profile either, on purpose: COMPOSE_PROFILES lives in .env, which
  # Compose reads by itself. Every later call in this directory - by hand,
  # or from scripts/update.sh - then picks the same services without a flag
  # to remember.
  ( cd "$STACK_DIR" && dk compose up -d ) ||
    die "Could not start the stack in $STACK_DIR. The checkout and .env are in place; fix the
cause and run this again. The logs are in:
  cd $STACK_DIR && $(docker_cmd) compose logs"
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

# 20 tries, but each one can also spend curl's own 3-second timeout before
# the 1-second sleep runs - a host that hangs instead of refusing to connect
# can take close to 20 * (3 + 1) = 80 seconds here, not the 20 the retry
# count alone suggests. The same shape exists in scripts/update.sh.
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
    HEALTHY=0
    add_finding "$health_url does not answer, so the bridge is not healthy yet.
Look at:
  cd $STACK_DIR && $(docker_cmd) compose logs loxmatter"
    return 0
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
  cd $STACK_DIR && $(docker_cmd) compose logs $service"
    fi
  done
}

check_rfkill() {
  step "checking the Bluetooth adapter"
  # A host can carry more than one Bluetooth radio - every rfkill* entry of
  # type bluetooth gets its own finding when blocked, instead of stopping at
  # the first one and leaving the rest unchecked.
  for entry in "$RFKILL_DIR"/rfkill*; do
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
  # Not a finding: nothing here needs a human. The updater service runs
  # scripts/otbr-watchdog.sh every minute; it clears a stale pid and
  # restarts the border router itself if the agent never came up or later
  # hangs, which used to be exactly what the printed workaround did by hand.
  note "No Thread interface (wpan*) yet. This can take a few minutes; the updater service's watchdog restarts the border router on its own if it hangs."
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

# ----------------------------------------------------------- phase seven --

# check_containers, check_rfkill and check_thread already print each finding
# right where they discover it, inside run_checks - printing the whole
# $FINDINGS block again here would put the same copy-pasteable commands on
# the screen twice, just a few lines apart. This only points back to them.
report() {
  step "writing the summary"
  if [ "$DRY_RUN" -eq 1 ]; then
    say "Dry run finished. Nothing was changed."
    return 0
  fi
  say "Done."
  # hostname -I is Linux-only, can print several addresses separated by
  # spaces, or none at all (no interface up yet) - take the first one, and
  # fall back to a placeholder rather than print a URL missing its host.
  lan_ip="$(hostname -I 2>/dev/null | awk '{ print $1 }')" || lan_ip=""
  if [ -z "$lan_ip" ]; then
    lan_ip="<this host>"
  fi
  printf '  Web interface: http://%s:%s/\n' "$lan_ip" "$PORT"
  printf '  Open it and set a password. Until you do, no /api route answers -\n'
  printf '  there is no open state.\n'
  if [ "$MODE" = "thread" ]; then
    # No crontab line to add any more: the updater service runs the same
    # watchdog every minute on its own (deploy/updater/watchdog-once.sh), and
    # also keeps the border router on the image, device and radio URL the
    # Compose file asks for - see the Radios card in Settings.
    printf '\n  The updater service watches the Thread border router: its watchdog\n'
    printf '  runs every minute, and it keeps the border router on the image and\n'
    printf '  device this host is configured for. Nothing to set up by hand.\n'
  else
    printf '\n  Running WiFi and Ethernet only. To add Thread later: plug the radio in,\n'
    printf '  then open Settings -> Radios in the web interface and switch Thread on.\n'
  fi
  # Updates go through the web interface only: the bridge backs up its
  # database first and the updater service rolls back a version that does
  # not come up healthy - a console path would skip both and ask a person to
  # run commands the web interface already runs for them.
  printf '\n  Updates: open System -> Version in the web interface.\n'
  if [ "$DOCKER_SUDO" -eq 1 ]; then
    printf '\n'
    warn "Docker was installed during this run. Log out and back in once, so"
    warn "that 'docker' works without sudo."
  fi
  if [ -n "$FINDINGS" ]; then
    printf '\n'
    note "Some things above still need you - see \"Findings\" for the commands."
  fi
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
  check_config_source
  ask_questions
  install_packages
  install_docker
  ensure_checkout
  configure
  start_stack
  run_checks
  report
  # Everything above still applies even when the bridge itself is not
  # answering - the container list, the findings and the web address are
  # exactly what points at why. So this stays the very last thing main does,
  # after report() has already printed all of it.
  if [ "$HEALTHY" -eq 0 ]; then
    die "The bridge does not answer yet. Everything printed above still applies."
  fi
}

main "$@"
