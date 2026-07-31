from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "contactanalyzer"
DEFAULT_VAULT = Path.home() / "Documents" / "Contact-Analyzer"
DEFAULT_CDP = "http://127.0.0.1:9222"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def default_config() -> dict[str, Any]:
    return {
        "vault_path": str(DEFAULT_VAULT),
        "cdp_endpoint": DEFAULT_CDP,
        "browser_launcher": "contactanalyzer-browser",
        "settings": {
            "settle_seconds": 3.0,
            "scroll_delay_seconds": 1.35,
            "stall_round_limit": 22,
            "max_scroll_rounds": 100000,
            "max_pagination_pages": 10000,
            "completion_retry_limit": 1,
            "codex_timeout_seconds": 900,
            "facebook_content_stall_round_limit": 12,
            "facebook_loading_content_stall_round_limit": 20,
            # A Facebook list can recycle a loading viewport without exposing
            # any relationship rows. Position changes alone must not make that
            # pass unbounded.
            "facebook_zero_row_max_rounds": 20,
            # Absolute guardrail for Facebook's virtualized lists. Rows found
            # before the limit remain valid; the relation is reported as
            # browser-limited when the UI does not settle.
            "facebook_relation_max_seconds": 90,
            "instagram_content_stall_round_limit": 20,
            "x_content_stall_round_limit": 18,
            "threads_content_stall_round_limit": 30,
            "threads_completion_pass_limit": 6,
            "threads_no_new_pass_limit": 2,
            "network_capture": True,
            "manual_rescue": True,
            "auto_discovery": True,
            "verbose_terminal": True,
        },
    }


def load_config() -> dict[str, Any]:
    path = config_path()
    defaults = default_config()
    if not path.exists():
        save_config(defaults)
        return defaults
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config root in {path}")
    data.setdefault("vault_path", defaults["vault_path"])
    data.setdefault("cdp_endpoint", defaults["cdp_endpoint"])
    data.setdefault("browser_launcher", defaults["browser_launcher"])
    settings = data.setdefault("settings", {})
    for key, value in defaults["settings"].items():
        settings.setdefault(key, value)
    return data


def save_config(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def vault_path(config: dict[str, Any]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(config["vault_path"]))))


def ensure_vault(config: dict[str, Any]) -> Path:
    vault = vault_path(config)
    (vault / ".contactanalyzer").mkdir(parents=True, exist_ok=True)
    (vault / "Subjects").mkdir(parents=True, exist_ok=True)
    return vault
