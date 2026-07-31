# Browser setup

Contact Analyzer controls a visible, authenticated Chrome or Chromium window
through the Chrome DevTools Protocol (CDP) at:

```text
http://127.0.0.1:9222
```

The window must use a dedicated browser data directory. Starting with Chrome
136, `--remote-debugging-port` is ignored for the default Chrome data directory.
Google recommends a non-default `--user-data-dir` for this use case:
[Changes to remote debugging switches](https://developer.chrome.com/blog/remote-debugging-port).

## Recommended setup

After running `./install.sh`, launch:

```bash
contactanalyzer-browser
```

The launcher:

- finds Google Chrome or Chromium;
- creates a dedicated profile;
- binds remote debugging to loopback only;
- opens a normal visible browser window;
- waits until `http://127.0.0.1:9222/json/version` responds.

Default profile locations:

```text
Linux:  ~/.local/state/contact-analyzer/chrome-profile
macOS:  ~/.local/state/contact-analyzer/chrome-profile
```

If `XDG_STATE_HOME` is set, the profile is stored beneath that directory.

The first launch opens a clean profile. Sign in manually to each platform you
intend to collect. Login state remains in this dedicated profile between runs.

On Ubuntu, the launcher also detects `/snap/bin/chromium` and the
`/usr/bin/chromium-browser` transition wrapper. Its default Snap profile is
`~/snap/chromium/common/contact-analyzer-profile`, which is writable inside
Snap confinement. Other Linux packages use the XDG location above.

Distribution-specific browser and Python package commands are in
[Linux installation](LINUX_INSTALL.md).

## Verify the endpoint

Either command should report success:

```bash
contactanalyzer doctor
curl http://127.0.0.1:9222/json/version
```

The JSON response should include a `Browser` value and a
`webSocketDebuggerUrl`.

## Manual Linux command

Close any older Contact Analyzer Chrome window, create a profile directory,
and run:

```bash
mkdir -p "$HOME/.local/state/contact-analyzer/chrome-profile"
google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.local/state/contact-analyzer/chrome-profile" \
  --no-first-run \
  --no-default-browser-check
```

Depending on the package, the executable may be `google-chrome-stable`,
`chromium`, `/snap/bin/chromium`, or `chromium-browser`.

## Manual macOS command

Quit any older Contact Analyzer Chrome window, then run:

```bash
mkdir -p "$HOME/.local/state/contact-analyzer/chrome-profile"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.local/state/contact-analyzer/chrome-profile" \
  --no-first-run \
  --no-default-browser-check
```

## Custom Chrome executable or profile

Set these variables before starting the launcher:

```bash
export CONTACT_ANALYZER_CHROME_BIN=/absolute/path/to/chrome
export CONTACT_ANALYZER_CHROME_PROFILE=/absolute/path/to/dedicated-profile
export CONTACT_ANALYZER_CDP_PORT=9222
contactanalyzer-browser
```

If the CDP port changes, update Contact Analyzer too:

```bash
contactanalyzer config --cdp http://127.0.0.1:9333
export CONTACT_ANALYZER_CDP_PORT=9333
contactanalyzer-browser
```

## Troubleshooting

### Chrome opens but the health check fails

Check the exact Chrome command line at `chrome://version`. Confirm that both
`--remote-debugging-port=9222` and a non-default `--user-data-dir` are present.

Then check whether another process owns the port:

```bash
curl http://127.0.0.1:9222/json/version
```

If the endpoint is stale, close only the dedicated Contact Analyzer Chrome
window and start it again.

### The profile is already in use

Only one Chrome process can own a user-data directory. Close the dedicated
Contact Analyzer window before launching it again. Do not delete the profile
to resolve a lock while Chrome is still running.

### The wrong Chrome window opens

Chrome reuses an existing process when it receives the same profile directory.
Make sure `CONTACT_ANALYZER_CHROME_PROFILE` points to the dedicated directory,
not your everyday Chrome data directory.

### A platform shows a login screen, challenge, or rate limit

Resolve the condition manually in the visible browser. Do not paste cookies,
session tokens, or passwords into Contact Analyzer. If the list remains
unavailable, the run must remain private, blocked, or incomplete.

### Chrome reports that it cannot run as root

Run both the installer and browser launcher as your normal graphical desktop
user. This is especially relevant on Kali Linux. Contact Analyzer does not add
`--no-sandbox`, because disabling Chrome's sandbox would weaken the browser
security boundary.

## Security boundary

The DevTools endpoint controls everything visible to that Chrome profile.

- Never bind it to `0.0.0.0` or a LAN address.
- Never use your default everyday Chrome profile.
- Never sync or publish the dedicated profile.
- Do not run untrusted local software while the endpoint is available.
- Close the dedicated browser when collection is finished.
