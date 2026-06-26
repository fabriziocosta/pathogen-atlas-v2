# Phase 1: Species

Species source files, normalised taxonomies, and synonym mappings.

This phase rebuilds species synonym mappings from LPSN.

- `raw/`: cached raw LPSN JSONL records.
- `interim/`: normalised LPSN records before release projection.
- `release/`: canonical species, synonym mappings, taxonomy, and metadata.

Live acquisition writes retrieved candidate records to
`raw/lpsn_species_candidates.jsonl`, enriched raw records to
`raw/lpsn_species_records.jsonl`, and progress to
`raw/lpsn_species_progress.json`. Resume uses the candidate file first, so a
run interrupted during retrieval does not start again from zero. The
`raw/lpsn_lookup_cache.jsonl` file avoids repeating accepted-name and
parent-record lookups across resumed runs. Parent taxonomy hierarchy traversal
is off by default for speed; set `enrich_taxonomy_hierarchy: true` in
`configs/species_sources.yaml` for the slower full taxonomy pass.

Downstream phases should consume only files in `release/`.
