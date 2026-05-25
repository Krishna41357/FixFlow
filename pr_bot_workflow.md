# FixFlow — PR Bot Workflow Documentation

## Overview

The FixFlow PR bot automatically analyzes GitHub pull requests that contain data asset changes (`.sql` and dbt `.yml` files). When a qualifying PR is opened or updated, the bot:

1. Detects all data-relevant file changes
2. Derives a fully qualified name (FQN) for every changed asset
3. Traverses lineage upstream from each asset in OpenMetadata
4. Merges all per-asset lineage subgraphs into a unified graph
5. Runs a single AI call across all changed assets together
6. Posts a detailed comment to the PR — with per-cause errors, file locations, and ready-to-apply fixes

The bot operates as a background task so the webhook endpoint returns `202 Accepted` immediately. The PR author sees a placeholder comment within seconds, which is then updated in-place once analysis completes.

---

## Architecture: File → Feature Map

```
models/github.py              → All PR-specific data schemas
controllers/github_controller.py  → File filtering, FQN extraction, GitHub API, comment rendering
controllers/investigation_controller.py  → Lineage merging, AI prompt, AI call, result storage
routes/github_routes.py       → Webhook entry point, OAuth flow, webhook lifecycle routes
```

---

## File-by-File Feature Reference

---

### `models/github.py`

Defines every data shape used by the PR bot. Existing OAuth and legacy models are preserved untouched.

#### New PR AI Response Schema (section 2)

| Class | Purpose |
|---|---|
| `ErrorLocation` | Where a breakage manifests: `file`, `clause` (SELECT/JOIN/etc.), `approximate_line` |
| `CauseFix` | One actionable fix: `description`, `fix_type`, `target_file`, `code_snippet` |
| `AssetCause` | One reason a downstream asset breaks, traced back to one specific PR file. Contains `source_asset_fqn`, `error_type`, `error_description`, `error_location`, `fix` |
| `DownstreamImpact` | One broken downstream asset. Deduplicated by FQN. Holds a `causes[]` list — one entry per upstream PR file that contributes to its breakage |
| `ChangedAssetSummary` | Summary of one PR file post-filtering: `fqn`, `filename`, `change_type`, `change_description`, `patch_evidence`, `fqn_approximate` |
| `PRRootCause` | Top-level AI result for a PR. One instance per PR. Fields: `pr_summary`, `overall_severity`, `safe_to_merge`, `confidence`, `changed_assets[]`, `downstream_impacts[]` |

#### Reuse Policy (no duplication)
- `SuggestedFix` → imported from `models.investigations`, not redefined
- `AffectedAsset` → imported from `models.events`, not redefined
- `SeverityLevel` → imported from `models.base`

#### Legacy (section 3)
- `PRAnalysis`, `PRAnalysisInDB` — kept for backwards compatibility with existing stored documents. Not used by the new PR bot flow.

#### OAuth / App Registration (sections 4–5, unchanged)
- `GitHubOAuthProfile`, `GitHubInstallation`, `GitHubAppRegistration`
- `GitHubWebhookConfigRequest`, `GitHubRegistrationStatusResponse`

---

### `controllers/github_controller.py`

Handles all GitHub API interactions and PR file processing. Organized into 9 sections.

#### Section 5 — PR File Filtering

**`_is_relevant_yml(filename, patch) → bool`**

Three-stage decision to determine if a `.yml`/`.yaml` file is data-relevant:

| Stage | Logic | Result |
|---|---|---|
| 1 | Path starts with `.github/`, `deploy/`, `docker/`, `docs/` | → Reject immediately |
| 2 | Path starts with `models/`, `seeds/`, `snapshots/`, `analyses/`, `macros/` | → Accept immediately |
| 3 | Ambiguous path → scan patch content for `version:`, `models:`, `sources:`, `seeds:`, `metrics:`, `exposures:` | → Accept if found, reject if not |

If patch is unavailable and path is ambiguous → conservative reject.

**`filter_relevant_files(changed_assets) → List[ChangedAsset]`**

