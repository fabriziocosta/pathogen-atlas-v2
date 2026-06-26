"""Shared input/output helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import yaml


def project_root() -> Path:
    """Return the repository root for the V2 package."""
    return Path(__file__).resolve().parents[2]


def resolve_path(path: str | Path, base_dir: str | Path | None = None) -> Path:
    """Resolve a path relative to ``base_dir`` or the repository root."""
    path = Path(path)
    if path.is_absolute():
        return path
    base = Path(base_dir) if base_dir is not None else project_root()
    return (base / path).resolve()


def ensure_parent(path: str | Path) -> Path:
    """Create the parent directory for a path and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file into a dictionary."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data or {}


def write_json(path: str | Path, data: Any) -> None:
    """Write JSON with stable formatting."""
    path = ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSON Lines file."""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    """Write records as JSON Lines."""
    path = ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")
