"""Create CSV exports from Phase 4 release tables."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def export_release_tables(release_dir: str | Path, output_dir: str | Path | None = None) -> dict[str, str]:
    """Write one UTF-8 CSV beside each Parquet release and return output paths."""
    release_dir = Path(release_dir)
    output_dir = Path(output_dir) if output_dir else release_dir / "web_exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}
    for parquet_path in sorted(release_dir.glob("*.parquet")):
        frame = pd.read_parquet(parquet_path)
        csv_path = output_dir / f"{parquet_path.stem}.csv"
        frame.to_csv(csv_path, index=False, encoding="utf-8")
        exports[parquet_path.stem] = str(csv_path)
    return exports
