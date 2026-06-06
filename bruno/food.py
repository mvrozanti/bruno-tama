"""Food-emoji recognizer for the feed FIFO.

`is_food(text)` returns True if any character in `text` is a recognized
food/drink emoji. Empty input is not food. Whitespace is ignored.

Bruno eats food and burps everything else back onto the screen.
"""
from __future__ import annotations

_FOOD_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F32D, 0x1F37F),
    (0x1F950, 0x1F96F),
    (0x1F9C0, 0x1F9CB),
    (0x1FAD0, 0x1FAE7),
)
_FOOD_SINGLES: frozenset[int] = frozenset({0x2615})


def is_food_char(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    if cp in _FOOD_SINGLES:
        return True
    for lo, hi in _FOOD_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def is_food(text: str) -> bool:
    if not text:
        return False
    for ch in text:
        if ch.isspace():
            continue
        if is_food_char(ch):
            return True
    return False
