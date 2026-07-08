"""OSMCha REST API client (https://osmcha.org/api/v1/)."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OSMCHA_API = os.environ.get("OSMCHA_API_URL", "https://osmcha.org/api/v1").rstrip("/")


def get_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit.strip() or None
    return os.environ.get("OSMCHA_TOKEN") or os.environ.get("OSMCHA_API_TOKEN")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Token {token}"}


def fetch_changesets(
    *,
    token: str,
    users: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    is_suspect: bool | None = None,
    page_size: int = 100,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Paginate OSMCha /changesets/ until no next page."""
    session = requests.Session()
    session.verify = False

    params: dict[str, Any] = {"page_size": page_size}
    if users:
        params["users"] = ",".join(users)
    if date_from:
        params["date__gte"] = date_from
    if date_to:
        params["date__lte"] = date_to
    if is_suspect is not None:
        params["is_suspect"] = str(is_suspect).lower()

    results: list[dict[str, Any]] = []
    url: str | None = f"{OSMCHA_API}/changesets/"
    pages = 0

    while url and (max_pages is None or pages < max_pages):
        resp = session.get(
            url,
            params=params if pages == 0 else None,
            headers=_headers(token),
            timeout=90,
        )
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("results", payload.get("features", []))
        results.extend(batch)
        url = payload.get("next")
        params = {}
        pages += 1

    return results


def _reasons_text(record: dict[str, Any]) -> str:
    reasons = record.get("reasons") or record.get("reasons_ids") or []
    if not reasons:
        return ""
    parts = []
    for r in reasons:
        if isinstance(r, dict):
            parts.append(str(r.get("name") or r.get("text") or r))
        else:
            parts.append(str(r))
    return "; ".join(parts)


def normalize_osmcha_record(record: dict[str, Any]) -> dict[str, Any]:
    """Map OSMCha JSON to dashboard row."""
    bbox = record.get("bbox") or {}
    if isinstance(bbox, dict):
        min_lat = bbox.get("min_lat") or bbox.get("south")
        min_lon = bbox.get("min_lon") or bbox.get("west")
        max_lat = bbox.get("max_lat") or bbox.get("north")
        max_lon = bbox.get("max_lon") or bbox.get("east")
    else:
        min_lat = min_lon = max_lat = max_lon = None

    created = record.get("date") or record.get("created_at")
    feat_create = record.get("featCreate") or record.get("features_created") or 0
    feat_mod = record.get("featMod") or record.get("features_modified") or 0
    feat_del = record.get("featDelete") or record.get("features_deleted") or 0

    return {
        "user": record.get("user"),
        "changeset_id": str(record.get("id") or record.get("changeset_id")),
        "created_at": created,
        "closed_at": record.get("closed_at"),
        "comment": record.get("comment") or "",
        "osmcha_feat_create": int(feat_create or 0),
        "osmcha_feat_mod": int(feat_mod or 0),
        "osmcha_feat_del": int(feat_del or 0),
        "nodes_created": 0,
        "ways_created": 0,
        "relations_created": 0,
        "total_created": int(feat_create or 0),
        "nodes_modified": 0,
        "ways_modified": 0,
        "relations_modified": 0,
        "total_modified": int(feat_mod or 0),
        "nodes_deleted": 0,
        "ways_deleted": 0,
        "relations_deleted": 0,
        "total_deleted": int(feat_del or 0),
        "min_lat": min_lat,
        "min_lon": min_lon,
        "max_lat": max_lat,
        "max_lon": max_lon,
        "osmcha_suspect": record.get("is_suspect"),
        "osmcha_reasons": _reasons_text(record),
        "osmcha_editor": record.get("editor"),
    }


def fetch_osmcha_dataframe(
    *,
    token: str,
    users: list[str],
    date_from: str,
    date_to: str,
    suspect_only: bool = False,
) -> pd.DataFrame:
    """Fetch OSMCha changesets per user (avoids API result caps on combined queries)."""
    all_rows: list[dict] = []
    for user in users:
        records = fetch_changesets(
            token=token,
            users=[user],
            date_from=date_from,
            date_to=date_to,
            is_suspect=True if suspect_only else None,
        )
        all_rows.extend(normalize_osmcha_record(r) for r in records)

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    return df


def merge_osmcha_counts(
    osm_df: pd.DataFrame,
    osmcha_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge OSMCha feature counts and QC flags onto OSM API rows.
    Features modified in the dashboard use osmcha_feat_mod (featMod).
    """
    if osm_df.empty:
        return osm_df
    if osmcha_df.empty:
        from src.osm_ops_metrics import apply_features_modified

        return apply_features_modified(osm_df)

    cha_cols = [
        "changeset_id",
        "osmcha_feat_mod",
        "osmcha_feat_create",
        "osmcha_feat_del",
        "osmcha_suspect",
        "osmcha_reasons",
        "osmcha_editor",
    ]
    cha_cols = [c for c in cha_cols if c in osmcha_df.columns]
    flags = osmcha_df[cha_cols].drop_duplicates("changeset_id")

    out = osm_df.copy()
    out["changeset_id"] = out["changeset_id"].astype(str)
    flags["changeset_id"] = flags["changeset_id"].astype(str)
    out = out.merge(flags, on="changeset_id", how="left", suffixes=("_drop", ""))
    out = out[[c for c in out.columns if not c.endswith("_drop")]]
    from src.osm_ops_metrics import apply_features_modified

    return apply_features_modified(out)


# Backwards compatibility
merge_osmcha_flags = merge_osmcha_counts


def enrich_dataframe_with_osmcha(
    df: pd.DataFrame,
    *,
    token: str,
    users: list[str],
    date_from: str,
    date_to: str,
) -> pd.DataFrame:
    """Add OSMCha modified counts to an existing changeset DataFrame."""
    cha = fetch_osmcha_dataframe(
        token=token,
        users=users,
        date_from=date_from,
        date_to=date_to,
    )
    return merge_osmcha_counts(df, cha)


def test_connection(token: str) -> tuple[bool, str]:
    try:
        session = requests.Session()
        session.verify = False
        resp = session.get(
            f"{OSMCHA_API}/changesets/",
            params={"page_size": 1},
            headers=_headers(token),
            timeout=20,
        )
        if resp.status_code == 401:
            return False, "Invalid or missing OSMCha API token."
        resp.raise_for_status()
        return True, "Connected to OSMCha API."
    except requests.RequestException as exc:
        return False, str(exc)
