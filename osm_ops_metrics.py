"""Aggregate OSM changeset CSV exports for the operations dashboard."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from src.osm_ops_config import apply_mapper_start_filter, load_osm_ops_config, mapper_start_timestamp, project_date_bounds
from src.road_km import estimate_road_km_bbox, is_road_related_comment

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CHANGESETS_CSV = ROOT / "osm_changesets_with_counts.csv"
DEFAULT_ERRORS_CSV = ROOT / "ramshi04_errors_by_changeset.csv"

__all__ = [
    "DEFAULT_CHANGESETS_CSV",
    "DEFAULT_ERRORS_CSV",
    "apply_features_modified",
    "apply_mapper_start_filter",
    "build_daily_activity_counts",
    "build_mapper_summary",
    "changesets_with_errors",
    "daily_activity_figure",
    "demo_changesets",
    "edit_hotspot_points",
    "filter_changesets",
    "global_kpis",
    "highway_hierarchy_cell",
    "hotspot_map_center",
    "hotspot_map_zoom",
    "load_changesets",
    "load_errors",
    "load_osm_ops_config",
    "mapper_start_timestamp",
    "project_date_bounds",
]

HIGHWAY_KEYWORDS = [
    ("motorway", "Motorway"),
    ("trunk", "Trunk"),
    ("primary", "Primary"),
    ("secondary", "Secondary"),
    ("tertiary", "Tertiary"),
    ("residential", "Residential"),
    ("service", "Service"),
    ("unclassified", "Unclassified"),
    ("track", "Track"),
    ("footway", "Footway"),
]


def _region_from_bbox(row: pd.Series) -> str:
    """Rough city label from changeset bounding box."""
    for col in ("min_lat", "max_lat", "min_lon", "max_lon"):
        if col not in row.index or pd.isna(row.get(col)):
            return _region_from_comment(str(row.get("comment", "")))
    try:
        lat = (float(row["min_lat"]) + float(row["max_lat"])) / 2
        lon = (float(row["min_lon"]) + float(row["max_lon"])) / 2
    except (TypeError, ValueError):
        return _region_from_comment(str(row.get("comment", "")))

    if 12.8 <= lat <= 13.2 and 77.4 <= lon <= 77.8:
        return "Bangalore"
    if 13.0 <= lat <= 13.2 and 80.2 <= lon <= 80.3:
        return "Chennai"
    if 17.3 <= lat <= 17.5 and 78.4 <= lon <= 78.6:
        return "Hyderabad"
    if 18.9 <= lat <= 19.3 and 72.8 <= lon <= 73.0:
        return "Mumbai"
    if 18.9 <= lat <= 19.3 and 73.1 <= lon <= 73.4:
        return "Navi Mumbai"
    if 28.5 <= lat <= 28.8 and 77.1 <= lon <= 77.5:
        return "Delhi NCR"
    return "Other"


def _region_from_comment(comment: str) -> str:
    """Fallback when bbox columns are absent."""
    return "Unknown"


def _estimate_road_km_row(row: pd.Series) -> float:
    """Road km: stored road_km column, else diff ways, else bbox heuristic."""
    stored = row.get("road_km")
    if stored is not None and pd.notna(stored):
        try:
            val = float(stored)
            if val > 0:
                return round(val, 3)
        except (TypeError, ValueError):
            pass

    ways = int(row.get("ways_created") or 0)
    nodes = int(row.get("nodes_created") or 0)
    if ways > 0:
        return _estimate_road_km(ways, nodes)

    return estimate_road_km_bbox(
        str(row.get("comment", "")),
        row.get("min_lat"),
        row.get("min_lon"),
        row.get("max_lat"),
        row.get("max_lon"),
        int(row.get("total_created") or 0),
    )


def _highway_breakdown(comment: str, ways_created: int) -> dict[str, float]:
    """Assign way count to highway classes using comment keywords (heuristic)."""
    text = (comment or "").lower()
    breakdown: dict[str, float] = {}
    matched = False
    n = max(ways_created, 1 if is_road_related_comment(comment) else 0)
    for keyword, label in HIGHWAY_KEYWORDS:
        if keyword in text:
            breakdown[label] = breakdown.get(label, 0) + n
            matched = True
    if not matched and ways_created > 0:
        breakdown["Other"] = breakdown.get("Other", 0) + ways_created
    return breakdown


def _estimate_road_km(ways_created: int, nodes_created: int) -> float:
    """Rough road length from element counts when geometry is unavailable."""
    if ways_created <= 0:
        return 0.0
    nodes_per_way = nodes_created / ways_created if ways_created else 4
    km_per_way = min(0.15, max(0.02, nodes_per_way * 0.008))
    return round(ways_created * km_per_way, 2)


def apply_features_modified(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dashboard metric: Features Modified = OSMCha featMod when available,
    otherwise OSM diff total_modified.
    When OSMCha create/delete counts exist and OSM diff is zero, use OSMCha.
    """
    if df.empty:
        return df
    out = df.copy()
    # Modified: prefer OSMCha featMod; else OSM API modified_count (total_modified column)
    osm_mod = pd.to_numeric(out.get("total_modified", 0), errors="coerce").fillna(0)
    if "osmcha_feat_mod" in out.columns:
        cha_mod = pd.to_numeric(out["osmcha_feat_mod"], errors="coerce")
        out["features_modified"] = cha_mod.where(cha_mod.notna(), osm_mod).astype(int)
    else:
        out["features_modified"] = osm_mod.astype(int)

    osm_created = pd.to_numeric(out.get("total_created", 0), errors="coerce").fillna(0)
    osm_deleted = pd.to_numeric(out.get("total_deleted", 0), errors="coerce").fillna(0)
    if "osmcha_feat_create" in out.columns:
        cha_c = pd.to_numeric(out["osmcha_feat_create"], errors="coerce").fillna(0)
        out["total_created"] = osm_created.where(osm_created > 0, cha_c).astype(int)
    if "osmcha_feat_del" in out.columns:
        cha_d = pd.to_numeric(out["osmcha_feat_del"], errors="coerce").fillna(0)
        out["total_deleted"] = osm_deleted.where(osm_deleted > 0, cha_d).astype(int)
    return out


