"""Generate and load inspectable PubMed query plans."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.common.io import ensure_parent, load_yaml, resolve_path
from pathogen_atlas_v2.abstracts.pubmed_query_templates import (
    load_abstract_filter_terms,
    load_pubmed_query_template_config,
    render_pubmed_query,
    stable_query_hash,
)


class PubMedQueryPlanError(RuntimeError):
    """Raised when PubMed query-plan generation cannot proceed."""


REQUIRED_MAPPING_COLUMNS = {"input_name", "canonical_species", "mapping_type"}


def utc_now_iso() -> str:
    """Return a UTC timestamp suitable for metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_phase1_synonym_mapping(path: str | Path) -> pd.DataFrame:
    """Load Phase 1 synonym mappings used to build PubMed queries."""
    path = resolve_path(path)
    if not path.exists():
        raise PubMedQueryPlanError(f"Missing Phase 1 synonym mapping: {path}")
    mapping = pd.read_parquet(path)
    missing = REQUIRED_MAPPING_COLUMNS - set(mapping.columns)
    if missing:
        raise PubMedQueryPlanError(f"Phase 1 synonym mapping is missing columns: {sorted(missing)}")
    return mapping.drop_duplicates(subset=["input_name", "canonical_species", "mapping_type"])


def canonical_species_synonym_groups(mapping: pd.DataFrame) -> list[dict[str, Any]]:
    """Group Phase 1 input names into one synonym set per canonical species."""
    groups: list[dict[str, Any]] = []
    for canonical_species, group in mapping.groupby("canonical_species", sort=True):
        names = sorted(set(str(name) for name in group["input_name"] if str(name).strip()), key=str.casefold)
        mapping_types = sorted(set(str(kind) for kind in group["mapping_type"] if str(kind).strip()))
        groups.append(
            {
                "canonical_species": canonical_species,
                "matched_names": names,
                "mapping_types": mapping_types,
                "synonym_count": len(names),
            }
        )
    return groups


def build_pubmed_query_plan(
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
    filter_config_path: str | Path = "configs/abstract_filters.yaml",
) -> dict[str, Any]:
    """Build an inspectable PubMed query plan from Phase 1 outputs."""
    config = load_pubmed_query_template_config(template_config_path)
    terms = load_abstract_filter_terms(filter_config_path)
    source = config["source"]
    mapping = load_phase1_synonym_mapping(source["species_synonym_mapping_path"])

    queries: list[dict[str, Any]] = []
    for group in canonical_species_synonym_groups(mapping):
        for template in config["templates"]:
            for field in source["fields"]:
                query = render_pubmed_query(
                    template=template["template"],
                    matched_names=group["matched_names"],
                    field=field,
                    human_terms=terms["human_context"],
                    infection_terms=terms["infection_context"],
                    min_year=int(source["min_year"]),
                    max_year=int(source["max_year"]),
                )
                query_hash = stable_query_hash(query)
                queries.append(
                    {
                        "query_id": f"q_{query_hash}",
                        "query_hash": query_hash,
                        "template_id": template["id"],
                        "canonical_species": group["canonical_species"],
                        "matched_names": group["matched_names"],
                        "mapping_types": group["mapping_types"],
                        "synonym_count": group["synonym_count"],
                        "field": field,
                        "min_year": int(source["min_year"]),
                        "max_year": int(source["max_year"]),
                        "term_sets": {
                            "human_context": terms["human_context"],
                            "infection_context": terms["infection_context"],
                        },
                        "query": query,
                    }
                )

    return {
        "generated_at": utc_now_iso(),
        "source": {
            "name": source["name"],
            "species_synonym_mapping_path": source["species_synonym_mapping_path"],
            "min_year": int(source["min_year"]),
            "max_year": int(source["max_year"]),
            "fields": list(source["fields"]),
            "max_pmids_per_canonical_species": int(source.get("max_pmids_per_canonical_species", 200)),
            "oldest_pmids_per_canonical_species": int(source.get("oldest_pmids_per_canonical_species", 30)),
            "pmid_sample_random_seed": int(source.get("pmid_sample_random_seed", 42)),
        },
        "approval": dict(config.get("query_plan", {}).get("approval", {})),
        "queries": queries,
    }


def write_pubmed_query_plan(
    plan: dict[str, Any],
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
) -> Path:
    """Write an inspectable PubMed query plan YAML."""
    config = load_pubmed_query_template_config(template_config_path)
    path = ensure_parent(resolve_path(config["query_plan"]["path"]))
    import yaml

    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(plan, handle, sort_keys=False, allow_unicode=False)
    return path


def materialize_pubmed_query_plan(
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
    filter_config_path: str | Path = "configs/abstract_filters.yaml",
) -> dict[str, Any]:
    """Build and write the Phase 2 PubMed query plan."""
    plan = build_pubmed_query_plan(template_config_path, filter_config_path)
    path = write_pubmed_query_plan(plan, template_config_path)
    return {"path": str(path), "query_count": len(plan["queries"]), "approval": plan.get("approval", {})}


def load_pubmed_query_plan(path: str | Path) -> dict[str, Any]:
    """Load a generated PubMed query plan YAML."""
    plan = load_yaml(resolve_path(path))
    if "queries" not in plan:
        raise PubMedQueryPlanError(f"Query plan does not contain queries: {path}")
    return plan
