#!/usr/bin/env python3
"""Transcribe a session's audio on the researcher's machine and write derived/transcript.jsonl.

Uses, in order of preference: mlx_whisper (Apple Silicon), faster_whisper, openai-whisper. Install one of them:
  pip install mlx-whisper            # macOS Apple Silicon
  pip install faster-whisper         # any platform (CPU ok)
Audio sources: backup_audio.wav (whole session, preferred when present) or screenpipe's audio chunks (30-s files,
offset by the chunk timestamp in audio_chunks).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kit.timeutil import parse_timestamp  # noqa: E402


def get_backend(model: str):
    try:
        import mlx_whisper  # type: ignore

        repo = {"large-v3-turbo": "mlx-community/whisper-large-v3-turbo", "small": "mlx-community/whisper-small-mlx",
                "medium": "mlx-community/whisper-medium-mlx"}.get(model, model)

        def run(path: str):
            res = mlx_whisper.transcribe(path, path_or_hf_repo=repo, word_timestamps=False)
            return [(s["start"], s["end"], s["text"]) for s in res["segments"]]
        return "mlx_whisper", run
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel  # type: ignore

        wm = WhisperModel(model, device="auto", compute_type="int8")

        def run(path: str):
            segs, _ = wm.transcribe(path, vad_filter=True)
            return [(s.start, s.end, s.text) for s in segs]
        return "faster_whisper", run
    except ImportError:
        pass
    try:
        import whisper  # type: ignore

        wm = whisper.load_model(model)

        def run(path: str):
            res = wm.transcribe(path)
            return [(s["start"], s["end"], s["text"]) for s in res["segments"]]
        return "openai_whisper", run
    except ImportError:
        return None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dir")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--chunks", action="store_true", help="use screenpipe chunks even if backup_audio.wav exists")
    args = ap.parse_args(argv)
    session_dir = Path(args.session_dir)
    sess = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    name, run = get_backend(args.model)
    if run is None:
        print(__doc__)
        return 1
    print(f"backend: {name}, model: {args.model}")
    sources = []
    backup = session_dir / "backup_audio.wav"
    if backup.exists() and not args.chunks:
        sources.append((parse_timestamp(sess["recording_started_at"]), backup))
    else:
        db = session_dir / "screenpipe" / "db.sqlite"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for file_path, ts in conn.execute("select file_path, timestamp from audio_chunks order by timestamp"):
            p = Path(file_path)
            if not p.is_absolute():
                p = session_dir / "screenpipe" / p
            if p.exists() and ts:
                sources.append((parse_timestamp(ts), p))
        conn.close()
    if not sources:
        print("no audio found")
        return 1
    derived = session_dir / "derived"
    derived.mkdir(exist_ok=True)
    n = 0
    with open(derived / "transcript.jsonl", "w", encoding="utf-8") as out:
        for base_ts, path in sources:
            for start, end, text in run(str(path)):
                if text.strip():
                    out.write(json.dumps({"start_ts": base_ts + start, "end_ts": base_ts + end, "text": text, "source": path.name}) + "\n")
                    n += 1
    print(f"wrote {n} segments to {derived / 'transcript.jsonl'}; now run export_history.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
