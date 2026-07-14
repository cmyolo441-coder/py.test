#!/usr/bin/env sh
# Build the standalone shiv binary for Terminal AI Agent.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
OUT_DIR="$ROOT/dist"
OUT_BIN="$OUT_DIR/agent"

printf '==> Cleaning old build artifacts\n'
rm -rf "$OUT_DIR" "$ROOT/build" "$ROOT"/*.egg-info
mkdir -p "$OUT_DIR"

printf '==> Ensuring shiv is installed\n'
if ! "$PYTHON" -m shiv --help >/dev/null 2>&1; then
  "$PYTHON" -m pip install --user --upgrade shiv
fi

printf '==> Building binary: %s\n' "$OUT_BIN"
"$PYTHON" -m shiv \
  --compressed \
  -p '/usr/bin/env python3' \
  -e agent.app:main \
  -o "$OUT_BIN" \
  "$ROOT"
chmod +x "$OUT_BIN"

printf '==> Built binary\n'
ls -lh "$OUT_BIN"
printf '\nSmoke test:\n'
"$OUT_BIN" --version
