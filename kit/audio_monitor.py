"""Microphone monitor (and backup WAV) through ffmpeg, so the kit knows when the participant has gone quiet.

One ffmpeg process reads the default microphone, writes backup_audio.wav (optional), and prints the RMS level of every
50-millisecond slice on stdout (astats). `VoiceDetector` turns those slices into one decision per second:

  * noise floor  = 10th percentile of slice levels over the last 60 s (adapts to the room and the microphone)
  * a second is "voice" when its mean level is `margin_db` above the floor, at least half of its slices are above
    the floor + 6 dB (speech fills the second; a keystroke is one or two loud slices), and the zero-crossing rate of
    the loud slices is at least `min_zcr` (on a laptop microphone, speech crosses zero ~0.1-0.3 of samples because
    of its consonants; keystrokes and desk bumps are low thuds at ~0.03-0.09; measured on a 2-minute test session)
  * the silence clock resets only when two of the last three seconds are voice (speech comes in runs)

`seconds_since_voice()` is what the silence reminder uses. Nothing is installed: ffmpeg comes from PATH or from
screenpipe's own copy (reported by its /health endpoint).
"""
from __future__ import annotations

import json
import math
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

SYSTEM = platform.system()
RMS_RE = re.compile(r"RMS_level=(-?[0-9.]+|-inf)")
ZCR_RE = re.compile(r"Zero_crossings_rate=([0-9.]+)")
FRAME_RE = re.compile(r"^frame:")
SLICES_PER_SECOND = 20  # 50 ms at 16 kHz = 800 samples


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
    return None  # Windows dshow needs a device name; the kit falls back to a fixed-interval reminder there.


def _mean_db(levels: list[float]) -> float:
    powers = [10 ** (d / 10) for d in levels if d > -200]
    return 10 * math.log10(sum(powers) / len(levels)) if powers else -99.0


