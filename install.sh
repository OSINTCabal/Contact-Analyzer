#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
INSTALL_DIR="${CONTACT_ANALYZER_INSTALL_DIR:-${DATA_HOME}/contact-analyzer}"
BIN_DIR="${CONTACT_ANALYZER_BIN_DIR:-${HOME}/.local/bin}"
INSTALL_MARKER=".contact-analyzer-install"

detect_linux_distro() {
    if [[ -n "${CONTACT_ANALYZER_DISTRO_ID:-}" ]]; then
        printf '%s\n' "$CONTACT_ANALYZER_DISTRO_ID"
        return 0
    fi

    local distro_id=""
    local distro_like=""
    local key
    local value

    if [[ -r /etc/os-release ]]; then
        while IFS='=' read -r key value; do
            value="${value#\"}"
            value="${value%\"}"
            value="${value#\'}"
            value="${value%\'}"
            case "$key" in
                ID) distro_id="$value" ;;
                ID_LIKE) distro_like="$value" ;;
            esac
        done </etc/os-release
    fi

    case "$distro_id" in
        fedora) printf 'fedora\n' ;;
        ubuntu) printf 'ubuntu\n' ;;
        linuxmint|mint) printf 'linuxmint\n' ;;
        kali) printf 'kali\n' ;;
        debian) printf 'debian\n' ;;
        *)
            case " $distro_like " in
                *" fedora "*) printf 'fedora\n' ;;
                *" ubuntu "*) printf 'ubuntu\n' ;;
                *" debian "*) printf 'debian\n' ;;
                *) printf '%s\n' "${distro_id:-unknown}" ;;
            esac
            ;;
    esac
}

print_system_requirements() {
    local distro="${1:-unknown}"

    echo "Contact Analyzer needs Python 3.10+ with venv, Codex CLI, and Chrome/Chromium."
    echo
    case "$distro" in
        fedora)
            cat <<'EOF'
Fedora:
  sudo dnf install python3 chromium
EOF
            ;;
        ubuntu)
            cat <<'EOF'
Ubuntu:
  sudo apt update
  sudo apt install python3 python3-venv
  sudo snap install chromium

Google Chrome's official .deb package is also supported.
EOF
            ;;
        linuxmint)
            cat <<'EOF'
Linux Mint:
  sudo apt update
  sudo apt install python3 python3-venv chromium
EOF
            ;;
        kali)
            cat <<'EOF'
Kali Linux:
  sudo apt update
  sudo apt install python3-full python3-venv chromium

Run Contact Analyzer and Chromium as a normal desktop user, not with sudo.
EOF
            ;;
        debian)
            cat <<'EOF'
Debian:
  sudo apt update
  sudo apt install python3 python3-venv chromium
EOF
            ;;
        *)
            cat <<'EOF'
Install Python 3.10 or newer with its venv module and Google Chrome or Chromium
using your operating system's package manager.
EOF
            ;;
    esac

    cat <<'EOF'

Install Codex CLI with one of the official methods:
  curl -fsSL https://chatgpt.com/codex/install.sh | sh
  npm install -g @openai/codex

Then authenticate:
  codex --version
  codex login
EOF
}

browser_available() {
    local candidate
    for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
        if command -v "$candidate" >/dev/null 2>&1; then
            return 0
        fi
    done
    for candidate in \
        /usr/bin/google-chrome-stable \
        /usr/bin/google-chrome \
        /usr/bin/chromium \
        /usr/bin/chromium-browser \
        /snap/bin/chromium \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
        if [[ -x "$candidate" ]]; then
            return 0
        fi
    done
    return 1
}

