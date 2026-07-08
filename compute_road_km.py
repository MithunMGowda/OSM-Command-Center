#!/usr/bin/env python3
"""
Compute road network km from OSM changeset diffs and save to CSV column `road_km`.

Usage:
  python3 scripts/compute_road_km.py --heuristic-only          # fast bbox estimate (all rows)
  python3 scripts/compute_road_km.py --users Pavang05 --limit 100 --force
  python3 scripts/compute_road_km.py --force                    # full diff run (slow)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.osm_fetch import export_changesets_csv  # noqa: E402
from src.road_km import (  # noqa: E402
    estimate_road_km_bbox,
    is_road_related_comment,
    road_km_from_changeset,
)


def load_config() -> dict:
    p = ROOT / "config" / "osm_ops.yaml"
    if p.exists():
        with p.open() as f:
            return yaml.safe_load(f) or {}
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute road network km per changeset")
    parser.add_argument("--csv", default=str(ROOT / "data" / "osm_changesets_with_counts.csv"))
    parser.add_argument("--users", nargs="+", help="Filter mappers")
    parser.add_argument("--limit", type=int, help="Max changesets to process from diff API")
    parser.add_argument("--heuristic-only", action="store_true", help="BBox estimate only (no API diffs)")
    parser.add_argument("--force", action="store_true", help="Recompute even if road_km already set")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    df = pd.read_csv(csv_path)
    if "road_km" not in df.columns:
        df["road_km"] = 0.0

    cfg = load_config()
    users = args.users or cfg.get("mappers")
    mask = pd.Series(True, index=df.index)
    if users:
        mask = df["user"].isin(users)

    road_mask = df["comment"].fillna("").apply(is_road_related_comment) & mask
    if args.force:
        need = road_mask
    else:
        need = road_mask & (pd.to_numeric(df["road_km"], errors="coerce").fillna(0) <= 0)

    if args.heuristic_only:
        for idx in df.index[need]:
            row = df.loc[idx]
            df.at[idx, "road_km"] = estimate_road_km_bbox(
                str(row.get("comment", "")),
                row.get("min_lat"),
                row.get("min_lon"),
                row.get("max_lat"),
                row.get("max_lon"),
                int(row.get("total_created") or 0),
            )
        export_changesets_csv(df, csv_path)
        print(f"Heuristic road_km written → {csv_path}")
        print(f"  Total road network: {df['road_km'].sum():,.1f} km")
        return 0

    to_process = df.index[need].tolist()
    if args.limit:
        to_process = to_process[: args.limit]

    print(f"Computing road km from diffs for {len(to_process)} changesets…", flush=True)
    for i, idx in enumerate(to_process):
        cs_id = str(df.at[idx, "changeset_id"])
        try:
            km = road_km_from_changeset(cs_id)
        except Exception as exc:
            print(f"  {cs_id} failed: {exc}", flush=True)
            row = df.loc[idx]
            km = estimate_road_km_bbox(
                str(row.get("comment", "")),
                row.get("min_lat"),
                row.get("min_lon"),
                row.get("max_lat"),
                row.get("max_lon"),
            )
        df.at[idx, "road_km"] = km
        if (i + 1) % 25 == 0:
            export_changesets_csv(df, csv_path)
            print(f"  {i + 1}/{len(to_process)} — total {df['road_km'].sum():,.1f} km", flush=True)

    export_changesets_csv(df, csv_path)
    print(f"Done. Total road network: {df['road_km'].sum():,.1f} km → {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
