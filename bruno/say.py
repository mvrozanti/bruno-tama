"""Speech bubbles, cowsay-style, and bruno's phrase pool."""
import random
import textwrap

PHRASES_IDLE = [
    "...",
    "*hops around*",
    "warm pixels :)",
    "i live here now",
    "the dots, they speak",
    "what's that you're typing?",
    "click clack",
    "where am i",
    "got any snacks?",
    "i am bruno",
    "pet me?",
    "hello :)",
    "*looks around*",
    "dot dot dot",
    "🭽 (well, almost)",
    "this terminal is cozy",
    "blob life",
    "*small wiggle*",
    "stop typing for a sec",
    "i am very small",
]

PHRASES_HUNGRY = [
    "im hungy",
    "snak?",
    "pls feed",
    "*tummy rumble*",
    "i would eat a pixel",
    "food?",
]

PHRASES_HAPPY = [
    "yay!",
    ":D",
    "*happy wiggle*",
    "the best day",
    "love this pane",
    "!!",
]

PHRASES_SLEEP = [
    "zzz",
    "*snore*",
    "shh sleeping",
    "five more minutes",
    "...zzz...",
]

PHRASES_BUMP = [
    "ouch!",
    "*squish*",
    "watch the wall",
    "ow my edge",
    "!",
]

PHRASES_DEV = [
    "vim or emacs?",
    "git push --force? bold",
    "is that production?",
    "looks like a typo",
    "the build is gonna fail",
    "did you write tests?",
    "rebase, don't merge",
    "your indent looks weird",
    "you have 47 unread tabs",
    "tabs or spaces?",
]


def pick(state: str, dev_mode: bool = False) -> str:
    """Pick a phrase appropriate to the current state."""
    if state == "sleep":
        pool = PHRASES_SLEEP
    elif state == "hungry":
        pool = PHRASES_HUNGRY
    elif state == "happy":
        pool = PHRASES_HAPPY
    elif state == "squish":
        pool = PHRASES_BUMP
    else:
        pool = PHRASES_IDLE
        if dev_mode:
            pool = pool + PHRASES_DEV
    return random.choice(pool)


def bubble(text: str, max_width: int = 28, tail: str = "right") -> list[str]:
    """Build a cowsay-style speech bubble. Returns lines of equal width.

    `tail` points back at bruno: "right" = bubble is on his left,
    "left" = bubble is on his right.
    """
    if max_width < 6:
        return []
    wrap_at = max(4, min(max_width - 4, 32))
    raw_lines = []
    for paragraph in text.splitlines() or [text]:
        if not paragraph:
            raw_lines.append("")
            continue
        wrapped = textwrap.wrap(paragraph, width=wrap_at) or [""]
        raw_lines.extend(wrapped)

    inner_width = max(len(line) for line in raw_lines)
    top = "╭" + "─" * (inner_width + 2) + "╮"
    bot = "╰" + "─" * (inner_width + 2) + "╯"
    middle = [f"│ {line.ljust(inner_width)} │" for line in raw_lines]

    if tail == "right":
        # Tail at bottom-right corner pointing at bruno who is to the right
        bot_tail_pos = inner_width + 1
        bot = bot[: bot_tail_pos] + "┴" + bot[bot_tail_pos + 1 :]
        tail_row = " " * (bot_tail_pos) + "╲"
        return [top, *middle, bot, tail_row]
    else:
        bot = bot[:1] + "┴" + bot[2:]
        tail_row = " ╱"
        return [top, *middle, bot, tail_row]
