# Phase 2: Abstracts

Raw, filtered, and release abstract corpora.

This phase generates PubMed queries from Phase 1 species mappings, writes an
inspectable query plan under `query_plan/`, acquires PubMed records, and exports
release parquet tables. Each PubMed query is built at canonical-species level:
all known synonyms for the canonical species are OR-ed together in one exact-name
expression per configured PubMed field. For each canonical species, the pipeline
keeps a configured number of oldest PMIDs first, then uniformly samples the
remaining PMIDs without duplication until the per-species limit is reached.
PMID acquisition appends completed query results as it runs, and abstract
acquisition appends fetched MEDLINE records by batch, so both steps can be
interrupted and resumed without duplicating completed work.

- `query_plan/`: generated YAML query plan for inspection before acquisition.
- `raw/`: raw PubMed search results and article records.
- `filtered/`: canonical species PMID lists, sampled PMID lists, species/query
  evidence rows, and filter-term matches.
- `release/`: deduplicated abstract corpus and release evidence tables.
