#!/usr/bin/env python3
"""Study kit entry point. Run `python3 study.py setup` once, then `python3 study.py run`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kit import session as session_mod  # noqa: E402
from kit.config import load_config  # noqa: E402


def main(argv=None) -> int:
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.")
        return 1
    parser = argparse.ArgumentParser(prog="study.py", description="Record one think-aloud computer-work session.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="check screenpipe, permissions, and do a 60-second dry run")
    p.add_argument("--no-dry-run", action="store_true")

    p = sub.add_parser("run", help="run a full session")
    p.add_argument("--participant", help="participant code")
    p.add_argument("--family", help="task family letter (A-E)")
    p.add_argument("--minutes", type=int, help="override session length")
    p.add_argument("--no-nudge", action="store_true", help="no side-task reminder")
    p.add_argument("--skip-checks", action="store_true")

    p = sub.add_parser("package", help="zip a session folder and show upload instructions")
    p.add_argument("session")

    p = sub.add_parser("status", help="show what a session folder contains")
    p.add_argument("session")

    p = sub.add_parser("redact", help="delete an app or a time range from a session")
    p.add_argument("session")
    p.add_argument("--app")
    p.add_argument("--from-min", type=float)
    p.add_argument("--to-min", type=float)

    args = parser.parse_args(argv)
    cfg = load_config()
    try:
        if args.cmd == "setup":
            return session_mod.run_setup(cfg, args)
        if args.cmd == "run":
            return session_mod.run_session(cfg, args)
        if args.cmd == "package":
            return session_mod.cmd_package(cfg, args)
        if args.cmd == "status":
            return session_mod.cmd_status(cfg, args)
        if args.cmd == "redact":
            return session_mod.cmd_redact(cfg, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
