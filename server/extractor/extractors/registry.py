"""
registry.py — Maps pending_extractor hint strings to extractor functions.

This is the ONLY file that needs to change when a new extractor is
added. Every extractor module exposes one function, extract(file_path,
content, classified_file) -> ExtractionResult, and registers itself
here under every pending_extractor string it handles.

Current coverage (Phase 3 complete):
  dbt      — dbt_sql_model, dbt_schema_yml
  react    — react_component, react_page_component,
             nextjs_route_handler, nextjs_pages_api
  nestjs   — nestjs_service, nestjs_controller, nestjs_dto, nestjs_module
  typeorm  — typeorm_entity, typeorm_repository, typeorm_migration

Stubs for next phases:
  express  — express_route, express_controller
  prisma   — prisma_schema, prisma_migration_sql
  mongoose — mongoose_schema
"""

from typing import Callable, Dict, Optional

from extractor.models.classification import ClassifiedFile
from extractor.models.identity import ExtractionResult
from extractor.extractors import dbt_extractor
from extractor.extractors import react_extractor
from extractor.extractors import nestjs_extractor
from extractor.extractors import typeorm_extractor

ExtractFn = Callable[[str, str, ClassifiedFile], ExtractionResult]

EXTRACTOR_REGISTRY: Dict[str, ExtractFn] = {
    # ── dbt ──────────────────────────────────────────────────────────────
    "dbt_sql_model":  dbt_extractor.extract,
    "dbt_schema_yml": dbt_extractor.extract,

    # ── React / Next.js ───────────────────────────────────────────────────
    "react_component":       react_extractor.extract,
    "react_page_component":  react_extractor.extract,
    "nextjs_route_handler":  react_extractor.extract,
    "nextjs_pages_api":      react_extractor.extract,

    # ── NestJS ────────────────────────────────────────────────────────────
    "nestjs_service":    nestjs_extractor.extract,
    "nestjs_controller": nestjs_extractor.extract,
    "nestjs_dto":        nestjs_extractor.extract,
    "nestjs_module":     nestjs_extractor.extract,

    # ── TypeORM ───────────────────────────────────────────────────────────
    "typeorm_entity":     typeorm_extractor.extract,
    "typeorm_repository": typeorm_extractor.extract,
    "typeorm_migration":  typeorm_extractor.extract,

    # ── Future stubs (not yet implemented) ───────────────────────────────
    # "express_route":       express_extractor.extract,
    # "express_controller":  express_extractor.extract,
    # "prisma_schema":       prisma_extractor.extract,
    # "prisma_migration_sql":prisma_extractor.extract,
    # "mongoose_schema":     mongoose_extractor.extract,
}


def get_extractor(pending_extractor_hint: str) -> Optional[ExtractFn]:
    """
    Looks up the extractor function for a given hint. Returns None if
    no extractor handles this hint yet — callers should treat that as
    "skip this file, not an error" rather than crashing, since plenty
    of classified files (INFRA, CI_CONFIG, DOCS, UNKNOWN) correctly
    have no pending_extractor at all, and some EXTRACTABLE_TAGS hints
    may not have an extractor implemented yet during incremental rollout.
    """
    return EXTRACTOR_REGISTRY.get(pending_extractor_hint)

