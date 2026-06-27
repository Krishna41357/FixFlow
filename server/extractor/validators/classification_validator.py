"""
classification_validator.py — Sanity checks on RepoClassification output.

These are NOT the same kind of validator as server/validators/*.py
(which detect breaking SQL changes). These validators check that the
classifier itself produced trustworthy output — catching systematic
misdetection (e.g. stack detection failed silently, or 90% of files
came back UNKNOWN) before that bad classification poisons every
downstream stage (identity extraction, edge discovery, graph build).

Returns a list of warnings — never raises. Classification with warnings
is still usable; the warnings tell the caller (or a human) where to look.
"""

from dataclasses import dataclass
from typing import List

from extractor.models.classification import RepoClassification, FileTag


@dataclass
class ClassificationWarning:
    code: str
    message: str
    severity: str   # "critical" | "warning" | "info"


def validate_classification(result: RepoClassification) -> List[ClassificationWarning]:
    """
    Runs all sanity checks against a RepoClassification result.
    """
    warnings: List[ClassificationWarning] = []

    warnings.extend(_check_stack_recognized(result))
    warnings.extend(_check_unknown_ratio(result))
    warnings.extend(_check_no_extractable_files(result))
    warnings.extend(_check_empty_repo(result))

    return warnings


def _check_stack_recognized(result: RepoClassification) -> List[ClassificationWarning]:
    if not result.stack_profile.is_recognized:
        return [ClassificationWarning(
            code="stack_unrecognized",
            message=(
                f"No stack signal detected for {result.repo_full_name} — "
                f"only universal rules (infra/CI/docs) were applied. "
                f"Most application files will be UNKNOWN."
            ),
            severity="critical",
        )]
    return []


def _check_unknown_ratio(result: RepoClassification) -> List[ClassificationWarning]:
    if result.total_files_scanned == 0:
        return []

    unknown_count = result.tag_counts.get(FileTag.UNKNOWN.value, 0)
    ratio = unknown_count / result.total_files_scanned

    if ratio > 0.5:
        return [ClassificationWarning(
            code="high_unknown_ratio",
            message=(
                f"{unknown_count}/{result.total_files_scanned} files "
                f"({ratio:.0%}) classified as UNKNOWN for {result.repo_full_name}. "
                f"Rule set may not match this repo's actual structure — "
                f"verify stack_profile is correct."
            ),
            severity="warning",
        )]
    return []


def _check_no_extractable_files(result: RepoClassification) -> List[ClassificationWarning]:
    if result.total_files_scanned > 0 and len(result.extractable_files) == 0:
        return [ClassificationWarning(
            code="no_extractable_files",
            message=(
                f"No SCHEMA_DEFINITION, MIGRATION, DATA_ACCESS, or API_CONTRACT "
                f"files found in {result.repo_full_name}. Downstream identity "
                f"extraction will have nothing to process."
            ),
            severity="warning",
        )]
    return []


def _check_empty_repo(result: RepoClassification) -> List[ClassificationWarning]:
    if result.total_files_scanned == 0:
        return [ClassificationWarning(
            code="empty_file_tree",
            message=f"No files found at all for {result.repo_full_name} — check repo access/permissions.",
            severity="critical",
        )]
    return []
