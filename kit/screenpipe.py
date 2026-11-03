"""Locate, configure, start, and stop the screenpipe recorder; read its database."""
from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

SYSTEM = platform.system()
REQUIRED_PERMISSIONS = ("screen recording", "microphone", "accessibility")


def find_npx() -> str | None:
    return shutil.which("npx") or shutil.which("npx.cmd")


def base_command(cfg: dict) -> list[str]:
    """`screenpipe` on PATH wins (a participant may have the binary); otherwise npx with a pinned version."""
    local = shutil.which("screenpipe")
    if local:
        return [local]
    npx = find_npx()
    if not npx:
        raise RuntimeError("Neither `screenpipe` nor `npx` was found. Install Node.js 18+ from https://nodejs.org and retry.")
    return [npx, "-y", f"screenpipe@{cfg['screenpipe_version']}"]


def build_record_args(cfg: dict, data_dir: Path, port: int, audio: bool = True) -> list[str]:
    args = [
        "record",
        "--data-dir", str(data_dir),
        "--port", str(port),
        "--disable-keyboard-capture", "false",
        "--disable-telemetry",
        "--disable-meeting-detector",
        "--disable-snapshot-compaction",
        "--retention-days", "0",
        "--idle-capture-interval-ms", str(cfg["idle_capture_interval_ms"]),
        "--language", cfg["language"],
    ]
    if audio:
        args += ["--audio-transcription-engine", cfg["audio_transcription_engine"],
                 "--audio-chunk-duration", str(cfg["audio_chunk_seconds"]),
                 "--experimental-meeting-piggyback", "false"]
    else:
        args.append("--disable-audio")
    for name in cfg.get("ignored_windows", []):
        args += ["--ignored-windows", name]
    for domain in cfg.get("url_block", []):
        args += ["--url-block", domain]
    return args


def run_doctor(cfg: dict) -> tuple[bool, str, list[str]]:
    """Run `screenpipe doctor`; return (all required permissions ok, raw text, missing permission names)."""
    try:
        proc = subprocess.run(base_command(cfg) + ["doctor"], capture_output=True, text=True, timeout=180)
        text = (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"could not run screenpipe doctor: {exc}", list(REQUIRED_PERMISSIONS)
    missing = parse_doctor(text)
    return not missing, text, missing


def parse_doctor(text: str) -> list[str]:
    missing = []
    for line in text.splitlines():
        low = line.strip().lower()
        for perm in REQUIRED_PERMISSIONS:
            if low.startswith(perm + ":") and ("missing" in low or "denied" in low or "failed" in low):
                missing.append(perm)
    return missing


def new_api_key() -> str:
    import secrets

    return "kit-" + secrets.token_hex(16)


def start_recorder(cfg: dict, data_dir: Path, port: int, log_path: Path, audio: bool = True,
                   api_key: str | None = None) -> subprocess.Popen:
    """Start screenpipe. `api_key` (per session, never written to disk) lets the kit read the daemon's API later."""
    data_dir.mkdir(parents=True, exist_ok=True)
    cmd = base_command(cfg) + build_record_args(cfg, data_dir, port, audio=audio)
    log = open(log_path, "ab")
    env = dict(os.environ)
    if api_key:
        env["SCREENPIPE_API_KEY"] = api_key
    kwargs = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": env}
    if SYSTEM == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True  # Ctrl+C in the terminal reaches us, not screenpipe
    proc = subprocess.Popen(cmd, **kwargs)
    proc._study_log = log  # type: ignore[attr-defined]
    return proc


def stop_recorder(proc: subprocess.Popen, grace: float = 25.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if SYSTEM == "Windows":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            # npx wraps the native binary; signal the whole session so both stop.
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except Exception:
        proc.terminate()
    deadline = time.time() + grace
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.5)
    if proc.poll() is None:
        try:
            if SYSTEM != "Windows":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            pass
        time.sleep(3)
    if proc.poll() is None:
        proc.kill()
    log = getattr(proc, "_study_log", None)
    if log:
        try:
            log.close()
        except Exception:
            pass


def health(port: int, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def db_path(data_dir: Path) -> Path:
    """screenpipe >= 0.4.5x: storage.json points at storage/<generation>/index.sqlite. Older: db.sqlite."""
    desc = data_dir / "storage.json"
    if desc.exists():
        try:
            rel = json.loads(desc.read_text(encoding="utf-8")).get("index")
            if rel:
                return data_dir / rel
        except (OSError, ValueError):
            pass
    return data_dir / "db.sqlite"


def open_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        return 0


def capture_stats(data_dir: Path) -> dict:
    """Counts and the apps/domains seen, read from the index while screenpipe runs (read-only).
    Text columns may already be archived to parquet, so this is for counts only; export.py has the full rows."""
    stats = {"frames": 0, "ui_events": {}, "audio_chunks": 0, "audio_files": 0, "apps": [], "domains": [], "typed_chars": 0}
    db = db_path(data_dir)
    audio_exts = {".mp4", ".wav", ".m4a", ".mp3", ".ogg", ".flac"}
    stats["audio_files"] = sum(1 for p in data_dir.rglob("*") if p.suffix.lower() in audio_exts)
    if not db.exists():
        return stats
    try:
        conn = open_ro(db)
    except sqlite3.Error:
        return stats
    with conn:
        stats["frames"] = _count(conn, "select count(*) from frames")
        stats["audio_chunks"] = _count(conn, "select count(*) from audio_chunks")
        try:
            rows = conn.execute("select event_type, count(*) c from ui_events group by 1").fetchall()
            stats["ui_events"] = {r["event_type"]: r["c"] for r in rows}
            stats["typed_chars"] = _count(conn, "select coalesce(sum(coalesce(text_length, length(text_content))),0) from ui_events where event_type='text'")
        except sqlite3.Error:
            pass
        try:
            apps = Counter()
            domains = Counter()
            for r in conn.execute("select app_name, browser_url from frames"):
                if r["app_name"]:
                    apps[r["app_name"]] += 1
                url = r["browser_url"] or ""
                if url:
                    host = urlparse(url if "://" in url else "https://" + url).netloc
                    if host:
                        domains[host] += 1
            stats["apps"] = apps.most_common()
            stats["domains"] = domains.most_common(15)
        except sqlite3.Error:
            pass
    conn.close()
    return stats


def wait_for_capture(data_dir: Path, need_audio: bool, timeout: float = 90.0, poll: float = 3.0) -> dict:
    """Block until at least one frame (and, if requested, one audio file) exists, or time out."""
    deadline = time.time() + timeout
    stats = capture_stats(data_dir)
    while time.time() < deadline:
        stats = capture_stats(data_dir)
        if stats["frames"] > 0 and (not need_audio or stats["audio_files"] > 0 or stats["audio_chunks"] > 0):
            break
        time.sleep(poll)
    return stats


def wal_checkpoint(db: Path) -> None:
    """Fold the WAL into the main file so a copied database is complete on its own."""
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(str(db), timeout=10)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error:
        pass


def version_string(cfg: dict) -> str:
    try:
        proc = subprocess.run(base_command(cfg) + ["--version"], capture_output=True, text=True, timeout=120)
        return (proc.stdout or proc.stderr).strip().splitlines()[-1]
    except Exception:
        return "unknown"
