<div align="center">
  <img src="./logo.png" alt="FixFlow Logo" width="35%">
</div>

# 🔍 FixFlow — AI-Powered Code & Data Dependency Failure Diagnosis

**An intelligent PR guard and root-cause engine for full-stack repositories.**

FixFlow automatically maps the dependency graph of an entire GitHub repository — spanning application code (React, NestJS, TypeORM), SQL migrations, and dbt data models — then uses that graph to detect breaking changes before they're merged, trace failures to their root cause, and deliver actionable, file-specific fixes.

No external metadata catalogue required. No OpenMetadata. No third-party lineage server. The graph is built directly from your repository files.

![Status](https://img.shields.io/badge/Backend-Active-green?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104-teal?style=for-the-badge&logo=fastapi)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

---

## 🎯 What FixFlow Does

FixFlow operates as a GitHub PR bot that intercepts pull requests and runs a multi-stage analysis pipeline:

1. **Repository Extraction** — Reads the live file tree from GitHub (via the Contents API) and classifies every file by stack layer (dbt model, NestJS service, TypeORM entity, React component, raw SQL migration, etc.)
2. **Dependency Graph Construction** — Parses `ref()` / `source()` calls in dbt SQL, `REFERENCES` / `FROM` / `JOIN` in migration SQL, import chains in TypeScript, and cross-layer foreign key wiring — building a unified, persistent lineage graph stored in MongoDB and cached in Redis
3. **Contract Validation** — Before any AI call, runs deterministic checks: column drops against downstream consumers, FK `REFERENCES` pointing to non-existent columns, migration ordering violations, and dbt `sources.yml` drift vs actual table schema
4. **PR Impact Analysis** — Filters changed files to data-relevant assets, derives FQNs, traverses the graph downstream, and sends a single multi-asset AI prompt to find every breakage with exact file + clause + line attribution
5. **Automated PR Comment** — Posts a placeholder comment within seconds of webhook receipt, then updates it in-place with the full structured report once analysis completes

---

## 🏗️ Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        INPUT LAYER                                    │
├──────────────────┬───────────────────────┬───────────────────────────┤
│  GitHub PR       │  Manual Query         │  dbt Test Webhook         │
│  Webhook         │  (Chat UI)            │  (future)                 │
└────────┬─────────┴──────────┬────────────┴──────────┬────────────────┘
         │                   │                        │
         └───────────────────┼────────────────────────┘
                             ▼
         ┌───────────────────────────────────────────┐
         │  EXTRACTOR PIPELINE                        │
         │  GitHub Contents API → file tree           │
         │  Classifier  → stack label per file        │
         │  Extractors  → structured dependency data  │
         │   ├── dbt_extractor     (ref/source/cols)  │
         │   ├── nestjs_extractor  (imports/injects)  │
         │   ├── react_extractor   (imports/hooks)    │
         │   └── typeorm_extractor (entities/FK)      │
         └─────────────────┬─────────────────────────┘
                           │
         ┌─────────────────▼─────────────────────────┐
         │  REPO LINEAGE GRAPH (repo_parser)           │
         │  Stored: MongoDB (persistent)               │
         │  Cached:  Redis  (TTL = 1 week)             │
         │  Provides: depends_on / referenced_by /     │
         │            column_usage per node            │
         └─────────────────┬─────────────────────────┘
                           │
         ┌─────────────────▼─────────────────────────┐
         │  CONTRACT VALIDATION (deterministic)        │
         │  ✓ Column drops vs downstream SQL          │
         │  ✓ FK REFERENCES vs actual columns         │
         │  ✓ Migration ordering violations           │
         │  ✓ dbt sources.yml schema drift            │
         └─────────────────┬─────────────────────────┘
                           │
         ┌─────────────────▼─────────────────────────┐
         │  AI INVESTIGATION LAYER                     │
         │  merge_lineage_subgraphs()                  │
         │  build_pr_ai_context()  → structured prompt │
         │  call_pr_ai_layer()     → PRRootCause       │
         │  Providers: Groq / OpenAI / Claude          │
         └─────────────────┬─────────────────────────┘
                           │
         ┌─────────────────▼─────────────────────────┐
         │  OUTPUT LAYER                               │
         ├──────────────────┬──────────────────────────┤
         │  GitHub PR       │  Investigation DB        │
         │  Comment         │  (MongoDB)               │
         │  (placeholder    │  Chat UI response        │
         │   → full report) │                          │
         └──────────────────┴──────────────────────────┘
```

---

## 📁 Project Structure

```
FixFlow/
├── .env                              # Docker Compose env (root)
├── docker-compose.yml                # MongoDB + Redis + Backend + Frontend
├── pr_bot_workflow.md                # Detailed PR bot design reference
│
├── server/                           # FastAPI backend
│   ├── app.py                        # Entry point, router registration
│   ├── requirements.txt
│   ├── Dockerfile
│   │
│   ├── extractor/                    # Standalone extraction pipeline
│   │   ├── classifiers/              # Stack + file-type classification
│   │   ├── extractors/               # Per-stack dependency extractors
│   │   │   ├── dbt_extractor.py      # ref() / source() / column parsing
│   │   │   ├── nestjs_extractor.py   # NestJS service/controller/DTO/module
│   │   │   ├── react_extractor.py    # React component / Next.js route
│   │   │   ├── typeorm_extractor.py  # Entity / repository / migration
│   │   │   └── registry.py          # hint-string → extractor function map
│   │   ├── models/                   # classification.py / identity.py / stack_rules.py
│   │   └── validators/
│   │
│   ├── controllers/
│   │   ├── repo_parser_controller.py # Core graph engine (standalone, no OpenMetadata)
│   │   ├── investigation_controller.py # PR + manual investigation lifecycle
│   │   ├── github_controller.py      # GitHub API, PR file filtering, comment rendering
│   │   ├── auth_controller.py        # JWT + bcrypt auth
│   │   ├── chat_controller.py        # Multi-turn chat sessions
│   │   ├── connection_controller.py  # GitHub connection management
│   │   └── event_controller.py       # Event normalisation → FailureEvent
│   │
│   ├── routes/
│   │   ├── github.py                 # PR webhook + OAuth + webhook lifecycle
│   │   ├── repo_parser_routes.py     # /repo-parser/* graph management routes
│   │   ├── events.py                 # /events/manual-query
│   │   ├── chats.py                  # Chat session CRUD + query
│   │   ├── connections.py            # Connection CRUD
│   │   ├── investigations.py         # Investigation read routes
│   │   └── auth.py                   # Register / login / me
│   │
│   └── models/                       # Pydantic v2 schemas
│       ├── github.py                 # PRRootCause, DownstreamImpact, AssetCause, CauseFix
│       ├── investigations.py
│       ├── lineage.py
│       ├── events.py
│       ├── users.py
│       ├── chat.py
│       └── base.py
│
├── frontend/                         # Next.js frontend
│   ├── app/
│   │   ├── components/
│   │   │   ├── AuthContext.tsx
│   │   │   ├── LoginSignup.tsx
│   │   │   ├── PipelineAutopsy.tsx
│   │   │   ├── InvestigationHistory.tsx
│   │   │   ├── ConnectionManager.tsx
│   │   │   └── LineageVisualizer.tsx  # D3.js dependency graph
│   │   └── hooks/useApi.ts
│   └── Dockerfile
│
└── tests/                            # Pytest test suites
```

---

## 🔑 Core Subsystems

### 1. Extractor Pipeline (`server/extractor/`)

Converts raw repository files into structured dependency data. Completely stack-agnostic — new stacks are added by writing one extractor module and registering it in `registry.py`.

**Current coverage:**

| Stack | File Types | What is Extracted |
|---|---|---|
| dbt | `.sql` models, `.yml` schemas | `ref()` calls, `source()` calls, column lists |
| NestJS | services, controllers, DTOs, modules | `@Injectable`, `@Controller`, import graph |
| React / Next.js | components, pages, route handlers | import graph, hook usage |
| TypeORM | entities, repositories, migrations | `@Entity`, `@Column`, `@ManyToOne` FK wiring |

**Planned:**

| Stack | Status |
|---|---|
| Express routes / controllers | Stub registered |
| Prisma schema + migrations | Stub registered |
| Mongoose schemas | Stub registered |

---

### 2. Repo Lineage Graph (`repo_parser_controller.py`)

**Zero OpenMetadata dependency.** Builds a full, cross-stack dependency graph by reading files directly from the GitHub repository.

**Graph nodes** carry: `fqn`, `file_path`, `node_type` (`dbt` or `migration`), `sql`, `columns`, `depends_on[]`, `referenced_by[]`, `column_usage{}`.

**Storage:**
- **MongoDB** (`repo_lineage_graphs` collection) — persistent, survives restarts
- **Redis** — hot cache with configurable TTL (default 1 week), falls back gracefully to MongoDB if Redis is unavailable

**Public API:**

| Function | Purpose |
|---|---|
| `scan_repo(...)` | Full repo scan — builds and stores graph |
| `get_repo_graph(...)` | Redis → MongoDB fallback chain |
| `get_downstream(...)` | BFS traversal of `referenced_by` edges |
| `get_column_dependents(...)` | Find downstream nodes that use specific columns |
| `build_subgraph_from_graph(...)` | Adapter: `RepoLineageGraph` → `LineageSubgraph` (used by investigation flow) |
| `update_graph_nodes(...)` | Incremental update after PR merge |

**REST routes (`/repo-parser/`):**

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/repo-parser/scan` | Trigger full repo scan |
| `GET` | `/repo-parser/graph` | Inspect stored graph summary |
| `GET` | `/repo-parser/graph/{fqn}` | Inspect a single node in detail |
| `POST` | `/repo-parser/refresh` | Force full rebuild ignoring TTL |
| `GET` | `/repo-parser/health` | Check graph freshness |

---

### 3. Contract Validation (deterministic, pre-AI)

Before any LLM call, `validate_contracts()` runs four hard checks against the current graph and the incoming PR diff:

| Check | What it catches |
|---|---|
| `_check_column_drops` | Downstream nodes referencing a column that the PR drops or renames |
| `_check_fk_column_existence` | `REFERENCES table(column)` where `column` does not exist in the target |
| `_check_migration_ordering` | Migration N depends on a table only defined by migration N+k (future migration) |
| `_check_source_yml_drift` | dbt `sources.yml` column list diverges from the actual migration column list |

If a `critical` or `high` violation is found, the AI verdict is hard-overridden to `safe_to_merge: false` regardless of the LLM response.

---

### 4. PR Bot Flow (`routes/github.py` + `investigation_controller.py`)

```
Developer opens / updates PR
          │
          ▼
POST /github/webhook
  ├── HMAC-SHA256 signature verified
  ├── Event filtered (pull_request + opened/synchronize)
  ├── Connection looked up → trusted_user_id derived from DB
  ├── Installation token fetched
  ├── All changed files fetched from GitHub API
  ├── Relevant files filtered (.sql + dbt .yml only)
  ├── FQNs derived per file (multi-model yml → multiple FQNs)
  ├── Investigation document created (event_type: github_pr)
  ├── Placeholder comment posted to PR immediately ← author sees this
  └── Background task queued → 202 returned immediately
          │
          ▼ (background)
run_pr_investigation()
  ├── build_subgraph_from_graph() per FQN  (repo_parser, no OpenMetadata)
  ├── validate_contracts()                 (deterministic pre-AI checks)
  ├── merge_lineage_subgraphs()
  │     ├── Deduplicate nodes by FQN
  │     ├── Annotate each node with source_assets[]
  │     ├── Escalate severity across subgraphs
  │     └── Deduplicate edges by (from, to) pair
  ├── build_pr_ai_context()
  │     ├── All changed assets + stripped patches (context lines removed)
  │     ├── Contract violations injected as structured block
  │     ├── Merged lineage with [reachable from:] annotations
  │     └── Token estimate logged before sending
  ├── call_pr_ai_layer() → PRRootCause  (3 retries, parse-level retry)
  ├── Hard-override safe_to_merge if critical/high violations found
  ├── Store pr_root_cause on investigation document
  └── render_pr_comment() → update placeholder in-place ← author sees this
```

**PR comment structure:**
- Severity header + merge verdict + confidence score
- "What Changed" table — asset FQN, change type, patch evidence
- Per broken asset: error type, exact file + clause + approximate line, ready-to-apply SQL fix snippet
- Investigation ID footer for traceability

---

### 5. AI Investigation Layer

Three LLM provider adapters, all using a strict JSON-only system prompt:

| Provider | Trigger |
|---|---|
| Groq (`llama-3.3-70b-versatile`) | `DEFAULT_LLM_PROVIDER=groq` or model name starts with `llama` |
| OpenAI | Model name starts with `gpt` |
| Anthropic Claude | All other model names |

All three strip markdown fences before JSON parsing. All three retry up to 3 times on parse failure (not just HTTP failure).

---

## 📊 Component Status

| Component | File | Status |
|---|---|---|
| Extractor pipeline | `server/extractor/` | ✅ Active |
| dbt extractor | `extractors/dbt_extractor.py` | ✅ Complete |
| NestJS extractor | `extractors/nestjs_extractor.py` | ✅ Complete |
| React extractor | `extractors/react_extractor.py` | ✅ Complete |
| TypeORM extractor | `extractors/typeorm_extractor.py` | ✅ Complete |
| Repo lineage graph | `repo_parser_controller.py` | ✅ Complete |
| Contract validation | `repo_parser_controller.py` | ✅ Complete |
| PR webhook + bot | `routes/github.py` | ✅ Complete |
| PR AI investigation | `investigation_controller.py` | ✅ Complete |
| Manual query (chat) | `routes/events.py` | ✅ Complete |
| Chat sessions | `routes/chats.py` | ✅ Complete |
| Auth (JWT + bcrypt) | `auth_controller.py` | ✅ Complete |
| Connection management | `connection_controller.py` | ✅ Complete |
| Graph REST routes | `repo_parser_routes.py` | ✅ Complete |
| Frontend (Next.js) | `frontend/` | ✅ Active |
| Express extractor | stub | 🔜 Planned |
| Prisma extractor | stub | 🔜 Planned |
| Mongoose extractor | stub | 🔜 Planned |

---

## 🚀 Quick Start (Docker — Recommended)

### Prerequisites
- Docker Desktop (running)
- 4GB+ RAM

### 1. Clone & Configure

```bash
git clone https://github.com/Krishna41357/FixFlow.git
cd FixFlow
```

Create a `.env` file at the **project root** (same level as `docker-compose.yml`):

```env
SECRET_KEY=your-secret-key-min-32-chars-change-this

# AI — choose one or all; unused keys can be set to "skip"
GROQ_API_KEY=gsk_your_groq_key_here
OPENAI_API_KEY=skip
CLAUDE_API_KEY=skip
DEFAULT_LLM_PROVIDER=groq

# GitHub App (for PR bot)
GITHUB_APP_ID=your-app-id
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."
GITHUB_WEBHOOK_SECRET=your-webhook-secret
GITHUB_CLIENT_ID=your-oauth-client-id
GITHUB_CLIENT_SECRET=your-oauth-client-secret
GITHUB_REDIRECT_URI=http://localhost:8000/github/oauth/callback

# For local dev without a GitHub App, use a PAT instead
GITHUB_TEST_PAT=ghp_your_personal_access_token

# Frontend redirect targets after OAuth
FRONTEND_SUCCESS_URL=http://localhost:3000/
FRONTEND_ERROR_URL=http://localhost:3000/error
API_BASE_URL=http://localhost:8000

DEBUG=true
```

### 2. Start the Stack

```bash
docker-compose up -d
```

Services started:
- **MongoDB 7.0** on port 27017
- **Redis 7** on port 6379
- **Backend (FastAPI)** on port 8000
- **Frontend (Next.js)** on port 3000

### 3. Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"ks-rag","version":"1.0.0"}
```

---

## 🚀 Local Development (Without Docker)

### Backend

```bash
cd server
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Configure `server/.env`:

```env
MONGO_URI=mongodb://localhost:27017/rag_database
REDIS_URL=redis://localhost:6379
SECRET_KEY=your-secret-key-min-32-chars

GROQ_API_KEY=gsk_your_key_here
DEFAULT_LLM_PROVIDER=groq
AI_MODEL=llama-3.3-70b-versatile

GITHUB_APP_ID=your-app-id
GITHUB_WEBHOOK_SECRET=your-webhook-secret
GITHUB_TEST_PAT=ghp_your_pat   # dev shortcut

CORS_ORIGINS=["http://localhost:3000"]
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true
```

```bash
python app.py
# Backend on http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev
# Frontend on http://localhost:3000
```

---

## 📖 API Reference

### Authentication

```bash
# Register
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","username":"myuser","password":"Testpass123"}'
# Returns: {"access_token": "eyJ...", "token_type": "bearer"}

# Login
curl -X POST http://localhost:8000/api/v1/users/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"Testpass123"}'
```

### Create a GitHub Connection

```bash
curl -X POST http://localhost:8000/api/v1/connections \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production",
    "github_repo": "owner/repo"
  }'
# Returns: {"id": "...", "name": "Production", ...}
```

### Trigger a Repo Graph Scan

```bash
curl -X POST http://localhost:8000/repo-parser/scan \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connection_id": "YOUR_CONNECTION_ID"}'
```

### Inspect the Graph

```bash
# Summary
curl "http://localhost:8000/repo-parser/graph?connection_id=CID" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Single node
curl "http://localhost:8000/repo-parser/graph/finance.revenue?connection_id=CID" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Manual Investigation (Chat)

```bash
# Start investigation
curl -X POST http://localhost:8000/api/v1/events/manual-query \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "asset_name": "finance.revenue",
    "question": "Why is this model failing?",
    "connection_id": "YOUR_CONNECTION_ID"
  }'
