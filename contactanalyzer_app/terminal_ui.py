from __future__ import annotations

import io
import re
import sys
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from typing import Any, Iterator

from .collection_status import (
    SUCCESS_STATUSES,
    reason_label,
    relationship_coverage_note,
    status_label,
)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
except Exception:  # pragma: no cover - terminal fallback
    RICH_AVAILABLE = False


_PROGRESS_RE = re.compile(
    r"\[(?P<platform>[^:\]]+):(?P<relation>[^\]]+)\]\s+"
    r"(?P<current>\d+)/(?P<total>\d+|\?)\s+"
    r"round=(?P<round>\d+)\s+added=(?P<added>\d+)\s+"
    r"stalls=(?P<stalls>\d+)(?:/(?P<content_stalls>\d+))?\s+pages=(?P<pages>\d+)"
    r"(?:\s+pass=(?P<pass>\d+))?"
    r"(?:\s+visible=(?P<visible>\d+)\s+first=(?P<first>\S+)\s+last=(?P<last>\S+)"
    r"\s+scroll=(?P<scroll_top>\d+)/(?P<scroll_height>\d+)"
    r"(?:\s+viewport=(?P<viewport>\d+))?)?",
    re.I,
)
_COUNT_RE = re.compile(r"displayed count:\s*(?P<count>\d+|unknown)", re.I)
_DEDICATED_EXPECTED_RE = re.compile(
    r"\[(?P<platform>tiktok|threads):(?P<relation>followers|following)\].*?"
    r"modal verified;.*?expected=(?P<count>\d+)",
    re.I,
)
_DEDICATED_PROGRESS_RE = re.compile(
    r"\[(?P<platform>tiktok|threads):(?P<relation>followers|following)\]\s+"
    r"round=(?P<round>\d+)\s+"
    r"visible=(?P<visible>\d+)\s+new=(?P<new>\d+)\s+accumulated=(?P<current>\d+)"
    r"(?:\s+first=(?P<first>\S+)\s+last=(?P<last>\S+)"
    r"\s+scroll=(?P<scroll_top>\d+)/(?P<scroll_height>\d+)"
    r"(?:\s+viewport=(?P<viewport>\d+))?)?",
    re.I,
)
_DEDICATED_OPEN_RE = re.compile(
    r"\[(?:tiktok|threads):[^\]]+\]\s+opening source profile", re.I
)
_DEDICATED_SWITCH_RE = re.compile(
    r"\[(?:tiktok|threads):(?P<relation>followers|following)\]\s+"
    r"switching exact modal tab",
    re.I,
)
_THREADS_AGGREGATE_RE = re.compile(
    r"\[threads:(?P<relation>followers|following)\]\s+aggregate\s+"
    r"pass=(?P<pass>\d+)\s+new=(?P<new>\d+)\s+"
    r"accumulated=(?P<current>\d+)/(?P<total>\d+|\?)",
    re.I,
)
_TIKTOK_STRATEGY_RE = re.compile(r"trusted click strategy=(?P<strategy>[a-z_]+)", re.I)
_RETRY_RE = re.compile(
    r"\[(?P<platform>[^:\]]+):(?P<relation>[^\]]+)\]\s+exact-count retry\s+"
    r"(?P<retry>\d+)/(?P<limit>\d+):\s+(?P<current>\d+)/(?P<total>\d+)",
    re.I,
)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _coverage_value(saved: int, reported: int | None) -> str:
    return f"{saved}/{reported}" if reported is not None else f"{saved} (displayed count unavailable)"


def _coverage_percent(saved: int, displayed: int) -> str:
    if displayed <= 0:
        return "n/a"
    return f"{min(100.0, (saved / displayed) * 100):.1f}%"


def _platform_label(platform: str) -> str:
    return {"tiktok": "TikTok", "x": "X"}.get(platform.casefold(), platform.title())


def _scroll_description(match: re.Match[str]) -> str:
    first = match.groupdict().get("first")
    last = match.groupdict().get("last")
    top_raw = match.groupdict().get("scroll_top")
    height_raw = match.groupdict().get("scroll_height")
    viewport_raw = match.groupdict().get("viewport")
    parts: list[str] = []
    if first and last:
        parts.append(f"{first} → {last}")
    if top_raw and height_raw:
        top = int(top_raw)
        height = int(height_raw)
        viewport = int(viewport_raw or 0)
        maximum = max(1, height - viewport)
        parts.append(f"scroll {min(100, round((top / maximum) * 100))}%")
    return " · ".join(parts)


