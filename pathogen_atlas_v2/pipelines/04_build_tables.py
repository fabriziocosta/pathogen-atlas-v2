"""Phase 4: build evidence, species, feature, and emergence release tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.common.io import ensure_parent, load_yaml, resolve_path
from pathogen_atlas_v2.llm.domain_expert import apply_domain_question_threshold, load_current_domain_expert_probabilities
from pathogen_atlas_v2.llm.pathogen_evidence import apply_confidence_threshold, load_pathogen_evidence_question
from pathogen_atlas_v2.notebook_utils import notebook_phase_parameters
from pathogen_atlas_v2.tables.abstract_counts import build_abstract_count_table
from pathogen_atlas_v2.tables.emergence_tables import build_emergence_table
from pathogen_atlas_v2.tables.feature_tables import build_feature_tables, build_species_feature_matrix
from pathogen_atlas_v2.tables.species_table import build_species_summary
from pathogen_atlas_v2.tables.web_exports import export_release_tables


def _path(config: dict[str, Any], key: str, default: str) -> Path:
    return resolve_path(str(config.get(key, default)))


def phase4_preflight(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report Phase 4 input readiness and the rows available to each release table."""
    settings = dict(config or {})
    pubmed = load_yaml(resolve_path(str(settings.get("pubmed_config_path", "configs/pubmed_query_templates.yaml"))))
    outputs = pubmed.get("outputs", {})
    paths = {
        "abstracts": _path(settings, "abstracts_path", outputs.get("release_abstracts_path", "data/02_abstracts/release/abstracts.parquet")),
        "species_matches": _path(settings, "species_matches_path", outputs.get("release_species_matches_path", "data/02_abstracts/release/abstract_species_matches.parquet")),
        "canonical_pmid_index": _path(settings, "canonical_pmid_index_path", outputs.get("canonical_pmid_index_path", "data/02_abstracts/filtered/canonical_species_pmids.parquet")),
        "sampled_pmid_index": _path(settings, "sampled_pmid_index_path", outputs.get("sampled_canonical_pmid_index_path", "data/02_abstracts/filtered/canonical_species_pmids_sampled.parquet")),
        "gate_decisions": _path(settings, "decision_path", "data/03_decisions/parsed/pathogen_evidence_jev.parquet"),
        "expert_answers": _path(settings, "expert_answers_path", "data/03_decisions/parsed/domain_expert_answers.parquet"),
        "species_taxonomy": _path(settings, "species_taxonomy_path", "data/01_species/release/species_taxonomy.parquet"),
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    return {
        "status": "not_ready" if missing else "ready",
        "inputs": {name: {"path": str(path), "exists": path.exists()} for name, path in paths.items()},
        "missing_inputs": missing,
        "run_id": settings.get("run_id", "jev-human-pathogen-evidence-v1"),
        "expert_run_id": settings.get("expert_run_id", "jev-domain-expert-features-v1"),
        "confidence_threshold": float(settings.get("confidence_threshold", 0.75)),
        "model": str(settings.get("model", "typesafe/jev-1.14")),
        "question_key": settings.get("question_key", "human_pathogen_evidence"),
        "question_config_path": settings.get("question_config_path", "configs/llm_questions.yaml"),
        "question_confidence_threshold": float(settings.get("question_confidence_threshold", 0.75)),
    }


def build_phase4_tables(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Join current Phase 2/3 results, create stable tables, and write Parquet releases."""
    settings = dict(config or {})
    preflight = phase4_preflight(settings)
    if preflight["status"] != "ready":
        return {**preflight, "status": "not_ready"}
    pubmed = load_yaml(resolve_path(str(settings.get("pubmed_config_path", "configs/pubmed_query_templates.yaml"))))
    outputs = pubmed.get("outputs", {})
    abstracts_path = _path(settings, "abstracts_path", outputs.get("release_abstracts_path", "data/02_abstracts/release/abstracts.parquet"))
    matches_path = _path(settings, "species_matches_path", outputs.get("release_species_matches_path", "data/02_abstracts/release/abstract_species_matches.parquet"))
    canonical_pmid_index_path = _path(settings, "canonical_pmid_index_path", outputs.get("canonical_pmid_index_path", "data/02_abstracts/filtered/canonical_species_pmids.parquet"))
    sampled_pmid_index_path = _path(settings, "sampled_pmid_index_path", outputs.get("sampled_canonical_pmid_index_path", "data/02_abstracts/filtered/canonical_species_pmids_sampled.parquet"))
    gate_path = _path(settings, "decision_path", "data/03_decisions/parsed/pathogen_evidence_jev.parquet")
    answers_path = _path(settings, "expert_answers_path", "data/03_decisions/parsed/domain_expert_answers.parquet")
    abstracts = pd.read_parquet(abstracts_path)
    matches = pd.read_parquet(matches_path)
    canonical_pmid_index = pd.read_parquet(canonical_pmid_index_path)
    sampled_pmid_index = pd.read_parquet(sampled_pmid_index_path)
    gate = pd.read_parquet(gate_path)
    run_id = str(settings.get("run_id", "jev-human-pathogen-evidence-v1"))
    if "run_id" in gate.columns:
        gate = gate.loc[gate["run_id"].astype(str).eq(run_id)].copy()
    model = str(settings.get("model", "typesafe/jev-1.14"))
    if "model" in gate.columns:
        gate = gate.loc[gate["model"].astype(str).eq(model)].copy()
    question = load_pathogen_evidence_question(
        settings.get("question_config_path", "configs/llm_questions.yaml"),
        str(settings.get("question_key", "human_pathogen_evidence")),
    )
    for column, expected in (("question_id", question["id"]), ("question_version", question["version"])):
        if column in gate.columns:
            gate = gate.loc[gate[column].astype(str).eq(str(expected))].copy()
    gate = apply_confidence_threshold(gate, float(settings.get("confidence_threshold", 0.75)))
    gate = gate.loc[gate["accepted"].fillna(False).astype(bool)].copy()
    gate["pmid"] = gate["pmid"].astype(str)
    abstracts["pmid"] = abstracts["pmid"].astype(str)
    article_columns = [column for column in ("pmid", "title", "abstract", "publication_year", "publication_date", "doi") if column in abstracts]
    evidence = gate.merge(abstracts[article_columns].drop_duplicates("pmid"), on="pmid", how="inner", validate="many_to_one")
    evidence = evidence.drop_duplicates(["pmid", "canonical_species"], keep="last")
    expert = load_current_domain_expert_probabilities(settings)
    # Keep the Phase 4 contract aligned with the standalone Phase 3 answer export.
    if expert.empty and answers_path.exists():
        expert = pd.read_parquet(answers_path)
        expert = expert.loc[
            expert.get("gate_run_id", pd.Series(index=expert.index, dtype=object)).astype(str).eq(str(settings.get("run_id", "jev-human-pathogen-evidence-v1")))
            & expert.get("expert_run_id", pd.Series(index=expert.index, dtype=object)).astype(str).eq(str(settings.get("expert_run_id", "jev-domain-expert-features-v1")))
        ].copy()
    expert = apply_domain_question_threshold(expert, float(settings.get("question_confidence_threshold", 0.75))) if not expert.empty else expert
    evidence_keys = evidence[["pmid", "canonical_species"]].drop_duplicates()
    if not expert.empty:
        expert["pmid"] = expert["pmid"].astype(str)
        expert = expert.merge(evidence_keys, on=["pmid", "canonical_species"], how="inner")
    species_summary = build_species_summary(abstracts, matches, evidence, canonical_pmid_index, sampled_pmid_index)
    taxonomy_path = _path(settings, "species_taxonomy_path", "data/01_species/release/species_taxonomy.parquet")
    if taxonomy_path.exists():
        taxonomy = pd.read_parquet(taxonomy_path).drop_duplicates("canonical_species")
        species_summary = species_summary.merge(taxonomy, on="canonical_species", how="left", suffixes=("", "_taxonomy"))
    feature_long, feature_summary = build_feature_tables(expert)
    feature_matrix = build_species_feature_matrix(feature_summary)
    abstract_counts = build_abstract_count_table(evidence)
    emergence = build_emergence_table(
        species_summary,
        evidence,
        first_window_years=int(settings.get("first_window_years", 10)),
        publications_to_reach=int(settings.get("publications_to_reach", 10)),
        minimum_causation_count=int(settings.get("minimum_causation_count", 11)),
        maximum_article_count=int(settings.get("maximum_article_count", 30000)),
    )
    release_dir = resolve_path(str(settings.get("release_dir", "data/04_tables/release")))
    tables = {
        "evidence_records": evidence.sort_values(["canonical_species", "pmid"]).reset_index(drop=True),
        "species_summary": species_summary,
        "domain_expert_answers": feature_long,
        "species_feature_summary": feature_summary,
        "species_feature_matrix": feature_matrix,
        "abstract_counts": abstract_counts,
        "emergence": emergence,
    }
    table_paths = {}
    for name, frame in tables.items():
        path = ensure_parent(release_dir / f"{name}.parquet")
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
        table_paths[name] = str(path)
    web_exports = export_release_tables(release_dir, settings.get("web_export_dir"))
    metadata = {
        "status": "completed",
        "source": "pathogen_atlas_v2_phase4",
        "run_id": str(settings.get("run_id", "jev-human-pathogen-evidence-v1")),
        "expert_run_id": str(settings.get("expert_run_id", "jev-domain-expert-features-v1")),
        "confidence_threshold": float(settings.get("confidence_threshold", 0.75)),
        "model": str(settings.get("model", "typesafe/jev-1.14")),
        "question_key": settings.get("question_key", "human_pathogen_evidence"),
        "question_config_path": settings.get("question_config_path", "configs/llm_questions.yaml"),
        "question_confidence_threshold": float(settings.get("question_confidence_threshold", 0.75)),
        "record_counts": {name: len(frame) for name, frame in tables.items()},
        "outputs": {"parquet_tables": table_paths, "web_csv_exports": web_exports},
        "inputs": {name: details["path"] for name, details in preflight["inputs"].items()},
    }
    metadata_path = ensure_parent(release_dir / "table_creation_metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> dict[str, Any]:
    """Run Phase 4 when enabled in notebook parameters; otherwise show preflight."""
    config = notebook_phase_parameters("phase4_table_creation")
    if not bool(config.get("run_phase", False)):
        result = {**phase4_preflight(config), "status": "preflight_only", "reason": "Set run_phase: true to build the Phase 4 tables."}
    else:
        result = build_phase4_tables(config)
    print(result)
    return result


if __name__ == "__main__":
    main()