# Returns: {"event_id": "...", "status": "accepted"}

# Poll results
curl http://localhost:8000/api/v1/investigations \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### PR Webhook (GitHub → FixFlow)

```
POST /github/webhook?connection_id=CID&user_id=UID
Headers: X-Hub-Signature-256, X-GitHub-Event: pull_request
Body: GitHub PR webhook payload
```

Response (202):
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

### Verified Endpoints

| Endpoint | Method | Status |
|---|---|---|
| `/health` | GET | ✅ |
| `/api/v1/users/register` | POST | ✅ |
| `/api/v1/users/login` | POST | ✅ |
| `/api/v1/users/me` | GET | ✅ |
| `/api/v1/connections` | POST | ✅ |
| `/api/v1/connections` | GET | ✅ |
| `/api/v1/events/manual-query` | POST | ✅ |
| `/api/v1/investigations` | GET | ✅ |
| `/repo-parser/scan` | POST | ✅ |
| `/repo-parser/graph` | GET | ✅ |
| `/repo-parser/graph/{fqn}` | GET | ✅ |
| `/repo-parser/refresh` | POST | ✅ |
| `/repo-parser/health` | GET | ✅ |
| `/github/webhook` | POST | ✅ |
| `/github/oauth/start` | GET | ✅ |
| `/github/oauth/callback` | GET | ✅ |

