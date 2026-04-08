# 🔍 Pipeline Autopsy — AI-Powered Data Lineage Failure Diagnosis

**Hackathon Project:** Automatic root cause analysis for data pipeline failures using OpenMetadata lineage and AI reasoning.

When a data asset breaks — a dbt test fails, a column gets renamed, a pipeline produces nulls — **Pipeline Autopsy** automatically walks the column-level lineage graph to find the exact breaking node, then explains the root cause in plain English and surfaces a fix.

A GitHub PR bot catches schema-breaking changes **before they're merged**, posting AI-generated impact warnings directly in pull request comments.

![Status](https://img.shields.io/badge/Backend-100%25%20Complete-green?style=for-the-badge)
![Status](https://img.shields.io/badge/Tests-70%2B%20Comprehensive-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

---

## 🎯 Project Highlights

- **3 Input Sources:** dbt webhooks, GitHub PR webhooks, manual chat queries
- **Lineage Traversal:** Real-time upstream navigation via OpenMetadata API
- **Schema Diff Detection:** Identifies breaking changes (renames, drops, type changes)
- **AI Root Cause Analysis:** Claude/GPT analysis with structured JSON responses
- **Chat Interface:** Multi-turn conversation with investigation context
- **GitHub PR Bot:** Auto-comment with impact analysis before merge
- **70+ Comprehensive Tests:** Full coverage with edge cases and error handling
- **Production Ready:** Fully tested, documented, ready to deploy

---

## 📊 Project Status

| Component | Layer | Status | Code Location |
|-----------|-------|--------|-----------------|
| dbt Test Webhook | Input | ✅ Complete | [routes/events.py](server/routes/events.py) |
| GitHub PR Webhook | Input | ✅ Complete | [routes/github.py](server/routes/github.py) |
| Manual Query (Chat) | Input | ✅ Complete | [routes/chats.py](server/routes/chats.py) |
| Event Router | Core | ✅ Complete | [controllers/event_controller.py](server/controllers/event_controller.py) |
| Lineage Engine | Core | ✅ Complete | [controllers/lineage_controller.py](server/controllers/lineage_controller.py) |
| Context Builder | Core | ✅ Complete | [controllers/investigation_controller.py](server/controllers/investigation_controller.py) |
| AI Reasoning Layer | Core | ✅ Complete | [controllers/investigation_controller.py](server/controllers/investigation_controller.py) |
| Chat UI | Frontend | ⏳ Pending | [frontend/app/components/](frontend/app/components/) |
| Lineage Visualization | Frontend | ⏳ Pending | [frontend/app/components/](frontend/app/components/) |

**Backend:** 100% Complete (7 of 7 components)  
**Tests:** 70+ comprehensive test cases with edge case coverage  
**Frontend:** Pending (2 components, ~5-7 days estimated)  

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- MongoDB 5.0+
- API Keys: [OpenMetadata](https://docs.open-metadata.org/), [OpenAI](https://platform.openai.com/), [Claude](https://console.anthropic.com/)

### Backend Setup

**1. Clone & Install**
```bash
git clone https://github.com/Krishna41357/Pipeline-Autopsy.git
cd Pipeline-Autopsy/server

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

**2. Configure Environment**
```bash
cp .env.example .env  # Already provided with demo values
```

**Edit `server/.env` (key variables):**
```env
# MongoDB
MONGO_URI=mongodb://localhost:27017/pipeline_autopsy_db

# Authentication
SECRET_KEY=your-secret-key-change-in-production

# OpenMetadata
OPENMETADATA_URL=http://localhost:8585
OPENMETADATA_API_KEY=your-openmetadata-token

# AI Providers (choose one or more)
OPENAI_API_KEY=sk-...
CLAUDE_API_KEY=sk-ant-...
GROQ_API_KEY=gsk-...
DEFAULT_LLM_PROVIDER=claude

# GitHub App (for PR bot)
GITHUB_APP_ID=your-github-app-id
GITHUB_WEBHOOK_SECRET=your-webhook-secret

# API Configuration
CORS_ORIGINS=["http://localhost:3000", "http://localhost:8000"]
APP_HOST=0.0.0.0
APP_PORT=8000
```

**3. Start MongoDB**
```bash
# Ensure MongoDB is running
mongosh --eval "db.adminCommand('ping')"
```

**4. Run the Server**
```bash
python app.py
# Server starts on http://localhost:8000
```

**5. Verify Setup**
```bash
# Check health
curl http://localhost:8000/health

# View API documentation
curl http://localhost:8000/api/docs  # Swagger UI
```

### Run Tests

```bash
# Install test dependencies (already in requirements.txt)
pip install pytest pytest-cov

# Run all tests (70+ test cases)
pytest tests/ -v

# Run specific test suite
pytest tests/test_auth_controller.py -v
pytest tests/test_lineage_controller.py -v
pytest tests/test_investigation_controller.py -v
pytest tests/test_event_controller.py -v
pytest tests/test_other_controllers.py -v

# Generate coverage report
pytest tests/ --cov=controllers --cov-report=html
# Opens htmlcov/index.html
```

**Expected Test Output:**
```
tests/test_auth_controller.py ........................... 25 tests PASSED
tests/test_lineage_controller.py ........................ 15 tests PASSED
tests/test_investigation_controller.py .................. 15 tests PASSED
tests/test_event_controller.py .......................... 12 tests PASSED
tests/test_other_controllers.py ......................... 30 tests PASSED
======================== 97 tests in 2.34s =========================
```

### Frontend Setup (Next Phase)

```bash
cd ../frontend
npm install

# Create .env.local
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local

npm run dev
# Frontend starts on http://localhost:3000
```

---

## 📖 API Quick Reference

### Authentication
```bash
# Register user
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "secure123", "full_name": "Test User"}'

# Login (get JWT token)
TOKEN=$(curl -X POST http://localhost:8000/api/v1/users/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "secure123"}' \
  | jq -r '.access_token')

echo "Token: $TOKEN"
```

### Create Connection
```bash
curl -X POST http://localhost:8000/api/v1/connections \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_name": "Production",
    "openmetadata_url": "http://localhost:8585",
    "openmetadata_token": "your-token",
    "github_repo": "myteam/data-repo"
  }'
```

### Trigger Investigation (dbt Webhook)
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

### Start Chat Session & Query
```bash
# Create session
SESSION=$(curl -X POST http://localhost:8000/api/v1/chats \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' \
  | jq -r '.session_id')

# Send query
curl -X POST http://localhost:8000/api/v1/chats/$SESSION/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Why is my pipeline breaking?",
    "asset_fqn": "snowflake.prod.orders_daily"
  }'
