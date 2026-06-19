"""Overlay mode: bruno lives ON TOP of your shell.

We fork a PTY, run $SHELL in it, pass user keystrokes through, mirror shell
output to the real terminal AND to a pyte virtual screen. Each tick we
re-render bruno on top, restricted to cells pyte reports as empty so he
walks around your prompt and output.

The compositing trick is `ESC 7` (save cursor + attrs) before our overlay
draw and `ESC 8` (restore) after, so the shell never notices we drew. The
shell may overwrite cells bruno was on; we just re-overlay on the next tick.
"""
from __future__ import annotations

import errno
import fcntl
import os
import pty
import pwd
import re
import select
import shutil
import signal
import struct
import sys
import termios
import time
import tty

import pyte

from . import llm as llm_mod
from . import (coord, feed as feed_mod, food, mouse as mouse_mod, say,
               shellhook, sprites, state, tmux)
from .creature import Bruno

ESC = "\x1b"
SAVE_CUR = ESC + "7"
RESTORE_CUR = ESC + "8"
RESET_SGR = ESC + "[0m"
DIM_SGR = ESC + "[2m"
SHOW_CURSOR = ESC + "[?25h"
HIDE_CURSOR = ESC + "[?25l"

TICK_HZ = 10
SPEECH_CHANCE_PER_TICK = 0.005
# Skip pyte parsing for reads bigger than this. Yazi/icat graphics
# blobs run ~130 KB+ for a typical preview; full-screen TUI redraws
# (claude, htop, lazygit) fit under ~64 KB. The threshold sits in
# between so we still parse cell-level updates from TUIs — without
# that, bruno's occupancy view goes stale and he walks over UI cells
# the host then repaints, leaving visible trails.
PYTE_FEED_MAX = 65536


def _strip_kitty_apc(data: bytes) -> bytes:
    """Drop `\\e_G…\\e\\\\` kitty-graphics APC replies from a stdin batch.

    Crush (and other charmbracelet TUIs) probe kitty graphics support
    via tmux DCS passthrough. When the host terminal IS kitty, it
    replies with `\\e_Gi=<n>;OK\\e\\\\`. Without bruno that reply never
    reaches the inner pane — tmux drops it — so crush times out and
    runs without graphics. With bruno wrapping the shell, the reply
    DOES reach our stdin (tmux delivers it to the pane we own), we
    forward it to the inner PTY, and crush is now past its probe
    window: the bytes land in its main input buffer as `Gi=31;OK`.
    Strip the APC reply on the way through so the inner TUI behaves
    the same as it does without bruno.
    """
    if b"\x1b_G" not in data:
        return data
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if i + 1 < n and data[i] == 0x1b and data[i + 1] == 0x5f \
                and i + 2 < n and data[i + 2] == 0x47:
            j = i + 3
            while j + 1 < n and not (data[j] == 0x1b and data[j + 1] == 0x5c):
                j += 1
            if j + 1 < n:
                i = j + 2
                continue
            break
        out.append(data[i])
        i += 1
    return bytes(out)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _term_size() -> tuple[int, int]:
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return (
            int(os.environ.get("COLUMNS", "80")),
            int(os.environ.get("LINES", "24")),
        )


def _move_to(row: int, col: int) -> str:
    return f"{ESC}[{row + 1};{col + 1}H"


def _cell_char(screen: pyte.Screen, x: int, y: int) -> str:
    if y < 0 or y >= screen.lines or x < 0 or x >= screen.columns:
        return " "
    ch = screen.buffer[y][x].data
    if not ch:
        return " "
    return ch


def _cell_empty(screen: pyte.Screen, x: int, y: int) -> bool:
    return _cell_char(screen, x, y) == " "


_NAMED_FG = {
    "black": "30", "red": "31", "green": "32", "brown": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    "default": "39",
    "brightblack": "90", "brightred": "91", "brightgreen": "92",
    "brightbrown": "93", "brightblue": "94", "brightmagenta": "95",
    "brightcyan": "96", "brightwhite": "97",
}
_NAMED_BG = {
    "black": "40", "red": "41", "green": "42", "brown": "43",
    "blue": "44", "magenta": "45", "cyan": "46", "white": "47",
    "default": "49",
    "brightblack": "100", "brightred": "101", "brightgreen": "102",
    "brightbrown": "103", "brightblue": "104", "brightmagenta": "105",
    "brightcyan": "106", "brightwhite": "107",
}


def _color_sgr(color: str, fg: bool) -> str | None:
    """Convert a pyte color name or 6-hex string to an SGR fragment."""
    if not color or color == "default":
        return "39" if fg else "49"
    table = _NAMED_FG if fg else _NAMED_BG
    if color in table:
        return table[color]
    if len(color) == 6:
        try:
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
            return f"{'38' if fg else '48'};2;{r};{g};{b}"
        except ValueError:
            return None
    return None


def _cell_paint(screen: pyte.Screen, x: int, y: int) -> str:
    """Return `<SGR><char>` so a restored cell keeps its fg/bg/attrs.

    Without this, repainting a cell bruno used to occupy emits the
    character with the SGR state that happened to be active — typically
    the renderer's RESET_SGR — and erases the prompt's coloring at that
    position. Read pyte's attribute view of the cell and emit a matching
    SGR before the character.
    """
    if y < 0 or y >= screen.lines or x < 0 or x >= screen.columns:
        return RESET_SGR + " "
    cell = screen.buffer[y][x]
    ch = cell.data or " "
    parts = ["0"]
    if cell.bold:
        parts.append("1")
    if cell.italics:
        parts.append("3")
    # Restore underline only on non-blank cells. Underlined spaces (TUI
    # header bars in claude, mc, etc.) render as visible underscore
    # trails when bruno vacates; underlined real characters render
    # correctly and matter for the user's content (markdown headers,
    # mc highlights). Gate on cell.data.
    if cell.underscore and ch != " ":
        parts.append("4")
    if cell.blink:
        parts.append("5")
    if cell.reverse:
        parts.append("7")
    if cell.strikethrough:
        parts.append("9")
    fg = _color_sgr(cell.fg, fg=True)
    if fg and fg != "39":
        parts.append(fg)
    bg = _color_sgr(cell.bg, fg=False)
    if bg and bg != "49":
        parts.append(bg)
    return f"{ESC}[{';'.join(parts)}m{ch}"


