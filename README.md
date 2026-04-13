# 🔍 Pipeline Autopsy — AI-Powered Data Lineage Failure Diagnosis

**Hackathon Project:** Automatic root cause analysis for data pipeline failures using OpenMetadata lineage and AI reasoning.

When a data asset breaks — a dbt test fails, a column gets renamed, a pipeline produces nulls — **Pipeline Autopsy** automatically walks the column-level lineage graph to find the exact breaking node, then explains the root cause in plain English and surfaces a fix.

A GitHub PR bot catches schema-breaking changes **before they're merged**, posting AI-generated impact warnings directly in pull request comments.

![Status](https://img.shields.io/badge/Backend-100%25%20Complete-green?style=for-the-badge)
![Status](https://img.shields.io/badge/Tests-70%2B%20Comprehensive-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

---

## 🎯 Project Highlights

- **3 Input Sources:** dbt webhooks, GitHub PR webhooks, manual chat queries
- **Lineage Traversal:** Real-time upstream navigation via OpenMetadata API
- **Schema Diff Detection:** Identifies breaking changes (renames, drops, type changes)
- **AI Root Cause Analysis:** Groq (llama3-70b-8192) with structured JSON responses
- **Chat Interface:** Multi-turn conversation with investigation context
- **GitHub PR Bot:** Auto-comment with impact analysis before merge
- **70+ Comprehensive Tests:** Full coverage with edge cases and error handling
- **Docker Compose:** Full stack deployment with OpenMetadata, MongoDB, Elasticsearch

---

## 📊 Project Status

| Component | Layer | Status | Code Location |
|-----------|-------|--------|---------------|
| dbt Test Webhook | Input | ✅ Complete | [routes/events.py](server/routes/events.py) |
| GitHub PR Webhook | Input | ✅ Complete | [routes/github.py](server/routes/github.py) |
| Manual Query (Chat) | Input | ✅ Complete | [routes/chats.py](server/routes/chats.py) |
| Event Router | Core | ✅ Complete | [controllers/event_controller.py](server/controllers/event_controller.py) |
| Lineage Engine | Core | ✅ Complete | [controllers/lineage_controller.py](server/controllers/lineage_controller.py) |
| Context Builder | Core | ✅ Complete | [controllers/investigation_controller.py](server/controllers/investigation_controller.py) |
| AI Reasoning Layer | Core | ✅ Complete | [controllers/investigation_controller.py](server/controllers/investigation_controller.py) |
| Chat UI | Frontend | ✅ Complete | [frontend/app/components/](frontend/app/components/) |
| Lineage Visualization | Frontend | ✅ Complete | [frontend/app/components/LineageVisualizer.tsx](frontend/app/components/) |

**Backend:** 100% Complete (7 of 7 components)
**Tests:** 70+ comprehensive test cases
**Frontend:** 90% Complete (7 of 7 components implemented)

---

## ⚙️ Verified Working Endpoints (April 12, 2026)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/health` | GET | ✅ 200 OK | |
| `/api/v1/users/register` | POST | ✅ 201 Created | Returns JWT token |
| `/api/v1/users/login` | POST | ✅ 200 OK | Body JSON, not query params |
| `/api/v1/users/me` | GET | ✅ 200 OK | Bearer token required |
| `/api/v1/connections` | POST | ✅ 201 Created | Use `name` + `openmetadata_host` fields |
| `/api/v1/connections` | GET | ✅ 200 OK | Returns masked tokens |
| `/api/v1/events/manual-query` | POST | ✅ 202 Accepted | Starts async investigation |
| `/api/v1/investigations` | GET | ✅ 200 OK | Returns `[]` when empty |

---

## 🚀 Quick Start (Docker — Recommended)

### Prerequisites
- Docker Desktop (running)
- 8GB+ RAM (Elasticsearch needs ~2GB)

### 1. Clone & Configure

```bash
git clone https://github.com/Krishna41357/Pipeline-Autopsy.git
cd Pipeline-Autopsy
```

Create a `.env` file at the **project root** (same level as `docker-compose.yml`):

```env
SECRET_KEY=your-secret-key-min-32-chars-change-this
GROQ_API_KEY=gsk_your_groq_key_here
DEFAULT_LLM_PROVIDER=groq
AI_MODEL=llama3-70b-8192
OPENMETADATA_API_KEY=your-openmetadata-bot-token
DEBUG=true
```

> ⚠️ **Important:** The root `.env` is for Docker Compose.
> `server/.env` is for local non-Docker development.
> Both must exist with appropriate values.

### 2. Pull Docker Images (first time only)

```bash
docker pull mongo:7.0
docker pull postgres:13
docker pull elasticsearch:8.10.2
docker pull openmetadata/server:1.3.1
```

### 3. Start the Stack

```bash
docker-compose up -d
```

> OpenMetadata takes ~2-3 minutes to boot. Watch progress:
> ```bash
> docker-compose logs -f openmetadata-server
> ```
> Wait until you see: `Started @Xms to org.eclipse.jetty`

### 4. Get OpenMetadata Bot Token

1. Open `http://localhost:8585` in your browser
2. Sign up / log in
3. Navigate to **Settings → Integrations → Bots → ingestion-bot**
4. Copy the **JWT Token**
5. Update `OPENMETADATA_API_KEY` in your root `.env`
6. Restart backend: `docker-compose restart backend`

### 5. Verify Everything is Running

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","service":"ks-rag","version":"1.0.0"}

curl http://localhost:8585/api/v1/system/status
# Expected: {"status":"healthy"}
```

---

## 🚀 Local Development (Without Docker)

### Prerequisites
- Python 3.11 (recommended) or 3.10+
- MongoDB running locally
- Node.js 18+

### Backend Setup

```bash
cd server

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

Configure `server/.env`:

```env
# Database — must be rag_database (hardcoded in controllers)
MONGO_URI=mongodb://localhost:27017/rag_database

# Authentication
SECRET_KEY=your-secret-key-min-32-chars

# OpenMetadata
OPENMETADATA_URL=http://localhost:8585
OPENMETADATA_API_KEY=your-ingestion-bot-token

# AI — Groq recommended
GROQ_API_KEY=gsk_your_key_here
DEFAULT_LLM_PROVIDER=groq
AI_MODEL=llama3-70b-8192

# API
CORS_ORIGINS=["http://localhost:3000"]
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true
```

```bash
python app.py
# Server starts on http://localhost:8000
```

### Frontend Setup

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev
# Frontend starts on http://localhost:3000
```

---

## 📖 API Reference

### ⚠️ Known Behavioral Differences from Original Docs

These were discovered during live testing (April 12, 2026):

**1. Connection fields use different names than old docs:**
```json
// ✅ Correct
{
  "name": "Production",
  "openmetadata_host": "http://localhost:8585",
  "openmetadata_token": "eyJ...",
  "github_repo": "owner/repo"   // optional, must match pattern owner/repo
}

// ❌ Wrong (old docs)
{
  "workspace_name": "Production",
  "openmetadata_url": "http://localhost:8585"
}
```

**2. Manual query fields:**
```json
// ✅ Correct
{
  "asset_name": "sample_data.ecommerce_db.shopify.dim_customer",
  "question": "Why is this table failing?",
  "connection_id": "your-connection-id"
}

// ❌ Wrong (old docs)
{
  "asset_fqn": "...",
  "failure_query": "..."
}
```

**3. Login takes JSON body (not query params):**
```bash
# ✅ Correct
POST /api/v1/users/login
Body: {"email": "user@example.com", "password": "Testpass123"}

# ❌ Wrong
POST /api/v1/users/login?email=...&password=...
```

---

### Authentication

```bash
# Register
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "username": "myusername",
    "password": "Testpass123",
    "full_name": "Optional Name"
  }'
# Returns: {"access_token": "eyJ...", "token_type": "bearer"}

# Login
curl -X POST http://localhost:8000/api/v1/users/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "Testpass123"}'
```

### Create Connection

```bash
curl -X POST http://localhost:8000/api/v1/connections \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production",
    "openmetadata_host": "http://localhost:8585",
    "openmetadata_token": "your-ingestion-bot-token"
  }'
