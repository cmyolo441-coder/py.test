#!/usr/bin/env bash
# FullAgent installer — detects architecture, installs the right way.
set -euo pipefail

REPO="cmyolo441-coder/py.test"
NAME="fullagent"
VERSION="v1.0.0"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"

ARCH="$(uname -m)"
OS="$(uname -s)"

echo ">> FullAgent installer ($OS/$ARCH)"

# ---------------------------------------------------------------------------
# x86_64 Linux: download the standalone binary (29MB, no Python needed)
# ---------------------------------------------------------------------------
if [ "$OS" = "Linux" ] && { [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; }; then
  echo ">> Downloading standalone binary..."
  mkdir -p "$INSTALL_DIR"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "https://github.com/${REPO}/releases/download/${VERSION}/fullagent-v1.0.0-linux.tar.gz" -o "$TMP/fa.tar.gz"
  tar -xzf "$TMP/fa.tar.gz" -C "$TMP"
  chmod +x "$TMP/fullagent"
  mv "$TMP/fullagent" "$INSTALL_DIR/$NAME"
  echo ">> Installed: $INSTALL_DIR/$NAME"
  echo ">> Run: $NAME"
  exit 0
fi

# ---------------------------------------------------------------------------
# Everything else (ARM64 Linux, macOS, etc.): ultra-light install.
# Downloads a 389KB prebuilt wheel — no git clone, no build step.
# ---------------------------------------------------------------------------
echo ">> Lightweight install for $ARCH (~1MB download)..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "!! python3 is required" >&2; exit 1
fi

VENV_DIR="${FULLAGENT_VENV:-$HOME/.fullagent-venv}"

if [ ! -d "$VENV_DIR" ]; then
  echo ">> Creating virtualenv..."
  python3 -m venv "$VENV_DIR"
fi

echo ">> Installing FullAgent..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
# Prebuilt wheel (389KB) + deps — way faster than git+https install
"$VENV_DIR/bin/pip" install -q "https://github.com/${REPO}/releases/download/${VERSION}/fullagent-3.1.0-py3-none-any.whl"

mkdir -p "$INSTALL_DIR"
cat > "$INSTALL_DIR/$NAME" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/python" -m fullagent "\$@"
EOF
chmod +x "$INSTALL_DIR/$NAME"

echo ">> Installed: $INSTALL_DIR/$NAME"
echo ">> Make sure $INSTALL_DIR is in your PATH"
echo ">> Run: $NAME"
