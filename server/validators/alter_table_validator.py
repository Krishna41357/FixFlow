"""
alter_table_validator.py — Parses ALTER TABLE statements from patches/SQL.

Handles three operation types:
  ALTER TABLE t DROP COLUMN col        → column removal
  ALTER TABLE t RENAME COLUMN old TO new → column rename
  ALTER TABLE t ALTER COLUMN col TYPE t  → column type change

These are detected from the PR diff patch and cross-referenced with
downstream column_usage to find breakage.
"""

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AlterOp:
    """A single ALTER TABLE operation parsed from SQL or a diff patch."""
    op_type:  str                  # "drop_column" | "rename_column" | "alter_type"
    table:    str                  # lowercased table name
    column:   str                  # affected column (old name for rename)
    new_name: Optional[str] = None # new column name (rename only)
    new_type: Optional[str] = None # new column type (alter_type only)


def extract_alter_operations(sql_or_patch: str) -> List[AlterOp]:
    """
    Extracts ALTER TABLE operations from SQL content or a diff patch.

    Handles (case-insensitive):
      ALTER TABLE users DROP COLUMN email
      ALTER TABLE users DROP email
      ALTER TABLE users RENAME COLUMN email TO email_address
      ALTER TABLE users ALTER COLUMN user_id TYPE UUID
      ALTER TABLE users ALTER COLUMN user_id SET DATA TYPE UUID
      ALTER TABLE users MODIFY COLUMN user_id UUID  (MySQL syntax)

    For patches: only processes added (+) lines to detect NEW operations.
    For full SQL: processes all lines.
    """
    operations: List[AlterOp] = []

    # Determine if this is a patch (has +/- lines) or raw SQL
    is_patch = any(
        line.startswith("+") or line.startswith("-")
        for line in sql_or_patch.splitlines()[:10]
    )

    lines_to_check = []
    if is_patch:
        # Only look at added lines
        for line in sql_or_patch.splitlines():
            if line.startswith("+"):
                lines_to_check.append(line[1:])
    else:
        lines_to_check = sql_or_patch.splitlines()

    full_text = "\n".join(lines_to_check)

    # ── DROP COLUMN ───────────────────────────────────────────────────────────
    drop_pattern = re.compile(
        r'\bALTER\s+TABLE\s+(\w+)\s+DROP\s+(?:COLUMN\s+)?(\w+)',
        re.IGNORECASE,
    )
    for m in drop_pattern.finditer(full_text):
        operations.append(AlterOp(
            op_type="drop_column",
            table=m.group(1).lower(),
            column=m.group(2).lower(),
        ))

    # ── RENAME COLUMN ─────────────────────────────────────────────────────────
    rename_pattern = re.compile(
        r'\bALTER\s+TABLE\s+(\w+)\s+RENAME\s+COLUMN\s+(\w+)\s+TO\s+(\w+)',
        re.IGNORECASE,
    )
    for m in rename_pattern.finditer(full_text):
        operations.append(AlterOp(
            op_type="rename_column",
            table=m.group(1).lower(),
            column=m.group(2).lower(),
            new_name=m.group(3).lower(),
        ))

    # ── ALTER COLUMN TYPE (PostgreSQL syntax) ─────────────────────────────────
    alter_type_pg = re.compile(
        r'\bALTER\s+TABLE\s+(\w+)\s+ALTER\s+COLUMN\s+(\w+)\s+'
        r'(?:SET\s+DATA\s+)?TYPE\s+(\w+)',
        re.IGNORECASE,
    )
    for m in alter_type_pg.finditer(full_text):
        operations.append(AlterOp(
            op_type="alter_type",
            table=m.group(1).lower(),
            column=m.group(2).lower(),
            new_type=m.group(3).lower(),
        ))

    # ── MODIFY COLUMN (MySQL syntax) ──────────────────────────────────────────
    modify_pattern = re.compile(
        r'\bALTER\s+TABLE\s+(\w+)\s+MODIFY\s+(?:COLUMN\s+)?(\w+)\s+(\w+)',
        re.IGNORECASE,
    )
    for m in modify_pattern.finditer(full_text):
        operations.append(AlterOp(
            op_type="alter_type",
            table=m.group(1).lower(),
            column=m.group(2).lower(),
            new_type=m.group(3).lower(),
        ))

    return operations