# Returns: {"id": "...", "name": "Production", ...}
```

### Trigger Investigation via Manual Query

```bash
# Step 1: Create event (starts investigation automatically)
curl -X POST http://localhost:8000/api/v1/events/manual-query \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "asset_name": "sample_data.ecommerce_db.shopify.dim_customer",
    "question": "Why is this table failing?",
    "connection_id": "YOUR_CONNECTION_ID"
  }'
# Returns: {"event_id": "...", "status": "accepted", "message": "Investigation started"}

# Step 2: Poll for results
curl http://localhost:8000/api/v1/investigations \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Trigger Investigation via dbt Webhook

```bash
curl -X POST http://localhost:8000/api/v1/events/dbt-webhook \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "run_id": "dbt_run_123",
      "node_id": "model.proj.orders",
      "error_message": "Column user_id not found",
      "status": "error"
    }
  }'
```

### Chat Sessions

```bash
# Create session
curl -X POST http://localhost:8000/api/v1/chats \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Orders Investigation"}'

# Send query
curl -X POST http://localhost:8000/api/v1/chats/SESSION_ID/query \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Why is my pipeline breaking?"}'
```

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     INPUT TRIGGERS (Layer 1)                 │
├─────────────────┬──────────────────┬─────────────────────────┤
│  dbt Test       │  GitHub PR        │  Manual Query           │
│  Webhook        │  Webhook          │  (Chat)                 │
└────────┬────────┴────────┬─────────┴──────────┬──────────────┘
         │                 │                    │
         └─────────────────┼────────────────────┘
                           ▼
         ┌─────────────────────────────────────┐
         │  EVENT ROUTER (Layer 2)              │
         │  Normalize all inputs → FailureEvent │
         └────────┬────────────────────────────┘
                  │
        ┌─────────┴──────────────────────┐
        │  BACKEND CORE (Layer 3)         │
        ├────────────────────────────────┤
        │ ✓ Lineage Traversal            │
        │   (OpenMetadata REST API)      │
        │ ✓ Schema Diff Detection        │
        │ ✓ Context Building             │
        │ ✓ AI Root Cause Analysis       │
        │   (Groq llama3-70b-8192)       │
        └────────┬──────────────────────┘
                 │
        ┌────────┴──────────────────────┐
        │  OUTPUTS (Layer 4)             │
        ├───────────────┬────────────────┤
        │ Chat Response │ GitHub Comment │
        │ JSON Result   │ Formatted Text │
        └───────────────┴────────────────┘