Entry point. Applies the filter to all changed files in a PR:
- All `.sql` files pass through unconditionally
- `.yml`/`.yaml` files go through `_is_relevant_yml`
- Returns a clean list preserving original order
- Logs `{n}/{total} files are data-relevant`

---

#### Section 6 — FQN Extraction

**`_strip_context_lines(patch) → str`**

Removes all unchanged context lines from a unified diff patch. Keeps only lines beginning with `+` or `-`, skipping `+++`/`---` file headers. This is the primary token optimization — a 500-line SQL file with 8 actual changes sends only 8 lines to the AI.

**`_warn_large_patch(filename, stripped_patch)`**

Logs a warning if a file has more than 200 changed lines. Does not truncate — sending all lines is always preferred over missing a breaking change.

**`_extract_fqn_from_sql(filename) → str`**

Derives a FQN from a `.sql` file path by stripping the dbt top-level directory prefix (`models/`, `seeds/`, etc.) and the extension, then joining path segments with dots.

```
models/finance/revenue.sql     → finance.revenue
seeds/raw/users.sql            → raw.users
snapshots/finance/orders.sql   → finance.orders
```

**`_extract_fqn_from_yml(filename, patch) → (List[str], bool)`**

Hybrid approach returning all model FQNs defined in a schema yml:

1. Extracts the domain from the file path (directory after dbt prefix)
2. Scans the stripped patch for all `- name: <model>` entries under `models:` / `sources:` blocks
3. Deduplicates while preserving order (renames produce two entries for the same model)
4. Combines as `domain.model_name` for each match
5. If patch parsing finds nothing → falls back to path-based single FQN, sets `fqn_approximate=True`

Returns a list — a single schema yml defining 5 models produces 5 FQNs.

**`derive_fqns(relevant_assets) → Dict[str, Tuple[str, bool]]`**

Builds the `asset_fqn_map` for all relevant files:
- `.sql` files → keyed by filename, one entry
- `.yml` files with one model → keyed by filename, one entry
- `.yml` files with multiple models → keyed as `filename::fqn` per model to avoid key collisions

Return format: `filename (or composite key) → (fqn, fqn_approximate)`

---

#### Section 8 — PR Comment Renderer

**`render_placeholder_comment(relevant_files, investigation_id) → str`**

Generates the initial comment posted immediately on webhook receipt. Lists all detected data files with status and line change counts. Signals that analysis is running.

**`render_pr_comment(pr_root_cause, investigation_id) → str`**

Generates the full analysis comment from a completed `PRRootCause`. Structure:

```
Header          — severity emoji, pr_summary, summary table (severity / verdict / counts / confidence)
What Changed    — markdown table: asset FQN, change type + description, patch evidence
                  approximate FQN flag shown inline
Downstream      — per broken asset:
                    severity emoji + FQN + display name
                    per cause block:
                      source asset, error type, error description
                      file + clause + approximate line
                      fix type, description, target file
                      ready-to-apply code snippet (fenced sql block)
No-breakage     — fallback section if downstream_impacts is empty
Footer          — investigation ID, confidence percentage
```

---

#### Sections 1–4, 7, 9 — GitHub API (unchanged from original)

| Function | Purpose |
|---|---|
| `verify_github_signature` | HMAC-SHA256 validation of `X-Hub-Signature-256` header |
| `_generate_app_jwt` | Generates GitHub App JWT for installation token exchange |
| `get_installation_token` | Exchanges JWT for an installation access token. Falls back to `GITHUB_TEST_PAT` in dev |
| `build_webhook_url` | Constructs the full webhook URL with `connection_id` and `user_id` query params |
| `register_github_webhook` | `POST /repos/{owner}/{repo}/hooks` — creates repo-level webhook, returns `webhook_id` |
| `update_github_webhook` | `PATCH /repos/{owner}/{repo}/hooks/{id}` — updates existing webhook |
| `delete_github_webhook` | `DELETE /repos/{owner}/{repo}/hooks/{id}` — treats 204 and 404 both as success |
| `verify_github_webhook` | `GET /repos/{owner}/{repo}/hooks/{id}` — checks webhook is still active |
| `parse_pr_diff` | `GET /repos/{owner}/{repo}/pulls/{pr}/files` — fetches all changed files as `ChangedAsset` list |
| `post_pr_comment` | `POST /repos/{owner}/{repo}/issues/{pr}/comments` — posts new comment, returns comment ID |
| `update_pr_comment` | `PATCH /repos/{owner}/{repo}/issues/comments/{id}` — updates existing comment in-place |

