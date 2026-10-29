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
