"""
Resilient Network Layer & Mirror Failover.

Fiduciary network transport providing jittered exponential backoff retries,
HTTP 429 / 5xx handling, Windows TCP 10054 connection reset recovery, SSL error handling,
and dynamic mirror endpoint rotation across Binance gateways.
"""
from __future__ import annotations

import logging
import random
import ssl
import time
from collections import OrderedDict
from typing import Any, Callable, Sequence, TypeVar

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    HTTPError,
    SSLError as ReqSSLError,
    Timeout as ReqTimeout,
)

try:
    from binance.exceptions import BinanceAPIException, BinanceRequestException
except ImportError:
    BinanceAPIException = None
    BinanceRequestException = None

logger = logging.getLogger(__name__)

T = TypeVar("T")
KT = TypeVar("KT")
VT = TypeVar("VT")


class NetworkError(Exception):
    """Base exception for resilient network operations."""
    pass


class MaxRetriesExceededError(NetworkError):
    """Raised when maximum retry attempts are exhausted."""
    def __init__(self, message: str, response: Any = None, last_exception: Exception | None = None) -> None:
        super().__init__(message)
        self.response = response
        self.last_exception = last_exception


class AllMirrorsExhaustedError(NetworkError):
    """Raised when all available mirror endpoints have failed or are degraded."""
    pass


class RateLimitExceededError(NetworkError):
    """Raised when rate limits (HTTP 429) cannot be backed off safely."""
    pass


class LRUCache(OrderedDict[KT, VT]):
    """Bounded LRU Cache with maximum capacity."""
    def __init__(self, maxsize: int = 200, *args: Any, **kwargs: Any) -> None:
        self.maxsize = maxsize
        super().__init__(*args, **kwargs)

    def __getitem__(self, key: KT) -> VT:
        val = super().__getitem__(key)
        self.move_to_end(key)
        return val

    def get(self, key: KT, default: VT | None = None) -> VT | None:
        if key in self:
            self.move_to_end(key)
            return self[key]
        return default

    def __setitem__(self, key: KT, value: VT) -> None:
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)


