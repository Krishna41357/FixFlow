"""
classification.py — Core classification schemas for the extractor module.

This is the contract every downstream piece (rule engine, identity
extractors, edge discoverers, graph builders) depends on. Getting this
shape right matters more than any individual rule — every stack-specific
plugin written later must produce output conforming to ClassifiedFile.

Mirrors the dataclass/Pydantic conventions already used in
server/models/lineage.py and server/controllers/repo_parser_controller.py
so this module feels native to the existing codebase, not bolted on.

Schema organisation:
  1. FileTag           — the universal category every file gets bucketed into
  2. StackProfile       — what language/framework/orm/database a repo uses
  3. ClassifiedFile     — one file + its tag + how confident we are + why
  4. RepoClassification — the full result for one repo scan
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, computed_field


# ── 1. File tags — universal across every stack ─────────────────────────────

class FileTag(str, Enum):
    """
    The category a file plays in the system, independent of which
    stack/framework/language it belongs to.

    These tags are deliberately stack-agnostic — a Prisma schema.prisma
    and a TypeORM *.entity.ts both resolve to SCHEMA_DEFINITION. The rule
    that maps a given path/content to a tag is stack-specific (see
    stack_rules.py); the tag itself is not.
    """
    SCHEMA_DEFINITION = "schema_definition"   # entities, ORM models, dbt schema yml, prisma schema
    MIGRATION = "migration"                    # explicit migration files (TypeORM, Prisma, raw SQL)
    DATA_ACCESS = "data_access"                # repositories, dbt SQL models, query builders
    API_CONTRACT = "api_contract"               # controllers, DTOs, route handlers, GraphQL resolvers
    UI_COMPONENT = "ui_component"               # React/Vue components, pages
    INFRA = "infra"                             # Dockerfiles, docker-compose, terraform, k8s manifests
    CI_CONFIG = "ci_config"                     # GitHub Actions, other CI pipeline configs
    DOCS = "docs"                                # README, markdown docs, comments-only files
    UNKNOWN = "unknown"                          # could not confidently classify


# Tags that the identity-extraction stage (built later) will eventually
# process. Anything not in this set is recorded but never queued for
# extraction — kept here so the rule engine and extractor controller
# agree on what "actionable" means without duplicating the list.
EXTRACTABLE_TAGS = {
    FileTag.SCHEMA_DEFINITION,
    FileTag.MIGRATION,
    FileTag.DATA_ACCESS,
    FileTag.API_CONTRACT,
}


# ── 2. Stack profile — detected once per repo, cached ────────────────────────

class StackProfile(BaseModel):
    """
    Describes what a repo is built with. Detected once at onboarding
    (or on-demand if missing), cached, and used to select which rule
    set and which future identity-extractor plugin to run.

    detected_from is kept for the same reason repo_parser_controller.py
    logs DEBUG/WARNING at every parsing step — traceability. If a stack
    is misdetected, we need to know which signal caused it.
    """
    language: Optional[str] = Field(
        None, description="Primary language: typescript | python | go | etc."
    )
    frameworks: List[str] = Field(
        default_factory=list,
        description="Detected frameworks, e.g. ['nestjs'], ['nextjs', 'express'], ['dbt']"
    )
    orm: Optional[str] = Field(
        None, description="typeorm | prisma | mongoose | sequelize | dbt | none"
    )
    database: Optional[str] = Field(
        None, description="postgres | mysql | mongodb | sqlite | unknown"
    )
    detected_from: List[str] = Field(
        default_factory=list,
        description="Which signal files/fields produced this profile, e.g. "
                     "['package.json:dependencies.@nestjs/core', 'package.json:dependencies.typeorm']"
    )

    @computed_field
    @property
    def is_recognized(self) -> bool:
        """False if detection found nothing usable — caller should fall back
        to UNKNOWN-tagging everything rather than guessing."""
        return bool(self.language or self.frameworks or self.orm)


# ── 3. Classified file — one file, tagged ────────────────────────────────────

class ClassifiedFile(BaseModel):
    """
    One file from the repo tree, after classification.

    pending_extractor is intentionally present now, populated later —
    this is the stub point where Stage 3 (identity extraction) plugs in.
    It stays None until that stage exists; the field exists so the
    contract doesn't need to change shape when that stage is built.
    """
    path: str
    tag: FileTag
    confidence: float = Field(
        1.0, ge=0.0, le=1.0,
        description="1.0 = matched an unambiguous path rule. Lower when "
                     "resolved via content-sniff tiebreaker."
    )
    matched_rule: Optional[str] = Field(
        None, description="Which rule fired, for debugging — e.g. "
                           "'path_prefix:src/.*\\.entity\\.ts$' or "
                           "'content_sniff:@Entity'"
    )
    pending_extractor: Optional[str] = Field(
        None,
        description="Name of the identity-extractor plugin that SHOULD run "
                     "on this file once Stage 3 exists, e.g. 'typeorm_entity'. "
                     "None means no extractor decision has been made yet "
                     "(current stage) or this tag has no extractor (e.g. DOCS)."
    )
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


# ── 4. Full repo classification result ───────────────────────────────────────

class RepoClassification(BaseModel):
    """
    Result of classifying every file in one repo (or one PR's changed
    files — same shape is reused for both full scans and incremental
    PR-time classification later).
    """
    repo_full_name: str
    stack_profile: StackProfile
    files: List[ClassifiedFile] = Field(default_factory=list)
    total_files_scanned: int = 0
    skipped_count: int = 0

    @computed_field
    @property
    def tag_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for f in self.files:
            counts[f.tag.value] = counts.get(f.tag.value, 0) + 1
        return counts

    @computed_field
    @property
    def extractable_files(self) -> List[ClassifiedFile]:
        """Files that will eventually feed Stage 3 (identity extraction)."""
        return [f for f in self.files if f.tag in EXTRACTABLE_TAGS]