---

### `controllers/investigation_controller.py`

Handles the full investigation lifecycle. Split into three clear flows.

#### Shared Utilities (both flows use these)

| Function | Purpose |
|---|---|
| `create_investigation(...)` | Inserts investigation document in MongoDB. Accepts `event_type` param — defaults to `"manual"`, PR bot passes `"github_pr"` |
| `update_investigation_status(...)` | Updates `status` + `updated_at` fields |
| `get_investigation(...)` | Fetches + deserialises a full `InvestigationResponse`. Handles both `root_cause` (manual) and `pr_root_cause` (PR bot) |
| `list_investigations(...)` | Compact list for sidebar, reads from `root_cause` only (manual flow) |
| `_deserialise_pr_root_cause(raw)` | Explicitly reconstructs `PRRootCause` from a raw MongoDB dict. Lazy import to avoid circular dependency. Mirrors `_parse_pr_ai_response` construction pattern |

#### Manual Investigation Flow (unchanged)

| Function | Purpose |
|---|---|
| `run_investigation(...)` | Single-asset investigation triggered by chat UI. Traverses lineage → builds context → calls AI → stores `RootCause` |
| `build_ai_context(...)` | Prompt builder for single-asset flow. Returns a structured string with lineage nodes and break point |
| `call_ai_layer(...)` | Calls configured LLM, parses response into `RootCause`. Retries up to 3 times |

#### Shared LLM Provider Adapters (used by both flows)

| Function | Provider | Notes |
|---|---|---|
| `_call_groq(prompt, key)` | Groq | Default provider. Used when `DEFAULT_LLM_PROVIDER=groq` or model starts with `llama` |
| `_call_openai(prompt)` | OpenAI | Used when model starts with `gpt` |
| `_call_claude(prompt)` | Anthropic | Used otherwise. Passes system prompt as top-level `system` field |

All three strip markdown fences from response before JSON parsing. All three use the same strict system prompt: `"You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks, no explanation outside the JSON object."`

#### PR Bot Investigation Flow (new)

| Function | Purpose |
|---|---|
| `merge_lineage_subgraphs(subgraphs)` | Merges N `(source_fqn, LineageSubgraph)` tuples into one. Deduplicates nodes by FQN — first occurrence wins. Annotates each node with `raw_metadata["source_assets"]` tracking which upstream PR file it was reached from. Escalates severity if the same node appears in multiple subgraphs with different severities. Deduplicates edges by `(from_fqn, to_fqn)` pair |
| `build_pr_ai_context(asset_fqn_map, merged_subgraph, pr_number)` | Builds the multi-asset AI prompt. Includes all changed assets with stripped patches, the merged lineage graph with `[reachable from: ...]` annotations, the full response schema with per-cause error + fix structure, deduplication rules, and severity enum values injected dynamically. Estimates and logs token count before sending |
| `_parse_pr_ai_response(response)` | Validates required top-level keys first. Constructs every nested model explicitly. Try/catch at changed_assets level, downstream_impacts level, and causes level independently — skips malformed entries with index-specific warnings, never crashes the whole parse |
| `call_pr_ai_layer(ai_context)` | Calls configured LLM, parses into `PRRootCause` via `_parse_pr_ai_response`. Retries up to 3 times if parse fails (not just if HTTP fails) |
| `run_pr_investigation(...)` | Full PR pipeline, called as FastAPI background task. 6 steps: (1) traverse lineage per FQN, (2) merge subgraphs, (3) build prompt, (4) AI call, (5) store result, (6) update PR comment |

---

### `routes/github_routes.py`

FastAPI router. Prefix: `/github`. Tag: `github`.

#### PR Webhook — `POST /webhook`

The entry point for all GitHub PR events. Rewritten for multi-asset flow.

**Gate sequence (fast-fail order):**