```

### Technology Stack

**Backend:**
- **Framework:** FastAPI 0.104.1
- **Database:** MongoDB (`rag_database` — hardcoded in controllers)
- **Authentication:** JWT (python-jose) + bcrypt (direct, no passlib)
- **LLM:** Groq `llama3-70b-8192` (primary), OpenAI/Claude (fallback)
- **External APIs:** OpenMetadata REST API, GitHub API
- **Testing:** Pytest 70+ test cases, 85%+ coverage

**Infrastructure (Docker):**
- MongoDB 7.0
- PostgreSQL 13 (OpenMetadata backend)
- Elasticsearch 8.10.2 (OpenMetadata search)
- OpenMetadata Server 1.3.1

**Frontend:**
- Next.js 16 + React 19 + TypeScript
- Tailwind CSS 4.0
- D3.js 7.8.5 (lineage visualization)

---

## ⚠️ Important Implementation Notes

### Database Name
All controllers hardcode `rag_database` as the MongoDB database name:
```python
db = client["rag_database"]  # hardcoded in all controllers
```
Always use `MONGO_URI=mongodb://host:27017/rag_database`.

### Password Hashing
Uses `bcrypt` directly — **not** passlib (incompatible with bcrypt 4.x+):
```python
import bcrypt as bcrypt_lib
# Passwords truncated to 72 bytes (bcrypt hard limit)
```

### AI Provider
Groq is the recommended provider. Claude/OpenAI keys can be set to `skip` if unused:
```env
DEFAULT_LLM_PROVIDER=groq
AI_MODEL=llama3-70b-8192
OPENAI_API_KEY=skip
CLAUDE_API_KEY=skip
```

### Docker .env Location
```
KS-RAG/
├── .env                  ← Docker Compose reads this (root)
├── docker-compose.yml
└── server/
    └── .env              ← Local development reads this
```

---

## 🐛 Troubleshooting

**`GROQ_API_KEY variable is not set` warning:**
- Ensure `.env` exists at project root (same folder as `docker-compose.yml`)
- Add `env_file: - .env` to backend service in `docker-compose.yml`

**`mongo:7.0-alpine` not found:**
- Use `mongo:7.0` — MongoDB does not publish alpine variants for 7.x

