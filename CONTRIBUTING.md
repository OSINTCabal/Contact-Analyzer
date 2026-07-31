# Contributing

Contributions are welcome when they preserve Contact Analyzer's conservative
data and browser boundaries.

## Development setup

```bash
git clone https://github.com/OSINTCabal/Contact-Analyzer.git
cd Contact-Analyzer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Pull requests

- Keep platform-specific changes in `adapters.py` and targeted collection
  behavior in `collector.py`.
- Add focused tests for every accepted and rejected URL route.
- Preserve the canonical relationships `followers`, `following`, and `friends`.
- Keep incomplete runs explicitly incomplete.
- Never add official platform API clients, tokens, or copied cookies.
- Never submit real browser profiles, user data, screenshots, HTML captures,
  response bodies, case bundles, or databases.
- Use synthetic fixtures and reserved example domains/accounts.

Run before opening a pull request:

```bash
bash -n install.sh contactanalyzer scripts/launch-chrome.sh
for id in fedora debian ubuntu linuxmint kali; do
  CONTACT_ANALYZER_DISTRO_ID="$id" ./install.sh --system-requirements >/dev/null
done
python -m unittest discover -s tests -v
```

When reporting a live-platform regression, describe the rendered UI and redact
all personal information. A minimal synthetic HTML/JSON fixture is preferable
to a real capture.