| Gate | Check |
|---|---|
| 1 | `connection_id` and `user_id` query params present |
| 2 | `X-Hub-Signature-256` header present |
| 3 | HMAC signature valid |
| 4 | Payload parses as `PRWebhookEvent` |
| 5 | Event is `pull_request` with action `opened` or `synchronize` |
| 6 | Connection document found — derives `trusted_user_id` from DB (never from request) |
| 7 | GitHub installation token obtained |

**Processing sequence (after all gates pass):**

```
parse_pr_diff()           → all changed files
filter_relevant_files()   → data-relevant files only (.sql + dbt .yml)
derive_fqns()             → asset_fqn_map: key → (fqn, approximate, stripped_patch)
create_investigation()    → MongoDB document, event_type="github_pr"
render_placeholder_comment() → immediate comment posted to PR
background_task →
    run_pr_investigation()
        traverse_upstream() × N assets
        merge_lineage_subgraphs()
        build_pr_ai_context()
        call_pr_ai_layer()
        store pr_root_cause on investigation document
        render_pr_comment() → update placeholder comment in-place
```

**Response (202):**
```json
{
  "pr_number": 42,
  "analyzed": true,
  "investigation_id": "...",
  "relevant_files": 3,
  "total_files": 7,
  "asset_fqns": ["finance.revenue", "users.orders"],
  "comment_id": "...",
  "message": "Analysis started for 3 data file(s). Comment posted to PR."
}
```

#### OAuth Flow Routes (unchanged)

| Route | Purpose |
|---|---|
| `GET /oauth/start` | Redirects to GitHub OAuth authorize URL with encoded state |
| `GET /oauth/callback` | Exchanges code for token, fetches profile + installations, stores registration, issues fresh JWT |
| `POST /oauth/select-installation` | User picks which GitHub App installation to use for this connection |
| `POST /oauth/configure-webhook` | Registers repo-level webhook on GitHub, stores `webhook_id` |

#### Webhook Lifecycle Routes (unchanged)

| Route | Purpose |
|---|---|
| `GET /oauth/status` | Returns full `GitHubRegistrationStatusResponse` for a connection |
| `GET /webhook/verify` | Fetches webhook from GitHub API to confirm it is still active |
| `POST /webhook/cleanup` | Deletes webhook from GitHub, clears local state |

---

## Full PR Bot Flow (end-to-end)

```
Developer opens/updates PR
          │
          ▼
POST /github/webhook
  ├── Signature verified
  ├── Event filtered (pull_request + opened/synchronized)
  ├── Connection looked up → trusted_user_id derived
  ├── Installation token fetched
  ├── All changed files fetched from GitHub
  ├── Relevant files filtered (.sql + dbt .yml only)
  ├── FQNs derived per file (multi-model yml → multiple FQNs)
  ├── Investigation document created (event_type: github_pr)
  ├── Placeholder comment posted to PR immediately ← PR author sees this
  └── Background task queued → 202 returned
          │
          ▼ (background)
run_pr_investigation()
  ├── traverse_upstream() for each FQN (max_depth=3)
  ├── detect_break_point() per subgraph
  ├── merge_lineage_subgraphs()
  │     ├── Deduplicate nodes by FQN
  │     ├── Annotate each node with source_assets[]
  │     ├── Escalate severity across subgraphs
  │     └── Deduplicate edges by (from, to) pair
  ├── build_pr_ai_context()
  │     ├── All changed assets + stripped patches
  │     ├── Merged lineage with [reachable from:] annotations
  │     ├── Token estimate logged
  │     └── Strict JSON-only response schema
  ├── call_pr_ai_layer() → PRRootCause (3 retries)
  │     └── _parse_pr_ai_response()
  │           ├── Validate required top-level keys
  │           ├── Construct ChangedAssetSummary[] explicitly
  │           ├── Construct DownstreamImpact[] explicitly
  │           └── Construct AssetCause[] + ErrorLocation + CauseFix per cause
  ├── Store pr_root_cause on investigation document
  └── render_pr_comment() → update placeholder in-place ← PR author sees this
```

---

## Optimizations Implemented

