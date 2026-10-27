# Plan: Apply AMR Workflow Lessons to Pathogen Atlas V2

**Status:** proposed implementation plan
**Prepared:** 2026-10-27
**Scope:** `Pathogen Atlas Data Acquisition/version2`

## Goal

Make V2 a reproducible, resumable, auditable pipeline from species inputs through PubMed acquisition, abstract decisions, release tables, and plots. Use the Antimicrobial-Resistance (AMR) repository as a working reference for pipeline operation and its Jev System 1 decisions path, while retaining V2's species-first data model and query-plan workflow.

This is a plan, not a change to the scientific definition of “pathogen” or to the PubMed search strategy. Those definitions need to be decided and evaluated against labelled examples before they become production defaults.

## What the comparison shows

### V2 foundations to keep

- Reusable package code is separated from thin notebook and CLI entry points.
- The pipeline has explicit phases and `raw -> interim/filtered -> release` data boundaries.
- PubMed query templates and acquisition settings are tracked in YAML.
- The query-plan step renders inspectable queries, groups synonyms by canonical species, and assigns stable query hashes/IDs.
- PMID search and abstract fetching append results and resume completed work. The Phase 2 tests cover query rendering, plan creation, resume preflight, client setup, and acquisition helpers.
- V2's per-species acquisition and configured PMID sampling are specific to its corpus design and should remain unless an evaluation shows they need to change.

### Version 1 continuity

- V1's README describes it as a reference archive: source/configuration and selected release/reference tables stay in Git, while large PubMed caches, per-species data, exploratory notebooks, figures, and other generated artifacts remain local/ignored.
- V2 says Phase 2 is generated from V2 Phase 1 and does not import V1 data. Keep that boundary explicit. During migration, inventory V1 scientific definitions and selected reference artifacts that still inform V2, record their provenance, and promote only reviewed inputs into V2's tracked configuration or data contracts. Do not assume an ignored V1 cache is reproducible input or silently treat it as V2 release data.
- The V1 README is an archival policy, not an operational workflow guide. V2's runbook should stand on its own and explain how to build its releases without relying on undocumented local V1 state.

### Useful AMR patterns to adopt

- A single shared run configuration supplies settings to each stage, instead of duplicating operational choices across notebooks.
- Long-running work has explicit resume keys, checkpoints, progress, error status, and retry behaviour. Expensive model calls are resumable independently of rebuilding derived datasets.
- Prediction outputs preserve model, run ID, question version, probability, status, and error metadata. A threshold can be changed and the derived dataset rebuilt offline.
- A cost/preflight step runs before large model jobs. Model/provider choice is explicit and the run can be bounded or throttled.
- Reusable transformations have focused tests and documented input/output contracts; notebooks orchestrate and inspect rather than own the implementation.

### V2 gaps at the comparison baseline

- At the comparison baseline, the Phase 3 pipeline, pathogen classifier, microbiology extraction, and vectorisation modules were placeholders. The pathogen-evidence slice is now implemented; other Phase 3 capabilities and shared schemas remain incomplete.
- V2 has no System 1/Jev client, decision-run configuration, model-call cost preflight, or common prediction ledger.
- Phase 2 is more developed than later phases. In particular, its plan approval is explicitly soft, so inspection does not currently establish a recorded, reproducible “approved query set” checkpoint.
- The query plan's hash identifies the rendered search string. A separate run/config fingerprint and acquisition metadata should identify the full inputs and operational settings used to make release outputs.
- The AMR System 1 question asks whether a paper supports a *specific pathogen–antibiotic pair*. That wording and its labels do not transfer to V2. Jev provides probabilistic answers to typed questions; V2 must define and validate its own question(s).

**Progress update (2026-10-27):** V2 now has an initial Jev source for abstract-level human pathogen evidence, a versioned disease-attribution question, a resumable decision ledger, and a provider-neutral Phase 3 notebook. Probability evaluation and threshold selection remain future work.

## Proposed target design

Keep the existing phase structure. Add a decision-provider boundary under `pathogen_atlas_v2/llm/` (or a clearly named `pathogen_atlas_v2/decisions/` package) so the same pipeline can run a configured conventional LLM backend or Jev System 1 against the same input rows. Keep API clients, prompt/question definitions, response parsing, checkpoint storage, and downstream table construction as distinct responsibilities.

