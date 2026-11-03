"""Audible and visible cues that reach the participant while the terminal is in the background."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys

SYSTEM = platform.system()


def _spawn(args: list[str]) -> None:
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def chime() -> None:
    if SYSTEM == "Darwin":
        _spawn(["afplay", "/System/Library/Sounds/Glass.aiff"])
    elif SYSTEM == "Windows":
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    else:
        sys.stdout.write("\a")
        sys.stdout.flush()


def speak(text: str) -> None:
    """Spoken prompt. It is also picked up by the microphone, which marks the moment in the recording."""
    if SYSTEM == "Darwin":
        _spawn(["say", "-r", "175", text])
    elif SYSTEM == "Windows":
        safe = text.replace("'", "''")
        _spawn(["powershell", "-NoProfile", "-Command",
                f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"])
    elif shutil.which("spd-say"):
        _spawn(["spd-say", text])


def notification(title: str, body: str) -> None:
    if SYSTEM == "Darwin":
        t = title.replace('"', "'")
        b = body.replace('"', "'")
        _spawn(["osascript", "-e", f'display notification "{b}" with title "{t}"'])
    elif SYSTEM == "Windows":
        safe_t = title.replace("'", "''")
        safe_b = body.replace("'", "''")
        _spawn(["powershell", "-NoProfile", "-Command",
                "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
                f"$n.Visible=$true;$n.ShowBalloonTip(10000,'{safe_t}','{safe_b}',[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep 11"])
    elif shutil.which("notify-send"):
        _spawn(["notify-send", title, body])


def cue(title: str, spoken: str, body: str | None = None) -> None:
    chime()
    notification(title, body or spoken)
    speak(spoken)
