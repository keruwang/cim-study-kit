"""Delete recorded data for one app or one time range from a session's exported records and media files.

Works on <session>/export/*.jsonl (what the bundle ships) plus the screenshot and audio files they point to.
The recorder's own index and parquet payloads under screenpipe/storage/ are not shipped; they stay on the
participant's computer until the session folder is deleted.
"""
from __future__ import annotations

import json
from pathlib import Path

from .export import load_jsonl, summarize
from .timeutil import parse_timestamp


def _save(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _remove_file(session_dir: Path, rel: str | None) -> int:
    if not rel:
        return 0
    p = Path(rel)
    p = p if p.is_absolute() else session_dir / p
    if p.exists():
        try:
            p.unlink()
            return 1
        except OSError:
            return 0
    return 0


def _apply(session_dir: Path, frame_pred, event_pred, audio_pred) -> dict:
    out = session_dir / "export"
    frames, events, audio = load_jsonl(out / "frames.jsonl"), load_jsonl(out / "ui_events.jsonl"), load_jsonl(out / "audio_chunks.jsonl")
    removed = {"frames": 0, "files": 0, "ui_events": 0, "audio_chunks": 0}
    gone_frames = set()
    keep_frames = []
    for f in frames:
        if frame_pred(f):
            gone_frames.add(f.get("id"))
            removed["frames"] += 1
            removed["files"] += _remove_file(session_dir, f.get("snapshot_path"))
        else:
            keep_frames.append(f)
    keep_events = []
    for e in events:
        if event_pred(e) or (e.get("frame_id") is not None and e.get("frame_id") in gone_frames):
            removed["ui_events"] += 1
        else:
            keep_events.append(e)
    keep_audio = []
    for a in audio:
        if audio_pred(a):
            removed["audio_chunks"] += 1
            removed["files"] += _remove_file(session_dir, a.get("file_path"))
        else:
            keep_audio.append(a)
    _save(out / "frames.jsonl", keep_frames)
    _save(out / "ui_events.jsonl", keep_events)
    _save(out / "audio_chunks.jsonl", keep_audio)
    summary = summarize(session_dir, keep_frames, keep_events, keep_audio)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return removed


def redact_app(session_dir: Path, app_substring: str) -> dict:
    """Remove every screenshot, screen text, and input event whose app or window title contains the text."""
    needle = app_substring.lower()

    def hit(app, title):
        return needle in ((app or "") + " " + (title or "")).lower()

    return _apply(session_dir,
                  lambda f: hit(f.get("app_name"), f.get("window_name")),
                  lambda e: hit(e.get("app_name"), e.get("window_title")),
                  lambda a: False)


def redact_range(session_dir: Path, start_ts: float, end_ts: float) -> dict:
    """Remove screenshots, screen text, input events, and audio files between two epoch timestamps (inclusive)."""
    def within(ts):
        return ts is not None and start_ts <= parse_timestamp(ts) <= end_ts

    return _apply(session_dir,
                  lambda f: within(f.get("timestamp")),
                  lambda e: within(e.get("timestamp")),
                  lambda a: within(a.get("timestamp")))
