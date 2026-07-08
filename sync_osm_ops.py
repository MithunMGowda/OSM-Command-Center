#!/usr/bin/env python3
"""
Sync OSM changesets for configured mappers → CSV and/or PostgreSQL.

Usage:
  python3 scripts/sync_osm_ops.py
  python3 scripts/sync_osm_ops.py --start 2026-05-25 --end 2026-05-26 --users Pavang05 ramshi04
  python3 scripts/sync_osm_ops.py --db-only
  python3 scripts/sync_osm_ops.py --no-diffs   # faster metadata-only

Cron example (daily 6am):
  0 6 * * * cd "/path/to/Geospatial Command Center" && OSM_OPS_DATABASE_URL=... python3 scripts/sync_osm_ops.py >> logs/osm_sync.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.osm_db import database_url, init_schema, is_available, upsert_changesets  # noqa: E402
from src.osm_fetch import export_changesets_csv, fetch_team_changesets  # noqa: E402
from src.osmcha_client import fetch_osmcha_dataframe, get_token, merge_osmcha_counts  # noqa: E402
from src.osm_ops_metrics import apply_features_modified  # noqa: E402


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync OSM operations data")
    parser.add_argument("--config", default=str(ROOT / "config" / "osm_ops.yaml"))
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    parser.add_argument("--users", nargs="+", help="OSM usernames")
    parser.add_argument("--csv", help="Output CSV path")
    parser.add_argument("--no-diffs", action="store_true", help="Skip diff download (faster)")
    parser.add_argument("--no-osmcha", action="store_true", help="Skip OSMCha merge")
    parser.add_argument("--db-only", action="store_true", help="Only write to PostgreSQL")
    parser.add_argument("--init-db", action="store_true", help="Create tables and exit")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge with existing CSV (dedupe by changeset_id)",
    )
    parser.add_argument(
        "--osmcha-only",
        action="store_true",
        help="Only enrich existing CSV with OSMCha counts (no OSM API fetch)",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    users = args.users or cfg.get("mappers") or ["Pavang05", "ramshi04"]
    end = args.end or cfg.get("end_date") or datetime.utcnow().strftime("%Y-%m-%d")
    start = args.start or cfg.get("start_date") or (
        datetime.utcnow() - timedelta(days=7)
    ).strftime("%Y-%m-%d")
    csv_path = args.csv or cfg.get("output_csv") or "data/osm_changesets_with_counts.csv"
    download_diffs = not args.no_diffs and cfg.get("download_diffs", True)
    merge_osmcha = not args.no_osmcha and cfg.get("merge_osmcha", True)

    db_url = database_url(cfg.get("database_url"))

    if args.init_db:
        if not db_url:
            print("Set OSM_OPS_DATABASE_URL or database_url in config.")
            return 1
        init_schema(db_url)
        print("Schema initialized.")
        return 0

    import pandas as pd

    csv_full = ROOT / csv_path
    token = get_token(cfg.get("osmcha_token"))

    if args.osmcha_only:
        if not token:
            print("Set OSMCHA_TOKEN for --osmcha-only")
            return 1
        if not csv_full.exists():
            print(f"No CSV at {csv_full}")
            return 1
        df = pd.read_csv(csv_full)
        print(f"Enriching {len(df)} rows from OSMCha…", flush=True)
        cha = fetch_osmcha_dataframe(token=token, users=users, date_from=start, date_to=end)
        df = merge_osmcha_counts(df, cha)
        export_changesets_csv(df, csv_full)
        print(f"  OSMCha records: {len(cha)}, wrote {csv_full}")
        return 0

    print(f"Sync {start} → {end} for {', '.join(users)}", flush=True)

    existing = pd.read_csv(csv_full) if csv_full.exists() else pd.DataFrame()
    new_parts: list[pd.DataFrame] = []

    for username in users:
        print(f'  User "{username}"…', flush=True)
        part = fetch_team_changesets(
            [username],
            start,
            end,
            download_diffs=download_diffs,
            progress=lambda msg, _: print(f"    {msg}", flush=True),
        )
        new_parts.append(part)
        print(f"    {len(part)} changesets", flush=True)

        if not args.db_only:
            if args.append and not existing.empty:
                keep = existing[~existing["user"].eq(username)]
                checkpoint = pd.concat([keep, part], ignore_index=True)
            else:
                checkpoint = pd.concat(new_parts, ignore_index=True)
            export_changesets_csv(checkpoint, csv_full)
            existing = checkpoint
            print(f"    Checkpoint → {csv_path} ({len(checkpoint)} rows)", flush=True)

    df = pd.concat(new_parts, ignore_index=True) if new_parts else pd.DataFrame()
    print(f"  OSM API total (this run): {len(df)} changesets", flush=True)

    if args.append and not existing.empty:
        if not new_parts:
            df = existing
        else:
            keep = existing[~existing["user"].isin(users)]
            df = pd.concat([keep, df], ignore_index=True)
        df = df.drop_duplicates(subset=["changeset_id"], keep="last")
        print(f"  Combined CSV: {len(df)} rows", flush=True)

    if merge_osmcha and token:
        try:
            enrich_users = sorted(df["user"].dropna().unique().tolist()) if not df.empty else users
            cha = fetch_osmcha_dataframe(
                token=token,
                users=enrich_users,
                date_from=start,
                date_to=end,
            )
            df = merge_osmcha_counts(df, cha)
            suspects = cha["osmcha_suspect"].fillna(False).astype(bool).sum() if not cha.empty else 0
            mod_sum = int(df["features_modified"].sum()) if "features_modified" in df.columns else 0
            print(f"  OSMCha: {len(cha)} records, {suspects} suspect, {mod_sum:,} features modified", flush=True)
        except Exception as exc:
            print(f"  OSMCha warning: {exc}", flush=True)
    elif merge_osmcha:
        print("  OSMCha skipped (set OSMCHA_TOKEN)", flush=True)
        df = apply_features_modified(df)

    if not args.db_only:
        out = export_changesets_csv(df, csv_full)
        print(f"  Wrote {out}")

    if db_url and is_available(db_url):
        init_schema(db_url)
        n = upsert_changesets(df, db_url)
        print(f"  PostgreSQL: upserted {n} rows")
    elif db_url:
        print("  PostgreSQL: connection failed (is docker compose up?)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
