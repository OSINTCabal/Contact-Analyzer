# How Contact Analyzer works

## 1. Subject and source profiles

A subject contains one or more source profile URLs. Each URL is assigned a
platform adapter and normalized before it is stored in SQLite.

The only canonical relationship names are:

- `followers`
- `following`
- `friends`

Other page concepts are never silently mapped into a relationship.

## 2. Visible browser control

Contact Analyzer connects to the dedicated Chrome instance over CDP. It opens
new visible tabs, navigates to the supplied source profile, locates rendered
relationship controls, and brings the relevant tab to the foreground.

Authentication belongs to Chrome. Contact Analyzer does not ask for passwords,
copy cookies, or implement official platform API clients.

## 3. Discovery

Known adapters provide conservative relationship routes, count hints, rendered
row selectors, and network-response keywords. When a platform requires
discovery, the collector inspects the controls that are actually visible in
the authenticated page and records what it verified.

Discovery is bounded. A plausible-looking navigation link is not enough to
declare that a relationship list exists.

## 4. Collection

The collector reads two views of the same browser session:

1. rendered DOM rows from the visible relationship list; and
2. JSON XHR/fetch responses generated as that visible list loads.

It scrolls virtualized lists, handles supported pagination, waits for content
to settle, and writes checkpoints so interrupted runs can resume.

Passive network capture supplements the rendered page; it does not introduce
separate credentials or a platform API client.

## 5. Canonicalization

Every candidate passes a platform-specific normalizer. The normalizer rejects
routes that represent posts, media, searches, topics, groups, repositories,
navigation, or other non-profile resources.

Increasing a count is never a reason to weaken this filter. A smaller trusted
set is preferable to false contacts.

## 6. Status and persistence

The collector compares the exact count rendered by the platform, when one is
available, with the number of unique canonical relationship URLs.

| Status | Meaning | Persistent contacts |
| --- | --- | --- |
| `verified` | Accumulated unique URLs equal the rendered count | Yes |
| `complete` | The exact supported list completed | Yes |
| `complete_accessible_list` | All browser-accessible rows completed; exact total unavailable | Yes |
| `incomplete` | Valid partial rows exist, but the list did not verify | Yes, with incomplete status retained |
| `private` | The authenticated browser cannot access the list | No new rows |
| `blocked` | Challenge or browser/platform block | No |
| `review` | Count or canonicalization mismatch needs review | No |
| `failed` | Collection failed before trusted completion | No |

Repeated runs update `last_seen_at` and relationship edges rather than
duplicating contacts. A partial run can become verified over later runs only
when the accumulated unique relationship URLs match an exact rendered count.

## 7. Exports and diagnostics

SQLite is the source of truth. After each collection step, Contact Analyzer
refreshes per-platform JSON, master JSON and Markdown, subject summaries, and
run reports.

Run directories can also contain checkpoints, screenshots, rendered HTML,
captured response summaries, and Codex output. These files are deliberately
kept out of Git because they may contain private information.

## 8. Codex

Generic rendered pages can use Codex to return structured person evidence
against the bundled JSON schema. That call uses `codex exec`, a read-only
sandbox, and an output schema. Deterministic validators still reject missing
evidence and invalid/non-profile URLs.

Adapter review is explicit. The `contactanalyzer codex` command builds a local
case bundle from the latest run and starts Codex with the project rules and
diagnostics. Users should review every proposed adapter change and rerun the
test suite.