### Input Size

| Optimization | Implementation | Benefit |
|---|---|---|
| Context line stripping | `_strip_context_lines()` — keeps only `+/-` lines | 500-line file with 8 changes → 8 lines sent to AI |
| No arbitrary truncation | All changed lines always sent | No risk of missing the breaking change |
| Large patch warning | `_warn_large_patch()` — logs if >200 changed lines | Visibility without data loss |
| Lineage depth cap | `max_depth=3` in `traverse_upstream()` | Prevents runaway graph traversal |
| Token estimate logging | `len(context) // 4` logged before AI call | Operational visibility |

### Response Reliability

| Optimization | Implementation | Benefit |
|---|---|---|
| Top-level key validation | `_parse_pr_ai_response()` checks required keys before deep parse | Fast fail on malformed responses |
| Explicit model construction | Every nested Pydantic model constructed field-by-field | No silent coercion failures |
| Granular try/catch | Separate try/catch at changed_assets, downstream_impacts, causes levels | One bad entry skipped, rest succeed |
| Parse-level retry | `call_pr_ai_layer()` retries if parse fails, not just HTTP | Handles intermittent LLM JSON errors |
| Strict system prompt | All three providers receive identical JSON-only system instruction | Reduces markdown wrapping in responses |

### Deduplication

| Optimization | Implementation | Benefit |
|---|---|---|
| Multi-model yml expansion | `_extract_fqn_from_yml()` returns `List[str]` | One schema yml with N models → N separate lineage traversals |
| Node deduplication | `merge_lineage_subgraphs()` deduplicates by FQN | Shared downstream assets appear once with all causes |
| Severity escalation | Highest severity wins across subgraphs for same node | Most critical impact always surfaced |
| Edge deduplication | `(from_fqn, to_fqn)` set | No duplicate lineage edges in merged graph |
| Downstream impact deduplication | One `DownstreamImpact` per FQN, N causes inside | PR comment is clean — one block per broken asset |

### Security

| Optimization | Implementation | Benefit |
|---|---|---|
| Trusted user_id | Derived from DB connection document, never from query param | Prevents user impersonation via webhook |
| Signature verification | HMAC-SHA256 on raw request body before any parsing | Rejects forged webhooks |
| State encoding | OAuth state is base64-encoded JSON, decoded on callback | Connection/user binding survives redirect |

---

## Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `GITHUB_APP_ID` | `github_controller` | GitHub App identifier for JWT generation |
| `GITHUB_APP_PRIVATE_KEY` | `github_controller` | RSA private key for JWT signing |
| `GITHUB_WEBHOOK_SECRET` | `github_controller` | HMAC secret for signature verification |
| `GITHUB_TEST_PAT` | `github_controller` | Dev mode — bypasses App JWT flow |
| `GITHUB_CLIENT_ID` | `github_routes` | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | `github_routes` | OAuth App client secret |
| `GITHUB_REDIRECT_URI` | `github_routes` | OAuth callback URL |
| `FRONTEND_SUCCESS_URL` | `github_routes` | Redirect target after successful OAuth |
| `FRONTEND_ERROR_URL` | `github_routes` | Redirect target on OAuth failure |
| `API_BASE_URL` | `github_routes` | Used to construct webhook URL |
| `MONGO_URI` | `investigation_controller` | MongoDB connection string |
| `AI_MODEL` | `investigation_controller` | Model name passed to LLM provider |
| `DEFAULT_LLM_PROVIDER` | `investigation_controller` | `groq` \| `openai` \| `claude` |
| `GROQ_API_KEY` | `investigation_controller` | Groq API key |
| `OPENAI_API_KEY` | `investigation_controller` | OpenAI API key |
| `CLAUDE_API_KEY` | `investigation_controller` | Anthropic API key |

---

## models/investigations.py — Required Manual Change

The `InvestigationResponse` model needs one addition to surface `pr_root_cause` via `get_investigation`:

```python
# In class InvestigationResponse — add after root_cause field:
from typing import Optional, List, Any   # add Any

pr_root_cause: Optional[Any] = None   # PRRootCause at runtime — Any avoids circular import
```