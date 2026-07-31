# Changelog

All notable changes to Contact Analyzer are documented here.

## 1.2.1

- Prepared a self-contained MIT-licensed public repository.
- Added a per-user installer that verifies Codex CLI and Python before install.
- Added a bundled Linux/macOS Chrome launcher with a dedicated user-data
  directory and a loopback-only DevTools endpoint.
- Added distro-aware prerequisite guidance for Fedora, Debian, Ubuntu, Linux
  Mint, and Kali Linux, with install-time `venv` and pip preflight checks.
- Added Ubuntu Chromium Snap detection and a confinement-writable dedicated
  profile path.
- Refused root installation/browser execution instead of disabling Chrome's
  sandbox.
- Added complete browser, usage, architecture, security, contribution, and
  publishing documentation.
- Added platform discovery, expanded reporting, website-person evidence
  validation, cumulative collection status, and focused tests.
- Canonical URL deduplication occurs before completion status is calculated.
- Review, failed, and blocked collections remain in diagnostics and are not
  committed to the persistent database.
- Incomplete collections may contribute valid partial contacts while retaining
  an explicit incomplete status.
- Added `contactanalyzer audit SUBJECT` to verify database and exported JSON
  uniqueness.
