"""SGR (1006) mouse parser + enable/disable sequences.

Click-on-bruno triggers a feed in both pane and overlay modes. We ask
the terminal for button-event tracking (DEC private 1000) encoded in
SGR-extended form (1006), then parse `\\x1b[<b;x;y;M|m` sequences out
of the stdin stream. Motion and wheel events are recognised but
discarded; only plain left-button press fires a hit.
"""
from __future__ import annotations

ENABLE = b"\x1b[?1000h\x1b[?1006h"
DISABLE = b"\x1b[?1006l\x1b[?1000l"


def parse(buf: bytearray) -> tuple[bytes, list[tuple[int, int, int, str]]]:
    """Strip SGR mouse sequences from `buf` in place.

    Returns (passthrough_bytes, events). Each event is
    (button_code, col_0idx, row_0idx, 'M' for press / 'm' for release).
    A trailing incomplete sequence is left in `buf` for the next read.
    """
    out = bytearray()
    events: list[tuple[int, int, int, str]] = []
    i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b == 0x1b and i + 2 < n and buf[i + 1] == 0x5b and buf[i + 2] == 0x3c:
            j = i + 3
            while j < n and buf[j] not in (0x4d, 0x6d):
                j += 1
            if j >= n:
                break
            try:
                params = bytes(buf[i + 3:j]).decode("ascii").split(";")
                if len(params) == 3:
                    btn = int(params[0])
                    col = int(params[1]) - 1
                    row = int(params[2]) - 1
                    events.append((btn, col, row, chr(buf[j])))
            except (ValueError, UnicodeDecodeError):
                pass
            i = j + 1
            continue
        out.append(b)
        i += 1
    del buf[:i]
    return bytes(out), events


def is_left_press(btn: int, term: str) -> bool:
    if term != "M":
        return False
    if btn & 0x20:  # motion
        return False
    if btn & 0x40:  # wheel
        return False
    return (btn & 0x03) == 0


def hits_bruno(bruno, col: int, row: int) -> bool:
    f = bruno.current_frame()
    if not (bruno.x <= col < bruno.x + f.width):
        return False
    if not (bruno.y <= row < bruno.y + f.height):
        return False
    return True
