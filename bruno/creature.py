"""bruno's state machine.

Bruno picks a state, lives in it for a few hundred ticks, then transitions.
The state owns which sprite list to draw, and movement logic per tick.

Coordinates: (x, y) is the *top-left corner of bruno's bounding box*.
The bbox is sprite-sized (varies between states/forms).
"""
from __future__ import annotations
import random
import time
from dataclasses import dataclass
from . import sprites

IDLE = "idle"
WALK = "walk"
SLEEP = "sleep"
HUNGRY = "hungry"
HAPPY = "happy"
SQUISH = "squish"
LOOK = "look"


@dataclass
class Frame:
    lines: list[str]

    @property
    def width(self) -> int:
        return max((len(line) for line in self.lines), default=0)

    @property
    def height(self) -> int:
        return len(self.lines)


class Bruno:
    def __init__(self, pane_w: int, pane_h: int, dev_mode: bool = False,
                 can_place=None):
        self.pane_w = pane_w
        self.pane_h = pane_h
        self.dev_mode = dev_mode
        # Callback signature: can_place(x, y, w, h) -> bool. The default
        # checks pane bounds only; overlay mode passes one that also rejects
        # rectangles that overlap shell content.
        self.can_place = can_place or (
            lambda x, y, w, h: x >= 0 and y >= 0
            and x + w <= self.pane_w and y + h <= self.pane_h
        )

        self.x = pane_w // 2
        self.y = pane_h // 2
        # Cardinal walking direction. Exactly one of dx/dy is non-zero.
        # +x is right, +y is down. `_last_facing` is what sprite-picking
        # uses when bruno is moving vertically (he keeps the last L/R look).
        self.dx = 1
        self.dy = 0
        self._last_facing = 1

        self.state = IDLE
        self.state_ticks = 60
        self.frame_idx = 0
        self.tick = 0
        self.frame_period = 4

        self.hunger = 0      # 0..100, 100 = starving
        self.energy = 100    # 0..100, 0 = exhausted
        self.mood = 60       # 0..100

        self.bumped = False

        self.speech = None
        self.speech_ticks = 0

        self.born_at = time.monotonic()

    @property
    def facing(self) -> int:
        # 1 = right, -1 = left. Used by sprite picking only.
        return 1 if self.dx > 0 else (-1 if self.dx < 0 else self._last_facing)

    # ---- sprite selection ----

    def _sprite_set(self) -> list[list[str]]:
        # Pick form based on available pane size
        max_h = max(1, self.pane_h - 2)
        max_w = max(1, self.pane_w - 2)
        if max_h < 3 or max_w < 5:
            if max_w < 3:
                return sprites.NANO
            if max_w < 5:
                return sprites.MICRO
            return sprites.TINY

        if self.state == SLEEP:
            return sprites.SLEEP
        if self.state == HUNGRY:
            return sprites.HUNGRY
        if self.state == HAPPY:
            return sprites.HAPPY
        if self.state == SQUISH:
            return sprites.SQUISH_R if self._last_facing > 0 else sprites.SQUISH_L
        if self.state == LOOK:
            return sprites.LOOK_R if self.facing > 0 else sprites.LOOK_L
        if self.state == WALK:
            return sprites.WALK_R if self.facing > 0 else sprites.WALK_L
        return sprites.IDLE

    def current_frame(self) -> Frame:
        s = self._sprite_set()
        return Frame(lines=list(s[self.frame_idx % len(s)]))

    # ---- update ----

    def resize(self, pane_w: int, pane_h: int) -> None:
        self.pane_w = pane_w
        self.pane_h = pane_h
        # Clamp position into new bounds
        f = self.current_frame()
        self.x = max(0, min(self.x, max(0, pane_w - f.width)))
        self.y = max(0, min(self.y, max(0, pane_h - f.height)))

    def tick_once(self) -> None:
        self.tick += 1

        if self.tick % self.frame_period == 0:
            self.frame_idx += 1

        # Drift stats slowly. Tick is 100ms so 600 ticks = 1 minute.
        if self.tick % 60 == 0:
            self.hunger = min(100, self.hunger + 1)
            if self.state == SLEEP:
                self.energy = min(100, self.energy + 2)
            else:
                self.energy = max(0, self.energy - 1)
            if self.hunger > 70:
                self.mood = max(0, self.mood - 1)
            elif self.energy < 20:
                self.mood = max(0, self.mood - 1)
            else:
                self.mood = min(100, self.mood + 1)

        # Speech timeout
        if self.speech_ticks > 0:
            self.speech_ticks -= 1
            if self.speech_ticks == 0:
                self.speech = None

        # State logic
        self.state_ticks -= 1
        if self.state == WALK:
            self._walk_tick()
        elif self.state == SQUISH:
            if self.state_ticks <= 0:
                # Spirit of bruno: squish → teleport to clear space.
                # If overlay.py's displacement check finds no space later,
                # it'll hide him; meanwhile we just relocate.
                f = Frame(lines=list(sprites.IDLE[0]))
                spot = self.find_clear_spot(f.width, f.height,
                                            near_x=self.x, near_y=self.y)
                if spot is not None:
                    self.x, self.y = spot
                self._pick_walk_direction()
                self._enter(WALK, random.randint(40, 120))

        if self.state_ticks <= 0 and self.state != SQUISH:
            self._pick_next_state()

    def _pick_walk_direction(self) -> None:
        """Pick one of the four cardinal directions for the next walk."""
        self.dx, self.dy = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
        if self.dx != 0:
            self._last_facing = 1 if self.dx > 0 else -1

    def _walk_tick(self) -> None:
        f = self.current_frame()
        if self.tick % 2 == 0:
            new_x = self.x + self.dx
            new_y = self.y + self.dy
            if not self.can_place(new_x, new_y, f.width, f.height):
                # Remember which way we were facing for the squish sprite.
                if self.dx != 0:
                    self._last_facing = 1 if self.dx > 0 else -1
                self._enter(SQUISH, 8)
                return
            self.x = new_x
            self.y = new_y

    def find_clear_spot(self, w: int, h: int, near_x: int | None = None,
                        near_y: int | None = None) -> tuple[int, int] | None:
        """Search the pane for a wxh rectangle the can_place predicate accepts.
        Returns (x, y) or None. Searches in expanding rings around (near_x, near_y)
        if given, else from the center."""
        cx = near_x if near_x is not None else self.pane_w // 2
        cy = near_y if near_y is not None else self.pane_h // 2
        # Search candidates in spiral-ish order
        candidates = []
        for radius in range(max(self.pane_w, self.pane_h)):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    candidates.append((cx + dx, cy + dy))
        for x, y in candidates:
            if self.can_place(x, y, w, h):
                return x, y
        return None

    def _pick_next_state(self) -> None:
        # Bias state by stats
        if self.energy < 15:
            self._enter(SLEEP, random.randint(150, 350))
            return
        if self.hunger > 75:
            self._enter(HUNGRY, random.randint(40, 100))
            return
        if self.mood > 80 and random.random() < 0.4:
            self._enter(HAPPY, random.randint(30, 70))
            return

        choice = random.random()
        if choice < 0.7:
            self._pick_walk_direction()
            self._enter(WALK, random.randint(60, 180))
        elif choice < 0.85:
            self._enter(IDLE, random.randint(30, 70))
        elif choice < 0.95:
            self._last_facing = random.choice([-1, 1])
            self.dx = self.dy = 0
            self._enter(LOOK, random.randint(20, 50))
        else:
            self._enter(SLEEP, random.randint(80, 200))

    def _enter(self, state: str, ticks: int) -> None:
        self.state = state
        self.state_ticks = ticks
        self.frame_idx = 0
        # Different states animate at different cadences
        if state == SLEEP:
            self.frame_period = 8
        elif state == SQUISH:
            self.frame_period = 4
        elif state == WALK:
            self.frame_period = 4
        else:
            self.frame_period = 6

    # ---- interactions ----

    def feed(self) -> None:
        self.hunger = max(0, self.hunger - 40)
        self.mood = min(100, self.mood + 15)
        self.say("om nom nom", ticks=40)
        self._enter(HAPPY, 30)

    def pet(self) -> None:
        self.mood = min(100, self.mood + 8)
        self.say(random.choice(["<3", "*purr*", ":3", "more please"]), ticks=30)
        self._enter(HAPPY, 25)

    def poke(self) -> None:
        self.mood = max(0, self.mood - 5)
        self.say(random.choice(["hey!", "rude.", "hmph", "*glare*"]), ticks=30)
        self._enter(LOOK, 20)

    def wake(self) -> None:
        if self.state == SLEEP:
            self.say("...what?", ticks=30)
            self._enter(IDLE, 60)

    def say(self, text: str, ticks: int = 60) -> None:
        self.speech = text
        self.speech_ticks = ticks