---

## 🧑‍💻 Technology Stack

**Backend:**
- **Framework:** FastAPI 0.104
- **Database:** MongoDB 7.0 (`rag_database`)
- **Cache:** Redis 7 (graph hot-cache, TTL configurable)
- **Auth:** JWT (`python-jose`) + `bcrypt` (direct, no passlib)
- **AI:** Groq `llama-3.3-70b-versatile` (primary) · OpenAI · Anthropic Claude (fallbacks)
- **GitHub:** REST API (Contents, Trees, Pulls, Issues) + GitHub App JWT + OAuth

**Infrastructure:**
- Docker Compose: MongoDB + Redis + Backend + Frontend
- No Elasticsearch · No PostgreSQL · No OpenMetadata

**Frontend:**
- Next.js + React + TypeScript
- Tailwind CSS
- D3.js (lineage graph visualisation)

---

## ⚙️ Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `MONGO_URI` | ✅ | MongoDB connection string |
| `REDIS_URL` | optional | Redis URL (default `redis://localhost:6379`). Graceful degradation if absent |
| `SECRET_KEY` | ✅ | JWT signing key (min 32 chars) |
| `GROQ_API_KEY` | ✅* | Groq API key (*one AI key required) |
| `OPENAI_API_KEY` | optional | OpenAI key (or `skip`) |
| `CLAUDE_API_KEY` | optional | Anthropic key (or `skip`) |
| `DEFAULT_LLM_PROVIDER` | ✅ | `groq` / `openai` / `claude` |
| `AI_MODEL` | ✅ | e.g. `llama-3.3-70b-versatile` |
| `GITHUB_APP_ID` | ✅ (PR bot) | GitHub App identifier |
| `GITHUB_APP_PRIVATE_KEY` | ✅ (PR bot) | RSA private key for JWT signing |
| `GITHUB_WEBHOOK_SECRET` | ✅ (PR bot) | HMAC secret for signature verification |
| `GITHUB_TEST_PAT` | optional | Dev shortcut — bypasses App JWT flow |
| `GITHUB_CLIENT_ID` | ✅ (OAuth) | GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | ✅ (OAuth) | GitHub OAuth App client secret |
| `GITHUB_REDIRECT_URI` | ✅ (OAuth) | OAuth callback URL |
| `FRONTEND_SUCCESS_URL` | ✅ | Redirect after successful OAuth |
| `FRONTEND_ERROR_URL` | ✅ | Redirect on OAuth failure |
| `API_BASE_URL` | ✅ | Used to construct the webhook registration URL |
| `GRAPH_CACHE_TTL_HOURS` | optional | Redis TTL for repo graph (default `168` = 1 week) |

