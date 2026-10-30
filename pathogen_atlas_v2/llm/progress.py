"""Progress and ETA formatting for long-running decision requests."""

from __future__ import annotations

import time
from datetime import datetime, timedelta


def _format_duration(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_eta(started_at: float, completed: int, total: int) -> str:
    """Estimate remaining wall time from observed request throughput."""
    remaining = max(int(total) - int(completed), 0)
    if remaining == 0:
        return "ETA 0s (complete)."
    if completed <= 0:
        return "ETA calculating."
    elapsed = max(time.monotonic() - started_at, 0.001)
    seconds_per_request = elapsed / completed
    eta_seconds = seconds_per_request * remaining
    finish_at = datetime.now().astimezone() + timedelta(seconds=eta_seconds)
    return (
        f"ETA {_format_duration(eta_seconds)}; estimated finish "
        f"{finish_at.strftime('%H:%M:%S %Z')} local."
    )


__all__ = ["format_eta"]

class OpenRouterSpendTracker:
    """Track remaining OpenRouter balance and estimate the run's ending balance."""

    def __init__(self, client: object) -> None:
        self._lookup = getattr(client, "credit_status", None)
        self.status = "remaining credit unavailable (client has no balance lookup)"
        self.remaining: float | None = None
        self.starting_remaining: float | None = None
        self.last_remaining: float | None = None
        self.attempts = 0
        self.cost_per_attempt: float | None = None
        self.refresh()
        self.starting_remaining = self.remaining
        self.last_remaining = self.remaining

    def add_attempts(self, count: int) -> None:
        self.attempts += max(0, int(count))

    def refresh(self) -> None:
        if not callable(self._lookup):
            return
        try:
            status, amount = self._lookup()
            self.status = str(status)
            self.remaining = float(amount) if amount is not None else None
        except Exception as exc:
            self.status = f"remaining credit unavailable ({type(exc).__name__})"
            self.remaining = None
        if (
            self.remaining is not None
            and self.last_remaining is not None
            and self.starting_remaining is not None
            and self.remaining < self.last_remaining
            and self.attempts > 0
        ):
            observed_spend = max(0.0, self.starting_remaining - self.remaining)
            if observed_spend > 0:
                self.cost_per_attempt = observed_spend / self.attempts
        if self.remaining is not None:
            self.last_remaining = self.remaining

    def describe(self, remaining_requests: int) -> str:
        """Format the latest balance and optional estimated ending balance."""
        if self.remaining is None:
            return self.status
        if self.cost_per_attempt is None:
            estimate = "estimated ending balance waiting for OpenRouter to report spend"
        else:
            forecast = max(0.0, self.remaining - self.cost_per_attempt * max(0, int(remaining_requests)))
            estimate = f"estimated ending balance ${forecast:.4f}"
        return f"{self.status}; {estimate}"


__all__ = ["OpenRouterSpendTracker", "format_eta"]
