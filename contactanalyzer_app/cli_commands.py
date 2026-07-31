from __future__ import annotations

import json
from typing import Any

from .discovery import discover_profile
from .platform_catalog import coverage_rows, platform_for_url


def print_platform_coverage() -> int:
    rows = coverage_rows()
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title="Contact Analyzer platform coverage", expand=True)
        table.add_column("Platform", style="bold")
        table.add_column("Hosts", overflow="fold")
        table.add_column("Relationships")
        table.add_column("Mode")
        table.add_column("Notes", overflow="fold", ratio=2)
        for row in rows:
            mode = row["mode"]
            style = "green" if mode == "enumerable" else "yellow" if mode == "conditional" else "red" if mode in {"private", "none"} else "white"
            table.add_row(
                row["platform"],
                ", ".join(row["hosts"]) or "dynamic instance",
                ", ".join(row["relations"]) or "none",
                f"[{style}]{mode}[/{style}]",
                row["notes"],
            )
        console.print(table)
    except Exception:
        for row in rows:
            print(f"{row['platform']}\t{row['mode']}\t{','.join(row['relations'])}\t{','.join(row['hosts'])}")
    return 0


def discover_command(config: dict[str, Any], url: str) -> int:
    platform = platform_for_url(url)
    result = discover_profile(
        str(config["cdp_endpoint"]),
        url,
        platform,
        settle_seconds=float((config.get("settings") or {}).get("settle_seconds", 3.0)),
    )
    print(json.dumps({
        "platform": result.platform,
        "source_url": result.source_url,
        "graph_mode": result.graph_mode,
        "available_relations": result.available_relations,
        "notes": result.notes,
        "controls": result.controls,
    }, indent=2, ensure_ascii=False))
    return 0
