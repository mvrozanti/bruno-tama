"""Lightweight tmux awareness.

Bruno can run in any terminal, but when he's inside tmux he gets extra
context: pane geometry from tmux itself, and (optionally) snapshots of
sibling panes so he can react to what's happening around him.
"""
from __future__ import annotations
import os
import shutil
import subprocess


def in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _tmux(*args: str) -> str | None:
    if not shutil.which("tmux"):
        return None
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True, text=True, check=True, timeout=1.0,
        )
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def pane_size() -> tuple[int, int] | None:
    """(width, height) of the current pane per tmux. None if not in tmux."""
    if not in_tmux():
        return None
    out = _tmux("display-message", "-p", "#{pane_width} #{pane_height}")
    if not out:
        return None
    try:
        w, h = out.strip().split()
        return int(w), int(h)
    except ValueError:
        return None


def sibling_panes() -> list[str]:
    """Pane IDs of every pane in the current window except this one."""
    if not in_tmux():
        return []
    out = _tmux("list-panes", "-F", "#{pane_id} #{pane_active}")
    if not out:
        return []
    panes = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "0":
            panes.append(parts[0])
    return panes


def capture_pane(pane_id: str, lines: int = 50) -> str | None:
    """Snapshot a pane's visible content. Bruno can sniff for keywords."""
    out = _tmux("capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}")
    return out


def activity_signal() -> str | None:
    """Crude vibe-check on sibling panes. Returns a one-word signal or None.

    Returns 'busy' if there's a lot of recent text, 'angry' on visible errors,
    'code' if it looks like an editor, 'quiet' if everything is empty.
    """
    panes = sibling_panes()
    if not panes:
        return None
    total_text = ""
    for pid in panes[:4]:
        snap = capture_pane(pid, lines=30)
        if snap:
            total_text += "\n" + snap
    if not total_text.strip():
        return "quiet"
    lower = total_text.lower()
    if any(w in lower for w in ("error", "traceback", "panic", "fatal", "failed")):
        return "angry"
    if any(w in lower for w in ("def ", "function ", "import ", "fn ", "class ")):
        return "code"
    nonempty_lines = sum(1 for line in total_text.splitlines() if line.strip())
    if nonempty_lines > 60:
        return "busy"
    return None
