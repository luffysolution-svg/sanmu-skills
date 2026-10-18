"""Durable per-provider request budget for acquisition lanes.

The ledger is deliberately small: UTC-day counters, a sliding request window,
the last request time, and a cooldown deadline. It stores no DOIs, URLs, keys,
cookies, or response bodies. A lock file plus atomic replace keeps concurrent
acquisition processes from spending the same budget twice.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import tempfile
import time

SCHEMA = "sanmu-rate-ledger/v1"
DEFAULT_WINDOW = 600.0
DEFAULT_DAILY_LIMIT = 200
DEFAULT_MIN_INTERVAL = 2.0
MAX_COOLDOWN = 7 * 24 * 3600.0

# Conservative local defaults. They are safety budgets, not claims about each
# publisher's contractual limit. Wiley's 60 requests/10 minutes is official.
DEFAULT_POLICIES = {
    "elsevier": {"min_interval": 2.0, "window_seconds": 600.0,
                  "window_limit": 0, "daily_limit": 200},
    "wiley": {"min_interval": 10.0, "window_seconds": 600.0,
              "window_limit": 60, "daily_limit": 200},
}
DEFAULT_BROWSER_POLICY = {"min_interval": 15.0, "window_seconds": 3600.0,
                          "window_limit": 20, "daily_limit": 60}


def default_state_path():
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if not root:
        try:
            root = str(Path.home() / ".cache")
        except (RuntimeError, OSError):
            root = str(Path(tempfile.gettempdir()))
    return Path(root) / "sanmu-paper-search" / "acquisition-state.json"


def parse_retry_after(value, now=None):
    """Return seconds from numeric or HTTP-date Retry-After, else None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return min(MAX_COOLDOWN, max(0.0, float(text)))
    except ValueError:
        pass
    try:
        deadline = parsedate_to_datetime(text)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        current = datetime.fromtimestamp(now if now is not None else time.time(),
                                          timezone.utc)
        return min(MAX_COOLDOWN, max(0.0, (deadline - current).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _today(now):
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


class RateLedger:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock = clock
        self._memory = {"schema": SCHEMA, "scopes": {}}

    def _empty(self):
        return {"schema": SCHEMA, "scopes": {}}

    def _load(self):
        if self.path is None:
            return self._memory
        if not self.path.is_file():
            return self._empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        if value.get("schema") != SCHEMA or not isinstance(value.get("scopes"), dict):
            return self._empty()
        return value

    def _save(self, data):
        if self.path is None:
            self._memory = data
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".ledger-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(name, 0o600)
            except OSError:
                pass
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    @contextmanager
    def _lock(self):
        if self.path is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        deadline = time.monotonic() + 5
        handle = None
        while handle is None:
            try:
                handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(handle, str(os.getpid()).encode("ascii", "ignore"))
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 60:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Acquisition ledger is locked by another process")
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                os.close(handle)
            finally:
                lock.unlink(missing_ok=True)

    @staticmethod
    def _scope(data, name):
        scope = data["scopes"].setdefault(name, {})
        scope.setdefault("day", "")
        scope.setdefault("day_count", 0)
        scope.setdefault("requests", [])
        scope.setdefault("last_request", 0.0)
        scope.setdefault("cooldown_until", 0.0)
        scope.setdefault("cooldown_reason", None)
        scope.setdefault("last_status", None)
        return scope

    @staticmethod
    def _prune(scope, window, now):
        scope["requests"] = [
            value for value in scope.get("requests", [])
            if isinstance(value, (int, float)) and value > now - window
        ]
        day = _today(now)
        if scope.get("day") != day:
            scope["day"] = day
            scope["day_count"] = 0

    def _check_scope(self, scope, policy, now):
        window = float(policy.get("window_seconds", DEFAULT_WINDOW))
        self._prune(scope, window, now)
        cooldown = float(scope.get("cooldown_until") or 0.0)
        if cooldown > now:
            return {"allowed": False, "reason": "cooldown",
                    "wait_seconds": cooldown - now, "cooldown_until": cooldown,
                    "cooldown_reason": scope.get("cooldown_reason")}
        daily = int(policy.get("daily_limit", DEFAULT_DAILY_LIMIT))
        if daily > 0 and int(scope.get("day_count") or 0) >= daily:
            return {"allowed": False, "reason": "daily_limit", "wait_seconds": 0.0,
                    "day_count": scope["day_count"], "daily_limit": daily}
        limit = int(policy.get("window_limit", 0) or 0)
        if limit > 0 and len(scope["requests"]) >= limit:
            wait = float(scope["requests"][0]) + window - now
            return {"allowed": False, "reason": "window_limit",
                    "wait_seconds": max(0.0, wait),
                    "window_limit": limit, "window_seconds": window}
        interval = float(policy.get("min_interval", DEFAULT_MIN_INTERVAL))
        last = float(scope.get("last_request") or 0.0)
        if interval > 0 and last and now - last < interval:
            return {"allowed": False, "reason": "min_interval",
                    "wait_seconds": interval - (now - last)}
        return {"allowed": True, "reason": "ok", "wait_seconds": 0.0}

    def check(self, name, policy=None):
        now = self.clock()
        data = self._load()
        scope = self._scope(data, name)
        return self._check_scope(scope, policy or {}, now)

    def snapshot(self, names, policies=None):
        policies = policies or {}
        now = self.clock()
        data = self._load()
        result = {}
        for name in names:
            scope = self._scope(data, name)
            policy = policies.get(name, {})
            check = self._check_scope(scope, policy, now)
            result[name] = {
                "allowed": check["allowed"],
                "reason": check["reason"],
                "wait_seconds": round(float(check.get("wait_seconds") or 0.0), 1),
                "day": scope.get("day"),
                "day_count": scope.get("day_count", 0),
                "window_count": len(scope.get("requests", [])),
                "cooldown_until": scope.get("cooldown_until") or 0.0,
                "cooldown_reason": scope.get("cooldown_reason"),
                "last_status": scope.get("last_status"),
            }
        return result

    def reserve(self, name, policy=None):
        """Count one request now if the policy allows it."""
        with self._lock():
            now = self.clock()
            data = self._load()
            scope = self._scope(data, name)
            check = self._check_scope(scope, policy or {}, now)
            if not check["allowed"]:
                return check
            scope["requests"].append(now)
            scope["last_request"] = now
            scope["day_count"] = int(scope.get("day_count") or 0) + 1
            self._save(data)
            return {**check, "day_count": scope["day_count"],
                    "window_count": len(scope["requests"])}

    def note(self, name, status=None, retry_after=None, cooldown_seconds=None,
             default_cooldown=3600.0):
        """Record an outcome and optionally start a cooldown."""
        with self._lock():
            now = self.clock()
            data = self._load()
            scope = self._scope(data, name)
            scope["last_status"] = None if status is None else str(status)
            seconds = cooldown_seconds
            if seconds is None and status in ("rate_limited", "429", 429):
                seconds = parse_retry_after(retry_after, now)
                if seconds is None:
                    seconds = default_cooldown
            if seconds is not None:
                seconds = min(MAX_COOLDOWN, max(0.0, float(seconds)))
                deadline = now + seconds
                if deadline >= float(scope.get("cooldown_until") or 0.0):
                    scope["cooldown_until"] = deadline
                    scope["cooldown_reason"] = (
                        str(status) if status is not None else "manual"
                    )
            self._save(data)

    def clear(self, name):
        with self._lock():
            data = self._load()
            scope = self._scope(data, name)
            scope["cooldown_until"] = 0.0
            scope["cooldown_reason"] = None
            self._save(data)

    def clear_challenge(self, name):
        """Clear a CAPTCHA/login cooldown without forgiving a 429 cooldown."""
        with self._lock():
            data = self._load()
            scope = self._scope(data, name)
            if scope.get("cooldown_reason") in ("publisher_challenge",
                                                "institutional_login_required"):
                scope["cooldown_until"] = 0.0
                scope["cooldown_reason"] = None
                self._save(data)
