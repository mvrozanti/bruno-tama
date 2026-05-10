# bruno

```
                                                    ╭───╮
                                                    │◕‿◕│
                                                    ╰───╯
```

A small ASCII creature that lives on top of your shell.

Bruno walks through whatever empty space your terminal isn't using. When
your output blocks his way he squishes against it, then teleports
somewhere clearer. When the pane is full he just hides until there's
room again. He doesn't get in your way — that's kind of the whole point.

```
   ~/Projects/bruno   master                 ╭───────────╮
$ ls                                         │ hello :)  │
bruno  flake.nix  pyproject.toml             ╰┴──────────╯
                                                ╲     ╭───╮
$ |                                                   │◕‿◕│
                                                      ╰┬─┴╯
```

## install

```sh
# anywhere with python (recommended)
pipx install bruno-tama

# nix
nix run github:mvrozanti/bruno-tama

# from a clone
git clone https://github.com/mvrozanti/bruno-tama
cd bruno-tama
pipx install .
```

Then run:

```sh
bruno
```

That wraps your shell — keep typing as usual. `exit` or `Ctrl-D` to
leave; bruno tears down cleanly and gives you your terminal back.

## how he works

Bruno forks `$SHELL` (your real login shell, picked from `/etc/passwd`,
not whatever `$SHELL` got polluted to) inside a PTY. Output flows
through to your terminal _and_ through a [pyte][pyte] virtual screen
that tracks which cells contain shell text. Each tick, bruno looks at
that grid, picks a clear rectangle, and overlays his sprite there with
ANSI cursor-positioning — `ESC 7` save, draw, `ESC 8` restore — so the
shell never notices.

[pyte]: https://github.com/selectel/pyte

## physics

Three rules. Everything else is detail.

| | |
|-|-|
| **walk**             | four cardinals, no diagonals. He picks a direction and goes. |
| **squish + teleport**| when blocked, he squishes for a beat, then teleports to a clear spot. |
| **hide**             | no clear spot anywhere → he doesn't render. He reappears when room opens up. |

## moods

He gets hungry. He gets sleepy. Sometimes he's happy. Occasionally he
says something in a speech bubble — but only if there's empty space
next to him for the bubble to fit. Nothing he says is important; he's
just keeping you company.

State machine lives in [`bruno/creature.py`](bruno/creature.py).

## flags

```
bruno --dev        programmer-themed phrases
bruno --no-tmux    skip sibling-pane activity awareness
bruno --fps N      tick rate (default 10)
bruno --mode pane  legacy: bruno owns the whole pane (no shell wrap)
```

## requirements

- python ≥ 3.10
- [`pyte`][pyte] (installed automatically)
- a terminal that does ANSI cursor-positioning and a unicode font with
  box-drawing glyphs (any modern emulator)

Tested mostly inside tmux, but bruno doesn't actually require it —
he'll happily live in a plain terminal pane too.

## name

Named for Bruno. He'd find the empty space too.

## license

MIT.
