"""Delete recorded data for one app or one time range from a session's screenpipe folder."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .timeutil import parse_timestamp


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}


def _remove_file(data_dir: Path, rel_or_abs: str | None) -> int:
    if not rel_or_abs:
        return 0
    p = Path(rel_or_abs)
    candidates = [p] if p.is_absolute() else [data_dir / p, data_dir / "data" / p]
    for c in candidates:
        if c.exists():
            try:
                c.unlink()
                return 1
            except OSError:
                return 0
    return 0


def _delete_frames(conn: sqlite3.Connection, data_dir: Path, frame_ids: list[int]) -> dict:
    removed = {"frames": 0, "files": 0, "ui_events": 0}
    if not frame_ids:
        return removed
    tables = _tables(conn)
    for i in range(0, len(frame_ids), 500):
        chunk = frame_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"select snapshot_path from frames where id in ({marks})", chunk):
            removed["files"] += _remove_file(data_dir, r["snapshot_path"])
        if "ui_events" in tables:
            cur = conn.execute(f"delete from ui_events where frame_id in ({marks})", chunk)
            removed["ui_events"] += cur.rowcount
        for t in ("elements", "activity_evidence"):
            if t in tables:
                try:
                    conn.execute(f"delete from {t} where frame_id in ({marks})", chunk)
                except sqlite3.Error:
                    pass
        cur = conn.execute(f"delete from frames where id in ({marks})", chunk)
        removed["frames"] += cur.rowcount
    return removed


def redact_app(data_dir: Path, app_substring: str) -> dict:
    """Remove every frame, screenshot, and input event whose app or window title contains the text."""
    db = data_dir / "db.sqlite"
    conn = _connect(db)
    like = f"%{app_substring.lower()}%"
    with conn:
        ids = [r[0] for r in conn.execute(
            "select id from frames where lower(coalesce(app_name,'')) like ? or lower(coalesce(window_name,'')) like ?", (like, like))]
        removed = _delete_frames(conn, data_dir, ids)
        if "ui_events" in _tables(conn):
            cur = conn.execute(
                "delete from ui_events where lower(coalesce(app_name,'')) like ? or lower(coalesce(window_title,'')) like ?", (like, like))
            removed["ui_events"] += cur.rowcount
    conn.execute("VACUUM")
    conn.close()
    return removed


def redact_range(data_dir: Path, start_ts: float, end_ts: float) -> dict:
    """Remove frames, input events, and audio between two epoch timestamps (inclusive)."""
    db = data_dir / "db.sqlite"
    conn = _connect(db)
    tables = _tables(conn)
    with conn:
        ids = [r["id"] for r in conn.execute("select id, timestamp from frames")
               if start_ts <= parse_timestamp(r["timestamp"]) <= end_ts]
        removed = _delete_frames(conn, data_dir, ids)
        if "ui_events" in tables:
            ev = [r["id"] for r in conn.execute("select id, timestamp from ui_events")
                  if start_ts <= parse_timestamp(r["timestamp"]) <= end_ts]
            for i in range(0, len(ev), 500):
                chunk = ev[i:i + 500]
                marks = ",".join("?" * len(chunk))
                removed["ui_events"] += conn.execute(f"delete from ui_events where id in ({marks})", chunk).rowcount
        removed["audio_chunks"] = 0
        if "audio_chunks" in tables:
            chunks = [(r["id"], r["file_path"]) for r in conn.execute("select id, file_path, timestamp from audio_chunks")
                      if r["timestamp"] is not None and start_ts <= parse_timestamp(r["timestamp"]) <= end_ts]
            for cid, path in chunks:
                removed["files"] += _remove_file(data_dir, path)
                if "audio_transcriptions" in tables:
                    conn.execute("delete from audio_transcriptions where audio_chunk_id=?", (cid,))
                conn.execute("delete from audio_chunks where id=?", (cid,))
                removed["audio_chunks"] += 1
    conn.execute("VACUUM")
    conn.close()
    return removed
