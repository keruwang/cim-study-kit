from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "researcher"))

from kit import env, export, prompts, screenpipe  # noqa: E402
from kit.audio_monitor import MicMonitor, VoiceDetector, find_ffmpeg  # noqa: E402
from kit.session import silence_due  # noqa: E402
from kit.config import DEFAULTS, load_config  # noqa: E402
from kit.package import make_zip  # noqa: E402
from kit.redact import redact_app, redact_range  # noqa: E402
from kit.session import checkpoint_minutes  # noqa: E402
from kit.timeutil import parse_timestamp  # noqa: E402
import export_history  # noqa: E402

T0 = "2026-11-02T15:00:00+00:00"

FRAMES = [
    {"id": 1, "timestamp": "2026-11-02T15:00:05+00:00", "app_name": "Google Chrome", "window_name": "jazz dinner - Google Search",
     "browser_url": "https://www.google.com/search?q=jazz+dinner", "capture_trigger": "window_focus", "text_source": "accessibility",
     "focused": True, "device_name": "monitor_1", "snapshot_path": "screenpipe/data/data/2026-11-02/f1.jpg", "text": "Results  for jazz dinner\n Blue Note"},
    {"id": 2, "timestamp": "2026-11-02T15:00:20+00:00", "app_name": "Google Chrome", "window_name": "jazz dinner - Google Search",
     "browser_url": "https://www.google.com/search?q=jazz+dinner", "capture_trigger": "idle", "text_source": "accessibility",
     "focused": True, "device_name": "monitor_1", "snapshot_path": "screenpipe/data/data/2026-11-02/f2.jpg", "text": "Results for jazz dinner Blue Note"},
    {"id": 3, "timestamp": "2026-11-02T15:03:00+00:00", "app_name": "Slack", "window_name": "#general", "browser_url": "",
     "capture_trigger": "app_switch", "text_source": "accessibility", "focused": True, "device_name": "monitor_1",
     "snapshot_path": "screenpipe/data/data/2026-11-02/f3.jpg", "text": "hello there"},
    {"id": 4, "timestamp": "2026-11-02T15:04:00+00:00", "app_name": "Terminal", "window_name": "study.py", "browser_url": "",
     "capture_trigger": "window_focus", "text_source": "accessibility", "focused": True, "device_name": "monitor_1",
     "snapshot_path": "screenpipe/data/data/2026-11-02/f4.jpg", "text": "kit text"},
]


def ev(i, ts, kind, app=None, title=None, url=None, role=None, name=None, value=None, text=None, key=None, mods=None, button=None, frame=None, dy=None):
    return {"id": i, "timestamp": ts, "session_id": "s", "relative_ms": 0, "event_type": kind, "x": None, "y": None, "delta_x": None,
            "delta_y": dy, "button": button, "click_count": None, "key_code": key, "modifiers": mods, "text_content": text,
            "text_length": len(text) if text else None, "app_name": app, "app_pid": None, "window_title": title, "browser_url": url,
            "element_role": role, "element_name": name, "element_value": value, "element_description": None, "element_bounds": None,
            "frame_id": frame}


