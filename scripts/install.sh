#!/bin/sh
# Copyright 2026 WySofia S.A.S. (Apache-2.0)
# Wysna one-line installer for macOS (arm64) and Linux (x86_64).
# Usage:
#   curl -fsSL https://wysna.wysofia.com/install.sh | sh
# Options:
#   --version <vX.Y.Z|latest>   --prefix <dir>   --no-modify-path   --uninstall

set -eu

REPO="${WYSNA_REPO:-WashingtonYandun/Wysna}"
VERSION="${WYSNA_VERSION:-latest}"
INSTALL_DIR="${WYSNA_INSTALL_DIR:-$HOME/.local/share/wysna}"
BIN_DIR="${WYSNA_BIN_DIR:-$HOME/.local/bin}"
BASE_URL_OVERRIDE="${WYSNA_BASE_URL:-}"
NO_PATH="${WYSNA_NO_PATH:-0}"
UNINSTALL="${WYSNA_UNINSTALL:-0}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:?missing value for --version}"
            shift 2
            ;;
        --prefix)
            PREFIX="${2:?missing value for --prefix}"
            INSTALL_DIR="$PREFIX/share/wysna"
            BIN_DIR="$PREFIX/bin"
            shift 2
            ;;
        --no-modify-path)
            NO_PATH=1
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        -h|--help)
            cat <<'EOF'
Wysna Native SDK installer

Usage:
  curl -fsSL https://wysna.wysofia.com/install.sh | sh
  curl -fsSL https://wysna.wysofia.com/install.sh | sh -s -- [options]

Options:
  --version <vX.Y.Z|latest>  Install a specific release tag (default: latest)
  --prefix <dir>             Install under <dir>/share/wysna with symlink in <dir>/bin
  --no-modify-path           Do not update shell rc files
  --uninstall                Remove Wysna installation and symlink
EOF
            exit 0
            ;;
        *)
            echo "[Wysna] Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [ "$UNINSTALL" = "1" ]; then
    echo "[Wysna] Uninstalling from $INSTALL_DIR..."
    if [ -L "$BIN_DIR/wysna" ] || [ -f "$BIN_DIR/wysna" ]; then
        rm -f "$BIN_DIR/wysna"
    fi
    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
    fi
    echo "[Wysna] Uninstalled cleanly."
    exit 0
fi

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[Wysna] Required command not found: $1" >&2
        exit 1
    fi
}

need_cmd curl
need_cmd tar

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"

case "$OS_NAME/$ARCH_NAME" in
    Darwin/arm64|Darwin/aarch64)
        TARGET_PLATFORM="aarch64-macos"
        ;;
    Linux/x86_64|Linux/amd64)
        TARGET_PLATFORM="x86_64-linux"
        ;;
    *)
        echo "[Wysna] Unsupported platform: $OS_NAME ($ARCH_NAME)." >&2
        echo "[Wysna] Native SDK releases support macOS arm64 and Linux x86_64 (and Windows x64 via install.ps1)." >&2
        exit 1
        ;;
esac

resolve_latest_tag() {
    effective="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" 2>/dev/null || true)"
    case "$effective" in
        */releases/tag/*)
            printf '%s\n' "${effective##*/releases/tag/}"
            return 0
            ;;
    esac
    tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
    if [ -z "$tag" ]; then
        echo "[Wysna] Could not resolve latest Wysna release tag." >&2
        exit 1
    fi
    printf '%s\n' "$tag"
}

verify_sha256() {
    sums_file="$1"
    asset_file="$2"
    asset_name="$3"
    expected="$(awk -v name="$asset_name" '$2 == name || $2 == ("*" name) { print tolower($1); exit }' "$sums_file")"
    if [ -z "$expected" ]; then
        echo "[Wysna] Checksum entry for $asset_name missing in SHA256SUMS." >&2
        exit 1
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$asset_file" | awk '{print tolower($1)}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$asset_file" | awk '{print tolower($1)}')"
    else
        echo "[Wysna] Neither sha256sum nor shasum found to verify download." >&2
        exit 1
    fi
    if [ "$actual" != "$expected" ]; then
        echo "[Wysna] SHA-256 mismatch for $asset_name (expected $expected, got $actual)." >&2
        exit 1
    fi
}

if [ "$VERSION" = "latest" ]; then
    TAG="$(resolve_latest_tag)"
else
    case "$VERSION" in
        v*) TAG="$VERSION" ;;
        *) TAG="v$VERSION" ;;
    esac
fi

VER_NUM="${TAG#v}"
ASSET_NAME="wysna-$VER_NUM-native-$TARGET_PLATFORM.tar.gz"
BASE_URL="${BASE_URL_OVERRIDE%/}"
if [ -z "$BASE_URL" ]; then
    BASE_URL="https://github.com/$REPO/releases/download/$TAG"
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wysna-install.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

echo "[Wysna] Installing $TAG ($TARGET_PLATFORM)..."
echo "[Wysna] Downloading $ASSET_NAME..."
curl -fsSL "$BASE_URL/$ASSET_NAME" -o "$TMP_DIR/$ASSET_NAME"
curl -fsSL "$BASE_URL/SHA256SUMS" -o "$TMP_DIR/SHA256SUMS"

verify_sha256 "$TMP_DIR/SHA256SUMS" "$TMP_DIR/$ASSET_NAME" "$ASSET_NAME"
echo "[Wysna] SHA-256 verified."

mkdir -p "$TMP_DIR/extract"
tar -xzf "$TMP_DIR/$ASSET_NAME" -C "$TMP_DIR/extract"

if [ -d "$TMP_DIR/extract/wysna-native" ]; then
    PAYLOAD_DIR="$TMP_DIR/extract/wysna-native"
else
    PAYLOAD_DIR="$TMP_DIR/extract"
fi

rm -rf "$INSTALL_DIR"
mkdir -p "$(dirname "$INSTALL_DIR")"
mv "$PAYLOAD_DIR" "$INSTALL_DIR"

mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/bin/wysna" "$BIN_DIR/wysna"

if [ "$NO_PATH" != "1" ]; then
    case ":${PATH:-}:" in
        *":$BIN_DIR:"*) ;;
        *)
            PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""
            for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
                if [ -f "$rc" ] && grep -Fqs "$BIN_DIR" "$rc"; then
                    PATH_LINE=""
                    break
                fi
            done
            if [ -n "$PATH_LINE" ]; then
                TARGET_RC="$HOME/.profile"
                case "${SHELL:-}" in
                    */zsh) TARGET_RC="$HOME/.zshrc" ;;
                    */bash) TARGET_RC="$HOME/.bashrc" ;;
                esac
                printf '\n# Added by Wysna installer\n%s\n' "$PATH_LINE" >> "$TARGET_RC"
                echo "[Wysna] Added $BIN_DIR to $TARGET_RC"
            fi
            ;;
    esac
fi

VERSION_STR="$("$BIN_DIR/wysna" --version)"
echo ""
echo "[OK] Installed $VERSION_STR"
echo "     SDK      : $INSTALL_DIR"
echo "     Command  : $BIN_DIR/wysna"
echo ""
echo "Run 'wysna --version' (restart your terminal if PATH was just updated)."
