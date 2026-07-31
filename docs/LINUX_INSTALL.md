# Linux installation

Contact Analyzer supports current Fedora, Debian, Ubuntu, Linux Mint, and Kali
Linux desktop releases that provide Python 3.10 or newer. Installation is
per-user: do not run `install.sh`, `contactanalyzer`, or
`contactanalyzer-browser` with `sudo`.

The installer does not modify system packages. It detects the Linux family,
checks Codex and Python, verifies that Python can create a virtual environment
with pip, and prints an exact package command if a prerequisite is missing.

To print the commands without installing anything:

```bash
./install.sh --system-requirements
```

## Browser choice

Google Chrome is the recommended browser when its official Linux package is
available. Download the `.deb` or `.rpm` from the
[official Chrome download page](https://www.google.com/chrome/) and install the
downloaded file:

```bash
# Debian, Ubuntu, Linux Mint, or Kali on x86-64
sudo apt install ./google-chrome-stable_current_amd64.deb

# Fedora on x86-64
sudo dnf install ./google-chrome-stable_current_x86_64.rpm
```

Run those commands from the directory containing the download. On other CPU
architectures, or when you prefer a distribution-maintained package, use the
Chromium commands below. The launcher checks Google Chrome first and then
Chromium.

## Fedora

Install Python and Fedora's Chromium fallback:

```bash
sudo dnf install python3 chromium
```

Google Chrome is also supported. Fedora documents both Chromium and the
optional Google Chrome third-party repository in its
[browser installation guide](https://docs.fedoraproject.org/en-US/quick-docs/installing-chromium-or-google-chrome-browsers/).

## Debian

Install Python and Debian's Chromium fallback:

```bash
sudo apt update
sudo apt install python3 python3-venv chromium
```

The `python3-venv` package supplies the `venv` module for Debian's default
Python. See the [Debian package page](https://packages.debian.org/stable/python/python3-venv).

## Ubuntu

Install Python and its virtual-environment module. If you did not install the
Google Chrome `.deb`, install Ubuntu's Chromium fallback from Snap:

```bash
sudo apt update
sudo apt install python3 python3-venv
```

Then install Chromium from Snap:

```bash
sudo snap install chromium
```

Ubuntu's `chromium-browser` APT package is a transition to the Chromium Snap.
The launcher detects both `/snap/bin/chromium` and the transitional wrapper.
For the Snap, it puts the dedicated profile beneath
`~/snap/chromium/common/`, where the confined browser can write it. Google
Chrome's official `.deb` package is also supported.

Ubuntu publishes `python3-venv` for its supported releases and documents
Chromium as a snapped browser:
[Ubuntu package information](https://packages.ubuntu.com/search?keywords=python3-venv&searchon=names&section=all),
[Chromium package transition](https://packages.ubuntu.com/search?keywords=chromium).

## Linux Mint

Install Python and Linux Mint's Chromium fallback:

```bash
sudo apt update
sudo apt install python3 python3-venv chromium
```

Both Linux Mint's native Chromium package and Google Chrome are detected.

## Kali Linux

Install Python and Kali's Chromium fallback:

```bash
sudo apt update
sudo apt install python3-full python3-venv chromium
```

Use a normal graphical desktop account. Contact Analyzer intentionally refuses
to start Chrome as root and never adds Chrome's unsafe `--no-sandbox` flag.
Kali recommends virtual environments for third-party Python packages:
[Kali Python guidance](https://www.kali.org/docs/general-use/python3-external-packages/).
Its [Chromium package documentation](https://www.kali.org/tools/chromium/)
also documents the separate `--user-data-dir` needed for independent browser
instances.

## Install Contact Analyzer

After the system packages and Codex CLI are available:

```bash
codex --version
codex login
./install.sh
hash -r
contactanalyzer-browser
```

Sign in to the supported sites in the dedicated visible browser window, then
verify the connection:

```bash
contactanalyzer doctor
contactanalyzer
```

The browser uses only `127.0.0.1:9222`; it is never exposed to the LAN. See
[Browser setup](BROWSER_SETUP.md) for manual commands and troubleshooting.
