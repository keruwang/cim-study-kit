"""A small always-on-top window that carries the participant through the session: consent, task, instructions,
recording with a visible countdown and checkpoint banners, the retrospective questions, review, and packaging.
Standard-library tkinter only."""
from __future__ import annotations

import json
import platform
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from . import notify, prompts, screenpipe
from .config import sessions_root
from .package import dir_size_mb, make_zip, open_url, reveal, sha256
from .export import ApiError, api_stats, export_session, stats_from_export, wait_for_capture
from .redact import redact_app, redact_range
from .session import append_jsonl, checkpoint_minutes, silence_due, start_backup_audio, stop_backup_audio, write_json
from .timeutil import iso_utc

SYSTEM = platform.system()
PAD = {"padx": 12, "pady": 6}
BANNER_BG = "#ffe66d"
REC_BG = "#ffd6d6"
PILL_W, PILL_H = 230, 44
MIN_SIZE = (460, 420)


class _DemoMic:
    """Demo mode: a microphone that never hears anything, so the silence reminder can be seen."""

    def __init__(self):
        self.t0 = time.time()
        self.lines = 1

    def alive(self):
        return True

    def heard_recently(self, within=3.0):
        return False

    def seconds_since_voice(self):
        return time.time() - self.t0

    def stop(self):
        pass

    def summary(self):
        return {"demo": True}


