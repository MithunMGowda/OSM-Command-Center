"""Fetch OSM changesets and diff counts from the OpenStreetMap API."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OSM_API = "https://api.openstreetmap.org/api/0.6"
DEFAULT_DELAY_SEC = 0.3

__all__ = [
    "changesets_to_dataframe",
    "export_changesets_csv",
    "fetch_all_changesets",
    "fetch_changeset_diff",
    "fetch_changesets_since",
    "fetch_team_changesets",
]

ProgressCallback = Callable[[str, float], None] | None


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    return s


def _int_attr(element: ET.Element, name: str) -> int:
    val = element.get(name)
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except ValueError:
        return 0


def counts_from_changeset_element(cs: ET.Element) -> dict[str, int]:
    """
    OSM API returns created_count / modified_count / deleted_count on each changeset.
    These match Add / Modify / Delete without downloading the full diff.
    """
    created = _int_attr(cs, "created_count")
    modified = _int_attr(cs, "modified_count")
    deleted = _int_attr(cs, "deleted_count")
    return {
        "nodes_created": 0,
        "ways_created": 0,
        "relations_created": 0,
        "total_created": created,
        "nodes_modified": 0,
        "ways_modified": 0,
        "relations_modified": 0,
        "total_modified": modified,
        "nodes_deleted": 0,
        "ways_deleted": 0,
        "relations_deleted": 0,
        "total_deleted": deleted,
    }


def _parse_changeset_element(cs: ET.Element, display_name: str) -> dict:
    tags = {tag.get("k"): tag.get("v") for tag in cs.findall("tag")}
    meta_counts = counts_from_changeset_element(cs)
    return {
        "id": cs.get("id"),
        "created_at": cs.get("created_at"),
        "closed_at": cs.get("closed_at"),
        "user": cs.get("user") or display_name,
        "uid": cs.get("uid"),
        "min_lat": cs.get("min_lat"),
        "min_lon": cs.get("min_lon"),
        "max_lat": cs.get("max_lat"),
        "max_lon": cs.get("max_lon"),
        "tags": tags,
        "metadata_counts": meta_counts,
    }


def _fetch_changesets_in_window(
    display_name: str,
    start_dt: datetime,
    end_dt: datetime,
    session: requests.Session,
) -> list[dict]:
    """Paginate OSM changeset listing for a UTC time window."""
    all_changesets: list[dict] = []
    current_end = end_dt

    while True:
        params = {
            "display_name": display_name,
            "time": f"{start_dt.isoformat()}Z,{current_end.isoformat()}Z",
        }
        response = session.get(f"{OSM_API}/changesets", params=params, timeout=60)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        batch = [_parse_changeset_element(cs, display_name) for cs in root.findall("changeset")]

        all_changesets.extend(batch)
        if len(batch) < 100:
            break
        oldest = batch[-1]["created_at"]
        current_end = datetime.strptime(oldest, "%Y-%m-%dT%H:%M:%SZ")

    return all_changesets


def fetch_all_changesets(
    display_name: str,
    start_date: str,
    end_date: str,
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch changeset metadata for a user in [start_date, end_date] (inclusive)."""
    session = session or _session()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    return _fetch_changesets_in_window(display_name, start_dt, end_dt, session)


def fetch_changesets_since(
    display_name: str,
    since: datetime,
    session: requests.Session | None = None,
) -> list[dict]:
    """
    Fetch changeset metadata newer than `since` (UTC). Fast — no diff downloads.
    Uses OSM API created_count / modified_count / deleted_count on each changeset.
    """
    session = session or _session()
    if since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)
    end_dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
    if since >= end_dt:
        return []
    return _fetch_changesets_in_window(display_name, since, end_dt, session)


