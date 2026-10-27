"""OpenRouter Decisions API client for Jev System 1."""

from __future__ import annotations

import time
from threading import local
from typing import Any, Protocol

import requests


DEFAULT_JEV_MODEL = "typesafe/jev-1.14"
DEFAULT_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class JevRequestError(RuntimeError):
    """Raised when a Jev request fails after its configured retries."""

    def __init__(self, message: str, *, attempts: int):
        super().__init__(message)
        self.attempts = attempts


class DecisionClient(Protocol):
    """Provider boundary used by the decision runner."""

    model: str
    provider: str

    def decide(self, state: dict[str, Any], *, question_id: str, question: dict[str, Any]) -> dict[str, Any]: ...


class OpenRouterJevClient:
    """Small HTTP client for OpenRouter's typed Decisions API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_JEV_MODEL,
        endpoint: str = DEFAULT_DECISIONS_URL,
        timeout: float = 90.0,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or _read_api_key()
        if not self.api_key:
            raise ValueError("Set OPENROUTER_API_KEY before running Jev decisions.")
        self.model = str(model)
        self.provider = "openrouter_jev"
        self.endpoint = str(endpoint)
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.backoff_seconds = float(backoff_seconds)
        if self.retries < 0:
            raise ValueError("retries must be non-negative.")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive.")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative.")
        self._provided_session = session
        self._thread_state = local()

    @property
    def last_attempts(self) -> int:
        """Return attempts for the current worker thread's last decision."""
        return int(getattr(self._thread_state, "last_attempts", 1))

    def decide(
        self,
        state: dict[str, Any],
        *,
        question_id: str,
        question: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit one typed question and return the raw JSON response."""
        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                question_id: {
                    "type": question["type"],
                    "instructions": question["instructions"],
                    "criteria": question.get("criteria", {}),
                }
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        total_attempts = self.retries + 1
        last_error: Exception | None = None

        for attempt in range(total_attempts):
            session = self._provided_session or requests.Session()
            try:
                # A separate Session per request avoids sharing mutable session
                # state across the concurrent workers used by the runner.
                response = session.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Jev response must be a JSON object.")
                self._thread_state.last_attempts = attempt + 1
                return data
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= total_attempts or not _is_transient_error(exc):
                    break
                time.sleep(self.backoff_seconds * (2**attempt))
            finally:
                if self._provided_session is None:
                    session.close()

        raise JevRequestError(
            f"OpenRouter Jev request failed: {last_error}",
            attempts=min(total_attempts, attempt + 1),
        ) from last_error

def _read_api_key() -> str | None:
    """Read the OpenRouter credential without importing extra dependencies."""
    import os

    value = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return value or None


def _is_transient_error(error: Exception) -> bool:
    """Return whether an HTTP or transport error is safe to retry."""
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        return status == 429 or (status is not None and 500 <= status <= 599)
    return False


def extract_noul_probability(response: dict[str, Any], question_id: str) -> float:
    """Validate and return the Jev probability that a noul statement is true."""
    answers = response.get("answers")
    answer = answers.get(question_id) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        raise ValueError(f"Jev response is missing the {question_id!r} noul answer.")
    probability = answer.get("noul")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise ValueError("Jev returned a non-numeric noul probability.")
    probability = float(probability)
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"Jev probability must be between 0 and 1; got {probability}.")
    return probability


def build_pathogen_evidence_state(
    *,
    canonical_species: str,
    matched_names: list[str],
    title: str,
    abstract: str,
) -> dict[str, Any]:
    """Build the bounded state Jev uses for one species/abstract judgment."""
    return {
        "species": {
            "canonical_name": canonical_species,
            "names_in_query": matched_names,
        },
        "article": {"title": title, "abstract": abstract},
    }


__all__ = [
    "DEFAULT_DECISIONS_URL",
    "DEFAULT_JEV_MODEL",
    "DecisionClient",
    "JevRequestError",
    "OpenRouterJevClient",
    "build_pathogen_evidence_state",
    "extract_noul_probability",
]
