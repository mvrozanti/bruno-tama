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
import select
import shutil
import signal
import struct
import sys
import termios
import time
import tty

import pyte

from . import say
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
    if cell.underscore:
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
        self._last_cells: set[tuple[int, int]] = set()
        self._last_bubble_cells: set[tuple[int, int]] = set()
        self._debug = debug

    def render(self, sprite_lines: list[str], x: int, y: int,
               bubble_lines: list[str] | None, bx: int, by: int) -> None:
        new_cells: set[tuple[int, int]] = set()
        new_bubble_cells: set[tuple[int, int]] = set()
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
                if row < 0 or row >= self.screen.lines:
                    continue
                if col < 0 or col >= self.screen.columns:
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
                    if row < 0 or row >= self.screen.lines:
                        continue
                    if col < 0 or col >= self.screen.columns:
                        continue
                    new_bubble_cells.add((row, col))
                    out.append(_move_to(row, col) + DIM_SGR + ch + RESET_SGR)

        # Restore cells we owned last tick that we don't own this tick.
        # Use whatever pyte reports for the underlying shell content,
        # including its SGR state — otherwise we strip the prompt's
        # colors when bruno walks off them.
        all_old = self._last_cells | self._last_bubble_cells
        all_new = new_cells | new_bubble_cells
        stale = all_old - all_new
        for row, col in stale:
            out.append(_move_to(row, col) + _cell_paint(self.screen, col, row))

        out.append(RESET_SGR + RESTORE_CUR)
        if self._debug:
            self._debug.write(
                f"render: new={sorted(new_cells)} stale={sorted(stale)} "
                f"bruno_xy=({x},{y})\n"
            )
        # Don't re-show cursor here — the user's shell controls visibility.
        os.write(self.stdout_fd, "".join(out).encode("utf-8", errors="replace"))
        self._last_cells = new_cells
        self._last_bubble_cells = new_bubble_cells

    def clear(self) -> None:
        all_old = self._last_cells | self._last_bubble_cells
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

    def handle_scroll(self, delta: int, rows: int) -> None:
        all_old = self._last_cells | self._last_bubble_cells
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


def run(args) -> int:
    """Spawn a shell with bruno overlaid. Returns the shell's exit code."""
    cols, rows = _term_size()

    pid, master_fd = pty.fork()
    if pid == 0:
        # Pick the user's real login shell from /etc/passwd, not $SHELL —
        # `nix develop -c` sets SHELL=/bin/bash which would lose the rice.
        try:
            entry = pwd.getpwuid(os.getuid())
            shell = entry.pw_shell if entry.pw_shell and os.path.exists(entry.pw_shell) \
                else os.environ.get("SHELL", "/bin/bash")
        except Exception:
            shell = os.environ.get("SHELL", "/bin/bash")

        env = os.environ.copy()
        env["BRUNO_ACTIVE"] = "1"
        env["SHELL"] = shell

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
        try:
            os.execvpe(shell, [f"-{shell_name}"], env)
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
        if x < 0 or y < 0 or x + w > cols or y + h > rows:
            return False
        for dy in range(h):
            for dx in range(w):
                if (x + dx, y + dy) in occupancy:
                    return False
        return True

    bruno = Bruno(cols, rows, dev_mode=args.dev, can_place=_can_place)
    bruno.x = max(0, cols - 8)
    bruno.y = max(0, rows // 2 - 1)

    compositor = Compositor(sys.stdout.fileno(), screen, debug=debug_log)

    resize_pending = [False]

    def on_winch(_signum, _frame):
        resize_pending[0] = True

    signal.signal(signal.SIGWINCH, on_winch)

    old_attrs = None
    if sys.stdin.isatty():
        old_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())

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

    next_tick = time.monotonic()
    tick_interval = 1.0 / TICK_HZ
    activity_idle_ticks = 0
    bubble_text: str | None = None
    bubble_until = 0.0
    # If shell bytes arrived very recently, an os.read boundary may have
    # split an escape sequence; injecting our SAVE_CUR / cursor-move into
    # the middle of that sequence corrupts the terminal. Wait for the
    # stream to settle before overlaying.
    shell_settle_s = 0.04
    last_shell_byte = 0.0

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
                    cols, rows = new_cols, new_rows
                    _set_winsize(master_fd, rows, cols)
                    screen.resize(rows, cols)
                    bruno.resize(cols, rows)
                    compositor._last_cells.clear()
                    compositor._last_bubble_cells.clear()

            if sys.stdin.fileno() in r:
                try:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if data:
                        os.write(master_fd, data)
                        activity_idle_ticks = 0
                except OSError:
                    pass

            if master_fd in r:
                try:
                    data = os.read(master_fd, 8192)
                except OSError:
                    break
                if not data:
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
                stream.feed(data)
                try:
                    os.write(sys.stdout.fileno(), data)
                except OSError:
                    pass
                activity_idle_ticks = 0
                last_shell_byte = time.monotonic()

                # On scroll, the terminal already shifted bruno's last-drawn
                # cells along with everything else; pyte's buffer scrolled in
                # lockstep. Only cells *we* drew need correcting — repaint
                # them at their shifted positions with whatever pyte now
                # shows there. Bruno re-overlays on the next tick.
                if screen.scroll_delta:
                    compositor.handle_scroll(screen.scroll_delta, rows)
                    screen.scroll_delta = 0

            now = time.monotonic()
            if now >= next_tick:
                next_tick += tick_interval
                if next_tick < now:
                    next_tick = now + tick_interval
                # Skip the overlay this tick if the shell just wrote — the
                # last byte may be the head of an unterminated escape and
                # our SAVE_CUR would get parsed as its parameters.
                if now - last_shell_byte < shell_settle_s:
                    continue
                _rebuild_occupancy()
                bruno.tick_once()
                activity_idle_ticks += 1

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

                compositor.render(f.lines, bruno.x, bruno.y, bubble, bx, by)

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
        compositor.clear()
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
            _, status = os.waitpid(pid, 0)
            return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else 0
        except ChildProcessError:
            return 0
    return 0
