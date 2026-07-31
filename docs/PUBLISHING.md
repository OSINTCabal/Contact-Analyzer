# Publishing checklist

This repository is designed to be uploaded without browser profiles, case
bundles, databases, or collected subject data.

## Before the first push

1. Review `LICENSE` and replace the copyright holder if needed.
2. Search the tree for credentials, local paths, and collected artifacts:

   ```bash
   rg -n -i 'api[_-]?key|authorization|bearer|cookie|password|sessionid|/home/|/Users/'
   find . -type f \( -name '*.sqlite3' -o -name '*.db' -o -name '*.png' -o -name '*.html' \)
   ```

3. Run the complete checks:

   ```bash
   bash -n install.sh contactanalyzer scripts/launch-chrome.sh
   for id in fedora debian ubuntu linuxmint kali; do
     CONTACT_ANALYZER_DISTRO_ID="$id" ./install.sh --system-requirements >/dev/null
   done
   python -m unittest discover -s tests -v
   ```

4. Inspect the exact files Git will publish:

   ```bash
   git status --short
   git diff --cached
   ```

## Create the repository locally

```bash
git init -b main
git add .
git commit -m "Initial open-source release"
```

## Push with GitHub CLI

After creating or authenticating a GitHub account:

```bash
gh auth login
gh repo create OSINTCabal/Contact-Analyzer --public --source=. --remote=origin --push
```

Or create an empty repository in the GitHub web interface and follow the
displayed commands to add `origin` and push `main`.

## First GitHub settings

- Add a short repository description and topics.
- Enable Issues and Discussions if you want community support.
- Confirm the Actions test workflow passes.
- Enable secret scanning and Dependabot alerts when available.
- Do not attach real run diagnostics to a public issue.
