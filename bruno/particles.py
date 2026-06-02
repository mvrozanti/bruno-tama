"""Particle system for Phase 3 polish.

Tiny short-lived single-cell glyphs that ride next to bruno: rising z's
during SLEEP, hearts/sparks when fed or petted, dust puffs at the cell
he just teleported away from. Each particle is a single character with
optional SGR; renderers paint them through the same cell-restore path
as the sprite so they leave no trails.

The system is unaware of shell content. Renderers in overlay mode are
expected to filter particle cells against pyte occupancy before drawing.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

DIM_GREY = "\x1b[2;37m"
DIM_BLUE = "\x1b[2;34m"
DIM_YELLOW = "\x1b[2;33m"
DIM_MAGENTA = "\x1b[2;35m"
DIM_RED = "\x1b[2;31m"
DIM_CYAN = "\x1b[2;36m"


@dataclass
class Particle:
    x: int
    y: int
    glyph: str
    ttl: int
    vx: int = 0
    vy: int = 0
    step_period: int = 4
    sgr: str | None = None
    age: int = 0


class ParticleSystem:
    def __init__(self, pane_w: int, pane_h: int):
        self.pane_w = pane_w
        self.pane_h = pane_h
        self.particles: list[Particle] = []

    def resize(self, pane_w: int, pane_h: int) -> None:
        self.pane_w = pane_w
        self.pane_h = pane_h
        self.particles = [
            p for p in self.particles
            if 0 <= p.x < pane_w and 0 <= p.y < pane_h
        ]

    def tick(self) -> None:
        survivors: list[Particle] = []
        for p in self.particles:
            p.age += 1
            p.ttl -= 1
            if p.ttl <= 0:
                continue
            if p.step_period > 0 and p.age % p.step_period == 0:
                p.x += p.vx
                p.y += p.vy
            if 0 <= p.x < self.pane_w and 0 <= p.y < self.pane_h:
                survivors.append(p)
        self.particles = survivors

    def clear(self) -> None:
        self.particles.clear()

    def cells(self) -> list[tuple[int, int, str, str | None]]:
        out: list[tuple[int, int, str, str | None]] = []
        for p in self.particles:
            if 0 <= p.x < self.pane_w and 0 <= p.y < self.pane_h:
                out.append((p.y, p.x, p.glyph, p.sgr))
        return out

    def spawn_sleep_z(self, bruno_x: int, bruno_y: int, bruno_w: int) -> None:
        if bruno_y <= 0:
            return
        x = bruno_x + random.randint(0, max(0, bruno_w - 1))
        glyph = random.choice(["z", "Z"])
        self.particles.append(Particle(
            x=x, y=bruno_y - 1, glyph=glyph,
            ttl=22, vx=0, vy=-1, step_period=6,
            sgr=DIM_BLUE,
        ))

    def spawn_pet_spark(self, bx: int, by: int, bw: int, bh: int) -> None:
        glyphs = ["*", "+", "✦"]
        for _ in range(4):
            dx = random.choice([-1, 0, 1, 2, bw, bw + 1])
            dy = random.choice([-1, 0, bh, bh - 1])
            self.particles.append(Particle(
                x=bx + dx, y=by + dy,
                glyph=random.choice(glyphs),
                ttl=10,
                vx=random.choice([-1, 0, 1]),
                vy=random.choice([-1, 0]),
                step_period=4,
                sgr=DIM_MAGENTA,
            ))

    def spawn_feed_spark(self, bx: int, by: int, bw: int, bh: int) -> None:
        for i in range(5):
            dx = random.choice([-1, 0, 1, 2, bw, bw + 1])
            dy = random.choice([-1, 0, 1, bh, bh - 1])
            glyph = "*" if i % 2 == 0 else "+"
            sgr = DIM_RED if i % 2 == 0 else DIM_YELLOW
            self.particles.append(Particle(
                x=bx + dx, y=by + dy,
                glyph=glyph,
                ttl=12,
                vx=0,
                vy=random.choice([-1, 0]),
                step_period=5,
                sgr=sgr,
            ))

    def spawn_dust(self, old_x: int, old_y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        ground_y = old_y + h - 1
        glyphs = ["·", "°", "."]
        count = random.randint(3, 5)
        for _ in range(count):
            dx = random.randint(0, max(0, w - 1))
            self.particles.append(Particle(
                x=old_x + dx, y=ground_y,
                glyph=random.choice(glyphs),
                ttl=6, vx=0, vy=0, step_period=0,
                sgr=DIM_GREY,
            ))
