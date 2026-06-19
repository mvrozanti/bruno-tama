"""Feed IPC: where the feed-signal file lives and how to send food to a
running bruno.

Every running bruno polls these paths each tick; anyone can drop text in
one (a food emoji to feed, anything else to burp) and bruno reacts. The
signal is intentionally PID-agnostic so a single drop wakes every bruno
on the machine.

Two paths are honored so feeding "just works" regardless of how you do
it: the XDG runtime path is primary, but `/tmp/bruno_feed` stays a
first-class fallback so muscle-memory `echo … > /tmp/bruno_feed` lands
even when XDG_RUNTIME_DIR is set.
"""
from __future__ import annotations
import os


def feed_paths() -> list[str]:
    """Candidate feed-signal paths, most-preferred first, deduped."""
    paths = []
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        paths.append(os.path.join(xdg, "bruno_feed"))
    paths.append("/tmp/bruno_feed")
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def open_readers() -> list[int]:
    """Open every feed path as a non-blocking read fd, truncating stale
    content so an old offering left in a file doesn't fire on startup."""
    fds: list[int] = []
    for p in feed_paths():
        try:
            fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK, 0o600)
            os.ftruncate(fd, 0)
            fds.append(fd)
        except OSError:
            continue
    return fds


def read_offering(fds: list[int]) -> str | None:
    """Return the first pending offering across `fds`, clearing the file it
    came from. None if nothing is waiting."""
    for fd in fds:
        try:
            chunk = os.read(fd, 256)
        except (BlockingIOError, OSError):
            continue
        if not chunk:
            continue
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass
        text = chunk.decode("utf-8", errors="replace").strip()
        if text:
            return text
    return None


def send(text: str) -> str | None:
    """Write `text` to the primary writable feed path (so it's consumed
    once, not once per path). Returns the path written, or None."""
    data = text.encode("utf-8")
    for p in feed_paths():
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            return p
        except OSError:
            continue
    return None
