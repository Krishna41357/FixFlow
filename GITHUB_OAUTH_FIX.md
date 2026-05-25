# GitHub OAuth Connection Fix

## Issue Identified
Your GitHub OAuth authentication was failing due to URL mismatches between:
1. Frontend API calls
2. Backend environment configuration
3. GitHub App OAuth callback registration

## Root Causes

### 1. ❌ Backend URL Mismatch
**Before:**
- Frontend calls: `https://subcollegiate-reproachfully-lyle.ngrok-free.dev` (ngrok tunnel)
- Backend env: `http://localhost:8000` (localhost)

**After:**
- Both now use: `https://subcollegiate-reproachfully-lyle.ngrok-free.dev`

### 2. ❌ GitHub OAuth Redirect URI Mismatch
**Before:**
- GitHub App callback: `https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/callbackac` ❌ (typo: `callbackac`)
- Backend env: `http://localhost:8000/api/v1/github/oauth/callback` ❌

**After:**
- GitHub App callback: `https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/callback` ✅
- Backend env: Same ✅

### 3. ❌ Frontend Redirect URL Mismatch
**Before:**
- Backend redirects to: `http://localhost:3000/dashboard` ❌
- Frontend route: `/github/setup` (doesn't exist)

**After:**
- Backend redirects to: `http://localhost:3000/github/setup` ✅

## Changes Made

### .env Updates:
```env
# OLD (localhost only)
GITHUB_REDIRECT_URI=http://localhost:8000/api/v1/github/oauth/callback
FRONTEND_SUCCESS_URL=http://localhost:3000/dashboard
FRONTEND_ERROR_URL=http://localhost:3000/error

# NEW (using ngrok tunnel)
GITHUB_REDIRECT_URI=https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/callback
FRONTEND_SUCCESS_URL=http://localhost:3000/github/setup
FRONTEND_ERROR_URL=http://localhost:3000/error
API_BASE_URL=https://subcollegiate-reproachfully-lyle.ngrok-free.dev
```

## OAuth Flow (Corrected)

```
1. Frontend opens popup to:
   https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/start
   └─→ Backend redirects to GitHub authorization

2. User authorizes on GitHub

3. GitHub redirects back to:
   https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/callback ✅
   └─→ Backend exchanges code for token

4. Backend redirects to frontend:
   http://localhost:3000/github/setup ✅
   └─→ Frontend shows success
```

## GitHub App Settings (Manual Fix Required)

🔴 **IMPORTANT:** Update your GitHub App settings:

1. Go to: https://github.com/settings/developers
2. Select your App → Settings
3. Find "Authorization callback URL"
4. Change from: `https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/callbackac`
5. Change to: `https://subcollegiate-reproachfully-lyle.ngrok-free.dev/api/v1/github/oauth/callback`
   - ✅ Remove the typo `ac` at the end

## Testing Steps

1. **Restart Backend** (apply new .env):
   ```bash
   docker-compose restart server
   ```

2. **Test OAuth Flow**:
   - Open frontend: `http://localhost:3000`
   - Navigate to GitHub OAuth setup
   - Click "Start GitHub OAuth"
   - Authorize on GitHub
   - Should redirect successfully to `/github/setup`
   - Should show GitHub installations list

## Architecture (Current Valid Setup)

```
┌─────────────────┐         ┌──────────────────────────────────────┐
│  Frontend       │         │         Backend (Dockerized)         │
│  localhost:3000 │────────→│  ngrok: subcollegiate-...             │
│                 │  API    │  http://localhost:8000 (internal)    │
└─────────────────┘  calls  └──────────────────────────────────────┘
                              │
                              ├─→ GitHub OAuth callback
                              │   via ngrok URL ✅
                              │
                              ├─→ MongoDB (internal docker)
                              │
                              └─→ OpenMetadata (internal docker)
```

## Verification

After changes, check:
- ✅ `.env` has correct `GITHUB_REDIRECT_URI` with ngrok URL
- ✅ `.env` has correct `API_BASE_URL` with ngrok URL
- ✅ `.env` has correct `FRONTEND_SUCCESS_URL` pointing to `/github/setup`
- ✅ GitHub App callback URL updated (manual step)
- ✅ Backend container restarted

Then retry OAuth flow.
