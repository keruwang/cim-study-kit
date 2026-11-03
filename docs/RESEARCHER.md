# Researcher notes

## Before recruiting

1. **Upload folder.** Create a Google Drive folder (e.g. `CIM study uploads`), share it as *Anyone with the link → Editor* (participants need a Google account to drop files in) or create a Dropbox *file request* (no account needed). Put the link in `study_config.json` → `upload_url`. Zips are 100–600 MB per hour (screenshots dominate).
2. **Consent text.** Replace `CONSENT.md` with the approved text and fill in compensation. The program shows a plain-language summary and records agreement in `session.json`.
3. **Pin screenpipe.** `screenpipe_version` is pinned so every participant records with the same build. Re-run `python3 study.py setup` yourself after changing it.
4. **Assignment sheet.** Decide per participant: family letter (A–E) and nudge on/off. Tell them `python3 study.py run --participant P07 --family C` (add `--no-nudge` when off).
5. **Dry run on your own machine** end to end once: setup, a 5-minute `run --minutes 5`, transcribe, export.

## After a session arrives

```bash
unzip P07_20261103-1402.zip -d incoming/
python3 researcher/transcribe.py incoming/P07_20261103-1402          # whisper, local; writes derived/transcript.jsonl
python3 researcher/export_history.py incoming/P07_20261103-1402      # writes derived/history_events.txt, history_events_views.txt, think_aloud.txt, checkpoints.txt
```

- `history_events.txt` is the events-only condition (what the old accessibility daemon gave). `history_events_views.txt` adds what was on screen at focus / scroll-stop / idle captures. That pair is the input ablation.
- `think_aloud.txt` is the reference. It is never part of the model input.
- `checkpoints.txt` gives the exact times of the chimes; the participant's one-sentence answers are in the transcript right after each cue, and the spoken cue itself appears in the transcript as a marker.
- The screenshots stay in `screenpipe/data/<date>/*.jpg` for a replay (`npx screenpipe@0.4.55 export --data-dir <session>/screenpipe --start ... --end ...` makes an MP4 with synced audio) and for later screenshot-input conditions.
- `session.json` → `retrospective` holds the typed answers (final goal, could-say-at-start 1–5, change moments, what mattered, carried forward, side tasks, think-aloud effect, naturalness).

## Known limits

- screenpipe's CLI attaches macOS permissions to the terminal app the participant uses. If they switch terminal apps, permissions must be granted again.
- Typed text is recorded (`--disable-keyboard-capture false`). Participants are told not to type passwords; the deny list hides password managers; private browser windows are skipped.
- Windows: no backup WAV (screenpipe audio only); spoken cues use the built-in voice.
- The `redact` step deletes rows and files but the SQLite file is vacuumed, not securely wiped.
- Audio transcription is off on the participant's machine on purpose (no model download, less CPU). Transcribe on our side.
