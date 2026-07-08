"""PostgreSQL persistence for OSM operations dashboard."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT / "sql" / "osm_ops_schema.sql"


def database_url(explicit: str | None = None) -> str | None:
    return (
        explicit
        or os.environ.get("OSM_OPS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )


def _connect(url: str):
    try:
        import psycopg2
    except ImportError as exc:
        raise ImportError(
            "Install psycopg2-binary: pip install -r requirements-osm-ops.txt"
        ) from exc
    return psycopg2.connect(url)


def init_schema(url: str | None = None) -> None:
    url = database_url(url)
    if not url:
        raise ValueError("Set OSM_OPS_DATABASE_URL or DATABASE_URL")
    sql = SCHEMA_SQL.read_text()
    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def is_available(url: str | None = None) -> bool:
    url = database_url(url)
    if not url:
        return False
    try:
        conn = _connect(url)
        conn.close()
        return True
    except Exception:
        return False


def upsert_changesets(df: pd.DataFrame, url: str | None = None) -> int:
    if df.empty:
        return 0
    url = database_url(url)
    if not url:
        raise ValueError("Database URL not configured")

    conn = _connect(url)
    n = 0
    try:
        with conn.cursor() as cur:
            for _, row in df.iterrows():
                cur.execute(
                    """
                    INSERT INTO osm_changesets (
                        changeset_id, user_name, created_at, closed_at, comment,
                        nodes_created, ways_created, relations_created, total_created,
                        nodes_modified, ways_modified, relations_modified, total_modified,
                        nodes_deleted, ways_deleted, relations_deleted, total_deleted,
                        min_lat, min_lon, max_lat, max_lon,
                        osmcha_suspect, osmcha_reasons, osmcha_editor, synced_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,NOW()
                    )
                    ON CONFLICT (changeset_id) DO UPDATE SET
                        user_name = EXCLUDED.user_name,
                        created_at = EXCLUDED.created_at,
                        closed_at = EXCLUDED.closed_at,
                        comment = EXCLUDED.comment,
                        nodes_created = EXCLUDED.nodes_created,
                        ways_created = EXCLUDED.ways_created,
                        relations_created = EXCLUDED.relations_created,
                        total_created = EXCLUDED.total_created,
                        nodes_modified = EXCLUDED.nodes_modified,
                        ways_modified = EXCLUDED.ways_modified,
                        relations_modified = EXCLUDED.relations_modified,
                        total_modified = EXCLUDED.total_modified,
                        nodes_deleted = EXCLUDED.nodes_deleted,
                        ways_deleted = EXCLUDED.ways_deleted,
                        relations_deleted = EXCLUDED.relations_deleted,
                        total_deleted = EXCLUDED.total_deleted,
                        min_lat = EXCLUDED.min_lat,
                        min_lon = EXCLUDED.min_lon,
                        max_lat = EXCLUDED.max_lat,
                        max_lon = EXCLUDED.max_lon,
                        osmcha_suspect = EXCLUDED.osmcha_suspect,
                        osmcha_reasons = EXCLUDED.osmcha_reasons,
                        osmcha_editor = EXCLUDED.osmcha_editor,
                        synced_at = NOW()
                    """,
                    (
                        int(row["changeset_id"]),
                        row["user"],
                        row.get("created_at"),
                        row.get("closed_at") or None,
                        row.get("comment") or "",
                        int(row.get("nodes_created") or 0),
                        int(row.get("ways_created") or 0),
                        int(row.get("relations_created") or 0),
                        int(row.get("total_created") or 0),
                        int(row.get("nodes_modified") or 0),
                        int(row.get("ways_modified") or 0),
                        int(row.get("relations_modified") or 0),
                        int(row.get("total_modified") or 0),
                        int(row.get("nodes_deleted") or 0),
                        int(row.get("ways_deleted") or 0),
                        int(row.get("relations_deleted") or 0),
                        int(row.get("total_deleted") or 0),
                        _float_or_none(row.get("min_lat")),
                        _float_or_none(row.get("min_lon")),
                        _float_or_none(row.get("max_lat")),
                        _float_or_none(row.get("max_lon")),
                        _bool_or_none(row.get("osmcha_suspect")),
                        row.get("osmcha_reasons") or None,
                        row.get("osmcha_editor") or None,
                    ),
                )
                n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def _float_or_none(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_or_none(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return bool(v)


def load_changesets(
    *,
    url: str | None = None,
    users: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    url = database_url(url)
    if not url:
        return pd.DataFrame()

    clauses = ["1=1"]
    params: list = []
    if users:
        clauses.append("user_name = ANY(%s)")
        params.append(users)
    if start_date:
        clauses.append("created_at >= %s::date")
        params.append(start_date)
    if end_date:
        clauses.append("created_at < (%s::date + interval '1 day')")
        params.append(end_date)

    sql = f"""
        SELECT
            user_name AS user,
            changeset_id,
            created_at,
            closed_at,
            comment,
            nodes_created, ways_created, relations_created, total_created,
            nodes_modified, ways_modified, relations_modified, total_modified,
            nodes_deleted, ways_deleted, relations_deleted, total_deleted,
            min_lat, min_lon, max_lat, max_lon,
            osmcha_suspect, osmcha_reasons, osmcha_editor
        FROM osm_changesets
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC
    """
    conn = _connect(url)
    try:
        return pd.read_sql(sql, conn, params=params or None)
    finally:
        conn.close()
