"""
KS-RAG API - Knowledge Source Root Cause Analysis Generator

Main FastAPI application that wires together:
- Authentication & user management
- OpenMetadata connections
- Event intake (dbt, GitHub, manual)
- Investigation pipeline
- Chat sessions
- GitHub PR integration
"""

import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Load environment
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="KS-RAG: Root Cause Analysis Generator",
    description="Intelligent lineage analysis and root cause detection for data pipelines",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json"
)

# ============================================================================
# CORS Configuration
# ============================================================================
CORS_ORIGINS = os.getenv("CORS_ORIGINS", '["http://localhost:3000", "http://localhost:3001"]')
try:
    import json
    origins = json.loads(CORS_ORIGINS)
except:
    origins = ["http://localhost:3000", "http://localhost:3001"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Route Imports
# ============================================================================
from routes.auth import router as auth_router
from routes.connections import router as connections_router
from routes.events import router as events_router
from routes.investigations import router as investigations_router
from routes.chats import router as chats_router
from routes.github import router as github_router

# ============================================================================
# Register Routers
# ============================================================================
app.include_router(auth_router, prefix="/api/v1")
app.include_router(connections_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
app.include_router(investigations_router, prefix="/api/v1")
app.include_router(chats_router, prefix="/api/v1")
app.include_router(github_router, prefix="/api/v1")

# ============================================================================
# Health Check Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Used by load balancers and monitoring systems.
    """
    return {
        "status": "ok",
        "service": "ks-rag",
        "version": "1.0.0"
    }


@app.get("/")
async def root():
    """
    API root endpoint.
    Provides welcome message and documentation links.
    """
    return {
        "name": "KS-RAG: Root Cause Analysis Generator",
        "version": "1.0.0",
        "description": "Intelligent lineage analysis and root cause detection for data pipelines",
        "docs": "/api/docs",
        "redoc": "/api/redoc",
        "openapi": "/api/openapi.json",
        "endpoints": {
            "auth": "/api/v1/users",
            "connections": "/api/v1/connections",
            "events": "/api/v1/events",
            "investigations": "/api/v1/investigations",
            "chats": "/api/v1/chats",
            "github": "/api/v1/github"
        }
    }


@app.get("/api/v1")
async def api_v1_root():
    """
    API v1 root endpoint.
    Lists all available endpoints.
    """
    return {
        "version": "1.0.0",
        "endpoints": {
            "authentication": {
                "register": "POST /api/v1/users/register",
                "login": "POST /api/v1/users/login",
                "me": "GET /api/v1/users/me",
                "refresh": "POST /api/v1/users/refresh"
            },
            "connections": {
                "create": "POST /api/v1/connections",
                "list": "GET /api/v1/connections",
                "get": "GET /api/v1/connections/{id}",
                "verify": "POST /api/v1/connections/{id}/verify",
                "delete": "DELETE /api/v1/connections/{id}"
            },
            "events": {
                "dbt_webhook": "POST /api/v1/events/dbt-webhook",
                "github_webhook": "POST /api/v1/events/github-webhook",
                "manual_query": "POST /api/v1/events/manual-query",
                "list": "GET /api/v1/events"
            },
            "investigations": {
                "create": "POST /api/v1/investigations",
                "get": "GET /api/v1/investigations/{id}",
                "list": "GET /api/v1/investigations",
                "status": "GET /api/v1/investigations/{id}/status"
            },
            "chats": {
                "create": "POST /api/v1/chats",
                "list": "GET /api/v1/chats",
                "get": "GET /api/v1/chats/{id}",
                "query": "POST /api/v1/chats/{id}/query",
                "update_title": "PUT /api/v1/chats/{id}/title",
                "delete": "DELETE /api/v1/chats/{id}"
            },
            "github": {
                "webhook": "POST /api/v1/github/webhook",
                "authorize": "POST /api/v1/github/authorize",
                "analysis": "GET /api/v1/github/pr-analysis/{pr_number}"
            }
        }
    }


# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Handle unexpected exceptions with consistent error format."""
    import traceback
    
    # Log full traceback
    print(f"ERROR: {exc}")
    traceback.print_exc()
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if os.getenv("DEBUG") == "true" else "An unexpected error occurred",
            "type": type(exc).__name__
        }
    )


# ============================================================================
# Startup Events
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    print("=" * 70)
    print("KS-RAG API is starting up...")
    print("=" * 70)
    
    # Verify environment
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        
        required_vars = ["MONGO_URI", "SECRET_KEY"]
        missing = [var for var in required_vars if not os.getenv(var)]
        
        if missing:
            print(f"⚠️  WARNING: Missing environment variables: {', '.join(missing)}")
        else:
            print("✓ Required environment variables configured")
    except Exception as e:
        print(f"⚠️  Startup check failed: {e}")
    
    print("KS-RAG API ready to accept requests")
    print("Documentation: /api/docs")


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown."""
    print("=" * 70)
    print("KS-RAG API is shutting down...")
    print("=" * 70)


if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", 8000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    
    print(f"Starting server on {host}:{port}")
    print(f"Debug mode: {debug}")
    
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info"
    )


@app.post("/query")
def query_docs(req: QueryRequest):
    """
    Query the stored embeddings (public endpoint - no authentication required).
    
    For one-off queries without chat history.
    Use POST /chats for persistent conversations.
    
    Request body:
    {
        "question": "Your question here"
    }
    
    Response:
    {
        "answer": "Generated answer",
        "sources": [{"text": "...", "pdf_name": "..."}],
        "question": "Your question"
    }
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        answer, sources = answer_query(question)
        return JSONResponse({
            "answer": answer,
            "sources": sources,
            "question": question
        })
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error during query: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)