EVENTS = [
    ev(1, "2026-11-02 15:00:04", "window_focus", "Google Chrome", "jazz dinner - Google Search", "", frame=1),
    ev(2, "2026-11-02 15:00:10", "text", "Google Chrome", "jazz dinner - Google Search", "", "AXTextField", "Search", text="jazz ", frame=1),
    ev(3, "2026-11-02 15:00:11", "text", "Google Chrome", "jazz dinner - Google Search", "", "AXTextField", "Search", text="dinner", frame=1),
    ev(4, "2026-11-02 15:00:12", "key", "Google Chrome", "jazz dinner - Google Search", "", key=36, mods=0, frame=1),
    ev(5, "2026-11-02 15:00:30", "click", "Google Chrome", "jazz dinner - Google Search", "https://www.google.com/search?q=jazz+dinner",
       "AXLink", "Blue Note NYC", button=0, frame=2),
    ev(6, "2026-11-02 15:00:31", "click", "Google Chrome", "jazz dinner - Google Search", "", "AXGroup", "", button=0, frame=2),
    ev(7, "2026-11-02 15:03:01", "scroll", frame=3, dy=-5),
    ev(8, "2026-11-02 15:03:05", "scroll", frame=3, dy=-5),
    ev(9, "2026-11-02 15:04:01", "click", "Terminal", "study.py", "", "AXTextArea", "shell", button=0, frame=4),
]
AUDIO = [{"id": 1, "file_path": "screenpipe/data/a1.mp4", "timestamp": "2026-11-02T15:00:00+00:00", "transcription_status": "pending"}]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def make_fake_session(root: Path, with_index: bool = True) -> Path:
    """A session folder as the kit leaves it: export/*.jsonl, media files, session.json, and screenpipe internals."""
    session = root / "P1_20261102-1000"
    data = session / "screenpipe"
    (data / "data" / "data" / "2026-11-02").mkdir(parents=True)
    for f in FRAMES:
        (session / f["snapshot_path"]).write_bytes(b"jpg")
    (session / AUDIO[0]["file_path"]).write_bytes(b"audio")
    write_jsonl(session / "export" / "frames.jsonl", FRAMES)
    write_jsonl(session / "export" / "ui_events.jsonl", EVENTS)
    write_jsonl(session / "export" / "audio_chunks.jsonl", AUDIO)
    (session / "export" / "summary.json").write_text(json.dumps(export.summarize(session, FRAMES, EVENTS, AUDIO)))
    if with_index:  # the recorder's own index, as screenpipe 0.4.55 lays it out
        gen = data / "storage" / "gen1"
        gen.mkdir(parents=True)
        (data / "storage.json").write_text(json.dumps({"format": 1, "mode": "hybrid-parquet-v1", "index": "storage/gen1/index.sqlite"}))
        db = sqlite3.connect(str(gen / "index.sqlite"))
        db.executescript("create table frames (id integer primary key, timestamp text, app_name text, browser_url text);"
                         "create table ui_events (id integer primary key, event_type text, text_content text, text_length int);"
                         "create table audio_chunks (id integer primary key, file_path text, timestamp text);")
        db.executemany("insert into frames values (?,?,?,?)", [(f["id"], f["timestamp"], f["app_name"], f["browser_url"]) for f in FRAMES])
        db.executemany("insert into ui_events values (?,?,?,?)", [(e["id"], e["event_type"], None if e["event_type"] == "text" else e["text_content"], e["text_length"]) for e in EVENTS])
        db.execute("insert into audio_chunks values (1, 'x', 'y')")
        db.commit()
        db.close()
        (gen / "payloads").mkdir()
        (gen / "payloads" / "p.parquet").write_bytes(b"parquet")
    (data / "secrets.sqlite").write_bytes(b"secret")
    (data / "pipes").mkdir()
    (data / "pipes" / "x.json").write_text("{}")
    (session / "session.json").write_text(json.dumps({"participant": "P1", "recording_started_at": T0, "created_at": T0}))
    (session / "checkpoints.jsonl").write_text(json.dumps({"kind": "checkpoint", "n": 1, "minute": 10, "at": "2026-11-02T15:10:00+00:00"}) + "\n")
    return session


