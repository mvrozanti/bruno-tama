"""Frame data for bruno.

Every frame is a list of strings, top-to-bottom. Bruno's main forms live
on a 5-column wide, 3-row tall bounding box so frame swaps within a state
don't reflow. SQUISH animations intentionally use smaller boxes to
read as impact.

Three life stages share the same bounding-box dimensions but differ in
glyph choice — Baby is the kawaii rounded form, Adult has bolder eyes
and a wider grin, Elder has half-closed eyes and a whisker droop.

Smaller fallbacks (TINY/MICRO/NANO) are used when the pane is too cramped
for the full-size form. Stage is ignored for those — too few cells.

Holiday decorations (santa hat, pumpkin, birthday cake) get composited
as an extra row above the sprite by `compose_decoration`.
"""
from __future__ import annotations
import datetime

# ---------------- BABY (default form, kawaii blob) ----------------

BABY_IDLE = [
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│-‿-│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
]

BABY_WALK_R = [
    ["╭───╮", "│◕‿◕│", "╰┴─┬╯"],
    ["╭───╮", "│◕‿◕│", "╰┬─┴╯"],
]

BABY_WALK_L = [
    ["╭───╮", "│◕‿◕│", "╰┴─┬╯"],
    ["╭───╮", "│◕‿◕│", "╰┬─┴╯"],
]

BABY_LOOK_R = [
    ["╭───╮", "│ ◕◕│", "╰───╯"],
]

BABY_LOOK_L = [
    ["╭───╮", "│◕◕ │", "╰───╯"],
]

BABY_SLEEP = [
    ["╭z──╮", "│-‿-│", "╰───╯"],
    ["╭z──╮", "│-‿-│", "╰───╯"],
    ["╭─z─╮", "│-‿-│", "╰───╯"],
    ["╭─z─╮", "│-‿-│", "╰───╯"],
    ["╭──z╮", "│-‿-│", "╰───╯"],
    ["╭──z╮", "│-‿-│", "╰───╯"],
    ["╭───╮", "│-‿-│", "╰───╯"],
]

BABY_HUNGRY = [
    ["╭───╮", "│◕ʖ◕│", "╰───╯"],
    ["╭───╮", "│◕○◕│", "╰───╯"],
]

BABY_HAPPY = [
    ["╭───╮", "│^◡^│", "╰───╯"],
    ["╭───╮", "│^‿^│", "╰───╯"],
]

BABY_SQUISH_H = [
    ["╭──╮", "│◕◕│", "╰──╯"],
    ["╭─╮", "│◉│", "╰─╯"],
    ["╭──╮", "│◕◕│", "╰──╯"],
]

BABY_SQUISH_V = [
    ["╭───╮", "│◉‿◉│", "╰───╯"],
    ["╭───╮", "╰─◉─╯"],
    ["╭───╮", "│◉‿◉│", "╰───╯"],
]

# ---------------- ADULT (8-30 days, bolder face) ----------------

ADULT_IDLE = [
    ["╭───╮", "│◉◡◉│", "╰───╯"],
    ["╭───╮", "│◉◡◉│", "╰───╯"],
    ["╭───╮", "│◉◡◉│", "╰───╯"],
    ["╭───╮", "│-◡-│", "╰───╯"],
    ["╭───╮", "│◉◡◉│", "╰───╯"],
    ["╭───╮", "│◉◡◉│", "╰───╯"],
]

ADULT_WALK_R = [
    ["╭───╮", "│◉◡◉│", "╰┴─┬╯"],
    ["╭───╮", "│◉◡◉│", "╰┬─┴╯"],
]

ADULT_WALK_L = [
    ["╭───╮", "│◉◡◉│", "╰┴─┬╯"],
    ["╭───╮", "│◉◡◉│", "╰┬─┴╯"],
]

ADULT_LOOK_R = [
    ["╭───╮", "│ ◉◉│", "╰───╯"],
]

ADULT_LOOK_L = [
    ["╭───╮", "│◉◉ │", "╰───╯"],
]

ADULT_SLEEP = [
    ["╭z──╮", "│-◡-│", "╰───╯"],
    ["╭z──╮", "│-◡-│", "╰───╯"],
    ["╭─z─╮", "│-◡-│", "╰───╯"],
    ["╭─z─╮", "│-◡-│", "╰───╯"],
    ["╭──z╮", "│-◡-│", "╰───╯"],
    ["╭──z╮", "│-◡-│", "╰───╯"],
    ["╭───╮", "│-◡-│", "╰───╯"],
]