---

## ⚠️ Implementation Notes

### Database Name
All controllers hardcode `rag_database`:
```python
db = client["rag_database"]
```
Always use `MONGO_URI=mongodb://host:27017/rag_database`.

### Redis is Optional
If Redis is unavailable at startup, `repo_parser_controller` logs a warning and falls back to MongoDB for all graph reads. No crash, no degraded functionality — just slower cold reads.

### Password Hashing
Uses `bcrypt` directly — **not** `passlib` (incompatible with bcrypt 4.x+):
```python
import bcrypt as bcrypt_lib
# Passwords truncated at 72 bytes (bcrypt hard limit)
```

### AI Provider Selection
```env
DEFAULT_LLM_PROVIDER=groq
AI_MODEL=llama-3.3-70b-versatile
OPENAI_API_KEY=skip
CLAUDE_API_KEY=skip
```

### Graph Cache TTL
The repo lineage graph is rebuilt only when:
- An explicit `/repo-parser/scan` or `/repo-parser/refresh` call is made
- The cached graph is older than `GRAPH_CACHE_TTL_HOURS` (default 1 week)
- After a PR is merged (`update_graph_nodes()` does an incremental patch)

---

## 🐛 Troubleshooting

**`GROQ_API_KEY variable is not set` warning:**
- Ensure `.env` exists at project root (same folder as `docker-compose.yml`)
- Confirm `env_file: - .env` is present in the backend service in `docker-compose.yml`

