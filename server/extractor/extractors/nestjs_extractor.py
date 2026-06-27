"""
nestjs_extractor.py — Identity extraction for NestJS files (Phase 3).

Handles five pending_extractor hints from NESTJS_TYPEORM_RULES:

  nestjs_service     (FileTag.DATA_ACCESS)    → class decorated @Injectable()
                                                (category=BEHAVIOR, subtype="nestjs_service")
                                                + constructor DI params as USES references
                                                + IMPORT references

  nestjs_controller  (FileTag.API_CONTRACT)   → class decorated @Controller(...)
                                                (category=API, subtype="nestjs_controller")
                                                + route prefix extracted from @Controller arg
                                                + HTTP method decorators in raw_metadata
                                                + constructor DI params as USES references
                                                + IMPORT references

  nestjs_dto         (FileTag.API_CONTRACT)   → plain TypeScript class (no decorator required)
                                                (category=API, subtype="nestjs_dto")
                                                Represents a validated request/response shape —
                                                API because a DTO IS the API contract, not
                                                internal behavior

  nestjs_module      (FileTag.API_CONTRACT)   → class decorated @Module(...)
                                                (category=INFRA, subtype="nestjs_module")
                                                Modules are wiring/config, not behavior or API.
                                                INFRA is the honest category for "this file
                                                tells NestJS how to assemble things."

DESIGN CHOICES:
  - Constructor DI injection is the key reference extraction target for
    NestJS. `constructor(private readonly usersService: UsersService)`
    means UsersController USES UsersService — this is exactly the service
    → API dependency the graph needs. These become USES references.

  - @InjectRepository(Entity) is also captured as a USES reference to the
    repository class name. The `Repository<Entity>` generic type is NOT
    chased — only the concrete class in @InjectRepository matters for the
    graph since that's what the extractor can see without type resolution.

  - HTTP method routes (@Get, @Post, etc.) on controller methods are
    collected into raw_metadata["routes"] as a list of
    {"method": "GET", "path": "/users"} — they don't each become
    separate identities (the controller IS the API surface; its individual
    methods are sub-details, not separate nodes in the graph at this stage).

  - Module imports/providers/exports arrays are collected into raw_metadata
    but NOT chased into USES references yet — those arrays contain string
    class names that require cross-file resolution, which is the resolver
    stage's job, not the extractor's.
"""

import re
from typing import List, Optional, Set, Dict, Any

from extractor.models.classification import ClassifiedFile
from extractor.models.identity import (
    ExtractedIdentity,
    ExtractedReference,
    ExtractionResult,
    IdentityCategory,
    ReferenceType,
)

EXTRACTOR_ID = "nestjs"

# ── Class and decorator detection ────────────────────────────────────────────

# export class UsersController {
_CLASS_NAME = re.compile(r'export\s+class\s+(\w+)')

# @Controller('users')  or  @Controller("api/users")  or  @Controller()
_CONTROLLER_PREFIX = re.compile(
    r'@Controller\s*\(\s*["\']([^"\']*)["\']'
)
_CONTROLLER_EMPTY = re.compile(r'@Controller\s*\(\s*\)')

# @Get('/path')  @Post()  @Put(':id')  etc.
_HTTP_ROUTE_DECORATOR = re.compile(
    r'@(Get|Post|Put|Delete|Patch|Head|Options)\s*\(\s*(?:["\']([^"\']*)["\'])?\s*\)',
    re.IGNORECASE,
)

# ── Constructor dependency injection ─────────────────────────────────────────

# Finds the START of the constructor parameter list.
# We then use a balanced-paren scanner (see _get_constructor_body) to
# find the matching close-paren, avoiding the naive [^)]* approach which
# stops at the first ) it encounters — wrong for @InjectRepository(User).
_CONSTRUCTOR_START = re.compile(r'\bconstructor\s*\(')


def _get_constructor_body(content: str) -> Optional[str]:
    """
    Extracts the text between the outer parentheses of a constructor
    declaration, handling nested parens (e.g. @InjectRepository(User)).

    Returns None if no constructor is found.
    """
    m = _CONSTRUCTOR_START.search(content)
    if not m:
        return None

    start = m.end()   # position just after the opening '('
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == '(':
            depth += 1
        elif content[i] == ')':
            depth -= 1
        i += 1

    if depth != 0:
        return None   # unbalanced — malformed source
    return content[start: i - 1]   # strip the final closing ')'

# private readonly usersService: UsersService
# private usersRepo: UsersRepository
# protected authService: AuthService
_DI_PARAM = re.compile(
    r'(?:private|protected|public)(?:\s+readonly)?\s+\w+\s*:\s*([A-Z]\w*)'
)