For each model decision, retain enough information to reproduce or audit it: stable input ID (PMID and canonical species, or species ID for species-level classification), abstract/input hash, provider, model, run ID, question ID and version, raw response reference, normalized answer/probability, threshold used for derived inclusion, status, attempt count, and error details. Keep raw responses separate from parsed decisions so parser changes do not require repeating API calls. Keep raw provider responses access-controlled if they contain sensitive credentials or data; never write credentials to artifacts.

Jev's AMR implementation uses OpenRouter's typed Decisions API (`noul`, a yes/no/uncertain probability), stores `p(yes)`, and separates the probability ledger from the threshold-based dataset. V2 should carry over that pattern, not copy the AMR-specific question. A reasonable first pilot is a clearly scoped question about whether the title/abstract supports classifying the named canonical species as a pathogen in the described context. The exact wording, treatment of colonisation/contamination/commensal findings, answer interpretation, and threshold must be drafted and checked with domain labels before the pilot is treated as a release classifier. Only use `noul` for genuinely binary propositions; use a structured multi-label schema for microbiology extraction questions.

## Implementation sequence

### 0. Baseline inventory and data contracts

1. Mark each phase/module as implemented, partial, or placeholder and record its inputs, outputs, and downstream consumers.
2. Inventory V1 scientific definitions, configuration, and selected reference/release artifacts that V2 still depends on conceptually. Record source and version; distinguish reviewed inputs from local caches and exploratory outputs.
3. Define schemas for the Phase 1 species releases, Phase 2 search/index/article releases, Phase 3 raw responses and parsed decisions, and Phase 4/5 releases. Include stable keys, nullability, duplicate policy, and schema version.
4. Add run metadata to generated releases: package/version or commit, UTC run time, input artifact hashes, config snapshot/hash, query-plan hash where relevant, provider/model/question versions, and row counts.
5. Preserve current data directories and names where possible. Document any migration before changing a release contract.

**Exit condition:** every active phase has a documented contract and can identify the exact upstream artifact/configuration used for a release.

### 1. Strengthen run configuration and Phase 2 provenance

1. Make one tracked runtime configuration the source for active phase settings, with ignored local files/environment variables for credentials. Validate required keys and ranges at startup.
2. Keep `configs/pubmed_query_templates.yaml` as the human-edited query definition. Materialize a query-plan artifact that includes rendered query strings, their stable IDs, species/synonym inputs, template/config fingerprint, date range, and creation metadata.
3. Add an explicit preflight report showing query counts, planned PMIDs, sampling parameters, expected requests, resume coverage, and missing credentials/configuration. If the project wants an approval gate, make it an explicit setting; do not silently treat the current soft approval field as a gate.
4. Make acquisition metadata distinguish complete, partial, and failed runs. Preserve per-query result/error status and make a failed query retryable without discarding successful query results.
5. Review the PubMed client retry/rate-limit behaviour against NCBI's current guidance; retain conservative rate limiting and expose it in configuration.

**Exit condition:** a user can inspect exactly what will be queried and how much work remains before starting acquisition, and an interrupted run can resume without repeating successful searches/fetches.

### 2. Complete a provider-neutral decision ledger

1. Define shared decision input/output schemas and a stable record key. Include provider, model, question version, run ID, probability or structured answer, status, timestamps, and errors.
2. Separate provider clients from decision orchestration and persistence. Parse/validate responses before marking a row successful; malformed or incomplete responses remain failed and retryable.
3. Persist raw response artifacts separately from normalized decision records. Checkpoint successful rows in bounded batches and resume by stable input/question/run identity. Make concurrency, request limits, retries, timeout, and max-call controls configurable.
4. Make run identity explicit. Reuse successful work only when the input hash, model, question version, and run ID match; a question or model change must not silently reuse stale predictions.
5. Build a deterministic offline step that converts saved probabilities/answers into Phase 3 release tables. Changing a threshold should rebuild the release without new provider calls.

**Exit condition:** an interrupted decision run resumes safely, failures can be retried, and the same saved decisions plus config rebuild the same release output.

### 3. Pilot Jev System 1 on a labelled V2 task