def _can_place(screen: pyte.Screen, sprite_lines: list[str], x: int, y: int,
               cols: int, rows: int) -> bool:
    if x < 0 or y < 0:
        return False
    h = len(sprite_lines)
    w = max((len(line) for line in sprite_lines), default=0)
    if x + w > cols or y + h > rows:
        return False
    for dy, line in enumerate(sprite_lines):
        for dx, ch in enumerate(line):
            if ch == " ":
                continue
            if not _cell_empty(screen, x + dx, y + dy):
                return False
    return True


class Compositor:
    """Renders bruno on top, restoring underlying shell content when he moves."""

    def __init__(self, stdout_fd: int, screen: pyte.Screen, debug=None):
        self.stdout_fd = stdout_fd
        self.screen = screen
        # Real-terminal bounds. Identical to the pyte screen's dims in
        # free-roam, larger while docked (the child PTY shrinks but the
        # terminal doesn't) — draw clipping must use these, not
        # screen.lines, or the dock strip becomes unreachable.
        self.term_rows = screen.lines
        self.term_cols = screen.columns
        self._last_cells: set[tuple[int, int]] = set()
        self._last_bubble_cells: set[tuple[int, int]] = set()
        self._last_particle_cells: set[tuple[int, int]] = set()
        self._debug = debug

    def render(self, sprite_lines: list[str], x: int, y: int,
               bubble_lines: list[str] | None, bx: int, by: int,
               particle_cells: list[tuple[int, int, str, str | None]] | None = None) -> None:
        new_cells: set[tuple[int, int]] = set()
        new_bubble_cells: set[tuple[int, int]] = set()
        new_particle_cells: set[tuple[int, int]] = set()
        # Don't HIDE_CURSOR here — it has no paired SHOW, so the cursor
        # stays invisible across long-running shell output. A 1-frame
        # cursor flicker on bruno's cell is invisible; a permanently
        # hidden cursor is reported as "the terminal is broken."
        out: list[str] = [SAVE_CUR, RESET_SGR]

        for dy, line in enumerate(sprite_lines):
            for dx, ch in enumerate(line):
                if ch == " ":
                    continue
                row = y + dy
                col = x + dx
                if row < 0 or row >= self.term_rows:
                    continue
                if col < 0 or col >= self.term_cols:
                    continue
                new_cells.add((row, col))
                out.append(_move_to(row, col) + ch)

        if bubble_lines:
            for dy, line in enumerate(bubble_lines):
                for dx, ch in enumerate(line):
                    if ch == " ":
                        continue
                    row = by + dy
                    col = bx + dx
                    if row < 0 or row >= self.term_rows:
                        continue
                    if col < 0 or col >= self.term_cols:
                        continue
                    new_bubble_cells.add((row, col))
                    out.append(_move_to(row, col) + DIM_SGR + ch + RESET_SGR)

        if particle_cells:
            for row, col, ch, sgr in particle_cells:
                if ch == " ":
                    continue
                if row < 0 or row >= self.term_rows:
                    continue
                if col < 0 or col >= self.term_cols:
                    continue
                if (row, col) in new_cells or (row, col) in new_bubble_cells:
                    continue
                new_particle_cells.add((row, col))
                prefix = sgr if sgr else ""
                out.append(_move_to(row, col) + prefix + ch + RESET_SGR)

        # Restore cells we owned last tick that we don't own this tick.
        # Use whatever pyte reports for the underlying shell content,
        # including its SGR state — otherwise we strip the prompt's
        # colors when bruno walks off them.
        all_old = self._last_cells | self._last_bubble_cells | self._last_particle_cells
        all_new = new_cells | new_bubble_cells | new_particle_cells
        stale = all_old - all_new
        for row, col in stale:
            out.append(_move_to(row, col) + _cell_paint(self.screen, col, row))

        out.append(RESET_SGR + RESTORE_CUR)
        if self._debug:
            stale_paint = {
                (r, c): repr(_cell_paint(self.screen, c, r))
                for r, c in sorted(stale)
            }
            self._debug.write(
                f"render: pyte={self.screen.columns}x{self.screen.lines} "
                f"new={sorted(new_cells)} stale={sorted(stale)} "
                f"stale_paint={stale_paint} bruno_xy=({x},{y})\n"
            )
        # Don't re-show cursor here — the user's shell controls visibility.
        os.write(self.stdout_fd, "".join(out).encode("utf-8", errors="replace"))
        self._last_cells = new_cells
        self._last_bubble_cells = new_bubble_cells
        self._last_particle_cells = new_particle_cells

    def clear(self) -> None:
        all_old = self._last_cells | self._last_bubble_cells | self._last_particle_cells
        if not all_old:
            return
        out = [SAVE_CUR]
        for row, col in all_old:
            out.append(_move_to(row, col) + _cell_paint(self.screen, col, row))
        out.append(RESET_SGR + RESTORE_CUR)
        try:
            os.write(self.stdout_fd, "".join(out).encode("utf-8", errors="replace"))
        except OSError:
            pass
        self._last_cells.clear()
        self._last_bubble_cells.clear()
        self._last_particle_cells.clear()

    def handle_scroll(self, delta: int, rows: int) -> None:
        all_old = self._last_cells | self._last_bubble_cells | self._last_particle_cells
        if not all_old:
            return
        out = [SAVE_CUR]
        wrote = False
        for row, col in all_old:
            new_row = row - delta
            if new_row < 0 or new_row >= rows:
                continue
            out.append(_move_to(new_row, col) + _cell_paint(self.screen, col, new_row))
            wrote = True
        out.append(RESET_SGR + RESTORE_CUR)
        if wrote:
            try:
                os.write(self.stdout_fd, "".join(out).encode("utf-8", errors="replace"))
            except OSError:
                pass
        self._last_cells.clear()
        self._last_bubble_cells.clear()
        self._last_particle_cells.clear()


