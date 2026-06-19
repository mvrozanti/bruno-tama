"""Persistent bruno state.

Atomic JSON at $XDG_DATA_HOME/bruno/state.json (default ~/.local/share/bruno/state.json).
Survives process restart so hunger/energy/mood/age don't reset every launch.
"""
from __future__ import annotations
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

_STAT_KEYS = frozenset({"hunger", "energy", "mood"})

_FIELDS = (
    "hunger", "energy", "mood", "born_at_wall",
    "llm_backend", "llm_prompted_on",
)


def _lock_path() -> Path:
    return state_path().parent / "state.lock"


@contextmanager
def _locked():
    p = _lock_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return
    try:
        fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield
        return
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


def state_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "bruno" / "state.json"


def load() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in _FIELDS if k in data}


def save(state: dict) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    payload = {k: state[k] for k in _FIELDS if k in state}
    try:
        fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".json", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            os.replace(tmp, p)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        return


def save_delta(delta: dict) -> None:
    """Apply stat deltas and overwrite other fields, under exclusive flock.

    For _STAT_KEYS (hunger/energy/mood): adds delta to on-disk value and
    clamps to [0, 100]. Other keys overwrite the disk value directly.
    None values are skipped (preserves existing disk entry).

    This lets multiple concurrent owner processes each contribute only
    the changes THEY made without stomping each other's updates.
    """
    with _locked():
        current = load()
        merged = dict(current)
        for k, v in delta.items():
            if v is None:
                continue
            if k in _STAT_KEYS:
                merged[k] = max(0, min(100, current.get(k, v) + v))
            else:
                merged[k] = v
        save(merged)


def log_path() -> Path:
    return state_path().parent / "log"


def log_once(message: str, key: str) -> None:
    p = log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    marker = p.parent / f".logged.{key}"
    if marker.exists():
        return
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
        marker.touch()
    except OSError:
        pass