# @InjectRepository(UserEntity) → USES UserEntity
_INJECT_REPOSITORY = re.compile(
    r'@InjectRepository\s*\(\s*(\w+)\s*\)'
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


def _extract_constructor_uses(content: str, file_path: str) -> List[ExtractedReference]:
    """
    Extracts USES references from constructor DI parameter types.

    Two sources:
      1. typed params: `private readonly usersService: UsersService`
      2. @InjectRepository(EntityClass) decorator

    Both produce USES references because both mean "this class depends
    on that class existing and being injectable."
    """
    references: List[ExtractedReference] = []
    seen: Set[str] = set()

    ctor_body = _get_constructor_body(content)
    if not ctor_body:
        return references

    ctor_start_line = content[: _CONSTRUCTOR_START.search(content).start()].count("\n") + 1

    # DI params by type annotation
    for m in _DI_PARAM.finditer(ctor_body):
        dep_type = m.group(1)
        if dep_type in seen:
            continue
        seen.add(dep_type)
        references.append(ExtractedReference(
            source_file=file_path,
            source_identity=None,   # backfilled by caller
            reference_type=ReferenceType.USES,
            target_expression=dep_type,
            line=ctor_start_line,
            extractor_id=EXTRACTOR_ID,
        ))

    # @InjectRepository decorators
    for m in _INJECT_REPOSITORY.finditer(ctor_body):
        entity = m.group(1)
        if entity in seen:
            continue
        seen.add(entity)
        references.append(ExtractedReference(
            source_file=file_path,
            source_identity=None,
            reference_type=ReferenceType.USES,
            target_expression=entity,
            line=ctor_start_line,
            extractor_id=EXTRACTOR_ID,
        ))

    return references


# ── Per-hint extraction functions ─────────────────────────────────────────────

def _extract_service(file_path: str, content: str) -> ExtractionResult:
    """
    Handles nestjs_service. Services are BEHAVIOR — they implement
    business logic called by controllers, other services, or guards.
    They're not directly reachable from outside the process (that's
    what controllers are for), hence BEHAVIOR not API.
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)
    uses = _extract_constructor_uses(content, file_path)
    refs = imports + uses
    for ref in refs:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.BEHAVIOR,
        subtype="nestjs_service",
        raw_metadata={
            "injected_deps": [r.target_expression for r in uses],
        },
        extractor_id=EXTRACTOR_ID,
    )
    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=refs,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_controller(file_path: str, content: str) -> ExtractionResult:
    """
    Handles nestjs_controller. Controllers are API — they ARE the
    reachable entry points. The route prefix and HTTP method decorators
    go into raw_metadata for use by the graph builder.
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)
    uses = _extract_constructor_uses(content, file_path)
    refs = imports + uses
    for ref in refs:
        ref.source_identity = name

    # Route prefix from @Controller('prefix')
    prefix_match = _CONTROLLER_PREFIX.search(content)
    route_prefix = prefix_match.group(1) if prefix_match else ""

    # Collect HTTP method decorator entries
    routes: List[Dict[str, Any]] = []
    for m in _HTTP_ROUTE_DECORATOR.finditer(content):
        routes.append({
            "method": m.group(1).upper(),
            "path": m.group(2) or "",
        })

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.API,
        subtype="nestjs_controller",
        raw_metadata={
            "route_prefix": route_prefix,
            "routes": routes,
            "injected_deps": [r.target_expression for r in uses],
        },
        extractor_id=EXTRACTOR_ID,
    )
    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=refs,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_dto(file_path: str, content: str) -> ExtractionResult:
    """
    Handles nestjs_dto. DTOs are the shape of data crossing the API
    boundary — they ARE the API contract, hence category=API.
    No DI extraction needed (DTOs are plain data classes, not injected).
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)
    for ref in imports:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.API,
        subtype="nestjs_dto",
        raw_metadata={},
        extractor_id=EXTRACTOR_ID,
    )
    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_module(file_path: str, content: str) -> ExtractionResult:
    """
    Handles nestjs_module. Modules are wiring/composition — they tell
    NestJS how to assemble providers and controllers. INFRA is the
    honest category: they're not API surfaces, not behavior, not data;
    they're config that makes the other things work.
    """
    name = _extract_class_name(content, file_path)
    imports = _extract_imports(content, file_path)
    for ref in imports:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.INFRA,
        subtype="nestjs_module",
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
        if hint == "nestjs_service":
            return _extract_service(file_path, content)
        elif hint == "nestjs_controller":
            return _extract_controller(file_path, content)
        elif hint == "nestjs_dto":
            return _extract_dto(file_path, content)
        elif hint == "nestjs_module":
            return _extract_module(file_path, content)
        else:
            return ExtractionResult(
                file_path=file_path,
                extractor_id=EXTRACTOR_ID,
                parse_errors=[f"nestjs_extractor does not handle hint: {hint!r}"],
            )
    except Exception as e:
        return ExtractionResult(
            file_path=file_path,
            extractor_id=EXTRACTOR_ID,
            parse_errors=[f"Unexpected error during extraction: {e}"],
        )