class PlainUI:
    def __init__(self) -> None:
        self._reset_totals()

    def _reset_totals(self) -> None:
        self.run_collected = 0
        self.run_reported = 0
        self.run_relations = 0
        self.run_complete = 0
        self.private_relations = 0
        self.private_reported = 0

    def banner(self, subject: str, profiles: int) -> None:
        self._reset_totals()
        print(f"\n=== Contact Analyzer: {subject} — {profiles} profiles ===")

    def plan(self, rows: list[dict[str, Any]]) -> None:
        for index, row in enumerate(rows, 1):
            mode = row.get("mode") or ""
            rel = (
                "skip — no enumerable public graph" if mode in {"private", "none"}
                else "Codex direct-page people analysis" if mode == "codex"
                else "human-author content gate" if mode == "content-review"
                else ", ".join(row.get("relations") or []) or "discovery required"
            )
            print(f"  {index}. {row['platform']}: {row['url']} [{rel}]")

    def step(self, number: int, total: int, title: str, detail: str = "") -> None:
        suffix = f" — {detail}" if detail else ""
        print(f"[{number}/{total}] {title}{suffix}")

    def note(self, message: str) -> None:
        print(f"  {message}")

    def warning(self, message: str) -> None:
        print(f"  WARNING: {message}")

    def result(self, outcome: Any, committed: int, new_contacts: int, total_saved: int, new_relationship_urls: int = 0) -> None:
        if outcome.status == "private":
            self.private_relations += 1
            self.private_reported += int(outcome.reported_count or 0)
        else:
            self.run_collected += int(outcome.collected_this_run)
            if outcome.reported_count is not None:
                self.run_reported += int(outcome.reported_count)
            self.run_relations += 1
            if outcome.status in SUCCESS_STATUSES:
                self.run_complete += 1
        coverage = relationship_coverage_note(
            outcome.platform,
            outcome.reported_count,
            int(outcome.collected_this_run),
            total_saved,
            str(outcome.status),
            str(outcome.reason),
        )
        reported = int(outcome.reported_count) if outcome.reported_count is not None else None
        remaining = max(0, reported - total_saved) if reported is not None else None
        print(f"  {outcome.platform}:{outcome.relation} — {status_label(outcome.status, outcome.reason)}")
        print(
            f"    Latest browser pass: {outcome.collected_this_run}/{reported if reported is not None else '?'}"
            f" | accepted trusted rows: {committed}"
            f" | newly saved edges: {new_relationship_urls}"
        )
        print(
            f"    Accumulated coverage: {_coverage_value(total_saved, reported)}"
            f" | remaining exact-count gap: {remaining if remaining is not None else '?'}"
            f" | new unique accounts: {new_contacts}"
        )
        if outcome.status == "private":
            print("    Collectible run totals: excluded — private list unavailable")
        else:
            print(
                f"    Run browser-pass count so far: {self.run_collected}/"
                f"{self.run_reported if self.run_reported else '?'}"
            )
        print(f"    Reason: {reason_label(outcome.reason)}")
        if coverage:
            print(f"    Coverage note: {coverage}")

    def website_result(self, outcome: Any, committed: int, new_associations: int, new_contacts: int) -> None:
        print(f"  {outcome.platform}:website-people — {outcome.status}")
        print(
            f"    subject visible={outcome.subject_present} | accepted people={committed} | "
            f"new associations={new_associations} | new canonical contacts={new_contacts}"
        )
        if outcome.author_entity_type:
            print(f"    author={outcome.author_name or 'unknown'} ({outcome.author_entity_type})")
        print(f"    reason={outcome.reason} | mode={outcome.analysis_mode}")

    @contextmanager
    def capture_collector(self, platform: str, relation: str, source_url: str) -> Iterator[None]:
        yield

    def finish(self, output: str) -> None:
        print(f"Saved: {output}")

    def run_summary(self, metrics: dict[str, Any]) -> None:
        displayed = int(metrics["displayed_relationship_records"])
        accumulated_exact = int(metrics.get(
            "accumulated_exact_count_records",
            min(int(metrics.get("accumulated_relationship_records", 0)), displayed),
        ))
        print(f"\nRun totals — {metrics.get('status_label') or str(metrics['status']).title()}")
        coverage_label = (
            "Collectible exact-count coverage"
            if metrics.get("private_relationships") or metrics.get("unavailable_relationships")
            else "Accumulated exact-count coverage"
        )
        print(
            f"  {coverage_label}: {accumulated_exact}/{displayed} "
            f"({_coverage_percent(accumulated_exact, displayed)})"
        )
        print(
            f"  Latest browser pass: {metrics.get('latest_pass_relationship_records', metrics['collected_relationship_records'])}/"
            f"{displayed} | latest-pass gap: {metrics.get('latest_pass_count_gap', 0)}"
        )
        print(
            f"  Saved relationship edges: {metrics.get('accumulated_relationship_records', 0)}"
            f" | unique platform accounts: {metrics['unique_contacts_saved']}"
            f" | repeated memberships deduplicated in master list: {metrics.get('relationship_membership_overlap', 0)}"
        )
        print(
            f"  Newly saved this run: {metrics.get('new_relationship_urls', 0)} relationship edges"
            f" | {metrics.get('new_contacts_added', 0)} unique accounts"
        )
        print(
            f"  Relationships complete: {metrics['complete_relations']}/"
            f"{metrics.get('collectible_relationships', metrics['relationships_attempted'])} collectible"
            f" | remaining exact-count gap: {metrics.get('accumulated_count_gap', metrics['unexposed_count_gap'])}"
        )
        if metrics.get("private_relationships"):
            print(
                f"  Private lists excluded from coverage: {metrics['private_relationships']}"
                f" | displayed counts recorded: {metrics.get('private_displayed_records', 0)}"
            )
        if metrics.get("unavailable_relationships"):
            print(
                f"  Unavailable source relationships excluded from coverage: "
                f"{metrics['unavailable_relationships']}"
            )
        if metrics.get("website_sources_attempted"):
            print(
                f"  Website sources: {metrics.get('website_sources_complete', 0)} complete, "
                f"{metrics.get('website_sources_skipped', 0)} skipped | "
                f"source mentions this pass: {metrics.get('associated_people_detected', 0)} | "
                f"unique associated people saved: {metrics.get('associated_people_unique_saved', 0)} | "
                f"new source associations: {metrics.get('new_associations', 0)}"
            )


