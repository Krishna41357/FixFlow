# Environment Setup Guide

## Quick Start

### 1. **Create your `.env` file**
```bash
cp .env.example .env
```

### 2. **Fill in the required values**
Open `.env` and update:
- `MONGO_URI` - Your MongoDB connection string
- `SECRET_KEY` - A secure random key for JWT signing
- `CLAUDE_API_KEY` or `OPENAI_API_KEY` - Your AI provider credentials
- GitHub App credentials (if using GitHub integration)
- OpenMetadata credentials

### 3. **Verify the setup**
```bash
python -c "from dotenv import load_dotenv; load_dotenv(); print('✓ .env loaded successfully')"
```

---

## Environment Variables Reference

### Database
- **MONGO_URI** (required)
  - MongoDB connection string
  - Local: `mongodb://localhost:27017`
  - Atlas: `mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority`

### Authentication
- **SECRET_KEY** (required) 
  - JWT signing key - MUST be changed in production
  - Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
  - Min 32 characters recommended
  
- **ACCESS_TOKEN_EXPIRE_MINUTES** (optional, default: 30)
  - JWT token TTL in minutes

### OpenMetadata
- **OPENMETADATA_URL** (required)
  - Base URL of your OpenMetadata instance
  - Example: `https://metadata.yourcompany.com`

- **OPENMETADATA_TOKEN** (required)
  - OpenMetadata API token
  - Generate in OpenMetadata UI → Settings → API tokens

### Webhooks
- **DBT_WEBHOOK_SECRET** (required if using dbt integration)
  - Generate in dbt Cloud → Account → Webhooks → Generate Webhook Secret
  - Min 32 characters

- **GITHUB_WEBHOOK_SECRET** (required if using GitHub integration)
  - Generate in GitHub App → Webhooks → Secret
  - Min 32 characters

### GitHub App (optional, for PR automation)
- **GITHUB_APP_ID**
  - Numeric GitHub App ID from GitHub App settings

- **GITHUB_APP_PRIVATE_KEY**
  - PEM-formatted private key from GitHub App settings
  - Multi-line: replace newlines with `\n` in .env

### AI/LLM
- **AI_MODEL** (optional, default: `claude-3-sonnet-20240229`)
  - Options: 
    - `claude-3-sonnet-20240229` (Anthropic)
    - `claude-3-opus-20240229` (Anthropic, more powerful)
    - `gpt-4-turbo` (OpenAI)
    - `gpt-4` (OpenAI)

- **CLAUDE_API_KEY** (required if using Claude)
  - Get from: https://console.anthropic.com/account/keys

- **OPENAI_API_KEY** (required if using GPT)
  - Get from: https://platform.openai.com/account/api-keys

### Logging & Debug
- **LOG_LEVEL** (optional, default: `INFO`)
  - Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

- **DEBUG** (optional, default: `false`)
  - Set to `true` for verbose output and detailed error messages

### Application
- **APP_ENV** (optional, default: `development`)
  - Options: `development`, `staging`, `production`

- **APP_HOST** (optional, default: `0.0.0.0`)
  - Server host address

- **APP_PORT** (optional, default: `8000`)
  - Server port

- **CORS_ORIGINS** (optional)
  - JSON array of allowed origins: `["http://localhost:3000"]`

---

## Setup by Environment

### Local Development

**Requirements:**
- MongoDB running locally: `mongod`
- Python 3.9+
- API keys for OpenAI or Claude

**Setup:**
```bash
# Copy template
cp .env.local.example .env

# Update with your actual API keys
nano .env

# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn app:app --reload
```

---

### Docker Deployment

Create a `.env.docker` file:
```bash
MONGO_URI=mongodb://mongo:27017
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
DEBUG=false
```

In `docker-compose.yml`:
```yaml
services:
  app:
    env_file: .env.docker
    environment:
      - PYTHONUNBUFFERED=1
```

---

### Production Deployment

