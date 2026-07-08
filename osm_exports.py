"""Export filtered changesets to CSV and GeoJSON."""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import datetime, timezone
from typing import Any

import pandas as pd

EXPORT_COLUMNS = [
    "user",
    "changeset_id",
    "created_at",
    "closed_at",
    "comment",
    "nodes_created",
    "ways_created",
    "relations_created",
    "total_created",
    "features_modified",
    "total_modified",
    "total_deleted",
    "road_km",
    "road_km_est",
    "region",
    "min_lat",
    "min_lon",
    "max_lat",
    "max_lon",
    "osmcha_suspect",
    "osmcha_reasons",
]


def _export_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "changeset_id" in out.columns:
        out["changeset_id"] = out["changeset_id"].astype(str)
    cols = [c for c in EXPORT_COLUMNS if c in out.columns]
    extra = [c for c in out.columns if c not in cols and not c.startswith("_")]
    return out[cols + extra]


def export_csv_bytes(df: pd.DataFrame) -> bytes:
    return _export_frame(df).to_csv(index=False).encode("utf-8")


def _row_properties(row: pd.Series) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for col in EXPORT_COLUMNS:
        if col not in row.index:
            continue
        val = row[col]
        if pd.isna(val):
            continue
        if isinstance(val, pd.Timestamp):
            props[col] = val.isoformat()
        elif isinstance(val, (bool,)):
            props[col] = val
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            props[col] = float(val) if isinstance(val, float) else int(val)
        else:
            props[col] = str(val)
    props["changeset_id"] = str(row.get("changeset_id", ""))
    return props


def _bbox_geometry(row: pd.Series) -> dict[str, Any] | None:
    try:
        la1, lo1 = float(row["min_lat"]), float(row["min_lon"])
        la2, lo2 = float(row["max_lat"]), float(row["max_lon"])
    except (TypeError, ValueError, KeyError):
        return None
    if any(math.isnan(v) for v in (la1, lo1, la2, lo2)):
        return None

    if la1 == la2 and lo1 == lo2:
        return {"type": "Point", "coordinates": [lo1, la1]}

    ring = [
        [lo1, la1],
        [lo2, la1],
        [lo2, la2],
        [lo1, la2],
        [lo1, la1],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def changesets_to_geojson(df: pd.DataFrame) -> dict[str, Any]:
    """GeoJSON FeatureCollection — bbox polygon per changeset (or centroid point)."""
    features: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        geom = _bbox_geometry(row)
        if geom is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": str(row.get("changeset_id", "")),
                "geometry": geom,
                "properties": _row_properties(row),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def export_geojson_bytes(df: pd.DataFrame) -> bytes:
    return json.dumps(changesets_to_geojson(df), indent=2).encode("utf-8")


def export_per_mapper_zip(df: pd.DataFrame) -> bytes:
    """One CSV per mapper inside a ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if df.empty:
            zf.writestr("changesets_empty.csv", "user,changeset_id\n")
        else:
            for user, group in df.groupby("user"):
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(user))
                name = f"{safe}_changesets.csv"
                zf.writestr(name, _export_frame(group).to_csv(index=False))
    return buf.getvalue()


def export_filename(prefix: str, start: pd.Timestamp | None, end: pd.Timestamp | None, ext: str) -> str:
    s = start.strftime("%Y%m%d") if start is not None else "all"
    e = end.strftime("%Y%m%d") if end is not None else datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}_{s}_{e}.{ext}"
