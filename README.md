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

## 2. The session (about 60 minutes)

```bash
python3 study.py run
```

The program walks you through: consent, your participant code, the task family you were given (or your own choice), the think-aloud instructions, then recording with a chime every 10 minutes. When the time is up, you come back to the terminal, answer a few typed questions, review what was recorded (you can delete any app or time range), and the program packs everything into one zip and opens the upload page.

Useful flags: `--participant P07 --family C` skips two prompts; `--no-nudge` turns off the halfway side-task reminder; `--minutes 30` shortens the session.

## 3. If something goes wrong

- **Stop early:** press Ctrl+C in the terminal and confirm. The recording so far is kept.
- **Pack later:** `python3 study.py package <session folder name>` (folders are in `sessions/`).
- **Delete something after the fact:** `python3 study.py redact <session> --app Slack` or `--from-min 12 --to-min 18`.
- **Delete everything:** remove the `sessions/` folder. If you already uploaded, email the contact in `study_config.json` and we delete it.
- **Look at what was captured:** `python3 study.py status <session>`.

## For researchers

- `study_config.json`: upload link, session length, checkpoint interval, deny list, pinned screenpipe version.
- `tasks/tasks.json`: the task families (A clear/routine, B choose among options, C plan something open, D explore an idea, E make something from sources) with their difficulty axes.
- `researcher/transcribe.py <session>`: transcribe the audio locally (whisper); `researcher/export_history.py <session>`: produce the model-readable histories (events only; events + views), the think-aloud transcript kept separate, and the checkpoint times. Details in [docs/RESEARCHER.md](docs/RESEARCHER.md).
- Tests: `python3 -m unittest discover tests`.

Session bundle layout:

```
sessions/P07_20261103-1402/
  session.json          participant, family, axes, task text, timing, screenpipe args, retrospective answers, redactions
  checkpoints.jsonl     when each chime fired (checkpoint / nudge / wrap / end)
  screenpipe/           db.sqlite (frames, ui_events, audio_chunks, ...) and data/<date>/*.jpg + audio chunks
  backup_audio.wav      whole-session microphone recording (if ffmpeg was available)
  screenpipe.log
  manifest.json         zip name, size, sha256
```

License: the kit is MIT. screenpipe is a separate program under its own license (free for personal, research, and educational use); the kit downloads it, it does not include it.
