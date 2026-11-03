from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "researcher"))

from kit import prompts, screenpipe  # noqa: E402
from kit.config import DEFAULTS, load_config  # noqa: E402
from kit.package import make_zip  # noqa: E402
from kit.redact import redact_app, redact_range  # noqa: E402
from kit.session import checkpoint_minutes  # noqa: E402
from kit.timeutil import parse_timestamp  # noqa: E402
import export_history  # noqa: E402

T0 = "2026-11-02T15:00:00+00:00"


def make_fake_session(root: Path) -> Path:
    """A session folder with the screenpipe tables the kit touches, three frames, events, and one audio chunk."""
    session = root / "P1_20261102-1000"
    data = session / "screenpipe"
    (data / "data" / "2026-11-02").mkdir(parents=True)
    db = sqlite3.connect(str(data / "db.sqlite"))
    db.executescript(
        """
        create table frames (id integer primary key, timestamp text, app_name text, window_name text, browser_url text,
            capture_trigger text, text_source text, accessibility_text text, full_text text, snapshot_path text);
        create table ui_events (id integer primary key, timestamp text, event_type text, app_name text, window_title text,
            browser_url text, element_role text, element_name text, element_value text, text_content text, key_code int,
            modifiers int, button int, frame_id int, delta_y int);
        create table audio_chunks (id integer primary key, file_path text, timestamp text);
        create table audio_transcriptions (id integer primary key, audio_chunk_id int, timestamp text, transcription text,
            start_time real, end_time real);
        """
    )
    frames = [
        (1, "2026-11-02T15:00:05+00:00", "Google Chrome", "jazz dinner - Google Search", "https://www.google.com/search?q=jazz+dinner",
         "window_focus", "accessibility", "Results  for jazz dinner\n Blue Note", "data/2026-11-02/f1.jpg"),
        (2, "2026-11-02T15:00:20+00:00", "Google Chrome", "jazz dinner - Google Search", "https://www.google.com/search?q=jazz+dinner",
         "idle", "accessibility", "Results for jazz dinner Blue Note", "data/2026-11-02/f2.jpg"),
        (3, "2026-11-02T15:03:00+00:00", "Slack", "#general", "", "app_switch", "accessibility", "hello there", "data/2026-11-02/f3.jpg"),
        (4, "2026-11-02T15:04:00+00:00", "Terminal", "study.py", "", "window_focus", "accessibility", "kit text", "data/2026-11-02/f4.jpg"),
    ]
    for f in frames:
        db.execute("insert into frames values (?,?,?,?,?,?,?,?,?,?)", f[:7] + (f[7], f[7], f[8]))
        (data / f[8]).write_bytes(b"jpg")
    events = [
        (1, "2026-11-02 15:00:04", "window_focus", "Google Chrome", "jazz dinner - Google Search", "", None, None, None, None, None, None, None, 1, None),
        (2, "2026-11-02 15:00:10", "text", "Google Chrome", "jazz dinner - Google Search", "", "AXTextField", "Search", None, "jazz ", None, None, None, 1, None),
        (3, "2026-11-02 15:00:11", "text", "Google Chrome", "jazz dinner - Google Search", "", "AXTextField", "Search", None, "dinner", None, None, None, 1, None),
        (4, "2026-11-02 15:00:12", "key", "Google Chrome", "jazz dinner - Google Search", "", None, None, None, None, 36, 0, None, 1, None),
        (5, "2026-11-02 15:00:30", "click", "Google Chrome", "jazz dinner - Google Search", "https://www.google.com/search?q=jazz+dinner",
         "AXLink", "Blue Note NYC", None, None, None, None, 0, 2, None),
        (6, "2026-11-02 15:00:31", "click", "Google Chrome", "jazz dinner - Google Search", "", "AXGroup", "", None, None, None, None, 0, 2, None),
        (7, "2026-11-02 15:03:01", "scroll", None, None, None, None, None, None, None, None, None, None, 3, -5),
        (8, "2026-11-02 15:03:05", "scroll", None, None, None, None, None, None, None, None, None, None, 3, -5),
        (9, "2026-11-02 15:04:01", "click", "Terminal", "study.py", "", "AXTextArea", "shell", None, None, None, None, 0, 4, None),
    ]
    db.executemany("insert into ui_events values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", events)
    (data / "data" / "2026-11-02" / "a1.mp4").write_bytes(b"audio")
    db.execute("insert into audio_chunks values (1, 'data/2026-11-02/a1.mp4', '2026-11-02T15:00:00+00:00')")
    db.execute("insert into audio_transcriptions values (1, 1, '2026-11-02T15:00:00+00:00', 'I want jazz and dinner', 2.0, 4.0)")
    db.commit()
    db.close()
    (data / "secrets.sqlite").write_bytes(b"secret")
    (data / "pipes").mkdir()
    (data / "pipes" / "x.json").write_text("{}")
    (session / "session.json").write_text(json.dumps({"participant": "P1", "recording_started_at": T0, "created_at": T0}))
    (session / "checkpoints.jsonl").write_text(json.dumps({"kind": "checkpoint", "n": 1, "minute": 10, "at": "2026-11-02T15:10:00+00:00"}) + "\n")
    return session


