#!/usr/bin/env python3
"""Build the compact, static JSON dataset for the Pathogen Atlas explorer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[3]
RELEASE = ROOT / "data/04_tables/release"
COORDINATES = ROOT / "data/05_plots/release/species_feature_space_coordinates.parquet"
SYNONYM_MAPPING = ROOT / "data/01_species/release/species_synonym_mapping.parquet"
FEATURE_ANSWERS = RELEASE / "domain_expert_answers.parquet"
WIKIPEDIA_LINKS = ROOT / "sites/pathogen-atlas-explorer/data/species_wikipedia_links.csv"
OUTPUT = ROOT / "sites/pathogen-atlas-explorer/dist/explorer-data.json"
EVIDENCE_DIR = ROOT / "sites/pathogen-atlas-explorer/dist/evidence"
QUESTION_SET_PATH = ROOT / "configs/domain_expert_questions.yaml"
TAXONOMY_LEVELS = ("domain", "kingdom", "phylum", "class", "order", "family", "genus")
EVIDENCE_SHARD_PREFIX_LENGTH = 2


def clean_text(value: Any) -> str | None:
    """Convert nullable scalar values to JSON-safe strings."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def as_optional_number(value: Any) -> int | float | None:
    """Convert a nullable pandas scalar to a JSON-safe number."""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def build_payload(
    evidence: pd.DataFrame,
    species_summary: pd.DataFrame,
    feature_matrix: pd.DataFrame,
    coordinates: pd.DataFrame,
    pathogenic_index: pd.DataFrame,
    synonym_mapping: pd.DataFrame | None = None,
    wikipedia_links: pd.DataFrame | None = None,
    feature_answers: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Join release tables into the small client-side explorer contract."""
    required_coordinates = {"canonical_species", "imste_1", "imste_2"}
    if not required_coordinates.issubset(coordinates.columns):
        raise ValueError(f"Coordinate table needs columns {sorted(required_coordinates)}.")
    required_features = {"canonical_species"}
    if not required_features.issubset(feature_matrix.columns):
        raise ValueError("Feature matrix is missing canonical_species.")
    required_evidence = {"canonical_species", "pmid"}
    if not required_evidence.issubset(evidence.columns):
        raise ValueError("Evidence table needs canonical_species and pmid.")
    if not {"canonical_species", "pathogenic_index"}.issubset(pathogenic_index.columns):
        raise ValueError("Pathogenic-index table needs canonical_species and pathogenic_index.")
    if pathogenic_index["canonical_species"].duplicated().any():
        raise ValueError("Pathogenic-index table must contain exactly one row per species.")
    index_by_species = pathogenic_index.set_index("canonical_species")["pathogenic_index"].to_dict()
    wikipedia_by_species: dict[str, str | None] = {}
    if wikipedia_links is not None:
        required_wikipedia = {"canonical_species", "wikipedia_url"}
        if not required_wikipedia.issubset(wikipedia_links.columns):
            raise ValueError("Wikipedia table is missing canonical_species or wikipedia_url.")
        if wikipedia_links["canonical_species"].duplicated().any():
            raise ValueError("Wikipedia table must contain exactly one row per species.")
        wikipedia_by_species = {
            str(row.canonical_species): clean_text(row.wikipedia_url)
            for row in wikipedia_links[["canonical_species", "wikipedia_url"]].itertuples(index=False)
        }
    synonyms_by_species: dict[str, set[str]] = {}
    if synonym_mapping is not None:
        required_synonyms = {"input_name", "canonical_species", "mapping_type"}
        if not required_synonyms.issubset(synonym_mapping.columns):
            raise ValueError("Synonym mapping is missing input_name, canonical_species, or mapping_type.")
        synonym_rows = synonym_mapping.loc[synonym_mapping["mapping_type"].astype(str).str.casefold().eq("synonym")]
        for alias_value, species_value in synonym_rows[["input_name", "canonical_species"]].itertuples(index=False, name=None):
            alias = clean_text(alias_value)
            species = clean_text(species_value)
            if alias and species and alias.casefold() != species.casefold():
                synonyms_by_species.setdefault(species, set()).add(alias)

    feature_summary_path = RELEASE / "species_feature_summary.parquet"
    feature_summary = pd.read_parquet(
        feature_summary_path,
        columns=["canonical_species", "question_id", "concept", "category", "n_answered"],
    )
    question_config = yaml.safe_load(QUESTION_SET_PATH.read_text(encoding="utf-8"))
    questions = question_config.get("questions", {})
    features = pd.DataFrame(
        [
            {
                "question_id": str(question_id),
                "concept": str(question.get("concept", question_id)),
                "category": str(question.get("category", "uncategorized")),
                "column": f"feature_{question_id}",
            }
            for question_id, question in questions.items()
        ]
    ).sort_values(["category", "concept", "question_id"], kind="stable")
    feature_answered_counts = {
        (str(row.canonical_species), str(row.question_id)): as_optional_number(row.n_answered)
        for row in feature_summary.itertuples(index=False)
    }
    feature_definitions = [
        {
            "id": str(row.question_id),
            "label": str(row.concept),
            "category": str(row.category),
            "definition": str(questions.get(str(row.question_id), {}).get("definition", "")).strip(),
        }
        for row in features.itertuples(index=False)
    ]
    missing_definitions = [feature["id"] for feature in feature_definitions if not feature["definition"]]
    if missing_definitions:
        raise ValueError(f"Question set is missing concept definitions for: {missing_definitions}")
    feature_ids = features["question_id"].astype(str).tolist()

    feature_ids_by_pair: dict[tuple[str, str], list[str]] = {}
    if feature_answers is not None:
        answer_columns = ["canonical_species", "pmid", "question_id", "answer_yes"]
        missing_answer_columns = set(answer_columns) - set(feature_answers.columns)
        if missing_answer_columns:
            raise ValueError(f"Feature-answer table is missing columns: {sorted(missing_answer_columns)}.")
        answers = feature_answers[answer_columns].copy()
        for column in answer_columns[:3]:
            answers[column] = answers[column].astype(str).str.strip()
        answers = answers.loc[answers["answer_yes"].fillna(False).astype(bool)]
        answers = answers.loc[answers["question_id"].isin(feature_ids)]
        if answers.duplicated(["canonical_species", "pmid", "question_id"]).any():
            raise ValueError("Feature-answer table contains duplicate species, PMID, and feature rows.")
        feature_order = {question_id: index for index, question_id in enumerate(feature_ids)}
        for (species, pmid), group in answers.groupby(["canonical_species", "pmid"], sort=False):
            feature_ids_by_pair[(species, pmid)] = sorted(
                group["question_id"].tolist(), key=feature_order.__getitem__
            )

    if "accepted" not in evidence.columns:
        raise ValueError("Evidence table needs an 'accepted' flag to identify pathogen-confirming PMIDs.")
    evidence = evidence.loc[evidence["accepted"].fillna(False).astype(bool)].copy()
    evidence["pmid"] = evidence["pmid"].astype(str).str.strip()
    evidence = evidence.loc[evidence["pmid"].str.fullmatch(r"\d+")].drop_duplicates(
        ["canonical_species", "pmid"], keep="last"
    )

    paper_by_species: dict[str, list[dict[str, Any]]] = {}
    paper_columns = [column for column in ("canonical_species", "pmid", "title", "publication_year", "publication_date", "doi", "p_yes") if column in evidence]
    for row in evidence[paper_columns].sort_values(
        [column for column in ("canonical_species", "publication_year", "pmid") if column in paper_columns],
        ascending=[True] + ([False] if "publication_year" in paper_columns else []) + [True],
        na_position="last",
    ).itertuples(index=False, name=None):
        record = dict(zip(paper_columns, row))
        species_name = str(record["canonical_species"])
        paper_by_species.setdefault(species_name, []).append(
            {
                "pmid": str(record["pmid"]),
                "title": clean_text(record.get("title")),
                "date": clean_text(record.get("publication_date")),
                "year": as_optional_number(record.get("publication_year")),
                "doi": clean_text(record.get("doi")),
                "confidence": as_optional_number(record.get("p_yes")),
                "feature_ids": feature_ids_by_pair.get((species_name, str(record["pmid"])), []),
            }
        )

    summary_columns = ["canonical_species", *[rank for rank in TAXONOMY_LEVELS if rank in species_summary.columns], "n_articles", "n_sampled", "n_causation"]
    summary_by_species = (
        species_summary[summary_columns]
        .drop_duplicates("canonical_species", keep="last")
        .set_index("canonical_species")
        .to_dict(orient="index")
    )
    feature_by_species = feature_matrix.set_index("canonical_species").to_dict(orient="index")
    coordinate_species = coordinates["canonical_species"].astype(str)
    if coordinate_species.duplicated().any():
        examples = coordinate_species.loc[coordinate_species.duplicated(keep=False)].drop_duplicates().head(5).tolist()
        raise ValueError(f"IMSTE coordinates must contain exactly one row per species; duplicates include {examples}.")
    if wikipedia_links is not None:
        coordinate_names = set(coordinate_species)
        wikipedia_names = set(wikipedia_by_species)
        missing_wikipedia_rows = sorted(coordinate_names - wikipedia_names)[:5]
        extra_wikipedia_rows = sorted(wikipedia_names - coordinate_names)[:5]
        if missing_wikipedia_rows or extra_wikipedia_rows:
            raise ValueError(
                "Wikipedia lookup table must cover the coordinate species exactly; "
                f"missing examples: {missing_wikipedia_rows}, extra examples: {extra_wikipedia_rows}."
            )
    coordinate_rows = coordinates

    records: list[dict[str, Any]] = []
    for point in coordinate_rows.sort_values("canonical_species").itertuples(index=False):
        name = str(point.canonical_species)
        if name not in index_by_species:
            raise ValueError(f"Pathogenic-index table has no row for plotted species {name!r}.")
        index_value = as_optional_number(index_by_species[name])
        if index_value is None or not 0 <= float(index_value) <= 1:
            raise ValueError(f"Pathogenic index for {name!r} must be a finite value between zero and one.")
        feature_row = feature_by_species.get(name, {})
        summary_row = summary_by_species.get(name, {})
        taxonomy = {rank: clean_text(summary_row.get(rank)) for rank in TAXONOMY_LEVELS if rank in summary_columns}
        shard = hashlib.sha256(name.encode("utf-8")).hexdigest()[:EVIDENCE_SHARD_PREFIX_LENGTH]
        records.append(
            {
                "species": name,
                "wikipedia_url": wikipedia_by_species.get(name),
                "x": float(point.imste_1),
                "y": float(point.imste_2),
                "taxonomy": taxonomy,
                "synonyms": sorted(synonyms_by_species.get(name, set()), key=str.casefold),
                "n_articles": as_optional_number(summary_row.get("n_articles")),
                "n_sampled": as_optional_number(summary_row.get("n_sampled")),
                "n_causation": as_optional_number(summary_row.get("n_causation")),
                "n_pmids": len(paper_by_species.get(name, [])),
                "pathogenic_index": float(index_value),
                "evidence_shard": shard,
                "features": [as_optional_number(feature_row.get(f"feature_{question_id}")) for question_id in feature_ids],
                "feature_answered": [feature_answered_counts.get((name, question_id)) for question_id in feature_ids],
            }
        )

    evidence_shards: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for record in records:
        species_name = record["species"]
        shard = record["evidence_shard"]
        evidence_shards.setdefault(shard, {})[species_name] = paper_by_species.get(species_name, [])
    evidence_version = hashlib.sha256(
        json.dumps(evidence_shards, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    return {
        "schema_version": 2,
        "evidence_version": evidence_version,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "Pathogen Atlas V2 Phase 4 accepted evidence and Phase 5 IMSTE coordinates",
        "counts": {
            "species": len(records),
            "species_pmid_records": sum(record["n_pmids"] for record in records),
            "pubmed_search_records": sum(int(record["n_articles"] or 0) for record in records),
            "features": len(feature_definitions),
            "evidence_shards": len(evidence_shards),
        },
        "taxonomy_levels": list(TAXONOMY_LEVELS),
        "features": feature_definitions,
        "species": records,
        "_evidence_shard_payloads": evidence_shards,
    }


def main() -> None:
    """Load local Phase 4/5 releases and write browser-ready JSON."""
    required_paths = [
        RELEASE / "evidence_records.parquet",
        RELEASE / "species_summary.parquet",
        RELEASE / "pathogenic_index.parquet",
        RELEASE / "species_feature_matrix.parquet",
        RELEASE / "species_feature_summary.parquet",
        FEATURE_ANSWERS,
        COORDINATES,
        SYNONYM_MAPPING,
        WIKIPEDIA_LINKS,
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Build Phase 4 tables and run Phase 5 IMSTE plots first. Missing: " + ", ".join(missing)
        )
    payload = build_payload(
        pd.read_parquet(RELEASE / "evidence_records.parquet"),
        pd.read_parquet(RELEASE / "species_summary.parquet"),
        pd.read_parquet(RELEASE / "species_feature_matrix.parquet"),
        pd.read_parquet(COORDINATES),
        pd.read_parquet(RELEASE / "pathogenic_index.parquet"),
        pd.read_parquet(SYNONYM_MAPPING),
        pd.read_csv(WIKIPEDIA_LINKS, keep_default_na=False),
        pd.read_parquet(FEATURE_ANSWERS, columns=["canonical_species", "pmid", "question_id", "answer_yes"]),
    )
    evidence_shards = payload.pop("_evidence_shard_payloads")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    expected_shards = {f"{shard}.json" for shard in evidence_shards}
    for stale_shard in EVIDENCE_DIR.glob("*.json"):
        if stale_shard.name not in expected_shards:
            stale_shard.unlink()
    for shard, species_papers in evidence_shards.items():
        shard_path = EVIDENCE_DIR / f"{shard}.json"
        shard_content = json.dumps(species_papers, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        if not shard_path.exists() or shard_path.read_text(encoding="utf-8") != shard_content:
            shard_path.write_text(shard_content, encoding="utf-8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}: {payload['counts']['species']:,} species, "
        f"{payload['counts']['species_pmid_records']:,} species-PMID records, "
        f"{payload['counts']['features']} features; "
        f"{payload['counts']['evidence_shards']} lazy evidence shards "
        f"({OUTPUT.stat().st_size / 1_000_000:.1f} MB initial dataset)."
    )


if __name__ == "__main__":
    main()
