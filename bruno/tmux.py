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


def window_id() -> str | None:
    if not in_tmux():
        return None
    out = _tmux("display-message", "-p", "#{window_id}")
    return out.strip() if out else None


def current_pane_id() -> str | None:
    if not in_tmux():
        return None
    env_pane = os.environ.get("TMUX_PANE")
    if env_pane:
        return env_pane
    out = _tmux("display-message", "-p", "#{pane_id}")
    return out.strip() if out else None


def window_pane_layout() -> list[dict]:
    """Geometry of every pane in the current tmux window.

    Each entry: {pane_id, left, top, width, height}. Left/top are the
    window-relative cell coordinates of the pane's top-left, matching
    what bruno needs for adjacency math.
    """
    if not in_tmux():
        return []
    out = _tmux(
        "list-panes", "-F",
        "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height}",
    )
    if not out:
        return []
    panes = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            panes.append({
                "pane_id": parts[0],
                "left": int(parts[1]),
                "top": int(parts[2]),
                "width": int(parts[3]),
                "height": int(parts[4]),
            })
        except ValueError:
            pass
    return panes


def find_pane(layout: list[dict], pane_id: str) -> dict | None:
    for p in layout:
        if p["pane_id"] == pane_id:
            return p
    return None


def neighbor_pane(layout: list[dict], my_pane_id: str,
                  dx: int, dy: int,
                  local_exit_x: int, local_exit_y: int,
                  sprite_w: int, sprite_h: int) -> tuple[dict, int, int] | None:
    """Find which pane bruno would enter when he walks off my edge.

    `local_exit_x`/`local_exit_y` are bruno's anchor in my pane's local
    coords at the moment of attempted exit (the would-be next position,
    still using my-pane origin). Returns (neighbor_pane, entry_local_x,
    entry_local_y) for the receiver, or None if no pane sits in that
    direction.

    Adjacent panes in tmux share an edge across a 1-cell divider, so we
    nudge across the divider when looking up the neighbor. The receiver
    enters at the opposite edge in the same row/column band where bruno
    left, clamped to the receiver's bounds.
    """
    me = find_pane(layout, my_pane_id)
    if me is None:
        return None
    abs_x = me["left"] + local_exit_x
    abs_y = me["top"] + local_exit_y
    if dx > 0:
        probe_x = me["left"] + me["width"] + 1
        probe_y = abs_y
    elif dx < 0:
        probe_x = me["left"] - 2
        probe_y = abs_y
    elif dy > 0:
        probe_x = abs_x
        probe_y = me["top"] + me["height"] + 1
    elif dy < 0:
        probe_x = abs_x
        probe_y = me["top"] - 2
    else:
        return None
    for p in layout:
        if p["pane_id"] == my_pane_id:
            continue
        if not (p["left"] <= probe_x < p["left"] + p["width"]
                and p["top"] <= probe_y < p["top"] + p["height"]):
            continue
        if dx > 0:
            entry_x = 0
            entry_y = max(0, min(p["height"] - sprite_h, probe_y - p["top"]))
        elif dx < 0:
            entry_x = max(0, p["width"] - sprite_w)
            entry_y = max(0, min(p["height"] - sprite_h, probe_y - p["top"]))
        elif dy > 0:
            entry_x = max(0, min(p["width"] - sprite_w, probe_x - p["left"]))
            entry_y = 0
        else:
            entry_x = max(0, min(p["width"] - sprite_w, probe_x - p["left"]))
            entry_y = max(0, p["height"] - sprite_h)
        return p, entry_x, entry_y
    return None


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
