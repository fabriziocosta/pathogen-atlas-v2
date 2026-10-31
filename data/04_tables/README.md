# Phase 4: Tables

`release/` contains stable Parquet tables built from Phase 1 taxonomy, Phase 2 PubMed search and abstract outputs, and Phase 3 Jev decisions. Rebuilding Phase 4 performs no PubMed or model requests.

The release includes:

- `evidence_records.parquet`: accepted species–PMID evidence with article metadata and gate probability.
- `species_summary.parquet`: full PubMed article counts, accepted evidence counts, first evidence year, and taxonomy fields. Full counts come from the unsampled Phase 2 PMID index, while evidence counts are bounded by the abstracts actually fetched and scored.
- `pathogenic_index.parquet`: the V1 sampling-adjusted log index, computed as `log(n_causation / n_sampled * n_articles)` and normalized by its maximum. Species without accepted evidence receive zero. The static explorer uses it to size points when its pathogenic-index toggle is on.
- `domain_expert_answers.parquet`: current gate-passing species–abstract pairs with question probabilities and threshold-derived yes/no answers.
- `species_feature_summary.parquet`: per-species, per-question yes counts and prevalence from the V1 73-question set.
- `species_feature_matrix.parquet`: wide species-by-question prevalence matrix for plotting and further analysis.
- `abstract_counts.parquet`: yearly unique PMID counts per species for accepted evidence.
- `web_exports/*.csv`: selected, smaller CSV summaries for downloads and external consumers. The export allowlist omits `domain_expert_answers.parquet`, the detailed PMID-by-question ledger; keep that full table in Parquet and build purpose-specific web payloads from it.
- `emergence.parquet`: V1-style first-evidence year, year of reaching the publication target, time to that target, and evidence count in the initial year window. Defaults match the V1 selection logic (more than 10 evidence abstracts and fewer than 30,000 PubMed articles).
- `table_creation_metadata.json`: input paths, thresholds, run IDs, and row counts.
