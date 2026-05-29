"""
repo_parser_controller.py — Self-contained repo-based lineage graph for Pipeline Autopsy.

This controller is COMPLETELY STANDALONE. It has zero imports from:
  - openmetadata_controller
  - lineage_controller
  - Any OpenMetadata-related code

It builds a full lineage graph by reading SQL and dbt yml files directly
from the GitHub repo, stores the graph in MongoDB (persistent) and Redis
(hot cache), and exposes a LineageSubgraph-compatible interface so the
existing investigation flow needs only a minimal surgical change.

Supports TWO file types in the same repo:
  - dbt models/seeds/snapshots  → ref() / source() dependency parsing
  - raw SQL migrations           → REFERENCES / FROM / JOIN dependency parsing
  Both node types are unified in the same graph and cross-linked via a
  table_name → migration node lookup built in _populate_referenced_by.

Function organisation:
  ── Data structures ───────────────────────────────────────────────────────────
  ColumnUsage, RepoLineageNode, RepoLineageGraph — internal graph models
  ContractViolation                              — a confirmed pre-merge breakage

  ── GitHub API layer ──────────────────────────────────────────────────────────
  _get_repo_file_tree        — fetch flat file list from GitHub tree API
  _fetch_file_content        — fetch and decode a single file via Contents API

  ── Parsing layer ─────────────────────────────────────────────────────────────
  _derive_fqn_from_path           — file path → FQN
  _parse_ref_and_source_calls     — extract ref() and source() from dbt SQL
  _extract_defined_table          — extract CREATE TABLE name from migration SQL
  _extract_migration_columns      — extract column names from CREATE TABLE body
  _parse_table_references         — extract REFERENCES/FROM/JOIN from migration SQL
  _parse_column_usage             — extract alias.column patterns from SQL clauses
  _parse_yml_columns              — extract column names from dbt schema yml
  _filter_all_files               — split file tree into dbt_sql / migration_sql / yml

  ── Graph build layer ─────────────────────────────────────────────────────────
  _build_nodes_from_dbt_sql       — build dbt nodes with ref/source depends_on
  _build_nodes_from_migrations    — build migration nodes with table depends_on
  _enrich_nodes_with_yml          — add column lists from schema ymls
  _populate_referenced_by         — invert edges → referenced_by (cross-type aware)
  _populate_column_usage          — column-level dependency tracking

  ── Contract validation layer ─────────────────────────────────────────────────
  validate_contracts              — runs all checks, returns List[ContractViolation]
  _check_column_drops             — downstream nodes referencing dropped/renamed cols
  _check_fk_column_existence      — FK REFERENCES pointing to non-existent columns
  _check_migration_ordering       — migration N depends on something only N+k defines
  _check_source_yml_drift         — dbt sources.yml columns vs migration column list

  ── Storage layer ─────────────────────────────────────────────────────────────
  _graph_to_mongo_doc        — serialize graph for MongoDB
  _mongo_doc_to_graph        — deserialize MongoDB doc → graph
  _save_graph_to_mongo       — upsert graph to repo_lineage_graphs collection
  _save_graph_to_redis       — write graph to Redis with TTL
  _load_graph_from_redis     — load graph from Redis (None on miss)
  _load_graph_from_mongo     — load graph from MongoDB (None if stale)

  ── Public API ────────────────────────────────────────────────────────────────
  scan_repo                  — full repo scan, builds and stores graph
  get_repo_graph             — Redis → MongoDB with fallback chain
  get_downstream             — BFS traversal of referenced_by edges
  get_column_dependents      — find downstream nodes that use changed columns
  build_subgraph_from_graph  — adapter: RepoLineageGraph → LineageSubgraph
  update_graph_nodes         — incremental update after PR merge
"""

import os
import re
import json
import base64
import requests
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ── Environment ───────────────────────────────────────────────────────────────

MONGO_URI             = os.getenv("MONGO_URI")
REDIS_URL             = os.getenv("REDIS_URL", "redis://localhost:6379")
GRAPH_CACHE_TTL_HOURS = int(os.getenv("GRAPH_CACHE_TTL_HOURS", "168"))  # 1 week default
GITHUB_API_TIMEOUT    = 15

if not MONGO_URI:
    raise RuntimeError("MONGO_URI not set in environment")

_mongo_client = MongoClient(MONGO_URI)
_db           = _mongo_client["rag_database"]
_graphs_col   = _db["repo_lineage_graphs"]

# Redis client — optional, graceful degradation if unavailable
_redis_client = None
try:
    import redis as redis_lib
    _redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _redis_client.ping()
    print("DEBUG repo_parser: Redis connected")
except Exception as _re:
    print(f"WARNING repo_parser: Redis unavailable ({_re}) — falling back to MongoDB only")
    _redis_client = None

# dbt top-level directories that contain data assets
_DBT_DIRS = ("models/", "seeds/", "snapshots/")

# dbt top-level directory names (for FQN stripping)
_DBT_TOP_DIRS = {"models", "seeds", "snapshots", "analyses", "macros"}

# raw migration directories
_MIGRATION_DIRS = ("migrations/",)

# node type constants
NODE_TYPE_DBT       = "dbt"
NODE_TYPE_MIGRATION = "migration"


# ══════════════════════════════════════════════════════════════════════════════
# Data Structures
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ColumnUsage:
    """
    Tracks how one column from an upstream asset is used in a downstream SQL file.
    Special column name '*' means a SELECT * wildcard was detected.
    """
    column:         str
    used_in_select: bool = False
    used_in_where:  bool = False
    used_in_join:   bool = False


@dataclass
class RepoLineageNode:
    """
    One node in the repo lineage graph — one SQL model, seed, or migration file.

    node_type     — "dbt" or "migration"
    depends_on    — FQNs this node reads from (ref/source calls or table references)
    referenced_by — FQNs that read from this node (populated in second pass)
    column_usage  — {upstream_fqn: [ColumnUsage]} from SELECT/WHERE/JOIN analysis
    columns       — column names defined in schema yml or extracted from CREATE TABLE
    raw_metadata  — extra per-type data:
                    dbt:       {}
                    migration: {"defined_table": "users"}  ← for cross-type bridge
    """
    fqn:           str
    file_path:     str
    node_type:     str                                     = NODE_TYPE_DBT
    sql:           str                                     = ""
    columns:       List[str]                               = field(default_factory=list)
    depends_on:    List[str]                               = field(default_factory=list)
    referenced_by: List[str]                               = field(default_factory=list)
    column_usage:  Dict[str, List[ColumnUsage]]            = field(default_factory=dict)
    raw_metadata:  Dict[str, Any]                          = field(default_factory=dict)


@dataclass
class RepoLineageGraph:
    """
    Full lineage graph for one GitHub repo.
    Stored in MongoDB keyed by repo_full_name.
    Cached in Redis with TTL.
    """
    repo_full_name:      str
    connection_id:       str
    user_id:             str
    built_at:            str                               # ISO datetime UTC
    nodes:               Dict[str, RepoLineageNode]        = field(default_factory=dict)
    total_files_scanned: int                               = 0
    total_nodes:         int                               = 0


@dataclass
class ContractViolation:
    """
    A confirmed dependency breakage detected by static analysis before merge.

    violation_type — category of the break:
      column_dropped        — a column used downstream no longer exists in the changed asset
      column_renamed        — a column was renamed; downstream still references the old name
      fk_column_missing     — FOREIGN KEY REFERENCES a column that doesn't exist in the target
      migration_order       — migration N depends on a table only created in migration N+k
      source_yml_drift      — dbt sources.yml declares a column not present in the migration

    changed_fqn      — the asset being changed in this PR (the source of the break)
    affected_fqn     — the downstream asset that will break
    column           — the specific column involved (None for table-level violations)
    detail           — human-readable description of exactly what is wrong
    severity         — "critical" | "high" | "medium" | "low"
                       critical: runtime failure guaranteed
                       high:     very likely failure
                       medium:   possible failure depending on data
                       low:      schema drift, no immediate runtime impact
    file_path        — file that needs to be fixed (affected asset's file)
    fix_hint         — concrete suggestion for resolving the violation
    """
    violation_type: str
    changed_fqn:    str
    affected_fqn:   str
    column:         Optional[str]
    detail:         str
    severity:       str
    file_path:      str
    fix_hint:       str


