"""Pull the session's records out of the running screenpipe daemon into plain JSONL files.

screenpipe 0.4.5x keeps its index at <data-dir>/storage/<generation>/index.sqlite and moves text columns into
parquet payloads soon after capture. Only the daemon can read those back, so the kit asks the daemon's local API
(with the per-session key it was started with) for every row before the recorder is stopped, and writes:

  <session>/export/ui_events.jsonl    one row per input event, every column of screenpipe's ui_events table
  <session>/export/frames.jsonl       one row per screenshot: app, window, url, trigger, screen text, snapshot path
  <session>/export/audio_chunks.jsonl one row per 30-s audio file
  <session>/export/summary.json       counts, apps, domains (the same shape as screenpipe.capture_stats)

Paths inside the JSONL are relative to the session folder, so the bundle is self-contained.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from .timeutil import iso_utc, parse_timestamp

UI_EVENT_COLUMNS = ["id", "timestamp", "session_id", "relative_ms", "event_type", "x", "y", "delta_x", "delta_y", "button",
                    "click_count", "key_code", "modifiers", "text_content", "text_length", "app_name", "app_pid", "window_title",
                    "browser_url", "element_role", "element_name", "element_value", "element_description", "element_bounds",
                    "frame_id"]
FRAME_META_COLUMNS = ["id", "timestamp", "app_name", "window_name", "browser_url", "capture_trigger", "text_source",
                      "snapshot_path", "focused", "device_name"]
AUDIO_COLUMNS = ["id", "file_path", "timestamp", "transcription_status"]


class ApiError(RuntimeError):
    pass


def _request(port: int, key: str | None, path: str, params: dict | None = None, body: dict | None = None, timeout: float = 120.0):
    url = f"http://127.0.0.1:{port}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read()[:300].decode("utf-8", errors="replace")
        except OSError:
            detail = ""
        raise ApiError(f"{path} -> HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ApiError(f"{path} -> {exc}") from None


def raw_sql(port: int, key: str | None, query: str) -> list[dict]:
    res = _request(port, key, "/raw_sql", body={"query": query})
    if isinstance(res, dict):
        res = res.get("data") or res.get("rows") or []
    return list(res)


def raw_sql_all(port: int, key: str | None, table: str, columns: list[str], page: int = 5000) -> list[dict]:
    """Every row of `table` ordered by id, in pages, so the API's LIMIT rule is respected."""
    rows: list[dict] = []
    last = 0
    cols = ", ".join(columns)
    while True:
        chunk = raw_sql(port, key, f"select {cols} from {table} where id > {last} order by id limit {page}")
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        last = int(chunk[-1]["id"])


def search_all(port: int, key: str | None, content_type: str, start_iso: str, end_iso: str, page: int = 2000) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        res = _request(port, key, "/search", params={"content_type": content_type, "limit": page, "offset": offset,
                                                      "start_time": start_iso, "end_time": end_iso})
        data = res.get("data", []) if isinstance(res, dict) else list(res)
        items.extend(data)
        total = (res.get("pagination") or {}).get("total") if isinstance(res, dict) else None
        offset += len(data)
        if not data or (total is not None and offset >= total) or len(data) < page:
            return items


def _relative(path: str | None, session_dir: Path) -> str | None:
    """Make a path the daemon wrote relative to the session folder (`screenpipe/data/...`)."""
    if not path:
        return path
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.relative_to(session_dir).as_posix()
    except ValueError:
        parts = p.parts
        if "screenpipe" in parts:  # the folder was moved or copied; keep the part under the data dir
            return Path(*parts[len(parts) - 1 - parts[::-1].index("screenpipe"):]).as_posix()
        return p.as_posix()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def summarize(session_dir: Path, frames: list[dict], events: list[dict], audio: list[dict]) -> dict:
    stats = {"frames": len(frames), "ui_events": dict(Counter(e.get("event_type") for e in events)),
             "audio_chunks": len(audio), "audio_files": 0, "apps": [], "domains": [], "typed_chars": 0}
    stats["typed_chars"] = sum(int(e.get("text_length") or len(e.get("text_content") or "")) for e in events if e.get("event_type") == "text")
    apps, domains = Counter(), Counter()
    for f in frames:
        if f.get("app_name"):
            apps[f["app_name"]] += 1
        url = f.get("browser_url") or ""
        if url:
            host = urlparse(url if "://" in url else "https://" + url).netloc
            if host:
                domains[host] += 1
    stats["apps"] = apps.most_common()
    stats["domains"] = domains.most_common(15)
    stats["audio_files"] = sum(1 for a in audio if a.get("file_path") and (session_dir / a["file_path"]).exists())
    return stats


