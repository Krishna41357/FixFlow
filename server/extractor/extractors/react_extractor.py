"""
react_extractor.py — Identity extraction for React/Next.js files (Phase 3).

Handles four pending_extractor hints from stack_rules.py:

  react_component       (FileTag.UI_COMPONENT)   → one ExtractedIdentity
                                                    (category=UI, subtype="react_component")
                                                    + IMPORT refs per import statement
                                                    + RENDERS refs per JSX component usage

  react_page_component  (FileTag.UI_COMPONENT)   → same shape as react_component;
                                                    subtype still "react_component" because the
                                                    distinction between a page and a component is
                                                    navigational (it's a page BECAUSE of its file
                                                    path, not because it's a different kind of UI
                                                    node — the classifier already tagged it, so the
                                                    extractor doesn't need to re-tag it differently)

  nextjs_route_handler  (FileTag.API_CONTRACT)   → one ExtractedIdentity per exported HTTP method
                                                    (GET, POST, PUT, DELETE, PATCH, …)
                                                    (category=API, subtype="nextjs_route_handler")
                                                    Named per HTTP verb because an App Router
                                                    route.ts can export multiple independent
                                                    handlers — they're separate API surfaces.

  nextjs_pages_api      (FileTag.API_CONTRACT)   → one ExtractedIdentity for the whole file
                                                    (category=API, subtype="nextjs_pages_api")
                                                    Pages Router API routes are one handler per
                                                    file; there's no per-method breakdown.

DESIGN CHOICES:
  - Component name extraction tries five patterns in priority order;
    falls back to the filename stem if none match (anonymous exports,
    .js files without class/function keywords, etc.). The fallback is
    intentionally honest — name=filename is still useful for the graph.

  - IMPORT references use the raw module specifier as target_expression
    ("react", "./hooks/useAuth", "../services/auth.service") — NOT
    the resolved file path. The resolver stage handles path resolution.

  - RENDERS references are deduplicated per component name within one
    file (one RENDERS edge from PageComponent → UserProfile is enough;
    if UserProfile appears three times in the JSX that's not three
    separate graph edges). Line is recorded from the FIRST occurrence.

  - Self-renders are excluded (a component rendering itself is recursion,
    not a cross-component dependency worth graphing at this stage).

  - JSX component detection uses the uppercase-first-letter convention:
    <Foo> is a component, <div> is an HTML element. This is the same
    heuristic React itself uses at runtime — no false positives from
    built-in HTML tags.
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

EXTRACTOR_ID = "react"

# ── Component name extraction — ordered by specificity ─────────────────────

# export default function UserProfile(...) {
_DEFAULT_NAMED_FN = re.compile(
    r'export\s+default\s+(?:async\s+)?function\s+([A-Z]\w*)'
)
# export default class UserProfile extends ...
_DEFAULT_NAMED_CLASS = re.compile(
    r'export\s+default\s+class\s+([A-Z]\w*)'
)
# export function UserProfile(...)   (named, non-default)
_NAMED_EXPORT_FN = re.compile(
    r'export\s+(?:async\s+)?function\s+([A-Z]\w*)'
)
# export const UserProfile = ...  or  export const UserProfile: React.FC = ...
_NAMED_EXPORT_CONST = re.compile(
    r'export\s+const\s+([A-Z]\w*)\s*(?::|=)'
)
# export default UserProfile;    (re-export of an already-defined name)
_DEFAULT_REEXPORT = re.compile(
    r'export\s+default\s+([A-Z]\w*)\s*;'
)

_COMPONENT_PATTERNS = [
    _DEFAULT_NAMED_FN,
    _DEFAULT_NAMED_CLASS,
    _NAMED_EXPORT_FN,
    _NAMED_EXPORT_CONST,
    _DEFAULT_REEXPORT,
]

# ── Hook detection ──────────────────────────────────────────────────────────

# export function useAuth(...)
_HOOK_EXPORT = re.compile(
    r'export\s+(?:async\s+)?function\s+(use[A-Z]\w*)'
)

# const useSomething = ...
_HOOK_CONST = re.compile(
    r'const\s+(use[A-Z]\w*)\s*='
)

# useHook(...) call usage
_HOOK_CALL = re.compile(
    r'(use[A-Z]\w*)\s*\('
)

# ── HTTP method exports for Next.js App Router ──────────────────────────────

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})

# export async function GET(request: NextRequest) {
# export function POST(...) {
_HTTP_METHOD_EXPORT = re.compile(
    r'export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*\('
)

# ── Imports — handles both single-line and multi-line forms ─────────────────

# Matches the module specifier in:
#   import React from 'react'
#   import { useState, useEffect } from 'react'
#   import * as api from './api'
#   import './globals.css'   (side-effect import)
# Using `from '...'` as the anchor since it appears at end of all forms;
# side-effect-only imports (`import '...'`) are caught by the fallback pattern.
_IMPORT_FROM = re.compile(
    r'\bfrom\s+["\']([^"\']+)["\']'
)
_IMPORT_SIDE_EFFECT = re.compile(
    r'^\s*import\s+["\']([^"\']+)["\']',
    re.MULTILINE,
)

# ── JSX component usage ──────────────────────────────────────────────────────

# <UserProfile ...> or <UserProfile/>   — uppercase = React component
# <div ...> — lowercase = HTML element, ignored
_JSX_COMPONENT_USE = re.compile(
    r'<([A-Z][A-Za-z0-9]*)\s*(?:[^>]*?)(?:/>|>)'
)


def _file_stem(file_path: str) -> str:
    """'app/components/UserProfile.tsx' → 'UserProfile'"""
    name = file_path.rsplit("/", 1)[-1]
    for ext in (".tsx", ".jsx", ".ts", ".js"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _extract_component_name(content: str, file_path: str) -> str:
    """
    Tries pattern matches in priority order; falls back to filename stem.
    The fallback is intentionally kept — an anonymous default export in a
    file called UserProfile.tsx is still "UserProfile" in the graph.
    """
    for pattern in _COMPONENT_PATTERNS:
        m = pattern.search(content)
        if m:
            return m.group(1)
    return _file_stem(file_path)


def _extract_hook_name(content: str) -> Optional[str]:
    """Finds first exported custom hook name."""
    for pattern in (_HOOK_EXPORT, _HOOK_CONST):
        m = pattern.search(content)
        if m:
            return m.group(1)
    return None


def _extract_imports(content: str, file_path: str) -> List[ExtractedReference]:
    """
    Produces one IMPORT reference per distinct module specifier found.
    Deduplication is intentional — if a file has two `from 'react'`
    lines (unusual but valid), there's no value in two IMPORT edges.
    """
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
            source_identity=None,   # backfilled by caller once name is known
            reference_type=ReferenceType.IMPORT,
            target_expression=module,
            line=line,
            extractor_id=EXTRACTOR_ID,
        ))

    for m in _IMPORT_SIDE_EFFECT.finditer(content):
        module = m.group(1)
        if module in seen:
            continue
        seen.add(module)
        line = content[: m.start()].count("\n") + 1
        references.append(ExtractedReference(
            source_file=file_path,
            source_identity=None,
            reference_type=ReferenceType.IMPORT,
            target_expression=module,
            line=line,
            extractor_id=EXTRACTOR_ID,
        ))

    return references


def _extract_jsx_renders(
    content: str,
    file_path: str,
    self_name: Optional[str],
) -> List[ExtractedReference]:
    """
    Produces one RENDERS reference per distinct uppercase JSX component
    usage. Deduplicates within the file; records line of first occurrence.
    Excludes self-references (recursive components).
    """
    seen: Set[str] = set()
    references: List[ExtractedReference] = []

    for m in _JSX_COMPONENT_USE.finditer(content):
        component = m.group(1)
        if component == self_name or component in seen:
            continue
        seen.add(component)
        line = content[: m.start()].count("\n") + 1
        references.append(ExtractedReference(
            source_file=file_path,
            source_identity=self_name,
            reference_type=ReferenceType.RENDERS,
            target_expression=component,
            line=line,
            extractor_id=EXTRACTOR_ID,
        ))

    return references


def _extract_hook_calls(
    content: str,
    file_path: str,
    source_name: str,
) -> List[ExtractedReference]:
    """Produces CALLS references for hooks used in the file."""
    seen: Set[str] = set()
    references: List[ExtractedReference] = []

    for m in _HOOK_CALL.finditer(content):
        hook = m.group(1)
        if hook in seen:
            continue
        seen.add(hook)
        line = content[: m.start()].count("\n") + 1
        references.append(ExtractedReference(
            source_file=file_path,
            source_identity=source_name,
            reference_type=ReferenceType.CALLS,
            target_expression=hook,
            line=line,
            extractor_id=EXTRACTOR_ID,
        ))

    return references


# ── Per-hint extraction functions ────────────────────────────────────────────

def _extract_component(file_path: str, content: str) -> ExtractionResult:
    """
    Handles react_component and react_page_component.
    """
    name = _extract_component_name(content, file_path)
    imports = _extract_imports(content, file_path)
    renders = _extract_jsx_renders(content, file_path, name)
    calls = _extract_hook_calls(content, file_path, name)

    # Backfill source_identity
    for ref in imports + calls:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.UI,
        subtype="react_component",
        raw_metadata={
            "renders": [r.target_expression for r in renders],
            "imports": [r.target_expression for r in imports],
            "hooks": [r.target_expression for r in calls],
        },
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports + renders + calls,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_hook(file_path: str, content: str) -> ExtractionResult:
    """Handles react_hook."""
    name = _extract_hook_name(content) or _file_stem(file_path)
    imports = _extract_imports(content, file_path)
    calls = _extract_hook_calls(content, file_path, name)

    for ref in imports + calls:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.BEHAVIOR,
        subtype="react_hook",
        raw_metadata={
            "imports": [r.target_expression for r in imports],
            "hooks": [r.target_expression for r in calls],
        },
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports + calls,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_route_handler(file_path: str, content: str) -> ExtractionResult:
    """Handles nextjs_route_handler."""
    imports = _extract_imports(content, file_path)
    identities: List[ExtractedIdentity] = []

    for m in _HTTP_METHOD_EXPORT.finditer(content):
        method = m.group(1)
        line = content[: m.start()].count("\n") + 1
        identities.append(ExtractedIdentity(
            file_path=file_path,
            name=method,
            category=IdentityCategory.API,
            subtype="nextjs_route_handler",
            line_start=line,
            raw_metadata={
                "http_method": method,
                "route_file": file_path,
            },
            extractor_id=EXTRACTOR_ID,
        ))

    if not identities:
        identities.append(ExtractedIdentity(
            file_path=file_path,
            name=_file_stem(file_path),
            category=IdentityCategory.API,
            subtype="nextjs_route_handler",
            raw_metadata={"route_file": file_path},
            extractor_id=EXTRACTOR_ID,
        ))

    first_name = identities[0].name if identities else None
    for ref in imports:
        ref.source_identity = first_name

    return ExtractionResult(
        file_path=file_path,
        identities=identities,
        references=imports,
        extractor_id=EXTRACTOR_ID,
    )


def _extract_pages_api(file_path: str, content: str) -> ExtractionResult:
    """Handles nextjs_pages_api."""
    imports = _extract_imports(content, file_path)
    name = _file_stem(file_path)

    for ref in imports:
        ref.source_identity = name

    identity = ExtractedIdentity(
        file_path=file_path,
        name=name,
        category=IdentityCategory.API,
        subtype="nextjs_pages_api",
        raw_metadata={"route_file": file_path},
        extractor_id=EXTRACTOR_ID,
    )

    return ExtractionResult(
        file_path=file_path,
        identities=[identity],
        references=imports,
        extractor_id=EXTRACTOR_ID,
    )


# ── Public entrypoint ────────────────────────────────────────────────────────

def extract(file_path: str, content: str, classified_file: ClassifiedFile) -> ExtractionResult:
    """
    Dispatches on pending_extractor hint.
    """
    hint = classified_file.pending_extractor
    try:
        if hint in ("react_component", "react_page_component"):
            return _extract_component(file_path, content)
        elif hint == "react_hook":
            return _extract_hook(file_path, content)
        elif hint == "nextjs_route_handler":
            return _extract_route_handler(file_path, content)
        elif hint == "nextjs_pages_api":
            return _extract_pages_api(file_path, content)
        else:
            return ExtractionResult(
                file_path=file_path,
                extractor_id=EXTRACTOR_ID,
                parse_errors=[f"react_extractor does not handle hint: {hint!r}"],
            )
    except Exception as e:
        return ExtractionResult(
            file_path=file_path,
            extractor_id=EXTRACTOR_ID,
            parse_errors=[f"Unexpected error during extraction: {e}"],
        )