# ══════════════════════════════════════════════════════════════════════════════
# GitHub API Layer
# ══════════════════════════════════════════════════════════════════════════════

def _get_repo_file_tree(
    github_token: str,
    repo_owner: str,
    repo_name: str
) -> List[dict]:
    """
    Fetches the full recursive file tree for the default branch.
    Uses GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1

    Returns list of tree entries:
      [{"path": "models/finance/revenue.sql", "type": "blob", ...}, ...]

    Returns [] on any failure — callers must handle empty list.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/trees/HEAD"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"recursive": "1"}

        resp = requests.get(url, headers=headers, params=params, timeout=GITHUB_API_TIMEOUT)

        if resp.status_code == 200:
            data = resp.json()
            tree = [entry for entry in data.get("tree", []) if entry.get("type") == "blob"]
            print(f"DEBUG _get_repo_file_tree: Found {len(tree)} files in {repo_owner}/{repo_name}")
            return tree
        else:
            print(f"ERROR _get_repo_file_tree: Status {resp.status_code} — {resp.text[:200]}")
            return []

    except Exception as e:
        print(f"ERROR _get_repo_file_tree: {e}")
        return []


def _fetch_file_content(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    file_path: str
) -> Optional[str]:
    """
    Fetches and decodes the content of one file via GitHub Contents API.
    GET /repos/{owner}/{repo}/contents/{path}

    Returns decoded string content or None on failure.
    Handles 404, rate limits (403), and encoding errors gracefully.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        resp = requests.get(url, headers=headers, timeout=GITHUB_API_TIMEOUT)

        if resp.status_code == 404:
            print(f"DEBUG _fetch_file_content: Not found: {file_path}")
            return None

        if resp.status_code == 403:
            print(f"WARNING _fetch_file_content: Rate limited or forbidden for {file_path}")
            return None

        if resp.status_code != 200:
            print(f"ERROR _fetch_file_content: Status {resp.status_code} for {file_path}")
            return None

        data        = resp.json()
        raw_content = data.get("content", "")

        if not raw_content:
            return None

        decoded = base64.b64decode(raw_content.replace("\n", "")).decode("utf-8", errors="replace")
        return decoded

    except Exception as e:
        print(f"ERROR _fetch_file_content: {e} for {file_path}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Parsing Layer
# ══════════════════════════════════════════════════════════════════════════════

def _derive_fqn_from_path(file_path: str) -> str:
    """
    Derives a dot-separated FQN from any supported file path.

    dbt paths — strip the top-level dir:
      models/finance/revenue.sql   → finance.revenue
      seeds/raw/users.sql          → raw.users
      snapshots/finance/orders.sql → finance.orders

    migration paths — keep the migrations prefix:
      migrations/001_users.sql      → migrations.001_users
      migrations/homepage-theme.sql → migrations.homepage-theme
    """
    without_ext = file_path.removesuffix(".sql").removesuffix(".yml").removesuffix(".yaml")
    parts = without_ext.replace("\\", "/").split("/")

    # Strip dbt top-level dirs but KEEP migrations/ as the first FQN segment
    if parts and parts[0] in _DBT_TOP_DIRS:
        parts = parts[1:]

    return ".".join(parts) if parts else without_ext


def _parse_ref_and_source_calls(sql_content: str) -> List[str]:
    """
    Extracts all dbt dependency name segments from dbt SQL content.

    Handles:
      {{ ref('model_name') }}           → "model_name"
      {{ ref("model_name") }}           → "model_name"
      {{ source('schema', 'table') }}   → "schema.table"

    Returns deduplicated list preserving first-seen order.
    """
    results = []
    seen    = set()

    ref_pattern = re.compile(
        r'\{\{-?\s*ref\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*-?\}\}',
        re.IGNORECASE
    )
    source_pattern = re.compile(
        r'\{\{-?\s*source\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]\s*\)\s*-?\}\}',
        re.IGNORECASE
    )

    for match in ref_pattern.finditer(sql_content):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            results.append(name)

    for match in source_pattern.finditer(sql_content):
        schema = match.group(1).strip()
        table  = match.group(2).strip()
        fqn    = f"{schema}.{table}"
        if fqn not in seen:
            seen.add(fqn)
            results.append(fqn)

    return results


def _extract_defined_table(sql_content: str) -> Optional[str]:
    """
    Extracts the table name this migration file DEFINES via CREATE TABLE.
    Returns lowercase table name or None if no CREATE TABLE found.

    migrations/001_users.sql  (CREATE TABLE users ...)  → "users"
    migrations/001_orders.sql (CREATE TABLE orders ...) → "orders"
    """
    match = re.search(
        r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
        sql_content,
        re.IGNORECASE
    )
    return match.group(1).lower() if match else None


def _extract_migration_columns(sql_content: str) -> List[str]:
    """
    Extracts column names from a CREATE TABLE statement in a migration file.
    Also captures columns added via ALTER TABLE ... ADD COLUMN statements.

    Skips constraint lines (PRIMARY KEY, FOREIGN KEY, INDEX, UNIQUE, CHECK).
    Returns list of column names in definition order, lowercased.
    """
    columns: List[str] = []
    seen:    set        = set()

    # ── CREATE TABLE columns ──────────────────────────────────────────────────
    body_match = re.search(
        r'CREATE\s+TABLE[^(]+\((.+?)\)(?:\s*;|\s*$)',
        sql_content,
        re.IGNORECASE | re.DOTALL
    )
    if body_match:
        skip = re.compile(
            r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY)\b',
            re.IGNORECASE
        )
        for line in body_match.group(1).splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped or skip.match(stripped):
                continue
            col_match = re.match(r'^(\w+)\s+\w+', stripped)
            if col_match:
                col = col_match.group(1).lower()
                if col not in seen:
                    seen.add(col)
                    columns.append(col)

    # ── ALTER TABLE ADD COLUMN ────────────────────────────────────────────────
    alter_pattern = re.compile(
        r'\bALTER\s+TABLE\s+\w+\s+ADD\s+(?:COLUMN\s+)?(\w+)\s+\w+',
        re.IGNORECASE
    )
    for match in alter_pattern.finditer(sql_content):
        col = match.group(1).lower()
        if col not in seen:
            seen.add(col)
            columns.append(col)

    return columns


def _parse_table_references(sql_content: str) -> List[str]:
    """
    Extracts table names a migration file REFERENCES (not the one it defines).

    Handles:
      FOREIGN KEY (user_id) REFERENCES users(id)  → "users"
      FROM users                                   → "users"
      JOIN orders ON ...                           → "orders"
      INSERT INTO cart ...                         → "cart"

    Excludes the table being CREATEd in this same file to avoid self-loops.
    Returns deduplicated lowercase list.
    """
    defined = _extract_defined_table(sql_content)
    results = []
    seen    = set()

    patterns = [
        re.compile(r'\bREFERENCES\s+(\w+)\s*\(', re.IGNORECASE),
        re.compile(r'\bFROM\s+(\w+)\b',          re.IGNORECASE),
        re.compile(r'\bJOIN\s+(\w+)\b',           re.IGNORECASE),
        re.compile(r'\bINSERT\s+INTO\s+(\w+)\b',  re.IGNORECASE),
    ]

    for pattern in patterns:
        for match in pattern.finditer(sql_content):
            name = match.group(1).strip().lower()
            if name and name != defined and name not in seen:
                seen.add(name)
                results.append(name)

    return results


