# Phase 1: Species

Species source files, normalised taxonomies, and synonym mappings.

This phase rebuilds species synonym mappings from LPSN.

- `raw/`: cached raw LPSN JSONL records.
- `interim/`: normalised LPSN records before release projection.
- `release/`: canonical species, synonym mappings, taxonomy, and metadata.

Downstream phases should consume only files in `release/`.
