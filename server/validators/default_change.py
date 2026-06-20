"""
default_change.py — Detects DEFAULT value changes in migration columns.

Flags when a column's DEFAULT is removed or changed, which can break
downstream INSERT statements that rely on the default value.

Example:
  PR removes `DEFAULT true` from `is_active BOOLEAN DEFAULT true`
  Downstream INSERTs that omit is_active will now get NULL (or fail if NOT NULL)
  → Default removed violation (severity=high if NOT NULL, medium otherwise)
"""

import re
from typing import Dict, List, Optional


def extract_column_defaults(sql_content: str) -> Dict[str, Optional[str]]:
    """
    Parses CREATE TABLE to extract {col: default_value} mapping.

    Handles:
      is_active BOOLEAN DEFAULT true        → {is_active: "true"}
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP → {created_at: "current_timestamp"}
      status TEXT DEFAULT 'pending'         → {status: "'pending'"}
      count INTEGER                         → not included (no default)

    Returns only columns that HAVE a DEFAULT clause.
    """
    defaults: Dict[str, Optional[str]] = {}

    skip = re.compile(
        r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY)\b',
        re.IGNORECASE,
    )

    body_match = re.search(
        r'CREATE\s+TABLE[^(]+\((.+?)\)(?:\s*;|\s*$)',
        sql_content,
        re.IGNORECASE | re.DOTALL,
    )
    if not body_match:
        return defaults

    for line in body_match.group(1).splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or skip.match(stripped):
            continue

        col_match = re.match(r'^(\w+)\s+\w+', stripped)
        if not col_match:
            continue

        col = col_match.group(1).lower()

        # Look for DEFAULT keyword
        default_match = re.search(
            r'\bDEFAULT\s+(.+?)(?:\s+(?:NOT\s+NULL|NULL|UNIQUE|CHECK|REFERENCES|PRIMARY|CONSTRAINT|,)|\s*$)',
            stripped,
            re.IGNORECASE,
        )
        if default_match:
            defaults[col] = default_match.group(1).strip().lower()

    return defaults


def _has_not_null(column_def: str) -> bool:
    """Check if a column definition includes NOT NULL."""
    return bool(re.search(r'\bNOT\s+NULL\b', column_def, re.IGNORECASE))


def extract_not_null_columns(sql_content: str) -> set:
    """Returns set of column names that have NOT NULL constraint."""
    not_null_cols = set()

    body_match = re.search(
        r'CREATE\s+TABLE[^(]+\((.+?)\)(?:\s*;|\s*$)',
        sql_content,
        re.IGNORECASE | re.DOTALL,
    )
    if not body_match:
        return not_null_cols

    for line in body_match.group(1).splitlines():
        stripped = line.strip().rstrip(",")
        col_match = re.match(r'^(\w+)\s+\w+', stripped)
        if col_match and _has_not_null(stripped):
            not_null_cols.add(col_match.group(1).lower())

    return not_null_cols


def check_default_changes(
    graph,
    changed_fqn: str,
    new_defaults: Dict[str, Optional[str]],
    new_not_null_cols: set,
    ContractViolation,
) -> list:
    """
    Checks if DEFAULT value changes could break downstream behavior.

    - DEFAULT removed on NOT NULL column → severity=high (INSERTs will fail)
    - DEFAULT removed on nullable column → severity=medium (behavior change)
    - DEFAULT value changed → severity=low (informational)

    Args:
        graph             — RepoLineageGraph
        changed_fqn       — FQN of changed asset
        new_defaults      — {col: default_value} from post-PR SQL
        new_not_null_cols — set of columns with NOT NULL in post-PR SQL
        ContractViolation — dataclass to instantiate
    """
    violations = []
    changed_node = graph.nodes.get(changed_fqn)
    if not changed_node:
        return violations

    old_defaults: Dict[str, str] = changed_node.raw_metadata.get("column_defaults", {})
    if not old_defaults:
        return violations

    for col, old_val in old_defaults.items():
        new_val = new_defaults.get(col)

        if old_val and not new_val:
            # DEFAULT was removed
            is_not_null = col in new_not_null_cols
            violations.append(ContractViolation(
                violation_type="default_removed",
                changed_fqn=changed_fqn,
                affected_fqn=changed_fqn,
                column=col,
                detail=(
                    f"Column '{col}' in {changed_fqn} had DEFAULT {old_val} "
                    f"which was removed. "
                    + (
                        f"The column is NOT NULL — INSERTs that omit '{col}' "
                        f"will now fail."
                        if is_not_null else
                        f"INSERTs that omit '{col}' will now get NULL "
                        f"instead of {old_val}."
                    )
                ),
                severity="high" if is_not_null else "medium",
                file_path=changed_node.file_path,
                fix_hint=(
                    f"Add back DEFAULT {old_val} to column '{col}', or "
                    f"update all INSERT statements to explicitly provide a "
                    f"value for '{col}'."
                ),
            ))
        elif old_val and new_val and old_val != new_val:
            # DEFAULT value changed
            violations.append(ContractViolation(
                violation_type="default_changed",
                changed_fqn=changed_fqn,
                affected_fqn=changed_fqn,
                column=col,
                detail=(
                    f"Column '{col}' in {changed_fqn} had DEFAULT {old_val}, "
                    f"now DEFAULT {new_val}. Existing INSERTs that rely on "
                    f"the default will produce different values."
                ),
                severity="low",
                file_path=changed_node.file_path,
                fix_hint=(
                    f"Verify that the new default '{new_val}' is intentional "
                    f"and compatible with downstream consumers."
                ),
            ))

    return violations