def check_alter_impacts(
    graph,
    changed_fqn: str,
    operations: List[AlterOp],
    get_downstream_fn,
    ContractViolation,
) -> list:
    """
    Cross-references ALTER TABLE operations with downstream column_usage.

    Args:
        graph             — RepoLineageGraph
        changed_fqn       — FQN of the changed asset
        operations        — parsed AlterOp list
        get_downstream_fn — reference to get_downstream(graph, fqn, depth)
        ContractViolation — dataclass to instantiate
    """
    violations = []
    if not operations:
        return violations

    changed_node = graph.nodes.get(changed_fqn)
    if not changed_node:
        return violations
    defined_table = changed_node.raw_metadata.get("defined_table", "").lower()

    for op in operations:
        # Match operation table to the changed asset
        if defined_table and op.table != defined_table:
            continue

        if op.op_type == "drop_column":
            for downstream in get_downstream_fn(graph, changed_fqn, depth=3):
                _check_column_in_usage(
                    downstream, changed_fqn, op.column,
                    "alter_drop_column",
                    f"ALTER TABLE DROP COLUMN '{op.column}' removes a column "
                    f"that {downstream.fqn} depends on.",
                    "critical",
                    ContractViolation, violations,
                )

        elif op.op_type == "rename_column":
            for downstream in get_downstream_fn(graph, changed_fqn, depth=3):
                _check_column_in_usage(
                    downstream, changed_fqn, op.column,
                    "alter_rename_column",
                    f"ALTER TABLE RENAME COLUMN '{op.column}' TO "
                    f"'{op.new_name}' — {downstream.fqn} still references "
                    f"the old name '{op.column}'.",
                    "high",
                    ContractViolation, violations,
                    fix_hint=(
                        f"Update {downstream.file_path} to use "
                        f"'{op.new_name}' instead of '{op.column}'."
                    ),
                )

        elif op.op_type == "alter_type":
            for downstream in get_downstream_fn(graph, changed_fqn, depth=3):
                _check_column_in_usage(
                    downstream, changed_fqn, op.column,
                    "alter_type_change",
                    f"ALTER TABLE changes '{op.column}' to type "
                    f"'{op.new_type}' — {downstream.fqn} uses this column "
                    f"and may encounter type mismatch.",
                    "high",
                    ContractViolation, violations,
                )

    return violations


def _check_column_in_usage(
    downstream_node,
    changed_fqn: str,
    column: str,
    violation_type: str,
    detail: str,
    severity: str,
    ContractViolation,
    violations: list,
    fix_hint: Optional[str] = None,
):
    """Helper: checks if a downstream node uses the given column from changed_fqn."""
    for usage_fqn, usages in downstream_node.column_usage.items():
        bare_changed = changed_fqn.split(".")[-1].lower()
        bare_usage   = usage_fqn.split(".")[-1].lower()
        if bare_changed != bare_usage and usage_fqn != changed_fqn:
            continue

        for cu in usages:
            if cu.column.lower() == column:
                violations.append(ContractViolation(
                    violation_type=violation_type,
                    changed_fqn=changed_fqn,
                    affected_fqn=downstream_node.fqn,
                    column=column,
                    detail=detail,
                    severity=severity,
                    file_path=downstream_node.file_path,
                    fix_hint=fix_hint or (
                        f"Update {downstream_node.file_path} to account "
                        f"for the change to column '{column}'."
                    ),
                ))
                break  # one violation per downstream per column