usage() {
    cat <<'EOF'
Usage:
  ./install.sh
  ./install.sh --system-requirements

Installs Contact Analyzer for the current user.

Optional environment variables:
  CONTACT_ANALYZER_INSTALL_DIR  Application directory
  CONTACT_ANALYZER_BIN_DIR      Directory for command symlinks
  XDG_DATA_HOME                 XDG data directory
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
if [[ "${1:-}" == "--system-requirements" && $# -eq 1 ]]; then
    if [[ "$(uname -s)" == "Linux" ]]; then
        print_system_requirements "$(detect_linux_distro)"
    else
        print_system_requirements "unknown"
    fi
    exit 0
fi
if [[ $# -ne 0 ]]; then
    usage >&2
    exit 2
fi

SYSTEM_NAME="$(uname -s)"
case "$SYSTEM_NAME" in
    Linux|Darwin) ;;
    *)
        echo "This installer currently supports Linux and macOS." >&2
        exit 1
        ;;
esac

if (( EUID == 0 )); then
    echo "Do not run this installer with sudo or as root." >&2
    echo "Contact Analyzer, its browser profile, and Chrome must belong to your desktop user." >&2
    exit 1
fi

if [[ "$SYSTEM_NAME" == "Linux" ]]; then
    DISTRO_FAMILY="$(detect_linux_distro)"
else
    DISTRO_FAMILY="unknown"
fi

if ! command -v codex >/dev/null 2>&1; then
    cat >&2 <<'EOF'
OpenAI Codex CLI was not found on PATH. Install it with one of the official methods:

  curl -fsSL https://chatgpt.com/codex/install.sh | sh
  npm install -g @openai/codex
  brew install --cask codex

Then run:

  codex --version
  codex login
  ./install.sh
EOF
    exit 1
fi

if ! CODEX_VERSION="$(codex --version 2>/dev/null)"; then
    echo "A 'codex' command exists but 'codex --version' failed." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required." >&2
    echo "Run './install.sh --system-requirements' for package commands." >&2
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "Python 3.10 or newer is required. Found: $(python3 --version 2>&1)" >&2
    echo "Run './install.sh --system-requirements' for package commands." >&2
    exit 1
fi

VENV_CHECK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/contact-analyzer-venv-check.XXXXXX")"
cleanup_venv_check() {
    if [[ -n "${VENV_CHECK_DIR:-}" && -d "$VENV_CHECK_DIR" ]]; then
        rm -rf -- "$VENV_CHECK_DIR"
    fi
}
trap cleanup_venv_check EXIT
if ! python3 -m venv "$VENV_CHECK_DIR/venv" >/dev/null 2>&1 ||
    ! "$VENV_CHECK_DIR/venv/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "Python is installed, but it cannot create a virtual environment with pip." >&2
    echo >&2
    print_system_requirements "$DISTRO_FAMILY" >&2
    exit 1
fi
cleanup_venv_check
VENV_CHECK_DIR=""
trap - EXIT

mkdir -p "$BIN_DIR"

if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
    if [[ -d "$INSTALL_DIR" && ! -f "$INSTALL_DIR/$INSTALL_MARKER" ]]; then
        if find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
            echo "Refusing to overwrite an unmanaged directory: $INSTALL_DIR" >&2
            echo "Choose another location with CONTACT_ANALYZER_INSTALL_DIR." >&2
            exit 1
        fi
    fi
    mkdir -p "$INSTALL_DIR"
    python3 - "$SOURCE_DIR" "$INSTALL_DIR" "$INSTALL_MARKER" <<'PY_COPY'
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
marker = sys.argv[3]

managed = (
    "contactanalyzer_app",
    "tests",
    "scripts",
    "docs",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "VERSION",
    "contactanalyzer",
    "install.sh",
    "pyproject.toml",
    "requirements.txt",
)

for name in managed:
    source_path = source / name
    if not source_path.exists():
        continue
    target_path = destination / name
    if target_path.exists() or target_path.is_symlink():
        if target_path.is_dir() and not target_path.is_symlink():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()
    if source_path.is_dir():
        shutil.copytree(
            source_path,
            target_path,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    else:
        shutil.copy2(source_path, target_path)

(destination / marker).write_text(
    "Managed by the Contact Analyzer installer.\n",
    encoding="utf-8",
)
PY_COPY
else
    touch "$INSTALL_DIR/$INSTALL_MARKER"
fi

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install --disable-pip-version-check \
    -r "$INSTALL_DIR/requirements.txt"

chmod +x \
    "$INSTALL_DIR/contactanalyzer" \
    "$INSTALL_DIR/install.sh" \
    "$INSTALL_DIR/scripts/launch-chrome.sh"
ln -sfn "$INSTALL_DIR/contactanalyzer" "$BIN_DIR/contactanalyzer"
ln -sfn "$INSTALL_DIR/scripts/launch-chrome.sh" "$BIN_DIR/contactanalyzer-browser"

echo
echo "Installed Contact Analyzer in: $INSTALL_DIR"
echo "Installed commands in: $BIN_DIR"
echo "Codex: $CODEX_VERSION"
if [[ "$SYSTEM_NAME" == "Linux" ]]; then
    echo "Linux family: $DISTRO_FAMILY"
fi

if codex login status >/dev/null 2>&1; then
    echo "Codex login: authenticated"
else
    echo "Codex login: run 'codex login' before Codex-assisted analysis"
fi

case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
        echo
        echo "Add this line to your shell profile, then open a new terminal:"
        echo "  export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac

if ! browser_available; then
    echo
    echo "Chrome/Chromium was not found. Install it before the browser step:"
    print_system_requirements "$DISTRO_FAMILY"
fi

echo
echo "Next:"
echo "  contactanalyzer-browser"
echo "  # Sign in to sites in the dedicated Chrome window"
echo "  contactanalyzer doctor"
echo "  contactanalyzer"
