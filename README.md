# Study kit: one think-aloud computer-work session

This records **one session of about an hour** of your own computer work for a research study on how people's goals take shape while they work. It needs about five minutes to set up.

What it records: screenshots and on-screen text, your clicks and typed text, app switches, and your voice (we ask you to think aloud). Nothing leaves your computer until you upload the file yourself. Full details: [CONSENT.md](CONSENT.md).

## 1. Before the session (5 minutes)

You need **Python 3.9+** and **Node.js 18+** (the recorder, [screenpipe](https://github.com/screenpipe/screenpipe), is fetched through `npx`).

- macOS: Python is included (if `python3` asks to install developer tools, accept). Node: https://nodejs.org (LTS).
- Windows: Python from https://python.org (tick "Add to PATH"), Node from https://nodejs.org.

Then, in a terminal:

```bash
git clone https://github.com/keruwang/cim-study-kit.git
cd cim-study-kit
python3 study.py setup
```

`setup` checks permissions and does a 60-second test recording. On macOS it will tell you to allow your **terminal app** (Terminal, iTerm, …) under *System Settings → Privacy & Security* for **Screen & System Audio Recording**, **Microphone**, and **Accessibility**, then quit and reopen the terminal and run `setup` again. Repeat until it prints `Setup OK`.

If the screenpipe desktop app is installed, quit it first.

The silence reminder needs `ffmpeg` to hear the microphone (macOS: `brew install ffmpeg`). Without it the kit reminds you at a fixed interval instead; `setup` tells you which.

## 2. The session (about 60 minutes)

```bash
python3 study.py run
```

A small window opens and stays on top of your other windows. It walks you through: consent, your participant code, the task family you were given, and the think-aloud instructions. When you press Start it shrinks to a small timer in the bottom-right corner of the screen and stays out of the way. Just work and keep saying what you are thinking. If you go quiet for about five minutes, a chime and a short voice line remind you to think aloud; nothing else interrupts you until "two minutes left" and "time is up". Then the window comes back, asks you for a short written summary (your goals, what you did, what you might do next), shows what was recorded (you can delete any app or time range), packs everything into one zip, and opens the upload page.

Press **End** on the timer to stop early. Use the **Test chime and voice** button on the first page to check that you can hear the cues. The small "mic ▮▮▯▯" meter on the timer shows how loud you are relative to the room and turns green while the kit hears you talking; if it never turns green while you speak, tell us.

Useful flags: `--participant P07 --family C` pre-fills two fields; `--no-nudge` turns off the halfway side-task reminder; `--minutes 30` shortens the session; `--checkpoint-every 10` adds fixed spoken checkpoints every 10 minutes (off by default); `--demo` lets you click through the whole flow in about two minutes with nothing recorded; `--cli` uses a terminal-only flow instead of the window.

## 3. If something goes wrong

- **Stop early:** press **End session now** in the window (or Ctrl+C in the terminal flow) and confirm. The recording so far is kept.
- **Pack later:** `python3 study.py package <session folder name>` (folders are in `sessions/`).
- **Delete something after the fact:** `python3 study.py redact <session> --app Slack` or `--from-min 12 --to-min 18`.
- **Delete everything:** remove the `sessions/` folder. If you already uploaded, email the contact in `study_config.json` and we delete it.
- **Look at what was captured:** `python3 study.py status <session>`.

## For researchers

- `study_config.json`: upload link, session length, silence reminder (minutes without voice; `voice_margin_db` above the room's noise floor and `voice_min_zcr` zero-crossing rate decide what counts as voice; fixed-interval fallback without ffmpeg), optional checkpoints, deny list, pinned screenpipe version.
- `tasks/tasks.json`: the task families (A clear/routine, B choose among options, C plan something open, D explore an idea, E make something from sources) with their difficulty axes.
- `researcher/transcribe.py <session>`: transcribe the audio locally (whisper); `researcher/export_history.py <session>`: produce the model-readable histories (events only; events + views), the think-aloud transcript kept separate, and the checkpoint times. Details in [docs/RESEARCHER.md](docs/RESEARCHER.md).
- Tests: `python3 -m unittest discover tests`.

Session bundle layout:

```
sessions/P07_20261103-1402/
  session.json          participant, family, axes, task text, timing, screenpipe args, the written summary (retrospective.summary), redactions
  checkpoints.jsonl     when each cue fired (silence reminder with seconds of silence / nudge / wrap / end / optional checkpoint)
  export/               ui_events.jsonl (every input event), frames.jsonl (every screenshot: app, window, url,
                        trigger, screen text, snapshot path), audio_chunks.jsonl, summary.json; read back from the
                        recorder's local API at the end of the session (screenpipe 0.4.5x archives text into parquet
                        files that only the running daemon can read)
  screenpipe/data/      the screenshots (<date>/*.jpg) and the 30-s audio files the export points to
  backup_audio.wav      whole-session microphone recording from the ffmpeg monitor (if ffmpeg was available); session.json "mic" has voiced seconds
  screenpipe.log
  manifest.json         zip name, size, sha256
  (screenpipe/storage/, the recorder's own index and parquet payloads, stays on the participant's machine)
```

License: the kit is MIT. screenpipe is a separate program under its own license (free for personal, research, and educational use); the kit downloads it, it does not include it.
