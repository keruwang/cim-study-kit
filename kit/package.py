"""Pack a session folder into one zip, reveal it, open the upload page."""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import webbrowser
import zipfile
from pathlib import Path

# screenpipe internals that are not study data.
EXCLUDE_DIRS = {"pipes", "skills", "storage", "exports", "models", "cache"}
EXCLUDE_FILES = {"secrets.sqlite", "store.bin", ".storage.lock", "storage.json", "secrets.sqlite.quarantine.reserve.json",
                 "db.sqlite.owner", "db.sqlite-shm", ".DS_Store"}


def iter_session_files(session_dir: Path):
    for root, dirs, files in os.walk(session_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in files:
            if name in EXCLUDE_FILES or name.endswith(".zip"):
                continue
            p = Path(root) / name
            yield p, p.relative_to(session_dir)


def make_zip(session_dir: Path, out_path: Path | None = None) -> Path:
    out_path = out_path or session_dir.parent / f"{session_dir.name}.zip"
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, rel in iter_session_files(session_dir):
            zf.write(path, arcname=f"{session_dir.name}/{rel.as_posix()}")
    return out_path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reveal(path: Path) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        pass


def open_url(url: str) -> None:
    if url:
        try:
            webbrowser.open(url)
        except Exception:
            pass


def dir_size_mb(path: Path) -> float:
    total = sum(p.stat().st_size for p, _ in iter_session_files(path))
    return total / (1024 * 1024)
