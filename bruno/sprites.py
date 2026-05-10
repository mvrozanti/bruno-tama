"""Frame data for bruno.

Every frame is a list of strings, top-to-bottom. All sprites live on a
5-column wide, 3-row tall bounding box so frame swaps don't reflow.
The leading space is intentional padding for asymmetric tail glyphs.

Smaller fallbacks (TINY/MICRO/NANO) are used when the pane is too cramped
for the full-size form.
"""

IDLE = [
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│-‿-│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
    ["╭───╮", "│◕‿◕│", "╰───╯"],
]

WALK_R = [
    ["╭───╮", "│◕‿◕│", "╰┴─┬╯"],
    ["╭───╮", "│◕‿◕│", "╰┬─┴╯"],
]

WALK_L = [
    ["╭───╮", "│◕‿◕│", "╰┴─┬╯"],
    ["╭───╮", "│◕‿◕│", "╰┬─┴╯"],
]

LOOK_R = [
    ["╭───╮", "│ ◕◕│", "╰───╯"],
]

LOOK_L = [
    ["╭───╮", "│◕◕ │", "╰───╯"],
]

SLEEP = [
    ["╭z──╮", "│-‿-│", "╰───╯"],
    ["╭z──╮", "│-‿-│", "╰───╯"],
    ["╭─z─╮", "│-‿-│", "╰───╯"],
    ["╭─z─╮", "│-‿-│", "╰───╯"],
    ["╭──z╮", "│-‿-│", "╰───╯"],
    ["╭──z╮", "│-‿-│", "╰───╯"],
    ["╭───╮", "│-‿-│", "╰───╯"],
]

HUNGRY = [
    ["╭───╮", "│◕ʖ◕│", "╰───╯"],
    ["╭───╮", "│◕○◕│", "╰───╯"],
]

HAPPY = [
    ["╭───╮", "│^◡^│", "╰───╯"],
    ["╭───╮", "│^‿^│", "╰───╯"],
]

# Squish frames are 4 wide (squash stage) and 3 wide (full smush).
# Dimensions intentionally vary so impact reads visually.
SQUISH_R = [
    ["╭──╮", "│◕◕│", "╰──╯"],
    ["╭─╮", "│◉│", "╰─╯"],
    ["╭──╮", "│◕◕│", "╰──╯"],
]

SQUISH_L = [
    ["╭──╮", "│◕◕│", "╰──╯"],
    ["╭─╮", "│◉│", "╰─╯"],
    ["╭──╮", "│◕◕│", "╰──╯"],
]

POP = [
    ["╭───╮", "│o‿o│", "╰───╯"],
    ["╭───╮", "│◯‿◯│", "╰───╯"],
]

# Compressed forms for cramped panes
TINY = [["(◕‿◕)"], ["(-‿-)"], ["(◕‿◕)"], ["(◕‿◕)"]]
MICRO = [["(•)"], ["(•)"], ["(-)"], ["(•)"]]
NANO = [["◉"], ["◉"], ["◌"], ["◉"]]