class VoiceDetector:
    """Per-second voice decisions from 50-ms slice levels (dBFS). Pure, so it can be tested on a WAV."""

    def __init__(self, margin_db: float = 8.0, min_zcr: float = 0.10, active_fraction: float = 0.5,
                 floor_db: float | None = None, floor_window_s: int = 60):
        self.margin_db = margin_db
        self.min_zcr = min_zcr
        self.active_fraction = active_fraction
        self.fixed_floor = floor_db
        self.history: deque = deque(maxlen=floor_window_s * SLICES_PER_SECOND)
        self.recent: deque = deque(maxlen=3)
        self.seconds = 0
        self.voiced_seconds = 0
        self.level_db: float | None = None
        self.zcr = 0.0
        self.floor_db: float | None = floor_db
        self.last_voice_second: int | None = None  # index of the last second that reset the clock

    def floor(self) -> float:
        if self.fixed_floor is not None:
            return self.fixed_floor
        if len(self.history) < SLICES_PER_SECOND * 3:
            return -60.0
        vals = sorted(self.history)
        return vals[len(vals) // 10]

    def push_second(self, slices: list[float], zcrs: list[float] | None = None) -> bool:
        """Feed one second of slice levels (dBFS) and zero-crossing rates; returns True if it counts as voice."""
        slices = [(-99.0 if d is None or d < -120 else d) for d in slices]
        zcrs = list(zcrs) if zcrs is not None else [self.min_zcr] * len(slices)
        self.history.extend(slices)
        self.seconds += 1
        fl = self.floor()
        self.floor_db = fl
        level = _mean_db(slices)
        self.level_db = level
        loud = [(d, z) for d, z in zip(slices, zcrs) if d > fl + 6]
        active = len(loud) / max(1, len(slices))
        zcr = sum(z for _, z in loud) / len(loud) if loud else 0.0
        self.zcr = zcr
        voice = level > fl + self.margin_db and active >= self.active_fraction and zcr >= self.min_zcr
        self.recent.append(voice)
        if voice:
            self.voiced_seconds += 1
        if sum(self.recent) >= 2:
            self.last_voice_second = self.seconds
        return voice

    def bars(self) -> int:
        """0-4: how far the current second is above the floor, for the little meter on the timer."""
        if self.level_db is None or self.floor_db is None:
            return 0
        return max(0, min(4, int((self.level_db - self.floor_db) / 5)))


class MicMonitor:
    def __init__(self, ffmpeg: str, session_dir: Path, detector: VoiceDetector | None = None, backup_wav: bool = True,
                 input_args: list[str] | None = None, log_path: Path | None = None):
        self.ffmpeg = ffmpeg
        self.session_dir = session_dir
        self.det = detector or VoiceDetector()
        self.backup_wav = backup_wav
        self.input_args = input_args or default_input_args()
        self.log_path = log_path or session_dir / "mic_monitor.log"
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.last_voice_ts: float | None = None
        self.lines = 0
        self._pending: list[float] = []
        self._pending_zcr: list[float] = []
        self._cur: dict = {}
        self._thread: threading.Thread | None = None

    # compatibility with the old attribute names used by the flows
    @property
    def voiced_seconds(self) -> int:
        return self.det.voiced_seconds

    @property
    def level_db(self) -> float | None:
        return self.det.level_db

    @property
    def wav_path(self) -> Path:
        return self.session_dir / "backup_audio.wav"

    def command(self) -> list[str]:
        meter = "aresample=16000,aformat=channel_layouts=mono,asetnsamples=n=800,astats=metadata=1:reset=1,ametadata=mode=print:file=-"
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
            if FRAME_RE.match(line):
                self._flush_slice()
                continue
            m = RMS_RE.search(line)
            if m and ".1." in line or (m and "Overall" in line and "rms" not in self._cur):
                val = m.group(1)
                self._cur["rms"] = -99.0 if val == "-inf" else float(val)
                continue
            z = ZCR_RE.search(line)
            if z and ".1." in line:
                self._cur["zcr"] = float(z.group(1))
        self._flush_slice()

    def _flush_slice(self) -> None:
        if "rms" not in self._cur:
            self._cur = {}
            return
        self._pending.append(self._cur["rms"])
        self._pending_zcr.append(self._cur.get("zcr", self.det.min_zcr))
        self._cur = {}
        if len(self._pending) >= SLICES_PER_SECOND:
            self.lines += 1
            self.det.push_second(self._pending, self._pending_zcr)
            self._pending, self._pending_zcr = [], []
            if self.det.last_voice_second == self.det.seconds:
                self.last_voice_ts = time.time()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def seconds_since_voice(self) -> float | None:
        """None until the monitor has produced a reading; otherwise seconds since the last voiced stretch."""
        if self.started_at is None or self.lines == 0:
            return None
        return time.time() - (self.last_voice_ts or self.started_at)

    def heard_recently(self, within: float = 3.0) -> bool:
        return self.last_voice_ts is not None and time.time() - self.last_voice_ts <= within

    def bars(self) -> int:
        return self.det.bars()

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
        return {"voiced_seconds": self.det.voiced_seconds, "readings": self.lines, "noise_floor_db": self.det.floor_db,
                "margin_db": self.det.margin_db, "min_zcr": self.det.min_zcr,
                "backup_wav": self.backup_wav and self.wav_path.exists()}


def wav_slices(path: Path):
    """Yield (slice levels, slice zero-crossing rates) per second from a 16-bit mono WAV (offline checks and tests)."""
    import array
    import wave

    w = wave.open(str(path))
    sr, n = w.getframerate(), w.getnframes()
    a = array.array("h", w.readframes(n))
    step = sr // SLICES_PER_SECOND
    for s in range(n // sr):
        sec = a[s * sr:(s + 1) * sr]
        out, zc = [], []
        for i in range(0, len(sec) - step + 1, step):
            sl = sec[i:i + step]
            rms = math.sqrt(sum(x * x for x in sl) / len(sl)) / 32768.0
            out.append(20 * math.log10(rms) if rms > 0 else -99.0)
            zc.append(sum(1 for j in range(1, len(sl)) if (sl[j - 1] < 0) != (sl[j] < 0)) / len(sl))
        yield out, zc