```

---

## 🏗️ Architecture Overview

### System Design (9 Components)

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
         │  Normalize all inputs                │
         └────────┬────────────────────────────┘
                  │
        ┌─────────┴──────────────────────┐
        │  BACKEND CORE (Layer 3)         │
        ├────────────────────────────────┤
        │ ✓ Lineage Traversal            │
        │ ✓ Schema Diff Detection        │
        │ ✓ Context Building             │
        │ ✓ AI Root Cause Analysis       │
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
- **Database:** MongoDB 5.0+
- **Authentication:** JWT + HTTPBearer
- **LLM APIs:** Claude, OpenAI, Groq
- **External APIs:** OpenMetadata REST API, GitHub API
- **Testing:** Pytest with 70+ comprehensive test cases
- **Security:** bcrypt, passlib, CORS

**Frontend (Pending):**
- **Framework:** Next.js 16
- **Language:** TypeScript
- **Styling:** TailwindCSS
- **Visualization:** D3.js or Cytoscape.js
- **State Management:** React Context API

---

## 📁 Project Structure

```
Pipeline-Autopsy/
├── server/                          # FastAPI backend
│   ├── app.py                       # Entry point
│   ├── requirements.txt              # Dependencies (updated)
│   ├── .env                          # Configuration (demo values)
│   ├── context.md                    # Architecture documentation
│   │
│   ├── routes/                       # API endpoints
│   │   ├── auth.py                  # User registration/login
│   │   ├── connections.py           # OpenMetadata config
│   │   ├── events.py                # dbt webhook + event intake
│   │   ├── investigations.py         # Investigation status/details
│   │   ├── chats.py                 # Chat sessions + queries
│   │   └── github.py                # GitHub webhook + PR bot
│   │
│   ├── controllers/                  # Business logic
│   │   ├── auth_controller.py       # JWT + password handling
│   │   ├── lineage_controller.py    # OpenMetadata traversal
│   │   ├── investigation_controller.py # Investigation pipeline + AI
│   │   ├── event_controller.py      # Event normalization
│   │   ├── connection_controller.py # Connection management
│   │   ├── github_controller.py     # PR signature + diff parsing
│   │   └── chat_controller.py       # Chat session management
│   │
│   ├── models/                       # Pydantic schemas
│   │   ├── base.py                  # Common utilities
│   │   ├── user.py                  # User model
│   │   ├── chat.py                  # Chat/session/message models
│   │   ├── events.py                # Event models
│   │   ├── investigations.py         # Investigation models
│   │   ├── lineage.py               # Lineage/node models
│   │   └── github.py                # GitHub webhook payload
│   │
│   ├── tests/                        # Test suite (70+ tests)
│   │   ├── conftest.py              # Pytest fixtures
│   │   ├── test_auth_controller.py (25 tests)
│   │   ├── test_lineage_controller.py (15 tests)
│   │   ├── test_investigation_controller.py (15 tests)
│   │   ├── test_event_controller.py (12 tests)
│   │   └── test_other_controllers.py (30 tests)
│   │
│   └── utils/                        # Utilities
│       └── security.py              # Security helpers
│
├── frontend/                         # Next.js frontend (pending)
│   ├── app/
│   │   ├── layout.tsx               # Root layout
│   │   ├── page.tsx                 # Home page
│   │   └── components/
│   │       ├── ChatInterface.tsx     # ⏳ Chat UI (pending)
│   │       ├── LineageMap.tsx        # ⏳ Lineage visualization (pending)
│   │       ├── LoginSignup.tsx       # User auth UI
│   │       └── AuthContext.tsx       # Auth state management
│   ├── package.json
│   ├── tsconfig.json
│   └── .env.local                   # Frontend config
│
├── .env                              # (Demo values provided)
├── COMPONENT_CHECKLIST.md            # Status + setup guide
├── TESTING.md                        # Test suite documentation
├── context.md                        # Architecture + API reference
└── README.md                         # This file
```

---

## 🧪 Test Suite Overview

**70+ comprehensive tests** covering all 7 controllers:

| Test File | Tests | Coverage |
|-----------|-------|----------|
| test_auth_controller.py | 25 | Password, JWT, registration, login, user retrieval |
| test_lineage_controller.py | 15 | Lineage traversal, break point detection, error handling |
| test_investigation_controller.py | 15 | Investigation pipeline, AI context, AI calling with retry |
| test_event_controller.py | 12 | dbt/GitHub/manual webhooks, event retrieval |
| test_other_controllers.py | 30 | Connections, GitHub, chat (CRUD + auth) |

**Key Features:**
- ✅ Mock dependencies (MongoDB, OpenAI, OpenMetadata)
- ✅ Happy path + error cases + edge cases
- ✅ Authorization checks
- ✅ Retry logic testing
- ✅ 85%+ coverage target
- ✅ CI/CD ready (pytest + coverage)

See [TESTING.md](TESTING.md) for detailed test documentation.

---

## 📚 Key Documentation

| File | Purpose |
|------|---------|
| [context.md](server/context.md) | Architecture, component breakdown, API examples |
| [TESTING.md](TESTING.md) | Test suite guide, running tests, coverage goals |
| [COMPONENT_CHECKLIST.md](COMPONENT_CHECKLIST.md) | Setup instructions, troubleshooting, quick reference |

---

## 🔌 Key API Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| **Users** | | | |
| POST | `/api/v1/users/register` | Register new user | ❌ |
| POST | `/api/v1/users/login` | Login user (get JWT token) | ❌ |
| GET | `/api/v1/users/{id}` | Get user details | ✅ |
| **Connections** | | | |
| POST | `/api/v1/connections` | Create OpenMetadata connection | ✅ |
| GET | `/api/v1/connections` | List user's connections | ✅ |
| GET | `/api/v1/connections/{id}` | Get connection details | ✅ |
| POST | `/api/v1/connections/verify` | Test connection to OpenMetadata | ✅ |
| DELETE | `/api/v1/connections/{id}` | Delete connection | ✅ |
| **Events** | | | |
| POST | `/api/v1/events/dbt-webhook` | Handle dbt test failure | ❌ (webhook) |
| POST | `/api/v1/github/webhook` | Handle GitHub PR webhook | ❌ (signed) |
| **Investigations** | | | |
| GET | `/api/v1/investigations/{id}` | Get investigation details | ✅ |
| GET | `/api/v1/investigations/{id}/status` | Get investigation status | ✅ |
| GET | `/api/v1/investigations/user/{user_id}` | List user's investigations | ✅ |
| **Chat** | | | |
| POST | `/api/v1/chats` | Create chat session | ✅ |
| GET | `/api/v1/chats/{id}` | Get session details | ✅ |
| GET | `/api/v1/chats` | List user's sessions | ✅ |
| POST | `/api/v1/chats/{id}/query` | Send query + investigate | ✅ |
| PUT | `/api/v1/chats/{id}` | Update session title | ✅ |
| DELETE | `/api/v1/chats/{id}` | Delete session | ✅ |

---

## 🐛 Troubleshooting

**Server won't start:**
- Ensure MongoDB is running: `mongosh --eval "db.adminCommand('ping')"`
- Check `.env` file exists: `cat server/.env`
- Try: `python app.py` from server directory, not root

**Tests fail to import:**
- Run from root directory, not server/: `cd /c/Users/BIT/KS-RAG`
- Ensure pytest.ini exists in root
- Check Python path: `echo $PYTHONPATH`

**API returns 401 Unauthorized:**
- Ensure token is in Authorization header: `Authorization: Bearer <token>`
- Tokens expire after 30 minutes
- Check SECRET_KEY in .env matches token generation

**OpenMetadata connection fails:**
- Verify OPENMETADATA_URL is accessible
- Check OPENMETADATA_API_KEY is valid
- For testing, tests use mocked API responses

---

## 🎓 Learning Resources

- [OpenMetadata Documentation](https://docs.open-metadata.org/)
- [FastAPI Guide](https://fastapi.tiangolo.com/)
- [MongoDB Python Driver](https://pymongo.readthedocs.io/)
- [Pytest Documentation](https://docs.pytest.org/)

---

## 👨‍💻 Authors

**Krishna Srivastava**  
GitHub: [@Krishna41357](https://github.com/Krishna41357)  
Email: krishnasrivastava41357@gmail.com

---

## 📄 License

MIT License — See LICENSE file for details

---

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -am 'Add feature'`
4. Push to branch: `git push origin feature/your-feature`
5. Submit a pull request

---

**Built with ❤️ for data engineers who want visibility into their pipelines**