class UnitTests(unittest.TestCase):
    def test_parse_timestamp_formats(self):
        a = parse_timestamp("2026-11-02T15:00:00+00:00")
        self.assertEqual(parse_timestamp("2026-11-02 15:00:00"), a)
        self.assertEqual(parse_timestamp("2026-11-02T15:00:00Z"), a)
        self.assertAlmostEqual(parse_timestamp("2026-11-02T15:00:00.566921+00:00"), a + 0.566921, places=5)
        self.assertEqual(parse_timestamp("2026-11-02 15:00:00 UTC"), a)

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
        self.assertIn("{every}", prompts.THINK_ALOUD)
        for item in prompts.RETROSPECTIVE:
            self.assertIn(item["kind"], ("text", "int1to5", "list"))


class SessionFolderTests(unittest.TestCase):
    def test_capture_stats(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            stats = screenpipe.capture_stats(session / "screenpipe")
        self.assertEqual(stats["frames"], 4)
        self.assertEqual(stats["ui_events"]["click"], 3)
        self.assertEqual(stats["typed_chars"], len("jazz dinner"))
        self.assertEqual(stats["audio_files"], 1)
        self.assertEqual(stats["apps"][0][0], "Google Chrome")
        self.assertEqual(stats["domains"][0][0], "www.google.com")

    def test_zip_excludes_internals(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            z = make_zip(session)
            names = zipfile.ZipFile(z).namelist()
        self.assertTrue(any(n.endswith("screenpipe/db.sqlite") for n in names))
        self.assertTrue(any(n.endswith("f1.jpg") for n in names))
        self.assertTrue(any(n.endswith("session.json") for n in names))
        self.assertFalse(any("secrets.sqlite" in n for n in names))
        self.assertFalse(any("/pipes/" in n for n in names))

    def test_redact_app_and_range(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
            data = session / "screenpipe"
            removed = redact_app(data, "slack")
            self.assertEqual(removed["frames"], 1)
            self.assertFalse((data / "data/2026-11-02/f3.jpg").exists())
            stats = screenpipe.capture_stats(data)
            self.assertEqual(stats["frames"], 3)
            self.assertNotIn("scroll", stats["ui_events"])  # scroll rows hung off the Slack frame
            removed = redact_range(data, parse_timestamp("2026-11-02T15:00:00+00:00"), parse_timestamp("2026-11-02T15:01:00+00:00"))
            self.assertEqual(removed["frames"], 2)
            self.assertEqual(removed["audio_chunks"], 1)
            self.assertFalse((data / "data/2026-11-02/a1.mp4").exists())
            stats = screenpipe.capture_stats(data)
            self.assertEqual(stats["frames"], 1)
            self.assertEqual(stats["audio_chunks"], 0)

    def test_export_history(self):
        with tempfile.TemporaryDirectory() as d:
            session = make_fake_session(Path(d))
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