ADULT_HUNGRY = [
    ["╭───╮", "│◉ʖ◉│", "╰───╯"],
    ["╭───╮", "│◉○◉│", "╰───╯"],
]

ADULT_HAPPY = [
    ["╭───╮", "│★◡★│", "╰───╯"],
    ["╭───╮", "│★‿★│", "╰───╯"],
]

ADULT_SQUISH_H = [
    ["╭──╮", "│◉◉│", "╰──╯"],
    ["╭─╮", "│◎│", "╰─╯"],
    ["╭──╮", "│◉◉│", "╰──╯"],
]

ADULT_SQUISH_V = [
    ["╭───╮", "│◎◡◎│", "╰───╯"],
    ["╭───╮", "╰─◎─╯"],
    ["╭───╮", "│◎◡◎│", "╰───╯"],
]

# ---------------- ELDER (30+ days, drooped + whiskers) ----------------

ELDER_IDLE = [
    ["╭~─~╮", "│•⌣•│", "╰───╯"],
    ["╭~─~╮", "│•⌣•│", "╰───╯"],
    ["╭~─~╮", "│-⌣-│", "╰───╯"],
    ["╭~─~╮", "│-⌣-│", "╰───╯"],
    ["╭~─~╮", "│•⌣•│", "╰───╯"],
    ["╭~─~╮", "│•⌣•│", "╰───╯"],
]

ELDER_WALK_R = [
    ["╭~─~╮", "│•⌣•│", "╰┴─┬╯"],
    ["╭~─~╮", "│•⌣•│", "╰┬─┴╯"],
]

ELDER_WALK_L = [
    ["╭~─~╮", "│•⌣•│", "╰┴─┬╯"],
    ["╭~─~╮", "│•⌣•│", "╰┬─┴╯"],
]

ELDER_LOOK_R = [
    ["╭~─~╮", "│ ••│", "╰───╯"],
]

ELDER_LOOK_L = [
    ["╭~─~╮", "│•• │", "╰───╯"],
]

ELDER_SLEEP = [
    ["╭z~~╮", "│-⌣-│", "╰───╯"],
    ["╭z~~╮", "│-⌣-│", "╰───╯"],
    ["╭~z~╮", "│-⌣-│", "╰───╯"],
    ["╭~z~╮", "│-⌣-│", "╰───╯"],
    ["╭~~z╮", "│-⌣-│", "╰───╯"],
    ["╭~~z╮", "│-⌣-│", "╰───╯"],
    ["╭~─~╮", "│-⌣-│", "╰───╯"],
]

ELDER_HUNGRY = [
    ["╭~─~╮", "│•ʖ•│", "╰───╯"],
    ["╭~─~╮", "│•○•│", "╰───╯"],
]

ELDER_HAPPY = [
    ["╭~─~╮", "│^⌣^│", "╰───╯"],
    ["╭~─~╮", "│^‿^│", "╰───╯"],
]

ELDER_SQUISH_H = [
    ["╭~~╮", "│••│", "╰──╯"],
    ["╭─╮", "│○│", "╰─╯"],
    ["╭~~╮", "│••│", "╰──╯"],
]

ELDER_SQUISH_V = [
    ["╭~─~╮", "│○⌣○│", "╰───╯"],
    ["╭~─~╮", "╰─○─╯"],
    ["╭~─~╮", "│○⌣○│", "╰───╯"],
]

# Stage → state lookup table. Keeps _sprite_set() in creature.py simple.
STAGE_SPRITES = {
    "baby": {
        "IDLE": BABY_IDLE,
        "WALK_R": BABY_WALK_R,
        "WALK_L": BABY_WALK_L,
        "LOOK_R": BABY_LOOK_R,
        "LOOK_L": BABY_LOOK_L,
        "SLEEP": BABY_SLEEP,
        "HUNGRY": BABY_HUNGRY,
        "HAPPY": BABY_HAPPY,
        "SQUISH_H": BABY_SQUISH_H,
        "SQUISH_V": BABY_SQUISH_V,
    },
    "adult": {
        "IDLE": ADULT_IDLE,
        "WALK_R": ADULT_WALK_R,
        "WALK_L": ADULT_WALK_L,
        "LOOK_R": ADULT_LOOK_R,
        "LOOK_L": ADULT_LOOK_L,
        "SLEEP": ADULT_SLEEP,
        "HUNGRY": ADULT_HUNGRY,
        "HAPPY": ADULT_HAPPY,
        "SQUISH_H": ADULT_SQUISH_H,
        "SQUISH_V": ADULT_SQUISH_V,
    },
    "elder": {
        "IDLE": ELDER_IDLE,
        "WALK_R": ELDER_WALK_R,
        "WALK_L": ELDER_WALK_L,
        "LOOK_R": ELDER_LOOK_R,
        "LOOK_L": ELDER_LOOK_L,
        "SLEEP": ELDER_SLEEP,
        "HUNGRY": ELDER_HUNGRY,
        "HAPPY": ELDER_HAPPY,
        "SQUISH_H": ELDER_SQUISH_H,
        "SQUISH_V": ELDER_SQUISH_V,
    },
}

