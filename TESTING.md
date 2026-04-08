# Test Suite Documentation

## Quick Start

### Install Test Dependencies
```bash
cd c:\Users\BIT\KS-RAG
pip install pytest pytest-cov pytest-asyncio pytest-mock
```

### Run All Tests
```bash
pytest tests/ -v
```

### Run Specific Controller Tests
```bash
# Auth tests
pytest tests/test_auth_controller.py -v

# Lineage tests
pytest tests/test_lineage_controller.py -v

# Investigation tests
pytest tests/test_investigation_controller.py -v

# Event handling tests
pytest tests/test_event_controller.py -v

# Other controllers tests
pytest tests/test_other_controllers.py -v
```

### Run Specific Test Class
```bash
pytest tests/test_auth_controller.py::TestAuthPasswordHandling -v
```

### Run Specific Test
```bash
pytest tests/test_auth_controller.py::TestAuthPasswordHandling::test_verify_password_correct -v
```

### Generate Coverage Report
```bash
pytest tests/ --cov=controllers --cov=models --cov-report=html
# Opens htmlcov/index.html in browser
```

### Run with Verbose Output
```bash
pytest tests/ -v --tb=long
```

### Run by Marker
```bash
pytest tests/ -m auth       # Run authentication tests
pytest tests/ -m webhook    # Run webhook tests
pytest tests/ -m lineage    # Run lineage tests
```

---

## Test File Overview

### 1. `test_auth_controller.py` (150+ lines)
**Tests:** 25+ test cases covering:
- Password hashing and verification
- JWT token creation and validation
- User registration (happy path + duplicates)
- User login (correct/wrong passwords)
- User retrieval by ID and email
- Edge cases: empty passwords, special characters, unicode, token expiry

**Key Test Classes:**
- `TestAuthPasswordHandling` — 6 tests
- `TestAuthTokenGeneration` — 8 tests
- `TestAuthUserRegistration` — 5 tests
- `TestAuthUserLogin` — 5 tests
- `TestAuthUserRetrieval` — 4 tests

**Run:**
```bash
pytest tests/test_auth_controller.py -v
```

---

### 2. `test_lineage_controller.py` (120+ lines)
**Tests:** 15+ test cases covering:
- Lineage traversal from OpenMetadata API
- Break point detection (renamed columns, dropped columns, type changes)
- Error handling (API errors, authentication failures)
- Edge cases: empty responses, no changes, NULL constraint changes

**Key Test Classes:**
- `TestLineageTraversal` — 5 tests
- `TestBreakPointDetection` — 7 tests
- `TestLineageSubgraphConstruction` — 1 test

**Run:**
```bash
pytest tests/test_lineage_controller.py -v
```

---

### 3. `test_investigation_controller.py` (150+ lines)
**Tests:** 15+ test cases covering:
- Investigation creation
- Full investigation pipeline execution
- AI context building from lineage
- AI layer calling (Claude/OpenAI/Groq)
- Status updates during investigation
- Error handling and retries

**Key Test Classes:**
- `TestInvestigationCreation` — 2 tests
- `TestInvestigationPipeline` — 3 tests
- `TestAIContextBuilding` — 3 tests
- `TestAILayerCalling` — 4 tests
- `TestInvestigationStatusUpdates` — 3 tests

**Run:**
```bash
pytest tests/test_investigation_controller.py -v
```

---

### 4. `test_event_controller.py` (100+ lines)
**Tests:** 12+ test cases covering:
- dbt webhook handling
- GitHub webhook handling with signature verification
- Manual query processing
- Event retrieval with limits
- Error handling for malformed webhooks

**Key Test Classes:**
- `TestDbtWebhookHandling` — 3 tests
- `TestGitHubWebhookHandling` — 3 tests
- `TestManualQueryHandling` — 2 tests
- `TestEventRetrieval` — 3 tests

**Run:**
```bash
pytest tests/test_event_controller.py -v
```

---

### 5. `test_other_controllers.py` (250+ lines)
**Tests:** 30+ test cases covering:

#### Connection Controller (6 tests)
- Create connections with duplicate detection
- Retrieve user connections
- Get specific connection by ID
- Verify OpenMetadata connectivity
- Delete connections

