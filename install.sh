#!/usr/bin/env bash
set -euo pipefail
# ctop installer. Run it as your normal user, from any folder: ./install.sh
#
# It changes exactly two things:
#   <repo>/.venv          a private Python environment that holds ctop and its libraries
#   ~/.local/bin/ctop     a link to the ctop command inside that environment
# It never uses sudo. If a system package is missing, it prints the one line you
# should run yourself, and stops.
#
# Usage: ./install.sh [--dry-run] [--help]

# Work from the folder this script really lives in (symlinks resolved), so every path
# below is the checkout's real location. # why: the venv and the link must point here.
cd "$(dirname "$(readlink -f "$0")")"
REPO="$PWD"
VENV="$REPO/.venv"
BIN_DIR="$HOME/.local/bin"
LINK="$BIN_DIR/ctop"
PY="${PYTHON:-python3}"   # why: lets you pick a newer interpreter without editing this file

# Hint lines: plain text for you to run yourself. This script never executes them.
HINT_APT='sudo apt install python3-venv'
HINT_APT_PY='sudo apt install python3'
HINT_PACMAN='sudo pacman -S python'
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

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
    'Usage: ./install.sh [--dry-run] [--help]' \
    '' \
    'Installs ctop into <repo>/.venv and links the ctop command into ~/.local/bin.' \
    'Run it as your normal user. It never runs anything as root; if a system package' \
    'is missing it prints the one line for you to run yourself.' \
    '' \
    '  --dry-run   print each step as "would: ..." and change nothing' \
    '  --help      show this text' \
    '' \
    'Pick the Python with: PYTHON=python3.11 ./install.sh   (default: python3)'
}

# Read the options before anything else, so --help works everywhere.
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$arg" >&2; usage >&2; exit 1 ;;
  esac
done

# Refuse root. # why: files created as root would be owned by root and break later updates.
if [ "$(id -u)" -eq 0 ]; then
  printf '%s\n' 'Run as your normal user; a root-owned .venv would be a mess.' >&2
  exit 1
fi

# Which distro family we are on, from /etc/os-release (the standard file that names the
# distro). Only used to choose which hint line to print; nothing is installed here.
distro_family() {
  local id="" like=""
  if [ -r /etc/os-release ]; then
    id=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
    like=$(sed -n 's/^ID_LIKE=//p' /etc/os-release | tr -d '"')
  fi
  case " $id $like " in
    *" debian "*|*" ubuntu "*) echo debian ;;
    *" arch "*) echo arch ;;
    *) echo other ;;
  esac
}

python_hint() {
  case "$(distro_family)" in
    debian) printf '%s\n' "$HINT_APT_PY" ;;
    arch) printf '%s\n' "$HINT_PACMAN" ;;
    *) printf '%s\n' 'install Python 3.9 or newer from your distro' ;;
  esac
}

venv_hint() {
  case "$(distro_family)" in
    debian) printf '%s\n' "$HINT_APT" ;;
    arch) printf '%s\n' "$HINT_PACMAN" ;;
    *) printf '%s\n' "install your distro's Python venv package (often python3-venv)" ;;
  esac
}

# Step 1: Python 3.9 or newer. # why: ctop uses 3.9 syntax and its libraries need 3.9+.
if ! command -v "$PY" >/dev/null 2>&1; then
  printf 'ctop needs Python 3.9 or newer, but %s was not found.\n' "$PY" >&2
  printf 'Install it yourself, then run this again:\n  %s\n' "$(python_hint)" >&2
  exit 2
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  found=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)
  printf 'ctop needs Python 3.9 or newer (found %s).\n' "$found" >&2
  printf 'Install a newer Python yourself, then run this again:\n  %s\n' "$(python_hint)" >&2
  exit 2
fi

# Step 2: can this Python make a virtual environment? Some distros ship that module as a
# separate package. # why: test in a throw-away folder that the trap deletes, so a failed
# probe leaves nothing behind.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if ! "$PY" -m venv "$tmp/probe" >/dev/null 2>&1; then
  printf '%s\n' 'Python cannot create a virtual environment (ensurepip is not available).' >&2
  printf 'Install the venv module yourself, then run this again:\n  %s\n' "$(venv_hint)" >&2
  exit 2
fi

# Step 3: the private environment. Reuse it if it works; rebuild it if it is missing or
# broken. # why: a half-made venv from an old run gives confusing import errors.
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  say "Using the existing environment: $VENV"
else
  if [ -e "$VENV" ] || [ -L "$VENV" ]; then
    act rm -rf -- "$VENV"
  fi
  act "$PY" -m venv "$VENV"
fi

# Step 4: install ctop into the venv only. # why: pip never touches the system Python,
# and a second run finds everything already installed, so it changes nothing new.
if [ "$DRY" -eq 1 ]; then
  would "upgrade pip inside $VENV"
  would "pip install -e . (ctop-tui and its libraries) inside $VENV"
else
  if ! "$VENV/bin/python" -m pip install --quiet --upgrade pip; then
    printf 'pip could not upgrade itself. Scroll up for the error.\n' >&2
    printf 'Nothing outside %s was changed.\n' "$VENV" >&2
    exit 1
  fi
  if ! "$VENV/bin/python" -m pip install --quiet -e .; then
    printf 'pip could not install ctop. Scroll up for the error.\n' >&2
    printf 'Nothing outside %s was changed.\n' "$VENV" >&2
    exit 1
  fi
fi

# Step 5: the command link. # why: a regular file, or a link that belongs to some other
# program, is never overwritten; replacing it silently could break that program.
if [ -e "$LINK" ] || [ -L "$LINK" ]; then
  dest=""
  if [ -L "$LINK" ]; then
    dest=$(readlink "$LINK")
  fi
  case "$dest" in
    */.venv/bin/ctop) ;;   # our own link from an earlier install: safe to replace
    *)
      printf '%s already exists and was not made by install.sh.\n' "$LINK" >&2
      printf 'Move it out of the way yourself, then run this again:\n' >&2
      printf '  mv -- "%s" "%s.bak-%s"\n' "$LINK" "$LINK" "$(date +%Y%m%d)" >&2
      exit 1
      ;;
  esac
fi
act mkdir -p -- "$BIN_DIR"
act ln -sfn "$VENV/bin/ctop" "$LINK"

# Step 6: PATH. Printed, never written. # why: editing your shell startup file is your call.
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    printf 'Note: %s is not on your PATH, so a new terminal will not find ctop.\n' "$BIN_DIR"
    printf 'To add it, run this line yourself, then open a new terminal:\n'
    printf '  echo %s >> ~/.bashrc\n' "'$PATH_LINE'"
    ;;
esac

# Step 7: prove the install works before saying so. # why: a missing dependency shows up
# here, not later as a crash on first launch.
if [ "$DRY" -eq 1 ]; then
  would "check that ctop, textual and psutil import from $VENV"
  say "Dry run finished. Nothing was changed."
  exit 0
fi
if ! "$VENV/bin/python" -c 'import ctop, textual, psutil' >/dev/null 2>&1; then
  printf 'The install did not check out: ctop, textual or psutil does not import.\n' >&2
  printf 'Run this yourself to see the error:\n  %s -c "import ctop, textual, psutil"\n' \
    "$VENV/bin/python" >&2
  exit 1
fi
say "Installed. Run: ctop"
