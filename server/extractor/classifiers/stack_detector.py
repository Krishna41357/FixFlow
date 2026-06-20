"""
stack_detector.py — Detects a repo's StackProfile from manifest files.

Reads root-level signal files (package.json, dbt_project.yml,
requirements.txt) the same way repo_parser_controller.py's
_fetch_file_content fetches individual files — same GitHub Contents
API, same decode pattern, same graceful-None-on-failure handling.

This runs ONCE per repo (cached by the caller, same TTL idea as
repo_parser_controller.py's graph cache) — not on every PR.

Detection priority, most confident first:
  1. dbt_project.yml present              → dbt, no further detection needed
  2. package.json dependencies            → language=typescript/javascript,
                                             framework(s) + orm from dep names
  3. requirements.txt / pyproject.toml    → language=python (not yet wired
                                             to a rule set — see stack_rules.py)
  4. nothing recognized                   → StackProfile.is_recognized == False
"""

import json
import base64
from typing import Optional, List

import requests

from models.classification import StackProfile

GITHUB_API_TIMEOUT = 15

# package.json dependency name → (framework, orm) hints.
# Order doesn't matter here; multiple can match (e.g. both nestjs AND
# typeorm appear in the same dependencies block).
_FRAMEWORK_DEPS = {
    "next": "nextjs",
    "react": "react",          # only meaningful combined with another signal
    "express": "express",
    "@nestjs/core": "nestjs",
    "vue": "vue",
}

_ORM_DEPS = {
    "typeorm": "typeorm",
    "prisma": "prisma",
    "@prisma/client": "prisma",
    "mongoose": "mongoose",
    "sequelize": "sequelize",
}

_DATABASE_HINTS = {
    "pg": "postgres",
    "mysql": "mysql",
    "mysql2": "mysql",
    "mongoose": "mongodb",
    "mongodb": "mongodb",
    "sqlite3": "sqlite",
}


def _fetch_root_file(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    file_path: str,
) -> Optional[str]:
    """
    Fetches a single root-level file's content via GitHub Contents API.
    Mirrors repo_parser_controller.py's _fetch_file_content exactly —
    same headers, same timeout, same decode handling — kept as a local
    copy here rather than importing across modules, since extractor/
    is meant to be a fully standalone module per the user's design call.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        resp = requests.get(url, headers=headers, timeout=GITHUB_API_TIMEOUT)

        if resp.status_code != 200:
            return None

        data = resp.json()
        raw_content = data.get("content", "")
        if not raw_content:
            return None

        return base64.b64decode(raw_content.replace("\n", "")).decode("utf-8", errors="replace")

    except Exception as e:
        print(f"WARNING _fetch_root_file: {e} for {file_path}")
        return None


def _detect_from_package_json(content: str) -> StackProfile:
    """
    Parses package.json dependencies + devDependencies to detect
    frameworks, ORM, and implied database.
    """
    frameworks: List[str] = []
    orm: Optional[str] = None
    database: Optional[str] = None
    detected_from: List[str] = []

    try:
        data = json.loads(content)
    except Exception as e:
        print(f"WARNING _detect_from_package_json: failed to parse JSON: {e}")
        return StackProfile(language="typescript", detected_from=["package.json (unparseable)"])

    all_deps = {}
    all_deps.update(data.get("dependencies", {}))
    all_deps.update(data.get("devDependencies", {}))

    for dep_name, framework in _FRAMEWORK_DEPS.items():
        if dep_name in all_deps:
            frameworks.append(framework)
            detected_from.append(f"package.json:dependencies.{dep_name}")

    for dep_name, orm_name in _ORM_DEPS.items():
        if dep_name in all_deps:
            orm = orm_name
            detected_from.append(f"package.json:dependencies.{dep_name}")
            break   # first ORM match wins — repos rarely mix two ORMs

    for dep_name, db_name in _DATABASE_HINTS.items():
        if dep_name in all_deps:
            database = db_name
            detected_from.append(f"package.json:dependencies.{dep_name}")
            break

    # "react" alone (without next) is a framework signal only when
    # paired with express — otherwise it's noise (next.js also depends
    # transitively on react-adjacent tooling in some setups).
    if "react" in frameworks and "nextjs" in frameworks:
        frameworks.remove("react")   # nextjs implies react, avoid double-counting

    language = "typescript" if "typescript" in all_deps else "javascript"

    return StackProfile(
        language=language,
        frameworks=frameworks,
        orm=orm,
        database=database,
        detected_from=detected_from,
    )


def detect_stack(
    github_token: str,
    repo_owner: str,
    repo_name: str,
) -> StackProfile:
    """
    Public entrypoint. Tries each signal file in priority order,
    returns the first confident match. Falls through to an
    unrecognized StackProfile if nothing matches — callers should
    check .is_recognized before trusting the result.
    """
    # ── 1. dbt — most specific, checked first ────────────────────────────
    dbt_content = _fetch_root_file(github_token, repo_owner, repo_name, "dbt_project.yml")
    if dbt_content is not None:
        print(f"DEBUG detect_stack: dbt_project.yml found for {repo_owner}/{repo_name}")
        return StackProfile(
            language="sql",
            frameworks=["dbt"],
            orm="dbt",
            database=None,   # dbt is database-agnostic at the project level
            detected_from=["dbt_project.yml"],
        )

    # ── 2. package.json — covers NestJS, Next.js, Express, React ────────
    package_json = _fetch_root_file(github_token, repo_owner, repo_name, "package.json")
    if package_json is not None:
        profile = _detect_from_package_json(package_json)
        print(
            f"DEBUG detect_stack: package.json detected for {repo_owner}/{repo_name} — "
            f"frameworks={profile.frameworks} orm={profile.orm} database={profile.database}"
        )
        return profile

    # ── 3. Python signal files — language only for now ──────────────────
    requirements = _fetch_root_file(github_token, repo_owner, repo_name, "requirements.txt")
    if requirements is not None:
        print(f"DEBUG detect_stack: requirements.txt found for {repo_owner}/{repo_name}")
        return StackProfile(
            language="python",
            detected_from=["requirements.txt"],
        )

    pyproject = _fetch_root_file(github_token, repo_owner, repo_name, "pyproject.toml")
    if pyproject is not None:
        print(f"DEBUG detect_stack: pyproject.toml found for {repo_owner}/{repo_name}")
        return StackProfile(
            language="python",
            detected_from=["pyproject.toml"],
        )

    # ── 4. nothing recognized ────────────────────────────────────────────
    print(f"WARNING detect_stack: no recognized manifest for {repo_owner}/{repo_name}")
    return StackProfile(detected_from=[])