**Security checklist:**
- [ ] Use strong `SECRET_KEY` (32+ chars, random)
- [ ] Use environment-specific credentials
- [ ] Never commit `.env` file to git
- [ ] Encrypt `OPENMETADATA_TOKEN` and API keys at rest
- [ ] Use secret management tool (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault)
- [ ] Set `DEBUG=false`
- [ ] Set `APP_ENV=production`
- [ ] Use HTTPS for OpenMetadata URL
- [ ] Rotate API keys regularly

**Secret Management Options:**
1. **AWS Secrets Manager**
   ```python
   import boto3
   sm = boto3.client('secretsmanager')
   secret = sm.get_secret_value(SecretId='ks-rag-env')
   ```

2. **Azure Key Vault**
   ```python
   from azure.identity import DefaultAzureCredential
   from azure.keyvault.secrets import SecretClient
   
   credential = DefaultAzureCredential()
   client = SecretClient(vault_url="https://...", credential=credential)
   secret = client.get_secret("secret-name")
   ```

3. **HashiCorp Vault**
   ```python
   import hvac
   client = hvac.Client(url='http://localhost:8200')
   secret = client.secrets.kv.read_secret_version(path='ks-rag')
   ```

---

## Troubleshooting

### "MONGO_URI not set in environment"
```bash
# Check if .env is in the right location
ls -la .env

# Verify load_dotenv() is called in your code
# Check: top of app.py or main entry point
from dotenv import load_dotenv
load_dotenv()
```

### "Invalid OpenMetadata token"
```bash
# Test connection
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://YOUR_URL/api/v1/system/status
```

### "CLAUDE_API_KEY not set" (or OpenAI)
```bash
# Verify API key format
# Anthropic keys start with: sk-ant-
# OpenAI keys start with: sk-

# Check if key is in .env
grep CLAUDE_API_KEY .env
```

### Port already in use
```bash
# Change APP_PORT in .env to something else (e.g., 8001)
APP_PORT=8001

# Or kill the process on port 8000
lsof -ti:8000 | xargs kill -9  # macOS/Linux
netstat -ano | findstr :8000   # Windows
```

---

## Security Warnings ⚠️

1. **Never commit `.env` to git**
   ```bash
   echo ".env" >> .gitignore
   echo ".env.local" >> .gitignore
   ```

2. **Rotate credentials regularly**
   - Change API keys every 90 days
   - Update webhook secrets after key rotation

3. **Use different keys per environment**
   - Development: lower security
   - Staging: medium security
   - Production: maximum security

4. **Monitor API usage**
   - Set billing alerts
   - Track token consumption
   - Watch for unusual activity

---

## FAQ

**Q: Can I use the same key in development and production?**  
A: No! Always use separate keys. Development keys should have limited access.

**Q: How do I securely pass secrets to Docker?**  
A: Use Docker secrets or AWS Secrets Manager, not .env files in production.

**Q: What if I accidentally commit the .env file?**  
A: 
```bash
git rm --cached .env
git commit -m "Remove .env"
# Then rotate all credentials in that file
```

**Q: Where do I get OpenMetadata token?**  
OpenMetadata UI → Settings (gear icon) → Personal → API Token → Generate

**Q: How do I test if my setup is correct?**  
```bash
python
>>> from dotenv import load_dotenv; import os
>>> load_dotenv()
>>> print(os.getenv('MONGO_URI'))
mongodb://localhost:27017
```

---

## Additional Resources

- [Anthropic API Documentation](https://docs.anthropic.com)
- [OpenAI API Documentation](https://platform.openai.com/docs)
- [MongoDB URI Format](https://docs.mongodb.com/manual/reference/connection-string/)
- [OpenMetadata REST API](https://docs.open-metadata.org/openmetadata/apis/overview)
- [GitHub App Configuration](https://docs.github.com/en/developers/apps/building-github-apps/authenticating-with-github-apps)
- [dbt Cloud Webhooks](https://docs.getdbt.com/docs/deliver/webhooks)
