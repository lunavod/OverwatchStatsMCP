# Google OAuth Setup

This guide walks through configuring Google OAuth so users can authenticate with the MCP server.

## 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Note your project ID

## 2. Configure the OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External** user type (unless you have a Google Workspace org and want internal-only)
3. Fill in the required fields:
   - **App name**: OverwatchStats (or whatever you prefer)
   - **User support email**: your email
   - **Developer contact email**: your email
4. Add any test users if the app is in "Testing" status — only test users can log in until you publish the app
5. Save

## 3. Create OAuth 2.0 Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Application type: **Web application**
4. Name: `OverwatchStats MCP`
5. Add **Authorized redirect URIs**:
   ```
   https://your-domain.com/auth/google/callback
   ```
   For local development:
   ```
   http://localhost:8000/auth/google/callback
   ```
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

## 4. Configure Environment Variables

Set these on the server (via `.env` file, Docker env, or your hosting platform):

```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
EXTERNAL_URL=https://your-domain.com
```

`EXTERNAL_URL` must match the origin of the redirect URI you registered in step 4. No trailing slash.

For local development:
```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
EXTERNAL_URL=http://localhost:8000
```

## 5. Run the Migration

If you haven't already:

```bash
alembic upgrade head
```

This creates the `users` table and adds `user_id` columns to `matches` and `player_notes`.

## 6. Start the Server

```bash
python src/main.py
```

When `GOOGLE_CLIENT_ID` is set, the server enables OAuth automatically. When it's not set, auth is disabled (useful for local development/testing).

## 7. First User = Admin

The first user to authenticate via Google is automatically promoted to admin. They can access the admin panel at `/admin/login` and manage other users from there.

## Connecting from Claude

### Claude Desktop / claude.ai

1. Go to **Settings > Connectors > Add custom connector**
2. Enter your MCP server URL (e.g. `https://your-domain.com/mcp`)
3. Claude will discover the OAuth configuration and redirect you to Google to sign in
4. After approval, tools become available

### Claude Code

1. Run `/mcp` and select **Remote (HTTP/SSE)**
2. Enter the server URL
3. Your browser opens to Google's sign-in page
4. After approval, tokens are stored in your system keychain

### Claude API

The API's MCP connector does not perform OAuth itself. Obtain a token separately and pass it as `authorization_token` in your API request.

## Troubleshooting

**"redirect_uri_mismatch" error from Google**
The redirect URI in your Google Cloud Console must exactly match `{EXTERNAL_URL}/auth/google/callback`. Check for http vs https, trailing slashes, and port numbers.

**Auth works but tools return "No authenticated user"**
The `GOOGLE_CLIENT_ID` env var might not be set, so the server started without auth enabled. Restart with the variable set.

**"Account disabled" after login**
An admin has disabled your account. Contact them or check the admin panel at `/admin/`.

**Token expired**
Claude clients handle refresh automatically. If using the API, obtain a new token.
