"""
view_validator.py — Detects broken CREATE VIEW dependencies.

When a migration column is dropped, any VIEW that selects from that table
will break at runtime. This validator parses CREATE VIEW statements in
migration files and checks if they reference columns that no longer exist.

Example:
  001_users.sql defines CREATE TABLE users (id, name, email)
  003_views.sql defines CREATE VIEW active_users AS SELECT id, email FROM users
  PR drops 'email' from 001_users.sql
  → View references dropped column (severity=critical)
"""

import re
from typing import Dict, List, Optional, Tuple


def extract_views(sql_content: str) -> List[Tuple[str, str]]:
    """
    Parses CREATE [OR REPLACE] VIEW statements from migration SQL.

    Returns list of (view_name, select_body) tuples.
    """
    views: List[Tuple[str, str]] = []

    pattern = re.compile(
        r'CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'(\w+)\s+AS\s+(SELECT\s+.+?)(?:;|$)',
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(sql_content):
        view_name = m.group(1).lower()
        select_body = m.group(2).strip()
        views.append((view_name, select_body))

    return views


def _extract_view_table_refs(select_body: str) -> List[str]:
    """
    Extracts table names referenced in a VIEW's SELECT body.

    Handles:
      FROM table_name
      JOIN table_name ON ...
      LEFT JOIN table_name ...
    """
    tables: List[str] = []
    seen: set = set()

    pattern = re.compile(
        r'\b(?:FROM|JOIN)\s+(\w+)',
        re.IGNORECASE,
    )
    for m in pattern.finditer(select_body):
        table = m.group(1).lower()
        if table not in seen and table not in ("select", "where", "on", "as"):
            seen.add(table)
            tables.append(table)

    return tables


def _extract_view_columns(select_body: str) -> List[str]:
    """
    Extracts column names from a VIEW's SELECT clause.

    Handles:
      SELECT id, name, email FROM ...    → [id, name, email]
      SELECT u.id, u.name FROM ...       → [id, name]
      SELECT * FROM ...                  → [*]
    """
    # Get text between SELECT and FROM
    select_match = re.match(
        r'SELECT\s+(.*?)\s+FROM\b',
        select_body,
        re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        return []

    select_clause = select_match.group(1)
    if select_clause.strip() == "*":
        return ["*"]

    columns: List[str] = []
    for part in select_clause.split(","):
        part = part.strip()
        if not part:
            continue

        # Handle alias.column → extract column
        # Handle column AS alias → extract column
        # Handle function(col) → skip
        if "(" in part:
            continue

        as_match = re.match(r'(?:\w+\.)?(\w+)(?:\s+AS\s+\w+)?$', part, re.IGNORECASE)
        if as_match:
            columns.append(as_match.group(1).lower())

    return columns


def check_view_dependencies(
    graph,
    changed_fqn: str,
    new_columns: List[str],
    ContractViolation,
) -> list:
    """
    Checks if any CREATE VIEW in the repo references columns from the
    changed migration table that no longer exist.

    Scans all migration nodes for CREATE VIEW statements, checks if
    the view's SELECT references the changed table, and verifies the
    selected columns still exist.

    Args:
        graph             — RepoLineageGraph
        changed_fqn       — FQN of changed migration asset
        new_columns       — column list after the PR change
        ContractViolation — dataclass to instantiate
    """
    violations = []
    new_col_set = {c.lower() for c in new_columns}
    if not new_col_set:
        return violations

    changed_node = graph.nodes.get(changed_fqn)
    if not changed_node:
        return violations

    defined_table = changed_node.raw_metadata.get("defined_table", "").lower()
    if not defined_table:
        return violations

    # Scan all nodes for CREATE VIEW statements
    for fqn, node in graph.nodes.items():
        if fqn == changed_fqn or not node.sql:
            continue

        views = extract_views(node.sql)
        for view_name, select_body in views:
            # Check if this view references the changed table
            referenced_tables = _extract_view_table_refs(select_body)
            if defined_table not in referenced_tables:
                continue

            # Check if the view selects specific columns
            view_cols = _extract_view_columns(select_body)
            if not view_cols:
                continue

            if "*" in view_cols:
                # SELECT * — any column removal changes the view output
                violations.append(ContractViolation(
                    violation_type="view_broken",
                    changed_fqn=changed_fqn,
                    affected_fqn=fqn,
                    column="*",
                    detail=(
                        f"View '{view_name}' in {fqn} uses SELECT * FROM "
                        f"{defined_table}. Any column removal from "
                        f"{changed_fqn} will silently change the view's "
                        f"output schema."
                    ),
                    severity="medium",
                    file_path=node.file_path,
                    fix_hint=(
                        f"Replace SELECT * with an explicit column list in "
                        f"view '{view_name}' to make dependencies visible."
                    ),
                ))
                continue

            for col in view_cols:
                if col not in new_col_set:
                    violations.append(ContractViolation(
                        violation_type="view_broken",
                        changed_fqn=changed_fqn,
                        affected_fqn=fqn,
                        column=col,
                        detail=(
                            f"View '{view_name}' in {fqn} selects column "
                            f"'{col}' from {defined_table}, but '{col}' no "
                            f"longer exists in {changed_fqn}. The view will "
                            f"fail at query time."
                        ),
                        severity="critical",
                        file_path=node.file_path,
                        fix_hint=(
                            f"Update view '{view_name}' in {node.file_path} "
                            f"to remove or replace reference to '{col}'."
                        ),
                    ))

    return violations