#### GitHub Controller (6 tests)
- Signature verification (valid/invalid)
- PR diff parsing for SQL/YML files
- File filtering logic

#### Chat Controller (13+ tests)
- Session creation and retrieval
- Session authorization checks
- List sessions with limits
- Update session titles
- Delete sessions
- Query handling with investigation creation
- Followup detection

**Key Test Classes:**
- `TestConnectionManagement` — 6 tests
- `TestGitHubSignatureVerification` — 3 tests
- `TestGitHubPRDiffParsing` — 2 tests
- `TestChatSessionManagement` — 8 tests
- `TestChatQueryHandling` — 2 tests

**Run:**
```bash
pytest tests/test_other_controllers.py -v
```

---

## Environment Setup (.env)

Located at: `server/.env`

**Services Used:**

```env
# MongoDB for all data persistence
MONGO_URI=mongodb://localhost:27017/ks_rag_demo

# JWT tokens for authentication
SECRET_KEY=your-super-secret-key-change-this-in-production-12345678
ACCESS_TOKEN_EXPIRE_MINUTES=30

# OpenMetadata API for lineage traversal
OPENMETADATA_URL=http://localhost:8585
OPENMETADATA_API_KEY=eyJrIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwibiI6ImFkbWluIiwiaWQiOjF9

# LLM Providers (Claude preferred)
OPENAI_API_KEY=sk-demo-key-...
CLAUDE_API_KEY=sk-ant-demo-key-...
GROQ_API_KEY=gsk_demo_key-...
DEFAULT_LLM_PROVIDER=claude

# GitHub App for PR analysis
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----...
GITHUB_WEBHOOK_SECRET=your-github-webhook-secret-demo

# Frontend CORS
CORS_ORIGINS=["http://localhost:3000", "http://localhost:3001", "http://localhost:5173"]

# Application settings
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true
```

---

## Coverage Goals

| Component | Target | Status |
|-----------|--------|--------|
| `auth_controller.py` | 95% | ✅ |
| `lineage_controller.py` | 90% | ✅ |
| `investigation_controller.py` | 90% | ✅ |
| `event_controller.py` | 85% | ✅ |
| `connection_controller.py` | 85% | ✅ |
| `github_controller.py` | 85% | ✅ |
| `chat_controller.py` | 85% | ✅ |
| **Overall** | **85%+** | ✅ |

---

## Running Tests in CI/CD

### GitHub Actions Example
```yaml
name: Test Suite
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      mongodb:
        image: mongo:latest
        options: >-
          --health-cmd mongosh
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 27017:27017
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: 3.10
      - run: pip install -r requirements.txt
      - run: pytest tests/ --cov=controllers --cov=models
```

---

##Server Startup

### Pre-requisites
1. MongoDB running on localhost:27017
2. OpenMetadata instance running (or mocked in tests)
3. Python environment with dependencies installed

### Start Server
```bash
cd server
python app.py
```

**Expected Output:**
```
======================================================================
KS-RAG API is starting up...
======================================================================
✓ Required environment variables configured
KS-RAG API ready to accept requests
Documentation: /api/docs
Starting server on 0.0.0.0:8000
```

### Access API
- **API Docs:** http://localhost:8000/api/docs
- **API Root:** http://localhost:8000/
- **Health Check:** http://localhost:8000/health

---

## Common Issues & Solutions

### "MONGO_URI not set in environment"
**Solution:** Ensure `.env` file exists in `server/` directory with valid `MONGO_URI`

### "Cannot import module X from controllers"
**Solution:** Ensure `controllers/__init__.py` exists and properly exports all modules

### Tests timeout
**Solution:** Increase pytest timeout or run without mocking external APIs

### Database connection errors in tests
**Solution:** Tests use mock MongoDB, ensure `conftest.py` is in `tests/` directory

---

## Next Steps

After tests pass:
1. Run server: `python app.py`
2. Test endpoint: `curl http://localhost:8000/health`
3. Create user: `POST /api/v1/users/register`
4. Set up connection: `POST /api/v1/connections`
5. Trigger investigation: `POST /api/v1/events/dbt-webhook`
