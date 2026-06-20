"""
rule_engine.py — Applies a stack's classification rules to a file tree.

This is intentionally generic — it knows nothing about NestJS, dbt, or
any specific stack. It only knows how to apply an ordered list of
ClassificationRule objects against (path, content) pairs. All stack
knowledge lives in stack_rules.py as data; this file is pure mechanism.

Matching strategy per rule (mirrors the three-stage decision already
used in repo_parser_controller.py's _is_relevant_yml):
  - path_pattern set, content_sniff None  → match on path alone, fire immediately
  - path_pattern set, content_sniff set   → path must match AND content must
                                             match (content as confirming tiebreaker)
  - path_pattern None, content_sniff set  → content alone decides (used for
                                             genuinely ambiguous paths, e.g. root .yml)

First matching rule wins — rule_set ordering controls priority.
Content is only fetched when a rule actually needs it (lazy), to avoid
fetching every file's content just to classify by path.
"""

import re
from typing import List, Optional, Callable

from models.classification import ClassifiedFile, FileTag
from models.stack_rules import ClassificationRule


def _path_matches(rule: ClassificationRule, path: str) -> bool:
    if rule.path_pattern is None:
        return True   # content-only rule — path is not a gate
    return re.search(rule.path_pattern, path) is not None


def _content_matches(rule: ClassificationRule, content: Optional[str]) -> bool:
    if rule.content_sniff is None:
        return True   # no content requirement — path alone is sufficient
    if content is None:
        return False   # rule needs content but none was fetched/available
    return re.search(rule.content_sniff, content, re.IGNORECASE) is not None


def classify_file(
    path: str,
    rule_set: List[ClassificationRule],
    content_fetcher: Optional[Callable[[str], Optional[str]]] = None,
) -> ClassifiedFile:
    """
    Classifies one file against an ordered rule set.

    content_fetcher — lazy callable, only invoked if a rule with
    content_sniff matches on path first (or has no path_pattern at all).
    This avoids fetching content for files that classify confidently
    on path alone — same efficiency principle as _strip_context_lines
    avoiding unnecessary work in the existing PR bot.

    Returns FileTag.UNKNOWN with confidence 0.0 if no rule matches —
    an honest "we don't know" rather than a forced guess.
    """
    fetched_content: Optional[str] = None
    content_fetch_attempted = False

    for rule in rule_set:
        if not _path_matches(rule, path):
            continue

        if rule.content_sniff is not None:
            if not content_fetch_attempted and content_fetcher is not None:
                fetched_content = content_fetcher(path)
                content_fetch_attempted = True
            if not _content_matches(rule, fetched_content):
                continue

        return ClassifiedFile(
            path=path,
            tag=rule.tag,
            confidence=rule.confidence,
            matched_rule=rule.rule_name,
            pending_extractor=rule.extractor_hint,
        )

    return ClassifiedFile(
        path=path,
        tag=FileTag.UNKNOWN,
        confidence=0.0,
        matched_rule=None,
        pending_extractor=None,
    )


def classify_files(
    paths: List[str],
    rule_set: List[ClassificationRule],
    content_fetcher: Optional[Callable[[str], Optional[str]]] = None,
) -> List[ClassifiedFile]:
    """
    Classifies a batch of files. Skips node_modules and other obvious
    vendor/build directories before running any rules — same exclusion
    repo_parser_controller.py's _filter_all_files already applies.
    """
    _SKIP_DIRS = ("node_modules/", ".git/", "dist/", "build/", "__pycache__/", "venv/")

    results: List[ClassifiedFile] = []
    skipped = 0

    for path in paths:
        if any(skip_dir in path for skip_dir in _SKIP_DIRS):
            skipped += 1
            continue
        results.append(classify_file(path, rule_set, content_fetcher))

    print(
        f"DEBUG classify_files: classified {len(results)} files "
        f"({skipped} skipped as vendor/build dirs)"
    )
    return results
