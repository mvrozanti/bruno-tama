"""Shell precmd hook glue for Phase 2 context-awareness.

Spawns a FIFO that the child bash/zsh writes one line per command into.
Format: `cmd\\t<exit>\\t<duration_ms>\\t<command>\\n`.

Overlay reads the FIFO non-blocking each tick and dispatches reactions.
"""
from __future__ import annotations

import errno
import os
import re
import shlex
import shutil
import stat
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


RUNTIME_BASE_ENV = "XDG_RUNTIME_DIR"


def _runtime_base() -> Path:
    base = os.environ.get(RUNTIME_BASE_ENV)
    if base:
        return Path(base)
    return Path(tempfile.gettempdir())


@dataclass
class HookInstall:
    fifo_path: Path
    work_dir: Path
    env_updates: dict[str, str]
    zdotdir_tempdir: Path | None = None  # cleanup target

    def cleanup(self) -> None:
        try:
            if self.fifo_path.exists():
                self.fifo_path.unlink()
        except OSError:
            pass
        if self.zdotdir_tempdir is not None:
            try:
                for f in self.zdotdir_tempdir.iterdir():
                    try:
                        f.unlink()
                    except OSError:
                        pass
                self.zdotdir_tempdir.rmdir()
            except OSError:
                pass
        try:
            self.work_dir.rmdir()
        except OSError:
            pass


def _rc_path(name: str) -> Path | None:
    """Locate bundled rc snippet path. Works in both editable and installed installs."""
    try:
        with resources.as_file(resources.files("bruno").joinpath("rc", name)) as p:
            if p.exists():
                return Path(p)
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass
    fallback = Path(__file__).parent / "rc" / name
    return fallback if fallback.exists() else None


def install(shell_path: str) -> HookInstall | None:
    """Prepare FIFO + env vars for the child shell. Returns None on failure.

    Caller injects `install.env_updates` into the child env, then reads
    `install.fifo_path` non-blocking each tick.
    """
    shell_name = os.path.basename(shell_path)
    base = _runtime_base() / f"bruno-{os.getpid()}"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    fifo = base / "events"
    try:
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo, 0o600)
    except OSError:
        try:
            base.rmdir()
        except OSError:
            pass
        return None

    env: dict[str, str] = {"BRUNO_FIFO": str(fifo)}
    zdotdir: Path | None = None

    if shell_name in ("bash", "sh"):
        rc = _rc_path("bruno.bash")
        if rc is None:
            _cleanup_fifo(fifo, base)
            return None
        env["BASH_ENV"] = str(rc)
        # bash only sources BASH_ENV in non-interactive shells, so also
        # arrange a per-session interactive load: write a tiny user rc
        # shim that sources existing ~/.bashrc then our hook.
        try:
            shim = base / "bashrc"
            existing = Path.home() / ".bashrc"
            existing_line = (
                f". {shlex.quote(str(existing))}\n" if existing.exists() else ""
            )
            shim.write_text(
                existing_line + f". {shlex.quote(str(rc))}\n",
                encoding="utf-8",
            )
            env["BRUNO_BASH_RC"] = str(shim)
        except OSError:
            pass
    elif shell_name == "zsh":
        rc = _rc_path("bruno.zsh")
        if rc is None:
            _cleanup_fifo(fifo, base)
            return None
        # Build a temp ZDOTDIR that sources the user's real config files
        # then our hook. Without preserving the real ZDOTDIR chain we'd
        # nuke the user's whole zsh setup.
        zdotdir = base / "zdotdir"
        try:
            zdotdir.mkdir(parents=True, exist_ok=True)
            real_zdotdir = Path(os.environ.get("ZDOTDIR", str(Path.home())))
            _write_zsh_shim(zdotdir / ".zshenv",   real_zdotdir / ".zshenv",   rc)
            _write_zsh_shim(zdotdir / ".zprofile", real_zdotdir / ".zprofile", rc)
            _write_zsh_shim(zdotdir / ".zshrc",    real_zdotdir / ".zshrc",    rc)
            _write_zsh_shim(zdotdir / ".zlogin",   real_zdotdir / ".zlogin",   rc)
        except OSError:
            _cleanup_fifo(fifo, base)
            return None
        env["ZDOTDIR"] = str(zdotdir)
    else:
        _cleanup_fifo(fifo, base)
        return None

    return HookInstall(
        fifo_path=fifo,
        work_dir=base,
        env_updates=env,
        zdotdir_tempdir=zdotdir,
    )


