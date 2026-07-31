from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

URL_RE = re.compile(r"https?://[^\s<>'\"\]\[(){}]+", re.I)
NAME_PREFIX_RE = re.compile(
    r"^\s*(?:here(?:'|’)?s\s+)?(?:my\s+)?(?:next\s+)?subject\s*(?::|-)?\s*",
    re.I,
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "subject"


def normalize_url_text(raw: str) -> str:
    return raw.rstrip(".,;:!?)]}>'\"")


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.findall(text):
        url = normalize_url_text(match)
        key = url.rstrip("/").casefold()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def extract_name(text: str, explicit_name: str | None = None) -> str | None:
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()
    without_urls = URL_RE.sub("", text)
    lines = [re.sub(r"\s+", " ", line).strip(" \t:-") for line in without_urls.splitlines()]
    lines = [line for line in lines if line]
    for line in lines:
        stripped = NAME_PREFIX_RE.sub("", line).strip(" .,:;-")
        if stripped != line or re.search(r"\bsubject\b", line, re.I):
            stripped = re.sub(r"^(?:name\s+is|is)\s+", "", stripped, flags=re.I).strip()
            if 1 <= len(stripped.split()) <= 20:
                return stripped
    if lines:
        candidate = lines[0].strip(" .,:;-")
        if 1 <= len(candidate.split()) <= 20:
            return candidate
    return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def parse_count(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().lower().replace("\u00a0", " ")
    match = re.search(r"(?<![\w.])(\d[\d,\.\s]*)([kmb])?(?!\w)", text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    suffix = match.group(2)
    try:
        if suffix:
            number = float(raw.replace(",", ""))
            return int(round(number * {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]))
        # A bare period in a count is usually a thousands separator on non-US locales.
        return int(raw.replace(",", "").replace(".", ""))
    except ValueError:
        return None