**`mongo:7.0-alpine` not found:**
- Use `mongo:7.0` — MongoDB does not publish alpine variants for 7.x

**Redis connection refused:**
- Redis is optional. The backend will log `WARNING repo_parser: Redis unavailable` and continue using MongoDB only.

**Graph returns `graph_exists: false`:**
- You must explicitly scan first: `POST /repo-parser/scan` with a valid `connection_id`
- Ensure the GitHub token in your connection has `repo` scope

**Investigations return `[]`:**
- Check `MONGO_URI` in the running container points to `rag_database`
- Verify `GROQ_API_KEY` is not blank in the container

**Server returns 422 on connection creation:**
- Use `name` and `github_repo` (format `owner/repo`) fields — old field names `workspace_name` / `openmetadata_host` no longer exist

**`docker exec` returns error on Windows:**
- Remove `-it` flag: `docker exec container_name mongosh --eval "..."`

---

## 🧪 Tests

```bash
cd server

# Run all tests
pytest tests/ -v

# Specific suites
pytest tests/test_auth_controller.py -v
pytest tests/test_investigation_controller.py -v
pytest tests/test_event_controller.py -v
pytest tests/test_other_controllers.py -v

# Coverage
pytest tests/ --cov=controllers --cov-report=html
```

---

## 🛣️ Roadmap

