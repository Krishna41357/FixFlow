#!/usr/bin/env python3
"""
Environment Validation Script for KS-RAG

Usage:
    python check_env.py              # Check all required variables
    python check_env.py --verbose    # Show all variables (including None values)
    python check_env.py --test-db    # Test database connection
    python check_env.py --test-ai    # Test AI API connection
    python check_env.py --full       # Run all tests
"""

import os
import sys
from pathlib import Path
from typing import Dict, Tuple

try:
    from dotenv import load_dotenv
    import requests
except ImportError:
    print("ERROR: Required packages missing. Run: pip install python-dotenv requests")
    sys.exit(1)


# Load environment
load_dotenv()


# Required variables by feature
REQUIRED_VARS = {
    "core": {
        "MONGO_URI": "MongoDB connection string",
        "SECRET_KEY": "JWT signing key",
    },
    "openmetadata": {
        "OPENMETADATA_URL": "OpenMetadata service URL",
        "OPENMETADATA_TOKEN": "OpenMetadata API token",
    },
    "webhooks": {
        "DBT_WEBHOOK_SECRET": "dbt webhook signature secret",
        "GITHUB_WEBHOOK_SECRET": "GitHub webhook signature secret",
    },
    "github": {
        "GITHUB_APP_ID": "GitHub App ID",
        "GITHUB_APP_PRIVATE_KEY": "GitHub App private key",
    },
    "ai": {
        "AI_MODEL": "AI model selection",
        "CLAUDE_API_KEY": "Anthropic Claude API key (required if using Claude)",
        "OPENAI_API_KEY": "OpenAI API key (required if using GPT)",
    },
}

OPTIONAL_VARS = {
    "LOG_LEVEL": ("INFO", "Logging level"),
    "DEBUG": ("false", "Debug mode"),
    "APP_ENV": ("development", "Application environment"),
    "APP_HOST": ("0.0.0.0", "Server host"),
    "APP_PORT": ("8000", "Server port"),
    "ACCESS_TOKEN_EXPIRE_MINUTES": ("30", "JWT expiration"),
    "RATE_LIMIT_RPM": ("60", "Rate limit"),
}


