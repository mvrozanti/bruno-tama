"""Phase 6: LLM-infused pane awareness.

Periodically samples sibling tmux pane text and asks a small LLM to
emit a one-line pet reaction. Every LLM invocation goes through
`gpu-lock run …` per the mandragora non-negotiable, and runs on a
background thread so bruno's main loop never stalls.

Backends:
  - "qwen"   → local ollama (qwen2.5:7b), via `gpu-lock run ollama run …`
  - "gemini" → google CLI, via `gpu-lock run gemini -p …`
  - "none"   → disabled

The first time a user runs bruno without a persisted choice, we probe
available backends and let the next `--llm` invocation explicitly pick
one; we don't pop UI here, the caller (overlay) handles that.
"""
from __future__ import annotations

import hashlib
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from . import tmux

GPULOCK = "gpu-lock"
OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_HOST = "http://127.0.0.1:11434"

LLM_TIMEOUT_S = 8.0
MIN_NEW_TEXT = 80          # skip call if pane changed less than this many chars
SAMPLE_LINES = 20          # how many lines of pane text to feed
MAX_REACTION_LEN = 40

_PROMPT_TEMPLATE = (
    "You watch a developer's terminal. Reply with ONE short reaction "
    "(max 6 words, no quotes, no emoji) a tiny pet would say about what's "
    "happening. Be playful, not formal.\n"
    "Terminal:\n"
    "{snippet}\n"
    "Reaction:"
)

# Reject obvious LLM-refusal boilerplate.
_REFUSAL_PATTERNS = re.compile(
    r"(?i)\b(as an ai|i (cannot|can't|won't|will not)|sorry,|i'm sorry|"
    r"i don't (have|know)|unable to)\b"
)


@dataclass
class BackendProbe:
    qwen_available: bool
    gemini_available: bool

    def options(self) -> list[str]:
        out: list[str] = []
        if self.qwen_available:
            out.append("qwen")
        if self.gemini_available:
            out.append("gemini")
        return out


def probe_backends() -> BackendProbe:
    qwen = _ollama_reachable()
    gemini = shutil.which("gemini") is not None
    return BackendProbe(qwen_available=qwen, gemini_available=gemini)


def _ollama_reachable() -> bool:
    if shutil.which("ollama") is None:
        return False
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except (urllib.error.URLError, socket.timeout, OSError):
        return False


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def sample_tmux_text(max_panes: int = 4, lines: int = SAMPLE_LINES) -> str:
    """Concatenate sibling-pane snapshots, stripping ANSI."""
    panes = tmux.sibling_panes()
    if not panes:
        return ""
    chunks: list[str] = []
    for pid in panes[:max_panes]:
        snap = tmux.capture_pane(pid, lines=lines)
        if snap:
            chunks.append(_strip_ansi(snap))
    return "\n".join(chunks).strip()


def sanitize(raw: str) -> Optional[str]:
    """Clamp + reject LLM output that looks like noise or refusal."""
    if not raw:
        return None
    text = _strip_ansi(raw).strip()
    if not text:
        return None
    # Take the first non-empty line — many models echo "Reaction:" or
    # extra reasoning before the actual response.
    for line in text.splitlines():
        line = line.strip().strip("\"'`")
        if line.lower().startswith(("reaction:", "answer:")):
            line = line.split(":", 1)[1].strip().strip("\"'`")
        if line:
            text = line
            break
    if _REFUSAL_PATTERNS.search(text):
        return None
    if len(text) > MAX_REACTION_LEN:
        text = text[: MAX_REACTION_LEN - 1].rstrip() + "…"
    return text or None


def _build_prompt(snippet: str) -> str:
    # Trim to last SAMPLE_LINES lines to keep prompt cost bounded.
    lines = [ln for ln in snippet.splitlines() if ln.strip()]
    snippet = "\n".join(lines[-SAMPLE_LINES:])
    return _PROMPT_TEMPLATE.format(snippet=snippet)


def _run_qwen(prompt: str) -> Optional[str]:
    if shutil.which(GPULOCK) is None:
        # gpu-lock missing — fail closed rather than hammer GPU directly.
        return None
    try:
        result = subprocess.run(
            [GPULOCK, "run", "ollama", "run", OLLAMA_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _run_gemini(prompt: str) -> Optional[str]:
    gemini_path = shutil.which("gemini")
    if gemini_path is None:
        return None
    cmd = [gemini_path, "-p", prompt]
    if shutil.which(GPULOCK):
        cmd = [GPULOCK, "run", *cmd]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def react(backend: str, snippet: str) -> Optional[str]:
    if not snippet or backend == "none":
        return None
    prompt = _build_prompt(snippet)
    if backend == "qwen":
        raw = _run_qwen(prompt)
    elif backend == "gemini":
        raw = _run_gemini(prompt)
    else:
        return None
    if raw is None:
        return None
    return sanitize(raw)


class AsyncReactor:
    """Background-threaded LLM caller.

    Owns its own worker thread. `request(snippet)` returns immediately;
    the result drops into `poll()` when it's ready. At most one in-flight
    request at a time — `request` is a no-op while a call is pending.
    """

    def __init__(self, backend: str):
        self.backend = backend
        self._results: queue.Queue[str] = queue.Queue()
        self._pending = False
        self._lock = threading.Lock()

    def is_pending(self) -> bool:
        with self._lock:
            return self._pending

    def request(self, snippet: str) -> bool:
        with self._lock:
            if self._pending:
                return False
            self._pending = True
        t = threading.Thread(
            target=self._run, args=(snippet,), daemon=True,
            name="bruno-llm",
        )
        t.start()
        return True

    def _run(self, snippet: str) -> None:
        try:
            text = react(self.backend, snippet)
        except Exception:
            text = None
        if text:
            self._results.put(text)
        with self._lock:
            self._pending = False

    def poll(self) -> Optional[str]:
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
