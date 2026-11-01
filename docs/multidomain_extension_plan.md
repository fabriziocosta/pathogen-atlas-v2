# Plan: Extend the Pathogen Atlas to Fungi and Viruses

## Goal

Extend the Atlas from its current bacterial and archaeal species set to fungal
and viral pathogens while preserving comparable, auditable evidence across
organism groups. Keep one master catalogue of features, then select an
appropriate shared and domain-specific subset for each organism group. Use
domain-specialised Jev prompts with a common decision and evidence schema.

This is a design plan. It does not add fungal or viral records, questions, or
prompts to the production pipeline.

## Design decisions

1. **Maintain one versioned master feature catalogue.** A feature has one stable
   ID, label, definition, answer type, category, and scoring rule. Each feature
   declares the organism groups to which it applies. The master catalogue is
   the union of shared, bacterial, fungal, and viral features.
2. **Select features through organism-group profiles.** Each group gets an
   ordered profile containing shared features plus its own applicable
   features. A feature that does not apply to a group is excluded from that
   group's Jev requests and summaries; it is never recorded as a negative.
3. **Separate organism group from taxonomy ranks.** Add a controlled
   `organism_group` field (`bacteria`, `fungi`, `viruses`) for pipeline routing.
   Taxonomy ranks remain source-specific: ICTV viral realms and higher ranks,
   for example, should not be forced into a bacterial `domain`/`kingdom`
   hierarchy.
4. **Use specialised prompts, with a shared contract.** A domain has a
   pathogen-evidence prompt and feature-question prompt set written for its
   biology, terminology, and common abstract failure modes. All domain prompts
   return the same structured fields and obey the same abstract-only evidence
   rule.
5. **Score literature statements, not unstated biological facts.** A feature
   value describes whether that abstract supports the feature. An abstract
   that does not mention a feature is not proof that the species lacks it.
   Keep `not_scored` distinct from a scored `no` and from `not_applicable`.
6. **Evaluate each domain before releasing scores.** Keep Jev probabilities
   separate from accepted feature values until labelled evaluation supports a
   domain- and question-specific confidence threshold.

## Master feature catalogue

The catalogue should be data-driven, not a single flat list applied to every
organism. One proposed record shape is:

```yaml
- feature_id: transmission.airborne
  label: AIRBORNE
  category: transmission
  applicable_groups: [bacteria, fungi, viruses]
  definition: "Does the abstract report human infection acquired through inhalation of infectious material?"
  question_version: transmission-airborne-v1
  prompt_family: shared_transmission
  answer_type: yes_no
  evidence_policy: explicit_abstract_support

- feature_id: bacteria.biofilm
  label: BIOFILM
  category: bacterial_physiology
  applicable_groups: [bacteria]
  definition: "Does the abstract report biofilm formation by the named bacterium in the human disease context?"
  question_version: bacteria-biofilm-v1
  prompt_family: bacterial_features
  answer_type: yes_no
  evidence_policy: explicit_abstract_support
```

Feature IDs must remain stable when labels or definitions are edited. A
materially changed question gets a new question version and is rescored; old
answers remain attributable to the old version. Store the resolved feature
profile version with every run and release.

### Shared features

Review each existing feature rather than assuming it is universally portable.
Potential shared groups include transmission and exposure, animal or
environmental reservoirs, clinical presentation, patient risk factors,
healthcare association, geography, outbreaks, epidemiology, and vaccines.
Rewrite a shared definition only when it can retain one clear meaning across
groups; otherwise split it into separately versioned domain questions.

### Bacterial features

Retain bacterial concepts such as bacterial physiology and resistance where
appropriate. Review the existing 73 questions for bacterial-only assumptions,
including carriage, anaerobic physiology, bacterial spores, and the meaning of
antimicrobial resistance. Do not automatically classify every feature outside
the current bacterial-physiology category as shared.

### Fungal features

Candidate fungal concepts for a pilot include:

- Yeast, mould, or dimorphic form; temperature-associated morphology changes.
- Environmental reservoir and spore exposure, including inhalation.
- Infection by inoculation through damaged skin or wounds.
- Endogenous overgrowth versus acquisition from another host or environment.
- Superficial, cutaneous, subcutaneous, pulmonary, invasive, or disseminated
  disease context.
- Antifungal resistance, antifungal treatment class, and clinically important
  resistant infection.