def load_changesets(path: Path | None = None) -> pd.DataFrame:
    path = path or DEFAULT_CHANGESETS_CSV
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    for col in [
        "nodes_created",
        "ways_created",
        "relations_created",
        "total_created",
        "total_modified",
        "total_deleted",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    df = apply_features_modified(df)
    if "osmcha_suspect" in df.columns:
        df["osmcha_suspect"] = df["osmcha_suspect"].map(
            lambda x: x in (True, "True", "true", 1, "1") if pd.notna(x) else False
        )
    if all(c in df.columns for c in ("min_lat", "max_lat", "min_lon", "max_lon")):
        df["region"] = df.apply(_region_from_bbox, axis=1)
    else:
        user_default = {"Pavang05": "Mumbai", "ramshi04": "Navi Mumbai"}
        df["region"] = df["user"].map(user_default).fillna("Mumbai / NCR")
    df["road_km_est"] = df.apply(_estimate_road_km_row, axis=1)
    df["gates_added"] = df["comment"].fillna("").str.lower().str.contains("gate").astype(int)
    df["has_building"] = df["comment"].fillna("").str.lower().str.contains("building").astype(int)
    return df


def load_errors(path: Path | None = None) -> pd.DataFrame:
    path = path or DEFAULT_ERRORS_CSV
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "changeset_id" in df.columns:
        df["changeset_id"] = df["changeset_id"].astype(str)
    return df


def changesets_with_errors(
    errors: pd.DataFrame,
    active_changeset_ids: set[str] | None = None,
    changesets_df: pd.DataFrame | None = None,
) -> set[str]:
    ids: set[str] = set()
    if not errors.empty and "changeset_id" in errors.columns:
        ids |= set(errors["changeset_id"].astype(str).unique())
    if changesets_df is not None and "osmcha_suspect" in changesets_df.columns:
        suspect = changesets_df[
            changesets_df["osmcha_suspect"].fillna(False).astype(bool)
        ]
        ids |= set(suspect["changeset_id"].astype(str).unique())
    if active_changeset_ids is not None:
        ids &= active_changeset_ids
    return ids


def filter_changesets(
    df: pd.DataFrame,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
    users: list[str] | None = None,
    region: str | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if users:
        out = out[out["user"].isin(users)]
    if start_date is not None:
        out = out[out["created_at"] >= start_date]
    if end_date is not None:
        out = out[out["created_at"] < end_date + pd.Timedelta(days=1)]
    if region and region != "All Regions":
        out = out[out["region"] == region]
    return apply_features_modified(out)


def _hotspot_weight(row: pd.Series, weight_by: str) -> float:
    if weight_by == "changesets":
        return 1.0
    if weight_by == "added":
        return float(row.get("total_created") or 0)
    if weight_by == "road_km":
        return float(row.get("road_km_est") or row.get("road_km") or 0)
    added = float(row.get("total_created") or 0)
    modified = float(row.get("features_modified") or row.get("total_modified") or 0)
    removed = float(row.get("total_deleted") or 0)
    return math.log1p(added + modified + removed)


def edit_hotspot_points(
    df: pd.DataFrame,
    weight_by: str = "activity",
) -> list[list[float]]:
    """Build [lat, lon, weight] rows for a Folium HeatMap from changeset bboxes."""
    if df.empty:
        return []
    bbox_cols = ("min_lat", "min_lon", "max_lat", "max_lon")
    if not all(c in df.columns for c in bbox_cols):
        return []

    points: list[list[float]] = []
    for _, row in df.iterrows():
        try:
            la1, lo1 = float(row["min_lat"]), float(row["min_lon"])
            la2, lo2 = float(row["max_lat"]), float(row["max_lon"])
        except (TypeError, ValueError):
            continue
        if any(math.isnan(v) for v in (la1, lo1, la2, lo2)):
            continue
        weight = _hotspot_weight(row, weight_by)
        if weight <= 0 and weight_by != "changesets":
            continue
        points.append([(la1 + la2) / 2, (lo1 + lo2) / 2, weight])
    return points


def hotspot_map_center(points: list[list[float]]) -> list[float]:
    if not points:
        return [20.5937, 78.9629]
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return [sum(lats) / len(lats), sum(lons) / len(lons)]


def hotspot_map_zoom(points: list[list[float]]) -> int:
    if not points:
        return 5
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    span = max(max(lats) - min(lats), max(lons) - min(lons))
    if span > 10:
        return 5
    if span > 3:
        return 6
    if span > 1:
        return 8
    if span > 0.2:
        return 10
    return 12


def build_mapper_summary(df: pd.DataFrame, error_cs_ids: set[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows = []
    for user, g in df.groupby("user"):
        highway: dict[str, float] = {}
        for _, r in g.iterrows():
            part = _highway_breakdown(str(r.get("comment", "")), int(r.get("ways_created", 0)))
            for k, v in part.items():
                highway[k] = highway.get(k, 0) + v

        cs_ids = g["changeset_id"].astype(str)
        rejected = sum(1 for cid in cs_ids if cid in error_cs_ids)
        approved = len(g) - rejected

        rows.append(
            {
                "Mapper": user,
                "Start date": mapper_start_timestamp(str(user)).strftime("%Y-%m-%d"),
                "Features Added": int(g["total_created"].sum()),
                "Features Modified": int(g["features_modified"].sum()),
                "Features Removed": int(g["total_deleted"].sum()),
                "Total Contributions": int(
                    g["total_created"].sum() + g["features_modified"].sum() + g["total_deleted"].sum()
                ),
                "Road Network Added (km)": round(g["road_km_est"].sum(), 1),
                "Access Gates Added": int(g["gates_added"].sum()),
                "Changesets": len(g),
                "QC Approved": approved,
                "QC Rejected": rejected,
                "Region": g["region"].mode().iloc[0] if len(g) else "Unknown",
                "_highway": highway,
            }
        )

    summary = pd.DataFrame(rows)
    if "Start date" not in summary.columns and "Mapper" in summary.columns:
        summary["Start date"] = summary["Mapper"].map(
            lambda m: mapper_start_timestamp(str(m)).strftime("%Y-%m-%d")
        )
    return summary


def highway_hierarchy_cell(highway: dict[str, float]) -> str:
    if not highway:
        return "—"
    parts = []
    for label in ["Motorway", "Trunk", "Primary", "Secondary", "Tertiary", "Residential", "Service", "Other"]:
        if label in highway and highway[label] > 0:
            parts.append(f"{label}: {highway[label]:.0f} ways")
    return " · ".join(parts) if parts else "—"


def global_kpis(df: pd.DataFrame, mapper_df: pd.DataFrame) -> dict[str, str | float]:
    if df.empty:
        return {}

    df = apply_features_modified(df)
    total_contrib = int(
        df["total_created"].sum() + df["features_modified"].sum() + df["total_deleted"].sum()
    )
    approved = int(mapper_df["QC Approved"].sum()) if not mapper_df.empty else 0
    rejected = int(mapper_df["QC Rejected"].sum()) if not mapper_df.empty else 0
    qc_total = approved + rejected
    quality = (100.0 * approved / qc_total) if qc_total else 100.0

    return {
        "total_contributions": total_contrib,
        "features_added": int(df["total_created"].sum()),
        "features_modified": int(df["features_modified"].sum()),
        "features_removed": int(df["total_deleted"].sum()),
        "active_mappers": df["user"].nunique(),
        "road_km": round(df["road_km_est"].sum(), 1),
        "gates": int(df["gates_added"].sum()),
        "qc_approved": approved,
        "qc_rejected": rejected,
        "quality_score": round(quality, 1),
        "building_polygons": int(df["has_building"].sum()) if "has_building" in df.columns else 0,
        "changesets": len(df),
    }


def demo_changesets() -> pd.DataFrame:
    """Sample data matching the reference dashboard layout."""
    data = [
        ("mithun_mapper", 412, 380, 45, 42.5, 18, "Bangalore", "residential road", 12.97, 77.59),
        ("rahul_geo", 388, 350, 52, 38.2, 22, "Chennai", "tertiary road", 13.08, 80.27),
        ("ajay_qc", 295, 280, 38, 31.0, 15, "Hyderabad", "service road and gate", 17.41, 78.49),
        ("Pavang05", 1214, 0, 106, 28.4, 55, "Mumbai", "road and gate", 19.16, 72.85),
        ("ramshi04", 1526, 0, 150, 35.2, 73, "Navi Mumbai", "residential road and gate", 19.17, 73.24),
    ]
    rows = []
    for user, added, updated, removed, km, gates, region, comment, lat, lon in data:
        ways = max(1, added // 7)
        nodes = ways * 5
        rows.append(
            {
                "user": user,
                "changeset_id": f"demo_{user}",
                "created_at": pd.Timestamp("2026-05-26", tz="UTC"),
                "closed_at": "",
                "comment": comment,
                "nodes_created": nodes,
                "ways_created": ways,
                "relations_created": 0,
                "total_created": added,
                "nodes_modified": 0,
                "ways_modified": 0,
                "relations_modified": 0,
                "total_modified": updated,
                "nodes_deleted": removed,
                "ways_deleted": 0,
                "relations_deleted": 0,
                "total_deleted": removed,
                "min_lat": lat - 0.01,
                "min_lon": lon - 0.01,
                "max_lat": lat + 0.01,
                "max_lon": lon + 0.01,
                "region": region,
                "road_km_est": km,
                "gates_added": gates,
                "has_building": 0,
                "osmcha_feat_mod": updated,
            }
        )
    return apply_features_modified(pd.DataFrame(rows))


# --- Daily activity bar chart (last 10 days, Y max 200) ---

from collections import defaultdict as _defaultdict
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone

ACTIVITY_Y_MAX = 200
ACTIVITY_LAST_N_DAYS = 10
ActivityRow = tuple[_date, str, int]


def _activity_to_utc_date(value: object) -> _date | None:
    if value is None:
        return None
    if isinstance(value, _date) and not isinstance(value, _datetime):
        return value
    if isinstance(value, _datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=_timezone.utc)
        return dt.astimezone(_timezone.utc).date()
    text = str(value).strip()
    if not text or text.lower() in ("nat", "none", "nan"):
        return None
    try:
        dt = _datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_timezone.utc)
    return dt.astimezone(_timezone.utc).date()


def build_daily_activity_counts(
    df: object,
    mappers: list[str] | None = None,
) -> list[ActivityRow]:
    if df is None or not hasattr(df, "itertuples"):
        return []

    mapper_set = {str(m) for m in mappers} if mappers else None
    buckets: dict[tuple[_date, str], int] = _defaultdict(int)

    cols = getattr(df, "columns", None)
    if cols is None or "created_at" not in cols or "user" not in cols:
        return []

    for row in df.itertuples(index=False):
        user = str(getattr(row, "user", ""))
        if mapper_set is not None and user not in mapper_set:
            continue
        day = _activity_to_utc_date(getattr(row, "created_at", None))
        if day is None:
            continue
        buckets[(day, user)] += 1

    return sorted((d, u, c) for (d, u), c in buckets.items())


def _activity_last_n_days(end_day: _date, n: int = ACTIVITY_LAST_N_DAYS) -> list[_date]:
    return [end_day - _timedelta(days=n - 1 - i) for i in range(n)]


def daily_activity_figure(
    counts: list[ActivityRow],
    *,
    last_n_days: int = ACTIVITY_LAST_N_DAYS,
):
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(11, 4.5))

    users = sorted({u for _, u, _ in counts})
    end_day = _datetime.now(_timezone.utc).date()
    if counts:
        end_day = min(end_day, max(d for d, _, _ in counts))

    days = _activity_last_n_days(end_day, last_n_days)
    day_set = set(days)

    if not users:
        ax.set_ylim(0, ACTIVITY_Y_MAX)
        ax.set_ylabel("Changesets")
        ax.text(0.5, 0.5, "No activity for selected mappers", ha="center", va="center", transform=ax.transAxes)
        fig.tight_layout()
        return fig

    day_index = {d: i for i, d in enumerate(days)}
    user_index = {u: i for i, u in enumerate(users)}

    matrix = np.zeros((len(days), len(users)), dtype=np.int64)
    for day, user, n in counts:
        if day in day_set:
            matrix[day_index[day], user_index[user]] = n

    n_users = len(users)
    x = np.arange(len(days))
    bar_w = 0.72 / max(n_users, 1)

    for i, user in enumerate(users):
        ax.bar(
            x + i * bar_w,
            matrix[:, i],
            width=bar_w,
            label=user,
            edgecolor="white",
            linewidth=0.4,
        )

    if int(matrix.sum()) == 0:
        ax.text(
            0.5,
            0.5,
            "No changesets in the last 10 days for selected mappers.\nTry widening the date filter or picking other mappers.",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=10,
            color="#64748b",
        )

    ax.set_ylim(0, ACTIVITY_Y_MAX)
    ax.set_ylabel("Changesets per day")
    ax.set_xlabel(f"Last {last_n_days} days (UTC)")
    ax.set_xticks(x + bar_w * (n_users - 1) / 2)
    ax.set_xticklabels([d.strftime("%a\n%d %b") for d in days], fontsize=9)
    ax.legend(title="Mapper", loc="upper right", fontsize=8, ncol=min(n_users, 2))
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return fig
