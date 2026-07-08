"""OSM operations dashboard config and per-mapper start-date filtering."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "osm_ops.yaml"


def load_osm_ops_config(path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _parse_config_date(value: str | date | datetime | None, default: str) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp(default, tz="UTC")
    if isinstance(value, pd.Timestamp):
        return value.tz_localize("UTC") if value.tz is None else value
    if isinstance(value, datetime):
        return pd.Timestamp(value, tz="UTC")
    if isinstance(value, date):
        return pd.Timestamp(value.isoformat(), tz="UTC")
    return pd.Timestamp(str(value), tz="UTC")


def mapper_start_timestamp(user: str, cfg: dict[str, Any] | None = None) -> pd.Timestamp:
    """Per-mapper project start (config mapper_start_dates, else global start_date)."""
    cfg = cfg or load_osm_ops_config()
    default = cfg.get("start_date") or "2024-12-01"
    per_user = cfg.get("mapper_start_dates") or {}
    return _parse_config_date(per_user.get(user), default)


def apply_mapper_start_filter(df: pd.DataFrame, cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    """Keep only changesets on or after each mapper's configured start date."""
    if df.empty or "created_at" not in df.columns:
        return df
    cfg = cfg or load_osm_ops_config()
    out = df.copy()
    out["created_at"] = pd.to_datetime(out["created_at"], utc=True, errors="coerce")
    starts = out["user"].map(lambda u: mapper_start_timestamp(str(u), cfg))
    return out[out["created_at"] >= starts].copy()


def project_date_bounds(cfg: dict[str, Any] | None = None) -> tuple[pd.Timestamp, pd.Timestamp | None]:
    cfg = cfg or load_osm_ops_config()
    start = _parse_config_date(cfg.get("start_date"), "2024-12-01")
    end_raw = cfg.get("end_date")
    end = _parse_config_date(end_raw, "") if end_raw else None
    return start, end
