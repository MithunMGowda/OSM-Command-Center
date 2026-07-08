#!/usr/bin/env python3
"""
Background live tracker for OSM team edits.

Polls the OSM API on an interval and appends new changesets to CSV (and optionally PostgreSQL).

Usage:
  python3 scripts/live_osm_tracker.py
  python3 scripts/live_osm_tracker.py --interval 60 --since-hours 6
  python3 scripts/live_osm_tracker.py --users Pavang05 ramshi04

Cron / systemd: run as a long-lived process or use --once for single poll.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.osm_db import database_url, init_schema, is_available, upsert_changesets  # noqa: E402
from src.osm_live import poll_and_merge  # noqa: E402
from src.osm_ops_metrics import load_changesets  # noqa: E402


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Live OSM changeset tracker")
    parser.add_argument("--config", default=str(ROOT / "config" / "osm_ops.yaml"))
    parser.add_argument("--csv", help="Output CSV path")
    parser.add_argument("--users", nargs="+", help="Mapper usernames")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval seconds")
    parser.add_argument("--since-hours", type=float, default=1.0, help="Initial lookback window")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    parser.add_argument("--db", action="store_true", help="Upsert to PostgreSQL after each poll")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    users = args.users or cfg.get("mappers") or []
    if not users:
        print("No mappers configured.")
        return 1

    csv_path = Path(args.csv or cfg.get("output_csv") or ROOT / "data" / "osm_changesets_with_counts.csv")
    existing = load_changesets(csv_path)
    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    since = since.replace(tzinfo=None)
    db_url = database_url(cfg.get("database_url"))

    if args.db and db_url:
        init_schema(db_url)

    print(f"Live tracker: {len(users)} mapper(s), every {args.interval}s, CSV → {csv_path}", flush=True)

    while True:
        try:
            merged, new_only, since = poll_and_merge(
                existing,
                users,
                since,
                csv_path=csv_path,
                on_status=lambda msg: print(f"  {msg}", flush=True),
            )
            existing = merged
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts}] +{len(new_only)} new changeset(s), total {len(merged)}", flush=True)

            if args.db and db_url and is_available(db_url) and not new_only.empty:
                upsert_changesets(new_only, db_url)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        except Exception as exc:
            print(f"Poll error: {exc}", flush=True)

        if args.once:
            return 0

        time.sleep(max(args.interval, 15))


if __name__ == "__main__":
    raise SystemExit(main())
