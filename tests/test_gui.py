"""Drives the window through every page in demo mode (no recorder, 5-second minutes) without a human."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit import gui, notify  # noqa: E402
from kit.config import DEFAULTS  # noqa: E402


def pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.05)


class GuiDemoFlow(unittest.TestCase):
    def test_default_schedule_has_no_checkpoints(self):
        try:
            import tkinter
            tkinter.Tk().destroy()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"no display: {exc}")
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(DEFAULTS, sessions_dir=d)
            args = types.SimpleNamespace(participant="T1", family="A", minutes=None, no_nudge=True, checkpoint_every=None, skip_checks=True)
            app = gui.StudyApp(cfg, args, demo=True)
            self.assertEqual(app.schedule(), (50, 0, []))
            app.root.destroy()

    def test_full_flow(self):
        try:
            import tkinter
            tkinter.Tk().destroy()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"no display: {exc}")
        notify.cue = lambda *a, **k: None  # silence
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(DEFAULTS, sessions_dir=d, wrap_up_minutes=1, nudge_at_minute=1, upload_url="", silence_reminder_minutes=1)
            args = types.SimpleNamespace(participant="T1", family="C", minutes=3, no_nudge=False, checkpoint_every=1, skip_checks=True)
            gui.reveal = lambda p: None
            gui.open_url = lambda u: None
            app = gui.StudyApp(cfg, args, demo=True)
            app.minute_s = 1  # one "minute" = one second for the test
            app.agree.set(True)
            app.on_agree()
            app.side_box.insert("1.0", "reply to a message")
            app.on_details()
            self.assertEqual(app.session["family"], "C")
            self.assertTrue(app.session["nudge_enabled"])
            app.show_instructions()
            app.start_recording()
            pump(app, 2.0)  # recorder thread (demo) -> started -> recording pill
            self.assertIsNotNone(app.start_ts)
            self.assertTrue(app.root.geometry().startswith(f"{gui.PILL_W}x"))  # shrunk to the corner pill
            pump(app, 4.5)  # 3 "minutes" elapse: checkpoints at 1, 2; nudge at 1; wrap at 2; silence reminder (demo mic hears nothing); end at 3
            self.assertIsNone(app.start_ts)
            pump(app, 1.5)  # stop thread -> retrospective
            lines = [json.loads(l) for l in (app.session_dir / "checkpoints.jsonl").read_text().splitlines()]
            kinds = [l["kind"] for l in lines]
            self.assertEqual(kinds.count("checkpoint"), 2)
            self.assertGreaterEqual(kinds.count("silence"), 1)
            self.assertLessEqual(kinds.count("silence"), 3)  # at most once per silence interval (1 "minute" here)
            self.assertIn("nudge", kinds)
            self.assertTrue(app.root.geometry().startswith("520x"))  # full window is back for the questions
            self.assertIn("wrap", kinds)
            self.assertEqual(kinds[-1], "end")
            # retrospective: answer every question
            n = 0
            while app.retro_index < len(gui.prompts.RETROSPECTIVE):
                item = gui.prompts.RETROSPECTIVE[app.retro_index]
                if item["kind"] == "int1to5":
                    app.retro_scale.set(4)
                else:
                    app.retro_widget.insert("1.0", "line one\nline two")
                app.save_retro(item)
                n += 1
            self.assertEqual(n, len(gui.prompts.RETROSPECTIVE))
            sess = json.loads((app.session_dir / "session.json").read_text())
            self.assertEqual(sess["retrospective"]["could_say_at_start"], 4)
            self.assertEqual(sess["retrospective"]["changes"], ["line one", "line two"])
            self.assertEqual(sess["outcome"], "completed")
            # review -> pack -> done
            app.pack()
            pump(app, 2.0)
            self.assertTrue(app.zip_path and app.zip_path.exists())
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
