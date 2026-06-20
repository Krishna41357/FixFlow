"""
smoke_test.py — Proves the extractor module wires together correctly
without needing a live GitHub token or network access.

Tests:
  1. Rule engine classifies a synthetic NestJS+TypeORM file list correctly
  2. Rule engine classifies a synthetic dbt file list correctly
  3. Stack registry resolves the right rule set for a manually-built StackProfile
  4. Validator flags a deliberately bad classification (high UNKNOWN ratio)

Run: python smoke_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.classification import StackProfile, RepoClassification, FileTag
from registry.stack_registry import get_rule_set, resolve_rule_set_key
from classifiers.rule_engine import classify_files
from validators.classification_validator import validate_classification, ClassificationWarning


def test_nestjs_typeorm_classification():
    print("\n=== Test 1: NestJS + TypeORM file classification ===")

    stack = StackProfile(
        language="typescript",
        frameworks=["nestjs"],
        orm="typeorm",
        database="postgres",
        detected_from=["package.json:dependencies.@nestjs/core", "package.json:dependencies.typeorm"],
    )

    files = [
        "src/users/users.entity.ts",
        "src/users/users.controller.ts",
        "src/users/users.service.ts",
        "src/users/users.repository.ts",
        "src/users/dto/create-user.dto.ts",
        "src/users/users.module.ts",
        "migrations/1700000000000-AddEmailToUsers.ts",
        "Dockerfile",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
        "README.md",
        "node_modules/express/index.js",   # should be skipped entirely
    ]

    rule_set = get_rule_set(stack)
    results = classify_files(files, rule_set, content_fetcher=None)

    expected = {
        "src/users/users.entity.ts": FileTag.SCHEMA_DEFINITION,
        "src/users/users.controller.ts": FileTag.API_CONTRACT,
        "src/users/users.service.ts": FileTag.DATA_ACCESS,
        "src/users/users.repository.ts": FileTag.DATA_ACCESS,
        "src/users/dto/create-user.dto.ts": FileTag.API_CONTRACT,
        "src/users/users.module.ts": FileTag.API_CONTRACT,
        "migrations/1700000000000-AddEmailToUsers.ts": FileTag.MIGRATION,
        "Dockerfile": FileTag.INFRA,
        "docker-compose.yml": FileTag.INFRA,
        ".github/workflows/ci.yml": FileTag.CI_CONFIG,
        "README.md": FileTag.DOCS,
    }

    by_path = {r.path: r for r in results}

    # node_modules file should never appear in results at all
    assert "node_modules/express/index.js" not in by_path, "node_modules file was not skipped!"

    all_passed = True
    for path, expected_tag in expected.items():
        actual = by_path.get(path)
        if actual is None:
            print(f"  FAIL  {path} — missing from results entirely")
            all_passed = False
        elif actual.tag != expected_tag:
            print(f"  FAIL  {path} — expected {expected_tag.value}, got {actual.tag.value}")
            all_passed = False
        else:
            print(f"  PASS  {path:50s} -> {actual.tag.value:20s} (extractor hint: {actual.pending_extractor})")

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def test_dbt_classification():
    print("\n=== Test 2: dbt file classification ===")

    stack = StackProfile(
        language="sql",
        frameworks=["dbt"],
        orm="dbt",
        detected_from=["dbt_project.yml"],
    )

    files = [
        "models/finance/revenue.sql",
        "models/finance/schema.yml",
        "seeds/raw/users.sql",
        "migrations/001_create_users.sql",
    ]

    rule_set = get_rule_set(stack)
    results = classify_files(files, rule_set, content_fetcher=None)
    by_path = {r.path: r for r in results}

    expected = {
        "models/finance/revenue.sql": FileTag.DATA_ACCESS,
        "models/finance/schema.yml": FileTag.SCHEMA_DEFINITION,
        "seeds/raw/users.sql": FileTag.DATA_ACCESS,
        "migrations/001_create_users.sql": FileTag.MIGRATION,
    }

    all_passed = True
    for path, expected_tag in expected.items():
        actual = by_path.get(path)
        if actual is None or actual.tag != expected_tag:
            got = actual.tag.value if actual else "MISSING"
            print(f"  FAIL  {path} — expected {expected_tag.value}, got {got}")
            all_passed = False
        else:
            print(f"  PASS  {path:40s} -> {actual.tag.value}")

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def test_rule_set_resolution():
    print("\n=== Test 3: Stack registry resolution ===")

    cases = [
        (StackProfile(frameworks=["dbt"], orm="dbt"), "dbt"),
        (StackProfile(frameworks=["nestjs"], orm="typeorm"), "nestjs+typeorm"),
        (StackProfile(frameworks=["nextjs"], orm="prisma"), "nextjs+prisma"),
        (StackProfile(frameworks=["express"], orm="mongoose"), "react+express+mongo"),
        (StackProfile(frameworks=["flask"], orm=None), None),   # unrecognized combo
    ]

    all_passed = True
    for profile, expected_key in cases:
        actual_key = resolve_rule_set_key(profile)
        status = "PASS" if actual_key == expected_key else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  {status}  frameworks={profile.frameworks} orm={profile.orm} -> {actual_key} (expected {expected_key})")

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def test_validator_catches_bad_classification():
    print("\n=== Test 4: Validator catches high-UNKNOWN-ratio classification ===")

    # Case A: truly undetected stack (no signal files matched at all —
    # this is what detect_stack() returns when nothing recognized fires).
    # is_recognized should be False here since nothing was set.
    undetected_stack = StackProfile(detected_from=[])
    files = [
        "src/users/users.entity.ts",
        "src/users/users.controller.ts",
        "src/users/users.service.ts",
    ]
    rule_set = get_rule_set(undetected_stack)   # falls back to UNIVERSAL_RULES only
    classified = classify_files(files, rule_set, content_fetcher=None)

    result = RepoClassification(
        repo_full_name="test/bad-repo",
        stack_profile=undetected_stack,
        files=classified,
        total_files_scanned=len(files),
    )

    warnings = validate_classification(result)
    codes = {w.code for w in warnings}

    expected_codes = {"stack_unrecognized", "high_unknown_ratio", "no_extractable_files"}
    all_passed = expected_codes.issubset(codes)

    # Case B: a framework WAS detected (e.g. flask) but we have no rule
    # set for it — is_recognized is True (something was found), but the
    # registry still has no matching rule set, so files still come back
    # UNKNOWN. This proves the two failure modes are distinguishable:
    # "detected nothing" vs "detected something we don't support yet".
    detected_but_unsupported = StackProfile(frameworks=["flask"], detected_from=["requirements.txt:flask"])
    assert detected_but_unsupported.is_recognized is True, (
        "is_recognized should be True when a framework was found, "
        "even if we have no rule set for it"
    )
    unsupported_key = resolve_rule_set_key(detected_but_unsupported)
    assert unsupported_key is None, "flask has no rule set yet — should resolve to None"
    print(f"  PASS  detected_but_unsupported.is_recognized={detected_but_unsupported.is_recognized}, "
          f"resolve_rule_set_key={unsupported_key} (correctly distinguishes from fully-undetected case)")

    for w in warnings:
        print(f"  [{w.severity.upper()}] {w.code}: {w.message}")

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'} (expected codes: {expected_codes}, got: {codes})")
    return all_passed


if __name__ == "__main__":
    results = [
        test_nestjs_typeorm_classification(),
        test_dbt_classification(),
        test_rule_set_resolution(),
        test_validator_catches_bad_classification(),
    ]

    print("\n" + "=" * 60)
    if all(results):
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"FAILED: {results.count(False)}/{len(results)} test groups failed")
        sys.exit(1)
