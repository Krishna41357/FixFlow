"""
stack_registry.py — Maps a detected StackProfile to its rule set.

This is the single point of indirection between "what did we detect"
and "which rules do we apply". Adding a new stack means adding an
entry here plus a rule set in models/stack_rules.py — nothing else
in the extractor module needs to change.

Resolution order mirrors how repo_parser_controller.py already prefers
explicit signals over guesses: ORM match first (most specific), then
framework match, then a bare-language fallback, then UNKNOWN.
"""

from typing import Optional, List

from models.classification import StackProfile
from models.stack_rules import (
    ClassificationRule,
    STACK_RULE_SETS,
    UNIVERSAL_RULES,
)


def resolve_rule_set_key(stack: StackProfile) -> Optional[str]:
    """
    Resolves a StackProfile to a key in STACK_RULE_SETS.

    Resolution priority:
      1. orm + framework combo (most specific, e.g. "nestjs+typeorm")
      2. orm alone (e.g. dbt has no "framework" in the usual sense)
      3. framework alone, paired with a guessed common ORM/db combo
      4. None — caller falls back to UNIVERSAL_RULES only, tags
         everything else UNKNOWN (better than guessing wrong)
    """
    frameworks = {f.lower() for f in stack.frameworks}
    orm = (stack.orm or "").lower()

    if orm == "dbt":
        return "dbt"

    if "nestjs" in frameworks and orm == "typeorm":
        return "nestjs+typeorm"

    if "nextjs" in frameworks and orm == "prisma":
        return "nextjs+prisma"

    if "express" in frameworks and orm == "mongoose":
        return "react+express+mongo"

    # No confident combo match — caller should know detection was weak
    return None


def get_rule_set(stack: StackProfile) -> List[ClassificationRule]:
    """
    Returns the full ordered rule list to apply for this stack:
    stack-specific rules first (more specific, checked first by the
    rule engine), universal rules appended after as fallback/tiebreakers.

    If no stack-specific rule set is found, returns UNIVERSAL_RULES alone —
    the repo will mostly classify as UNKNOWN except for infra/CI/docs,
    which is the conservative, honest outcome when detection fails.
    """
    key = resolve_rule_set_key(stack)
    stack_specific = STACK_RULE_SETS.get(key, []) if key else []
    return stack_specific + UNIVERSAL_RULES
