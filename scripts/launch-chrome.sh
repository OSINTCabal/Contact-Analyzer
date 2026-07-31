#!/usr/bin/env bash
set -euo pipefail

CDP_HOST="${CONTACT_ANALYZER_CDP_HOST:-127.0.0.1}"
CDP_PORT="${CONTACT_ANALYZER_CDP_PORT:-9222}"
STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
PROFILE_DIR="${CONTACT_ANALYZER_CHROME_PROFILE:-${STATE_HOME}/contact-analyzer/chrome-profile}"
PROFILE_WAS_EXPLICIT=0
if [[ -n "${CONTACT_ANALYZER_CHROME_PROFILE:-}" ]]; then
    PROFILE_WAS_EXPLICIT=1
fi
ENDPOINT="http://${CDP_HOST}:${CDP_PORT}"

if (( EUID == 0 )); then
    echo "Refusing to run Chrome as root." >&2
    echo "Run contactanalyzer-browser as your normal desktop user and do not use sudo." >&2
    exit 1
fi
if [[ "$CDP_HOST" != "127.0.0.1" ]]; then
    echo "Refusing to expose Chrome DevTools outside 127.0.0.1." >&2
    exit 1
fi
if [[ ! "$CDP_PORT" =~ ^[0-9]+$ ]] || (( CDP_PORT < 1 || CDP_PORT > 65535 )); then
    echo "CONTACT_ANALYZER_CDP_PORT must be an integer from 1 through 65535." >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to verify the Chrome DevTools endpoint." >&2
    exit 1
fi

cdp_ready() {
    python3 - "$ENDPOINT/json/version" <<'PY_CHECK'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.5) as response:
        payload = json.load(response)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if payload.get("webSocketDebuggerUrl") else 1)
PY_CHECK
}

find_chrome() {
    if [[ -n "${CONTACT_ANALYZER_CHROME_BIN:-}" ]]; then
        if [[ -x "$CONTACT_ANALYZER_CHROME_BIN" ]]; then
            printf '%s\n' "$CONTACT_ANALYZER_CHROME_BIN"
            return 0
        fi
        echo "CONTACT_ANALYZER_CHROME_BIN is not executable: $CONTACT_ANALYZER_CHROME_BIN" >&2
        return 1
    fi

    local candidate
    for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done

    for candidate in \
        "/usr/bin/google-chrome-stable" \
        "/usr/bin/google-chrome" \
        "/usr/bin/chromium" \
        "/usr/bin/chromium-browser" \
        "/snap/bin/chromium" \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if cdp_ready; then
    echo "Chrome DevTools is already available at $ENDPOINT"
    exit 0
fi

if ! CHROME_BIN="$(find_chrome)"; then
    cat >&2 <<'EOF'
Google Chrome or Chromium was not found.

Install Google Chrome, or set the executable explicitly:

  export CONTACT_ANALYZER_CHROME_BIN=/absolute/path/to/chrome
  contactanalyzer-browser
EOF
    exit 1
fi

# Ubuntu's Chromium snap cannot reliably use arbitrary hidden directories.
# Keep its default Contact Analyzer profile inside the snap-writable area.
if (( PROFILE_WAS_EXPLICIT == 0 )); then
    case "$CHROME_BIN" in
        /snap/bin/chromium|/usr/bin/chromium-browser)
            PROFILE_DIR="${HOME}/snap/chromium/common/contact-analyzer-profile"
            ;;
    esac
fi

mkdir -p "$PROFILE_DIR"
PROFILE_DIR="$(cd -- "$PROFILE_DIR" && pwd -P)"

echo "Starting a dedicated visible Chrome window"
echo "  Browser: $CHROME_BIN"
echo "  Profile: $PROFILE_DIR"
echo "  DevTools: $ENDPOINT"

nohup "$CHROME_BIN" \
    "--remote-debugging-address=$CDP_HOST" \
    "--remote-debugging-port=$CDP_PORT" \
    "--user-data-dir=$PROFILE_DIR" \
    --no-first-run \
    --no-default-browser-check \
    about:blank \
    >/dev/null 2>&1 &

for (( attempt = 0; attempt < 40; attempt++ )); do
    if cdp_ready; then
        echo "Chrome is ready. Sign in to the sites you intend to collect."
        exit 0
    fi
    sleep 0.5
done

echo "Chrome started, but DevTools did not become ready at $ENDPOINT." >&2
echo "Run the browser command in docs/BROWSER_SETUP.md manually to see Chrome errors." >&2
exit 1
