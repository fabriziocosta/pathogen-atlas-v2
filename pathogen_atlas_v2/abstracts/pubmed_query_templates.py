"""Load and render PubMed query templates."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pathogen_atlas_v2.common.io import load_yaml, resolve_path


class PubMedQueryTemplateError(RuntimeError):
    """Raised when query-template configuration is invalid."""


def load_pubmed_query_template_config(path: str | Path = "configs/pubmed_query_templates.yaml") -> dict[str, Any]:
    """Load tracked PubMed query-template configuration."""
    config = load_yaml(resolve_path(path))
    if "source" not in config or "templates" not in config or "outputs" not in config:
        raise PubMedQueryTemplateError("PubMed query config must define source, templates, and outputs.")
    if not config["templates"]:
        raise PubMedQueryTemplateError("PubMed query config must contain at least one template.")
    return config


def load_abstract_filter_terms(path: str | Path = "configs/abstract_filters.yaml") -> dict[str, list[str]]:
    """Load tracked abstract filter term sets."""
    config = load_yaml(resolve_path(path))
    term_sets = config.get("term_sets", {})
    required = {"human_context", "infection_context"}
    missing = required - set(term_sets)
    if missing:
        raise PubMedQueryTemplateError(f"Abstract filter config is missing term sets: {sorted(missing)}")
    return {name: [str(term) for term in terms] for name, terms in term_sets.items()}


def quote_pubmed_term(term: str, field: str) -> str:
    """Render a single exact PubMed term for a field."""
    escaped = str(term).replace('"', '\\"')
    return f'"{escaped}"[{field}]'


def render_term_expression(terms: list[str], field: str) -> str:
    """Render an OR expression for terms in a PubMed field."""
    return " OR ".join(quote_pubmed_term(term, field) for term in terms)


def render_name_expression(names: list[str], field: str) -> str:
    """Render an OR expression for exact species/synonym names in a PubMed field."""
    return " OR ".join(quote_pubmed_term(name, field) for name in names)


def render_pubmed_query(
    template: str,
    matched_names: list[str],
    field: str,
    human_terms: list[str],
    infection_terms: list[str],
    min_year: int,
    max_year: int,
) -> str:
    """Render a PubMed query from a tracked template."""
    return " ".join(
        template.format(
            matched_names=render_name_expression(matched_names, field),
            field=field,
            human_terms=render_term_expression(human_terms, field),
            infection_terms=render_term_expression(infection_terms, field),
            min_year=min_year,
            max_year=max_year,
        ).split()
    )


def stable_query_hash(query: str) -> str:
    """Return a stable short hash for a rendered query."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
