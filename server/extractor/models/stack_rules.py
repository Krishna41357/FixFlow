"""
stack_rules.py — Per-stack file classification rules.

Each rule set is pure data: an ordered list of (matcher, tag, extractor_hint)
tuples. The rule_engine applies them path-first, content-sniff-as-tiebreaker —
same pattern as the existing _is_relevant_yml() three-stage decision in
repo_parser_controller.py, generalized across stacks.

Order matters within a rule set: first match wins. Put specific/unambiguous
path patterns first, broad/ambiguous ones (which need content sniffing) last.

Adding a new stack = adding a new entry here, NOT touching rule_engine.py.
"""

import re
from dataclasses import dataclass
from typing import Optional, Callable, List

from extractor.models.classification import FileTag


@dataclass
class ClassificationRule:
    """
    One rule in a stack's rule set.

    path_pattern   — regex tested against the file path. If it matches and
                      content_sniff is None, this rule fires immediately.
    content_sniff   — optional regex tested against file content. Only used
                      as a tiebreaker when path_pattern alone is ambiguous,
                      OR as the sole matcher when path_pattern is None
                      (e.g. ambiguous .yml files needing content to disambiguate).
    tag             — the FileTag this rule assigns on match.
    extractor_hint  — name of the (future) Stage-3 extractor plugin that
                      should run on files this rule matches. Stored now,
                      consumed later — see ClassifiedFile.pending_extractor.
    confidence      — 1.0 for unambiguous path-only matches, lower (e.g. 0.6)
                      when a content_sniff tiebreaker was needed.
    """
    path_pattern: Optional[str]
    tag: FileTag
    extractor_hint: Optional[str] = None
    content_sniff: Optional[str] = None
    confidence: float = 1.0
    rule_name: str = ""   # auto-filled if blank, see __post_init__

    def __post_init__(self):
        if not self.rule_name:
            self.rule_name = (
                f"path:{self.path_pattern}"
                if self.path_pattern else f"content:{self.content_sniff}"
            )


# ── dbt (matches existing repo_parser_controller.py behavior) ───────────────
# Kept here for consistency even though repo_parser_controller.py already
# has its own hardcoded version — this is the generalized equivalent that
# future stacks follow the same shape as.

DBT_RULES: List[ClassificationRule] = [
    ClassificationRule(
        path_pattern=r"^migrations/.*\.sql$",
        tag=FileTag.MIGRATION,
        extractor_hint="raw_sql_migration",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"^(models|seeds|snapshots)/.*\.sql$",
        tag=FileTag.DATA_ACCESS,
        extractor_hint="dbt_sql_model",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"^(models|seeds|snapshots|analyses)/.*\.(yml|yaml)$",
        tag=FileTag.SCHEMA_DEFINITION,
        extractor_hint="dbt_schema_yml",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"^dbt_project\.yml$",
        tag=FileTag.DOCS,
        extractor_hint=None,
        confidence=1.0,
    ),
]


# ── NestJS + TypeORM + Postgres ───────────────────────────────────────────────

NESTJS_TYPEORM_RULES: List[ClassificationRule] = [
    ClassificationRule(
        path_pattern=r".*migrations?/.*\.ts$",
        tag=FileTag.MIGRATION,
        extractor_hint="typeorm_migration",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.entity\.ts$",
        tag=FileTag.SCHEMA_DEFINITION,
        extractor_hint="typeorm_entity",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.dto\.ts$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="nestjs_dto",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.controller\.ts$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="nestjs_controller",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.repository\.ts$",
        tag=FileTag.DATA_ACCESS,
        extractor_hint="typeorm_repository",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.service\.ts$",
        tag=FileTag.DATA_ACCESS,
        extractor_hint="nestjs_service",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.module\.ts$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="nestjs_module",
        confidence=1.0,
    ),
]


# ── Next.js + Prisma + Postgres ───────────────────────────────────────────────

NEXTJS_PRISMA_RULES: List[ClassificationRule] = [
    ClassificationRule(
        path_pattern=r"prisma/schema\.prisma$",
        tag=FileTag.SCHEMA_DEFINITION,
        extractor_hint="prisma_schema",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"prisma/migrations/.*\.sql$",
        tag=FileTag.MIGRATION,
        extractor_hint="prisma_migration_sql",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"app/api/.*/route\.(ts|js)$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="nextjs_route_handler",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"pages/api/.*\.(ts|js)$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="nextjs_pages_api",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"app/.*/page\.(tsx|jsx)$",
        tag=FileTag.UI_COMPONENT,
        extractor_hint="react_page_component",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r".*\.(tsx|jsx)$",
        tag=FileTag.UI_COMPONENT,
        extractor_hint="react_component",
        confidence=0.7,   # broad catch-all, lower confidence
    ),
]


# ── React + Express + MongoDB ─────────────────────────────────────────────────

REACT_EXPRESS_MONGO_RULES: List[ClassificationRule] = [
    ClassificationRule(
        path_pattern=r"models/.*\.(js|ts)$",
        tag=FileTag.SCHEMA_DEFINITION,
        extractor_hint="mongoose_schema",
        confidence=0.85,   # mongoose models are schema + access combined;
                            # default to schema, extractor can re-tag if needed
    ),
    ClassificationRule(
        path_pattern=r"routes/.*\.(js|ts)$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="express_route",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"controllers/.*\.(js|ts)$",
        tag=FileTag.API_CONTRACT,
        extractor_hint="express_controller",
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"src/components/.*\.(jsx|tsx|js)$",
        tag=FileTag.UI_COMPONENT,
        extractor_hint="react_component",
        confidence=1.0,
    ),
]


# ── Cross-stack: infra / CI / docs (applied AFTER stack-specific rules) ──────
# These fire regardless of detected stack — infra files look the same
# whether the app is NestJS or Next.js.

UNIVERSAL_RULES: List[ClassificationRule] = [
    ClassificationRule(
        path_pattern=r"^Dockerfile",
        tag=FileTag.INFRA,
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"docker-compose\.ya?ml$",
        tag=FileTag.INFRA,
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"^terraform/.*\.tf$",
        tag=FileTag.INFRA,
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"^\.github/workflows/.*\.ya?ml$",
        tag=FileTag.CI_CONFIG,
        confidence=1.0,
    ),
    ClassificationRule(
        path_pattern=r"\.md$",
        tag=FileTag.DOCS,
        confidence=1.0,
    ),
    # Ambiguous .yml not caught by any stack-specific rule above —
    # content-sniff tiebreaker, mirrors _is_relevant_yml stage 3.
    ClassificationRule(
        path_pattern=r".*\.(yml|yaml)$",
        content_sniff=r"(version:|services:)",
        tag=FileTag.INFRA,
        confidence=0.6,
    ),
    ClassificationRule(
        path_pattern=r".*\.(yml|yaml)$",
        content_sniff=r"(models:|sources:|metrics:)",
        tag=FileTag.SCHEMA_DEFINITION,
        extractor_hint="dbt_schema_yml",
        confidence=0.6,
    ),
]


# ── Registry: StackProfile.frameworks/orm → rule set ─────────────────────────
# Consumed by registry/stack_registry.py — kept here so rules and the
# data describing which rules apply to which stack live in one file.

STACK_RULE_SETS = {
    "dbt": DBT_RULES,
    "nestjs+typeorm": NESTJS_TYPEORM_RULES,
    "nextjs+prisma": NEXTJS_PRISMA_RULES,
    "react+express+mongo": REACT_EXPRESS_MONGO_RULES,
}
