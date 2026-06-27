"""
dbt_extractor.py — First concrete extractor, proving the universal
ExtractedIdentity/ExtractedReference contract (Stage 3, Phase 2).

dbt was chosen as the first extractor not because it's FixFlow's
product priority — the roadmap explicitly puts React/Next.js/NestJS
ahead of it in long-term value — but because dbt's ref()/source()
syntax is already well-understood (repo_parser_controller.py has a
working, battle-tested version of this exact parsing logic), making
it the fastest way to validate that the NEW universal contract is
shaped correctly before investing in harder, larger extractors.

Handles two pending_extractor hints from stack_rules.py's DBT_RULES:

  dbt_sql_model   (FileTag.DATA_ACCESS)       -> one ExtractedIdentity
                                                  (category=BEHAVIOR,
                                                   subtype="dbt_model")
                                                  + one ExtractedReference
                                                  per ref()/source() call
  dbt_schema_yml  (FileTag.SCHEMA_DEFINITION) -> one ExtractedIdentity
                                                  (category=DATA,
                                                   subtype="dbt_source")
                                                  per model/source listed

Every extractor function in this module follows the same interface:
    extract(file_path: str, content: str, classified_file: ClassifiedFile) -> ExtractionResult
This is the interface every future extractor (typeorm, react, nestjs,
express, ...) must also implement — enforced by ExtractionResult's
shape, not by a base class, per the design decision to keep this
lightweight until a second/third extractor proves whether a shared
base class would actually pull its weight.
"""

import re
from typing import List, Optional

from extractor.models.classification import ClassifiedFile
from extractor.models.identity import (
    ExtractedIdentity,
    ExtractedReference,
    ExtractionResult,
    IdentityCategory,
    ReferenceType,
)

EXTRACTOR_ID = "dbt"


def _model_name_from_path(file_path: str) -> str:
    """
    Derives a model name from a dbt SQL file path — just the filename
    stem, no directory prefix stripping needed here (that FQN-style
    stripping already happens elsewhere; this extractor only needs a
    human-readable name for the identity, not a fully qualified one).

    models/finance/revenue.sql -> "revenue"
    seeds/raw/users.sql        -> "users"
    """
    stem = file_path.rsplit("/", 1)[-1]
    for suffix in (".sql", ".yml", ".yaml"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _extract_ref_and_source_calls(sql_content: str) -> List[ExtractedReference]:
    """
    Extracts ref() and source() calls from dbt SQL content as
    unresolved references. Mirrors the regex logic already proven in
    repo_parser_controller.py's _parse_ref_and_source_calls, adapted
    to produce ExtractedReference instead of a plain string list —
    same parsing, new output shape.

    target_expression intentionally stays as the raw model/source name
    dbt itself uses ("orders", "raw.users") — NOT a resolved file path
    or graph node id. Resolving that is a later stage's job.
    """
    references: List[ExtractedReference] = []

    ref_pattern = re.compile(
        r'\{\{-?\s*ref\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*-?\}\}',
        re.IGNORECASE,
    )
    source_pattern = re.compile(
        r'\{\{-?\s*source\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]\s*\)\s*-?\}\}',
        re.IGNORECASE,
    )

    for match in ref_pattern.finditer(sql_content):
        name = match.group(1).strip()
        if name:
            line = sql_content[: match.start()].count("\n") + 1
            references.append(ExtractedReference(
                source_file="",   # filled in by caller, which knows the path
                source_identity=None,   # filled in by caller, which knows the identity name
                reference_type=ReferenceType.USES,
                target_expression=name,
                line=line,
                extractor_id=EXTRACTOR_ID,
            ))

    for match in source_pattern.finditer(sql_content):
        schema = match.group(1).strip()
        table = match.group(2).strip()
        if schema and table:
            line = sql_content[: match.start()].count("\n") + 1
            references.append(ExtractedReference(
                source_file="",
                source_identity=None,
                reference_type=ReferenceType.USES,
                target_expression=f"{schema}.{table}",
                line=line,
                extractor_id=EXTRACTOR_ID,
            ))

    return references


def _extract_sql_model(file_path: str, content: str) -> ExtractionResult:
    """
    Handles the dbt_sql_model hint — one .sql file under models/seeds/snapshots.

    Produces exactly one ExtractedIdentity for the model itself
    (category=BEHAVIOR — a dbt SQL model transforms data, it doesn't
    just describe its shape, which is why it isn't DATA), plus one
    ExtractedReference per ref()/source() call found in the file.
    """
    model_name = _model_name_from_path(file_path)
    parse_errors: List[str] = []

    try:
        references = _extract_ref_and_source_calls(content)
    except Exception as e:
        references = []
        parse_errors.append(f"Failed to parse ref()/source() calls: {e}")

    # Backfill source_file/source_identity now that we know them —
    # the helper above doesn't know the file path or model name.
    for ref in references:
        ref.source_file = file_path
        ref.source_identity = model_name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=model_name,
        category=IdentityCategory.BEHAVIOR,
        subtype="dbt_model",
        raw_metadata={
            "ref_count": len(references),
            "referenced_models": [r.target_expression for r in references],
        },
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=references,
        extractor_id=EXTRACTOR_ID,
        parse_errors=parse_errors,
    )


