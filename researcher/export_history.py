#!/usr/bin/env python3
"""Turn one session bundle into the text histories a model reads, plus the think-aloud kept separate.

Outputs (in <session>/derived/):
  history_events.txt        what the user did (clicks, typing, app switches, scrolls)      = the ablation baseline
  history_events_views.txt  the same, plus what was on screen at focus/scroll/idle captures
  think_aloud.txt           transcript lines with clock times (from screenpipe or transcript.jsonl); never shown to the model
  checkpoints.txt           the cue times, to align model probes with the participant's checkpoint sentences
  summary.json              counts
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kit.timeutil import local_clock, parse_timestamp  # noqa: E402

VIEW_TRIGGERS = {"window_focus", "app_switch", "scroll_stop", "idle", "typing_pause", "manual", "visual_change"}
MAC_KEYS = {36: "return", 48: "tab", 51: "delete", 53: "escape", 76: "enter", 123: "left", 124: "right", 125: "down", 126: "up", 49: "space"}


def clean(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url if "://" in url else "https://" + url).netloc
    except ValueError:
        return ""


def load_rows(db: Path):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    frames = [dict(r) for r in conn.execute(
        "select id, timestamp, app_name, window_name, browser_url, capture_trigger, text_source, "
        "coalesce(accessibility_text, full_text, '') as text from frames order by timestamp")]
    events = [dict(r) for r in conn.execute(
        "select id, timestamp, event_type, app_name, window_title, browser_url, element_role, element_name, element_value, "
        "text_content, key_code, modifiers, button, frame_id, delta_y from ui_events order by timestamp, id")]
    transcript = []
    try:
        transcript = [dict(r) for r in conn.execute(
            "select t.timestamp, t.transcription, t.start_time, t.end_time, c.timestamp as chunk_ts "
            "from audio_transcriptions t left join audio_chunks c on c.id = t.audio_chunk_id order by t.timestamp")]
    except sqlite3.Error:
        pass
    conn.close()
    return frames, events, transcript


def event_lines(events: list[dict], frames_by_id: dict, exclude: list[str], t0: float, tz_offset: float) -> list[tuple[float, str]]:
    out = []
    typed_buffer = []

    def flush():
        if typed_buffer:
            ts, app, text = typed_buffer[0][0], typed_buffer[0][1], "".join(t for _, _, t in typed_buffer)
            out.append((ts, f"[typed] {app}: \"{clean(text, 200)}\""))
            typed_buffer.clear()

    for e in events:
        ts = parse_timestamp(e["timestamp"])
        if ts < t0:
            continue
        frame = frames_by_id.get(e.get("frame_id")) or {}
        app = e.get("app_name") or frame.get("app_name") or "?"
        title = e.get("window_title") or frame.get("window_name") or ""
        url = e.get("browser_url") or frame.get("browser_url") or ""
        if any(x.lower() in (app + " " + title).lower() for x in exclude):
            continue
        kind = e["event_type"]
        if kind == "text":
            typed_buffer.append((ts, app, e.get("text_content") or ""))
            continue
        flush()
        if kind in ("app_switch", "window_focus"):
            out.append((ts, f"[switch] {app}: {clean(title, 100)}" + (f" ({host_of(url)})" if url else "")))
        elif kind == "click":
            if e.get("button") not in (None, 0):
                continue
            label = e.get("element_name") or e.get("element_value") or ""
            role = (e.get("element_role") or "").replace("AX", "").lower() or "element"
            if not label and role in ("group", "generic", "element", "window", "unknown"):
                continue
            out.append((ts, f"[click] {app}: {role} \"{clean(label, 80)}\"" + (f" url={url}" if url else "")))
        elif kind == "key":
            name = MAC_KEYS.get(e.get("key_code") or -1)
            mods = e.get("modifiers") or 0
            if name == "return" or name == "enter":
                out.append((ts, f"[key] {app}: Enter"))
            elif mods and name is None:
                out.append((ts, f"[key] {app}: shortcut (modifiers={mods}, code={e.get('key_code')})"))
        elif kind == "scroll":
            out.append((ts, f"[scroll] {app}: {clean(title, 80)}"))
        elif kind == "clipboard":
            out.append((ts, f"[clipboard] {app}"))
    flush()
    # Collapse runs of identical scroll lines within 20 s.
    collapsed = []
    for ts, line in out:
        if collapsed and line.startswith("[scroll]") and collapsed[-1][1] == line and ts - collapsed[-1][0] < 20:
            continue
        collapsed.append((ts, line))
    return collapsed


def view_lines(frames: list[dict], exclude: list[str], t0: float, excerpt: int, dedup_s: float) -> list[tuple[float, str]]:
    out = []
    last = {}
    for f in frames:
        ts = parse_timestamp(f["timestamp"])
        if ts < t0 or (f.get("capture_trigger") or "") not in VIEW_TRIGGERS:
            continue
        app = f.get("app_name") or "?"
        title = f.get("window_name") or ""
        if any(x.lower() in (app + " " + title).lower() for x in exclude):
            continue
        url = f.get("browser_url") or ""
        key = (app, title, url)
        if key in last and ts - last[key] < dedup_s:
            continue
        last[key] = ts
        text = clean(f.get("text") or "", excerpt)
        out.append((ts, f"[view] {app}: {clean(title, 100)}" + (f" url={url}" if url else "") + (f" :: {text}" if text else "")))
    return out


def transcript_lines(session_dir: Path, transcript_rows: list[dict], t0: float) -> list[tuple[float, str]]:
    out = []
    external = session_dir / "derived" / "transcript.jsonl"
    if external.exists():
        for line in external.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.append((float(d["start_ts"]), d["text"].strip()))
        return sorted(out)
    for r in transcript_rows:
        base = parse_timestamp(r.get("chunk_ts") or r["timestamp"])
        ts = base + float(r.get("start_time") or 0.0)
        if ts >= t0 and (r.get("transcription") or "").strip():
            out.append((ts, r["transcription"].strip()))
    return sorted(out)


def write_lines(path: Path, lines: list[tuple[float, str]], t0: float) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for ts, line in sorted(lines, key=lambda x: x[0]):
            rel = int(ts - t0)
            fh.write(f"{local_clock(ts)} (+{rel // 60:02d}:{rel % 60:02d}) {line}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dir")
    ap.add_argument("--exclude", action="append", default=["Terminal", "iTerm", "study.py", "cim-study-kit", "Windows PowerShell"],
                    help="app or title substrings to drop (the kit's own terminal is dropped by default)")
    ap.add_argument("--excerpt", type=int, default=300, help="characters of screen text per view line")
    ap.add_argument("--dedup-seconds", type=float, default=60.0)
    args = ap.parse_args(argv)
    session_dir = Path(args.session_dir)
    db = session_dir / "screenpipe" / "db.sqlite"
    if not db.exists():
        print(f"no db at {db}")
        return 1
    sess = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    t0 = parse_timestamp(sess.get("recording_started_at") or sess.get("created_at"))
    frames, events, transcript = load_rows(db)
    frames_by_id = {f["id"]: f for f in frames}
    ev = event_lines(events, frames_by_id, args.exclude, t0, 0.0)
    views = view_lines(frames, args.exclude, t0, args.excerpt, args.dedup_seconds)
    derived = session_dir / "derived"
    derived.mkdir(exist_ok=True)
    write_lines(derived / "history_events.txt", ev, t0)
    write_lines(derived / "history_events_views.txt", ev + views, t0)
    write_lines(derived / "think_aloud.txt", transcript_lines(session_dir, transcript, t0), t0)
    cps = []
    cp_path = session_dir / "checkpoints.jsonl"
    if cp_path.exists():
        for line in cp_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                cps.append((parse_timestamp(d["at"]), f"[{d['kind']}] minute {d.get('minute', '?')}"))
    write_lines(derived / "checkpoints.txt", cps, t0)
    summary = {"frames": len(frames), "events": len(events), "event_lines": len(ev), "view_lines": len(views),
               "transcript_segments": len(transcript), "approx_tokens_events": sum(len(l) for _, l in ev) // 4,
               "approx_tokens_events_views": sum(len(l) for _, l in ev + views) // 4}
    (derived / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
