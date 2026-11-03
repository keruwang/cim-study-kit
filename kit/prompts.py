"""All participant-facing text in one place, so wording changes do not touch the flow."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = ROOT / "tasks" / "tasks.json"


def load_tasks(path: Path | None = None) -> dict:
    with open(path or TASKS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


BANNER = """
==========================================================
  {study_name}
==========================================================
This program records ONE work session on this computer:
  - what is on your screen (screenshots + the text on them)
  - what you do (clicks, app switches, scrolling, typed text)
  - your voice, because we ask you to think aloud
It also keeps time and plays a short chime at checkpoints.
Nothing is uploaded automatically. At the end you review a
summary, can delete parts, and upload the zip yourself.
"""

CONSENT_SUMMARY = """
WHAT WE RECORD, IN PLAIN WORDS
  Screen: a screenshot plus the text on screen whenever something changes
          (app switch, click, scroll, typing pause) and at least every
          {idle_s} seconds. All monitors.
  Actions: clicks (with the name of the clicked element), app and window
          switches, scrolling, and the text you type. Typed text IS recorded,
          so do not type passwords or card numbers during the session.
  Audio:  your microphone, the whole session. We transcribe it later.
  Never:  windows of {ignored}. Private/incognito browser windows are skipped.

WHAT HAPPENS TO IT
  The recording stays on this computer until you upload the zip yourself.
  Before that, you see a summary of the apps and websites recorded and can
  delete any app or time range. After upload, only the study team can read
  it; it is used to study how people's goals take shape while they work.
  You can stop at any time with the "End session now" button and choose
  not to upload. The raw recording folder stays on this computer until you
  delete it.

Full text: CONSENT.md in this folder. Questions: {contact}.
"""

THINK_ALOUD = """
THINKING ALOUD
  While you work, say what goes through your head, as it happens:
  what you are looking at, what you are trying to do, what you are
  unsure about, when you change your mind, when you give up on something,
  when you come back to something. Short fragments are fine.
  Do not explain or justify yourself for us; just keep talking.
  If you go quiet for a while, that is OK; resume when you notice.

CHECKPOINTS
  Every {every} minutes you will hear a chime and a short spoken prompt.
  When you hear it, say ONE sentence:
     "Right now I am trying to ... and I am [sure / fairly sure / not sure]."
  Then carry on. Do not stop working.

ENDING
  At {minutes} minutes you hear "time is up". Finish your thought and
  come back to the study window. A few typed questions follow (about
  5 minutes), then the review and the upload.
"""

TASK_INTRO = """
YOUR TASK
  Family {code}: {name}
  {one_line}
  {guidance}
  Examples of the kind of thing we mean:
{examples}
  Please avoid:
{avoid}
"""

NUDGE_INTRO = """
SMALL SIDE TASKS (optional)
  Real work gets interrupted. If you have one or two small, real things
  you also need to do today (reply to a message, look something up,
  send a file), list them now. About halfway through you will hear a
  prompt reminding you that you may handle them whenever it suits you.
  You may also ignore the prompt entirely.
"""

CHECKPOINT_SPOKEN = "Checkpoint. In one sentence, say what you are trying to do right now, and how sure you are."
CHECKPOINT_TITLE = "Checkpoint {n}"
NUDGE_SPOKEN = "Reminder: if you listed a small side task, you may handle it whenever it suits you. Or ignore this."
START_SPOKEN = "Recording has started. Please begin, and remember to think aloud."
WRAP_UP_SPOKEN = "Two minutes left. Start wrapping up your thought."
END_SPOKEN = "Time is up. Please finish your sentence, then come back to the study window."

# Retrospective questions. kind: text | int1to5 | yesno | list
RETROSPECTIVE = [
    {"key": "final_goal", "kind": "text",
     "q": "In your own words: by the end, what were you trying to do? (one or two sentences)"},
    {"key": "could_say_at_start", "kind": "int1to5",
     "q": "Could you have stated that goal at the START of the session? 1 = not at all, 5 = yes, exactly this"},
    {"key": "changes", "kind": "list",
     "q": "Moments where what you were trying to do changed. For each, one line: roughly when (minutes in), what changed, and why. Enter an empty line when done"},
    {"key": "mattered", "kind": "list",
     "q": "Which pages, documents, or messages you saw turned out to matter most to where you ended up? Up to five, one per line. Empty line when done"},
    {"key": "carried_forward", "kind": "text",
     "q": "Was there anything you saw early that stayed in your mind and shaped later steps (a constraint, an option you rejected, a price, a date)? Describe briefly, or leave empty"},
    {"key": "side_tasks_done", "kind": "text",
     "q": "If you did any side tasks, which, and roughly when? Leave empty if none"},
    {"key": "think_aloud_effect", "kind": "int1to5",
     "q": "Did thinking aloud change how you worked? 1 = not at all, 5 = a lot"},
    {"key": "naturalness", "kind": "int1to5",
     "q": "How close was this to how you would have done this task on your own? 1 = very different, 5 = the same"},
    {"key": "comments", "kind": "text",
     "q": "Anything else (problems, confusion, what you would tell us)? Leave empty to skip"},
]

REVIEW_INTRO = """
REVIEW BEFORE UPLOAD
  Below is what was recorded: the apps, the most common websites, and counts.
  You can delete everything from one app, or everything in a time range.
  Deleting removes the screenshots, screen text, input events, and audio
  for that part from the bundle.
"""

UPLOAD_INSTRUCTIONS = """
UPLOAD
  The session is packed into one zip file:
    {zip_path}
  Size: {size_mb:.1f} MB   SHA-256: {sha}
  1. A browser window opens at the upload folder (or open this link): {url}
  2. Drag the zip file into it (the file is also revealed in your file browser).
  3. Tell the study team the file name. Keep the local copy until they confirm.
  Thank you.
"""