def fetch_changeset_diff(
    changeset_id: str,
    session: requests.Session | None = None,
) -> dict[str, int]:
    """Count created / modified / deleted elements from OsmChange XML."""
    session = session or _session()
    url = f"{OSM_API}/changeset/{changeset_id}/download"
    response = session.get(url, timeout=60)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    counts = {
        "nodes_created": 0,
        "ways_created": 0,
        "relations_created": 0,
        "nodes_modified": 0,
        "ways_modified": 0,
        "relations_modified": 0,
        "nodes_deleted": 0,
        "ways_deleted": 0,
        "relations_deleted": 0,
    }

    for action in root:
        action_type = action.tag
        for element in action:
            key = f"{element.tag}s_{action_type}d"
            if key in counts:
                counts[key] += 1

    counts["total_created"] = (
        counts["nodes_created"] + counts["ways_created"] + counts["relations_created"]
    )
    counts["total_modified"] = (
        counts["nodes_modified"] + counts["ways_modified"] + counts["relations_modified"]
    )
    counts["total_deleted"] = (
        counts["nodes_deleted"] + counts["ways_deleted"] + counts["relations_deleted"]
    )
    return counts


def changesets_to_dataframe(all_data: dict[str, list[dict]]) -> pd.DataFrame:
    """Flatten user -> changesets dict into a dashboard-ready DataFrame."""
    rows = []
    for username, changesets in all_data.items():
        for cs in changesets:
            diff = cs.get("diff_counts", {})
            rows.append(
                {
                    "user": username,
                    "changeset_id": cs["id"],
                    "created_at": cs.get("created_at"),
                    "closed_at": cs.get("closed_at"),
                    "comment": cs.get("tags", {}).get("comment", ""),
                    "nodes_created": diff.get("nodes_created", 0),
                    "ways_created": diff.get("ways_created", 0),
                    "relations_created": diff.get("relations_created", 0),
                    "total_created": diff.get("total_created", 0),
                    "nodes_modified": diff.get("nodes_modified", 0),
                    "ways_modified": diff.get("ways_modified", 0),
                    "relations_modified": diff.get("relations_modified", 0),
                    "total_modified": diff.get("total_modified", 0),
                    "nodes_deleted": diff.get("nodes_deleted", 0),
                    "ways_deleted": diff.get("ways_deleted", 0),
                    "relations_deleted": diff.get("relations_deleted", 0),
                    "total_deleted": diff.get("total_deleted", 0),
                    "min_lat": cs.get("min_lat"),
                    "min_lon": cs.get("min_lon"),
                    "max_lat": cs.get("max_lat"),
                    "max_lon": cs.get("max_lon"),
                    "osmcha_feat_mod": cs.get("osmcha_feat_mod"),
                    "osmcha_feat_create": cs.get("osmcha_feat_create"),
                    "osmcha_feat_del": cs.get("osmcha_feat_del"),
                    "osmcha_suspect": cs.get("osmcha_suspect"),
                    "osmcha_reasons": cs.get("osmcha_reasons"),
                    "features_modified": cs.get("features_modified"),
                }
        )
    from src.osm_ops_metrics import apply_features_modified

    return apply_features_modified(pd.DataFrame(rows))


def fetch_team_changesets(
    usernames: list[str],
    start_date: str,
    end_date: str,
    *,
    download_diffs: bool = True,
    delay_sec: float = DEFAULT_DELAY_SEC,
    progress: ProgressCallback = None,
) -> pd.DataFrame:
    """
    Fetch changesets for multiple users and optionally download diffs for counts.
    """
    session = _session()
    all_data: dict[str, list[dict]] = {}
    total_users = len(usernames)

    for ui, username in enumerate(usernames):
        if progress:
            progress(f"Listing changesets for {username}…", ui / max(total_users, 1))

        changesets = fetch_all_changesets(username, start_date, end_date, session=session)
        if download_diffs:
            n = len(changesets)
            for i, cs in enumerate(changesets):
                cs["diff_counts"] = fetch_changeset_diff(cs["id"], session=session)
                if progress and n:
                    frac = (ui + (i + 1) / n) / max(total_users, 1)
                    progress(
                        f"Diffs for {username}: {i + 1}/{n}",
                        frac,
                    )
                time.sleep(delay_sec)
        else:
            for cs in changesets:
                cs["diff_counts"] = cs.get("metadata_counts") or {
                    "total_created": 0,
                    "total_modified": 0,
                    "total_deleted": 0,
                }

        all_data[username] = changesets

    if progress:
        progress("Done", 1.0)

    return changesets_to_dataframe(all_data)


def export_changesets_csv(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)
    return path
