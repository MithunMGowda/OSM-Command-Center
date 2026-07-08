"""Live polling and merge for OSM changeset tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from src.osm_fetch import (
    _session,
    changesets_to_dataframe,
    export_changesets_csv,
    fetch_changesets_since,
)

PollCallback = Callable[[str], None] | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _attach_metadata_counts(changesets: list[dict]) -> None:
    for cs in changesets:
        cs["diff_counts"] = cs.get("metadata_counts") or {
            "nodes_created": 0,
            "ways_created": 0,
            "relations_created": 0,
            "total_created": 0,
            "nodes_modified": 0,
            "ways_modified": 0,
            "relations_modified": 0,
            "total_modified": 0,
            "nodes_deleted": 0,
            "ways_deleted": 0,
            "relations_deleted": 0,
            "total_deleted": 0,
        }


def poll_team_changesets(
    usernames: list[str],
    since: datetime,
    *,
    on_status: PollCallback = None,
) -> pd.DataFrame:
    """Poll OSM API for new changesets across a team (metadata only)."""
    session = _session()
    all_data: dict[str, list[dict]] = {}

    for username in usernames:
        if on_status:
            on_status(f"Polling {username}…")
        changesets = fetch_changesets_since(username, since, session=session)
        _attach_metadata_counts(changesets)
        all_data[username] = changesets

    df = changesets_to_dataframe(all_data)
    if df.empty:
        return df

    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    return df.sort_values("created_at", ascending=False)


def merge_changesets(
    existing: pd.DataFrame,
    new: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Merge new rows into existing; return (merged, new_only)."""
    if new.empty:
        return existing, new
    if existing.empty:
        return new.copy(), new.copy()

    ex = existing.copy()
    ex["changeset_id"] = ex["changeset_id"].astype(str)
    nw = new.copy()
    nw["changeset_id"] = nw["changeset_id"].astype(str)

    known = set(ex["changeset_id"])
    new_only = nw[~nw["changeset_id"].isin(known)]
    if new_only.empty:
        return ex, new_only

    merged = pd.concat([ex, new_only], ignore_index=True)
    merged = merged.drop_duplicates(subset=["changeset_id"], keep="last")
    if "created_at" in merged.columns:
        merged["created_at"] = pd.to_datetime(merged["created_at"], utc=True, errors="coerce")
        merged = merged.sort_values("created_at", ascending=False)
    return merged, new_only


def next_poll_since(
    current_since: datetime,
    new_rows: pd.DataFrame,
    poll_at: datetime | None = None,
) -> datetime:
    """Watermark for the next poll — latest changeset time or poll time."""
    if not new_rows.empty and "created_at" in new_rows.columns:
        latest = pd.to_datetime(new_rows["created_at"], utc=True, errors="coerce").max()
        if pd.notna(latest):
            return latest.to_pydatetime().replace(tzinfo=None)
    if poll_at is not None:
        if poll_at.tzinfo is not None:
            return poll_at.astimezone(timezone.utc).replace(tzinfo=None)
        return poll_at
    if current_since.tzinfo is not None:
        return current_since.astimezone(timezone.utc).replace(tzinfo=None)
    return current_since


def poll_and_merge(
    existing: pd.DataFrame,
    usernames: list[str],
    since: datetime,
    *,
    csv_path: Path | str | None = None,
    on_status: PollCallback = None,
) -> tuple[pd.DataFrame, pd.DataFrame, datetime]:
    """
    Poll OSM for edits since `since`, merge into `existing`, optionally persist CSV.
    Returns (merged_df, new_only_df, next_since).
    """
    poll_at = _utc_now()
    if on_status:
        on_status(f"Checking {len(usernames)} mapper(s) since {since.isoformat()}Z…")

    new_df = poll_team_changesets(usernames, since, on_status=on_status)
    merged, new_only = merge_changesets(existing, new_df)
    next_since = next_poll_since(since, new_only, poll_at)

    if csv_path and not new_only.empty:
        export_changesets_csv(merged, csv_path)
        if on_status:
            on_status(f"Appended {len(new_only)} changeset(s) → {csv_path}")

    return merged, new_only, next_since


def live_feed_dataframe(feed: pd.DataFrame, limit: int = 50) -> pd.DataFrame:
    """Format recent live edits for the dashboard feed table."""
    if feed.empty:
        return feed
    cols = [
        "user",
        "changeset_id",
        "created_at",
        "comment",
        "total_created",
        "features_modified",
        "total_deleted",
        "region",
    ]
    out = feed.copy()
    if "created_at" in out.columns:
        out = out.sort_values("created_at", ascending=False)
    show = [c for c in cols if c in out.columns]
    return out[show].head(limit)