def _parse_fk_references(sql_content: str) -> List[Tuple[str, str]]:
    """
    Extracts explicit FOREIGN KEY column references from migration SQL.

    Handles:
      FOREIGN KEY (user_id) REFERENCES users(id)
      user_id UUID REFERENCES users(id)

    Returns list of (referenced_table, referenced_column) tuples, lowercased.
    Used by _check_fk_column_existence to validate the referenced column exists.
    """
    results: List[Tuple[str, str]] = []

    # FOREIGN KEY (col) REFERENCES table(col)
    fk_block = re.compile(
        r'FOREIGN\s+KEY\s*\([^)]+\)\s*REFERENCES\s+(\w+)\s*\(([^)]+)\)',
        re.IGNORECASE
    )
    # inline: col_name TYPE REFERENCES table(col)
    inline_fk = re.compile(
        r'\b\w+\s+\w+.*?REFERENCES\s+(\w+)\s*\((\w+)\)',
        re.IGNORECASE
    )

    for match in fk_block.finditer(sql_content):
        table = match.group(1).strip().lower()
        cols  = [c.strip().lower() for c in match.group(2).split(",")]
        for col in cols:
            results.append((table, col))

    for match in inline_fk.finditer(sql_content):
        table = match.group(1).strip().lower()
        col   = match.group(2).strip().lower()
        if (table, col) not in results:
            results.append((table, col))

    return results


def _get_migration_sequence_number(fqn: str) -> Optional[int]:
    """
    Extracts the numeric prefix from a migration FQN for ordering checks.

    migrations.001_users   → 1
    migrations.042_orders  → 42
    migrations.rate_limits → None (no sequence number)
    """
    last_segment = fqn.split(".")[-1]
    match = re.match(r'^(\d+)', last_segment)
    return int(match.group(1)) if match else None


def _parse_column_usage(
    sql_content: str,
    upstream_fqns: List[str]
) -> Dict[str, List[ColumnUsage]]:
    """
    Parses SQL to determine which columns from each upstream asset are used.

    Strategy:
      1. Build alias → upstream_fqn map from FROM/JOIN clauses
         (handles both dbt {{ ref() }} style and bare table name aliases)
      2. For each alias, find alias.column_name in SELECT, WHERE, JOIN ON clauses
      3. Handle SELECT * and alias.* as wildcards
      4. Return {upstream_fqn: [ColumnUsage(...)]}

    Unparseable SQL (heavy macros, dynamic columns) returns {} — not an error.
    """
    if not sql_content or not upstream_fqns:
        return {}

    result: Dict[str, List[ColumnUsage]] = {}
    alias_to_fqn: Dict[str, str] = {}

    # ── dbt ref/source aliases ────────────────────────────────────────────────
    ref_with_alias = re.compile(
        r'\{\{-?\s*ref\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*-?\}\}\s+(?:AS\s+)?(\w+)',
        re.IGNORECASE
    )
    source_with_alias = re.compile(
        r'\{\{-?\s*source\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]\s*\)\s*-?\}\}\s+(?:AS\s+)?(\w+)',
        re.IGNORECASE
    )

    for match in ref_with_alias.finditer(sql_content):
        model_name = match.group(1).strip()
        alias      = match.group(2).strip()
        for fqn in upstream_fqns:
            if fqn == model_name or fqn.endswith(f".{model_name}"):
                alias_to_fqn[alias] = fqn
                break

    for match in source_with_alias.finditer(sql_content):
        schema     = match.group(1).strip()
        table      = match.group(2).strip()
        alias      = match.group(3).strip()
        source_fqn = f"{schema}.{table}"
        for fqn in upstream_fqns:
            if fqn == source_fqn or fqn.endswith(f".{table}"):
                alias_to_fqn[alias] = fqn
                break

    # ── bare table name aliases (raw SQL) ────────────────────────────────────
    bare_alias = re.compile(
        r'\b(?:FROM|JOIN)\s+(\w+)\s+(?:AS\s+)?(\w+)\b',
        re.IGNORECASE
    )
    for match in bare_alias.finditer(sql_content):
        table_name = match.group(1).strip().lower()
        alias      = match.group(2).strip()
        for fqn in upstream_fqns:
            bare       = fqn.split(".")[-1].lower()
            table_part = re.sub(r'^\d+_', '', bare)   # strip leading "001_"
            if table_part == table_name or bare == table_name:
                alias_to_fqn[alias] = fqn
                break

    if not alias_to_fqn:
        return {}

    # ── SELECT * wildcard ─────────────────────────────────────────────────────
    if re.search(r'SELECT\s+\*', sql_content, re.IGNORECASE):
        for alias, fqn in alias_to_fqn.items():
            result[fqn] = [ColumnUsage(column="*", used_in_select=True)]
        return result

    # ── Clause-level column usage ─────────────────────────────────────────────
    usage_map: Dict[str, Dict[str, ColumnUsage]] = {fqn: {} for fqn in alias_to_fqn.values()}

    alias_star = re.compile(r'(\w+)\.\*', re.IGNORECASE)
    for match in alias_star.finditer(sql_content):
        alias = match.group(1)
        if alias in alias_to_fqn:
            fqn = alias_to_fqn[alias]
            usage_map[fqn]["*"] = ColumnUsage(column="*", used_in_select=True)

    col_pattern = re.compile(r'(\w+)\.(\w+)', re.IGNORECASE)
    sql_upper   = sql_content.upper()

    select_end   = max(sql_upper.find("FROM"), len(sql_content))
    select_block = sql_content[:select_end]

    where_start = sql_upper.find("WHERE")
    where_block = sql_content[where_start:] if where_start >= 0 else ""

    join_blocks = []
    for match in re.finditer(r'\bJOIN\b', sql_upper):
        join_blocks.append(sql_content[match.start():match.start() + 500])

    def _register(alias: str, col: str, in_select=False, in_where=False, in_join=False):
        if alias not in alias_to_fqn:
            return
        fqn = alias_to_fqn[alias]
        if col not in usage_map[fqn]:
            usage_map[fqn][col] = ColumnUsage(column=col)
        u = usage_map[fqn][col]
        if in_select: u.used_in_select = True
        if in_where:  u.used_in_where  = True
        if in_join:   u.used_in_join   = True

    for m in col_pattern.finditer(select_block):
        _register(m.group(1), m.group(2), in_select=True)
    for m in col_pattern.finditer(where_block):
        _register(m.group(1), m.group(2), in_where=True)
    for jb in join_blocks:
        for m in col_pattern.finditer(jb):
            _register(m.group(1), m.group(2), in_join=True)

    for fqn, col_dict in usage_map.items():
        if col_dict:
            result[fqn] = list(col_dict.values())

    return result


def _parse_yml_columns(
    yml_content: str,
    file_path: str
) -> Dict[str, List[str]]:
    """
    Parses a dbt schema yml file to extract column names per model/source table.

    Returns {model_name: [col1, col2, ...]}
    """
    result: Dict[str, List[str]] = {}

    try:
        lines          = yml_content.splitlines()
        current_model: Optional[str]       = None
        current_cols:  Optional[List[str]] = None
        in_columns     = False
        col_indent     = 0

        for line in lines:
            stripped = line.strip()
            indent   = len(line) - len(line.lstrip())

            model_match = re.match(r'^-\s+name:\s+(\S+)', stripped)
            if model_match:
                if current_model and current_cols is not None:
                    result[current_model] = current_cols
                candidate = model_match.group(1)
                if in_columns and indent > col_indent:
                    if current_cols is not None:
                        current_cols.append(candidate)
                    continue
                else:
                    current_model = candidate
                    current_cols  = []
                    in_columns    = False
                continue

            if stripped == "columns:" and current_model:
                in_columns = True
                col_indent = indent
                continue

            if in_columns:
                col_match = re.match(r'^-\s+name:\s+(\S+)', stripped)
                if col_match and indent > col_indent:
                    if current_cols is not None:
                        current_cols.append(col_match.group(1))
                    continue
                if indent <= col_indent and stripped and not stripped.startswith("#"):
                    if not stripped.startswith("-"):
                        in_columns = False

        if current_model and current_cols is not None:
            result[current_model] = current_cols

    except Exception as e:
        print(f"WARNING _parse_yml_columns: Failed to parse {file_path}: {e}")

    return result


