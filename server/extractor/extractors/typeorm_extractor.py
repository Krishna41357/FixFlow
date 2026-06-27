"""
typeorm_extractor.py — Identity extraction for TypeORM files (Phase 3).

Handles three pending_extractor hints from NESTJS_TYPEORM_RULES:

  typeorm_entity      (FileTag.SCHEMA_DEFINITION) → class decorated @Entity()
                                                     (category=DATA, subtype="typeorm_entity")
                                                     + relation decorators as USES references
                                                       (@OneToMany, @ManyToOne, @OneToOne,
                                                        @ManyToMany)
                                                     + IMPORT references

  typeorm_repository  (FileTag.DATA_ACCESS)        → class extending Repository<T> or
                                                     decorated @EntityRepository(T)
                                                     (category=BEHAVIOR, subtype="typeorm_repository")
                                                     + USES reference to the managed entity type
                                                     + IMPORT references

  typeorm_migration   (FileTag.MIGRATION)           → class implementing MigrationInterface
                                                     (category=DATA, subtype="typeorm_migration")
                                                     Migrations describe schema changes — they
                                                     ARE data-layer artifacts. No USES references
                                                     extracted (migrations execute raw SQL or
                                                     schema operations, not referencing typed
                                                     entities in a stable way).

DESIGN CHOICES:
  - Relation decorators (@OneToMany, @ManyToOne, etc.) produce USES references
    because they are the key cross-entity dependencies in the data layer.
    TypeORM's syntax is `@OneToMany(() => Order, order => order.user)` — the
    arrow-function first argument `() => Order` is what we extract as the
    target entity. This is intentional: TypeORM wraps it in a lambda to
    avoid circular import issues, but the semantic is "this entity USES Order."

  - Column decorators (@Column, @PrimaryGeneratedColumn, etc.) are NOT extracted
    as references — they describe field shape, not cross-entity dependencies.
    Column details can be added to raw_metadata in a later pass if needed.

  - @EntityRepository(Entity) is the legacy TypeORM custom repository decorator
    (pre-DataSource API). We still handle it since many NestJS codebases haven't
    migrated to the newer Repository injection pattern.

  - Migration class names tend to be timestamp-prefixed (e.g.,
    `1700000000000AddEmailToUsers`). The class name is extracted as-is —
    no name normalization, since the exact class name is what TypeORM uses
    internally to track which migrations have run.
"""

import re
from typing import List, Optional, Set

from extractor.models.classification import ClassifiedFile
from extractor.models.identity import (
    ExtractedIdentity,
    ExtractedReference,
    ExtractionResult,
    IdentityCategory,
    ReferenceType,
)

EXTRACTOR_ID = "typeorm"

# ── Class detection ───────────────────────────────────────────────────────────

_CLASS_NAME = re.compile(r'export\s+class\s+(\w+)')

# ── Entity decorator and relation patterns ────────────────────────────────────

_ENTITY_DECORATOR = re.compile(r'@Entity\s*\(')

# TypeORM relation syntax: @OneToMany(() => Order, order => order.user)
# We capture the first argument (the entity type) only.
_RELATION_DECORATORS = re.compile(
    r'@(OneToMany|ManyToOne|OneToOne|ManyToMany)\s*\(\s*\(\s*\)\s*=>\s*(\w+)'
)

# ── Repository patterns ───────────────────────────────────────────────────────

# extends Repository<UserEntity>  or  extends AbstractRepository<UserEntity>
_EXTENDS_REPOSITORY = re.compile(
    r'extends\s+(?:Abstract)?Repository\s*<\s*(\w+)\s*>'
)

# @EntityRepository(UserEntity)   (legacy decorator)
_ENTITY_REPOSITORY_DECORATOR = re.compile(
    r'@EntityRepository\s*\(\s*(\w+)\s*\)'
)

# ── Import module specifiers ──────────────────────────────────────────────────

_IMPORT_FROM = re.compile(r'\bfrom\s+["\']([^"\']+)["\']')


# ── Shared helpers ────────────────────────────────────────────────────────────