class FakeDaemon(BaseHTTPRequestHandler):
    """Answers /search (ocr) and /raw_sql the way screenpipe 0.4.55 does, with absolute media paths."""
    base: Path = Path("/")
    key = "kit-test"

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get("Authorization") != f"Bearer {self.key}":
            self._send(403, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self._auth():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/search" and q.get("content_type") == ["ocr"]:
            limit, offset = int(q["limit"][0]), int(q["offset"][0])
            items = [{"type": "OCR", "content": {"frame_id": f["id"], "text": f["text"], "timestamp": f["timestamp"], "file_path": str(self.base / f["snapshot_path"]),
                                                   "app_name": f["app_name"], "window_name": f["window_name"], "browser_url": f["browser_url"],
                                                   "focused": True, "device_name": "monitor_1", "text_source": "accessibility", "event_source": f["capture_trigger"]}}
                     for f in FRAMES]
            self._send(200, {"data": items[offset:offset + limit], "pagination": {"limit": limit, "offset": offset, "total": len(items)}})
        else:
            self._send(200, {"data": [], "pagination": {"total": 0}})

    def do_POST(self):
        if not self._auth():
            return
        n = int(self.headers.get("Content-Length", 0))
        q = json.loads(self.rfile.read(n))["query"].lower()
        if "limit" not in q:
            self._send(400, {"error": "SELECT without LIMIT"})
            return
        last = int(q.split("id >")[1].split()[0]) if "id >" in q else 0
        limit = int(q.rsplit("limit", 1)[1].split()[0])
        if "count(*)" in q and "from frames" in q and "group by" not in q:
            rows = [{"c": len(FRAMES)}]
        elif "count(*)" in q and "from audio_chunks" in q:
            rows = [{"c": len(AUDIO)}]
        elif "event_type, count(*)" in q:
            from collections import Counter
            rows = [{"event_type": k, "c": v} for k, v in Counter(e["event_type"] for e in EVENTS).items()]
        elif "sum(text_length)" in q:
            rows = [{"c": sum(e["text_length"] or 0 for e in EVENTS if e["event_type"] == "text")}]
        elif "app_name, count(*)" in q:
            from collections import Counter
            rows = [{"app_name": k, "c": v} for k, v in Counter(f["app_name"] for f in FRAMES).most_common()]
        elif "_bulk_logical_ui_events" in q:
            rows = [{k: e[k] for k in export.UI_EVENT_COLUMNS} for e in EVENTS if e["id"] > last][:limit]
        elif "from ui_events" in q:
            self._send(500, {"error": "should use the logical view"})
            return
        elif "from frames" in q:
            rows = [{"id": f["id"], "timestamp": f["timestamp"], "app_name": f["app_name"], "window_name": f["window_name"], "browser_url": f["browser_url"],
                     "capture_trigger": f["capture_trigger"], "text_source": "accessibility", "snapshot_path": str(self.base / f["snapshot_path"]),
                     "focused": 1, "device_name": "monitor_1"} for f in FRAMES if f["id"] > last]
        elif "from audio_chunks" in q:
            rows = [{"id": 1, "file_path": str(self.base / AUDIO[0]["file_path"]), "timestamp": AUDIO[0]["timestamp"], "transcription_status": "pending"}]
            rows = [r for r in rows if r["id"] > last]
        else:
            rows = []
        self._send(200, rows)


class UnitTests(unittest.TestCase):
    def test_parse_timestamp_formats(self):
        a = parse_timestamp("2026-11-02T15:00:00+00:00")
        self.assertEqual(parse_timestamp("2026-11-02 15:00:00"), a)
        self.assertEqual(parse_timestamp("2026-11-02T15:00:00Z"), a)
        self.assertAlmostEqual(parse_timestamp("2026-11-02T15:00:00.566921+00:00"), a + 0.566921, places=5)
        self.assertEqual(parse_timestamp("2026-11-02 15:00:00 UTC"), a)
        self.assertEqual(parse_timestamp("2026-11-02T10:00:00-05:00"), a)  # the API answers in local time

    def test_config_defaults_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"session_minutes": 20, "_note": "ignored"}))
            cfg = load_config(p)
        self.assertEqual(cfg["session_minutes"], 20)
        self.assertEqual(cfg["checkpoint_every_minutes"], DEFAULTS["checkpoint_every_minutes"])
        self.assertNotIn("_note", cfg)

    def test_record_args(self):
        cfg = dict(DEFAULTS)
        args = screenpipe.build_record_args(cfg, Path("/tmp/x"), 3031)
        joined = " ".join(args)
        self.assertIn("--disable-keyboard-capture false", joined)
        self.assertIn("--audio-transcription-engine disabled", joined)
        self.assertIn("--ignored-windows 1Password", joined)
        self.assertIn("--retention-days 0", joined)
        self.assertIn("--disable-audio", " ".join(screenpipe.build_record_args(cfg, Path("/tmp/x"), 3031, audio=False)))

    def test_doctor_parse(self):
        text = "permissions:\n  screen recording: ok\n  microphone: missing\n    grant access\n  accessibility: missing (input capture will be disabled)\n  system audio capture: failed\n"
        self.assertEqual(screenpipe.parse_doctor(text), ["microphone", "accessibility"])
        self.assertEqual(screenpipe.parse_doctor("  screen recording: ok\n  microphone: ok\n  accessibility: ok\n"), [])

    def test_checkpoints(self):
        self.assertEqual(checkpoint_minutes(50, 10), [10, 20, 30, 40])
        self.assertEqual(checkpoint_minutes(10, 10), [])

    def test_tasks_and_prompts(self):
        tasks = prompts.load_tasks()
        codes = [f["code"] for f in tasks["families"]]
        self.assertEqual(codes, ["A", "B", "C", "D", "E"])
        for f in tasks["families"]:
            self.assertEqual(set(f["axes"]), {"fuzziness", "predictability", "expertise"})
            self.assertTrue(f["examples"])
        self.assertIn("{silence}", prompts.THINK_ALOUD)
        self.assertIn("{every}", prompts.CHECKPOINTS_OPTIONAL)
        self.assertEqual(DEFAULTS["checkpoint_every_minutes"], 0)
        self.assertNotIn("terminal", prompts.END_SPOKEN.lower())
        for item in prompts.RETROSPECTIVE:
            self.assertIn(item["kind"], ("text", "int1to5", "list"))

    def test_silence_due_logic(self):
        class Mon:
            def __init__(self, since): self.since = since
            def alive(self): return True
            def seconds_since_voice(self): return self.since
        cfg = {"silence_reminder_minutes": 5, "silence_fallback_reminder_minutes": 10}
        self.assertIsNone(silence_due(cfg, Mon(100), 100, None))
        self.assertEqual(silence_due(cfg, Mon(301), 400, None), 301)
        self.assertIsNone(silence_due(cfg, Mon(301), 400, 200))      # reminded 200 s ago: wait
        self.assertEqual(silence_due(cfg, Mon(700), 800, 400), 700)  # still quiet 5 min after the last reminder
        self.assertIsNone(silence_due(cfg, Mon(None), 800, None))    # no reading yet
        self.assertIsNone(silence_due(cfg, None, 599, None))         # fallback: fixed interval without a mic
        self.assertEqual(silence_due(cfg, None, 601, None), 601)
        self.assertIsNone(silence_due(cfg, None, 900, 601))
        self.assertIsNone(silence_due({"silence_reminder_minutes": 0, "silence_fallback_reminder_minutes": 0}, None, 5000, None))

    def test_voice_detector_rules(self):
        det = VoiceDetector(margin_db=8, min_zcr=0.10)
        quiet = [-55.0] * 20
        for _ in range(5):
            self.assertFalse(det.push_second(quiet, [0.05] * 20))  # room noise settles the floor near -55
        speech = [-35.0] * 20
        self.assertTrue(det.push_second(speech, [0.15] * 20))      # loud, sustained, consonant-rich
        self.assertFalse(det.push_second(speech, [0.04] * 20))     # loud and sustained but a low thud: keystrokes
        self.assertFalse(det.push_second([-35.0] * 4 + [-55.0] * 16, [0.15] * 20))  # one loud click in a quiet second
        self.assertFalse(det.push_second([-50.0] * 20, [0.2] * 20))  # too close to the floor
        self.assertEqual(det.voiced_seconds, 1)
        # the clock resets only on two voice seconds out of three
        det.push_second(quiet, [0.05] * 20)
        before = det.last_voice_second
        det.push_second(speech, [0.15] * 20)
        self.assertEqual(det.last_voice_second, before)
        det.push_second(speech, [0.15] * 20)
        self.assertEqual(det.last_voice_second, det.seconds)
        self.assertGreaterEqual(det.bars(), 3)

    def test_mic_monitor_on_synthetic_audio(self):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.skipTest("ffmpeg not installed")
        with tempfile.TemporaryDirectory() as d:
            # 1200 Hz tone: zero-crossing rate 0.15 at 16 kHz, so it passes the speech rule
            mon = MicMonitor(ffmpeg, Path(d), backup_wav=True, input_args=["-f", "lavfi", "-i", "sine=frequency=1200:duration=3"])
            self.assertTrue(mon.start())
            mon.proc.wait(timeout=20)
            mon._thread.join(timeout=5)
            # the adaptive floor catches up with a constant tone after ~3 s, so only the first seconds count
            self.assertGreaterEqual(mon.voiced_seconds, 2)
            self.assertIsNotNone(mon.last_voice_ts)
            self.assertGreaterEqual(mon.lines, 2)
            self.assertTrue(mon.wav_path.exists() and mon.wav_path.stat().st_size > 1000)
            quiet = MicMonitor(ffmpeg, Path(d), backup_wav=False, input_args=["-f", "lavfi", "-i", "anullsrc=duration=2"])
            self.assertTrue(quiet.start())
            quiet.proc.wait(timeout=20)
            quiet._thread.join(timeout=5)
            self.assertEqual(quiet.voiced_seconds, 0)
            self.assertGreaterEqual(quiet.lines, 1)
            self.assertIsNotNone(quiet.seconds_since_voice())

    def test_tk_check(self):
        ok, detail = env.tk_check()
        if not ok and "required" in detail:
            self.skipTest("this interpreter's Tk is the broken Apple one; the check reported it: " + detail)
        self.assertTrue(ok, detail)
        self.assertTrue(detail.startswith("Tk "))
        self.assertIn("python.org", env.tk_advice())

    def test_api_key_env(self):
        self.assertTrue(screenpipe.new_api_key().startswith("kit-"))
        self.assertNotEqual(screenpipe.new_api_key(), screenpipe.new_api_key())