def _filter_all_files(
    file_tree: List[dict]
) -> Tuple[List[str], List[str], List[str]]:
    """
    Splits the full repo file tree into three lists:
      dbt_sql_files       — .sql under models/ seeds/ snapshots/
      migration_sql_files — .sql under migrations/
      yml_files           — .yml/.yaml under dbt dirs (for column enrichment)

    Skips node_modules and any other non-data directories.
    """
    dbt_sql_files:       List[str] = []
    migration_sql_files: List[str] = []
    yml_files:           List[str] = []

    for entry in file_tree:
        path = entry.get("path", "")

        if "node_modules" in path:
            continue

        if any(path.startswith(d) for d in _DBT_DIRS):
            if path.endswith(".sql"):
                dbt_sql_files.append(path)
            elif path.endswith((".yml", ".yaml")):
                yml_files.append(path)

        elif any(path.startswith(d) for d in _MIGRATION_DIRS):
            if path.endswith(".sql"):
                migration_sql_files.append(path)

    print(
        f"DEBUG _filter_all_files: "
        f"{len(dbt_sql_files)} dbt SQL, "
        f"{len(migration_sql_files)} migration SQL, "
        f"{len(yml_files)} YML"
    )
    return dbt_sql_files, migration_sql_files, yml_files


# ══════════════════════════════════════════════════════════════════════════════
# Graph Build Layer
# ══════════════════════════════════════════════════════════════════════════════

def _build_nodes_from_dbt_sql(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    sql_files: List[str]
) -> Dict[str, RepoLineageNode]:
    """
    First pass — fetches and parses every dbt SQL file.
    referenced_by and column_usage left empty — populated in later passes.
    """
    nodes: Dict[str, RepoLineageNode] = {}

    for file_path in sql_files:
        try:
            fqn         = _derive_fqn_from_path(file_path)
            sql_content = _fetch_file_content(github_token, repo_owner, repo_name, file_path)

            if sql_content is None:
                print(f"WARNING _build_nodes_from_dbt_sql: Could not fetch {file_path} — skipping")
                continue

            deps = _parse_ref_and_source_calls(sql_content)

            nodes[fqn] = RepoLineageNode(
                fqn=fqn,
                file_path=file_path,
                node_type=NODE_TYPE_DBT,
                sql=sql_content,
                depends_on=deps,
            )

        except Exception as e:
            print(f"WARNING _build_nodes_from_dbt_sql: Failed for {file_path}: {e}")
            continue

    print(f"DEBUG _build_nodes_from_dbt_sql: Built {len(nodes)} dbt nodes")
    return nodes


def _build_nodes_from_migrations(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    migration_files: List[str]
) -> Dict[str, RepoLineageNode]:
    """
    First pass — fetches and parses every migration SQL file.

    For each file:
      - Derives FQN keeping the migrations/ prefix: migrations.001_users
      - Extracts the table this file DEFINES (CREATE TABLE) → raw_metadata
      - Extracts columns from CREATE TABLE + ALTER TABLE ADD COLUMN
      - Parses REFERENCES/FROM/JOIN → depends_on (bare table names)

    referenced_by and column_usage left empty — populated in later passes.
    """
    nodes: Dict[str, RepoLineageNode] = {}

    for file_path in migration_files:
        try:
            fqn         = _derive_fqn_from_path(file_path)
            sql_content = _fetch_file_content(github_token, repo_owner, repo_name, file_path)

            if sql_content is None:
                print(f"WARNING _build_nodes_from_migrations: Could not fetch {file_path} — skipping")
                continue

            defined_table = _extract_defined_table(sql_content)
            columns       = _extract_migration_columns(sql_content) if defined_table else []
            deps          = _parse_table_references(sql_content)

            nodes[fqn] = RepoLineageNode(
                fqn=fqn,
                file_path=file_path,
                node_type=NODE_TYPE_MIGRATION,
                sql=sql_content,
                columns=columns,
                depends_on=deps,
                raw_metadata={"defined_table": defined_table},
            )

        except Exception as e:
            print(f"WARNING _build_nodes_from_migrations: Failed for {file_path}: {e}")
            continue

    print(f"DEBUG _build_nodes_from_migrations: Built {len(nodes)} migration nodes")
    return nodes


def _enrich_nodes_with_yml(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    yml_files: List[str],
    nodes: Dict[str, RepoLineageNode]
) -> Dict[str, RepoLineageNode]:
    """
    Enrichment pass — adds column definitions to dbt nodes from schema yml files.
    Migration nodes already have columns from _extract_migration_columns.
    Also stores raw yml column declarations in raw_metadata for source drift checks.
    """
    for file_path in yml_files:
        try:
            yml_content = _fetch_file_content(github_token, repo_owner, repo_name, file_path)
            if yml_content is None:
                continue

            model_columns = _parse_yml_columns(yml_content, file_path)

            for model_name, columns in model_columns.items():
                matched_fqn = None

                if model_name in nodes:
                    matched_fqn = model_name
                else:
                    for fqn in nodes:
                        if fqn == model_name or fqn.endswith(f".{model_name}"):
                            matched_fqn = fqn
                            break

                if matched_fqn:
                    nodes[matched_fqn].columns = columns
                    # Store yml_columns separately so drift checks can compare
                    # yml declarations vs migration actual columns
                    nodes[matched_fqn].raw_metadata["yml_columns"] = columns

        except Exception as e:
            print(f"WARNING _enrich_nodes_with_yml: Failed for {file_path}: {e}")
            continue

    return nodes


def _populate_referenced_by(
    nodes: Dict[str, RepoLineageNode]
) -> Dict[str, RepoLineageNode]:
    """
    Second pass — inverts depends_on edges to build referenced_by lists.
    Handles cross-type resolution between dbt nodes and migration nodes.

    Resolution priority for each dep_name:
      1. Exact FQN match
      2. FQN suffix match                (dep "orders" → "finance.orders")
      3. Bare table → migration bridge   (dep "users" or "raw.users" →
                                          "migrations.001_users" via defined_table)
    """
    # Build lookup: bare_table_name → migration node FQN
    table_to_migration_fqn: Dict[str, str] = {}
    for fqn, node in nodes.items():
        if node.node_type == NODE_TYPE_MIGRATION:
            defined = node.raw_metadata.get("defined_table")
            if defined:
                table_to_migration_fqn[defined.lower()] = fqn

    def _resolve(dep_name: str) -> Optional[str]:
        if dep_name in nodes:
            return dep_name
        for candidate in nodes:
            if candidate.endswith(f".{dep_name}"):
                return candidate
        bare = dep_name.split(".")[-1].lower()
        if bare in table_to_migration_fqn:
            return table_to_migration_fqn[bare]
        return None

    for fqn, node in nodes.items():
        for dep_name in node.depends_on:
            matched = _resolve(dep_name)
            if matched and fqn not in nodes[matched].referenced_by:
                nodes[matched].referenced_by.append(fqn)

    total_edges     = sum(len(n.referenced_by) for n in nodes.values())
    migration_count = len(table_to_migration_fqn)
    print(
        f"DEBUG _populate_referenced_by: {total_edges} total edges "
        f"({migration_count} migration table mappings available for bridging)"
    )
    return nodes


def _populate_column_usage(
    nodes: Dict[str, RepoLineageNode]
) -> Dict[str, RepoLineageNode]:
    """
    Third pass — fills column_usage for every node.
    Works for both dbt SQL ({{ ref() }} aliases) and raw SQL (bare table aliases).
    """
    parsed_count = 0

    for fqn, node in nodes.items():
        if not node.sql or not node.depends_on:
            continue

        try:
            resolved_upstream: List[str] = []
            for dep_name in node.depends_on:
                if dep_name in nodes:
                    resolved_upstream.append(dep_name)
                else:
                    matched = None
                    for candidate in nodes:
                        if candidate == dep_name or candidate.endswith(f".{dep_name}"):
                            matched = candidate
                            break
                    if not matched:
                        # Try table bridge for migration deps
                        bare = dep_name.split(".")[-1].lower()
                        for candidate in nodes:
                            if nodes[candidate].node_type == NODE_TYPE_MIGRATION:
                                if nodes[candidate].raw_metadata.get("defined_table", "") == bare:
                                    matched = candidate
                                    break
                    resolved_upstream.append(matched or dep_name)

            usage = _parse_column_usage(node.sql, resolved_upstream)
            node.column_usage = usage
            if usage:
                parsed_count += 1

        except Exception as e:
            print(f"WARNING _populate_column_usage: Failed for {fqn}: {e}")
            continue

    print(f"DEBUG _populate_column_usage: Column usage parsed for {parsed_count} nodes")
    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# Contract Validation Layer