Decide which of these belong in abstract-scored features. Stable taxonomic or
laboratory properties that rarely appear in clinical abstracts may be better
stored as curated metadata instead of asked of Jev. Use the [WHO Fungal
Priority Pathogens List](https://www.who.int/publications/b/60698) to select
representative fungi for pilot coverage, not as a replacement for the Atlas's
species inclusion rules.

### Viral features

Candidate viral concepts for a pilot include:

- Animal reservoir, zoonotic spillover, and evidence of sustained
  human-to-human transmission.
- Respiratory, faecal-oral, blood/body-fluid, vector-borne, sexual, and vertical
  routes where supported by the abstract.
- Duration of shedding, persistent infection, latency, and reactivation.
- Antiviral resistance and vaccine or vaccine-breakthrough context.
- Host range, clinical presentation, and public-health or outbreak context.

Keep genome type, segmentation, envelope, virion shape, and ICTV classification
as taxonomy or curated metadata unless the intended analysis specifically
requires those properties to be scored from abstracts. Use the [ICTV Master
Species List](https://ictv.global/msl) as the authoritative viral taxonomy
release, and record its release identifier and source taxon IDs. Preserve
strain, subtype, serotype, and variant names separately from the accepted viral
species when a source record provides them.

## Domain-specialised prompts

### Pathogen-evidence decision

Maintain separate, versioned prompt families for bacteria, fungi, and viruses.
Each asks whether the abstract directly supports that the named organism
caused or materially contributed to human infection or disease. Preserve the
same output contract across families: probability, status, evidence/run
metadata, and matched species identity.

Domain instructions should handle common distinctions explicitly:

- **Bacteria:** infection versus colonisation or carriage; isolate detection
  versus disease attribution; named species versus co-occurring organisms.
- **Fungi:** disease versus colonisation or environmental contamination; fungal
  infection versus allergy or mycotoxin illness; invasive disease versus
  incidental culture.
- **Viruses:** active human infection versus exposure, seropositivity, or
  environmental detection; virus versus infected host-cell or phage mention;
  species versus strain, subtype, or variant attribution.

Keep the abstract-only evidence rule and negative criteria versioned and
auditable. Do not use outside knowledge to fill gaps in an abstract.

### Feature decisions

For each abstract, construct one domain-specific state containing the accepted
species name, matched name/aliases, organism group, taxonomy source identifiers,
title, and abstract. Ask the selected profile's applicable yes/no features in
the same abstract scoring pass where provider limits allow. Give every
feature a short, versioned definition and instructions that state what counts
as positive evidence, what counts as negative, and when an answer is unknown
or not scored.

Keep the provider adapter and normalized result schema shared. Prompts and
feature selection vary by organism group; caching keys include species/alias
identity, abstract content hash, prompt family/version, feature-profile
version, model, and decision-source version so that a change reruns only the
affected decisions.

## Taxonomy and species identity

- Preserve the current bacterial Phase 1 workflow and its source metadata.
- Select and document a fungal accepted-name authority and identifiers before
  a full acquisition run. Reconcile alternate spellings, synonyms, complexes,
  and genus changes, keeping source IDs and mapping decisions auditable.
- Import viral taxa from a pinned ICTV Master Species List release. Add
  literature-used names and identifiers as aliases, without silently turning a
  strain, serotype, or variant into a species.
- Store `organism_group`, accepted name, source taxon ID, taxonomy source, and
  taxonomy release separately from names observed in PubMed queries.
- Maintain explicit mapping conflicts and unresolved names. Do not merge
  records across groups on name similarity alone.
- Use GBIF links as optional external references, not as the canonical source
  of bacterial, fungal, or viral identity. Retain match type and quality for
  every external mapping.

## Data and pipeline changes

1. **Phase 1 — multi-group species identity:** introduce source-specific
   taxonomy adapters that produce one normalized species table and a
   crosswalk/alias ledger with `organism_group` and provenance.
2. **Phase 2 — abstract acquisition:** query and cache PubMed at each accepted
   name/alias while keeping the current PMIDs/abstracts contract. Record which
   matched name produced each species–PMID association.
3. **Phase 3 — decisions:** select the domain prompt family and applicable
   feature profile. Store pathogen-evidence decisions and feature answers in a
   long-form ledger keyed by species, PMID, feature ID, and prompt version.
4. **Phase 4 — tables:** build shared summaries plus domain-aware feature
   tables. Feature profiles must declare applicability; missing/unscored
   values must not be coerced to zero. Record index versions and the
   normalization cohort used for each Pathogenic Interest Index.
5. **Phase 5 — embedding:** embed the shared feature subset for cross-group
   comparisons. Keep group-specific embeddings separate or clearly faceted;
   never use zero imputation for inapplicable domain-specific features.
6. **Web explorer:** filter by organism group; show shared and selected-group
   features; identify question definitions and coverage; provide taxonomy and
   evidence links appropriate to the taxonomic source.

The Pathogenic Interest Index should not be assumed directly comparable across
groups: PubMed query coverage, abstract sampling, terminology, and prompt
performance may differ. Keep its formula visible, retain its count components,
and evaluate whether a shared or group-normalized scale is defensible before
using it to compare marker sizes across groups.

## Evaluation and release gates

Before production labels are released:

1. Select representative pathogen species and abstracts for each organism
   group, including difficult negative cases and naming ambiguity.
2. Have domain experts label human disease attribution and a sample of each
   group's features.
3. Evaluate pathogen-evidence and feature prompts separately by group and
   question. Review false positives, false negatives, calibration, malformed
   responses, and coverage differences.
4. Select and document confidence thresholds per decision family only after
   evaluation. Store model, question, prompt, feature-profile, source taxonomy,
   and threshold versions with the release.
5. Verify that domain filtering never presents an inapplicable feature as zero
   and that web summaries clearly show scored counts and unscored coverage.

## Suggested implementation order

1. Finalize the master feature registry schema and define `organism_group`.
2. Audit the 73 existing questions into shared, bacterial-specific, revise, or
   retire decisions.
3. Draft fungal and viral feature candidates and domain-specific prompt
   templates; keep them in draft configuration.
4. Select taxonomy sources/releases and produce small, inspectable pilot
   species/alias tables for fungi and viruses.
5. Run bounded abstract acquisition and decision pilots, build labelled
   evaluations, and revise prompts/questions.
6. Implement the domain-aware Phase 1–4 contracts, reusing caches where input
   hashes and prompt/profile versions match.
7. Decide the cross-domain embedding and index comparison strategy, then
   update the explorer to browse all groups.
8. Release additional organism groups only after the quality gates above pass.

## Initial scope boundary

The first extension should add fungi and viruses as separate organism groups,
not attempt to comprehensively represent every microbial eukaryote, parasite,
or prion. Keep their inclusion possible through the registry and taxonomy
adapters, but defer their species sources and feature profiles to later work.
