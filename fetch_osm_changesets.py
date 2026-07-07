"""CLI wrapper — prefer: python3 scripts/sync_osm_ops.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.osm_fetch import export_changesets_csv, fetch_team_changesets

if __name__ == "__main__":
    USERNAMES = ["Pavang05", "ramshi04"]
    START_DATE = "2025-07-01"
    END_DATE = "2026-05-26"
    OUTPUT_FILE = "data/osm_changesets_with_counts.csv"

    df = fetch_team_changesets(
        USERNAMES,
        START_DATE,
        END_DATE,
        progress=lambda msg, _: print(msg),
    )
    export_changesets_csv(df, OUTPUT_FILE)
    print(f"\nCSV saved: {OUTPUT_FILE} ({len(df)} changesets)")
