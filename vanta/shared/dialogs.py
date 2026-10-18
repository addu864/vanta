"""Native pick-a-file / pick-a-folder dialogs for macOS and Linux.

Windows uses the PowerShell dialogs in app.py. On macOS this asks AppleScript
(`osascript`), which is always present. On Linux it uses zenity or kdialog when
installed. Returns (path, error); path is "" when nothing was chosen.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Callable

IMAGE_TYPES = ("png", "jpg", "jpeg", "webp", "gif", "bmp")


def _mac_command(kind: str, prompt: str) -> list[str]:
    safe = prompt.replace('"', "'")
    if kind == "folder":
        script = f'POSIX path of (choose folder with prompt "{safe}")'
    else:
        types = ", ".join(f'"{t}"' for t in IMAGE_TYPES)
        script = f'POSIX path of (choose file with prompt "{safe}" of type {{{types}}})'
    return ["osascript", "-e", script]


def _linux_command(kind: str, prompt: str, which: Callable[[str], str | None]) -> list[str] | None:
    if which("zenity"):
        cmd = ["zenity", "--file-selection", f"--title={prompt}"]
        if kind == "folder":
            cmd.append("--directory")
        else:
            cmd.append("--file-filter=Images | " + " ".join(f"*.{t}" for t in IMAGE_TYPES))
        return cmd
    if which("kdialog"):
        if kind == "folder":
            return ["kdialog", "--getexistingdirectory", ".", "--title", prompt]
        return ["kdialog", "--getopenfilename", ".", " ".join(f"*.{t}" for t in IMAGE_TYPES), "--title", prompt]
    return None


def pick(kind: str, prompt: str, *, platform: str | None = None,
         runner: Callable[..., subprocess.CompletedProcess] | None = None,
         which: Callable[[str], str | None] | None = None) -> tuple[str, str]:
    platform = platform or sys.platform
    runner = runner or subprocess.run
    which = which or shutil.which
    what = "folder" if kind == "folder" else "image"
    if platform == "darwin":
        cmd = _mac_command(kind, prompt)
    elif platform.startswith("linux"):
        cmd = _linux_command(kind, prompt, which)
        if cmd is None:
            return "", f"No {what} dialog on this system (install zenity). Nothing was changed."
    else:
        return "", f"No {what} dialog on this system. Nothing was changed."
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"The {what} dialog did not open: {exc}"
    lines = [line.strip() for line in (done.stdout or "").splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        return "", f"No {what} was selected."
    path = lines[-1]
    if kind == "folder" and len(path) > 1:
        path = path.rstrip("/")
    return path, ""
