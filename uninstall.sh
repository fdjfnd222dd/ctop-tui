#!/usr/bin/env bash
set -euo pipefail
# ctop uninstaller. Run it as your normal user, from any folder: ./uninstall.sh
#
# It removes only what install.sh made:
#   ~/.local/bin/ctop     but only if it is the link to THIS checkout's command
#   <repo>/.venv          the private environment
# It never touches the repository itself, ~/.local/bin, your config or any other file.
# Running it twice is fine: "nothing to remove" counts as success.
#
# Usage: ./uninstall.sh [--dry-run] [--help]

# Work from the folder this script really lives in (symlinks resolved).
cd "$(dirname "$(readlink -f "$0")")"
REPO="$PWD"
VENV="$REPO/.venv"
LINK="$HOME/.local/bin/ctop"

DRY=0

say() { printf '%s\n' "$*"; }
would() { printf 'would: %s\n' "$*"; }

# Run a command, or in --dry-run only describe it. # why: one place decides whether
# anything on the machine changes.
act() {
  if [ "$DRY" -eq 1 ]; then
    would "$*"
  else
    "$@"
  fi
}

usage() {
  printf '%s\n' \
    'Usage: ./uninstall.sh [--dry-run] [--help]' \
    '' \
    'Removes the ctop command link from ~/.local/bin and the .venv folder inside this' \
    'checkout. Never removes the repository itself or any config file.' \
    '' \
    '  --dry-run   print each step as "would: ..." and remove nothing' \
    '  --help      show this text'
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$arg" >&2; usage >&2; exit 1 ;;
  esac
done

# Refuse root. # why: a root run could remove files it has no business touching.
if [ "$(id -u)" -eq 0 ]; then
  printf '%s\n' 'Run as your normal user; this script only removes your own ctop files.' >&2
  exit 1
fi

# Step 1: the command link. # why: a link to some other program, or a plain file, is not
# ours to delete.
if [ -L "$LINK" ]; then
  dest=$(readlink "$LINK")
  if [ "$dest" = "$VENV/bin/ctop" ]; then
    act rm -f -- "$LINK"
  else
    say "Leaving $LINK alone: it does not point into this checkout."
  fi
elif [ -e "$LINK" ]; then
  say "Leaving $LINK alone: it is a regular file, not a link made by install.sh."
else
  say "No $LINK. Nothing to remove there."
fi

# Step 2: the private environment. The checks make sure rm only ever hits
# <this repo>/.venv. # why: an empty or wrong path must never turn into rm -rf of
# something else.
if [ -n "$VENV" ] && [ "$(basename -- "$VENV")" = ".venv" ] \
  && [ "$(dirname -- "$VENV")" = "$REPO" ]; then
  if [ -d "$VENV" ] && [ ! -L "$VENV" ]; then
    act rm -rf -- "$VENV"
  else
    say "No environment at $VENV. Nothing to remove there."
  fi
else
  printf 'Refusing to remove %s: the path failed a safety check.\n' "$VENV" >&2
  exit 1
fi

if [ "$DRY" -eq 1 ]; then
  say "Dry run finished. Nothing was removed."
else
  say "ctop removed. You can delete this folder."
fi