| Feature | Status |
|---|---|
| dbt / NestJS / React / TypeORM extractors | ✅ Done |
| Repo lineage graph (MongoDB + Redis) | ✅ Done |
| Contract validation (4 checks) | ✅ Done |
| PR bot (multi-asset, AI + deterministic) | ✅ Done |
| Incremental graph update on PR merge | ✅ Done |
| Frontend lineage graph visualisation | ✅ Active |
| Express route extractor | 🔜 Next |
| Prisma schema extractor | 🔜 Next |
| Mongoose schema extractor | 🔜 Next |
| CI/CD pipeline status integration | 🔜 Planned |
| Slack / Teams notification adapter | 🔜 Planned |

---

## 📚 Documentation

| File | Purpose |
|---|---|
| [pr_bot_workflow.md](pr_bot_workflow.md) | Full PR bot design: schema, controller sections, gate sequence, optimisations |
| [server/ENV_SETUP.md](server/ENV_SETUP.md) | Detailed environment variable setup guide |
| [COMPONENT_CHECKLIST.md](COMPONENT_CHECKLIST.md) | Implementation checklist and status tracking |
| [TESTING.md](TESTING.md) | Test suite guide and coverage goals |

---

## 👨‍💻 Author

**Krishna Srivastava**  
GitHub: [@Krishna41357](https://github.com/Krishna41357)  
Email: krishnasrivastava41357@gmail.com

---

## 📄 License

MIT License — See LICENSE file for details

---

**FixFlow: catch breaking changes before they break production.**
