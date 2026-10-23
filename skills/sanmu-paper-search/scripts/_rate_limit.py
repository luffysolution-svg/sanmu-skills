"""Small credential-hashed, process-shared start-time limiter (stdlib only)."""
from __future__ import annotations

from contextlib import contextmanager
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time


class RateLimitError(Exception):
    pass


class RateLimiter:
    def __init__(self, directory=None, *, shared=True, clock=None, sleep=None):
        user = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
        self.directory = Path(directory) if directory is not None else Path(tempfile.gettempdir()) / ("sanmu-paper-search-" + user)
        self.shared = shared
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.states = {}
        self.logical_now = 0.0
        self._private_dir = None

    def _lock_dir(self):
        """On POSIX the shared temp dir may be pre-created by another user: require a
        real directory we own, else fall back to a uid-suffixed dir, else a private one."""
        if not hasattr(os, "getuid"):
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            return self.directory
        fallback = Path(tempfile.gettempdir()) / f"sanmu-paper-search-uid{os.getuid()}"
        for candidate in (self.directory, fallback):
            try:
                try:
                    candidate.mkdir(mode=0o700, parents=True)
                    os.chmod(candidate, 0o700)
                except FileExistsError:
                    pass
                info = os.lstat(candidate)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
                return candidate
        # Last resort: still safe, only loses cross-process sharing.
        if self._private_dir is None:
            self._private_dir = Path(tempfile.mkdtemp(prefix="sanmu-paper-search-"))
        return self._private_dir

    def _now(self):
        return max(self.clock(), self.logical_now) if not self.shared else self.clock()

    @contextmanager
    def _state(self, source, key, max_wait=10.0):
        token = hashlib.sha256((source + "\0" + (key or "anonymous")).encode()).hexdigest()
        if not self.shared:
            yield self.states.setdefault(token, {})
            return
        descriptor = os.open(self._lock_dir() / (token + ".lock"),
                             os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b") as handle:
            # Both OS lock APIs permit locking byte zero before any state is written.
            deadline = time.monotonic() + max_wait
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    pass
                except OSError as exc:
                    if exc.errno not in (11, 13, 36):
                        raise
                if time.monotonic() >= deadline:
                    raise RateLimitError("Shared rate-limit lock exceeds retry wait budget")
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            try:
                handle.seek(0)
                raw = handle.read().strip()
                try:
                    state = json.loads(raw) if raw else {}
                    if not isinstance(state, dict) or any(
                            type(v) not in (int, float) or not math.isfinite(v) for v in state.values()):
                        raise ValueError("Invalid rate-limit state")
                except (ValueError, UnicodeError) as exc:
                    raise RateLimitError("Invalid shared rate-limit state") from exc
                yield state
                handle.seek(0)
                handle.write(json.dumps(state).encode())
                handle.truncate()
                handle.flush()
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def wait(self, source, key=None, interval=1.05, max_wait=10.0):
        started = time.monotonic()
        with self._state(source, key, max_wait) as state:
            lock_wait = time.monotonic() - started
            now = self._now()
            delay = max(0.0, state.get("next", 0) - now, state.get("cooldown", 0) - now)
            if delay > 0 and delay > max(0, max_wait - (time.monotonic() - started)):
                raise RateLimitError("Rate-limit cooldown exceeds bounded retry wait budget")
            if delay:
                self.sleep(delay)
            # A no-op test sleep still advances the logical reservation.
            current = max(self.clock(), now + delay)
            if not self.shared:
                self.logical_now = current
            state["next"] = current + interval
            return delay + lock_wait

    def cooldown(self, source, key, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Cooldown must be finite and nonnegative")
        with self._state(source, key) as state:
            state["cooldown"] = max(state.get("cooldown", 0), self._now() + seconds)
