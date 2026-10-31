"""Create CSV exports from Phase 4 release tables."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from pathogen_atlas_v2.common.io import project_root, resolve_path


# Web downloads are intentionally curated. Detailed PMID-by-question
# ledgers remain canonical Parquet tables and are not copied into CSV bundles.
WEB_EXPORT_TABLES = (
    "abstract_counts",
    "emergence",
    "pathogenic_index",
    "species_feature_matrix",
    "species_feature_summary",
    "species_summary",
)


def export_release_tables(release_dir: str | Path, output_dir: str | Path | None = None) -> dict[str, str]:
    """Write curated summary CSVs with paths rooted at the project directory."""
    root = project_root()
    release_dir = resolve_path(release_dir)
    output_dir = resolve_path(output_dir) if output_dir else release_dir / "web_exports"
    notebooks_dir = root / "notebooks"
    for label, path in (("release_dir", release_dir), ("web export directory", output_dir)):
        if path.is_relative_to(notebooks_dir):
            raise ValueError(
                f"{label} must not be inside notebooks/: {path}. "
                "Store generated data under the repository's data/ tree."
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}
    for table_name in WEB_EXPORT_TABLES:
        parquet_path = release_dir / f"{table_name}.parquet"
        if not parquet_path.exists():
            continue
        frame = pd.read_parquet(parquet_path)
        csv_path = output_dir / f"{parquet_path.stem}.csv"
        frame.to_csv(csv_path, index=False, encoding="utf-8")
        exports[parquet_path.stem] = str(csv_path)
    return exports
