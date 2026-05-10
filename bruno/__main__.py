"""Main loop. Run with: python -m bruno  (or just `bruno` after install)."""
from __future__ import annotations
import argparse
import random
import select
import signal
import sys
import termios
import time
import tty
from contextlib import contextmanager

from . import render, say, tmux
from .creature import Bruno, IDLE, WALK, SLEEP, HUNGRY, HAPPY, SQUISH, LOOK

TICK_MS = 100   # 10 fps
SPEECH_CHANCE_PER_TICK = 0.003   # ~once every ~30s on average
ACTIVITY_POLL_TICKS = 80         # poll tmux siblings every ~8s


@contextmanager
def cbreak_stdin():
    """Set stdin to cbreak (no buffering, no echo) so we can read keys live."""
    if not sys.stdin.isatty():
        yield None
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key(fd) -> str | None:
    if fd is None:
        return None
    r, _, _ = select.select([fd], [], [], 0)
    if not r:
        return None
    try:
        ch = sys.stdin.read(1)
    except Exception:
        return None
    return ch


def pick_bubble_position(bruno_x: int, bruno_y: int, bruno_w: int,
                         pane_w: int, pane_h: int,
                         text: str) -> tuple[list[str], int, int] | None:
    """Place the bubble next to bruno wherever it fits. None if it doesn't."""
    if pane_w < 14 or pane_h < 4:
        return None
    # Try right side first
    right_room = pane_w - (bruno_x + bruno_w) - 1
    left_room = bruno_x - 1
    if right_room >= 6:
        lines = say.bubble(text, max_width=min(28, right_room), tail="left")
        if lines:
            bx = bruno_x + bruno_w + 1
            by = max(0, bruno_y - (len(lines) - 2))
            if by + len(lines) <= pane_h:
                return lines, bx, by
    if left_room >= 6:
        lines = say.bubble(text, max_width=min(28, left_room), tail="right")
        if lines:
            bx = max(0, bruno_x - len(lines[0]) - 1)
            by = max(0, bruno_y - (len(lines) - 2))
            if by + len(lines) <= pane_h:
                return lines, bx, by
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bruno",
        description="a dot-blob tamagotchi that lives in your terminal",
    )
    parser.add_argument("--dev", action="store_true",
                        help="enable dev-themed phrases")
    parser.add_argument("--no-tmux", action="store_true",
                        help="skip tmux pane-sibling activity polling")
    parser.add_argument("--fps", type=int, default=10,
                        help="frames per second (default: 10)")
    parser.add_argument("--mode", choices=["overlay", "pane"], default="overlay",
                        help="overlay = wrap your shell and draw on top "
                             "(default); pane = own the whole pane")
    args = parser.parse_args()

    if args.mode == "overlay":
        from . import overlay
        return overlay.run(args)

    tick_seconds = 1.0 / max(1, min(30, args.fps))

    pane_w, pane_h = render.term_size()
    bruno = Bruno(pane_w, pane_h, dev_mode=args.dev)
    painter = render.Painter()

    resize_pending = [False]

    def on_resize(_signum, _frame):
        resize_pending[0] = True

    signal.signal(signal.SIGWINCH, on_resize)

    use_tmux = (not args.no_tmux) and tmux.in_tmux()

    with render.screen(), cbreak_stdin() as fd:
        try:
            last_activity = None
            activity_counter = 0
            next_tick = time.monotonic()
            while True:
                if resize_pending[0]:
                    pane_w, pane_h = render.term_size()
                    if use_tmux:
                        tm = tmux.pane_size()
                        if tm:
                            pane_w, pane_h = tm
                    bruno.resize(pane_w, pane_h)
                    painter.clear()
                    sys.stdout.write(render.CLEAR_SCREEN)
                    sys.stdout.flush()
                    resize_pending[0] = False

                # Keyboard interactions
                while True:
                    key = read_key(fd)
                    if key is None:
                        break
                    if key in ("q", "Q", "\x03", "\x04"):  # q, Ctrl-C, Ctrl-D
                        return 0
                    if key in (" ", "p"):
                        bruno.pet()
                    elif key == "f":
                        bruno.feed()
                    elif key == "P":
                        bruno.poke()
                    elif key == "w":
                        bruno.wake()
                    elif key == "s":
                        # Force a fresh phrase
                        bruno.say(say.pick(bruno.state, dev_mode=args.dev), ticks=50)

                bruno.tick_once()

                # Spontaneous speech
                if bruno.speech is None and random.random() < SPEECH_CHANCE_PER_TICK:
                    bruno.say(say.pick(bruno.state, dev_mode=args.dev), ticks=50)

                # tmux activity awareness
                if use_tmux:
                    activity_counter += 1
                    if activity_counter >= ACTIVITY_POLL_TICKS:
                        activity_counter = 0
                        signal_word = tmux.activity_signal()
                        if signal_word and signal_word != last_activity:
                            last_activity = signal_word
                            reactions = {
                                "angry": "*concerned blob*",
                                "busy": "wow lots of words",
                                "code": "ah, code time",
                                "quiet": "...so quiet...",
                            }
                            if signal_word in reactions and bruno.speech is None:
                                bruno.say(reactions[signal_word], ticks=50)

                # Compose render
                f = bruno.current_frame()
                # Keep bruno inside the pane (he may have shifted form/size)
                bruno.x = max(0, min(bruno.x, max(0, pane_w - f.width)))
                bruno.y = max(0, min(bruno.y, max(0, pane_h - f.height)))

                bubble_lines = None
                bubble_x = bubble_y = 0
                if bruno.speech:
                    placed = pick_bubble_position(
                        bruno.x, bruno.y, f.width, pane_w, pane_h, bruno.speech
                    )
                    if placed:
                        bubble_lines, bubble_x, bubble_y = placed

                painter.paint(f.lines, bruno.x, bruno.y,
                              bubble_lines, bubble_x, bubble_y)

                next_tick += tick_seconds
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_tick = time.monotonic()
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
