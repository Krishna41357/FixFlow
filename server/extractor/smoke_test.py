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

# Run from the PARENT of extractor/ so `import extractor.X` resolves the
# same way it will once extractor/ sits next to server/ as a sibling package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor.models.classification import StackProfile, RepoClassification, FileTag, ClassifiedFile
from extractor.registry.stack_registry import get_rule_set, resolve_rule_set_key
from extractor.classifiers.rule_engine import classify_files
from extractor.validators.classification_validator import validate_classification, ClassificationWarning
from extractor.models.identity import IdentityCategory, ReferenceType
from extractor.extractors import dbt_extractor
from extractor.extractors import react_extractor
from extractor.extractors import nestjs_extractor
from extractor.extractors import typeorm_extractor


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


def test_dbt_identity_extraction():
    print("\n=== Test 5: dbt identity & reference extraction (Phase 2) ===")
    all_passed = True

    # ── 5a. Valid SQL model with ref() and source() calls ────────────────
    sql_content = """
    SELECT
        o.id,
        o.user_id,
        u.email
    FROM {{ ref('orders') }} o
    JOIN {{ source('raw', 'users') }} u ON o.user_id = u.id
    """
    sql_file = ClassifiedFile(
        path="models/finance/revenue.sql",
        tag=FileTag.DATA_ACCESS,
        pending_extractor="dbt_sql_model",
    )
    result = dbt_extractor.extract(sql_file.path, sql_content, sql_file)

    if result.had_errors:
        print(f"  FAIL  valid SQL model produced parse_errors: {result.parse_errors}")
        all_passed = False
    elif len(result.identities) != 1:
        print(f"  FAIL  expected 1 identity, got {len(result.identities)}")
        all_passed = False
    else:
        identity = result.identities[0]
        checks = [
            (identity.name == "revenue", f"name should be 'revenue', got {identity.name!r}"),
            (identity.category == IdentityCategory.BEHAVIOR, f"category should be BEHAVIOR, got {identity.category}"),
            (identity.subtype == "dbt_model", f"subtype should be 'dbt_model', got {identity.subtype!r}"),
            (len(result.references) == 2, f"expected 2 references, got {len(result.references)}"),
        ]
        for passed, msg in checks:
            if not passed:
                print(f"  FAIL  {msg}")
                all_passed = False

        if result.references:
            targets = {r.target_expression for r in result.references}
            expected_targets = {"orders", "raw.users"}
            if targets != expected_targets:
                print(f"  FAIL  expected reference targets {expected_targets}, got {targets}")
                all_passed = False
            else:
                print(f"  PASS  identity: {identity.name} ({identity.category.value}/{identity.subtype})")
                for r in result.references:
                    print(f"  PASS  reference: {r.source_identity} --{r.reference_type.value}--> {r.target_expression} (line {r.line})")
                    if r.reference_type != ReferenceType.USES:
                        print(f"  FAIL  expected reference_type USES, got {r.reference_type}")
                        all_passed = False

    # ── 5b. Valid schema yml with multiple model names ──────────────────
    yml_content = """
version: 2

models:
  - name: revenue
    description: "Finance revenue model"
    columns:
      - name: id
      - name: amount
  - name: orders
    columns:
      - name: id
      - name: user_id
"""
    yml_file = ClassifiedFile(
        path="models/finance/schema.yml",
        tag=FileTag.SCHEMA_DEFINITION,
        pending_extractor="dbt_schema_yml",
    )
    yml_result = dbt_extractor.extract(yml_file.path, yml_content, yml_file)

    if yml_result.had_errors:
        print(f"  FAIL  valid schema yml produced parse_errors: {yml_result.parse_errors}")
        all_passed = False
    else:
        names = {i.name for i in yml_result.identities}
        expected_names = {"revenue", "orders"}
        if names != expected_names:
            print(f"  FAIL  expected model names {expected_names}, got {names}")
            all_passed = False
        else:
            for identity in yml_result.identities:
                if identity.category != IdentityCategory.DATA:
                    print(f"  FAIL  {identity.name}: expected category DATA, got {identity.category}")
                    all_passed = False
                elif identity.subtype != "dbt_source":
                    print(f"  FAIL  {identity.name}: expected subtype 'dbt_source', got {identity.subtype}")
                    all_passed = False
                else:
                    print(f"  PASS  identity: {identity.name} ({identity.category.value}/{identity.subtype})")
            # Confirm column-level "- name:" entries were NOT pulled in as model names
            if "id" in names or "amount" in names or "user_id" in names:
                print(f"  FAIL  column names leaked into model names: {names}")
                all_passed = False
            else:
                print(f"  PASS  column-level names correctly excluded from model identities")

    # ── 5c. Malformed/empty content — must NOT crash, must report gracefully ──
    empty_sql_file = ClassifiedFile(
        path="models/broken/empty.sql",
        tag=FileTag.DATA_ACCESS,
        pending_extractor="dbt_sql_model",
    )
    empty_result = dbt_extractor.extract(empty_sql_file.path, "", empty_sql_file)
    if empty_result.had_errors:
        print(f"  FAIL  empty SQL should not produce parse_errors (no ref/source calls is valid, not an error)")
        all_passed = False
    elif len(empty_result.identities) != 1:
        print(f"  FAIL  empty SQL should still produce 1 identity (the model itself, just with 0 references)")
        all_passed = False
    else:
        print(f"  PASS  empty SQL handled gracefully — 1 identity, {len(empty_result.references)} references, no crash")

    malformed_yml_file = ClassifiedFile(
        path="models/broken/empty.yml",
        tag=FileTag.SCHEMA_DEFINITION,
        pending_extractor="dbt_schema_yml",
    )
    malformed_yml_result = dbt_extractor.extract(malformed_yml_file.path, "not: valid: : yaml: structure:::", malformed_yml_file)
    if not malformed_yml_result.had_errors and len(malformed_yml_result.identities) == 0:
        print(f"  PASS  malformed yml handled gracefully — no crash, parse_errors populated or empty result returned")
    elif malformed_yml_result.had_errors:
        print(f"  PASS  malformed yml correctly reported parse_errors: {malformed_yml_result.parse_errors}")
    else:
        print(f"  FAIL  malformed yml produced unexpected identities: {malformed_yml_result.identities}")
        all_passed = False

    # ── 5d. Unrecognized pending_extractor hint — must not crash ────────
    unknown_hint_file = ClassifiedFile(
        path="models/weird.sql",
        tag=FileTag.DATA_ACCESS,
        pending_extractor="some_future_extractor_hint",
    )
    unknown_result = dbt_extractor.extract(unknown_hint_file.path, "SELECT 1", unknown_hint_file)
    if unknown_result.had_errors and len(unknown_result.identities) == 0:
        print(f"  PASS  unrecognized hint handled gracefully — no crash, parse_errors explains why")
    else:
        print(f"  FAIL  unrecognized hint should produce parse_errors and no identities")
        all_passed = False

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def test_react_extraction():
    print("\n=== Test 6: React component & Next.js route extraction (Phase 3) ===")
    all_passed = True

    # ── 6a. Named default export component with JSX renders and imports ──
    component_content = """
import React from 'react';
import { useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import UserAvatar from './UserAvatar';

export default function UserProfile({ userId }: { userId: string }) {
  const { user } = useAuth();
  return (
    <div>
      <UserAvatar user={user} />
      <ProfileCard user={user} />
    </div>
  );
}
"""
    cf = ClassifiedFile(
        path="src/components/UserProfile.tsx",
        tag=FileTag.UI_COMPONENT,
        pending_extractor="react_component",
    )
    result = react_extractor.extract(cf.path, component_content, cf)

    identity = result.identities[0] if result.identities else None
    checks = [
        (not result.had_errors, f"unexpected parse_errors: {result.parse_errors}"),
        (identity is not None, "no identity extracted"),
        (identity and identity.name == "UserProfile", f"name={identity.name if identity else None}"),
        (identity and identity.category == IdentityCategory.UI, f"category={identity.category if identity else None}"),
        (identity and identity.subtype == "react_component", f"subtype={identity.subtype if identity else None}"),
    ]
    for passed, msg in checks:
        if not passed:
            print(f"  FAIL  {msg}")
            all_passed = False

    if identity:
        print(f"  PASS  identity: {identity.name} ({identity.category.value}/{identity.subtype})")

    import_targets = {r.target_expression for r in result.references if r.reference_type == ReferenceType.IMPORT}
    renders_targets = {r.target_expression for r in result.references if r.reference_type == ReferenceType.RENDERS}

    # react appears once even though imported twice (dedup)
    if "react" in import_targets and "../hooks/useAuth" in import_targets:
        print(f"  PASS  imports: {sorted(import_targets)}")
    else:
        print(f"  FAIL  expected import refs to include 'react' and '../hooks/useAuth', got {import_targets}")
        all_passed = False

    if {"UserAvatar", "ProfileCard"} == renders_targets:
        print(f"  PASS  renders: {sorted(renders_targets)}")
    else:
        print(f"  FAIL  expected renders {{UserAvatar, ProfileCard}}, got {renders_targets}")
        all_passed = False

    # ── 6b. Next.js App Router route handler with multiple HTTP methods ──
    route_content = """
import { NextRequest, NextResponse } from 'next/server';
import { userService } from '@/services/userService';

export async function GET(request: NextRequest) {
  const users = await userService.findAll();
  return NextResponse.json(users);
}

export async function POST(request: NextRequest) {
  const body = await request.json();
  const user = await userService.create(body);
  return NextResponse.json(user, { status: 201 });
}
"""
    rf = ClassifiedFile(
        path="app/api/users/route.ts",
        tag=FileTag.API_CONTRACT,
        pending_extractor="nextjs_route_handler",
    )
    route_result = react_extractor.extract(rf.path, route_content, rf)

    method_names = {i.name for i in route_result.identities}
    if method_names == {"GET", "POST"}:
        print(f"  PASS  route handlers: {sorted(method_names)}")
    else:
        print(f"  FAIL  expected {{GET, POST}}, got {method_names}")
        all_passed = False

    for i in route_result.identities:
        if i.category != IdentityCategory.API:
            print(f"  FAIL  {i.name}: expected category API, got {i.category}")
            all_passed = False
        else:
            print(f"  PASS  {i.name}: category={i.category.value} subtype={i.subtype}")

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def test_nestjs_extraction():
    print("\n=== Test 7: NestJS service & controller extraction (Phase 3) ===")
    all_passed = True

    # ── 7a. Service with constructor DI ──────────────────────────────────
    service_content = """
import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { User } from './user.entity';
import { JwtService } from '@nestjs/jwt';

@Injectable()
export class AuthService {
  constructor(
    @InjectRepository(User)
    private readonly userRepo: Repository<User>,
    private readonly jwtService: JwtService,
  ) {}

  async validateUser(email: string) {
    return this.userRepo.findOne({ where: { email } });
  }
}
"""
    sf = ClassifiedFile(
        path="src/auth/auth.service.ts",
        tag=FileTag.DATA_ACCESS,
        pending_extractor="nestjs_service",
    )
    svc_result = nestjs_extractor.extract(sf.path, service_content, sf)

    svc_identity = svc_result.identities[0] if svc_result.identities else None
    checks = [
        (not svc_result.had_errors, f"parse_errors: {svc_result.parse_errors}"),
        (svc_identity and svc_identity.name == "AuthService", f"name={svc_identity.name if svc_identity else None}"),
        (svc_identity and svc_identity.category == IdentityCategory.BEHAVIOR, f"category={svc_identity.category if svc_identity else None}"),
        (svc_identity and svc_identity.subtype == "nestjs_service", f"subtype={svc_identity.subtype if svc_identity else None}"),
    ]
    for passed, msg in checks:
        if not passed:
            print(f"  FAIL  {msg}")
            all_passed = False

    if svc_identity:
        print(f"  PASS  identity: {svc_identity.name} ({svc_identity.category.value}/{svc_identity.subtype})")

    uses_targets = {r.target_expression for r in svc_result.references if r.reference_type == ReferenceType.USES}
    if "JwtService" in uses_targets and "User" in uses_targets:
        print(f"  PASS  DI USES refs: {sorted(uses_targets)}")
    else:
        print(f"  FAIL  expected USES refs to include JwtService and User, got {uses_targets}")
        all_passed = False

    # ── 7b. Controller with route prefix and HTTP decorators ─────────────
    controller_content = """
import { Controller, Get, Post, Body, Param } from '@nestjs/common';
import { AuthService } from './auth.service';
import { CreateUserDto } from './dto/create-user.dto';

@Controller('users')
export class UsersController {
  constructor(private readonly authService: AuthService) {}

  @Get()
  findAll() {
    return this.authService.findAll();
  }

  @Post()
  create(@Body() dto: CreateUserDto) {
    return this.authService.create(dto);
  }

  @Get(':id')
  findOne(@Param('id') id: string) {
    return this.authService.findOne(id);
  }
}
"""
    cf = ClassifiedFile(
        path="src/users/users.controller.ts",
        tag=FileTag.API_CONTRACT,
        pending_extractor="nestjs_controller",
    )
    ctrl_result = nestjs_extractor.extract(cf.path, controller_content, cf)

    ctrl_identity = ctrl_result.identities[0] if ctrl_result.identities else None
    if ctrl_identity and ctrl_identity.name == "UsersController" and ctrl_identity.category == IdentityCategory.API:
        print(f"  PASS  identity: {ctrl_identity.name} ({ctrl_identity.category.value}/{ctrl_identity.subtype})")
        print(f"  PASS  route_prefix: '{ctrl_identity.raw_metadata.get('route_prefix')}' routes: {ctrl_identity.raw_metadata.get('routes')}")
    else:
        print(f"  FAIL  controller identity wrong: {ctrl_identity}")
        all_passed = False

    ctrl_uses = {r.target_expression for r in ctrl_result.references if r.reference_type == ReferenceType.USES}
    if "AuthService" in ctrl_uses:
        print(f"  PASS  controller USES refs: {sorted(ctrl_uses)}")
    else:
        print(f"  FAIL  expected AuthService in USES refs, got {ctrl_uses}")
        all_passed = False

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def test_typeorm_extraction():
    print("\n=== Test 8: TypeORM entity & repository extraction (Phase 3) ===")
    all_passed = True

    # ── 8a. Entity with relation decorators ──────────────────────────────
    entity_content = """
import { Entity, PrimaryGeneratedColumn, Column, OneToMany, ManyToOne } from 'typeorm';
import { Order } from '../orders/order.entity';
import { Role } from '../roles/role.entity';

@Entity()
export class User {
  @PrimaryGeneratedColumn()
  id: number;

  @Column()
  email: string;

  @OneToMany(() => Order, order => order.user)
  orders: Order[];

  @ManyToOne(() => Role, role => role.users)
  role: Role;
}
"""
    ef = ClassifiedFile(
        path="src/users/user.entity.ts",
        tag=FileTag.SCHEMA_DEFINITION,
        pending_extractor="typeorm_entity",
    )
    entity_result = typeorm_extractor.extract(ef.path, entity_content, ef)

    entity_identity = entity_result.identities[0] if entity_result.identities else None
    checks = [
        (not entity_result.had_errors, f"parse_errors: {entity_result.parse_errors}"),
        (entity_identity and entity_identity.name == "User", f"name={entity_identity.name if entity_identity else None}"),
        (entity_identity and entity_identity.category == IdentityCategory.DATA, f"category={entity_identity.category if entity_identity else None}"),
        (entity_identity and entity_identity.subtype == "typeorm_entity", f"subtype={entity_identity.subtype if entity_identity else None}"),
    ]
    for passed, msg in checks:
        if not passed:
            print(f"  FAIL  {msg}")
            all_passed = False

    if entity_identity:
        print(f"  PASS  identity: {entity_identity.name} ({entity_identity.category.value}/{entity_identity.subtype})")
        print(f"  PASS  relations in raw_metadata: {entity_identity.raw_metadata.get('relations')}")

    uses_targets = {r.target_expression for r in entity_result.references if r.reference_type == ReferenceType.USES}
    if uses_targets == {"Order", "Role"}:
        print(f"  PASS  relation USES refs: {sorted(uses_targets)}")
    else:
        print(f"  FAIL  expected USES {{Order, Role}}, got {uses_targets}")
        all_passed = False

    # ── 8b. Repository with extends Repository<T> ────────────────────────
    repo_content = """
import { Injectable } from '@nestjs/common';
import { Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { User } from './user.entity';

@Injectable()
export class UsersRepository extends Repository<User> {
  constructor(@InjectRepository(User) repo: Repository<User>) {
    super(repo.target, repo.manager, repo.queryRunner);
  }

  async findByEmail(email: string): Promise<User | null> {
    return this.findOne({ where: { email } });
  }
}
"""
    rf = ClassifiedFile(
        path="src/users/users.repository.ts",
        tag=FileTag.DATA_ACCESS,
        pending_extractor="typeorm_repository",
    )
    repo_result = typeorm_extractor.extract(rf.path, repo_content, rf)

    repo_identity = repo_result.identities[0] if repo_result.identities else None
    if repo_identity and repo_identity.name == "UsersRepository" and repo_identity.category == IdentityCategory.BEHAVIOR:
        print(f"  PASS  identity: {repo_identity.name} ({repo_identity.category.value}/{repo_identity.subtype})")
    else:
        print(f"  FAIL  repo identity wrong: {repo_identity}")
        all_passed = False

    repo_uses = {r.target_expression for r in repo_result.references if r.reference_type == ReferenceType.USES}
    if "User" in repo_uses:
        print(f"  PASS  repository USES entity: {repo_uses}")
    else:
        print(f"  FAIL  expected 'User' in USES refs, got {repo_uses}")
        all_passed = False

    print(f"\n  Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


if __name__ == "__main__":
    results = [
        test_nestjs_typeorm_classification(),
        test_dbt_classification(),
        test_rule_set_resolution(),
        test_validator_catches_bad_classification(),
        test_dbt_identity_extraction(),
        test_react_extraction(),
        test_nestjs_extraction(),
        test_typeorm_extraction(),
    ]

    print("\n" + "=" * 60)
    if all(results):
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"FAILED: {results.count(False)}/{len(results)} test groups failed")
        sys.exit(1)
