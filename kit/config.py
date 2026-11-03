from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "study_config.json"

DEFAULTS = {
    "study_name": "Open-ended computer work study",
    "contact": "the study team (see README)",
    # Where participants upload the zipped session (a shared Google Drive folder, a Dropbox file request, ...).
    "upload_url": "",
    # Timing (minutes).
    "session_minutes": 50,
    "checkpoint_every_minutes": 10,
    "nudge_at_minute": 25,
    "wrap_up_minutes": 2,
    # screenpipe.
    "screenpipe_version": "0.4.55",
    "screenpipe_port": 3031,
    # "disabled" = record the microphone but do not transcribe on the participant's machine
    # (no model download, less CPU). We transcribe on our side. "whisper-tiny" gives a rough live transcript.
    "audio_transcription_engine": "disabled",
    "audio_chunk_seconds": 30,
    "idle_capture_interval_ms": 15000,
    "language": "english",
    # Apps never recorded (case-insensitive substring match on app or window title).
    "ignored_windows": ["1Password", "Bitwarden", "Keychain Access", "LastPass", "Dashlane", "KeePass", "Passwords"],
    # Browser domains never recorded. Append ":subdomains" to include descendants.
    "url_block": [],
    # If ffmpeg is on PATH, also record the microphone to a plain WAV as a backup channel.
    "backup_audio": True,
    "sessions_dir": "sessions",
}


def load_config(path: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    path = path or CONFIG_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    env_url = os.environ.get("STUDY_UPLOAD_URL")
    if env_url:
        cfg["upload_url"] = env_url
    return cfg


def sessions_root(cfg: dict) -> Path:
    p = Path(cfg["sessions_dir"])
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p