def export_session(port: int, key: str | None, session_dir: Path, start_ts: float, end_ts: float | None = None) -> dict:
    """Ask the daemon for every record between start_ts and end_ts (epoch seconds) and write export/*.jsonl."""
    end_ts = end_ts or time.time()
    start_iso, end_iso = iso_utc(start_ts - 120), iso_utc(end_ts + 120)
    out = session_dir / "export"
    out.mkdir(exist_ok=True)

    try:
        events = raw_sql_all(port, key, "_bulk_logical_ui_events", UI_EVENT_COLUMNS)
    except ApiError:
        events = raw_sql_all(port, key, "ui_events", UI_EVENT_COLUMNS)  # older storage: plain table
    events = [e for e in events if start_ts - 120 <= parse_timestamp(e["timestamp"]) <= end_ts + 120]

    meta = {int(r["id"]): r for r in raw_sql_all(port, key, "frames", FRAME_META_COLUMNS)}
    texts: dict[int, dict] = {}
    for item in search_all(port, key, "ocr", start_iso, end_iso):
        c = item.get("content", item)
        fid = c.get("frame_id")
        if fid is not None:
            texts[int(fid)] = c
    frames = []
    for fid in sorted(set(meta) | set(texts)):
        m, t = meta.get(fid, {}), texts.get(fid, {})
        row = {"id": fid, "timestamp": m.get("timestamp") or t.get("timestamp"), "app_name": m.get("app_name") or t.get("app_name"),
               "window_name": m.get("window_name") or t.get("window_name"), "browser_url": m.get("browser_url") or t.get("browser_url"),
               "capture_trigger": m.get("capture_trigger") or t.get("event_source"), "text_source": m.get("text_source") or t.get("text_source"),
               "focused": m.get("focused", t.get("focused")), "device_name": m.get("device_name") or t.get("device_name"),
               "snapshot_path": _relative(m.get("snapshot_path") or t.get("file_path"), session_dir),
               "text": t.get("text") or ""}
        if row["timestamp"] and start_ts - 120 <= parse_timestamp(row["timestamp"]) <= end_ts + 120:
            frames.append(row)

    audio = raw_sql_all(port, key, "audio_chunks", AUDIO_COLUMNS)
    for a in audio:
        a["file_path"] = _relative(a.get("file_path"), session_dir)

    _write_jsonl(out / "ui_events.jsonl", events)
    _write_jsonl(out / "frames.jsonl", frames)
    _write_jsonl(out / "audio_chunks.jsonl", audio)
    stats = summarize(session_dir, frames, events, audio)
    stats["exported_at"] = iso_utc()
    (out / "summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


def api_stats(port: int, key: str | None) -> dict:
    """Counts while the recorder runs. screenpipe opens its index with an exclusive lock, so sqlite reads from another
    process fail until it exits; the API is the only live view. Raises ApiError until the daemon answers."""
    stats = {"frames": 0, "ui_events": {}, "audio_chunks": 0, "audio_files": 0, "apps": [], "domains": [], "typed_chars": 0}
    stats["frames"] = int(raw_sql(port, key, "select count(*) as c from frames limit 1")[0]["c"])
    stats["audio_chunks"] = int(raw_sql(port, key, "select count(*) as c from audio_chunks limit 1")[0]["c"])
    stats["audio_files"] = stats["audio_chunks"]
    stats["ui_events"] = {r["event_type"]: int(r["c"]) for r in raw_sql(port, key, "select event_type, count(*) as c from ui_events group by 1 limit 100")}
    stats["typed_chars"] = int(raw_sql(port, key, "select coalesce(sum(text_length), 0) as c from ui_events where event_type='text' limit 1")[0]["c"] or 0)
    stats["apps"] = [(r["app_name"], int(r["c"])) for r in raw_sql(port, key, "select app_name, count(*) as c from frames where app_name is not null group by 1 order by 2 desc limit 50")]
    return stats


def wait_for_capture(port: int, key: str | None, timeout: float = 120.0, poll: float = 2.0, progress=None) -> dict:
    """Block until the daemon answers and has at least one frame, or time out. `progress(elapsed_s, stats_or_None)` is called each poll."""
    deadline = time.time() + timeout
    t0 = time.time()
    stats = None
    while time.time() < deadline:
        try:
            stats = api_stats(port, key)
        except ApiError:
            stats = None
        if progress:
            progress(time.time() - t0, stats)
        if stats and stats["frames"] > 0:
            return stats
        time.sleep(poll)
    return stats or {"frames": 0, "ui_events": {}, "audio_chunks": 0, "audio_files": 0, "apps": [], "domains": [], "typed_chars": 0}


def stats_from_export(session_dir: Path) -> dict | None:
    out = session_dir / "export"
    if not (out / "frames.jsonl").exists():
        return None
    return summarize(session_dir, load_jsonl(out / "frames.jsonl"), load_jsonl(out / "ui_events.jsonl"), load_jsonl(out / "audio_chunks.jsonl"))
