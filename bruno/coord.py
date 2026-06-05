"""Window-scoped bruno coordination.

When multiple overlay processes run inside the same tmux window (one
per pane) we want exactly one visible bruno per window, not one per
pane. This module owns the shared runtime file each window's bruno
processes use to elect a single owner, forward shell-hook reactions
from non-owners, and pass bruno across pane boundaries via a handoff
payload.

Stats (hunger/mood/energy/age) keep flowing through state.json — that
file is already shared across processes; only position and animation
state live here, and only for the lifetime of the tmux window.

File layout: $XDG_RUNTIME_DIR/bruno_window_<window_id>.json. Atomic
RMW guarded by a sidecar flock file. Wall-clock leases (time.time)
because different processes have independent monotonic clocks.
"""
from __future__ import annotations
import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

LEASE_SECONDS = 3.0


def _runtime_root() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base)


def _safe(window_id: str) -> str:
    return window_id.replace("/", "_").replace("@", "at")


def state_path(window_id: str) -> Path:
    return _runtime_root() / f"bruno_window_{_safe(window_id)}.json"


def lock_path(window_id: str) -> Path:
    return _runtime_root() / f"bruno_window_{_safe(window_id)}.lock"


@contextmanager
def _file_lock(path: Path):
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def _read(window_id: str) -> dict:
    p = state_path(window_id)
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(window_id: str, data: dict) -> None:
    p = state_path(window_id)
    tmp = p.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def _mutate(window_id: str, mutator):
    with _file_lock(lock_path(window_id)):
        data = _read(window_id)
        result = mutator(data)
        if result is None:
            result = data
        _write(window_id, result)
        return result


def claim_or_refresh(window_id: str, pid: int, pane_id: str) -> bool:
    """Become owner (or extend lease if already owner). Returns True if I
    am owner after the call.

    Anyone may claim if the slot is unowned, the current owner's lease
    expired, or the current owner pid is dead. Otherwise the existing
    owner keeps the slot.
    """
    now = time.time()

    def mut(data):
        owner_pid = data.get("owner_pid")
        lease = data.get("owner_lease_until", 0) or 0
        if owner_pid == pid:
            data["owner_lease_until"] = now + LEASE_SECONDS
            data["owner_pane_id"] = pane_id
            return data
        if owner_pid and _pid_alive(owner_pid) and lease > now:
            return data
        data["owner_pid"] = pid
        data["owner_pane_id"] = pane_id
        data["owner_lease_until"] = now + LEASE_SECONDS
        return data

    after = _mutate(window_id, mut)
    return after.get("owner_pid") == pid


def release(window_id: str, pid: int) -> None:
    def mut(data):
        if data.get("owner_pid") == pid:
            data["owner_pid"] = None
            data["owner_pane_id"] = None
            data["owner_lease_until"] = 0
        return data

    try:
        _mutate(window_id, mut)
    except OSError:
        pass


def owner_pane_id(window_id: str) -> str | None:
    data = _read(window_id)
    pid = data.get("owner_pid")
    if not _pid_alive(pid):
        return None
    return data.get("owner_pane_id")


def push_event(window_id: str, method_name: str, method_args) -> None:
    """Non-owners queue reactions for the owner to apply. Bounded so a
    stuck owner can't grow the file without limit."""
    def mut(data):
        events = data.get("pending_events") or []
        events.append({"name": method_name, "args": list(method_args)})
        if len(events) > 64:
            events = events[-64:]
        data["pending_events"] = events
        return data

    try:
        _mutate(window_id, mut)
    except OSError:
        pass


def drain_events(window_id: str, pid: int):
    """Owner pops every queued event."""
    drained = []

    def mut(data):
        nonlocal drained
        if data.get("owner_pid") != pid:
            return data
        drained = data.get("pending_events") or []
        data["pending_events"] = []
        return data

    try:
        _mutate(window_id, mut)
    except OSError:
        return []
    return drained


def post_handoff(window_id: str, from_pid: int, to_pane_id: str,
                 entry_x: int, entry_y: int, dx: int, dy: int) -> None:
    """Outgoing owner hands the creature to a sibling pane.

    Clears its own ownership so the receiver can claim freely. The
    handoff blob carries the entry coordinates and direction in the
    receiver pane's local frame so bruno appears continuing his walk.
    """
    def mut(data):
        if data.get("owner_pid") == from_pid:
            data["owner_pid"] = None
            data["owner_pane_id"] = to_pane_id
            data["owner_lease_until"] = 0
        handoffs = data.get("handoffs") or {}
        handoffs[to_pane_id] = {
            "x": int(entry_x),
            "y": int(entry_y),
            "dx": int(dx),
            "dy": int(dy),
            "posted_at": time.time(),
        }
        data["handoffs"] = handoffs
        return data

    try:
        _mutate(window_id, mut)
    except OSError:
        pass


def pop_handoff(window_id: str, my_pane_id: str) -> dict | None:
    """Receiver consumes a pending handoff addressed to its pane."""
    popped: dict | None = None

    def mut(data):
        nonlocal popped
        handoffs = data.get("handoffs") or {}
        if my_pane_id in handoffs:
            popped = handoffs.pop(my_pane_id)
            data["handoffs"] = handoffs
        return data

    try:
        _mutate(window_id, mut)
    except OSError:
        return None
    return popped
