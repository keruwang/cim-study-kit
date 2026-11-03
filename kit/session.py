"""The participant flow: setup check, consent, task, recording with timed cues, retrospective, review, packaging."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import notify, prompts, screenpipe
from .audio_monitor import MicMonitor, find_ffmpeg
from .config import sessions_root
from .package import dir_size_mb, make_zip, open_url, reveal, sha256
from .export import ApiError, export_session, stats_from_export
from .redact import redact_app, redact_range
from .timeutil import iso_utc, local_clock, parse_timestamp

SYSTEM = platform.system()


# ---------- small I/O helpers ----------

def say(text: str = "") -> None:
    print(text)
    sys.stdout.flush()


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or (default or "")


def ask_yes(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        v = ask(f"{prompt} ({d})").lower()
        if not v:
            return default
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False


def ask_int(prompt: str, lo: int, hi: int) -> int | None:
    while True:
        v = ask(f"{prompt} ({lo}-{hi}, Enter to skip)")
        if not v:
            return None
        if v.isdigit() and lo <= int(v) <= hi:
            return int(v)


def ask_list(prompt: str) -> list[str]:
    say(prompt)
    items = []
    while True:
        v = ask(f"  {len(items) + 1}")
        if not v:
            return items
        items.append(v)


def pause(prompt: str = "Press Enter to continue") -> None:
    try:
        input(f"{prompt} ... ")
    except EOFError:
        pass


def write_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def append_jsonl(path: Path, data: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False) + "\n")


def checkpoint_minutes(session_minutes: int, every: int) -> list[int]:
    return [m for m in range(every, session_minutes, every)]


# ---------- setup ----------

def run_setup(cfg: dict, args) -> int:
    say(prompts.BANNER.format(study_name=cfg["study_name"]))
    say("SETUP CHECK")
    say(f"  python: {platform.python_version()} on {SYSTEM} {platform.machine()}")
    try:
        cmd = screenpipe.base_command(cfg)
    except RuntimeError as exc:
        say(f"  screenpipe: NOT AVAILABLE. {exc}")
        return 1
    say(f"  screenpipe command: {' '.join(cmd)} (first run downloads about 100 MB)")
    say(f"  screenpipe version: {screenpipe.version_string(cfg)}")
    ffmpeg = find_ffmpeg(cfg["screenpipe_port"])
    say(f"  ffmpeg (microphone monitor + backup audio): {ffmpeg or 'NOT FOUND: the silence reminder will fall back to a fixed interval. macOS: brew install ffmpeg'}")
    if screenpipe.port_in_use(cfg["screenpipe_port"]):
        say(f"  port {cfg['screenpipe_port']}: IN USE. Another screenpipe is running? Quit it, or change screenpipe_port in study_config.json.")
        return 1
    say("")
    say("Checking permissions with `screenpipe doctor` (macOS may show permission dialogs: click Allow) ...")
    ok, text, missing = screenpipe.run_doctor(cfg)
    say(text.strip())
    if not ok:
        say("")
        say("PERMISSIONS MISSING: " + ", ".join(missing))
        if SYSTEM == "Darwin":
            say("  Open System Settings > Privacy & Security, and enable your Terminal app (Terminal, iTerm, ...) under:")
            say("    Screen & System Audio Recording, Microphone, Accessibility.")
            say("  Then QUIT the terminal app completely (Cmd+Q), reopen it, and run `python3 study.py setup` again.")
        elif SYSTEM == "Windows":
            say("  Settings > Privacy & security: allow desktop apps to access the Microphone. Run the terminal as a normal user.")
        return 1
    if args.no_dry_run:
        say("Permissions OK. Skipping the dry run.")
        return 0
    say("")
    say("DRY RUN (about 60 seconds): screenpipe records into a temporary folder while you click around and say a few words.")
    pause("Press Enter, then switch to another window, click a few things, and talk")
    tmp = sessions_root(cfg) / "_dry_run"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    log_path = tmp / "screenpipe.log"
    proc = screenpipe.start_recorder(cfg, tmp / "screenpipe", cfg["screenpipe_port"], log_path, audio=True)
    mon = start_backup_audio(tmp, cfg)
    try:
        stats = screenpipe.wait_for_capture(tmp / "screenpipe", need_audio=True, timeout=75)
        time.sleep(10)
        stats = screenpipe.capture_stats(tmp / "screenpipe")
    finally:
        screenpipe.stop_recorder(proc)
        stop_backup_audio(mon)
    say("")
    if mon is not None:
        say(f"  microphone monitor: heard voice during {mon.voiced_seconds} of {mon.lines} seconds (threshold {mon.voice_db} dB; last level {mon.level_db})")
        if mon.lines and mon.voiced_seconds == 0:
            say("  WARNING: no voice detected while you talked. Lower voice_rms_db in study_config.json (e.g. -50) or check the input device.")
    say(f"  frames (screenshots + text): {stats['frames']}")
    say(f"  input events: {stats['ui_events'] or 'none'}")
    say(f"  audio files: {stats['audio_files']}  (db rows: {stats['audio_chunks']})")
    problems = []
    if stats["frames"] == 0:
        problems.append("no screenshots were captured (Screen Recording permission, or the display was not found)")
    if stats["audio_files"] == 0 and stats["audio_chunks"] == 0:
        problems.append("no audio was captured (Microphone permission for the terminal app, or screenpipe is still waiting for it)")
    if not stats["ui_events"]:
        problems.append("no clicks or key events were captured (Accessibility permission for the terminal app)")
    if problems:
        say("PROBLEMS:")
        for p in problems:
            say(f"  - {p}")
        say(f"  screenpipe log: {log_path}")
        say("Fix the permissions, fully restart the terminal app, and run setup again.")
        return 1
    shutil.rmtree(tmp, ignore_errors=True)
    say("Setup OK. You can run the session with:  python3 study.py run")
    return 0


# ---------- the session ----------

def choose_family(tasks: dict, code: str | None) -> dict:
    fams = tasks["families"]
    by_code = {f["code"].upper(): f for f in fams}
    if code and code.upper() in by_code:
        return by_code[code.upper()]
    say("TASK FAMILIES (the study team may have told you which letter to pick):")
    for f in fams:
        say(f"  {f['code']}  {f['name']}: {f['one_line']}")
    while True:
        v = ask("Letter").upper()
        if v in by_code:
            return by_code[v]


def show_task(fam: dict, tasks: dict) -> None:
    examples = "\n".join(f"    - {e}" for e in fam["examples"])
    avoid = "\n".join(f"    - {a}" for a in tasks.get("avoid", []))
    say(prompts.TASK_INTRO.format(code=fam["code"], name=fam["name"], one_line=fam["one_line"],
                                  guidance=fam["guidance"], examples=examples, avoid=avoid))


def start_backup_audio(session_dir: Path, cfg: dict) -> MicMonitor | None:
    """Start the microphone monitor (level metering for the silence reminder; backup WAV if configured)."""
    ffmpeg = find_ffmpeg(cfg.get("screenpipe_port"))
    if not ffmpeg:
        return None
    mon = MicMonitor(ffmpeg, session_dir, voice_db=float(cfg.get("voice_rms_db", -40)), backup_wav=bool(cfg.get("backup_audio", True)))
    return mon if mon.start() else None


def stop_backup_audio(mon: MicMonitor | None) -> None:
    if mon:
        mon.stop()


def silence_due(cfg: dict, mon: MicMonitor | None, elapsed: float, last_reminder: float | None, minute_s: float = 60.0) -> float | None:
    """Return the seconds of silence to report if a reminder is due now, else None."""
    if mon is not None and mon.alive():
        limit = float(cfg.get("silence_reminder_minutes", 0) or 0) * minute_s
        since = mon.seconds_since_voice()
        if limit <= 0 or since is None:
            return None
        since_reminder = elapsed - last_reminder if last_reminder is not None else since
        return since if since >= limit and since_reminder >= limit else None
    limit = float(cfg.get("silence_fallback_reminder_minutes", 0) or 0) * minute_s
    if limit <= 0:
        return None
    anchor = last_reminder if last_reminder is not None else 0.0
    return elapsed - anchor if elapsed - anchor >= limit else None


def timed_recording(cfg: dict, session: dict, session_dir: Path, nudge: bool, mon: MicMonitor | None = None) -> str:
    """Run the timer with cues. Returns 'completed' or 'stopped_early'."""
    minutes = session["session_minutes"]
    every = session.get("checkpoint_every_minutes") or 0
    checkpoints = checkpoint_minutes(minutes, every) if every else []
    last_reminder = None
    nudge_min = cfg["nudge_at_minute"] if nudge else None
    wrap = max(0, minutes - cfg["wrap_up_minutes"])
    start = time.time()
    session["recording_started_at"] = iso_utc(start)
    fired = set()
    cp_path = session_dir / "checkpoints.jsonl"
    notify.cue("Recording started", prompts.START_SPOKEN)
    say(f"[{local_clock(start)}] Recording. Session length {minutes} min; checkpoints at {checkpoints} min.")
    say("You can leave this window. Press Ctrl+C here if you must stop early.")  # terminal flow only
    last_printed = -1
    while True:
        try:
            elapsed = time.time() - start
            m = int(elapsed // 60)
            if m != last_printed:
                last_printed = m
                say(f"  {m:02d}/{minutes} min")
            for cp in checkpoints:
                if m >= cp and ("cp", cp) not in fired:
                    fired.add(("cp", cp))
                    n = checkpoints.index(cp) + 1
                    append_jsonl(cp_path, {"kind": "checkpoint", "n": n, "minute": cp, "at": iso_utc(), "elapsed_s": round(elapsed)})
                    notify.cue(prompts.CHECKPOINT_TITLE.format(n=n), prompts.CHECKPOINT_SPOKEN)
                    say(f"  >> checkpoint {n}: say one sentence about what you are trying to do")
            quiet = silence_due(cfg, mon, elapsed, last_reminder)
            if quiet is not None:
                last_reminder = elapsed
                append_jsonl(cp_path, {"kind": "silence", "minute": round(elapsed / 60, 1), "silent_s": round(quiet), "at": iso_utc(), "elapsed_s": round(elapsed)})
                notify.cue("Keep thinking aloud", prompts.SILENCE_SPOKEN)
                say(f"  >> quiet for {int(quiet // 60)} min: reminder played")
            if nudge_min is not None and m >= nudge_min and ("nudge", 0) not in fired:
                fired.add(("nudge", 0))
                append_jsonl(cp_path, {"kind": "nudge", "minute": nudge_min, "at": iso_utc(), "elapsed_s": round(elapsed)})
                notify.cue("Side-task reminder", prompts.NUDGE_SPOKEN)
            if m >= wrap and ("wrap", 0) not in fired and 0 < cfg["wrap_up_minutes"] < minutes:
                fired.add(("wrap", 0))
                notify.cue("Wrap up", prompts.WRAP_UP_SPOKEN)
            if elapsed >= minutes * 60:
                append_jsonl(cp_path, {"kind": "end", "minute": minutes, "at": iso_utc(), "elapsed_s": round(elapsed)})
                notify.cue("Time is up", prompts.END_SPOKEN)
                say("  >> time is up")
                return "completed"
            time.sleep(1.0)
        except KeyboardInterrupt:
            say("")
            if ask_yes("Stop the session now?", default=False):
                append_jsonl(cp_path, {"kind": "stopped_early", "at": iso_utc(), "elapsed_s": round(time.time() - start)})
                return "stopped_early"
            say("Continuing.")


def retrospective() -> dict:
    say("")
    say("A FEW QUESTIONS (typed; about 5 minutes). Keep thinking aloud if you like; the microphone is off now.")
    answers = {}
    for item in prompts.RETROSPECTIVE:
        say("")
        if item["kind"] == "text":
            answers[item["key"]] = ask(item["q"])
        elif item["kind"] == "int1to5":
            answers[item["key"]] = ask_int(item["q"], 1, 5)
        elif item["kind"] == "list":
            answers[item["key"]] = ask_list(item["q"])
    return answers


def print_stats(stats: dict) -> None:
    say(f"  screenshots: {stats['frames']}   input events: {stats['ui_events'] or 'none'}   typed characters: {stats['typed_chars']}")
    say(f"  audio files: {stats['audio_files']}")
    say("  apps recorded:")
    for app, n in stats["apps"][:20]:
        say(f"    {n:5d}  {app}")
    if stats["domains"]:
        say("  websites (most frequent):")
        for host, n in stats["domains"]:
            say(f"    {n:5d}  {host}")


def review_and_redact(session_dir: Path, session: dict) -> list[dict]:
    say(prompts.REVIEW_INTRO)
    actions = []
    while True:
        print_stats(stats_from_export(session_dir) or screenpipe.capture_stats(session_dir / "screenpipe"))
        say("")
        say("Delete something?  [a] everything from an app   [t] a time range   [Enter] nothing, continue")
        v = ask("Choice").lower()
        if not v:
            return actions
        if v == "a":
            name = ask("App or window-title text to delete (case-insensitive)")
            if name and ask_yes(f"Delete everything matching '{name}'?"):
                removed = redact_app(session_dir, name)
                actions.append({"kind": "app", "match": name, "removed": removed, "at": iso_utc()})
                say(f"  removed: {removed}")
        elif v == "t":
            say("  Give minutes into the session, e.g. 12 to 18.")
            a = ask("From minute")
            b = ask("To minute")
            try:
                start = datetime.fromisoformat(session["recording_started_at"]).timestamp()
                lo, hi = start + float(a) * 60, start + float(b) * 60
            except (ValueError, KeyError):
                say("  could not read those numbers")
                continue
            if ask_yes(f"Delete minutes {a}-{b} (screen, input, and audio)?"):
                removed = redact_range(session_dir, lo, hi)
                actions.append({"kind": "range", "from_min": a, "to_min": b, "removed": removed, "at": iso_utc()})
                say(f"  removed: {removed}")
                if (session_dir / "backup_audio.wav").exists():
                    say("  note: the backup audio file covers the whole session; deleting it too.")
                    (session_dir / "backup_audio.wav").unlink()


def package_and_upload(cfg: dict, session_dir: Path) -> Path:
    say("")
    say(f"Packing {dir_size_mb(session_dir):.1f} MB ...")
    zip_path = make_zip(session_dir)
    digest = sha256(zip_path)
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    url = cfg.get("upload_url") or "(no upload link configured; send the file to the study team)"
    say(prompts.UPLOAD_INSTRUCTIONS.format(zip_path=zip_path, size_mb=size_mb, sha=digest, url=url))
    manifest = {"zip": str(zip_path), "sha256": digest, "size_bytes": zip_path.stat().st_size, "packed_at": iso_utc()}
    write_json(session_dir / "manifest.json", manifest)
    reveal(zip_path)
    open_url(cfg.get("upload_url", ""))
    return zip_path


def run_session(cfg: dict, args) -> int:
    if getattr(args, "silence_every", None):
        cfg = dict(cfg, silence_reminder_minutes=args.silence_every)
    tasks = prompts.load_tasks()
    say(prompts.BANNER.format(study_name=cfg["study_name"]))
    if not args.skip_checks:
        try:
            screenpipe.base_command(cfg)
        except RuntimeError as exc:
            say(str(exc))
            return 1
        if screenpipe.port_in_use(cfg["screenpipe_port"]):
            say(f"Port {cfg['screenpipe_port']} is in use. Quit any running screenpipe (or the screenpipe app) and retry.")
            return 1
    say(prompts.CONSENT_SUMMARY.format(idle_s=cfg["idle_capture_interval_ms"] // 1000,
                                       ignored=", ".join(cfg["ignored_windows"]) or "(none)", contact=cfg["contact"]))
    if not ask_yes("I have read this and agree to take part", default=False):
        say("No recording was made.")
        return 0
    pid = (args.participant or ask("Participant code (given by the study team)")).strip().replace(" ", "_") or "P0"
    fam = choose_family(tasks, args.family)
    show_task(fam, tasks)
    task_text = ask("One line on what you will actually do (optional; Enter to skip)")
    nudge = not args.no_nudge
    side_tasks = []
    if nudge:
        say(prompts.NUDGE_INTRO)
        side_tasks = ask_list("Side tasks (Enter on an empty line to finish; nothing is fine):")
        if not side_tasks:
            nudge = False
    minutes = args.minutes or cfg["session_minutes"]
    every = getattr(args, "checkpoint_every", None) or cfg["checkpoint_every_minutes"] or 0
    say(prompts.THINK_ALOUD.format(silence=cfg["silence_reminder_minutes"], minutes=minutes,
                                   checkpoints=prompts.CHECKPOINTS_OPTIONAL.format(every=every) if every else ""))
    pause("Open the apps you need. When ready, press Enter to start recording")

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    session_dir = sessions_root(cfg) / f"{pid}_{stamp}"
    session_dir.mkdir(parents=True, exist_ok=False)
    data_dir = session_dir / "screenpipe"
    session = {
        "participant": pid, "study": cfg["study_name"], "kit_version": "0.1.0",
        "family": fam["code"], "family_name": fam["name"], "axes": fam["axes"], "task_text": task_text,
        "nudge_enabled": nudge, "side_tasks": side_tasks, "session_minutes": minutes,
        "checkpoint_every_minutes": every, "silence_reminder_minutes": cfg["silence_reminder_minutes"],
        "nudge_at_minute": cfg["nudge_at_minute"] if nudge else None,
        "os": f"{SYSTEM} {platform.release()}", "machine": platform.machine(), "python": platform.python_version(),
        "screenpipe_version": cfg["screenpipe_version"], "screenpipe_args": screenpipe.build_record_args(cfg, data_dir, cfg["screenpipe_port"]),
        "consent": {"agreed": True, "at": iso_utc()}, "created_at": iso_utc(), "local_timezone": time.strftime("%Z"),
        "local_utc_offset_s": -time.timezone if not time.daylight else -time.altzone,
    }
    write_json(session_dir / "session.json", session)

    say("Starting the recorder (first start can take 20-30 s) ...")
    api_key = screenpipe.new_api_key()
    proc = screenpipe.start_recorder(cfg, data_dir, cfg["screenpipe_port"], session_dir / "screenpipe.log", audio=True, api_key=api_key)
    stats = screenpipe.wait_for_capture(data_dir, need_audio=True, timeout=90)
    if stats["frames"] == 0:
        screenpipe.stop_recorder(proc)
        say("The recorder produced no screenshots in 90 s. Run `python3 study.py setup` to check permissions. Log: " + str(session_dir / "screenpipe.log"))
        return 1
    if stats["audio_files"] == 0 and stats["audio_chunks"] == 0:
        say("WARNING: no audio yet. If this persists the think-aloud will be missing; the backup recorder (if available) still runs.")
    backup = start_backup_audio(session_dir, cfg)
    session["mic_monitor"] = backup is not None
    if backup is None:
        say("NOTE: ffmpeg not found, so the kit cannot hear whether you are talking; it will remind you at a fixed interval instead.")
    outcome = "aborted"
    try:
        outcome = timed_recording(cfg, session, session_dir, nudge, backup)
    finally:
        session["outcome"] = outcome
        session["recording_ended_at"] = iso_utc()
        stop_backup_audio(backup)
        if backup is not None:
            session["mic"] = backup.summary()
        write_json(session_dir / "session.json", session)
        say("Collecting the records from the recorder (this can take a minute) ...")
        try:
            ex = export_session(cfg["screenpipe_port"], api_key, session_dir, parse_timestamp(session["recording_started_at"]))
            session["export"] = {k: ex[k] for k in ("frames", "ui_events", "audio_chunks", "audio_files", "typed_chars")}
        except (ApiError, OSError, ValueError, KeyError) as exc:
            session["export_error"] = str(exc)
            say(f"WARNING: could not read the records back from the recorder: {exc}. The raw folder is kept; tell the study team.")
        say("Stopping the recorder ...")
        screenpipe.stop_recorder(proc)
        write_json(session_dir / "session.json", session)
    pause("Recording stopped. Press Enter for the questions")
    session["retrospective"] = retrospective()
    write_json(session_dir / "session.json", session)
    session["redactions"] = review_and_redact(session_dir, session)
    write_json(session_dir / "session.json", session)
    if ask_yes("Pack the session into a zip for upload now?", default=True):
        package_and_upload(cfg, session_dir)
    else:
        say(f"Not packed. Later:  python3 study.py package {session_dir.name}")
    return 0


# ---------- stand-alone commands ----------

def find_session(cfg: dict, name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.exists():
        return p
    q = sessions_root(cfg) / name_or_path
    if q.exists():
        return q
    raise SystemExit(f"session not found: {name_or_path}")


def cmd_package(cfg: dict, args) -> int:
    session_dir = find_session(cfg, args.session)
    package_and_upload(cfg, session_dir)
    return 0


def cmd_status(cfg: dict, args) -> int:
    session_dir = find_session(cfg, args.session)
    sess = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    say(f"{session_dir.name}: participant {sess.get('participant')}, family {sess.get('family')}, outcome {sess.get('outcome')}")
    print_stats(stats_from_export(session_dir) or screenpipe.capture_stats(session_dir / "screenpipe"))
    if sess.get("export_error"):
        say(f"  export error: {sess['export_error']}")
    return 0


def cmd_redact(cfg: dict, args) -> int:
    session_dir = find_session(cfg, args.session)
    sess_path = session_dir / "session.json"
    sess = json.loads(sess_path.read_text(encoding="utf-8")) if sess_path.exists() else {}
    if args.app:
        removed = redact_app(session_dir, args.app)
        sess.setdefault("redactions", []).append({"kind": "app", "match": args.app, "removed": removed, "at": iso_utc()})
    elif args.from_min is not None and args.to_min is not None:
        start = datetime.fromisoformat(sess["recording_started_at"]).timestamp()
        removed = redact_range(session_dir, start + args.from_min * 60, start + args.to_min * 60)
        sess.setdefault("redactions", []).append({"kind": "range", "from_min": args.from_min, "to_min": args.to_min, "removed": removed, "at": iso_utc()})
    else:
        say("give --app TEXT or --from-min N --to-min M")
        return 1
    write_json(sess_path, sess)
    say(f"removed: {removed}")
    return 0