def _file_stem(file_path: str) -> str:
    name = file_path.rsplit("/", 1)[-1]
    for ext in (".ts", ".js"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _extract_class_name(content: str, file_path: str) -> str:
    m = _CLASS_NAME.search(content)
    return m.group(1) if m else _file_stem(file_path)


def _extract_imports(content: str, file_path: str) -> List[ExtractedReference]:
    seen: Set[str] = set()
    references: List[ExtractedReference] = []
    for m in _IMPORT_FROM.finditer(content):
        module = m.group(1)
        if module in seen:
            continue
        seen.add(module)
        line = content[: m.start()].count("\n") + 1
        references.append(ExtractedReference(
            source_file=file_path,
            source_identity=None,   # backfilled by caller
            reference_type=ReferenceType.IMPORT,
            target_expression=module,
            line=line,
            extractor_id=EXTRACTOR_ID,
        ))
    return references


# ── Per-hint extraction functions ─────────────────────────────────────────────

def _extract_entity(file_path: str, content: str) -> ExtractionResult:
    """
    Handles typeorm_entity. Entities are DATA — they describe the
    persisted shape of a domain concept. The relation decorators
    produce USES references: User @OneToMany Order means User USES Order.
    This is the core of the data-layer graph: which entities depend on
    which other entities.
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)

    relation_refs: List[ExtractedReference] = []
    seen_targets: Set[str] = set()
    relations_meta: List[dict] = []

    for m in _RELATION_DECORATORS.finditer(content):
        decorator = m.group(1)
        target_entity = m.group(2)
        line = content[: m.start()].count("\n") + 1

        relations_meta.append({
            "decorator": f"@{decorator}",
            "target": target_entity,
        })

        if target_entity not in seen_targets:
            seen_targets.add(target_entity)
            relation_refs.append(ExtractedReference(
                source_file=file_path,
                source_identity=name,
                reference_type=ReferenceType.USES,
                target_expression=target_entity,
                line=line,
                extractor_id=EXTRACTOR_ID,
            ))

    for ref in imports:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.DATA,
        subtype="typeorm_entity",
        raw_metadata={
            "relations": relations_meta,
        },
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports + relation_refs,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_repository(file_path: str, content: str) -> ExtractionResult:
    """
    Handles typeorm_repository. Repositories are BEHAVIOR — they
    implement data access patterns on top of entities. The managed
    entity type (from `extends Repository<EntityType>` or
    `@EntityRepository(EntityType)`) becomes a USES reference,
    because the repository is tightly coupled to that entity's shape.
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)
    uses: List[ExtractedReference] = []

    # Try extends Repository<T> first
    ext_match = _EXTENDS_REPOSITORY.search(content)
    if ext_match:
        entity_type = ext_match.group(1)
        line = content[: ext_match.start()].count("\n") + 1
        uses.append(ExtractedReference(
            source_file=file_path,
            source_identity=name,
            reference_type=ReferenceType.USES,
            target_expression=entity_type,
            line=line,
            extractor_id=EXTRACTOR_ID,
        ))

    # Also check legacy @EntityRepository(Entity) decorator
    dec_match = _ENTITY_REPOSITORY_DECORATOR.search(content)
    if dec_match:
        entity_type = dec_match.group(1)
        # Only add if not already captured from extends
        already = {r.target_expression for r in uses}
        if entity_type not in already:
            line = content[: dec_match.start()].count("\n") + 1
            uses.append(ExtractedReference(
                source_file=file_path,
                source_identity=name,
                reference_type=ReferenceType.USES,
                target_expression=entity_type,
                line=line,
                extractor_id=EXTRACTOR_ID,
            ))

    for ref in imports:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.BEHAVIOR,
        subtype="typeorm_repository",
        raw_metadata={
            "manages": [r.target_expression for r in uses],
        },
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports + uses,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_migration(file_path: str, content: str) -> ExtractionResult:
    """
    Handles typeorm_migration. Migrations are DATA — they are the record
    of schema changes over time. No USES references are extracted: a
    migration executes DDL/DML directly, not through typed entity
    references that would be meaningful graph edges.
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)
    for ref in imports:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.DATA,
        subtype="typeorm_migration",
        raw_metadata={},
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports,
        extractor_id=EXTRACTOR_ID,
    )


# ── Public entrypoint ─────────────────────────────────────────────────────────

def extract(file_path: str, content: str, classified_file: ClassifiedFile) -> ExtractionResult:
    """
    Dispatches on pending_extractor hint. Always returns ExtractionResult,
    never raises.
    """
    hint = classified_file.pending_extractor
    try:
        if hint == "typeorm_entity":
            return _extract_entity(file_path, content)
        elif hint == "typeorm_repository":
            return _extract_repository(file_path, content)
        elif hint == "typeorm_migration":
            return _extract_migration(file_path, content)
        else:
            return ExtractionResult(
                file_path=file_path,
                extractor_id=EXTRACTOR_ID,
                parse_errors=[f"typeorm_extractor does not handle hint: {hint!r}"],
            )
    except Exception as e:
        return ExtractionResult(
            file_path=file_path,
            extractor_id=EXTRACTOR_ID,
            parse_errors=[f"Unexpected error during extraction: {e}"],
        )
