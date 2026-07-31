# Contact Analyzer

**By [OSINT Cabal](https://osintcabal.org)**

Contact Analyzer collects social profile relationships through a visible
Google Chrome or Chromium session that you control. It opens rendered
relationship lists, scrolls or paginates them, reads browser generated network
responses when useful, and saves canonical profile URLs.

It does not require official social network APIs, developer accounts, API
tokens, or copied cookies.

Contact Analyzer is built for authorized OSINT research where an investigator
needs repeatable contact collection, clear source relationships, and honest
reporting when a platform does not expose a complete list.

## Features

* Collects `followers`, `following`, and `friends`
* Uses your visible and authenticated browser session
* Supports Google Chrome and Chromium
* Deduplicates canonical profile URLs in SQLite
* Preserves relationship sources without duplicating contacts
* Writes Markdown and JSON that work well in Obsidian
* Saves checkpoints and diagnostics for incomplete runs
* Keeps blocked, failed, and review results out of the persistent contact set
* Uses Codex for bounded page analysis and optional diagnostic review
* Requires no official platform API keys

## How it works

1. You create a subject and add known profile URLs.
2. Contact Analyzer connects to the dedicated browser at
   `http://127.0.0.1:9222`.
3. The collector opens relationship lists that are visible to your account.
4. It reads rendered rows and browser generated XHR or fetch responses.
5. Platform adapters reject posts, media, search pages, groups, repositories,
   hashtags, and other navigation URLs.
6. Accepted profiles are normalized to canonical profile URLs.
7. SQLite records unique contacts and their source relationship edges.
8. The exporter writes the current contact set, run history, and diagnostics.

If a platform reports a count but exposes fewer unique profiles, the run stays
incomplete. Contact Analyzer does not turn a partial list into a complete one.

## Supported systems

Contact Analyzer supports current desktop releases of:

* Fedora Linux
* Debian
* Ubuntu
* Linux Mint
* Kali Linux
* macOS

Python 3.10 or newer is required. Linux needs the Python `venv` module.
Google Chrome is recommended. Chromium is supported as a fallback.

The Linux installer recognizes Fedora, Debian, Ubuntu, Linux Mint, and Kali.
It prints the correct package commands when Python or a browser requirement is
missing.

See [Linux installation](docs/LINUX_INSTALL.md) for distribution specific
commands.

## Installation

Install and authenticate the OpenAI Codex CLI first:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
codex login
```

You can also install Codex with npm:

```bash
npm install -g @openai/codex
codex login
```

Clone Contact Analyzer and run the installer:

```bash
git clone https://github.com/OSINTCabal/Contact-Analyzer.git
cd Contact-Analyzer
./install.sh
hash -r
```

The installer checks Codex, Python, `venv`, and pip before copying the
application. It installs Contact Analyzer for the current user and creates:

```text
~/.local/share/contact-analyzer
~/.local/bin/contactanalyzer
~/.local/bin/contactanalyzer-browser
```

Do not run the installer with `sudo`.

To print the package requirements without installing anything:

```bash
./install.sh --system-requirements
```

## Browser setup

Start the dedicated browser:

```bash
contactanalyzer-browser
```

The launcher finds Google Chrome or Chromium, creates a separate browser
profile, and enables Chrome DevTools only on `127.0.0.1:9222`.

Sign in manually to the platforms you plan to collect. Keep this profile
separate from your everyday browser profile.

Verify the connection:

```bash
contactanalyzer doctor
```

Chrome 136 and newer require remote debugging to use a separate user data
directory. The included launcher handles this automatically. Ubuntu Chromium
Snap installations use a profile inside `~/snap/chromium/common/` so the
browser can write to it.

See [Browser setup](docs/BROWSER_SETUP.md) for manual commands and
troubleshooting.

## Usage

Open the interactive subject menu:

```bash
contactanalyzer
```

Create a subject and paste profile URLs:

```text
here's my next subject Example Person
https://www.instagram.com/example
https://x.com/example
https://bsky.app/profile/example.bsky.social
https://github.com/example
DONE
```

Contact Analyzer identifies the platforms, finds available relationship
controls, and collects only canonical profile URLs.

## Commands

| Command | Purpose |
| --- | --- |
| `contactanalyzer` | Open the interactive subject menu |
| `contactanalyzer subjects` | List saved subjects |
| `contactanalyzer new` | Create and run a subject from pasted URLs |
| `contactanalyzer add "Example Person" --run` | Add profile URLs and collect them |
| `contactanalyzer run "Example Person"` | Run or resume saved profiles |
| `contactanalyzer platforms` | Show platform coverage |
| `contactanalyzer discover URL` | Inspect visible relationship controls |
| `contactanalyzer export "Example Person"` | Refresh JSON and Markdown exports |
| `contactanalyzer audit "Example Person"` | Check database and export integrity |
| `contactanalyzer codex "Example Person"` | Review the latest run with Codex |
| `contactanalyzer doctor` | Check the browser, database, vault, and Codex |
| `contactanalyzer config --vault PATH` | Change the output vault |
| `contactanalyzer --version` | Print the installed version |

More examples are available in
[Usage and configuration](docs/USAGE.md).

## Output

The default vault is:

```text
~/Documents/Contact-Analyzer/
```

The output is ordinary Markdown and JSON:

```text
Contact-Analyzer/
├── .contactanalyzer/
│   └── contactanalyzer.sqlite3
└── Subjects/
    └── example-person/
        ├── Summary.md
        ├── Master Contacts.md
        ├── master_contacts.json
        ├── platforms/
        └── runs/
            └── YYYYMMDD-HHMMSS/
                ├── Run Summary.md
                ├── run.json
                └── platform/profile-id/
                    ├── followers.json
                    ├── followers.checkpoint.json
                    └── diagnostics/
```

A contact is unique by subject, platform, and canonical profile URL. One
contact can retain several source relationships while appearing only once in
master exports.

## Codex integration

Codex is used in two limited ways.

First, generic website pages can be analyzed from rendered browser evidence
with a JSON schema and a read only sandbox.

Second, you can ask Codex to review the latest run diagnostics:

```bash
contactanalyzer run "Example Person" --with-codex
contactanalyzer run "Example Person" --with-codex-exec
contactanalyzer codex "Example Person"
contactanalyzer codex "Example Person" --exec
```

Generated case bundles are stored under `codex_cases/` and ignored by Git.
Review them before sharing.

## Notes

* Use Contact Analyzer only on accounts and data you are authorized to access.
* Follow applicable law, platform terms, rate limits, and organizational
  policy.
* A visible relationship does not guarantee that the full list is available.
* Private lists, platform caps, challenges, and rate limits remain explicit in
  the final status.
* Never publish the dedicated browser profile, vault, database, screenshots,
  rendered HTML, captured responses, or diagnostic bundles.
* Close the dedicated browser when collection is finished.

The Chrome DevTools endpoint controls everything visible to the dedicated
profile. Contact Analyzer binds it to the loopback interface and refuses
external addresses.

Read [Security policy](SECURITY.md) before reporting a vulnerability.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
bash -n install.sh contactanalyzer scripts/launch-chrome.sh
```

The test suite uses synthetic fixtures and does not require a live browser.

See [Contributing](CONTRIBUTING.md) and the
[publishing checklist](docs/PUBLISHING.md).

## License

Contact Analyzer is available under the [MIT License](LICENSE).

Built for the OSINT Cabal community.