def _bubble_position(bruno_x: int, bruno_y: int, bruno_w: int,
                     cols: int, rows: int, text: str,
                     screen: pyte.Screen) -> tuple[list[str], int, int] | None:
    """Place a bubble alongside bruno, only over empty shell cells.

    Returns None if no clear placement fits.
    """
    if cols < 14 or rows < 4:
        return None
    right_room = cols - (bruno_x + bruno_w) - 1
    left_room = bruno_x - 1
    candidates = []
    if right_room >= 6:
        lines = say.bubble(text, max_width=min(28, right_room), tail="left")
        if lines:
            bx = bruno_x + bruno_w + 1
            by = max(0, bruno_y - (len(lines) - 2))
            candidates.append((lines, bx, by))
    if left_room >= 6:
        lines = say.bubble(text, max_width=min(28, left_room), tail="right")
        if lines:
            bx = max(0, bruno_x - len(lines[0]) - 1)
            by = max(0, bruno_y - (len(lines) - 2))
            candidates.append((lines, bx, by))
    for lines, bx, by in candidates:
        if by + len(lines) > rows:
            continue
        if all(_cell_empty(screen, bx + dx, by + dy)
               for dy, line in enumerate(lines)
               for dx, ch in enumerate(line) if ch != " "):
            return lines, bx, by
    return None