# Back-compat aliases. Nothing external imports these directly today
# (verified by grep), but keep them in case downstream tooling does.
IDLE = BABY_IDLE
WALK_R = BABY_WALK_R
WALK_L = BABY_WALK_L
LOOK_R = BABY_LOOK_R
LOOK_L = BABY_LOOK_L
SLEEP = BABY_SLEEP
HUNGRY = BABY_HUNGRY
HAPPY = BABY_HAPPY
SQUISH_H = BABY_SQUISH_H
SQUISH_V = BABY_SQUISH_V

POP = [
    ["╭───╮", "│o‿o│", "╰───╯"],
    ["╭───╮", "│◯‿◯│", "╰───╯"],
]

# Compressed forms for cramped panes
TINY = [["(◕‿◕)"], ["(-‿-)"], ["(◕‿◕)"], ["(◕‿◕)"]]
MICRO = [["(•)"], ["(•)"], ["(-)"], ["(•)"]]
NANO = [["◉"], ["◉"], ["◌"], ["◉"]]


# ---------------- Holiday decorations ----------------
#
# A decoration is one extra row prepended above the sprite. Width is
# arbitrary; compose_decoration centers it and pads the sprite if
# narrower than the decoration.

SANTA_HAT = "⊳▣◀"     # tiny santa-hat silhouette
PUMPKIN_HAT = "🎃"     # one-cell pumpkin emoji (terminals may render double-wide)
BIRTHDAY_CAKE = "🎂"   # birthday-cake emoji


def decoration_for_today(born_at_wall: float) -> str | None:
    today = datetime.date.today()
    try:
        birth = datetime.datetime.fromtimestamp(born_at_wall).date()
    except (OverflowError, OSError, ValueError):
        birth = today
    # Birthday wins over other holidays so the special day feels special.
    # Skip on day-of-birth itself (would be every fresh run on day 0).
    if (today.month, today.day) == (birth.month, birth.day) and today != birth:
        return BIRTHDAY_CAKE
    if today.month == 12 and 1 <= today.day <= 25:
        return SANTA_HAT
    if today.month == 10 and today.day == 31:
        return PUMPKIN_HAT
    return None


def aura_for(mood: int, hunger: int, energy: int, life_stage: str,
             sprite_w: int) -> tuple[str, str | None] | None:
    """Decoration row painted below bruno's bottom row.

    Returns (row_text, sgr_escape) or None. row_text width matches
    sprite_w; cells whose glyph is a space are skipped by the renderer
    so the aura blends with whatever sits underneath. SGR is a raw
    fragment like '\\x1b[2;34m' — renderer appends a reset.

    Picked by priority: low mood beats hungry beats tired beats happy.
    """
    if sprite_w <= 0:
        return None
    if mood < 25:
        return ("." * sprite_w, "\x1b[2;37m")
    if hunger > 75:
        return ("~" * sprite_w, "\x1b[2;33m")
    if energy < 20:
        return ("_" * sprite_w, "\x1b[2;34m")
    if mood > 85:
        return ("·" * sprite_w, "\x1b[2;35m")
    return None


def compose_decoration(lines: list[str], deco: str, sprite_width: int) -> list[str]:
    """Prepend `deco` centered above `lines`, padding to match sprite_width."""
    if not deco or not lines:
        return lines
    deco_w = len(deco)
    width = max(sprite_width, deco_w)
    left_pad = (width - deco_w) // 2
    deco_row = (" " * left_pad) + deco + (" " * (width - left_pad - deco_w))
    if sprite_width >= width:
        return [deco_row] + lines
    extra = width - sprite_width
    pad_l = extra // 2
    pad_r = extra - pad_l
    padded = [(" " * pad_l) + line + (" " * pad_r) for line in lines]
    return [deco_row] + padded
