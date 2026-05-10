"""ANSI terminal rendering.

Bruno owns the whole pane. We:
  - clear on entry / restore cursor + visibility on exit
  - re-render only the bounding box that bruno occupied last frame plus the
    bounding box he occupies now (paint-the-diff to keep it flicker-free)
  - draw a speech bubble next to him when there's lateral room

Everything is plain ANSI escapes so this works in tmux, kitty, gnome-term, etc.
"""
from __future__ import annotations
import os
import sys
from contextlib import contextmanager

ESC = "\x1b"
CSI = ESC + "["
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"
CLEAR_SCREEN = CSI + "2J"
HOME = CSI + "H"
RESET = CSI + "0m"
ALT_BUF_ON = CSI + "?1049h"
ALT_BUF_OFF = CSI + "?1049l"

DIM = CSI + "2m"
BOLD = CSI + "1m"


def _move_to(row: int, col: int) -> str:
    """Move cursor to (row, col), 1-indexed."""
    return f"{CSI}{row};{col}H"


@contextmanager
def screen():
    """Context manager that owns the pane: alt buffer, hidden cursor, clean exit."""
    sys.stdout.write(ALT_BUF_ON)
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.write(CLEAR_SCREEN)
    sys.stdout.write(HOME)
    sys.stdout.flush()
    try:
        yield
    finally:
        sys.stdout.write(RESET)
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.write(ALT_BUF_OFF)
        sys.stdout.flush()


def term_size() -> tuple[int, int]:
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return (
            int(os.environ.get("COLUMNS", "80")),
            int(os.environ.get("LINES", "24")),
        )


class Painter:
    """Tracks last frame's footprint so we can erase only the cells that changed."""

    def __init__(self) -> None:
        self._last_cells: set[tuple[int, int]] = set()

    def paint(self, sprite_lines: list[str], x: int, y: int,
              bubble_lines: list[str] | None, bx: int, by: int) -> None:
        new_cells: set[tuple[int, int]] = set()
        out: list[str] = []

        # Compose all draw operations
        for dy, line in enumerate(sprite_lines):
            for dx, ch in enumerate(line):
                col = x + dx
                row = y + dy
                if ch == " ":
                    continue
                new_cells.add((row, col))
                out.append(_move_to(row + 1, col + 1) + ch)

        if bubble_lines:
            for dy, line in enumerate(bubble_lines):
                for dx, ch in enumerate(line):
                    col = bx + dx
                    row = by + dy
                    if ch == " ":
                        continue
                    new_cells.add((row, col))
                    out.append(_move_to(row + 1, col + 1) + DIM + ch + RESET)

        # Erase cells from last frame that aren't in this frame
        stale = self._last_cells - new_cells
        for row, col in stale:
            out.append(_move_to(row + 1, col + 1) + " ")

        out.append(RESET)
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._last_cells = new_cells

    def clear(self) -> None:
        if not self._last_cells:
            return
        out = [_move_to(row + 1, col + 1) + " " for row, col in self._last_cells]
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._last_cells.clear()