def _parse_yml_model_names(yml_content: str) -> List[str]:
    """
    Extracts top-level model/source names from a dbt schema yml —
    every `- name: <x>` entry directly under a `models:` or `sources:`
    block (not nested column-level `- name:` entries).

    Distinguishing a model-level "- name:" from a column-level
    "- name:" can't be done by checking "does this line start with a
    dash" alone — both do. The actual signal is INDENT: once we've
    seen a `columns:` key at some indent, any "- name:" line indented
    MORE than that `columns:` key belongs to the columns list. A
    "- name:" line at the SAME OR LESSER indent than the model-level
    list (i.e. back at the original model indent) means we've returned
    to the next model entry, not a column.

    Tracked via model_list_indent — the indent of the very first
    "- name:" we see after a models:/sources: key. Every subsequent
    line at that exact indent is a new model; anything indented
    deeper (columns, nested fields) is not.

    KNOWN LIMITATION: dbt's `sources:` block has a different nesting
    shape than `models:` — a source declares a source NAME, and the
    actual tables live nested under a `tables:` key inside it:

        sources:
          - name: raw          <- this is the SOURCE name, not a table
            tables:
              - name: users    <- these are the actual queryable tables
              - name: orders

    This function currently only picks up the outer source name
    ("raw") and misses the nested table names ("users", "orders").
    For `models:` blocks (the common case, and what this phase's
    test fixtures cover) this is not an issue, since models have no
    such nesting. Handling `sources:` correctly needs a second pass
    that recognizes `tables:` the same way it already recognizes
    `columns:` — deferred rather than rushed here, since
    repo_parser_controller.py's _parse_yml_columns already solves an
    adjacent version of this problem and is the right reference to
    follow when this gets addressed properly.
    """
    names: List[str] = []
    lines = yml_content.splitlines()
    model_list_indent: Optional[int] = None

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        name_match = re.match(r'^-\s+name:\s+(\S+)', stripped)
        if not name_match:
            continue

        if model_list_indent is None:
            # First "- name:" we've encountered — this defines the
            # model-list indent level for the rest of the file.
            model_list_indent = indent
            names.append(name_match.group(1))
        elif indent == model_list_indent:
            # Same indent as the first model entry — another model,
            # not a column nested under the previous one.
            names.append(name_match.group(1))
        # else: indent > model_list_indent — this is a column entry
        # (or other nested "- name:") belonging to the current model,
        # not a new model itself. Skip it.

    return names


def _extract_schema_yml(file_path: str, content: str) -> ExtractionResult:
    """
    Handles the dbt_schema_yml hint — one .yml/.yaml file defining
    models/sources under models/seeds/snapshots/analyses.

    Produces one ExtractedIdentity per model/source name found
    (category=DATA — a schema yml describes shape, it doesn't
    transform anything, which is the BEHAVIOR/DATA distinction this
    extractor exists to validate). No references are extracted from
    schema ymls in this phase — dbt schema files declare structure,
    they don't reference other models the way SQL files do via ref().
    """
    parse_errors: List[str] = []

    try:
        model_names = _parse_yml_model_names(content)
    except Exception as e:
        model_names = []
        parse_errors.append(f"Failed to parse model/source names from yml: {e}")

    if not model_names and not parse_errors:
        parse_errors.append("No model/source names found — yml may be empty or use an unexpected structure.")

    identities = [
        ExtractedIdentity(
            file_path=file_path,
            name=name,
            category=IdentityCategory.DATA,
            subtype="dbt_source",
            raw_metadata={},
            extractor_id=EXTRACTOR_ID,
        )
        for name in model_names
    ]

    return ExtractionResult(
        file_path=file_path,
        identities=identities,
        references=[],
        extractor_id=EXTRACTOR_ID,
        parse_errors=parse_errors,
    )


def extract(file_path: str, content: str, classified_file: ClassifiedFile) -> ExtractionResult:
    """
    Public entrypoint — dispatches on classified_file.pending_extractor.

    Always returns an ExtractionResult, never raises — even completely
    malformed content should come back as a result with parse_errors
    populated and empty identities/references, per the contract's
    "found nothing is valid, but always return something" principle.
    """
    hint = classified_file.pending_extractor

    try:
        if hint == "dbt_sql_model":
            return _extract_sql_model(file_path, content)
        elif hint == "dbt_schema_yml":
            return _extract_schema_yml(file_path, content)
        else:
            return ExtractionResult(
                file_path=file_path,
                extractor_id=EXTRACTOR_ID,
                parse_errors=[f"dbt_extractor does not handle pending_extractor hint: {hint!r}"],
            )
    except Exception as e:
        # Last-resort catch — an extractor must never raise and crash
        # the caller's loop over many files. One bad file should not
        # take down extraction for the rest of the repo.
        return ExtractionResult(
            file_path=file_path,
            extractor_id=EXTRACTOR_ID,
            parse_errors=[f"Unexpected error during extraction: {e}"],
        )
