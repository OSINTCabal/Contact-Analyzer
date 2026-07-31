from __future__ import annotations

from typing import Any, Iterable, Mapping


COMMITTABLE_STATUSES = frozenset({"complete", "complete_accessible_list", "incomplete"})
SUCCESS_STATUSES = frozenset({"verified", "complete", "complete_accessible_list"})
BROWSER_ACCESS_LIMIT_REASONS = frozenset({
    "browser_control_error_after_partial_collection",
    "browser_relationship_cursor_not_requested",
    "displayed_count_exceeds_accessible_list",
    "platform_relationship_payload_exhausted_before_displayed_count",
})
NON_COLLECTIBLE_REASONS = frozenset({"source_profile_unavailable"})

_REASON_LABELS = {
    "browser_control_error_after_partial_collection": (
        "The browser stopped responding after relationship rows had already been "
        "validated. All trusted rows collected before the interruption were saved."
    ),
    "private_profile_relationship_list_unavailable": (
        "The displayed count was recorded, but this relationship list is private or "
        "hidden from the authenticated browser."
    ),
    "browser_relationship_cursor_not_requested": (
        "The authenticated browser stopped exposing additional relationship rows "
        "before the displayed total was reached. All rows exposed this run were saved."
    ),
    "displayed_count_exceeds_accessible_list": (
        "The displayed total is larger than the relationship list exposed by the "
        "authenticated browser. All exposed rows were saved."
    ),
    "platform_relationship_payload_exhausted_before_displayed_count": (
        "The authenticated relationship list ended before the displayed total was "
        "reached. All exposed rows were saved."
    ),
    "accumulated_unique_urls_below_reported_count": (
        "The accumulated unique relationship URLs are still below the displayed total."
    ),
    "accumulated_unique_urls_equal_reported_count": (
        "The accumulated unique relationship URLs equal the displayed total."
    ),
    "accumulated_unique_urls_exceed_reported_count": (
        "The accumulated unique relationship URLs exceed the displayed total; review is required."
    ),
    "source_profile_unavailable": (
        "The supplied source profile is unavailable in the authenticated browser, so no relationship list exists to collect."
    ),
    "trusted_exhausted_list_exceeds_stale_displayed_count_by_one": (
        "The exhausted Instagram relationship response and rendered modal exposed one "
        "more canonical account than the stale profile-header count. All dual-source "
        "rows were saved and the count mismatch remains marked for review."
    ),
}


def cumulative_relationship_status(
    current_status: str,
    current_reason: str,
    reported_count: int | None,
    total_unique_saved: int,
) -> tuple[str, str]:
    """Derive the persisted status after merging a trusted collection pass.

    A partial pass can become verified across reruns, but only when the exact
    rendered count is known and the accumulated unique edge count equals it.
    Failed, blocked, and review passes remain untrusted and cannot promote an
    earlier partial result.
    """
    if current_status not in COMMITTABLE_STATUSES:
        return current_status, current_reason
    if reported_count is None:
        return current_status, current_reason
    if total_unique_saved == reported_count:
        return "verified", "accumulated_unique_urls_equal_reported_count"
    if total_unique_saved > reported_count:
        return "review", "accumulated_unique_urls_exceed_reported_count"
    if current_reason in BROWSER_ACCESS_LIMIT_REASONS:
        return "incomplete", current_reason
    return "incomplete", "accumulated_unique_urls_below_reported_count"


def status_label(status: str, reason: str | None = None) -> str:
    if reason in NON_COLLECTIBLE_REASONS:
        return "Unavailable — Profile not found"
    if status == "verified":
        return "Verified"
    if status == "incomplete" and reason in BROWSER_ACCESS_LIMIT_REASONS:
        return "Incomplete — Browser-limited"
    if status == "private":
        return "Private — List unavailable"
    return status.replace("_", " ").title()


def reason_label(reason: str) -> str:
    return _REASON_LABELS.get(str(reason or ""), str(reason or "unknown").replace("_", " ").capitalize())


def relationship_coverage_note(
    platform: str,
    reported_count: int | None,
    collected_this_run: int,
    total_unique_saved: int,
    status: str,
    reason: str,
) -> str | None:
    """Describe browser-visible coverage without implying inaccessible rows were scraped."""
    if status == "verified":
        return f"Verified {total_unique_saved}/{reported_count} unique relationship URLs."
    if (
        reported_count is not None
        and total_unique_saved < reported_count
        and status == "incomplete"
        and reason in BROWSER_ACCESS_LIMIT_REASONS
    ):
        unavailable = reported_count - total_unique_saved
        unavailable_label = (
            "1 displayed account was"
            if unavailable == 1
            else f"{unavailable} displayed accounts were"
        )
        return (
            f"Authenticated browser exposed {collected_this_run} unique URLs this run; "
            f"{total_unique_saved}/{reported_count} are saved cumulatively. "
            f"{unavailable_label} not exposed by the browser."
        )
    if reported_count is None and status == "complete_accessible_list":
        return (
            f"Exact displayed total unavailable; saved the complete accessible list "
            f"({total_unique_saved} unique URLs)."
        )
    if reason in BROWSER_ACCESS_LIMIT_REASONS:
        return reason_label(reason)
    return None


