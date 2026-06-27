"""
identity.py — Universal identity and reference contracts (Stage 3, Phase 1).

Every stack-specific extractor (dbt, TypeORM, Prisma, React, NestJS,
Express, Mongoose, Django, ...) must produce output conforming to
ExtractedIdentity and ExtractedReference — nothing else. No
extractor-specific subclassing, no per-stack fields bolted onto these
models directly; stack-specific detail lives in raw_metadata.

Design principle: contracts are defined around CONCEPTS, not
TECHNOLOGIES.

  Bad:  EntityNode { tableName: str, columns: List[Column] }
        — assumes every project has database entities. Breaks the
          moment you hit a pure React frontend or a stateless NestJS
          service with no persistence at all.

  Good: ExtractedIdentity { name, category, subtype, raw_metadata }
        — a TypeORM entity, a Prisma model, a dbt model, a React
          component, and a NestJS service all fit through the same
          shape. Whatever is specific to one technology lives in
          raw_metadata, which downstream consumers read selectively —
          it never forces the shape of the contract itself.

Two-level category system:
  - `category` is a small, closed enum shared across every extractor.
    This is what lets a cross-layer query like "show me everything in
    the API layer" work without knowing which framework produced each
    identity.
  - `subtype` is a free-form string each extractor defines for itself
    (e.g. "typeorm_entity", "react_component", "express_route"). This
    is where stack-specific vocabulary lives without polluting the
    shared enum.

API is kept as its OWN top-level category, distinct from BEHAVIOR.
A NestJS @Injectable() service and a NestJS @Controller() route handler
are conceptually different things for FixFlow's purposes: the controller
is a reachable-from-outside entry point (its breakage is a public
contract break), the service is internal (its breakage is implementation
detail). Collapsing both into BEHAVIOR would lose that distinction,
which directly weakens the core value chain this project is built
around: PR changes service -> affected API -> affected page -> user
impact. That chain has three distinct hops; API needs its own slot
for the chain to be traceable by category alone.

References are deliberately a SEPARATE model from identities, and are
initially UNRESOLVED — an extractor records what it sees written in
the source ("this file imports something called useAuth") without
trying to resolve that to a concrete target identity. Resolution
(turning "useAuth" into a concrete node id) is a later stage's job,
because resolution often needs cross-file/cross-module knowledge a
single-file extractor doesn't have.

source_identity on ExtractedReference is Optional — an extractor is
allowed to say "I found a reference in this file" without yet knowing
which specific identity in that file it belongs to. This tolerates
early/simple extractors that haven't fully parsed identity boundaries
before scanning for references.
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, computed_field


# ── 1. Universal category — the only closed vocabulary in this system ──────

class IdentityCategory(str, Enum):
    """
    The five buckets every extracted identity falls into, regardless of
    stack. This is intentionally small and stable — it should almost
    never need a new member. New technologies get new SUBTYPES, not
    new categories.
    """
    DATA = "data"          # entities, schema models, tables, dbt sources/models,
                            # migrations — anything describing persisted shape
    BEHAVIOR = "behavior"   # services, hooks, business logic, use-cases — things
                            # that DO something but aren't a reachable entry point
                            # or a UI element themselves
    UI = "ui"               # components, pages, views, layouts
    API = "api"             # controllers, route handlers, resolvers — anything
                            # that is an entry point reachable from outside the
                            # process (HTTP, RPC, GraphQL)
    INFRA = "infra"         # Docker services, CI jobs, deployment/env config


# ── 2. Reference type — also a closed vocabulary, kept small ────────────────

class ReferenceType(str, Enum):
    """
    How one identity points at another, before resolution. Kept generic
    on purpose. USES is the deliberately broad bucket for ORM-style
    relations (TypeORM @OneToMany/@ManyToOne, dbt ref()/source(), raw
    SQL FK/FROM/JOIN) — these all carry similar breakage semantics
    (something downstream depends on something upstream existing in a
    particular shape), so they share one type rather than being split
    into HAS_MANY/BELONGS_TO/REFERENCES_TABLE individually. If a future
    extractor needs to distinguish them, that distinction can live in
    metadata (e.g. {"relation_kind": "one_to_many"}) without growing
    this enum.
    """
    IMPORT = "import"           # import/require of another module or symbol
    EXTENDS = "extends"         # class inheritance
    IMPLEMENTS = "implements"   # interface implementation
    USES = "uses"                # ORM relations, dbt ref()/source(), FK references,
                                  # dependency injection — "this depends on that existing"
    CALLS = "calls"               # function/method invocation
    RENDERS = "renders"           # JSX/template component usage inside another component
    UNKNOWN = "unknown"           # extractor saw a reference but couldn't categorize it


# ── 3. Extracted identity — the universal node shape ─────────────────────────

class ExtractedIdentity(BaseModel):
    """
    One identity extracted from one file. This is deliberately thin —
    raw_metadata is where every stack-specific detail lives (column
    lists, HTTP verbs, prop types, decorator arguments, whatever). The
    shape of THIS model never changes when a new extractor is added;
    only the contents of raw_metadata differ.

    No separate `id` field — (file_path, name) is the de-facto key at
    extraction time. A later resolver/graph-build stage is responsible
    for assigning a true graph-wide unique id; forcing extractors to
    invent one themselves at this stage adds complexity without benefit,
    since most extractors only see one file at a time anyway.
    """
    file_path: str = Field(..., description="Source file this identity was extracted from.")
    name: str = Field(..., description="Human-readable name — class name, component name, route path, etc.")
    category: IdentityCategory
    subtype: str = Field(
        ..., description="Free-form, extractor-defined subtype, e.g. "
                          "'typeorm_entity', 'react_component', 'dbt_model', "
                          "'nestjs_service', 'express_route'."
    )
    line_start: Optional[int] = Field(None, description="Line where this identity begins, if known.")
    line_end: Optional[int] = Field(None, description="Line where this identity ends. None for whole-file identities.")
    raw_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Everything stack-specific lives here: column lists, "
                     "HTTP methods, prop types, decorator args, etc. "
                     "Downstream consumers read keys they know how to "
                     "interpret and ignore the rest."
    )
    extractor_id: str = Field(..., description="Which extractor produced this, e.g. 'dbt_sql_model', 'typeorm_entity'.")


# ── 4. Extracted reference — unresolved edge ─────────────────────────────────

class ExtractedReference(BaseModel):
    """
    One reference FROM a file (and optionally a specific identity
    within it) TO something else, as literally written in the source —
    unresolved.

    target_expression is the raw text the extractor saw ("useAuth",
    "User", "./users.service") — NOT a resolved identity id. A later
    resolver stage (not built yet) is responsible for turning
    target_expression into a concrete target identity, because that
    resolution often requires knowledge the single-file extractor
    doesn't have at extraction time (e.g. which file actually exports
    "useAuth", which may require following import paths or barrel
    exports across the repo).
    """
    source_file: str = Field(..., description="File this reference appears in.")
    source_identity: Optional[str] = Field(
        None, description="Name of the identity making the reference, if known. "
                           "None is valid — some extractors detect a reference "
                           "before/without resolving which identity it belongs to."
    )
    reference_type: ReferenceType
    target_expression: str = Field(
        ..., description="Raw, unresolved text naming the target, exactly as "
                          "written in source — e.g. 'useAuth', './users.service', "
                          "'orders' (from dbt ref('orders'))."
    )
    line: Optional[int] = Field(None, description="Line number this reference appears on, if known.")
    extractor_id: str = Field(..., description="Which extractor produced this reference.")


# ── 5. Full extraction result for one file ───────────────────────────────────

class ExtractionResult(BaseModel):
    """
    What one extractor plugin returns after processing one file.

    Deliberately allows ZERO identities/references — this is the
    "TypeORM plugin finds 0 entities in a Prisma project" case from
    the design discussion. An empty result is a valid, expected
    outcome, not an error. parse_errors is for genuine parse failures
    (malformed syntax the extractor couldn't even attempt to read),
    kept separate from "found nothing" so the two cases aren't
    conflated upstream — an extractor should always return a result,
    never raise, even on bad input.
    """
    file_path: str
    identities: List[ExtractedIdentity] = Field(default_factory=list)
    references: List[ExtractedReference] = Field(default_factory=list)
    extractor_id: str
    parse_errors: List[str] = Field(default_factory=list)

    @computed_field
    @property
    def identity_count(self) -> int:
        return len(self.identities)

    @computed_field
    @property
    def had_errors(self) -> bool:
        return len(self.parse_errors) > 0
