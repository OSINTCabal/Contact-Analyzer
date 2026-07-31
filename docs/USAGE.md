# Usage and configuration

## Interactive workflow

Run:

```bash
contactanalyzer
```

The first screen lists saved subjects. You can create a subject, select an
existing one, add URLs, run or resume collection, refresh exports, open the
summary, or start a Codex review.

Paste input ends when `DONE` appears on its own line:

```text
here's my next subject Example Person
https://x.com/example
https://github.com/example
DONE
```

## Direct commands

```bash
contactanalyzer subjects
contactanalyzer platforms
contactanalyzer discover https://x.com/example
contactanalyzer new --name "Example Person"
contactanalyzer new --name "Example Person" --no-run
contactanalyzer add "Example Person"
contactanalyzer add "Example Person" --run
contactanalyzer run "Example Person"
contactanalyzer export "Example Person"
contactanalyzer audit "Example Person"
contactanalyzer doctor
```

## Codex review

Run collection and then open an interactive review:

```bash
contactanalyzer run "Example Person" --with-codex
```

Run a non-interactive post-run review:

```bash
contactanalyzer run "Example Person" --with-codex-exec
```

Review the latest saved run without collecting again:

```bash
contactanalyzer codex "Example Person"
contactanalyzer codex "Example Person" --exec
```

Confirm authentication if a Codex command fails:

```bash
codex --version
codex login status
codex login
```

## Configuration

The default configuration file is:

```text
~/.config/contactanalyzer/config.json
```

`XDG_CONFIG_HOME` is honored.

Change the output vault:

```bash
contactanalyzer config --vault "$HOME/Documents/My Contact Vault"
```

Change the CDP endpoint and launcher:

```bash
contactanalyzer config \
  --cdp http://127.0.0.1:9222 \
  --browser-launcher contactanalyzer-browser
```

Print the active configuration:

```bash
contactanalyzer config
```

## Resume and checkpoints

Each run has a timestamped directory. Relationship checkpoints preserve valid
progress during long virtualized lists. Re-running the subject merges canonical
URLs into the existing relationship without duplicating contacts.

An interrupted or browser-limited run stays explicitly incomplete. Do not edit
the status to `complete` by hand.

## Auditing

Run:

```bash
contactanalyzer audit "Example Person"
```

The audit checks duplicate contact keys, duplicate relationship edges, and
duplicate URLs in generated JSON. It exits nonzero if duplicates are found.

## Data removal

Contact Analyzer treats deletion of a subject folder under `Subjects/` as an
intentional deletion. On the next startup it removes the corresponding SQLite
rows after creating a database backup under:

```text
<vault>/.contactanalyzer/backups/
```

Back up the entire vault before deleting or moving subject folders.