def run_status_label(status: str, results: Iterable[Mapping[str, Any]]) -> str:
    """Return a concise, human-readable status for a whole collection run."""
    rows = list(results)
    if status == "complete":
        if rows and all(row.get("reported_count") is not None for row in rows):
            return "Complete — all exact counts verified"
        return "Complete — all accessible lists collected"

    unfinished = [row for row in rows if str(row.get("status") or "") not in SUCCESS_STATUSES]
    if status == "partial" and unfinished and all(
        str(row.get("reason") or "") in BROWSER_ACCESS_LIMIT_REASONS
        for row in unfinished
    ):
        return "Partial — browser-limited"
    if any(str(row.get("status") or "") == "review" for row in unfinished):
        return "Review required — count mismatch"
    if unfinished and all(
        str(row.get("reason") or "") in NON_COLLECTIBLE_REASONS
        for row in unfinished
    ):
        return "Partial — source profiles unavailable"
    if (
        unfinished
        and any(
            str(row.get("reason") or "") in NON_COLLECTIBLE_REASONS
            for row in unfinished
        )
        and all(
            str(row.get("reason") or "") in NON_COLLECTIBLE_REASONS
            or str(row.get("reason") or "") in BROWSER_ACCESS_LIMIT_REASONS
            or str(row.get("status") or "") == "private"
            for row in unfinished
        )
    ):
        return "Partial — unavailable/private/browser-limited lists"
    if any(str(row.get("status") or "") in {"failed", "blocked"} for row in unfinished):
        return "Partial — one or more collections failed"
    if unfinished and all(str(row.get("status") or "") == "private" for row in unfinished):
        return "Partial — private lists unavailable"
    if unfinished and all(
        str(row.get("status") or "") == "private"
        or str(row.get("reason") or "") in BROWSER_ACCESS_LIMIT_REASONS
        for row in unfinished
    ):
        return "Partial — private/browser-limited lists"
    if status == "partial":
        return "Partial — one or more relationships incomplete"
    return status.replace("_", " ").title()


def build_run_summary(
    results: Iterable[Mapping[str, Any]],
    *,
    profiles_inspected: int,
    unique_contacts_saved: int,
    accumulated_relationship_records: int,
    status: str,
    new_relationship_urls: int = 0,
    new_contacts_added: int | None = None,
) -> dict[str, Any]:
    """Build run metrics without mixing account and relationship-edge counts.

    ``collected_this_run`` describes the latest browser pass.  Cumulative
    coverage uses ``total_unique_saved`` so repeated runs can close an exact
    count without making an already-saved URL look new again.
    """
    rows = list(results)
    private_rows = [row for row in rows if str(row.get("status") or "") == "private"]
    unavailable_rows = [
        row for row in rows
        if str(row.get("reason") or "") in NON_COLLECTIBLE_REASONS
    ]
    collectible_rows = [
        row for row in rows
        if str(row.get("status") or "") != "private"
        and str(row.get("reason") or "") not in NON_COLLECTIBLE_REASONS
    ]
    exact_rows = [
        row for row in collectible_rows if row.get("reported_count") is not None
    ]
    displayed = sum(int(row["reported_count"]) for row in exact_rows)
    latest_pass = sum(
        int(row.get("collected_this_run") or 0) for row in collectible_rows
    )
    latest_pass_gap = sum(
        max(0, int(row["reported_count"]) - int(row.get("collected_this_run") or 0))
        for row in exact_rows
    )
    accumulated_exact = sum(
        min(int(row["reported_count"]), int(row.get("total_unique_saved") or 0))
        for row in exact_rows
    )
    accumulated_gap = sum(
        max(0, int(row["reported_count"]) - int(row.get("total_unique_saved") or 0))
        for row in exact_rows
    )
    complete = sum(
        1
        for row in collectible_rows
        if str(row.get("status") or "") in SUCCESS_STATUSES
    )
    if new_contacts_added is None:
        new_contacts_added = sum(int(row.get("new_contacts_added") or 0) for row in rows)

    return {
        "profiles_inspected": int(profiles_inspected),
        "relationships_attempted": len(rows),
        "collectible_relationships": len(collectible_rows),
        "private_relationships": len(private_rows),
        "private_displayed_records": sum(
            int(row.get("reported_count") or 0) for row in private_rows
        ),
        "unavailable_relationships": len(unavailable_rows),
        "known_count_relationships": len(exact_rows),
        "unknown_count_relationships": len(collectible_rows) - len(exact_rows),
        "displayed_relationship_records": displayed,
        "latest_pass_relationship_records": latest_pass,
        # Backward-compatible key retained for existing JSON consumers.
        "collected_relationship_records": latest_pass,
        "latest_pass_count_gap": latest_pass_gap,
        "accumulated_exact_count_records": accumulated_exact,
        "accumulated_relationship_records": int(accumulated_relationship_records),
        "accumulated_count_gap": accumulated_gap,
        # This historical key now means the remaining cumulative gap, not the
        # number absent from only the latest pass.
        "unexposed_count_gap": accumulated_gap,
        "unique_contacts_saved": int(unique_contacts_saved),
        "relationship_membership_overlap": max(
            0, int(accumulated_relationship_records) - int(unique_contacts_saved)
        ),
        "new_relationship_urls": int(new_relationship_urls),
        "new_contacts_added": int(new_contacts_added),
        "complete_relations": complete,
        "partial_or_failed_relations": len(collectible_rows) - complete,
        "status": status,
        "status_label": run_status_label(status, rows),
    }
