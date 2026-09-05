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
# shellcheck disable=SC2034 # read by the Docker-install phase added in a later task
DOCKER_INSTALL_URL="https://get.docker.com"

DRY_RUN=0
TARGET_DIR=""
STEP="starting up"
# shellcheck disable=SC2034 # set by the clone phase added in a later task
CHECKOUT_EXISTED=0
STACK_STARTED=0

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
    # shellcheck disable=SC2016 # backticks are literal quoting for the reader, not a substitution
    printf 'The stack in %s was started; `docker compose ps` there shows it.\n' \
      "$TARGET_DIR/deploy/testhost"
  elif [ -d "$TARGET_DIR" ]; then
    printf 'The checkout at %s exists; nothing was started.\n' "$TARGET_DIR"
  else
    printf 'Nothing was created; %s does not exist.\n' "$TARGET_DIR"
  fi
}

on_exit() {
  code=$?
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

# ------------------------------------------------------------------ main --

main() {
  trap on_exit EXIT
  parse_args "$@"
  say "loxmatter installer"
  if [ "$DRY_RUN" -eq 1 ]; then
    note "Dry run: every step is printed, nothing is changed."
  fi
  check_platform
}

main "$@"