1. Confirm OpenRouter/Jev access and the currently supported Decisions API request/response contract. Keep endpoint, model, timeout, and retry settings configurable; do not hard-code a secret.
2. Write and version one V2-specific question and a short rubric. State the target species explicitly alongside title and abstract, and define edge cases such as colonisation, contamination, commensal organisms, animal/environment-only evidence, and incidental mentions.
3. Create a small, human-labelled pilot spanning species and edge cases. Compare Jev probabilities with the existing/intended LLM workflow and human labels. Inspect disagreements, calibration, and abstention/uncertainty behaviour.
4. Estimate cost and latency before expanding. Add a maximum-call option and safe concurrency defaults; use resume checkpoints.
5. Select a probability threshold using the pilot's stated error trade-off. Save probabilities and apply the threshold in a later deterministic build step, rather than baking the threshold into provider calls.
6. Keep the conventional LLM path available until the pilot demonstrates acceptable performance. Do not assume the AMR threshold or performance transfers to V2.

**Exit condition:** the question, labelled evaluation set, error analysis, cost estimate, chosen threshold, and a reproducible run artifact are reviewed before broad application.

### 4. Finish Phase 3 incrementally

Implement and release one capability at a time through the shared decision ledger:

1. Species/pathogen classification, after the Jev pilot establishes its target and label policy.
2. Structured microbiology questions, with explicit answer schemas and per-question validation. Use Jev only where a question is truly binary; do not flatten richer categorical answers into a single probability.
3. Abstract anonymisation and vectorisation with separate artifacts, versioned settings, and deterministic rebuilds where applicable.

Keep each output independently rebuildable. Do not couple a parser change to fresh model calls.

**Exit condition:** Phase 3's documented release outputs exist and conform to tested schemas; no active Phase 3 entry point is a placeholder.

### 5. Close the end-to-end workflow

1. Complete Phase 4 tables and Phase 5 plots as deterministic transformations of versioned release inputs.
2. Add thin CLI entry points for each runnable phase and a top-level documented run order. Notebooks should set parameters, call package APIs, show preflight/output summaries, and support inspection.
3. Add an incremental-update guide covering refreshes, partial year/date slices where applicable, reruns after question/model changes, and how to rebuild downstream artifacts without repeating upstream network/model work.
4. Document release promotion, artifact retention, and migration rules for changed schemas.

**Exit condition:** a clean installation can run or resume the documented workflow from inputs to final exports, and each release can be traced to its inputs and settings.

## Recommended first milestone

Complete steps 0 and 1, then implement the provider-neutral decision ledger and a small Jev pilot. This resolves the biggest uncertainty (whether a V2-specific question is reliable enough to use) before spending on full-corpus decisions or filling all Phase 3 modules. Keep the current Phase 2 query logic intact during this milestone except for provenance and preflight improvements.

## Review checklist for the pilot

- Is the decision target unambiguous and distinct from “the species is ever pathogenic”?
- Does the rubric say how to handle colonisation, contamination, commensal status, non-human evidence, and incidental co-mentions?
- Are the human labels independent of Jev and representative of difficult cases?
- Are model, question version, abstract hash, raw response, probability, status, and run ID retained?
- Can threshold changes be evaluated and applied without another API call?
- Is performance acceptable for the intended use, and is uncertainty surfaced rather than hidden?
- Is estimated cost and runtime acceptable at the planned corpus size?

## Reference locations

- AMR workflow overview and run order: `/Users/f.costa/Code/Antimicrobial-Resistance/README.md`
- AMR Jev/System 1 client, prompt, response validation, checkpointing, and run controls: `/Users/f.costa/Code/Antimicrobial-Resistance/amr/screening/system1.py`
- AMR PubMed query construction and acquisition patterns: `/Users/f.costa/Code/Antimicrobial-Resistance/amr/pubmed/queries.py`, `/Users/f.costa/Code/Antimicrobial-Resistance/amr/pubmed/search.py`
- V2 structure and phase responsibilities: `docs/organization.md`
- V1 archival policy and reference-data context: `../../version1/README.md`
- V2 Phase 2 query plan and templates: `pathogen_atlas_v2/abstracts/pubmed_query_plan.py`, `pathogen_atlas_v2/abstracts/pubmed_query_templates.py`, `configs/pubmed_query_templates.yaml`
- V2 Phase 2 acquisition/resume orchestration: `pathogen_atlas_v2/abstracts/abstract_exports.py`
- V2 Phase 3 decision source: `pathogen_atlas_v2/llm/pathogen_evidence.py`, `pathogen_atlas_v2/llm/jev.py`