class CollectorOutputBridge(io.TextIOBase):
    def __init__(
        self,
        console: "Console",
        progress: "Progress",
        task_id: "TaskID",
        overall_base: int = 0,
    ):
        self.console = console
        self.progress = progress
        self.task_id = task_id
        self.overall_base = overall_base
        self.buffer = ""
        self._last_update: tuple[tuple[str, str], ...] | None = None
        self._dedicated_accumulated_floor = 0

    def _update(self, **fields: Any) -> None:
        signature = tuple(sorted((key, repr(value)) for key, value in fields.items()))
        if signature == self._last_update:
            return
        self._last_update = signature
        self.progress.update(self.task_id, **fields)

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        if not value:
            return 0
        self.buffer += value
        chunks = re.split(r"([\r\n])", self.buffer)
        self.buffer = ""
        current = ""
        for chunk in chunks:
            if chunk in {"\r", "\n"}:
                if current:
                    self._handle(current)
                    current = ""
            else:
                current += chunk
        self.buffer = current
        return len(value)

    def flush(self) -> None:
        if self.buffer.strip():
            self._handle(self.buffer)
        self.buffer = ""

    def _handle(self, line: str) -> None:
        clean = _ANSI_RE.sub("", line).strip()
        if not clean:
            return
        if _DEDICATED_OPEN_RE.search(clean):
            self._update(description="Opening source profile in authenticated Chromium")
            return
        match = _TIKTOK_STRATEGY_RE.search(clean)
        if match:
            strategy = match.group("strategy").replace("_", " ")
            self._update(description=f"Opening exact relationship control · {strategy}")
            return
        match = _DEDICATED_SWITCH_RE.search(clean)
        if match:
            self._update(description=f"Switching exact modal tab · {match.group('relation')}")
            return
        match = _DEDICATED_EXPECTED_RE.search(clean)
        if match:
            self._update(
                total=int(match.group("count")),
                description=f"Modal verified · active {match.group('relation')} tab",
            )
            return
        match = _DEDICATED_PROGRESS_RE.search(clean)
        if match:
            current = int(match.group("current"))
            displayed_current = max(current, self._dedicated_accumulated_floor)
            detail = _scroll_description(match)
            description = (
                f"Round {match.group('round')} · {match.group('visible')} visible · "
                f"+{match.group('new')} new"
            )
            if self._dedicated_accumulated_floor:
                description += f" · cumulative ≥{self._dedicated_accumulated_floor}"
            if detail:
                description += f" · {detail}"
            self._update(
                completed=displayed_current,
                overall=self.overall_base + displayed_current,
                description=description,
            )
            return
        match = _THREADS_AGGREGATE_RE.search(clean)
        if match:
            current = int(match.group("current"))
            raw_total = match.group("total")
            self._dedicated_accumulated_floor = current
            self._update(
                completed=current,
                total=int(raw_total) if raw_total.isdigit() else None,
                overall=self.overall_base + current,
                description=(
                    f"Pass {match.group('pass')} complete · +{match.group('new')} new · "
                    f"cumulative {current}/{raw_total}"
                ),
            )
            return
        match = _COUNT_RE.search(clean)
        if match:
            raw = match.group("count")
            if raw.isdigit():
                self._update(total=int(raw), description=f"Exact displayed count found · {raw}")
            else:
                self._update(description="Displayed count unavailable · collecting to stable end")
            return
        match = _RETRY_RE.search(clean)
        if match:
            current = int(match.group("current"))
            if match.group("platform").casefold() == "threads":
                self._dedicated_accumulated_floor = current
            self._update(
                completed=current,
                total=int(match.group("total")),
                overall=self.overall_base + current,
                description=(
                    f"Exact-count retry {match.group('retry')}/{match.group('limit')} · "
                    "reopening verified list"
                ),
            )
            return
        match = _PROGRESS_RE.search(clean)
        if match:
            current = int(match.group("current"))
            raw_total = match.group("total")
            total = int(raw_total) if raw_total.isdigit() else None
            fields = {
                "round": int(match.group("round")),
                "added": int(match.group("added")),
                "stalls": int(match.group("stalls")),
                "content_stalls": int(match.group("content_stalls") or 0),
                "pages": int(match.group("pages")),
                "pass": int(match.group("pass") or 1),
            }
            detail = _scroll_description(match)
            visible = match.group("visible")
            description = f"Round {fields['round']} · +{fields['added']} new"
            if visible is not None:
                description += f" · {visible} visible"
            if detail:
                description += f" · {detail}"
            description += (
                f" · page {fields['pages']} · pass {fields['pass']} · "
                f"stalls {fields['stalls']}/{fields['content_stalls']}"
            )
            self._update(
                completed=current,
                total=total,
                overall=self.overall_base + current,
                description=description,
            )
            return
        # Prompts and unexpected collector output remain visible above the progress bar.
        self.progress.console.print(Text(clean, style="dim"))