**OpenMetadata fails with `relation does not exist`:**
- Database migration hasn't run. Add `openmetadata-migrate` service to compose:
  ```yaml
  openmetadata-migrate:
    image: openmetadata/server:1.3.1
    command: "./bootstrap/bootstrap_storage.sh migrate-all"
    depends_on:
      postgresql:
        condition: service_healthy
    restart: "no"
  ```
- Run `docker-compose down -v` then `docker-compose up -d` for clean start

**Investigations return empty `[]`:**
- Check `MONGO_URI` in running container points to `rag_database`
- Verify `GROQ_API_KEY` and `OPENMETADATA_API_KEY` are not blank in container

**`docker exec` returns 500 error on Windows:**
- Remove `-it` flag: `docker exec container_name mongosh --eval "..."`
- Or update Docker Desktop to latest version

**Server returns 422 on connection creation:**
- Use `name` (not `workspace_name`) and `openmetadata_host` (not `openmetadata_url`)
- `github_repo` must match pattern `owner/repo` or be omitted entirely

**Token expires after 1 hour:**
- User session tokens expire. Use ingestion-bot JWT token for `OPENMETADATA_API_KEY` — it has `"exp": null` (never expires)

---

## 🧪 Test Suite

```bash
cd server

# Run all 70+ tests
pytest tests/ -v

# Run specific suite
pytest tests/test_auth_controller.py -v
pytest tests/test_lineage_controller.py -v
pytest tests/test_investigation_controller.py -v
pytest tests/test_event_controller.py -v
pytest tests/test_other_controllers.py -v

# Coverage report
pytest tests/ --cov=controllers --cov-report=html
```

| Test File | Tests | Coverage |
|-----------|-------|----------|
| test_auth_controller.py | 25 | Password, JWT, registration, login |
| test_lineage_controller.py | 15 | Traversal, break detection, errors |
| test_investigation_controller.py | 15 | Pipeline, AI context, retry logic |
| test_event_controller.py | 12 | dbt/GitHub/manual webhooks |
| test_other_controllers.py | 30 | Connections, GitHub, chat CRUD |

---

## 📁 Project Structure

```
Pipeline-Autopsy/
├── .env                              # ← Docker Compose env (root)
├── docker-compose.yml                # Full stack deployment
├── README.md
│
├── server/                           # FastAPI backend
│   ├── app.py                        # Entry point
│   ├── requirements.txt              # Python dependencies
│   ├── Dockerfile                    # Python 3.11-slim
│   ├── .env                          # ← Local dev env (server/)
│   │
│   ├── routes/                       # API endpoints
│   │   ├── auth.py
│   │   ├── connections.py
│   │   ├── events.py
│   │   ├── investigations.py
│   │   ├── chats.py
│   │   └── github.py
│   │
│   ├── controllers/                  # Business logic
│   │   ├── auth_controller.py        # bcrypt direct (no passlib)
│   │   ├── lineage_controller.py     # OpenMetadata traversal
│   │   ├── investigation_controller.py
│   │   ├── event_controller.py
│   │   ├── connection_controller.py
│   │   ├── github_controller.py
│   │   └── chat_controller.py
│   │
│   ├── models/                       # Pydantic v2 schemas
│   │   ├── base.py
│   │   ├── users.py                  # ConnectionCreate uses name + openmetadata_host
│   │   ├── events.py                 # ManualQueryPayload uses asset_name + question
│   │   ├── investigations.py
│   │   ├── lineage.py
│   │   ├── github.py
│   │   └── chat.py
│   │
│   └── tests/                        # 70+ test cases
│       ├── conftest.py
│       ├── test_auth_controller.py
│       ├── test_lineage_controller.py
│       ├── test_investigation_controller.py
│       ├── test_event_controller.py
│       └── test_other_controllers.py
│
└── frontend/                         # Next.js 16 frontend
    ├── app/
    │   ├── components/
    │   │   ├── AuthContext.tsx
    │   │   ├── LoginSignup.tsx
    │   │   ├── PipelineAutopsy.tsx
    │   │   ├── InvestigationHistory.tsx
    │   │   ├── ConnectionManager.tsx
    │   │   └── LineageVisualizer.tsx  # D3.js graph
    │   └── hooks/
    │       └── useApi.ts
    ├── package.json
    └── Dockerfile
```

---

## 📚 Documentation

| File | Purpose |
|------|---------|
| [server/context.md](server/context.md) | Full architecture, patch notes, API examples |
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

**Built with ❤️ for data engineers who want visibility into their pipelines**