def check_var(name: str, required: bool = False) -> Tuple[str, bool]:
    """Check if a variable is set and return its value (masked if sensitive)."""
    value = os.getenv(name)
    
    if value is None:
        return (None, False)
    
    # Mask sensitive values
    if any(sensitive in name for sensitive in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
        if len(value) > 8:
            masked = value[:4] + "*" * (len(value) - 8) + value[-4:]
            return (masked, True)
    
    return (value, True)


def print_header(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def check_required_vars() -> Tuple[bool, Dict[str, bool]]:
    """Check all required variables."""
    print_header("REQUIRED VARIABLES")
    
    results = {}
    all_ok = True
    
    for category, vars_dict in REQUIRED_VARS.items():
        print(f"\n[{category.upper()}]")
        category_ok = True
        
        for var_name, description in vars_dict.items():
            value, exists = check_var(var_name, required=True)
            results[var_name] = exists
            
            status = "✓ SET" if exists else "✗ MISSING"
            symbol = "✓" if exists else "✗"
            
            print(f"  {symbol} {var_name:30} {status:15} {description}")
            
            if not exists:
                all_ok = False
                category_ok = False
        
        if not category_ok:
            print(f"    ⚠️  {category} is incomplete!")
    
    return all_ok, results


def check_optional_vars() -> None:
    """Check optional variables."""
    print_header("OPTIONAL VARIABLES")
    
    for var_name, (default, description) in OPTIONAL_VARS.items():
        value, exists = check_var(var_name)
        
        if exists:
            status = f"SET: {value}"
            symbol = "ℹ"
        else:
            status = f"DEFAULT: {default}"
            symbol = "○"
        
        print(f"  {symbol} {var_name:30} {status:30} {description}")


def test_mongodb_connection() -> bool:
    """Test MongoDB connection."""
    print_header("TESTING MONGODB CONNECTION")
    
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        print("  ✗ MONGO_URI not set")
        return False
    
    try:
        from pymongo import MongoClient
        
        print(f"  Connecting to MongoDB...")
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        
        print(f"  ✓ MongoDB connection successful!")
        
        # List databases
        try:
            databases = client.list_database_names()
            print(f"  ℹ Available databases: {', '.join(databases[:3])}{'...' if len(databases) > 3 else ''}")
        except:
            pass
        
        return True
    except Exception as e:
        print(f"  ✗ MongoDB connection failed: {e}")
        return False


def test_openmetadata_connection() -> bool:
    """Test OpenMetadata API connection."""
    print_header("TESTING OPENMETADATA CONNECTION")
    
    om_url = os.getenv("OPENMETADATA_URL")
    om_token = os.getenv("OPENMETADATA_TOKEN")
    
    if not om_url or not om_token:
        print("  ⚠ OPENMETADATA_URL or OPENMETADATA_TOKEN not set")
        return False
    
    try:
        endpoint = f"{om_url.rstrip('/')}/api/v1/system/status"
        headers = {"Authorization": f"Bearer {om_token}"}
        
        print(f"  Testing endpoint: {endpoint}")
        response = requests.get(endpoint, headers=headers, timeout=10)
        
        if response.status_code == 200:
            print(f"  ✓ OpenMetadata connection successful!")
            return True
        else:
            print(f"  ✗ OpenMetadata returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ OpenMetadata connection failed: {e}")
        return False


def test_claude_api() -> bool:
    """Test Claude API connection."""
    print_header("TESTING CLAUDE API")
    
    api_key = os.getenv("CLAUDE_API_KEY")
    if not api_key:
        print("  ⚠ CLAUDE_API_KEY not set")
        return False
    
    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        # Send minimal test request
        data = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Hi"}]
        }
        
        print(f"  Testing Claude API endpoint...")
        response = requests.post(url, json=data, headers=headers, timeout=10)
        
        if response.status_code == 200:
            print(f"  ✓ Claude API connection successful!")
            return True
        elif response.status_code == 401:
            print(f"  ✗ Claude API: Invalid API key")
            return False
        else:
            print(f"  ✗ Claude API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ Claude API connection failed: {e}")
        return False


def test_openai_api() -> bool:
    """Test OpenAI API connection."""
    print_header("TESTING OPENAI API")
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("  ⚠ OPENAI_API_KEY not set")
        return False
    
    try:
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        print(f"  Testing OpenAI API endpoint...")
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            print(f"  ✓ OpenAI API connection successful!")
            return True
        elif response.status_code == 401:
            print(f"  ✗ OpenAI API: Invalid API key")
            return False
        else:
            print(f"  ✗ OpenAI API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ OpenAI API connection failed: {e}")
        return False


def generate_secret_key() -> str:
    """Generate a secure random secret key."""
    import secrets
    return secrets.token_urlsafe(32)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="KS-RAG Environment Validation")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all variables")
    parser.add_argument("--test-db", action="store_true", help="Test database connection")
    parser.add_argument("--test-ai", action="store_true", help="Test AI API connections")
    parser.add_argument("--full", action="store_true", help="Run all tests")
    parser.add_argument("--generate-key", action="store_true", help="Generate a new SECRET_KEY")
    
    args = parser.parse_args()
    
    if args.generate_key:
        print_header("GENERATE SECRET KEY")
        key = generate_secret_key()
        print(f"\n  Copy this to your .env file:\n")
        print(f"    SECRET_KEY={key}\n")
        return
    
    # Check required variables
    all_required_ok, results = check_required_vars()
    
    # Check optional variables
    if args.verbose:
        check_optional_vars()
    
    # Run tests
    if args.full or args.test_db:
        test_mongodb_connection()
    
    if args.full or args.test_ai:
        test_openmetadata_connection()
        test_claude_api()
        test_openai_api()
    
    # Summary
    print_header("SUMMARY")
    
    if all_required_ok:
        print("  ✓ All required variables are set!")
        if not args.test_db and not args.test_ai and not args.full:
            print("  💡 Tip: Run with --full to test API connections")
    else:
        print("  ✗ Some required variables are missing!")
        print("  → Copy .env.example to .env and fill in the values")
        sys.exit(1)
    
    print()


if __name__ == "__main__":
    main()