class RichUI:
    def __init__(self) -> None:
        # Bind Rich to the caller's stream now. Console() otherwise resolves
        # sys.stdout lazily and can point back at CollectorOutputBridge while the
        # collector is captured, recursively re-entering the bridge.
        self.console = Console(file=sys.stdout)
        self._reset_totals()

    def _reset_totals(self) -> None:
        self.run_collected = 0
        self.run_reported = 0
        self.run_relations = 0
        self.run_complete = 0
        self.private_relations = 0
        self.private_reported = 0

    def _output_width(self) -> int:
        """Keep summary panels readable on both narrow and ultrawide terminals."""
        return min(int(self.console.width), 120)

    def _short_url(self, url: str) -> str:
        # The full URL is printed again when its profile begins. Keep the plan
        # compact enough that a long URL can never force a one-character column.
        limit = max(24, min(92, self._output_width() - 12))
        return url if len(url) <= limit else f"{url[:limit - 1]}…"

    def banner(self, subject: str, profiles: int) -> None:
        self._reset_totals()
        title = Text("CONTACT ANALYZER", style="bold cyan")
        body = Text()
        body.append(subject, style="bold white")
        body.append(f"\n{profiles} saved profile URL{'s' if profiles != 1 else ''}", style="dim")
        self.console.print(Panel(
            body,
            title=title,
            border_style="cyan",
            expand=False,
            width=self._output_width(),
        ))

    def plan(self, rows: list[dict[str, Any]]) -> None:
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column("#", justify="right", style="dim", width=3, no_wrap=True)
        table.add_column("Profile", ratio=1)
        for index, row in enumerate(rows, 1):
            mode = row.get("mode") or ""
            if mode in {"private", "none"}:
                relations = "skip — no enumerable public graph"
            elif mode == "codex":
                relations = "Codex direct-page people analysis"
            elif mode == "content-review":
                relations = "human-author content gate"
            elif row.get("relations"):
                relations = ", ".join(row.get("relations") or [])
            else:
                relations = "verify exact visible controls first"
            details = Text()
            details.append(str(row["platform"]), style="bold white")
            details.append(f" · {relations}")
            if mode:
                details.append(f"  [{mode}]", style="dim")
            details.append("\n")
            details.append(self._short_url(str(row["url"])), style="cyan")
            table.add_row(str(index), details)
        self.console.print(Panel(
            table,
            title="Collection plan",
            border_style="cyan",
            expand=False,
            width=self._output_width(),
        ))

    def step(self, number: int, total: int, title: str, detail: str = "") -> None:
        text = Text()
        text.append(f"[{number}/{total}] ", style="bold cyan")
        text.append(title, style="bold white")
        if detail:
            text.append(f"  {detail}", style="dim")
        self.console.print(text)

    def note(self, message: str) -> None:
        self.console.print(f"    [dim]↳[/dim] {message}")

    def warning(self, message: str) -> None:
        self.console.print(f"    [bold yellow]⚠[/bold yellow] {message}")

    def result(self, outcome: Any, committed: int, new_contacts: int, total_saved: int, new_relationship_urls: int = 0) -> None:
        if outcome.status == "private":
            self.private_relations += 1
            self.private_reported += int(outcome.reported_count or 0)
        else:
            self.run_collected += int(outcome.collected_this_run)
            if outcome.reported_count is not None:
                self.run_reported += int(outcome.reported_count)
            self.run_relations += 1
            if outcome.status in SUCCESS_STATUSES:
                self.run_complete += 1
        status = str(outcome.status)
        style = "green" if status in SUCCESS_STATUSES else "yellow" if status in {"incomplete", "review", "private"} else "red"
        reported = int(outcome.reported_count) if outcome.reported_count is not None else None
        remaining = max(0, reported - total_saved) if reported is not None else None
        table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        table.add_column("Metric", style="dim", width=18)
        table.add_column("Value")
        table.add_row("Status", f"[{style}]{status_label(status, outcome.reason)}[/{style}]")
        table.add_row("Displayed count", str(reported if reported is not None else "unavailable"))
        table.add_row(
            "Latest pass",
            f"{outcome.collected_this_run}/{reported if reported is not None else '?'} unique relationship URLs",
        )
        table.add_row("Accepted rows", str(committed))
        table.add_row("New edges", str(new_relationship_urls))
        table.add_row("Saved coverage", _coverage_value(total_saved, reported))
        table.add_row("Remaining gap", str(remaining if remaining is not None else "unknown"))
        table.add_row("New accounts", str(new_contacts))
        if status == "private":
            table.add_row("Coverage totals", "Excluded — private list unavailable")
        else:
            table.add_row(
                "Run total",
                f"{self.run_collected}/{self.run_reported if self.run_reported else '?'} relationship URLs",
            )
        coverage = relationship_coverage_note(
            outcome.platform,
            outcome.reported_count,
            int(outcome.collected_this_run),
            total_saved,
            status,
            str(outcome.reason),
        )
        if coverage:
            table.add_row("Coverage", coverage)
        table.add_row("Completion reason", reason_label(str(outcome.reason)))
        self.console.print(Panel(
            table,
            border_style=style,
            title=(
                f"{_platform_label(outcome.platform)} · {outcome.relation.title()} · "
                f"{status_label(status, outcome.reason)}"
            ),
            expand=False,
            width=self._output_width(),
        ))

    def website_result(self, outcome: Any, committed: int, new_associations: int, new_contacts: int) -> None:
        status = str(outcome.status)
        style = "green" if status in {"complete", "skipped"} else "red"
        table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        table.add_column("Metric", style="dim", width=20)
        table.add_column("Value")
        table.add_row("Status", f"[{style}]{status.title()}[/{style}]")
        table.add_row("Subject visible", "yes" if outcome.subject_present else "no")
        table.add_row("Accepted people", str(committed))
        table.add_row("New associations", str(new_associations))
        table.add_row("New canonical contacts", str(new_contacts))
        table.add_row("Analysis mode", str(outcome.analysis_mode))
        if outcome.author_entity_type:
            table.add_row("Content author", f"{outcome.author_name or 'unknown'} · {outcome.author_entity_type}")
        table.add_row("Completion reason", str(outcome.reason).replace("_", " "))
        self.console.print(Panel(
            table,
            title=f"{_platform_label(outcome.platform)} · Associated people",
            border_style=style,
            expand=False,
            width=self._output_width(),
        ))

    @contextmanager
    def capture_collector(self, platform: str, relation: str, source_url: str) -> Iterator[None]:
        progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.fields[label]}[/bold]"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("Run pass [bold]{task.fields[overall]}[/bold]"),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
            transient=False,
            refresh_per_second=10,
        )
        with progress:
            task_id = progress.add_task(
                "Starting visible-browser extraction",
                total=None,
                label=f"{platform}:{relation}",
                overall=self.run_collected,
            )
            bridge = CollectorOutputBridge(
                self.console,
                progress,
                task_id,
                overall_base=self.run_collected,
            )
            with redirect_stdout(bridge):
                try:
                    yield
                finally:
                    bridge.flush()
                    progress.update(task_id, description="Browser extraction pass finished")

    def finish(self, output: str) -> None:
        limit = max(24, self._output_width() - 6)
        display_path = output if len(output) <= limit else f"…{output[-(limit - 1):]}"
        self.console.print(Panel(
            f"[bold green]Run saved[/bold green]\n{display_path}",
            border_style="green",
            expand=False,
            width=self._output_width(),
        ))

    def run_summary(self, metrics: dict[str, Any]) -> None:
        status = str(metrics["status"])
        style = "green" if status == "complete" else "yellow" if status == "partial" else "red"
        displayed = int(metrics["displayed_relationship_records"])
        accumulated_exact = int(metrics.get(
            "accumulated_exact_count_records",
            min(int(metrics.get("accumulated_relationship_records", 0)), displayed),
        ))
        table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        table.add_column("Metric", style="dim", width=22)
        table.add_column("Value")
        table.add_row("Overall status", f"[{style}]{metrics.get('status_label') or status.title()}[/{style}]")
        table.add_row(
            "Collectible coverage"
            if metrics.get("private_relationships") or metrics.get("unavailable_relationships")
            else "Exact-count coverage",
            f"{accumulated_exact}/{displayed} ({_coverage_percent(accumulated_exact, displayed)})",
        )
        table.add_row(
            "Latest pass",
            f"{metrics.get('latest_pass_relationship_records', metrics['collected_relationship_records'])}/{displayed}",
        )
        table.add_row("Latest-pass gap", str(metrics.get("latest_pass_count_gap", 0)))
        table.add_row("Remaining gap", str(metrics.get("accumulated_count_gap", metrics["unexposed_count_gap"])))
        table.add_row("Saved edges", str(metrics.get("accumulated_relationship_records", 0)))
        table.add_row("Unique accounts", str(metrics["unique_contacts_saved"]))
        table.add_row(
            "Overlap memberships",
            str(metrics.get("relationship_membership_overlap", 0)),
        )
        table.add_row("New edges this run", str(metrics.get("new_relationship_urls", 0)))
        table.add_row("New accounts this run", str(metrics.get("new_contacts_added", 0)))
        table.add_row(
            "Verified relations",
            f"{metrics['complete_relations']}/"
            f"{metrics.get('collectible_relationships', metrics['relationships_attempted'])} collectible",
        )
        table.add_row(
            "Count availability",
            f"{metrics.get('known_count_relationships', 0)} exact · "
            f"{metrics.get('unknown_count_relationships', 0)} unknown · "
            f"{metrics.get('private_relationships', 0)} private · "
            f"{metrics.get('unavailable_relationships', 0)} source unavailable",
        )
        if metrics.get("private_relationships"):
            table.add_row(
                "Private displayed counts",
                f"{metrics.get('private_displayed_records', 0)} recorded · excluded from coverage",
            )
        if metrics.get("website_sources_attempted"):
            table.add_row(
                "Website sources",
                f"{metrics.get('website_sources_complete', 0)} complete · "
                f"{metrics.get('website_sources_skipped', 0)} skipped",
            )
            table.add_row("Associated mentions", str(metrics.get("associated_people_detected", 0)))
            table.add_row("Unique people saved", str(metrics.get("associated_people_unique_saved", 0)))
            table.add_row("New source associations", str(metrics.get("new_associations", 0)))
        self.console.print(Panel(
            table,
            title="Accurate run totals",
            border_style=style,
            expand=False,
            width=self._output_width(),
        ))


_UI: PlainUI | RichUI | None = None


def get_ui() -> PlainUI | RichUI:
    global _UI
    if _UI is None:
        _UI = RichUI() if RICH_AVAILABLE and sys.stdout.isatty() else PlainUI()
    return _UI
