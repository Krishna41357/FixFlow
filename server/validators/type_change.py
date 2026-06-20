"""
type_change.py — Detects column type changes in migration tables.

Compares old column types (stored in graph raw_metadata) against new types
(extracted from PR branch SQL or patch) and flags downstream breakage.

Example:
  PR changes `user_id INTEGER` → `user_id UUID` in 001_users.sql
  Downstream 001_orders.sql has `JOIN users ON orders.user_id = users.user_id`
  → Type mismatch violation (severity=high)
"""

import re
from typing import Dict, List, Optional

# Avoid circular import — ContractViolation is imported at call time
# by the __init__.py orchestrator.


def extract_column_types(sql_content: str) -> Dict[str, str]:
    """
    Parses CREATE TABLE + ALTER TABLE ADD COLUMN to extract {col: type} mapping.

    Handles:
      id INTEGER PRIMARY KEY AUTOINCREMENT  → {id: "integer"}
      name TEXT NOT NULL                    → {name: "text"}
      email VARCHAR(255) UNIQUE            → {email: "varchar"}
      price DECIMAL(10,2) DEFAULT 0.00     → {price: "decimal"}

    Type is lowercased and stripped of size specifiers for comparison.
    """
    types: Dict[str, str] = {}

    skip = re.compile(
        r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY)\b',
        re.IGNORECASE,
    )

    # ── CREATE TABLE body ─────────────────────────────────────────────────────
    body_match = re.search(
        r'CREATE\s+TABLE[^(]+\((.+?)\)(?:\s*;|\s*$)',
        sql_content,
        re.IGNORECASE | re.DOTALL,
    )
    if body_match:
        for line in body_match.group(1).splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped or skip.match(stripped):
                continue
            col_match = re.match(r'^(\w+)\s+(\w+)', stripped)
            if col_match:
                col  = col_match.group(1).lower()
                ctype = col_match.group(2).lower()
                types[col] = ctype

    # ── ALTER TABLE ADD COLUMN ────────────────────────────────────────────────
    alter_add = re.compile(
        r'\bALTER\s+TABLE\s+\w+\s+ADD\s+(?:COLUMN\s+)?(\w+)\s+(\w+)',
        re.IGNORECASE,
    )
    for m in alter_add.finditer(sql_content):
        col  = m.group(1).lower()
        ctype = m.group(2).lower()
        types[col] = ctype

    return types


def extract_types_from_patch(patch: str) -> Dict[str, str]:
    """
    Extracts column types from added (+) lines in a diff patch.
    Returns {col: type} for newly added or modified column definitions.
    """
    types: Dict[str, str] = {}
    skip = re.compile(
        r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY|CREATE|ALTER|\))',
        re.IGNORECASE,
    )

    for line in patch.splitlines():
        if not line.startswith("+"):
            continue
        content = line[1:].strip().rstrip(",")
        if not content or skip.match(content):
            continue
        col_match = re.match(r'^(\w+)\s+(\w+)', content)
        if col_match:
            types[col_match.group(1).lower()] = col_match.group(2).lower()

    return types


def check_type_changes(
    graph,
    changed_fqn: str,
    new_types: Dict[str, str],
    get_downstream_fn,
    ContractViolation,
) -> list:
    """
    Checks if column type changes in the changed asset break downstream consumers.

    Args:
        graph              — RepoLineageGraph
        changed_fqn        — FQN of the asset changed in the PR
        new_types          — {col: type} from the PR (post-change)
        get_downstream_fn  — reference to get_downstream(graph, fqn, depth)
        ContractViolation  — the dataclass to instantiate violations

    Returns List[ContractViolation].
    """
    violations = []
    changed_node = graph.nodes.get(changed_fqn)
    if not changed_node:
        return violations

    old_types: Dict[str, str] = changed_node.raw_metadata.get("column_types", {})
    if not old_types or not new_types:
        return violations

    # Find columns whose type actually changed
    changed_cols: Dict[str, tuple] = {}
    for col, new_type in new_types.items():
        old_type = old_types.get(col)
        if old_type and old_type != new_type:
            changed_cols[col] = (old_type, new_type)

    if not changed_cols:
        return violations

    # Check downstream usage
    for downstream in get_downstream_fn(graph, changed_fqn, depth=3):
        for usage_fqn, usages in downstream.column_usage.items():
            bare_changed = changed_fqn.split(".")[-1].lower()
            bare_usage   = usage_fqn.split(".")[-1].lower()
            if bare_changed != bare_usage and usage_fqn != changed_fqn:
                continue

            for cu in usages:
                col = cu.column.lower()
                if col in changed_cols:
                    old_t, new_t = changed_cols[col]
                    violations.append(ContractViolation(
                        violation_type="column_type_changed",
                        changed_fqn=changed_fqn,
                        affected_fqn=downstream.fqn,
                        column=col,
                        detail=(
                            f"{downstream.fqn} uses column '{col}' from "
                            f"{changed_fqn}, but its type changed from "
                            f"'{old_t}' to '{new_t}'. This may cause type "
                            f"mismatch errors in JOINs, WHERE clauses, or "
                            f"implicit casts."
                        ),
                        severity="high",
                        file_path=downstream.file_path,
                        fix_hint=(
                            f"Update {downstream.file_path} to handle the "
                            f"new type '{new_t}' for column '{col}', or add "
                            f"an explicit CAST."
                        ),
                    ))

    return violations