class FiduciaryRetryPolicy:
    """
    Fiduciary Retry Policy with Jittered Exponential Backoff.

    Implements the mathematical formula:
        t = min(t_max, t_base * 2^attempt) * uniform(0.8, 1.2)

    Intercepts:
        - ConnectionResetError (WinError 10054)
        - requests.exceptions.Timeout / TimeoutError
        - requests.exceptions.SSLError / ssl.SSLError
        - HTTP statuses (429, 500, 502, 503, 504)
        - HTTP 429 'Retry-After' header enforcement
    """

    def __init__(
        self,
        max_retries: int = 4,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        jitter_range: tuple[float, float] = (0.8, 1.2),
        retry_statuses: Sequence[int] = (429, 500, 502, 503, 504),
        retry_exceptions: Sequence[type[Exception]] | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_range = jitter_range
        self.retry_statuses = tuple(retry_statuses)

        default_exceptions: list[type[Exception]] = [
            ReqConnectionError,
            ReqTimeout,
            ReqSSLError,
            ConnectionResetError,
            TimeoutError,
            ssl.SSLError,
        ]
        if BinanceAPIException is not None:
            default_exceptions.append(BinanceAPIException)
        if BinanceRequestException is not None:
            default_exceptions.append(BinanceRequestException)

        if retry_exceptions is not None:
            self.retry_exceptions = tuple(retry_exceptions)
        else:
            self.retry_exceptions = tuple(default_exceptions)

    def calculate_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """
        Calculate jittered exponential backoff delay.
        Formula: t = min(t_max, t_base * 2^attempt) * uniform(0.8, 1.2)
        If retry_after is provided, applies jitter to retry_after.
        """
        clamped_attempt = max(0, attempt)
        jitter = random.uniform(self.jitter_range[0], self.jitter_range[1])
        if retry_after is not None and retry_after > 0:
            return float(retry_after) + random.uniform(0.1, 1.5)
        raw_delay = min(self.max_delay, self.base_delay * (2 ** clamped_attempt))
        return raw_delay * jitter

    def is_retryable_exception(self, exc: Exception) -> bool:
        """Inspects exception and cause hierarchy to determine retry eligibility."""
        if exc is None:
            return False

        # If BinanceAPIException, check status_code or rate-limit code
        if BinanceAPIException is not None and isinstance(exc, BinanceAPIException):
            status_code = getattr(exc, "status_code", None)
            if status_code in self.retry_statuses:
                return True
            code = getattr(exc, "code", None)
            if code in (-1003, -1021):
                return True
            return False

        # If HTTPError, check response status code
        if isinstance(exc, HTTPError):
            resp = getattr(exc, "response", None)
            if resp is not None and getattr(resp, "status_code", None) in self.retry_statuses:
                return True

        # Check explicit retry exceptions
        if isinstance(exc, self.retry_exceptions):
            return True

        # Traverse cause and context chain for WinError 10054, socket resets, SSL errors
        curr: Exception | None = exc
        while curr is not None:
            if isinstance(curr, (ConnectionResetError, TimeoutError, ReqTimeout, ReqSSLError, ReqConnectionError, ssl.SSLError)):
                return True
            winerror = getattr(curr, "winerror", None)
            errno = getattr(curr, "errno", None)
            if winerror == 10054 or errno == 10054:
                return True
            err_str = str(curr).lower()
            if "10054" in err_str or "forcibly closed" in err_str or "connection reset" in err_str:
                return True
            if "ssl" in err_str and ("handshake" in err_str or "certificate" in err_str or "eof" in err_str):
                return True
            curr = curr.__cause__ if curr.__cause__ is not None else curr.__context__

        return False

    def is_transport_reset(self, exc: Exception) -> bool:
        """Determines if the exception indicates a dropped TCP socket or broken SSL state."""
        curr: Exception | None = exc
        while curr is not None:
            if isinstance(curr, (ConnectionResetError, ReqSSLError, ssl.SSLError)):
                return True
            winerror = getattr(curr, "winerror", None)
            errno = getattr(curr, "errno", None)
            if winerror == 10054 or errno == 10054:
                return True
            err_str = str(curr).lower()
            if "10054" in err_str or "forcibly closed" in err_str or "connection reset" in err_str:
                return True
            if "ssl" in err_str:
                return True
            curr = curr.__cause__ if curr.__cause__ is not None else curr.__context__
        return False

    @staticmethod
    def _extract_retry_after(response: Any) -> float | None:
        """Extracts Retry-After header from response object if present."""
        if response is None:
            return None
        headers = getattr(response, "headers", None)
        if headers and hasattr(headers, "get"):
            header_val = headers.get("Retry-After") or headers.get("retry-after")
            if header_val is not None:
                try:
                    return float(header_val)
                except (ValueError, TypeError):
                    pass
        return None

    def _extract_retry_after_from_exc(self, exc: Exception) -> float | None:
        """Extracts Retry-After from exception response if available."""
        resp = getattr(exc, "response", None)
        if resp is not None:
            return self._extract_retry_after(resp)
        return None

    def execute(self, callable_fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Executes a callable with jittered exponential backoff on transient errors.
        """
        attempt = 0
        while True:
            try:
                res = callable_fn(*args, **kwargs)
                if hasattr(res, "status_code") and res.status_code in self.retry_statuses:
                    if attempt >= self.max_retries:
                        return res
                    retry_after = self._extract_retry_after(res)
                    delay = self.calculate_delay(attempt, retry_after)
                    logger.warning(
                        "HTTP %s received (attempt %d/%d). Backing off for %.2fs.",
                        res.status_code,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                return res
            except Exception as exc:
                if not self.is_retryable_exception(exc):
                    raise
                if attempt >= self.max_retries:
                    raise MaxRetriesExceededError(
                        f"Max retries ({self.max_retries}) exceeded: {exc}",
                        last_exception=exc,
                    ) from exc

                retry_after = self._extract_retry_after_from_exc(exc)
                delay = self.calculate_delay(attempt, retry_after)
                logger.warning(
                    "Transient network error %s: %s (attempt %d/%d). Retrying in %.2fs...",
                    type(exc).__name__,
                    exc,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                attempt += 1


class MirrorManager:
    """
    Manages Binance API gateway mirrors, dynamic health scores, cooldown marking,
    and automatic failover.
    """

    DEFAULT_MIRRORS = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://data-api.binance.vision",
    ]
    EXECUTION_MIRRORS = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
    ]
    TESTNET_MIRRORS = [
        "https://testnet.binance.vision",
        "https://testnet1.binance.vision",
        "https://testnet2.binance.vision",
    ]

    def __init__(
        self,
        mirrors: Sequence[str] | None = None,
        testnet: bool = False,
        default_cooldown: float = 300.0,
    ) -> None:
        self.testnet = testnet
        self.default_cooldown = default_cooldown
        if mirrors is not None:
            self.mirrors: list[str] = [m.rstrip("/") for m in mirrors]
        elif testnet:
            self.mirrors = list(self.TESTNET_MIRRORS)
        else:
            self.mirrors = list(self.DEFAULT_MIRRORS)

        if not self.mirrors:
            self.mirrors = ["https://api.binance.com"]

        self._health_scores: dict[str, float] = {m: 100.0 for m in self.mirrors}
        self._cooldown_until: dict[str, float] = {m: 0.0 for m in self.mirrors}
        self._consecutive_failures: dict[str, int] = {m: 0 for m in self.mirrors}
        self._active_mirror: str = self.mirrors[0]

    def get_active_mirror(self, now: float | None = None) -> str:
        """Returns the currently active healthy mirror, rotating if degraded."""
        current_time = time.time() if now is None else float(now)
        candidates = [m for m in self.mirrors if self._cooldown_until.get(m, 0.0) <= current_time]
        if candidates:
            # Sort by mirror index preference so primary mirror is restored once cooldown expires
            candidates.sort(key=lambda m: self.mirrors.index(m))
            self._active_mirror = candidates[0]
            return self._active_mirror

        # If all mirrors are in cooldown, pick the one that expires earliest
        sorted_all = sorted(
            self.mirrors,
            key=lambda m: (self._cooldown_until.get(m, 0.0), self.mirrors.index(m)),
        )
        self._active_mirror = sorted_all[0]
        return self._active_mirror

    def mark_degraded(
        self,
        mirror: str | None = None,
        cooldown: float = 300.0,
        now: float | None = None,
    ) -> None:
        """Marks a mirror as degraded, sets its cooldown window, and shifts active mirror."""
        target = (mirror or self._active_mirror).rstrip("/")
        if target not in self.mirrors:
            self.mirrors.append(target)

        current_time = time.time() if now is None else float(now)
        self._cooldown_until[target] = current_time + cooldown
        self._consecutive_failures[target] = self._consecutive_failures.get(target, 0) + 1
        penalty = 20.0 * self._consecutive_failures[target]
        self._health_scores[target] = max(0.0, self._health_scores.get(target, 100.0) - penalty)
        logger.warning(
            "Mirror %s degraded (cooldown %.1fs, health %.1f, consecutive failures %d)",
            target,
            cooldown,
            self._health_scores[target],
            self._consecutive_failures[target],
        )

        # Re-evaluate active mirror
        self.get_active_mirror(now=current_time)

    def record_success(self, mirror: str | None = None, now: float | None = None) -> None:
        """Records a successful request, resetting failures and restoring health."""
        target = (mirror or self._active_mirror).rstrip("/")
        if target not in self.mirrors:
            self.mirrors.append(target)

        self._consecutive_failures[target] = 0
        self._cooldown_until[target] = 0.0
        self._health_scores[target] = min(100.0, self._health_scores.get(target, 100.0) + 5.0)

    def get_candidate_mirrors(self, now: float | None = None) -> list[str]:
        """Returns ordered list of mirrors: active/healthy first, then cooldown mirrors."""
        current_time = time.time() if now is None else float(now)
        healthy = [m for m in self.mirrors if self._cooldown_until.get(m, 0.0) <= current_time]
        healthy.sort(key=lambda m: (-self._health_scores.get(m, 100.0), self.mirrors.index(m)))

        cooldown = [m for m in self.mirrors if self._cooldown_until.get(m, 0.0) > current_time]
        cooldown.sort(key=lambda m: (self._cooldown_until.get(m, 0.0), self.mirrors.index(m)))

        return healthy + cooldown

    def get_request_url(self, path: str) -> str:
        """Constructs full URL using active mirror."""
        mirror = self.get_active_mirror()
        clean_path = path if path.startswith("/") else f"/{path}"
        return f"{mirror}{clean_path}"

    def reset(self) -> None:
        """Resets all mirrors to default state."""
        self._cooldown_until.clear()
        self._consecutive_failures.clear()
        self._health_scores = {m: 100.0 for m in self.mirrors}
        self._active_mirror = self.mirrors[0]
