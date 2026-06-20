"""
validators — Modular contract validation package for Pipeline Autopsy.

Each submodule handles one category of pre-merge breakage detection.
This __init__ exposes run_all_validators() as the single integration
point for investigation_controller / repo_parser_controller.
"""

from typing import Dict, List, Optional

from validators.type_change import (
    check_type_changes,
    extract_column_types,
    extract_types_from_patch,
)
from validators.default_change import (
    check_default_changes,
    extract_column_defaults,
    extract_not_null_columns,
)
from validators.view_validator import (
    check_view_dependencies,
)
from validators.alter_table_validator import (
    check_alter_impacts,
    extract_alter_operations,
    AlterOp,
)


def run_all_validators(
    graph,
    changed_fqn: str,
    new_columns: List[str],
    new_types: Dict[str, str],
    new_defaults: Dict[str, Optional[str]],
    new_not_null_cols: set,
    patch: str,
    get_downstream_fn,
    ContractViolation,
) -> list:
    """
    Runs all new validators and returns a combined list of ContractViolation.

    Called from validate_contracts() in repo_parser_controller.py after the
    existing checks (column_drops, fk_column_existence, etc.).

    Args:
        graph              — RepoLineageGraph
        changed_fqn        — FQN of the changed asset
        new_columns        — column list after the PR change
        new_types          — {col: type} after the PR change
        new_defaults       — {col: default_value} after the PR change
        new_not_null_cols  — set of NOT NULL columns after the PR change
        patch              — stripped diff patch for this asset
        get_downstream_fn  — reference to get_downstream(graph, fqn, depth)
        ContractViolation  — the dataclass to instantiate violations
    """
    all_violations: list = []

    # 1. Column type changes
    try:
        all_violations.extend(
            check_type_changes(
                graph, changed_fqn, new_types,
                get_downstream_fn, ContractViolation,
            )
        )
    except Exception as e:
        print(f"WARNING run_all_validators: type_change failed for {changed_fqn}: {e}")

    # 2. DEFAULT value changes
    try:
        all_violations.extend(
            check_default_changes(
                graph, changed_fqn, new_defaults,
                new_not_null_cols, ContractViolation,
            )
        )
    except Exception as e:
        print(f"WARNING run_all_validators: default_change failed for {changed_fqn}: {e}")

    # 3. CREATE VIEW dependency checks
    try:
        all_violations.extend(
            check_view_dependencies(
                graph, changed_fqn, new_columns,
                ContractViolation,
            )
        )
    except Exception as e:
        print(f"WARNING run_all_validators: view_validator failed for {changed_fqn}: {e}")

    # 4. ALTER TABLE statement impacts
    try:
        operations = extract_alter_operations(patch)
        if operations:
            all_violations.extend(
                check_alter_impacts(
                    graph, changed_fqn, operations,
                    get_downstream_fn, ContractViolation,
                )
            )
    except Exception as e:
        print(f"WARNING run_all_validators: alter_table failed for {changed_fqn}: {e}")

    return all_violations