class SessionFolderTests(unittest.TestCase):
    def test_db_path_and_capture_stats_on_hybrid_index(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            data = session / "screenpipe"
            self.assertEqual(screenpipe.db_path(data), data / "storage" / "gen1" / "index.sqlite")
            stats = screenpipe.capture_stats(data)
            self.assertEqual(stats["frames"], 4)
            self.assertEqual(stats["ui_events"]["click"], 3)
            self.assertEqual(stats["typed_chars"], len("jazz dinner"))  # text_content archived; text_length still counts
            self.assertEqual(stats["audio_files"], 1)
            self.assertEqual(stats["apps"][0][0], "Google Chrome")
            self.assertEqual(stats["domains"][0][0], "www.google.com")
            self.assertEqual(screenpipe.db_path(Path(d) / "nothing"), Path(d) / "nothing" / "db.sqlite")

    def test_stats_from_export(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d), with_index=False)
            s = export.stats_from_export(session)
        self.assertEqual(s["frames"], 4)
        self.assertEqual(s["ui_events"]["scroll"], 2)
        self.assertEqual(s["typed_chars"], len("jazz dinner"))
        self.assertEqual(s["audio_files"], 1)

    def test_export_session_via_fake_daemon(self):
        with tempfile.TemporaryDirectory() as d:
            session = Path(d) / "P2_x"
            (session / "screenpipe" / "data" / "data" / "2026-11-02").mkdir(parents=True)
            for f in FRAMES:
                (session / f["snapshot_path"]).write_bytes(b"jpg")
            (session / AUDIO[0]["file_path"]).write_bytes(b"audio")
            FakeDaemon.base = session
            srv = HTTPServer(("127.0.0.1", 0), FakeDaemon)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                with self.assertRaises(export.ApiError):
                    export.export_session(srv.server_port, "wrong-key", session, parse_timestamp(T0), parse_timestamp(T0) + 600)
                stats = export.export_session(srv.server_port, FakeDaemon.key, session, parse_timestamp(T0), parse_timestamp(T0) + 600)
                paged = export.raw_sql_all(srv.server_port, FakeDaemon.key, "_bulk_logical_ui_events", export.UI_EVENT_COLUMNS, page=2)
                live = export.api_stats(srv.server_port, FakeDaemon.key)
                seen = []
                waited = export.wait_for_capture(srv.server_port, FakeDaemon.key, timeout=5, poll=0.1, progress=lambda t, st: seen.append(st))
                nobody = export.wait_for_capture(1, FakeDaemon.key, timeout=0.3, poll=0.1)  # port 1: nothing listening
            finally:
                srv.shutdown()
            events = export.load_jsonl(session / "export" / "ui_events.jsonl")
            frames = export.load_jsonl(session / "export" / "frames.jsonl")
            audio = export.load_jsonl(session / "export" / "audio_chunks.jsonl")
        self.assertEqual([e["id"] for e in events], list(range(1, 10)))
        self.assertEqual([e["id"] for e in paged], list(range(1, 10)))  # paged 2 at a time, nothing lost or doubled
        self.assertEqual(events[1]["text_content"], "jazz ")
        self.assertEqual(len(frames), 4)
        self.assertEqual(frames[0]["snapshot_path"], "screenpipe/data/data/2026-11-02/f1.jpg")  # made relative
        self.assertEqual(frames[0]["text"], FRAMES[0]["text"])
        self.assertEqual(frames[0]["capture_trigger"], "window_focus")
        self.assertEqual(audio[0]["file_path"], "screenpipe/data/a1.mp4")
        self.assertEqual(stats["frames"], 4)
        self.assertEqual(stats["audio_files"], 1)
        self.assertEqual(stats["typed_chars"], len("jazz dinner"))
        self.assertEqual(live["frames"], 4)
        self.assertEqual(live["ui_events"]["click"], 3)
        self.assertEqual(live["typed_chars"], len("jazz dinner"))
        self.assertEqual(live["apps"][0], ("Google Chrome", 2))
        self.assertEqual(waited["frames"], 4)
        self.assertTrue(seen and seen[-1] is not None)
        self.assertEqual(nobody["frames"], 0)

    def test_zip_ships_export_and_media_not_internals(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            z = make_zip(session)
            names = zipfile.ZipFile(z).namelist()
        self.assertTrue(any(n.endswith("export/ui_events.jsonl") for n in names))
        self.assertTrue(any(n.endswith("export/frames.jsonl") for n in names))
        self.assertTrue(any(n.endswith("f1.jpg") for n in names))
        self.assertTrue(any(n.endswith("a1.mp4") for n in names))
        self.assertTrue(any(n.endswith("session.json") for n in names))
        self.assertFalse(any("secrets.sqlite" in n for n in names))
        self.assertFalse(any("/pipes/" in n for n in names))
        self.assertFalse(any("/storage/" in n or n.endswith("storage.json") for n in names))  # index + parquet stay local

    def test_redact_app_and_range(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            removed = redact_app(session, "slack")
            self.assertEqual(removed["frames"], 1)
            self.assertFalse((session / "screenpipe/data/data/2026-11-02/f3.jpg").exists())
            stats = export.stats_from_export(session)
            self.assertEqual(stats["frames"], 3)
            self.assertNotIn("scroll", stats["ui_events"])  # scroll rows hung off the Slack frame
            removed = redact_range(session, parse_timestamp("2026-11-02T15:00:00+00:00"), parse_timestamp("2026-11-02T15:01:00+00:00"))
            self.assertEqual(removed["frames"], 2)
            self.assertEqual(removed["audio_chunks"], 1)
            self.assertEqual(removed["ui_events"], 6)
            self.assertFalse((session / "screenpipe/data/a1.mp4").exists())
            stats = export.stats_from_export(session)
            self.assertEqual(stats["frames"], 1)
            self.assertEqual(stats["audio_chunks"], 0)
            self.assertEqual(json.loads((session / "export" / "summary.json").read_text())["frames"], 1)

    def test_export_history(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            (session / "derived").mkdir()
            (session / "derived" / "transcript.jsonl").write_text(json.dumps({"start_ts": parse_timestamp(T0) + 2, "end_ts": parse_timestamp(T0) + 4,
                                                                             "text": "I want jazz and dinner", "source": "backup_audio.wav"}) + "\n")
            rc = export_history.main([str(session)])
            self.assertEqual(rc, 0)
            derived = session / "derived"
            events = (derived / "history_events.txt").read_text()
            both = (derived / "history_events_views.txt").read_text()
            talk = (derived / "think_aloud.txt").read_text()
            cps = (derived / "checkpoints.txt").read_text()
        self.assertIn('[typed] Google Chrome: "jazz dinner"', events)
        self.assertIn("[key] Google Chrome: Enter", events)
        self.assertIn('[click] Google Chrome: link "Blue Note NYC" url=https://www.google.com/search?q=jazz+dinner', events)
        self.assertNotIn('""', events)  # unlabeled group click dropped
        self.assertEqual(events.count("[scroll] Slack"), 1)  # collapsed
        self.assertNotIn("Terminal", events)  # kit's own window excluded
        self.assertNotIn("[view]", events)
        self.assertEqual(both.count("[view] Google Chrome"), 1)  # idle re-capture of same page deduplicated
        self.assertIn(":: Results for jazz dinner Blue Note", both)
        self.assertIn("I want jazz and dinner", talk)
        self.assertNotIn("jazz and dinner", events)  # transcript never in the model history
        self.assertIn("[checkpoint] minute 10", cps)


if __name__ == "__main__":
    unittest.main()
