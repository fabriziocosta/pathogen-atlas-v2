# Phase 2: Abstracts

Raw, filtered, and release abstract corpora.

This phase generates PubMed queries from Phase 1 species mappings, writes an
inspectable query plan under `query_plan/`, acquires PubMed records, and exports
release parquet tables.

- `query_plan/`: generated YAML query plan for inspection before acquisition.
- `raw/`: raw PubMed search results and article records.
- `filtered/`: species/query evidence rows and filter-term matches.
- `release/`: deduplicated abstract corpus and release evidence tables.
