"""Microphone level monitor (and backup WAV) through ffmpeg, so the kit knows when the participant has gone quiet.

One ffmpeg process reads the default microphone, writes backup_audio.wav (optional), and prints one RMS level per
second on stdout (astats). A thread parses those lines; `seconds_since_voice()` is what the silence reminder uses.
Nothing is installed: ffmpeg comes from PATH or from screenpipe's own copy (reported by its /health endpoint).
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

SYSTEM = platform.system()
RMS_RE = re.compile(r"RMS_level=(-?[0-9.]+|-inf)")


def find_ffmpeg(port: int | None = None) -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    if port:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                dep = (json.loads(resp.read().decode("utf-8")).get("dependencies") or {}).get("ffmpeg") or {}
                p = dep.get("path")
                if p and Path(p).exists():
                    return p
        except Exception:
            pass
    return None


def default_input_args() -> list[str] | None:
    if SYSTEM == "Darwin":
        return ["-f", "avfoundation", "-i", ":default"]
    if SYSTEM == "Linux":
        return ["-f", "pulse", "-i", "default"]
    if SYSTEM == "Windows":
        return None  # dshow needs a device name; the kit falls back to a fixed-interval reminder there.
    return None


class MicMonitor:
    def __init__(self, ffmpeg: str, session_dir: Path, voice_db: float = -40.0, backup_wav: bool = True,
                 input_args: list[str] | None = None, log_path: Path | None = None):
        self.ffmpeg = ffmpeg
        self.session_dir = session_dir
        self.voice_db = voice_db
        self.backup_wav = backup_wav
        self.input_args = input_args or default_input_args()
        self.log_path = log_path or session_dir / "mic_monitor.log"
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.last_voice_ts: float | None = None
        self.voiced_seconds = 0
        self.level_db: float | None = None
        self.lines = 0
        self._thread: threading.Thread | None = None

    @property
    def wav_path(self) -> Path:
        return self.session_dir / "backup_audio.wav"

    def command(self) -> list[str]:
        meter = "aresample=16000,asetnsamples=n=16000,astats=metadata=1:reset=1,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-"
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error"] + list(self.input_args or [])
        if self.backup_wav:
            cmd += ["-filter_complex", f"[0:a]asplit=2[w][m];[m]{meter}[out]",
                    "-map", "[w]", "-ac", "1", "-ar", "16000", "-y", str(self.wav_path),
                    "-map", "[out]", "-f", "null", "-"]
        else:
            cmd += ["-af", meter, "-f", "null", "-"]
        return cmd

    def start(self) -> bool:
        if not self.input_args:
            return False
        try:
            self.proc = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=open(self.log_path, "ab"), bufsize=1, text=True)
        except Exception:
            self.proc = None
            return False
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return True

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            m = RMS_RE.search(line)
            if not m:
                continue
            self.lines += 1
            val = m.group(1)
            level = float("-inf") if val == "-inf" else float(val)
            self.level_db = level
            if level > self.voice_db:
                self.last_voice_ts = time.time()
                self.voiced_seconds += 1

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def seconds_since_voice(self) -> float | None:
        """None until the monitor has produced a reading; otherwise seconds since the last second with voice."""
        if self.started_at is None or self.lines == 0:
            return None
        return time.time() - (self.last_voice_ts or self.started_at)

    def heard_recently(self, within: float = 3.0) -> bool:
        return self.last_voice_ts is not None and time.time() - self.last_voice_ts <= within

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            self.proc.stdin.write("q")  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    def summary(self) -> dict:
        return {"voiced_seconds": self.voiced_seconds, "readings": self.lines, "voice_threshold_db": self.voice_db,
                "backup_wav": self.backup_wav and self.wav_path.exists()}
