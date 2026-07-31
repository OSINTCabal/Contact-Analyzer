# Security policy

## Supported version

Security fixes are applied to the latest release on the default branch.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose Chrome
sessions, authentication state, collected personal data, or local files.
Use GitHub's private vulnerability reporting feature after the repository is
published. If that feature is not enabled, contact the repository owner through
a private channel listed on their GitHub profile.

Include:

- the affected version;
- the minimum steps to reproduce;
- the expected and observed security boundary;
- whether browser session data or collected output may be exposed.

Do not include live credentials, cookies, tokens, browser profiles, or
unredacted subject data.

## Browser security

Chrome DevTools can control the entire dedicated profile. The bundled launcher
binds it to `127.0.0.1` and refuses other addresses. Do not alter it to expose
the port on a LAN or the public internet.

Always use the dedicated non-default profile. Close it when collection is
finished, keep the profile directory private, and never commit or upload it.

## Data security

The SQLite database, JSON/Markdown exports, checkpoints, screenshots, rendered
HTML, captured network data, and Codex case bundles may contain personal or
private information. Store them according to your organization's data
retention and access-control policy.
