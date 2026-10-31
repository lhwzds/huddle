"""Protection for a public instance: access code, rate limits, caps and budget.

Every feature is off unless its environment variable is set, so a local
development setup and the test suite keep working unchanged.
"""
from __future__ import annotations

import hmac
import math
import os
import time
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse

ACCESS_COOKIE = "huddle_access"
ACCESS_MAX_AGE = 30 * 24 * 60 * 60
LOCKOUT_LIMIT = 10
LOCKOUT_WINDOW = 3600.0
MINUTE = 60.0
DAY = 86400.0


class LimitError(Exception):
    """A request that a limit refused; the detail is short and stable."""

    def __init__(self, detail, *, retry_after=None, status_code=429):
        super().__init__(detail)
        self.detail = detail
        self.retry_after = retry_after
        self.status_code = status_code


def _int_env(name: str):
    """A positive integer setting, or None when it is not set."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def access_code():
    """The invite code of this instance, or None when it is open."""
    code = os.environ.get("HUDDLE_ACCESS_CODE")
    if code is None:
        return None
    code = code.strip()
    return code or None


def proxy_hops() -> int:
    """How many reverse proxies stand in front of the app (in front of the frontend container).

    Each of them appends the address it saw to X-Forwarded-For; 0 means clients connect directly.
    The older on/off form ("true", "yes", "on") means one proxy.
    """
    raw = os.environ.get("HUDDLE_TRUST_PROXY", "").strip().lower()
    if raw in ("true", "yes", "on"):
        return 1
    return _int_env("HUDDLE_TRUST_PROXY") or 0


def rate_messages_per_min():
    return _int_env("HUDDLE_RATE_MESSAGES_PER_MIN")


def rate_messages_per_day():
    return _int_env("HUDDLE_RATE_MESSAGES_PER_DAY")


def rate_conversations_per_day():
    return _int_env("HUDDLE_RATE_CONVERSATIONS_PER_DAY")


def rate_agents_per_day():
    return _int_env("HUDDLE_RATE_AGENTS_PER_DAY")


def max_messages_per_conversation():
    return _int_env("HUDDLE_MAX_MESSAGES_PER_CONVERSATION")


def max_active_conversations():
    return _int_env("HUDDLE_MAX_ACTIVE_CONVERSATIONS")


def daily_token_budget():
    return _int_env("HUDDLE_DAILY_TOKEN_BUDGET")


def utc_day() -> str:
    """The current UTC day, the key of the usage counter."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def client_ip(request) -> str:
    """The address of the client.

    The backend is only reachable through the frontend's nginx, which appends the address it was
    connected from to X-Forwarded-For. Every trusted proxy in front of it appended one more, so the
    client is the entry `proxy_hops()` places before the end. Entries further left were written by the
    client itself and are never believed.
    """
    entries = [e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()]
    if entries:
        return entries[max(0, len(entries) - 1 - proxy_hops())]
    client = request.client
    return client.host if client else "unknown"


def same_code(given, expected) -> bool:
    """Compare two codes without leaking their length through the time."""
    if not expected or given is None:
        return False
    return hmac.compare_digest(str(given).encode("utf-8"), str(expected).encode("utf-8"))


def is_https(request) -> bool:
    """True when the browser connected over HTTPS.

    Behind trusted proxies the outermost one says so in X-Forwarded-Proto (nginx passes it on);
    without them the header is the client's own and is ignored.
    """
    proto = request.headers.get("x-forwarded-proto", "") if proxy_hops() else ""
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


class SlidingWindow:
    """In-memory sliding window counters, one list per (bucket, client)."""

    def __init__(self):
        self._hits: dict[tuple[str, str], list[float]] = {}

    @staticmethod
    def _prune(hits, window, now):
        cutoff = now - window
        while hits and hits[0] <= cutoff:
            hits.pop(0)

    def count(self, bucket, ip, window) -> int:
        hits = self._hits.get((bucket, ip))
        if not hits:
            return 0
        now = time.monotonic()
        self._prune(hits, window, now)
        return len(hits)

    def retry_after(self, bucket, ip, window) -> int:
        """Seconds until the oldest hit of the bucket leaves the window."""
        hits = self._hits.get((bucket, ip))
        now = time.monotonic()
        if hits:
            self._prune(hits, window, now)
        if not hits:
            return 1
        return max(1, math.ceil(hits[0] + window - now))

    def record(self, bucket, ip):
        self._hits.setdefault((bucket, ip), []).append(time.monotonic())
        self._sweep()

    def forget(self, bucket, ip):
        """Drop the hits of a bucket, e.g. after a success."""
        self._hits.pop((bucket, ip), None)

    def hit(self, bucket, ip, limit, window):
        """Count one hit; return the wait time when the limit is reached."""
        if not limit or limit <= 0:
            return None
        now = time.monotonic()
        hits = self._hits.setdefault((bucket, ip), [])
        self._prune(hits, window, now)
        if len(hits) >= limit:
            return max(1, math.ceil(hits[0] + window - now))
        hits.append(now)
        self._sweep()
        return None

    def _sweep(self):
        """Forget the buckets that nobody touched for a day."""
        if len(self._hits) < 4096:
            return
        cutoff = time.monotonic() - DAY
        for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
            self._hits.pop(key, None)


class Guards:
    """The limits of this instance. Every one of them can be switched off."""

    def __init__(self, store=None):
        self.store = store
        self.limits = SlidingWindow()
        self.failures = SlidingWindow()

    # -- access -----------------------------------------------------------

    def has_access(self, request) -> bool:
        """True when the caller may use the API."""
        code = access_code()
        if code is None:
            return True
        return same_code(request.cookies.get(ACCESS_COOKIE), code)

    def check_code(self, request, code):
        """Try an invite code. Returns (accepted, retry_after).

        The right code always opens the app; only wrong guesses are counted,
        so a locked out client cannot keep a guest out.
        """
        expected = access_code()
        if expected is None:
            return True, None
        ip = client_ip(request)
        if same_code(code, expected):
            self.failures.forget("access", ip)
            return True, None
        if self.failures.count("access", ip, LOCKOUT_WINDOW) >= LOCKOUT_LIMIT:
            return False, self.failures.retry_after("access", ip, LOCKOUT_WINDOW)
        self.failures.record("access", ip)
        return False, None

    # -- limits ------------------------------------------------------------

    def rate_limit(self, bucket, request, limit, window):
        """Count one request; raise when the client is too fast."""
        wait = self.limits.hit(bucket, client_ip(request), limit, window)
        if wait is not None:
            raise LimitError("slow down", retry_after=wait)

    async def budget_left(self):
        """The tokens left for today, or None when the budget is off."""
        budget = daily_token_budget()
        if budget is None or self.store is None:
            return None
        used = await self.store.usage_tokens(utc_day())
        return max(0, budget - used)

    async def budget_spent(self) -> bool:
        """True when the tokens of today are used up."""
        budget = daily_token_budget()
        if budget is None or self.store is None:
            return False
        return await self.store.usage_tokens(utc_day()) >= budget


class AccessMiddleware:
    """Pure ASGI middleware: it answers 401 before a route runs."""

    def __init__(self, app, guards: Guards):
        self.app = app
        self.guards = guards

    @staticmethod
    def _exempt(method: str, path: str) -> bool:
        if method == "GET" and path == "/api/health":
            return True
        return method == "POST" and path == "/api/access"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith("/api/") or self._exempt(scope.get("method", "GET"), path):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if not self.guards.has_access(request):
            response = JSONResponse({"detail": "access required"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