# ══════════════════════════════════════════════════════════════════════════════

def _check_column_drops(
    graph: RepoLineageGraph,
    changed_fqn: str,
    new_columns: List[str],
) -> List[ContractViolation]:
    """
    Checks whether any downstream node references a column that no longer
    exists in the changed asset's new column list.

    new_columns — the column list AFTER the PR change (extracted from the
                  changed file's new content, passed in by validate_contracts).

    For each downstream node that has column_usage for changed_fqn:
      - If the used column is not in new_columns → confirmed breakage
      - Wildcard '*' usage → flagged as potential break (medium severity)
        since we cannot statically know which columns will be used at runtime
    """
    violations: List[ContractViolation] = []
    new_col_set = {c.lower() for c in new_columns}

    for downstream_node in get_downstream(graph, changed_fqn, depth=3):
        # Find column_usage entries that point to changed_fqn
        usage_list: List[ColumnUsage] = []

        if changed_fqn in downstream_node.column_usage:
            usage_list = downstream_node.column_usage[changed_fqn]
        else:
            # Try suffix/table match
            for usage_fqn, usages in downstream_node.column_usage.items():
                bare_changed = changed_fqn.split(".")[-1].lower()
                bare_usage   = usage_fqn.split(".")[-1].lower()
                if bare_changed == bare_usage or usage_fqn == changed_fqn:
                    usage_list = usages
                    break

        if not usage_list:
            continue

        for cu in usage_list:
            col = cu.column.lower()

            if col == "*":
                # Wildcard — can't confirm exact column but flag as risk
                if new_col_set:  # only flag if we know the new schema
                    violations.append(ContractViolation(
                        violation_type="column_dropped",
                        changed_fqn=changed_fqn,
                        affected_fqn=downstream_node.fqn,
                        column="*",
                        detail=(
                            f"{downstream_node.fqn} uses SELECT * from {changed_fqn}. "
                            f"New columns: {sorted(new_col_set)}. "
                            f"Any column removal will silently change downstream output."
                        ),
                        severity="medium",
                        file_path=downstream_node.file_path,
                        fix_hint=(
                            f"Replace SELECT * with explicit column list in "
                            f"{downstream_node.file_path} to make dependencies explicit."
                        ),
                    ))
            elif col not in new_col_set and new_col_set:
                # Column definitely missing from new schema
                violations.append(ContractViolation(
                    violation_type="column_dropped",
                    changed_fqn=changed_fqn,
                    affected_fqn=downstream_node.fqn,
                    column=col,
                    detail=(
                        f"{downstream_node.fqn} references column '{col}' from "
                        f"{changed_fqn}, but '{col}' does not exist in the new schema. "
                        f"Available columns: {sorted(new_col_set)}."
                    ),
                    severity="critical",
                    file_path=downstream_node.file_path,
                    fix_hint=(
                        f"Update {downstream_node.file_path} to use the new column name, "
                        f"or revert the column change in {changed_fqn}."
                    ),
                ))

    return violations


def _check_fk_column_existence(
    graph: RepoLineageGraph,
    changed_fqn: str,
    new_columns: List[str],
) -> List[ContractViolation]:
    """
    Checks whether any migration file has a FOREIGN KEY that references a
    column in the changed migration that no longer exists.

    Example:
      001_orders.sql: FOREIGN KEY (user_id) REFERENCES users(id)
      PR drops 'id' from 001_users.sql
      → violation: orders FK references users.id which no longer exists
    """
    violations: List[ContractViolation] = []
    new_col_set = {c.lower() for c in new_columns}

    if not new_col_set:
        return violations

    # Get the bare table name this changed node defines
    changed_node     = graph.nodes.get(changed_fqn)
    if not changed_node:
        return violations
    defined_table    = changed_node.raw_metadata.get("defined_table", "").lower()
    if not defined_table:
        return violations

    # Check all migration nodes that reference this table via FK
    for fqn, node in graph.nodes.items():
        if node.node_type != NODE_TYPE_MIGRATION or fqn == changed_fqn:
            continue

        fk_refs = _parse_fk_references(node.sql)
        for ref_table, ref_col in fk_refs:
            if ref_table.lower() == defined_table and ref_col.lower() not in new_col_set:
                violations.append(ContractViolation(
                    violation_type="fk_column_missing",
                    changed_fqn=changed_fqn,
                    affected_fqn=fqn,
                    column=ref_col,
                    detail=(
                        f"{fqn} has FOREIGN KEY REFERENCES {defined_table}({ref_col}), "
                        f"but column '{ref_col}' no longer exists in {changed_fqn}. "
                        f"Available columns: {sorted(new_col_set)}."
                    ),
                    severity="critical",
                    file_path=node.file_path,
                    fix_hint=(
                        f"Update the FOREIGN KEY in {node.file_path} to reference "
                        f"an existing column, or restore '{ref_col}' in {changed_fqn}."
                    ),
                ))

    return violations


def _check_migration_ordering(
    graph: RepoLineageGraph,
    changed_fqns: List[str],
) -> List[ContractViolation]:
    """
    Checks whether any migration depends on a table that is only defined
    in a later-numbered migration (ordering violation).

    Example:
      005_add_fk.sql (seq=5) has REFERENCES users
      but users is defined in 010_users.sql (seq=10)
      → 005 runs before 010, so the FK will fail at runtime

    Only checks migrations with numeric prefixes — unnumbered migrations
    are skipped since ordering is undefined.
    """
    violations: List[ContractViolation] = []

    # Build: table_name → (seq_number, fqn) for all migration nodes
    table_seq: Dict[str, Tuple[int, str]] = {}
    for fqn, node in graph.nodes.items():
        if node.node_type != NODE_TYPE_MIGRATION:
            continue
        seq   = _get_migration_sequence_number(fqn)
        table = node.raw_metadata.get("defined_table", "").lower()
        if seq is not None and table:
            table_seq[table] = (seq, fqn)

    # Check each changed migration — does it reference a table defined later?
    for changed_fqn in changed_fqns:
        node = graph.nodes.get(changed_fqn)
        if not node or node.node_type != NODE_TYPE_MIGRATION:
            continue

        this_seq = _get_migration_sequence_number(changed_fqn)
        if this_seq is None:
            continue

        for dep_table in node.depends_on:
            dep_table_lower = dep_table.lower()
            if dep_table_lower in table_seq:
                dep_seq, dep_fqn = table_seq[dep_table_lower]
                if dep_seq > this_seq:
                    violations.append(ContractViolation(
                        violation_type="migration_order",
                        changed_fqn=changed_fqn,
                        affected_fqn=dep_fqn,
                        column=None,
                        detail=(
                            f"Migration {changed_fqn} (seq={this_seq}) references table "
                            f"'{dep_table}' which is only created in {dep_fqn} (seq={dep_seq}). "
                            f"This migration will fail at runtime because it runs before "
                            f"the table it depends on exists."
                        ),
                        severity="critical",
                        file_path=node.file_path,
                        fix_hint=(
                            f"Renumber {changed_fqn} to run after {dep_fqn}, "
                            f"or move the CREATE TABLE for '{dep_table}' to an earlier migration."
                        ),
                    ))

    return violations