class _ScrollTrackingScreen(pyte.Screen):
    """Pyte screen that records vertical scroll deltas.

    When the shell prints past the bottom row, the real terminal scrolls
    too — so any cells we drew at row Y are now visually at row Y - delta.
    The overlay loop reads this counter to shift its old-cell tracking,
    then resets it. Without this, bruno's previous frames leave ghost copies
    on rows that scrolled up.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scroll_delta = 0

    def index(self):
        if self.cursor.y == self.margins.bottom if self.margins else self.lines - 1:
            self.scroll_delta += 1
        super().index()

    def reverse_index(self):
        if self.cursor.y == (self.margins.top if self.margins else 0):
            self.scroll_delta -= 1
        super().reverse_index()


_DEFAULT_PASSTHROUGH_NAMES = ("gemini", "qwen")

# Patterns that the pyte fallback scanner matches against newly-appearing
# screen lines. Used when the shell precmd hook is unavailable (different
# shell, BASH_ENV blocked, FIFO mkfifo failed).
_PYTE_COMMIT_RE = re.compile(r"^\s*\[[\w\-/]+\s+[0-9a-f]{7,}\]")
_PYTE_NEW_BRANCH_RE = re.compile(r"Switched to a new branch '([^']+)'")
_PYTE_PUSH_RE = re.compile(r"\bTo\s+(?:https?|git|ssh|\S+):.+")


class PyteScanner:
    """Scan pyte-rendered shell output for Phase 2 fallback signals.

    The shell hook catches everything precisely; this scanner exists only
    so users on shells we don't hook into still get some reactions.
    """

    def __init__(self):
        self._prev: list[str] = []

    def scan(self, screen) -> list[tuple[str, tuple]]:
        cur = list(screen.display)
        emitted: list[tuple[str, tuple]] = []
        # Compare line-by-line. When rows shrink (rare), reset.
        if len(cur) != len(self._prev):
            self._prev = cur
            return emitted
        for i, line in enumerate(cur):
            if i >= len(self._prev) or line == self._prev[i]:
                continue
            stripped = line.strip()
            if not stripped:
                continue
            if _PYTE_COMMIT_RE.match(stripped):
                emitted.append(("react_commit", ()))
                continue
            m = _PYTE_NEW_BRANCH_RE.search(stripped)
            if m:
                emitted.append(("react_branch", (m.group(1),)))
                continue
            if _PYTE_PUSH_RE.search(stripped):
                emitted.append(("react_push", ()))
                continue
        self._prev = cur
        return emitted


def _passthrough_names() -> tuple[str, ...]:
    raw = os.environ.get("BRUNO_PASSTHROUGH")
    if raw is None:
        return _DEFAULT_PASSTHROUGH_NAMES
    return tuple(n for n in raw.split(",") if n)


def _has_passthrough_descendant(root_pid: int, names: tuple[str, ...]) -> bool:
    """True if any descendant of root_pid has comm matching `names`.

    Walks /proc/<pid>/task/*/children, depth-first, bounded to keep the
    per-tick cost negligible. Used to suppress sprite rendering for
    specific TUIs (gemini-cli, qwen) whose redraw pattern collides with
    bruno's overlay. Other alt-screen TUIs (vim, htop, claude, less)
    have always tolerated the overlap, so the default keeps bruno
    visible there.
    """
    if not names:
        return False
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if len(seen) > 64:
            return False
        try:
            with open(f"/proc/{pid}/comm", "rb") as f:
                comm = f.read().strip().decode("utf-8", "replace")
        except OSError:
            continue
        if comm in names:
            return True
        try:
            entries = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        for tid in entries:
            try:
                with open(f"/proc/{pid}/task/{tid}/children", "rb") as f:
                    raw = f.read().decode("ascii", "replace").split()
            except OSError:
                continue
            for c in raw:
                try:
                    stack.append(int(c))
                except ValueError:
                    pass
    return False


def _resolve_llm_backend(args, persisted: dict) -> str:
    """Pick the active LLM backend for this run.

    CLI flag wins. Otherwise persisted choice. Otherwise "none" (first run
    is off by default; user gets a one-shot hint in-pane).
    """
    cli = getattr(args, "llm", None)
    if cli in ("none", "qwen", "gemini"):
        return cli
    if cli == "auto":
        probe = llm_mod.probe_backends()
        if probe.qwen_available:
            return "qwen"
        if probe.gemini_available:
            return "gemini"
        return "none"
    persisted_val = persisted.get("llm_backend")
    if persisted_val in ("none", "qwen", "gemini"):
        return persisted_val
    return "none"


def _maybe_show_first_run_hint(bruno: Bruno, args, persisted: dict,
                               active_backend: str) -> None:
    """One-shot hint about LLM availability on the very first run.

    Probes silently and emits a single speech bubble naming what's
    available + how to opt in. Persists `llm_prompted_on` so it never
    fires again.
    """
    if persisted.get("llm_prompted_on"):
        return
    if active_backend != "none":
        persisted["llm_prompted_on"] = time.time()
        return
    if getattr(args, "llm", None) is not None:
        # user already engaged with the flag; no need to nag.
        persisted["llm_prompted_on"] = time.time()
        return
    probe = llm_mod.probe_backends()
    opts = probe.options()
    persisted["llm_prompted_on"] = time.time()
    if not opts:
        return
    hint = "llm? --llm " + "/".join(opts)
    bruno.say(hint, ticks=80)


def run(args) -> int:
    """Spawn a shell with bruno overlaid. Returns the shell's exit code."""
    cols, rows = _term_size()

    # Pick shell up-front so the parent can install the precmd hook against
    # the right rc snippet before forking. Same selection logic as the
    # child branch below.
    try:
        entry = pwd.getpwuid(os.getuid())
        chosen_shell = entry.pw_shell if entry.pw_shell and os.path.exists(entry.pw_shell) \
            else os.environ.get("SHELL", "/bin/bash")
    except Exception:
        chosen_shell = os.environ.get("SHELL", "/bin/bash")

    hook_install = None
    if not getattr(args, "no_shell_hook", False):
        try:
            hook_install = shellhook.install(chosen_shell)
        except Exception:
            hook_install = None
        if hook_install is None:
            try:
                state.log_once(
                    f"shell-hook install failed for {chosen_shell}; "
                    "Phase 2 will rely on pyte scan only.",
                    "shellhook-install",
                )
            except Exception:
                pass

    pid, master_fd = pty.fork()
    if pid == 0:
        shell = chosen_shell
        env = os.environ.copy()
        env["BRUNO_ACTIVE"] = "1"
        env["SHELL"] = shell
        if hook_install is not None:
            env.update(hook_install.env_updates)

        # Strip nix-develop/build-shell pollution so the spawned login
        # shell starts from a clean profile and prezto/zshrc don't see
        # the wrong PS1/PATH overrides.
        for var in (
            "IN_NIX_SHELL", "IN_NIX_RUN", "NIX_BUILD_TOP", "NIX_BUILD_CORES",
            "NIX_LOG_FD", "NIX_STORE", "NIX_USER_PROFILE_DIR",
            "buildInputs", "stdenv", "name", "system", "outputs", "out",
            "src", "phases", "shellHook", "PYTHONNOUSERSITE", "SOURCE_DATE_EPOCH",
        ):
            env.pop(var, None)

        # argv[0] starting with '-' makes bash/zsh/etc. behave as a login
        # shell, sourcing ~/.zprofile (and via that, the full prezto chain).
        shell_name = os.path.basename(shell)
        bash_rc = env.get("BRUNO_BASH_RC")
        argv: list[str]
        if shell_name in ("bash", "sh") and bash_rc:
            # bash --rcfile loses login-shell semantics, so source the
            # user's profile chain via the shim itself; we still pass
            # `-bash` so $0 looks login-ish. The shim already chains
            # ~/.bashrc before our hook.
            argv = [f"-{shell_name}", "--rcfile", bash_rc, "-i"]
        else:
            argv = [f"-{shell_name}"]
        try:
            os.execvpe(shell, argv, env)
        except OSError:
            try:
                os.execvpe(shell, [shell], env)
            except OSError:
                os.execvp("/bin/sh", ["/bin/sh"])
        return 0  # unreachable

    _set_winsize(master_fd, rows, cols)

    screen = _ScrollTrackingScreen(cols, rows)
    stream = pyte.ByteStream(screen)

    debug_log = None
    if os.environ.get("BRUNO_DEBUG"):
        debug_log = open(os.environ["BRUNO_DEBUG"], "a", buffering=1)

    occupancy: set[tuple[int, int]] = set()
    selected_rows: set[int] = set()
    child_exited_naturally = False

    # ---- Dock mode ----
    # While a passthrough TUI runs, the child PTY is shrunk by dock_h rows
    # (TIOCSWINSZ) and bruno lives in the reserved bottom strip — zero
    # shared cells, zero redraw collision. A DECSTBM scroll region pinned
    # to the child's rows keeps its newline-scrolls out of the strip.
    docked = False
    child_rows = rows
    dock_h = 0

    def _rebuild_occupancy():
        occupancy.clear()
        buf = screen.buffer
        for y in range(rows):
            row_buf = buf[y]
            for x in range(cols):
                ch = row_buf[x].data
                if ch and ch != " ":
                    occupancy.add((x, y))

    def _can_place(x, y, w, h):
        if docked:
            # Dock strip only: bottom-anchored, horizontal freedom.
            return 0 <= x and x + w <= cols and y == rows - h
        if x < 0 or y < 0 or x + w > cols or y + h > rows:
            return False
        for dy in range(h):
            row = y + dy
            if row in selected_rows:
                return False
            for dx in range(w):
                if (x + dx, row) in occupancy:
                    return False
        return True

    persisted = state.load()
    _stat_baseline = {
        "hunger": persisted.get("hunger", 50),
        "energy": persisted.get("energy", 100),
        "mood": persisted.get("mood", 80),
    }

    # ---- Window-scoped coordination ----
    # In tmux, multiple bruno overlays (one per pane) collaborate so only
    # ONE bruno is rendered per window. Stats live in state.json (already
    # shared); position is per-process, with a handoff blob passed via the
    # window-scope runtime file when bruno walks past a pane edge.
    window_id_str = tmux.window_id()
    my_pane_id = tmux.current_pane_id()
    my_pid = os.getpid()
    coord_active = bool(window_id_str and my_pane_id)
    pane_layout_cache: list[dict] = []
    layout_refresh_at = 0.0
    layout_refresh_s = 0.5
    am_owner = not coord_active
    was_owner = am_owner
    pending_exit: dict | None = None

    def _refresh_layout(now: float) -> None:
        nonlocal pane_layout_cache, layout_refresh_at
        if not coord_active:
            return
        if now < layout_refresh_at:
            return
        layout_refresh_at = now + layout_refresh_s
        pane_layout_cache = tmux.window_pane_layout()

    def _on_pane_exit(dx: int, dy: int, new_x: int, new_y: int, frame) -> bool:
        """Bruno tries to walk off this pane's edge. Hand off to a
        neighbor pane if one exists; otherwise let the default
        turn-around behavior run.
        """
        nonlocal pending_exit
        if not coord_active or not am_owner:
            return False
        if not pane_layout_cache:
            return False
        result = tmux.neighbor_pane(
            pane_layout_cache, my_pane_id, dx, dy, new_x, new_y,
            frame.width, frame.height,
        )
        if result is None:
            return False
        neighbor, entry_x, entry_y = result
        pending_exit = {
            "to_pane_id": neighbor["pane_id"],
            "entry_x": entry_x,
            "entry_y": entry_y,
            "dx": dx,
            "dy": dy,
        }
        return True

    bruno = Bruno(cols, rows, dev_mode=args.dev, can_place=_can_place,
                  persisted=persisted, on_pane_exit=_on_pane_exit)
    save_every_ticks = 300

    def _persist():
        d = bruno.persist_dict()
        delta = {
            "hunger": d["hunger"] - _stat_baseline["hunger"],
            "energy": d["energy"] - _stat_baseline["energy"],
            "mood": d["mood"] - _stat_baseline["mood"],
            "born_at_wall": d.get("born_at_wall"),
            "llm_backend": persisted.get("llm_backend"),
            "llm_prompted_on": persisted.get("llm_prompted_on"),
        }
        state.save_delta(delta)
        _stat_baseline["hunger"] = d["hunger"]
        _stat_baseline["energy"] = d["energy"]
        _stat_baseline["mood"] = d["mood"]

    def _reload_stats():
        """After ownership changes hands, pull fresh stats from state.json
        so we don't keep mutating stale numbers."""
        fresh = state.load()
        for k in ("hunger", "energy", "mood", "born_at_wall"):
            if k in fresh:
                setattr(bruno, k, fresh[k])
        for k in ("hunger", "energy", "mood"):
            if k in fresh:
                _stat_baseline[k] = fresh[k]
    # Spawn in open space rather than against the right edge so the
    # random walk has room in every direction from the start.
    _initial_frame = bruno.current_frame()
    _fw, _fh = _initial_frame.width, _initial_frame.height
    _spawn = bruno.find_clear_spot(_fw, _fh,
                                   near_x=max(0, cols // 2 - _fw // 2),
                                   near_y=max(0, rows // 2 - _fh // 2))
    if _spawn is not None:
        bruno.x, bruno.y = _spawn
    else:
        bruno.x = max(0, cols - 8)
        bruno.y = max(0, rows // 2 - 1)

    compositor = Compositor(sys.stdout.fileno(), screen, debug=debug_log)

    def _write_stdout(s: str) -> None:
        try:
            os.write(sys.stdout.fileno(), s.encode("utf-8", errors="replace"))
        except OSError:
            pass

    def _dock_strip_clear_seq() -> str:
        return "".join(_move_to(r, 0) + ESC + "[2K"
                       for r in range(child_rows, rows))

    def _dock_margin_seq() -> str:
        return f"{ESC}[1;{child_rows}r"

    def _enter_dock() -> None:
        nonlocal docked, child_rows, dock_h
        dock_h = 3
        if sprites.decoration_for_today(bruno.born_at_wall) is not None:
            dock_h += 1
        if rows < dock_h + 4:
            return
        compositor.clear()
        docked = True
        child_rows = rows - dock_h
        _set_winsize(master_fd, child_rows, cols)
        screen.resize(child_rows, cols)
        _write_stdout(SAVE_CUR + _dock_margin_seq()
                      + _dock_strip_clear_seq() + RESTORE_CUR)
        f = bruno.current_frame()
        bruno.x = max(0, min(bruno.x, cols - f.width))
        bruno.y = max(0, rows - f.height)

    def _exit_dock() -> None:
        nonlocal docked, child_rows
        if not docked:
            return
        compositor.clear()
        docked = False
        _write_stdout(SAVE_CUR + ESC + "[r"
                      + _dock_strip_clear_seq() + RESTORE_CUR)
        child_rows = rows
        _set_winsize(master_fd, rows, cols)
        screen.resize(rows, cols)

    resize_pending = [False]
    # SIGUSR1 toggles bruno-visible without unwrapping the shell. Lets a
    # tmux key send `kill -USR1 <pane_pid>` to make him vanish/return on
    # demand. Default disposition for SIGUSR1 is term, so we install the
    # handler before anything else can race a signal.
    hidden = [False]
    hide_pending = [False]

    def on_winch(_signum, _frame):
        resize_pending[0] = True

    def on_usr1(_signum, _frame):
        hidden[0] = not hidden[0]
        hide_pending[0] = hidden[0]

    signal.signal(signal.SIGWINCH, on_winch)
    signal.signal(signal.SIGUSR1, on_usr1)

    old_attrs = None
    mouse_on = False
    if sys.stdin.isatty():
        old_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        try:
            os.write(sys.stdout.fileno(), mouse_mod.ENABLE)
            mouse_on = True
        except OSError:
            pass

    stdin_buf = bytearray()
    mouse_reenable_at = 0.0

    # Sync the visible terminal to pyte's fresh-start view. Otherwise any
    # pre-existing rows (nix warnings, last command output) leave terminal
    # and pyte coords offset, and bruno's collision checks read the wrong
    # cells.
    try:
        os.write(sys.stdout.fileno(), b"\x1b[2J\x1b[H")
    except OSError:
        pass

    def _restore_terminal():
        if old_attrs is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attrs)
            except Exception:
                pass

    hook_fd: int | None = None
    hook_buf = bytearray()
    if hook_install is not None:
        hook_fd = shellhook.open_reader(hook_install.fifo_path)

    # Phase 4: IPC feed files. Anyone can drop text in a feed path
    # (`echo 🍎 | bruno`, click-to-feed, or `echo … > /tmp/bruno_feed`)
    # to trigger a reaction. PID-agnostic so a single-user-multi-pane
    # setup wakes every bruno; that's the intent. See bruno/feed.py.
    feed_fds = feed_mod.open_readers()

    pyte_scanner = PyteScanner()
    pyte_scan_every = 5  # every ~0.5s @ 10Hz

    # ---- Phase 6: LLM setup ----
    llm_backend = _resolve_llm_backend(args, persisted)
    persisted["llm_backend"] = llm_backend
    reactor: llm_mod.AsyncReactor | None = None
    if llm_backend not in (None, "none"):
        reactor = llm_mod.AsyncReactor(llm_backend)
    llm_interval_s = max(30, int(getattr(args, "llm_interval", 180) or 180))
    llm_next_call_at = time.monotonic() + 5.0  # short initial delay
    llm_last_hash = ""
    _maybe_show_first_run_hint(bruno, args, persisted, llm_backend)
    _persist()

    next_tick = time.monotonic()
    tick_interval = 1.0 / TICK_HZ
    activity_idle_ticks = 0
    bubble_text: str | None = None
    bubble_until = 0.0
    tracked_cwd: str | None = None
    passthrough_names = _passthrough_names()
    passthrough_active = False
    last_passthrough = False
    passthrough_check_interval = 0.5
    passthrough_next_check = 0.0

    try:
        while True:
            now = time.monotonic()
            timeout = max(0, next_tick - now)
            try:
                r, _, _ = select.select(
                    [sys.stdin.fileno(), master_fd], [], [], timeout
                )
            except (InterruptedError, OSError) as e:
                if e.args and e.args[0] == errno.EINTR:
                    r = []
                else:
                    raise

            if resize_pending[0]:
                resize_pending[0] = False
                new_cols, new_rows = _term_size()
                if (new_cols, new_rows) != (cols, rows):
                    # Paint pyte content over bruno's current cells BEFORE
                    # resizing — once screen.resize runs the (row,col)
                    # coordinates may be out of bounds and we'd leak
                    # bruno's old glyphs as trails on the new layout.
                    compositor.clear()
                    cols, rows = new_cols, new_rows
                    compositor.term_rows = rows
                    compositor.term_cols = cols
                    if docked and rows >= dock_h + 4:
                        child_rows = rows - dock_h
                        _set_winsize(master_fd, child_rows, cols)
                        screen.resize(child_rows, cols)
                        _write_stdout(SAVE_CUR + _dock_margin_seq()
                                      + _dock_strip_clear_seq() + RESTORE_CUR)
                    else:
                        if docked:
                            _exit_dock()
                        child_rows = rows
                        _set_winsize(master_fd, rows, cols)
                        screen.resize(rows, cols)
                    bruno.resize(cols, rows)
                    # last_cells already cleared by compositor.clear() above

            if sys.stdin.fileno() in r:
                try:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if data:
                        stdin_buf.extend(data)
                        passthrough, mouse_events = mouse_mod.parse(stdin_buf)
                        for btn, col, row, term in mouse_events:
                            if not mouse_mod.is_left_press(btn, term):
                                continue
                            if bruno._hidden or hidden[0]:
                                continue
                            if mouse_mod.hits_bruno(bruno, col, row):
                                bruno.feed()
                        if passthrough:
                            os.write(master_fd, _strip_kitty_apc(passthrough))
                        activity_idle_ticks = 0
                except OSError:
                    pass

            if master_fd in r:
                try:
                    data = os.read(master_fd, 8192)
                except OSError:
                    child_exited_naturally = True
                    break
                if not data:
                    child_exited_naturally = True
                    break
                # Drain everything immediately available so a single read
                # boundary doesn't strand a partial escape sequence on the
                # terminal while we go off to render.
                while True:
                    try:
                        r2, _, _ = select.select([master_fd], [], [], 0)
                    except (InterruptedError, OSError):
                        break
                    if master_fd not in r2:
                        break
                    try:
                        more = os.read(master_fd, 8192)
                    except OSError:
                        more = b""
                    if not more:
                        break
                    data += more
                # Big writes are almost certainly graphics-protocol blobs
                # (yazi previews, icat). pyte parses them at ~1 MB/s and
                # would block the loop. They also typically scroll the
                # terminal — and skipping pyte means scroll_delta never
                # fires, so handle_scroll() can't shift bruno's tracked
                # cells. Wipe bruno from the terminal *before* the data
                # is mirrored so his glyphs don't ride the scroll up as
                # untrackable trails.
                if len(data) > PYTE_FEED_MAX:
                    compositor.clear()
                try:
                    os.write(sys.stdout.fileno(), data)
                except OSError:
                    pass
                if len(data) <= PYTE_FEED_MAX:
                    try:
                        stream.feed(data)
                    except Exception:
                        # pyte 0.8.2 raises on private CSI sequences whose
                        # dispatcher doesn't accept private= (e.g. crush's
                        # \e[>4m modifyOtherKeys). Swallowing keeps bruno
                        # alive; the screen view goes briefly stale until
                        # the next clean sequence resets the parser.
                        pass
                activity_idle_ticks = 0

                # On scroll, the terminal already shifted bruno's last-drawn
                # cells along with everything else; pyte's buffer scrolled in
                # lockstep. Only cells *we* drew need correcting — repaint
                # them at their shifted positions with whatever pyte now
                # shows there. Bruno re-overlays on the next tick.
                if screen.scroll_delta:
                    # Docked, the scroll region pins the child's scrolling
                    # to its own rows — the dock strip never shifts, so
                    # bruno's cells need no correction.
                    if not docked:
                        compositor.handle_scroll(screen.scroll_delta, rows)
                    screen.scroll_delta = 0

            now = time.monotonic()
            if now >= next_tick:
                next_tick += tick_interval
                if next_tick < now:
                    next_tick = now + tick_interval
                if mouse_on and now >= mouse_reenable_at:
                    # Some TUIs (less, vim, fzf) disable SGR mouse on exit.
                    # Re-emit periodically so click-to-feed survives them.
                    mouse_reenable_at = now + 3.0
                    try:
                        os.write(sys.stdout.fileno(), mouse_mod.ENABLE)
                    except OSError:
                        pass
                try:
                    child_cwd = os.readlink(f"/proc/{pid}/cwd")
                    if child_cwd != tracked_cwd:
                        os.chdir(child_cwd)
                        tracked_cwd = child_cwd
                except OSError:
                    pass
                # Detect a passthrough child BEFORE the settle gate. A child
                # that streams continuously (claude) keeps the time window
                # perpetually tripped; if the gate skipped the whole tick we
                # would never reach the dock logic below, so bruno could
                # never enter dock in the first place — a permanent freeze.
                if now >= passthrough_next_check:
                    passthrough_next_check = now + passthrough_check_interval
                    passthrough_active = _has_passthrough_descendant(
                        pid, passthrough_names
                    )

                # Skip the overlay this tick only if the terminal is mid
                # escape sequence — injecting our SAVE_CUR there would be
                # parsed as the sequence's parameters. The _taking_plain_text
                # flag is pyte's parser state for the bytes we've forwarded;
                # if pyte is mid-sequence, so is the terminal.
                #
                # We deliberately do NOT gate on a wall-clock settle window
                # here: it refreshes on every shell byte, so a TUI that
                # streams continuously (claude) would keep it perpetually
                # tripped and freeze bruno for the child's whole lifetime.
                # Parser-state is the authoritative check; between the
                # discrete escape sequences a streaming app emits, pyte
                # returns to plain-text and bruno renders, so he keeps
                # roaming the full screen on top of the live TUI.
                if getattr(stream, "_taking_plain_text", True) is not True:
                    continue
                if hide_pending[0]:
                    compositor.clear()
                    hide_pending[0] = False
                if hidden[0] or bruno._hidden:
                    compositor.clear()
                    continue

                # ---- Ownership: one bruno per tmux window ----
                _refresh_layout(now)
                if coord_active:
                    am_owner = coord.claim_or_refresh(
                        window_id_str, my_pid, my_pane_id
                    )
                else:
                    am_owner = True
                if am_owner and not was_owner:
                    _reload_stats()
                    incoming = coord.pop_handoff(window_id_str, my_pane_id) \
                        if coord_active else None
                    if incoming:
                        f0 = bruno.current_frame()
                        bruno.x = max(0, min(cols - f0.width,
                                             int(incoming.get("x", bruno.x))))
                        bruno.y = max(0, min(rows - f0.height,
                                             int(incoming.get("y", bruno.y))))
                        bruno.dx = int(incoming.get("dx", bruno.dx))
                        bruno.dy = int(incoming.get("dy", bruno.dy))
                        if bruno.dx != 0:
                            bruno._last_facing = 1 if bruno.dx > 0 else -1
                        from .creature import WALK
                        bruno._enter(WALK, 80)
                was_owner = am_owner

                if coord_active and not am_owner:
                    if docked:
                        _exit_dock()
                    # Non-owner: ferry shell-hook reactions to the owner,
                    # then disappear from this pane.
                    if hook_fd is not None:
                        for event in shellhook.drain_events(hook_fd, hook_buf):
                            decision = shellhook.interpret(event)
                            if not decision:
                                continue
                            method_name, method_args = decision
                            coord.push_event(window_id_str, method_name,
                                             list(method_args))
                    compositor.clear()
                    continue
                # A small whitelist of TUIs (gemini, qwen, claude, crush
                # via BRUNO_PASSTHROUGH) full-frame diff-render in a way
                # that collides with the overlay — they never repaint
                # bruno's foreign glyphs, leaving trails. While one runs,
                # bruno docks: the child PTY shrinks by dock_h rows and he
                # lives in the reserved bottom strip instead of vanishing.
                # Set BRUNO_PASSTHROUGH= (empty) to disable entirely.
                # (passthrough_active is refreshed above, before the gate.)
                if passthrough_active:
                    if not docked:
                        _enter_dock()
                    if not docked:
                        # Terminal too cramped for a dock strip — fall back
                        # to the old hide-entirely behavior.
                        if not last_passthrough:
                            compositor.clear()
                            last_passthrough = True
                        continue
                    last_passthrough = True
                    bruno.tick_once()
                    if bruno.tick % save_every_ticks == 0:
                        _persist()
                    if bruno.speech is None:
                        offering = feed_mod.read_offering(feed_fds)
                        if offering:
                            try:
                                if food.is_food(offering):
                                    bruno.feed()
                                else:
                                    bruno.burp(offering)
                            except Exception:
                                pass
                    if hook_fd is not None:
                        for event in shellhook.drain_events(hook_fd, hook_buf):
                            decision = shellhook.interpret(event)
                            if not decision:
                                continue
                            method_name, method_args = decision
                            is_verb = event and event[0] == "verb"
                            if not is_verb and bruno.speech is not None:
                                continue
                            method = getattr(bruno, method_name, None)
                            if method is None:
                                continue
                            try:
                                method(*method_args)
                            except Exception:
                                pass
                    f = bruno.current_frame()
                    bruno.y = max(0, rows - f.height)
                    bruno.x = max(0, min(bruno.x, cols - f.width))
                    # Child resets (\e[r) land on the real terminal as a
                    # full-real-screen region, exposing the strip to its
                    # scrolling. Re-assert whenever pyte shows the child
                    # holding full-view (or no) margins; a child's custom
                    # region (vim, less) maps to the same real rows and is
                    # left alone.
                    m = screen.margins
                    if m is None or (m.top == 0 and m.bottom >= child_rows - 1):
                        _write_stdout(SAVE_CUR + _dock_margin_seq()
                                      + RESTORE_CUR)
                    compositor.render(f.lines, bruno.x, bruno.y,
                                      None, 0, 0, None)
                    continue
                last_passthrough = False
                if docked:
                    _exit_dock()
                if tmux.in_tmux() and bruno.tick % 3 == 0:
                    sel = tmux.selection_rows()
                    if sel is None:
                        selected_rows.clear()
                    else:
                        top, bot = sel
                        selected_rows.clear()
                        selected_rows.update(range(top, bot + 1))
                _rebuild_occupancy()
                bruno.tick_once()
                activity_idle_ticks += 1

                if pending_exit is not None and coord_active:
                    # Bruno walked off this pane's edge into a neighbor.
                    # Persist stats so the receiver picks them up, post
                    # the handoff blob, drop ownership, and vanish locally.
                    try:
                        _persist()
                    except Exception:
                        pass
                    coord.post_handoff(
                        window_id_str, my_pid,
                        pending_exit["to_pane_id"],
                        pending_exit["entry_x"], pending_exit["entry_y"],
                        pending_exit["dx"], pending_exit["dy"],
                    )
                    pending_exit = None
                    am_owner = False
                    was_owner = False
                    compositor.clear()
                    continue

                if bruno.tick % save_every_ticks == 0:
                    _persist()

                if coord_active:
                    for ev in coord.drain_events(window_id_str, my_pid):
                        method_name = ev.get("name")
                        if not method_name:
                            continue
                        method = getattr(bruno, method_name, None)
                        if method is None:
                            continue
                        method_args = ev.get("args") or []
                        try:
                            method(*method_args)
                        except Exception:
                            pass

                if hook_fd is not None:
                    for event in shellhook.drain_events(hook_fd, hook_buf):
                        decision = shellhook.interpret(event)
                        if not decision:
                            continue
                        method_name, method_args = decision
                        # Verbs (hide/show/stats/feed) always fire — they
                        # are explicit user actions. Reactions fall through
                        # the speech-busy gate to avoid stomping bubbles.
                        is_verb = event and event[0] == "verb"
                        if not is_verb and bruno.speech is not None:
                            continue
                        method = getattr(bruno, method_name, None)
                        if method is None:
                            continue
                        try:
                            method(*method_args)
                        except Exception:
                            pass

                if bruno.speech is None:
                    offering = feed_mod.read_offering(feed_fds)
                    if offering:
                        try:
                            if food.is_food(offering):
                                bruno.feed()
                            else:
                                bruno.burp(offering)
                        except Exception:
                            pass

                if bruno.tick % pyte_scan_every == 0 and bruno.speech is None:
                    for method_name, method_args in pyte_scanner.scan(screen):
                        method = getattr(bruno, method_name, None)
                        if method is None:
                            continue
                        try:
                            method(*method_args)
                        except Exception:
                            pass
                        if bruno.speech is not None:
                            break

                if reactor is not None:
                    result = reactor.poll()
                    if result and bruno.speech is None:
                        bruno.react_llm(result)
                    if now >= llm_next_call_at and not reactor.is_pending() \
                            and bruno.speech is None:
                        snippet = llm_mod.sample_tmux_text() if tmux.in_tmux() else ""
                        if snippet and len(snippet) >= llm_mod.MIN_NEW_TEXT:
                            h = llm_mod.text_hash(snippet)
                            if h != llm_last_hash:
                                llm_last_hash = h
                                reactor.request(snippet)
                        llm_next_call_at = now + llm_interval_s

                # Handle displacement: if bruno's current spot now has content,
                # try to teleport to a clear spot. Otherwise just hide him.
                f = bruno.current_frame()
                if not bruno.can_place(bruno.x, bruno.y, f.width, f.height):
                    spot = bruno.find_clear_spot(f.width, f.height,
                                                 near_x=bruno.x, near_y=bruno.y)
                    if spot is not None:
                        bruno.x, bruno.y = spot
                    else:
                        compositor.clear()
                        continue

                if bruno.speech is None and now >= bubble_until \
                        and (now - bubble_until) > 5 \
                        and bruno.tick % 1 == 0:
                    if SPEECH_CHANCE_PER_TICK and \
                            __import__("random").random() < SPEECH_CHANCE_PER_TICK:
                        bruno.say(say.pick(bruno.state, dev_mode=args.dev), ticks=50)

                bubble = None
                bx = by = 0
                if bruno.speech:
                    placed = _bubble_position(bruno.x, bruno.y, f.width,
                                              cols, rows, bruno.speech, screen)
                    if placed is not None:
                        bubble, bx, by = placed

                particle_cells: list[tuple[int, int, str, str | None]] = []
                for row, col, ch, sgr in bruno.particle_cells():
                    if 0 <= row < rows and 0 <= col < cols \
                            and _cell_empty(screen, col, row):
                        particle_cells.append((row, col, ch, sgr))
                for row, col, ch, sgr in bruno.aura_cells(f.width, f.height):
                    if 0 <= row < rows and 0 <= col < cols \
                            and _cell_empty(screen, col, row):
                        particle_cells.append((row, col, ch, sgr))

                compositor.render(f.lines, bruno.x, bruno.y, bubble, bx, by,
                                  particle_cells)

            # Reap zombie if shell exited without us catching EOF
            try:
                done_pid, _ = os.waitpid(pid, os.WNOHANG)
                if done_pid == pid:
                    break
            except ChildProcessError:
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _persist()
        except Exception:
            pass
        if coord_active:
            try:
                coord.release(window_id_str, my_pid)
            except Exception:
                pass
        if hook_fd is not None:
            try:
                os.close(hook_fd)
            except OSError:
                pass
        for fd in feed_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        if hook_install is not None:
            try:
                hook_install.cleanup()
            except Exception:
                pass
        if docked:
            _write_stdout(ESC + "[r")
        compositor.clear()
        if mouse_on:
            try:
                os.write(sys.stdout.fileno(), mouse_mod.DISABLE)
            except OSError:
                pass
        try:
            os.write(sys.stdout.fileno(), (RESET_SGR + SHOW_CURSOR).encode())
        except OSError:
            pass
        _restore_terminal()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        if child_exited_naturally:
            return 42
        return 0
    return 0