class StudyApp:
    def __init__(self, cfg: dict, args, demo: bool = False):
        self.cfg = cfg
        self.args = args
        self.demo = demo
        self.minute_s = 5 if demo else 60
        self.tasks = prompts.load_tasks()
        self.session: dict = {}
        self.session_dir: Path | None = None
        self.data_dir: Path | None = None
        self.proc = None
        self.api_key = None
        self.silence_minutes = getattr(args, 'silence_every', None) or cfg['silence_reminder_minutes']
        self.cfg = dict(cfg, silence_reminder_minutes=self.silence_minutes)
        self.rec_start = None
        self.backup = None
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.fired: set = set()
        self.start_ts: float | None = None
        self.after_ids: list = []
        self.retro_answers: dict = {}
        self.zip_path: Path | None = None

        self.root = tk.Tk()
        self.root.title(cfg["study_name"])
        self.root.geometry("520x600+40+60")
        self.root.minsize(*MIN_SIZE)
        self.root.attributes("-topmost", True)
        self.default_bg = self.root.cget("bg")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.container = ttk.Frame(self.root)
        self.container.pack(fill="both", expand=True)
        self.show_welcome()

    # ---------- helpers ----------

    def clear(self) -> None:
        for aid in self.after_ids:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self.after_ids = []
        for child in self.container.winfo_children():
            child.destroy()

    def later(self, ms: int, fn) -> None:
        self.after_ids.append(self.root.after(ms, fn))

    def heading(self, text: str) -> None:
        ttk.Label(self.container, text=text, font=("TkDefaultFont", 15, "bold")).pack(anchor="w", **PAD)

    def para(self, text: str, height: int = 10) -> scrolledtext.ScrolledText:
        box = scrolledtext.ScrolledText(self.container, wrap="word", height=height, font=("TkDefaultFont", 11))
        box.insert("1.0", text.strip("\n"))
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, **PAD)
        return box

    def button_row(self, *buttons) -> None:
        row = ttk.Frame(self.container)
        row.pack(fill="x", **PAD)
        for text, cmd in buttons:
            ttk.Button(row, text=text, command=cmd).pack(side="right", padx=4)

    def on_close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if not messagebox.askyesno("Recording in progress", "Stop the session now? The recording so far is kept."):
                return
            self.end_recording("stopped_early")
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

    # ---------- pages ----------

    def show_welcome(self) -> None:
        self.clear()
        self.heading("Welcome")
        text = prompts.BANNER.format(study_name=self.cfg["study_name"]) + prompts.CONSENT_SUMMARY.format(
            idle_s=self.cfg["idle_capture_interval_ms"] // 1000, ignored=", ".join(self.cfg["ignored_windows"]) or "(none)",
            contact=self.cfg["contact"])
        if self.demo:
            text = "DEMO MODE: nothing is recorded; one 'minute' lasts 5 seconds.\n" + text
        self.para(text, height=18)
        self.agree = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.container, text="I have read this and agree to take part", variable=self.agree).pack(anchor="w", **PAD)
        self.button_row(("Continue", self.on_agree), ("Test chime and voice", self.test_cues))

    def test_cues(self) -> None:
        notify.cue("Study kit", "This is what a checkpoint sounds like.")
        messagebox.showinfo("Cue test", "You should have heard a chime and a voice. If not, check the volume; the window banner will still show every cue.")

    def on_agree(self) -> None:
        if not self.agree.get():
            messagebox.showwarning("Consent", "Please tick the box to continue, or close the window to leave.")
            return
        self.show_details()

    def show_details(self) -> None:
        self.clear()
        self.heading("About this session")
        frm = ttk.Frame(self.container)
        frm.pack(fill="x", **PAD)
        ttk.Label(frm, text="Participant code (from the study team)").grid(row=0, column=0, sticky="w", pady=3)
        self.pid_var = tk.StringVar(value=self.args.participant or "")
        ttk.Entry(frm, textvariable=self.pid_var, width=14).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(frm, text="Task family").grid(row=1, column=0, sticky="nw", pady=3)
        self.family_var = tk.StringVar(value=(self.args.family or "").upper())
        fam_frame = ttk.Frame(frm)
        fam_frame.grid(row=1, column=1, sticky="w")
        for f in self.tasks["families"]:
            ttk.Radiobutton(fam_frame, text=f"{f['code']}  {f['name']}: {f['one_line']}", value=f["code"],
                            variable=self.family_var).pack(anchor="w")
        ttk.Label(frm, text="One line on what you will actually do (optional)").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.task_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.task_var, width=56).grid(row=3, column=0, columnspan=2, sticky="we")
        self.side_box = None
        if not self.args.no_nudge:
            ttk.Label(frm, text="Small real side tasks you also need to do today, one per line (optional).\n"
                      "Halfway through, a reminder lets you handle them whenever it suits you, or ignore it.",
                      wraplength=470, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
            self.side_box = tk.Text(frm, height=3, width=56)
            self.side_box.grid(row=5, column=0, columnspan=2, sticky="we")
        self.button_row(("Continue", self.on_details), ("Back", self.show_welcome))

    def on_details(self) -> None:
        pid = self.pid_var.get().strip().replace(" ", "_")
        fam = self.family_var.get().strip().upper()
        fams = {f["code"]: f for f in self.tasks["families"]}
        if not pid or fam not in fams:
            messagebox.showwarning("Missing", "Please enter your participant code and pick a task family.")
            return
        side = []
        if self.side_box is not None:
            side = [l.strip() for l in self.side_box.get("1.0", "end").splitlines() if l.strip()]
        self.session = {
            "participant": pid, "family": fam, "family_name": fams[fam]["name"], "axes": fams[fam]["axes"],
            "task_text": self.task_var.get().strip(), "side_tasks": side, "nudge_enabled": bool(side) and not self.args.no_nudge,
        }
        self.show_task(fams[fam])

    def show_task(self, fam: dict) -> None:
        self.clear()
        self.heading(f"Your task: {fam['code']}  {fam['name']}")
        examples = "\n".join(f"  - {e}" for e in fam["examples"])
        avoid = "\n".join(f"  - {a}" for a in self.tasks.get("avoid", []))
        self.para(f"{fam['one_line']}\n\n{fam['guidance']}\n\nExamples of the kind of thing we mean:\n{examples}\n\nPlease avoid:\n{avoid}", height=16)
        self.button_row(("Continue", self.show_instructions), ("Back", self.show_details))

    def schedule(self) -> tuple[int, int, list[int]]:
        minutes = self.args.minutes or self.cfg["session_minutes"]
        every = getattr(self.args, "checkpoint_every", None) or self.cfg["checkpoint_every_minutes"] or 0
        return minutes, every, (checkpoint_minutes(minutes, every) if every else [])

    def show_instructions(self) -> None:
        self.clear()
        self.heading("Thinking aloud")
        minutes, every, cps = self.schedule()
        text = prompts.THINK_ALOUD.format(silence=self.cfg['silence_reminder_minutes'], minutes=minutes,
                                          checkpoints=prompts.CHECKPOINTS_OPTIONAL.format(every=every) if every else '')
        text += f"\nThis session: {minutes} minutes." + (f" Checkpoints at minute {', '.join(map(str, cps))}." if cps else "")
        if self.session.get("nudge_enabled"):
            text += f" Side-task reminder at minute {self.cfg['nudge_at_minute']}."
        text += "\n\nThis window stays on top. You can shrink it or move it to a corner, but keep it visible.\nOpen the apps you need, then press Start."
        self.para(text, height=18)
        self.button_row(("Start recording", self.start_recording), ("Back", lambda: self.show_task({f["code"]: f for f in self.tasks["families"]}[self.session["family"]])))

    # ---------- recording ----------

    def start_recording(self) -> None:
        minutes, every, cps = self.schedule()
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        self.session_dir = sessions_root(self.cfg) / f"{self.session['participant']}_{stamp}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.data_dir = self.session_dir / "screenpipe"
        self.session.update({
            "study": self.cfg["study_name"], "kit_version": "0.1.0", "ui": "gui", "demo": self.demo,
            "session_minutes": minutes, "checkpoint_every_minutes": every, "checkpoints_planned": cps,
            "silence_reminder_minutes": self.silence_minutes,
            "nudge_at_minute": self.cfg["nudge_at_minute"] if self.session.get("nudge_enabled") else None,
            "os": f"{SYSTEM} {platform.release()}", "machine": platform.machine(), "python": platform.python_version(),
            "screenpipe_version": self.cfg["screenpipe_version"],
            "screenpipe_args": screenpipe.build_record_args(self.cfg, self.data_dir, self.cfg["screenpipe_port"]),
            "consent": {"agreed": True, "at": iso_utc()}, "created_at": iso_utc(), "local_timezone": time.strftime("%Z"),
        })
        write_json(self.session_dir / "session.json", self.session)
        self.clear()
        self.heading("Starting the recorder")
        self.status_var = tk.StringVar(value="First start can take 20-30 seconds ...")
        ttk.Label(self.container, textvariable=self.status_var, wraplength=480).pack(anchor="w", **PAD)
        threading.Thread(target=self._recorder_thread, daemon=True).start()
        self.later(500, self._poll_start)

    def _recorder_thread(self) -> None:
        if self.demo:
            time.sleep(1.0)
            self.events.put(("started", {"frames": 0, "audio_files": 0, "audio_chunks": 0, "ui_events": {}}))
            return
        try:
            if screenpipe.port_in_use(self.cfg["screenpipe_port"]):
                self.events.put(("failed", f"Port {self.cfg['screenpipe_port']} is in use. Quit any running screenpipe and try again."))
                return
            self.api_key = screenpipe.new_api_key()
            self.proc = screenpipe.start_recorder(self.cfg, self.data_dir, self.cfg["screenpipe_port"],
                                                  self.session_dir / "screenpipe.log", audio=True, api_key=self.api_key)
            def progress(elapsed, st):
                if st is None:
                    msg = f"Recorder starting ({int(elapsed)} s; the first start downloads and boots, 20-40 s) ..."
                else:
                    msg = f"Recorder up ({int(elapsed)} s): {st['frames']} screenshots so far ..."
                self.events.put(("progress", msg))

            stats = wait_for_capture(self.cfg["screenpipe_port"], self.api_key, timeout=120, progress=progress)
            if stats["frames"] == 0:
                screenpipe.stop_recorder(self.proc)
                self.events.put(("failed", "No screenshots in 120 s. Run `python3 study.py setup` in Terminal to check permissions. Log: "
                                 + str(self.session_dir / "screenpipe.log")))
                return
            self.events.put(("started", stats))
        except Exception as exc:  # pragma: no cover
            self.events.put(("failed", str(exc)))

    def _poll_start(self) -> None:
        try:
            kind, payload = self.events.get_nowait()
        except queue.Empty:
            self.later(500, self._poll_start)
            return
        if kind == "progress":
            self.status_var.set(payload)
            self.later(500, self._poll_start)
            return
        if kind == "failed":
            messagebox.showerror("Recorder", payload)
            self.show_instructions()
            return
        warn = ""
        if not self.demo:
            self.backup = start_backup_audio(self.session_dir, self.cfg)
            self.session["mic_monitor"] = self.backup is not None
            if self.backup is None:
                warn = (warn + " " if warn else "") + "No ffmpeg: the kit cannot hear you, so it reminds at a fixed interval instead."
        else:
            self.backup = _DemoMic()
        self.last_reminder = None
        self.start_ts = time.time()
        self.rec_start = self.start_ts
        self.session["recording_started_at"] = iso_utc(self.start_ts)
        write_json(self.session_dir / "session.json", self.session)
        self.show_recording(warn)
        notify.cue("Recording started", prompts.START_SPOKEN)
        self.banner(prompts.START_SPOKEN, flash=False, seconds=6)

    def show_recording(self, warn: str = "") -> None:
        """Shrink to a small timer in the bottom-right corner; the full window comes back when the recording ends."""
        self.clear()
        self.root.configure(bg=REC_BG)
        self.root.title("recording")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.pill_geometry = f"{PILL_W}x{PILL_H}+{sw - PILL_W - 24}+{sh - PILL_H - 90}"
        top = tk.Frame(self.container, bg=REC_BG)
        top.pack(fill="x")
        tk.Label(top, text="●", fg="#b00020", bg=REC_BG, font=("TkDefaultFont", 14, "bold")).pack(side="left", padx=(8, 0))
        self.mic_lbl = tk.Label(top, text="mic", fg="#999", bg=REC_BG, font=("TkDefaultFont", 10))
        self.mic_lbl.pack(side="left", padx=(4, 0))
        self.clock_var = tk.StringVar(value="00:00")
        tk.Label(top, textvariable=self.clock_var, bg=REC_BG, font=("TkFixedFont", 18, "bold")).pack(side="left", padx=8)
        tk.Button(top, text="End", command=self.confirm_end, font=("TkDefaultFont", 10), highlightthickness=0, bd=0).pack(side="right", padx=6)
        self.banner_var = tk.StringVar(value="")
        self.banner_lbl = tk.Label(self.container, textvariable=self.banner_var, wraplength=PILL_W - 20, justify="left",
                                   font=("TkDefaultFont", 11, "bold"), bg=BANNER_BG, padx=8, pady=4)
        self.stats_var = tk.StringVar(value="")
        self.root.update_idletasks()  # let the old page's size request go before shrinking
        self.root.minsize(PILL_W, PILL_H)
        self.root.geometry(self.pill_geometry)
        if warn:
            self.banner(warn, flash=False, seconds=20)
        self.last_stats = None
        self.later(60000, self.poll_stats)
        self.tick()

    def _expand_pill(self, expanded: bool) -> None:
        if not hasattr(self, "pill_geometry"):
            return
        try:
            if expanded:
                self.banner_lbl.pack(fill="x", padx=6, pady=(0, 6))
                w, rest = self.pill_geometry.split("x", 1)
                h, pos = rest.split("+", 1)
                self.root.geometry(f"{w}x{PILL_H + 70}+{pos}")
            else:
                self.banner_lbl.pack_forget()
                self.root.geometry(self.pill_geometry)
        except tk.TclError:
            pass

    def banner(self, text: str, flash: bool = True, seconds: int = 15) -> None:
        """Show a short message under the timer for a few seconds (the pill grows, then shrinks back)."""
        self.banner_var.set(text)
        self._expand_pill(True)
        self._banner_gen = getattr(self, "_banner_gen", 0) + 1
        gen = self._banner_gen
        if flash:
            self._flash(6)
        self.later(seconds * 1000, lambda: gen == self._banner_gen and self._expand_pill(False))

    def _flash(self, n: int) -> None:
        if n <= 0 or not self.banner_lbl.winfo_exists():
            try:
                self.banner_lbl.configure(bg=BANNER_BG)
            except tk.TclError:
                pass
            return
        self.banner_lbl.configure(bg="#ff3b30" if n % 2 else BANNER_BG)
        self.later(350, lambda: self._flash(n - 1))

    def fire(self, kind: str, minute: int, n: int | None = None) -> None:
        elapsed = time.time() - self.start_ts
        entry = {"kind": kind, "minute": minute, "at": iso_utc(), "elapsed_s": round(elapsed)}
        if kind == "silence":
            entry = None
        if n is not None:
            entry["n"] = n
        if entry:
            append_jsonl(self.session_dir / "checkpoints.jsonl", entry)
        if kind == "checkpoint":
            notify.cue(prompts.CHECKPOINT_TITLE.format(n=n), prompts.CHECKPOINT_SPOKEN)
            self.banner(f"CHECKPOINT {n}: in one sentence, what are you trying to do right now, and how sure are you?")
        elif kind == "silence":
            notify.cue("Keep thinking aloud", prompts.SILENCE_SPOKEN)
            self.banner("Quiet for a while: please keep thinking aloud.")
        elif kind == "nudge":
            notify.cue("Side-task reminder", prompts.NUDGE_SPOKEN)
            self.banner("Reminder: you may handle a side task now if it suits you, or ignore this.")
        elif kind == "wrap":
            notify.cue("Wrap up", prompts.WRAP_UP_SPOKEN)
            self.banner("Two minutes left: start wrapping up your thought.")
        elif kind == "end":
            notify.cue("Time is up", prompts.END_SPOKEN)

    def tick(self) -> None:
        if self.start_ts is None:
            return
        self._drain_stats()
        minutes, every, cps = self.schedule()
        elapsed = time.time() - self.start_ts
        m = elapsed / self.minute_s
        remaining = max(0, minutes * self.minute_s - elapsed)
        self.clock_var.set(f"{int(remaining // 60):02d}:{int(remaining % 60):02d}")
        mon = self.backup
        try:
            heard = mon is not None and mon.heard_recently(3.0)
            self.mic_lbl.configure(fg="#1e8e3e" if heard else "#999", text="mic" if (mon is not None and mon.alive()) else "no mic")
        except tk.TclError:
            pass
        quiet = silence_due(self.cfg, mon, elapsed, self.last_reminder, self.minute_s)
        if quiet is not None:
            self.last_reminder = elapsed
            append_jsonl(self.session_dir / "checkpoints.jsonl", {"kind": "silence", "minute": round(m, 1), "silent_s": round(quiet),
                                                                   "at": iso_utc(), "elapsed_s": round(elapsed)})
            self.fire("silence", round(m, 1))
        nudge = self.session.get("nudge_at_minute")
        for i, c in enumerate(cps, start=1):
            if m >= c and ("cp", c) not in self.fired:
                self.fired.add(("cp", c))
                self.fire("checkpoint", c, i)
        if nudge and m >= nudge and ("nudge",) not in self.fired:
            self.fired.add(("nudge",))
            self.fire("nudge", nudge)
        wrap = minutes - self.cfg["wrap_up_minutes"]
        if 0 < self.cfg["wrap_up_minutes"] < minutes and m >= wrap and ("wrap",) not in self.fired:
            self.fired.add(("wrap",))
            self.fire("wrap", wrap)
        if m >= minutes:
            self.fire("end", minutes)
            self.end_recording("completed")
            return
        self.later(1000, self.tick)

    def poll_stats(self) -> None:
        """Once a minute, in a thread: warn on the pill if the recorder has produced no audio file yet."""
        if self.start_ts is None or self.demo:
            return

        def work():
            try:
                st = api_stats(self.cfg["screenpipe_port"], self.api_key)
                self.events.put(("stats", st))
            except (ApiError, OSError, ValueError, KeyError, IndexError):
                pass

        threading.Thread(target=work, daemon=True).start()
        self.later(60000, self.poll_stats)

    def _drain_stats(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "stats" and self.start_ts is not None:
                    self.last_stats = payload
                    if payload["audio_chunks"] == 0 and time.time() - self.start_ts > 75:
                        self.banner("No audio from the recorder yet. Is the microphone allowed for Terminal?", flash=False, seconds=20)
        except queue.Empty:
            pass

    def confirm_end(self) -> None:
        if messagebox.askyesno("End session", "Stop the session now? The recording so far is kept."):
            append_jsonl(self.session_dir / "checkpoints.jsonl", {"kind": "stopped_early", "at": iso_utc(), "elapsed_s": round(time.time() - self.start_ts)})
            self.end_recording("stopped_early")

    def end_recording(self, outcome: str) -> None:
        self.start_ts = None
        self.session["outcome"] = outcome
        self.session["recording_ended_at"] = iso_utc()
        write_json(self.session_dir / "session.json", self.session)
        self.clear()
        self.root.configure(bg=self.default_bg)
        self.root.title(self.cfg["study_name"])
        self.root.minsize(*MIN_SIZE)
        self.root.geometry("520x600+40+60")
        self.heading("Saving the recording")
        ttk.Label(self.container, text="Collecting the records from the recorder, then stopping it. This can take a minute ...",
                  wraplength=480).pack(anchor="w", **PAD)

        def stop():
            stop_backup_audio(self.backup)
            if self.backup is not None and hasattr(self.backup, "summary"):
                self.session["mic"] = self.backup.summary()
            if self.proc is not None:
                try:
                    stats = export_session(self.cfg["screenpipe_port"], self.api_key, self.session_dir, self.rec_start)
                    self.session["export"] = {k: stats[k] for k in ("frames", "ui_events", "audio_chunks", "audio_files", "typed_chars")}
                except (ApiError, OSError, ValueError) as exc:
                    self.session["export_error"] = str(exc)
                screenpipe.stop_recorder(self.proc)
                write_json(self.session_dir / "session.json", self.session)
            self.events.put(("stopped", None))

        threading.Thread(target=stop, daemon=True).start()
        self.later(500, self._poll_stop)

    def _poll_stop(self) -> None:
        try:
            self.events.get_nowait()
        except queue.Empty:
            self.later(500, self._poll_stop)
            return
        if self.session.get("export_error"):
            messagebox.showwarning("Recording", "The kit could not read the records back from the recorder:\n"
                                   + self.session["export_error"] + "\n\nThe raw recording folder is kept; please tell the study team.")
        self.retro_index = 0
        self.retro_answers = {}
        self.show_retro_question()

    # ---------- retrospective ----------

    def show_retro_question(self) -> None:
        items = prompts.RETROSPECTIVE
        if self.retro_index >= len(items):
            self.session["retrospective"] = self.retro_answers
            write_json(self.session_dir / "session.json", self.session)
            self.show_review()
            return
        item = items[self.retro_index]
        self.clear()
        self.heading(f"Question {self.retro_index + 1} of {len(items)}")
        ttk.Label(self.container, text=item["q"], wraplength=480, justify="left", font=("TkDefaultFont", 12)).pack(anchor="w", **PAD)
        self.retro_widget = None
        self.retro_scale = None
        if item["kind"] == "int1to5":
            self.retro_scale = tk.IntVar(value=0)
            row = ttk.Frame(self.container)
            row.pack(anchor="w", **PAD)
            for v in range(1, 6):
                ttk.Radiobutton(row, text=str(v), value=v, variable=self.retro_scale).pack(side="left", padx=6)
        else:
            self.retro_widget = tk.Text(self.container, height=8 if item["kind"] == "list" else 5, wrap="word")
            self.retro_widget.pack(fill="both", expand=True, **PAD)
            if item["kind"] == "list":
                ttk.Label(self.container, text="One item per line.").pack(anchor="w", padx=12)
        self.button_row(("Next", lambda: self.save_retro(item)))

    def save_retro(self, item: dict) -> None:
        if item["kind"] == "int1to5":
            v = self.retro_scale.get()
            self.retro_answers[item["key"]] = v or None
        else:
            raw = self.retro_widget.get("1.0", "end").strip()
            self.retro_answers[item["key"]] = [l.strip() for l in raw.splitlines() if l.strip()] if item["kind"] == "list" else raw
        self.retro_index += 1
        self.show_retro_question()

    # ---------- review and packaging ----------

    def stats_text(self) -> str:
        if self.demo:
            return "(demo mode: nothing was recorded)"
        s = stats_from_export(self.session_dir) or screenpipe.capture_stats(self.data_dir)
        lines = [f"screenshots: {s['frames']}    input events: {s['ui_events'] or 'none'}    typed characters: {s['typed_chars']}",
                 f"audio files: {s['audio_files']}", "", "apps recorded:"]
        lines += [f"  {n:5d}  {app}" for app, n in s["apps"][:25]]
        if s["domains"]:
            lines += ["", "websites (most frequent):"] + [f"  {n:5d}  {host}" for host, n in s["domains"]]
        return "\n".join(lines)

    def show_review(self) -> None:
        self.clear()
        self.heading("Review before upload")
        ttk.Label(self.container, text="This is what was recorded. You can delete everything from an app, or a time range.",
                  wraplength=480).pack(anchor="w", padx=12)
        self.review_box = self.para(self.stats_text(), height=14)
        frm = ttk.Frame(self.container)
        frm.pack(fill="x", padx=12)
        self.del_app_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.del_app_var, width=22).grid(row=0, column=0, sticky="w")
        ttk.Button(frm, text="Delete this app", command=self.delete_app).grid(row=0, column=1, padx=4)
        self.del_from = tk.StringVar()
        self.del_to = tk.StringVar()
        ttk.Label(frm, text="minutes").grid(row=1, column=0, sticky="e")
        sub = ttk.Frame(frm)
        sub.grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(sub, textvariable=self.del_from, width=5).pack(side="left")
        ttk.Label(sub, text=" to ").pack(side="left")
        ttk.Entry(sub, textvariable=self.del_to, width=5).pack(side="left")
        ttk.Button(frm, text="Delete this time range", command=self.delete_range).grid(row=1, column=1, padx=4)
        self.button_row(("Pack for upload", self.pack))

    def _refresh_review(self) -> None:
        self.review_box.configure(state="normal")
        self.review_box.delete("1.0", "end")
        self.review_box.insert("1.0", self.stats_text())
        self.review_box.configure(state="disabled")

    def delete_app(self) -> None:
        name = self.del_app_var.get().strip()
        if not name or self.demo:
            return
        if messagebox.askyesno("Delete", f"Delete everything matching '{name}'?"):
            removed = redact_app(self.session_dir, name)
            self.session.setdefault("redactions", []).append({"kind": "app", "match": name, "removed": removed, "at": iso_utc()})
            write_json(self.session_dir / "session.json", self.session)
            self._refresh_review()

    def delete_range(self) -> None:
        if self.demo:
            return
        try:
            a, b = float(self.del_from.get()), float(self.del_to.get())
            start = datetime.fromisoformat(self.session["recording_started_at"]).timestamp()
        except (ValueError, KeyError):
            messagebox.showwarning("Range", "Enter two numbers of minutes, e.g. 12 and 18.")
            return
        if messagebox.askyesno("Delete", f"Delete minutes {a:g}-{b:g} (screen, input, and audio)?"):
            removed = redact_range(self.session_dir, start + a * self.minute_s, start + b * self.minute_s)
            wav = self.session_dir / "backup_audio.wav"
            if wav.exists():
                wav.unlink()
                removed["backup_audio"] = "deleted whole file"
            self.session.setdefault("redactions", []).append({"kind": "range", "from_min": a, "to_min": b, "removed": removed, "at": iso_utc()})
            write_json(self.session_dir / "session.json", self.session)
            self._refresh_review()

    def pack(self) -> None:
        self.clear()
        self.heading("Packing")
        ttk.Label(self.container, text=f"Compressing about {dir_size_mb(self.session_dir):.0f} MB. This can take a minute ...").pack(anchor="w", **PAD)

        def work():
            z = make_zip(self.session_dir)
            digest = sha256(z)
            write_json(self.session_dir / "manifest.json", {"zip": str(z), "sha256": digest, "size_bytes": z.stat().st_size, "packed_at": iso_utc()})
            self.events.put(("packed", (z, digest)))

        threading.Thread(target=work, daemon=True).start()
        self.later(500, self._poll_pack)

    def _poll_pack(self) -> None:
        try:
            _, (z, digest) = self.events.get_nowait()
        except queue.Empty:
            self.later(500, self._poll_pack)
            return
        self.zip_path = z
        self.show_done(z, digest)

    def show_done(self, z: Path, digest: str) -> None:
        self.clear()
        self.heading("Upload")
        url = self.cfg.get("upload_url") or ""
        size_mb = z.stat().st_size / (1024 * 1024)
        text = (f"The session is packed into one file:\n{z}\n\nSize: {size_mb:.1f} MB\nSHA-256: {digest}\n\n"
                f"1. Press 'Open upload page' (or use this link): {url or '(no link configured; send the file to the study team)'}\n"
                "2. Drag the zip file into it ('Show file' reveals it).\n"
                "3. Tell the study team the file name. Keep the local copy until they confirm.\n\n"
                "The raw recording folder stays on this computer (sessions/ in the kit folder) until you delete it.\n\nThank you.")
        self.para(text, height=14)
        self.button_row(("Close", self.root.destroy), ("Show file", lambda: reveal(z)), ("Open upload page", lambda: open_url(url)))
        reveal(z)
        open_url(url)


def run_gui(cfg: dict, args, demo: bool = False) -> int:
    app = StudyApp(cfg, args, demo=demo)
    app.run()
    return 0