def _check_source_yml_drift(
    graph: RepoLineageGraph,
    changed_fqn: str,
    new_columns: List[str],
) -> List[ContractViolation]:
    """
    Checks whether any dbt sources.yml declares columns for the changed
    migration table that no longer exist in the new column list.

    Example:
      sources.yml declares users.email
      PR drops 'email' from 001_users.sql
      → dbt source tests will fail; downstream dbt models using
        {{ source('raw', 'users') }} and selecting 'email' will break

    Detects drift between yml declarations and actual migration schema.
    """
    violations: List[ContractViolation] = []
    new_col_set = {c.lower() for c in new_columns}

    if not new_col_set:
        return violations

    changed_node  = graph.nodes.get(changed_fqn)
    if not changed_node:
        return violations
    defined_table = changed_node.raw_metadata.get("defined_table", "").lower()
    if not defined_table:
        return violations

    # Find dbt nodes that source from this table and have yml_columns
    for fqn, node in graph.nodes.items():
        if node.node_type != NODE_TYPE_DBT:
            continue

        yml_cols = node.raw_metadata.get("yml_columns", [])
        if not yml_cols:
            continue

        # Check if this dbt node depends on the changed migration table
        is_dependent = False
        for dep in node.depends_on:
            bare = dep.split(".")[-1].lower()
            if bare == defined_table or dep.lower() == defined_table:
                is_dependent = True
                break

        if not is_dependent:
            continue

        # Find yml columns that no longer exist in the migration
        for yml_col in yml_cols:
            if yml_col.lower() not in new_col_set:
                violations.append(ContractViolation(
                    violation_type="source_yml_drift",
                    changed_fqn=changed_fqn,
                    affected_fqn=fqn,
                    column=yml_col,
                    detail=(
                        f"dbt node {fqn} declares column '{yml_col}' for source table "
                        f"'{defined_table}' in its schema yml, but '{yml_col}' no longer "
                        f"exists in {changed_fqn}. dbt source tests will fail."
                    ),
                    severity="high",
                    file_path=node.file_path,
                    fix_hint=(
                        f"Remove '{yml_col}' from the schema yml for {fqn}, "
                        f"or restore the column in {changed_fqn}."
                    ),
                ))

    return violations


def validate_contracts(
    graph: RepoLineageGraph,
    changed_fqns: List[str],
    new_column_map: Dict[str, List[str]],
) -> List[ContractViolation]:
    """
    Runs all contract validation checks for a set of changed assets and
    returns a deduplicated list of confirmed violations.

    This is the main entry point called from investigation_controller.py
    in Step 2b (Option B) before the AI prompt is built.

    Args:
        graph           — the current repo lineage graph
        changed_fqns    — FQNs of assets changed in this PR
        new_column_map  — {fqn: [new_col1, new_col2, ...]} — the column list
                          AFTER the PR change, extracted from the PR diff.
                          Pass empty list for a FQN if columns cannot be determined.

    Checks performed:
      1. Column drops / renames     → downstream nodes referencing missing columns
      2. FK column existence        → FKs pointing to dropped columns
      3. Migration ordering         → migration N depends on table defined in N+k
      4. Source yml drift           → yml declares columns not in new migration schema

    Returns List[ContractViolation] sorted by severity (critical first).
    Empty list means no confirmed violations — genuinely safe to merge.
    """
    all_violations: List[ContractViolation] = []
    seen_keys: set = set()

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    for fqn in changed_fqns:
        new_cols = new_column_map.get(fqn, [])

        try:
            all_violations.extend(_check_column_drops(graph, fqn, new_cols))
        except Exception as e:
            print(f"WARNING validate_contracts: _check_column_drops failed for {fqn}: {e}")

        try:
            all_violations.extend(_check_fk_column_existence(graph, fqn, new_cols))
        except Exception as e:
            print(f"WARNING validate_contracts: _check_fk_column_existence failed for {fqn}: {e}")

        try:
            all_violations.extend(_check_source_yml_drift(graph, fqn, new_cols))
        except Exception as e:
            print(f"WARNING validate_contracts: _check_source_yml_drift failed for {fqn}: {e}")

    # Migration ordering check — runs once across all changed migrations
    try:
        all_violations.extend(_check_migration_ordering(graph, changed_fqns))
    except Exception as e:
        print(f"WARNING validate_contracts: _check_migration_ordering failed: {e}")

    # Deduplicate by (violation_type, changed_fqn, affected_fqn, column)
    deduplicated: List[ContractViolation] = []
    for v in all_violations:
        key = (v.violation_type, v.changed_fqn, v.affected_fqn, v.column)
        if key not in seen_keys:
            seen_keys.add(key)
            deduplicated.append(v)

    # Sort: critical first
    deduplicated.sort(key=lambda v: severity_rank.get(v.severity, 99))

    critical = sum(1 for v in deduplicated if v.severity == "critical")
    high     = sum(1 for v in deduplicated if v.severity == "high")
    print(
        f"DEBUG validate_contracts: {len(deduplicated)} violations found "
        f"({critical} critical, {high} high) across {len(changed_fqns)} changed assets"
    )

    return deduplicated


# ══════════════════════════════════════════════════════════════════════════════
# Storage Layer
# ══════════════════════════════════════════════════════════════════════════════

def _column_usage_to_dict(usage: Dict[str, List[ColumnUsage]]) -> dict:
    """Serializes column_usage dict for MongoDB/Redis storage."""
    return {
        fqn: [asdict(cu) for cu in usages]
        for fqn, usages in usage.items()
    }


def _column_usage_from_dict(raw: dict) -> Dict[str, List[ColumnUsage]]:
    """Deserializes column_usage dict from MongoDB/Redis."""
    result = {}
    for fqn, usages in raw.items():
        result[fqn] = [ColumnUsage(**cu) for cu in usages]
    return result


def _graph_to_mongo_doc(graph: RepoLineageGraph) -> dict:
    """
    Serializes a RepoLineageGraph to a MongoDB-compatible dict.
    Includes node_type and raw_metadata so migration nodes round-trip correctly.
    """
    nodes_dict = {}
    for fqn, node in graph.nodes.items():
        nodes_dict[fqn] = {
            "fqn":           node.fqn,
            "file_path":     node.file_path,
            "node_type":     node.node_type,
            "sql":           node.sql,
            "columns":       node.columns,
            "depends_on":    node.depends_on,
            "referenced_by": node.referenced_by,
            "column_usage":  _column_usage_to_dict(node.column_usage),
            "raw_metadata":  node.raw_metadata,
        }

    return {
        "repo_full_name":      graph.repo_full_name,
        "connection_id":       graph.connection_id,
        "user_id":             graph.user_id,
        "built_at":            graph.built_at,
        "total_files_scanned": graph.total_files_scanned,
        "total_nodes":         graph.total_nodes,
        "nodes":               nodes_dict,
    }


def _mongo_doc_to_graph(doc: dict) -> RepoLineageGraph:
    """
    Deserializes a MongoDB document back to a RepoLineageGraph.
    Reconstructs all nested dataclasses explicitly.
    """
    nodes: Dict[str, RepoLineageNode] = {}

    for fqn, nd in doc.get("nodes", {}).items():
        nodes[fqn] = RepoLineageNode(
            fqn=nd["fqn"],
            file_path=nd["file_path"],
            node_type=nd.get("node_type", NODE_TYPE_DBT),
            sql=nd.get("sql", ""),
            columns=nd.get("columns", []),
            depends_on=nd.get("depends_on", []),
            referenced_by=nd.get("referenced_by", []),
            column_usage=_column_usage_from_dict(nd.get("column_usage", {})),
            raw_metadata=nd.get("raw_metadata", {}),
        )

    return RepoLineageGraph(
        repo_full_name=doc["repo_full_name"],
        connection_id=doc["connection_id"],
        user_id=doc["user_id"],
        built_at=doc["built_at"],
        nodes=nodes,
        total_files_scanned=doc.get("total_files_scanned", 0),
        total_nodes=doc.get("total_nodes", 0),
    )


