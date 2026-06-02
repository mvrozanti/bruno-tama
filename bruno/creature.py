"""bruno's state machine.

Bruno picks a state, lives in it for a few hundred ticks, then transitions.
The state owns which sprite list to draw, and movement logic per tick.

Coordinates: (x, y) is the *top-left corner of bruno's bounding box*.
The bbox is sprite-sized (varies between states/forms).
"""
from __future__ import annotations
import datetime
import random
import time
from dataclasses import dataclass
from . import sprites
from .particles import ParticleSystem

IDLE = "idle"
WALK = "walk"
SLEEP = "sleep"
HUNGRY = "hungry"
HAPPY = "happy"
SQUISH = "squish"
LOOK = "look"

BABY = "baby"
ADULT = "adult"
ELDER = "elder"

BABY_MAX_DAYS = 7
ADULT_MAX_DAYS = 30


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
                 can_place=None, persisted: dict | None = None):
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

        p = persisted or {}
        self.hunger = int(p.get("hunger", 0))
        self.energy = int(p.get("energy", 100))
        self.mood = int(p.get("mood", 60))

        self.bumped = False
        # "h" or "v" — which axis the next SQUISH animation pulses on.
        # Set just before entering SQUISH based on impact direction.
        self.squish_axis = "h"

        self.speech = None
        self.speech_ticks = 0

        self.born_at = time.monotonic()
        # Wall-clock birth survives restart. `born_at` (monotonic) is for
        # in-process timing only; `born_at_wall` drives age_days / life_stage.
        try:
            self.born_at_wall = float(p.get("born_at_wall", time.time()))
        except (TypeError, ValueError):
            self.born_at_wall = time.time()

        self.particles = ParticleSystem(pane_w, pane_h)
        # Set by bruno:hide / bruno:show verb FIFO events. Either renderer
        # can OR-combine this with its own visibility toggles (e.g. SIGUSR1).
        self._hidden = False

    @property
    def facing(self) -> int:
        # 1 = right, -1 = left. Used by sprite picking only.
        return 1 if self.dx > 0 else (-1 if self.dx < 0 else self._last_facing)

    # ---- age ----

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.born_at_wall)

    @property
    def age_days(self) -> float:
        return self.age_seconds / 86400.0

    @property
    def life_stage(self) -> str:
        d = self.age_days
        if d <= BABY_MAX_DAYS:
            return BABY
        if d <= ADULT_MAX_DAYS:
            return ADULT
        return ELDER

    def persist_dict(self) -> dict:
        return {
            "hunger": self.hunger,
            "energy": self.energy,
            "mood": self.mood,
            "born_at_wall": self.born_at_wall,
        }

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

        table = sprites.STAGE_SPRITES.get(self.life_stage, sprites.STAGE_SPRITES[BABY])
        if self.state == SLEEP:
            return table["SLEEP"]
        if self.state == HUNGRY:
            return table["HUNGRY"]
        if self.state == HAPPY:
            return table["HAPPY"]
        if self.state == SQUISH:
            return table["SQUISH_V"] if self.squish_axis == "v" else table["SQUISH_H"]
        if self.state == LOOK:
            return table["LOOK_R"] if self.facing > 0 else table["LOOK_L"]
        if self.state == WALK:
            return table["WALK_R"] if self.facing > 0 else table["WALK_L"]
        return table["IDLE"]

    def current_frame(self) -> Frame:
        s = self._sprite_set()
        lines = list(s[self.frame_idx % len(s)])
        deco = sprites.decoration_for_today(self.born_at_wall)
        if deco is not None:
            sprite_w = max((len(line) for line in lines), default=0)
            lines = sprites.compose_decoration(lines, deco, sprite_w)
        return Frame(lines=lines)

    # ---- update ----

    def resize(self, pane_w: int, pane_h: int) -> None:
        self.pane_w = pane_w
        self.pane_h = pane_h
        # Clamp position into new bounds
        f = self.current_frame()
        self.x = max(0, min(self.x, max(0, pane_w - f.width)))
        self.y = max(0, min(self.y, max(0, pane_h - f.height)))
        self.particles.resize(pane_w, pane_h)

    def tick_once(self) -> None:
        self.tick += 1

        self.particles.tick()

        if self.state == SLEEP and self.tick % 30 == 0:
            f = self.current_frame()
            self.particles.spawn_sleep_z(self.x, self.y, f.width)

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
                    old_x, old_y = self.x, self.y
                    self.x, self.y = spot
                    self.particles.spawn_dust(old_x, old_y, f.width, f.height)
                self._pick_walk_direction()
                self._enter(WALK, random.randint(40, 120))

        if self.state_ticks <= 0 and self.state != SQUISH:
            self._pick_next_state()

    def _free_run(self, dx: int, dy: int, f: Frame, cap: int) -> int:
        """How many steps of (dx, dy) bruno can take before something blocks.
        Used to bias direction picking toward open space."""
        steps = 0
        x, y = self.x, self.y
        for _ in range(cap):
            x += dx
            y += dy
            if not self.can_place(x, y, f.width, f.height):
                break
            steps += 1
        return steps

    def _pick_walk_direction(self) -> None:
        """Pick a cardinal direction, weighted by how much open space lies
        that way. Heavily blocked directions stay possible (weight +1) so
        cramped panes don't deadlock the picker."""
        f = self.current_frame()
        cap = max(self.pane_w, self.pane_h)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        weights = [self._free_run(dx, dy, f, cap) + 1 for dx, dy in dirs]
        (self.dx, self.dy) = random.choices(dirs, weights=weights, k=1)[0]
        if self.dx != 0:
            self._last_facing = 1 if self.dx > 0 else -1

    def _walk_tick(self) -> None:
        f = self.current_frame()
        if self.tick % 2 == 0:
            new_x = self.x + self.dx
            new_y = self.y + self.dy
            out_of_bounds = (
                new_x < 0 or new_y < 0
                or new_x + f.width > self.pane_w
                or new_y + f.height > self.pane_h
            )
            if out_of_bounds:
                # Hit a pane edge, not an obstacle — turn around, no squish.
                self._pick_walk_direction()
                return
            if not self.can_place(new_x, new_y, f.width, f.height):
                # In-bounds rejection = shell content. Squish axis follows
                # impact direction.
                if self.dx != 0:
                    self._last_facing = 1 if self.dx > 0 else -1
                self.squish_axis = "v" if self.dy != 0 else "h"
                self._enter(SQUISH, 8)
                return
            self.x = new_x
            self.y = new_y

    def find_clear_spot(self, w: int, h: int, near_x: int | None = None,
                        near_y: int | None = None) -> tuple[int, int] | None:
        """Search the pane for a wxh rectangle the can_place predicate accepts.
        Returns (x, y) or None. Searches in expanding rings around (near_x, near_y)
        if given, else from the center, exiting on the first match."""
        cx = near_x if near_x is not None else self.pane_w // 2
        cy = near_y if near_y is not None else self.pane_h // 2
        max_r = max(self.pane_w, self.pane_h)
        if self.can_place(cx, cy, w, h):
            return cx, cy
        for radius in range(1, max_r):
            for dx in range(-radius, radius + 1):
                for y in (cy - radius, cy + radius):
                    if self.can_place(cx + dx, y, w, h):
                        return cx + dx, y
            for dy in range(-radius + 1, radius):
                for x in (cx - radius, cx + radius):
                    if self.can_place(x, cy + dy, w, h):
                        return x, cy + dy
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
        if self.life_stage == ELDER:
            # Slower animations and slower walks for old bruno.
            self.frame_period = int(self.frame_period * 1.5) or 1

    # ---- interactions ----

    def feed(self) -> None:
        self.hunger = max(0, self.hunger - 40)
        self.mood = min(100, self.mood + 15)
        self.say("om nom nom", ticks=40)
        f = self.current_frame()
        self.particles.spawn_feed_spark(self.x, self.y, f.width, f.height)
        self._enter(HAPPY, 30)

    def pet(self) -> None:
        self.mood = min(100, self.mood + 8)
        self.say(random.choice(["<3", "*purr*", ":3", "more please"]), ticks=30)
        f = self.current_frame()
        self.particles.spawn_pet_spark(self.x, self.y, f.width, f.height)
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

    # ---- Phase 2 reactions ----

    _FILETYPE_REACTIONS = {
        "py": "snake!",
        "rs": "crab!",
        "go": "gopher!",
        "md": "wordy.",
        "ts": "yarn.",
        "tsx": "yarn.",
        "js": "yarn.",
        "jsx": "yarn.",
        "nix": "snowflake!",
        "lua": "moon time",
        "c": "old school",
        "cpp": "++!",
        "h": "headers...",
        "sh": "shellz",
        "zsh": "shellz",
        "bash": "shellz",
        "html": "marked up",
        "css": "stylin'",
        "json": "{braces}",
        "yaml": "yamls",
        "yml": "yamls",
        "toml": "configgy",
        "sql": "tables!",
        "txt": "plain text",
    }

    def react_commit(self, branch: str | None = None, short: str | None = None) -> None:
        msg = "+1 commit!" if not short else f"+1 ({short})"
        self.say(msg, ticks=45)
        self.mood = min(100, self.mood + 4)
        self._enter(HAPPY, 25)

    def react_push(self) -> None:
        self.say("bye bytes!", ticks=45)
        self.mood = min(100, self.mood + 3)
        self._enter(HAPPY, 25)

    def react_branch(self, name: str | None = None) -> None:
        msg = f"new branch: {name}" if name else "new branch!"
        if len(msg) > 28:
            msg = msg[:27] + "…"
        self.say(msg, ticks=45)
        self._enter(LOOK, 20)

    def react_fail(self, code: int | None = None) -> None:
        self.say(random.choice(["oof.", "?!", "yikes", "*concerned*"]), ticks=40)
        self.mood = max(0, self.mood - 3)
        self._enter(LOOK, 18)

    def react_long_done(self, seconds: float) -> None:
        self.say(random.choice(["phew!", "done!", "finally."]), ticks=40)
        self._enter(HAPPY, 20)

    def react_filetype(self, ext: str) -> None:
        ext = ext.lower().lstrip(".")
        msg = self._FILETYPE_REACTIONS.get(ext, "neat.")
        self.say(msg, ticks=40)
        self._enter(LOOK, 18)

    def react_llm(self, text: str) -> None:
        self.say(text, ticks=60)

    # ---- Phase 4 magic-word reactions ----

    def react_stats(self) -> None:
        days = int(self.age_days)
        self.say(
            f"hp:{100 - self.hunger} en:{self.energy} mo:{self.mood} d:{days}",
            ticks=60,
        )

    def react_hide(self) -> None:
        if not self._hidden:
            self.say("*poof*", ticks=18)
        self._hidden = True

    def react_show(self) -> None:
        self._hidden = False
        self.say("*back!*", ticks=24)

    # ---- Phase 3 cell exports ----

    def particle_cells(self) -> list[tuple[int, int, str, str | None]]:
        return self.particles.cells()

    def aura_cells(self, sprite_w: int, sprite_h: int) \
            -> list[tuple[int, int, str, str | None]]:
        aura = sprites.aura_for(self.mood, self.hunger, self.energy,
                                self.life_stage, sprite_w)
        if aura is None:
            return []
        text, sgr = aura
        row_y = self.y + sprite_h
        if row_y < 0 or row_y >= self.pane_h:
            return []
        cells: list[tuple[int, int, str, str | None]] = []
        for dx, ch in enumerate(text):
            if ch == " ":
                continue
            col = self.x + dx
            if 0 <= col < self.pane_w:
                cells.append((row_y, col, ch, sgr))
        return cells