def _write_zsh_shim(dest: Path, real_file: Path, hook_rc: Path) -> None:
    lines = []
    if real_file.exists():
        # `emulate -L zsh` first so options/aliases set in the real rc
        # behave the same as if zsh had loaded it directly.
        lines.append(f"[[ -r {_zsh_quote(real_file)} ]] && "
                     f"source {_zsh_quote(real_file)}\n")
    lines.append(f"source {_zsh_quote(hook_rc)}\n")
    dest.write_text("".join(lines), encoding="utf-8")


def _zsh_quote(p: Path) -> str:
    s = str(p).replace("'", "'\\''")
    return f"'{s}'"


def _cleanup_fifo(fifo: Path, base: Path) -> None:
    try:
        fifo.unlink()
    except OSError:
        pass
    try:
        base.rmdir()
    except OSError:
        pass


def open_reader(fifo_path: Path) -> int | None:
    """Open the FIFO read-end non-blocking. Returns fd or None."""
    try:
        fd = os.open(str(fifo_path), os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    return fd


def drain_events(fd: int, buffer: bytearray) -> list[tuple[str, ...]]:
    """Read available bytes, return parsed events. Mutates `buffer`."""
    events: list[tuple[str, ...]] = []
    while True:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            break
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            return events
        if not chunk:
            break
        buffer.extend(chunk)
    while b"\n" in buffer:
        line, _, rest = buffer.partition(b"\n")
        del buffer[:len(line) + 1]
        text = line.decode("utf-8", "replace").rstrip("\r")
        if not text:
            continue
        parts = text.split("\t")
        if not parts:
            continue
        events.append(tuple(parts))
    return events


# ---- event interpretation ----

_GIT_COMMIT_RE = re.compile(r"^\s*git\s+commit\b")
_GIT_PUSH_RE = re.compile(r"^\s*git\s+push\b")
_GIT_NEW_BRANCH_RE = re.compile(r"^\s*git\s+(checkout|switch)\s+-[bcC]\s+(\S+)")
_EDITOR_RE = re.compile(r"^\s*(?:[A-Z_]+=\S+\s+)*"
                        r"(?:vim|nvim|nano|emacs|hx|helix|kakoune|kate|kak|micro|ed|"
                        r"code|cursor|subl|gvim)\s+([^|;&]+)")
_LONG_THRESHOLD_MS = 30_000


def interpret(event: tuple[str, ...]):
    """Map a hook event tuple to a callable on Bruno. Returns None to skip.

    Returns a tuple `(method_name, args)` where method_name resolves on Bruno.
    """
    if event and event[0] == "verb" and len(event) >= 2:
        verb = event[1]
        if verb == "stats":
            return ("react_stats", ())
        if verb == "hide":
            return ("react_hide", ())
        if verb == "show":
            return ("react_show", ())
        if verb == "feed":
            return ("feed", ())
        return None
    if not event or event[0] != "cmd" or len(event) < 4:
        return None
    try:
        exit_code = int(event[1])
        duration_ms = int(event[2])
    except ValueError:
        return None
    cmd = "\t".join(event[3:]).strip()
    if not cmd:
        return None

    # Editor → react to first .ext we recognize.
    m = _EDITOR_RE.match(cmd)
    if m:
        for token in m.group(1).split():
            ext = _file_ext(token)
            if ext:
                return ("react_filetype", (ext,))

    if exit_code == 0 and _GIT_COMMIT_RE.match(cmd):
        return ("react_commit", ())
    if exit_code == 0 and _GIT_PUSH_RE.match(cmd):
        return ("react_push", ())
    nb = _GIT_NEW_BRANCH_RE.match(cmd)
    if exit_code == 0 and nb:
        return ("react_branch", (nb.group(2),))

    if exit_code != 0:
        # Suppress for trivial typos like `c` not found if duration was < 50ms?
        # Keep it broad — user wants concern feedback.
        return ("react_fail", (exit_code,))

    if duration_ms >= _LONG_THRESHOLD_MS:
        return ("react_long_done", (duration_ms / 1000.0,))

    return None


def _file_ext(token: str) -> str | None:
    token = token.strip().strip("'\"")
    if "/" in token:
        token = token.rsplit("/", 1)[-1]
    if "." not in token:
        return None
    ext = token.rsplit(".", 1)[-1]
    if not ext or len(ext) > 8:
        return None
    if not re.fullmatch(r"[A-Za-z0-9]+", ext):
        return None
    return ext