def _save_graph_to_mongo(graph: RepoLineageGraph) -> bool:
    try:
        doc = _graph_to_mongo_doc(graph)
        _graphs_col.replace_one(
            {"repo_full_name": graph.repo_full_name},
            doc,
            upsert=True
        )
        print(
            f"DEBUG _save_graph_to_mongo: Saved graph for {graph.repo_full_name} "
            f"({graph.total_nodes} nodes)"
        )
        return True
    except Exception as e:
        print(f"ERROR _save_graph_to_mongo: {e}")
        return False


def _save_graph_to_redis(graph: RepoLineageGraph) -> bool:
    if not _redis_client:
        return False
    try:
        doc     = _graph_to_mongo_doc(graph)
        key     = f"repo_graph:{graph.repo_full_name}"
        ttl_sec = GRAPH_CACHE_TTL_HOURS * 3600
        _redis_client.setex(key, ttl_sec, json.dumps(doc))
        print(
            f"DEBUG _save_graph_to_redis: Cached graph for {graph.repo_full_name} "
            f"(TTL {GRAPH_CACHE_TTL_HOURS}h)"
        )
        return True
    except Exception as e:
        print(f"WARNING _save_graph_to_redis: {e} — continuing without Redis cache")
        return False


def _load_graph_from_redis(repo_full_name: str) -> Optional[RepoLineageGraph]:
    if not _redis_client:
        return None
    try:
        key  = f"repo_graph:{repo_full_name}"
        data = _redis_client.get(key)
        if not data:
            return None
        graph = _mongo_doc_to_graph(json.loads(data))
        print(
            f"DEBUG _load_graph_from_redis: Cache hit for {repo_full_name} "
            f"({graph.total_nodes} nodes)"
        )
        return graph
    except Exception as e:
        print(f"WARNING _load_graph_from_redis: {e} — falling back to MongoDB")
        return None


def _load_graph_from_mongo(repo_full_name: str) -> Optional[RepoLineageGraph]:
    try:
        doc = _graphs_col.find_one({"repo_full_name": repo_full_name})
        if not doc:
            print(f"DEBUG _load_graph_from_mongo: No graph found for {repo_full_name}")
            return None

        built_at_str = doc.get("built_at", "")
        if built_at_str:
            try:
                built_at  = datetime.fromisoformat(built_at_str.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - built_at).total_seconds() / 3600
                if age_hours > GRAPH_CACHE_TTL_HOURS:
                    print(
                        f"DEBUG _load_graph_from_mongo: Graph for {repo_full_name} is "
                        f"{age_hours:.1f}h old (TTL {GRAPH_CACHE_TTL_HOURS}h) — stale"
                    )
                    return None
                print(
                    f"DEBUG _load_graph_from_mongo: Loaded graph for {repo_full_name} "
                    f"(age {age_hours:.1f}h)"
                )
            except Exception:
                pass

        return _mongo_doc_to_graph(doc)

    except Exception as e:
        print(f"ERROR _load_graph_from_mongo: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def scan_repo(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    connection_id: str,
    user_id: str
) -> RepoLineageGraph:
    """
    Performs a full repo scan and builds the complete unified lineage graph
    covering both dbt models and raw migration files.

    Steps:
      1.  Fetch full recursive file tree from GitHub
      2.  Filter into dbt SQL / migration SQL / YML lists
      3.  Build dbt nodes (FQN + ref/source depends_on)
      4.  Build migration nodes (FQN + table depends_on + defined_table metadata)
      5.  Merge both node dicts into one unified graph
      6.  Enrich dbt nodes with column definitions from YML files
      7.  Populate referenced_by (cross-type aware — bridges dbt ↔ migration)
      8.  Populate column_usage (alias.column analysis per node)
      9.  Save to MongoDB (persistent store)
      10. Save to Redis (hot cache with TTL)
    """
    start          = datetime.now(timezone.utc)
    repo_full_name = f"{repo_owner}/{repo_name}"

    print(f"DEBUG scan_repo: Starting full scan of {repo_full_name}")

    file_tree = _get_repo_file_tree(github_token, repo_owner, repo_name)
    if not file_tree:
        print(f"WARNING scan_repo: Empty file tree for {repo_full_name} — returning empty graph")
        return RepoLineageGraph(
            repo_full_name=repo_full_name,
            connection_id=connection_id,
            user_id=user_id,
            built_at=datetime.now(timezone.utc).isoformat(),
        )

    dbt_sql_files, migration_sql_files, yml_files = _filter_all_files(file_tree)
    total_files = len(dbt_sql_files) + len(migration_sql_files) + len(yml_files)

    dbt_nodes       = _build_nodes_from_dbt_sql(github_token, repo_owner, repo_name, dbt_sql_files)
    migration_nodes = _build_nodes_from_migrations(github_token, repo_owner, repo_name, migration_sql_files)

    # Merge: migration nodes first so dbt nodes win on any FQN collision
    nodes: Dict[str, RepoLineageNode] = {}
    nodes.update(migration_nodes)
    nodes.update(dbt_nodes)

    nodes = _enrich_nodes_with_yml(github_token, repo_owner, repo_name, yml_files, nodes)
    nodes = _populate_referenced_by(nodes)
    nodes = _populate_column_usage(nodes)

    elapsed_ms      = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    dbt_count       = sum(1 for n in nodes.values() if n.node_type == NODE_TYPE_DBT)
    migration_count = sum(1 for n in nodes.values() if n.node_type == NODE_TYPE_MIGRATION)

    graph = RepoLineageGraph(
        repo_full_name=repo_full_name,
        connection_id=connection_id,
        user_id=user_id,
        built_at=datetime.now(timezone.utc).isoformat(),
        nodes=nodes,
        total_files_scanned=total_files,
        total_nodes=len(nodes),
    )

    print(
        f"DEBUG scan_repo: Completed in {elapsed_ms}ms — "
        f"{len(nodes)} nodes ({dbt_count} dbt, {migration_count} migration), "
        f"{total_files} files scanned"
    )

    _save_graph_to_mongo(graph)
    _save_graph_to_redis(graph)

    return graph


def get_repo_graph(
    connection_id: str,
    repo_full_name: str
) -> Optional[RepoLineageGraph]:
    """
    Loads a graph using the Redis → MongoDB fallback chain.
    Returns None when no graph is available — caller should trigger scan_repo().
    Graph is kept fresh via update_graph_nodes() after each PR merge.
    """
    graph = _load_graph_from_redis(repo_full_name)
    if graph:
        return graph

    graph = _load_graph_from_mongo(repo_full_name)
    if graph:
        _save_graph_to_redis(graph)
        return graph

    print(f"DEBUG get_repo_graph: No graph available for {repo_full_name}")
    return None


def get_downstream(
    graph: RepoLineageGraph,
    fqn: str,
    depth: int = 3
) -> List[RepoLineageNode]:
    """
    BFS traversal of referenced_by edges starting from fqn.
    Returns all downstream consumer nodes up to depth levels.
    Works across node types (migration → dbt, dbt → dbt).
    Never returns the source node itself. Deduplicates by FQN.
    """
    if fqn not in graph.nodes:
        matched = None
        for candidate in graph.nodes:
            if candidate.endswith(f".{fqn}") or candidate == fqn:
                matched = candidate
                break
        if not matched:
            print(f"DEBUG get_downstream: {fqn} not found in graph")
            return []
        fqn = matched

    visited: set                   = set()
    result:  List[RepoLineageNode] = []
    queue    = deque([(fqn, 0)])

    while queue:
        current_fqn, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        node = graph.nodes.get(current_fqn)
        if not node:
            continue
        for ref_fqn in node.referenced_by:
            if ref_fqn not in visited:
                visited.add(ref_fqn)
                ref_node = graph.nodes.get(ref_fqn)
                if ref_node:
                    result.append(ref_node)
                    queue.append((ref_fqn, current_depth + 1))

    return result


