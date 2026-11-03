"""Environment checks that must run before the window is opened."""
from __future__ import annotations

import platform
import subprocess
import sys

PYTHON_ORG = "https://www.python.org/downloads/"


def tk_check() -> tuple[bool, str]:
    """Open and close a Tk window in a child process. Apple's bundled Python (Tk 8.5) aborts on macOS 14 with
    'macOS 14 (1407) or later required'; a child process keeps that crash away from the kit."""
    try:
        proc = subprocess.run([sys.executable, "-c", "import tkinter; r = tkinter.Tk(); r.withdraw(); r.destroy(); print(tkinter.TkVersion)"],
                              capture_output=True, text=True, timeout=60)
    except Exception as exc:  # pragma: no cover
        return False, f"could not test the window toolkit: {exc}"
    if proc.returncode == 0:
        return True, f"Tk {proc.stdout.strip()}"
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = err[-1] if err else f"exit code {proc.returncode}"
    return False, detail


def tk_advice() -> str:
    if platform.system() == "Darwin":
        return (f"This Python cannot open windows ({sys.executable}). Install Python 3.11 or newer from {PYTHON_ORG}, "
                "open a NEW Terminal window, and run the command again (the python.org installer puts its python3 first). "
                "Homebrew users: brew install python-tk, then use that python3.")
    if platform.system() == "Linux":
        return "Install the Tk bindings for Python (Debian/Ubuntu: sudo apt install python3-tk) and run again."
    return f"Reinstall Python from {PYTHON_ORG} with the 'tcl/tk and IDLE' option ticked."
