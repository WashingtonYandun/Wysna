#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-$HOME/.local/bin}"

echo "=================================================="
echo "Wysna Language toolchain installer"
echo "=================================================="

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 is required (>= 3.10)." >&2
    exit 1
fi

if ! command -v cc >/dev/null 2>&1; then
    echo "[ERROR] A C99 compiler (cc / clang / gcc) is required." >&2
    exit 1
fi

chmod +x "$REPO_ROOT/wysna"

echo "[1/2] Pre-building Stage-2 native self-hosted Wysna compiler..."
mkdir -p "$REPO_ROOT/.wysna_cache"
"$REPO_ROOT/wysna" build --engine=compiler "$REPO_ROOT/selfhost/compiler.wysna" -o "$REPO_ROOT/.wysna_cache/wysna_selfhost"

echo "[2/2] Installing 'wysna' CLI into $TARGET_DIR..."
mkdir -p "$TARGET_DIR"
ln -sf "$REPO_ROOT/wysna" "$TARGET_DIR/wysna"

echo "=================================================="
echo "[OK] Installed: $TARGET_DIR/wysna -> $REPO_ROOT/wysna"
"$TARGET_DIR/wysna" version
echo "=================================================="

case ":$PATH:" in
    *":$TARGET_DIR:"*)
        echo "You can now run 'wysna' from any directory."
        ;;
    *)
        echo "NOTE: Add $TARGET_DIR to your PATH if not already present:"
        echo "  export PATH=\"$TARGET_DIR:\$PATH\""
        ;;
esac