def get_column_dependents(
    graph: RepoLineageGraph,
    fqn: str,
    dropped_columns: List[str]
) -> Dict[str, List[str]]:
    """
    Finds all downstream nodes that actually USE the specified columns
    from the changed asset. Returns {downstream_fqn: [affected_cols]}.
    """
    result: Dict[str, List[str]] = {}

    for node in get_downstream(graph, fqn, depth=3):
        usage_for_fqn = node.column_usage.get(fqn, [])

        if not usage_for_fqn:
            for usage_fqn, usages in node.column_usage.items():
                if (usage_fqn == fqn
                        or fqn.endswith(f".{usage_fqn}")
                        or usage_fqn.endswith(f".{fqn.split('.')[-1]}")):
                    usage_for_fqn = usages
                    break

        if not usage_for_fqn:
            continue

        used_columns = {cu.column for cu in usage_for_fqn}

        if "*" in used_columns:
            result[node.fqn] = list(dropped_columns)
            continue

        affected = [col for col in dropped_columns if col in used_columns]
        if affected:
            result[node.fqn] = affected

    return result


def build_subgraph_from_graph(
    graph: RepoLineageGraph,
    fqn: str
) -> Optional[Any]:
    """
    Adapter: converts repo graph data into the LineageSubgraph/LineageNode format
    expected by merge_lineage_subgraphs() and build_pr_ai_context().

    Works for both dbt nodes and migration nodes.
    """
    from models.lineage import LineageSubgraph, LineageNode, LineageEdge
    from models.base import AssetType

    if not graph or not graph.nodes:
        return None

    resolved_fqn = fqn
    if fqn not in graph.nodes:
        for candidate in graph.nodes:
            if candidate.endswith(f".{fqn}") or candidate == fqn:
                resolved_fqn = candidate
                break
        else:
            print(f"DEBUG build_subgraph_from_graph: {fqn} not found in graph — returning None")
            return None

    primary_repo_node = graph.nodes[resolved_fqn]
    lineage_nodes:  List[LineageNode] = []
    lineage_edges:  List[LineageEdge] = []
    seen_fqns:      set               = set()

    def _make_lineage_node(
        repo_node: RepoLineageNode,
        depth: int,
        is_downstream: bool
    ) -> LineageNode:
        return LineageNode(
            fqn=repo_node.fqn,
            display_name=repo_node.fqn.split(".")[-1],
            asset_type=AssetType.TABLE,
            service_name=repo_node.fqn.split(".")[0] if "." in repo_node.fqn else "repo",
            is_break_point=False,
            is_downstream=is_downstream,
            depth_from_failure=depth,
            raw_metadata={
                "file_path":     repo_node.file_path,
                "sql":           repo_node.sql,
                "columns":       repo_node.columns,
                "node_type":     repo_node.node_type,
                "defined_table": repo_node.raw_metadata.get("defined_table"),
                "column_usage":  {
                    k: [
                        {
                            "column":         cu.column,
                            "used_in_select": cu.used_in_select,
                            "used_in_where":  cu.used_in_where,
                            "used_in_join":   cu.used_in_join,
                        }
                        for cu in v
                    ]
                    for k, v in repo_node.column_usage.items()
                },
                "source_assets": [resolved_fqn],
            }
        )

    lineage_nodes.append(_make_lineage_node(primary_repo_node, depth=0, is_downstream=False))
    seen_fqns.add(resolved_fqn)

    for dep_name in primary_repo_node.depends_on:
        dep_fqn = dep_name
        if dep_name not in graph.nodes:
            for candidate in graph.nodes:
                if candidate.endswith(f".{dep_name}") or candidate == dep_name:
                    dep_fqn = candidate
                    break

        if dep_fqn in graph.nodes and dep_fqn not in seen_fqns:
            dep_node = graph.nodes[dep_fqn]
            lineage_nodes.append(_make_lineage_node(dep_node, depth=1, is_downstream=False))
            seen_fqns.add(dep_fqn)
            lineage_edges.append(LineageEdge(from_fqn=dep_fqn, to_fqn=resolved_fqn))

    for downstream_node in get_downstream(graph, resolved_fqn, depth=3):
        if downstream_node.fqn not in seen_fqns:
            lineage_nodes.append(_make_lineage_node(downstream_node, depth=-1, is_downstream=True))
            seen_fqns.add(downstream_node.fqn)
            lineage_edges.append(LineageEdge(from_fqn=resolved_fqn, to_fqn=downstream_node.fqn))

    downstream_count = sum(1 for n in lineage_nodes if n.is_downstream)
    print(
        f"DEBUG build_subgraph_from_graph: {resolved_fqn} ({primary_repo_node.node_type}) — "
        f"{len(lineage_nodes)} nodes ({downstream_count} downstream consumers)"
    )

    return LineageSubgraph(
        failing_asset_fqn=resolved_fqn,
        nodes=lineage_nodes,
        edges=lineage_edges,
        traversal_depth=len(lineage_nodes)
    )


def update_graph_nodes(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    connection_id: str,
    changed_file_paths: List[str]
) -> bool:
    """
    Incremental graph update — re-parses only changed files after a PR merges.
    Routes changed files to the right parser by path prefix.
    Falls back to full scan if no base graph exists.

    Called as a background task after each PR merge — graph stays fresh
    without requiring a manual full scan trigger.
    """
    repo_full_name = f"{repo_owner}/{repo_name}"

    try:
        doc = _graphs_col.find_one({"repo_full_name": repo_full_name})
        if not doc:
            print(
                f"WARNING update_graph_nodes: No base graph for {repo_full_name} "
                f"— triggering full scan"
            )
            scan_repo(
                github_token=github_token,
                repo_owner=repo_owner,
                repo_name=repo_name,
                connection_id=connection_id,
                user_id=connection_id,
            )
            return True

        graph = _mongo_doc_to_graph(doc)

        dbt_sql_changed = [
            p for p in changed_file_paths
            if p.endswith(".sql") and any(p.startswith(d) for d in _DBT_DIRS)
        ]
        migration_sql_changed = [
            p for p in changed_file_paths
            if p.endswith(".sql") and any(p.startswith(d) for d in _MIGRATION_DIRS)
        ]
        yml_changed = [p for p in changed_file_paths if p.endswith((".yml", ".yaml"))]

        def _remove_old_refs(fqn: str, old_node: Optional[RepoLineageNode]):
            if not old_node:
                return
            for old_dep in old_node.depends_on:
                if old_dep in graph.nodes:
                    graph.nodes[old_dep].referenced_by = [
                        r for r in graph.nodes[old_dep].referenced_by if r != fqn
                    ]

        if dbt_sql_changed:
            updated = _build_nodes_from_dbt_sql(github_token, repo_owner, repo_name, dbt_sql_changed)
            for fqn, node in updated.items():
                _remove_old_refs(fqn, graph.nodes.get(fqn))
                graph.nodes[fqn] = node

        if migration_sql_changed:
            updated = _build_nodes_from_migrations(github_token, repo_owner, repo_name, migration_sql_changed)
            for fqn, node in updated.items():
                _remove_old_refs(fqn, graph.nodes.get(fqn))
                graph.nodes[fqn] = node

        if yml_changed:
            graph.nodes = _enrich_nodes_with_yml(github_token, repo_owner, repo_name, yml_changed, graph.nodes)

        graph.nodes = _populate_referenced_by(graph.nodes)
        graph.nodes = _populate_column_usage(graph.nodes)

        graph.built_at    = datetime.now(timezone.utc).isoformat()
        graph.total_nodes = len(graph.nodes)

        _save_graph_to_mongo(graph)
        _save_graph_to_redis(graph)

        dbt_count       = sum(1 for n in graph.nodes.values() if n.node_type == NODE_TYPE_DBT)
        migration_count = sum(1 for n in graph.nodes.values() if n.node_type == NODE_TYPE_MIGRATION)

        print(
            f"DEBUG update_graph_nodes: Updated {len(changed_file_paths)} files in "
            f"{repo_full_name} — graph now {graph.total_nodes} nodes "
            f"({dbt_count} dbt, {migration_count} migration)"
        )
        return True

    except Exception as e:
        print(f"ERROR update_graph_nodes: {e}")
        return False