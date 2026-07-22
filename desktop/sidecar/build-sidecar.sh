#!/usr/bin/env bash
# Freeze Kato into a single-binary sidecar and drop it where Tauri's
# ``externalBin`` expects it: src-tauri/binaries/kato-sidecar-<target-triple>.
#
# Prereqs on THIS (build) machine: Python 3.11 with kato installed editable
# (`pip install -e .` at repo root), PyInstaller, and Rust (for the triple).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/../src-tauri/binaries"
mkdir -p "$OUT"

# Use the kato venv's python by EXPLICIT PATH — don't rely on the activated
# `python`. (The venv's activate script / pip shebang can carry stale hardcoded
# paths if it was ever copied between projects, so PATH-based `python` may
# resolve to the wrong interpreter.) The .venv/bin/python symlink still resolves
# correctly and has kato's deps + PyInstaller. Override with KATO_PYTHON.
PYTHON="${KATO_PYTHON:-$REPO/.venv/bin/python}"
if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "error: PyInstaller not importable via '$PYTHON'." >&2
  echo "       Point KATO_PYTHON at a venv that has kato's deps + pyinstaller." >&2
  exit 1
fi

# Tauri names sidecars ``<name>-<rustc host triple>[.exe]``. Prefer rustc
# (authoritative); fall back to deriving the triple from uname so the freeze
# doesn't hard-require Rust just to name the file.
if command -v rustc >/dev/null 2>&1; then
  TRIPLE="$(rustc -Vv | awk '/^host:/ {print $2}')"
else
  _os="$(uname -s)"; _arch="$(uname -m)"
  case "$_arch" in arm64|aarch64) _arch=aarch64 ;; x86_64|amd64) _arch=x86_64 ;; esac
  case "$_os" in
    Darwin) TRIPLE="${_arch}-apple-darwin" ;;
    Linux)  TRIPLE="${_arch}-unknown-linux-gnu" ;;
    *) echo "error: can't derive target triple for $_os/$_arch — install Rust" >&2; exit 1 ;;
  esac
fi

EXT=""
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) EXT=".exe" ;;
esac

cd "$HERE"
"$PYTHON" -m PyInstaller --clean --noconfirm kato-sidecar.spec

SRC="dist/kato-sidecar${EXT}"
DST="$OUT/kato-sidecar-${TRIPLE}${EXT}"
cp "$SRC" "$DST"
chmod +x "$DST" 2>/dev/null || true
echo "sidecar → $DST"
