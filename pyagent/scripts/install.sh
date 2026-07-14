#!/usr/bin/env sh
# Install Terminal AI Agent from GitHub using curl.
# Default mode downloads the checked-in shiv binary.  Set
# TERMINAL_AGENT_INSTALL_MODE=pip to install from source with pip instead.
set -eu

REPO="${TERMINAL_AGENT_REPO:-https://github.com/cmyolo441-coder/py.test}"
BRANCH="${TERMINAL_AGENT_BRANCH:-main}"
BIN_DIR="${TERMINAL_AGENT_BIN_DIR:-$HOME/.local/bin}"
BIN_NAME="${TERMINAL_AGENT_BIN_NAME:-agent}"
MODE="${TERMINAL_AGENT_INSTALL_MODE:-binary}"
PYTHON="${PYTHON:-python3}"
RAW_BASE="https://raw.githubusercontent.com/cmyolo441-coder/py.test/$BRANCH/pyagent"
BINARY_URL="${TERMINAL_AGENT_BINARY_URL:-$RAW_BASE/dist/agent}"

mkdir -p "$BIN_DIR"

case "$MODE" in
  binary)
    echo "==> Downloading Terminal AI Agent binary"
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "$BINARY_URL" -o "$BIN_DIR/$BIN_NAME"
    elif command -v wget >/dev/null 2>&1; then
      wget -qO "$BIN_DIR/$BIN_NAME" "$BINARY_URL"
    else
      echo "curl or wget is required for binary install" >&2
      exit 1
    fi
    chmod +x "$BIN_DIR/$BIN_NAME"
    # Clear stale shiv extraction caches from older native-dependency builds.
    rm -rf "$HOME"/.shiv/agent_* 2>/dev/null || true
    ;;
  pip)
    echo "==> Installing Terminal AI Agent from source with pip"
    "$PYTHON" -m pip install --upgrade "git+$REPO.git@${BRANCH}#subdirectory=pyagent"
    ;;
  *)
    echo "Unknown TERMINAL_AGENT_INSTALL_MODE=$MODE (use binary or pip)" >&2
    exit 1
    ;;
esac

cat <<EOF

Installed: $BIN_DIR/$BIN_NAME
Run:       $BIN_NAME
Version:   $($BIN_DIR/$BIN_NAME --version 2>/dev/null || true)

If '$BIN_NAME' is not found, add this to your shell profile:
  export PATH="\$HOME/.local/bin:\$PATH"
EOF
