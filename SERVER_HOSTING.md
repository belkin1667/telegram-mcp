# Hosting Telegram MCP as a Remote Server with OAuth

This guide explains how to run Telegram MCP as an HTTP server with OAuth 2.0 protection, enabling use with the Claude mobile app and other remote MCP clients.

## Overview

By default, Telegram MCP runs over **stdio** (standard input/output), which works great for local desktop use. The **server mode** adds:

- **HTTP transport** via StreamableHTTP (`/mcp` endpoint)
- **OAuth 2.0 authentication** with dynamic client registration
- Suitable for hosting on a VPS, cloud server, or container platform

## Quick Start

### 1. Set up environment variables

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Required variables:

```env
# Telegram credentials (same as stdio mode)
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSION_STRING=your_session_string

# Server configuration
MCP_SERVER_URL=https://mcp.example.com   # Your public URL
MCP_SERVER_HOST=0.0.0.0                  # Bind address
MCP_SERVER_PORT=8000                     # Bind port
```

> **Important**: Use `TELEGRAM_SESSION_STRING` (not file-based session) for server deployments. Generate one with `python session_string_generator.py`.

### 2. Run the server

```bash
# Install dependencies
pip install -r requirements.txt

# Run the HTTP server
python server.py
```

The server will start on `http://0.0.0.0:8000` with:
- MCP endpoint: `/mcp`
- OAuth metadata: `/.well-known/oauth-authorization-server`
- Token endpoint: `/token`
- Authorization endpoint: `/authorize`
- Client registration: `/register`

### 3. Connect from Claude mobile app

In the Claude mobile app, add a new MCP server with:
- **URL**: `https://mcp.example.com/mcp`

The app will automatically discover the OAuth endpoints, register as a client, and authenticate.

## Docker Deployment

### Using Docker Compose (recommended)

```bash
# Set MCP_SERVER_URL in your .env file, then:
docker compose -f docker-compose.server.yml up -d
```

### Using Docker directly

```bash
docker build -f Dockerfile.server -t telegram-mcp-server .

docker run -d \
  --name telegram-mcp-server \
  -p 8000:8000 \
  -e TELEGRAM_API_ID=123456 \
  -e TELEGRAM_API_HASH=your_hash \
  -e TELEGRAM_SESSION_STRING=your_session \
  -e MCP_SERVER_URL=https://mcp.example.com \
  -v telegram-mcp-data:/app/data \
  telegram-mcp-server
```

## Production Deployment

### Reverse Proxy (nginx)

For production, put the server behind a reverse proxy with TLS:

```nginx
server {
    listen 443 ssl;
    server_name mcp.example.com;

    ssl_certificate /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for SSE streaming
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
```

### Security Considerations

1. **Always use HTTPS** in production — OAuth tokens are transmitted in headers
2. **Set `MCP_SERVER_URL`** to your HTTPS URL — this is used for OAuth metadata
3. **Protect the OAuth data file** — it contains client secrets and tokens
4. **Use a session string** — file-based sessions don't work well in containers
5. **Firewall**: Only expose the server port through your reverse proxy

## Configuration Reference

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `MCP_SERVER_URL` | *(required)* | Public URL of the server |
| `MCP_SERVER_HOST` | `0.0.0.0` | Bind address |
| `MCP_SERVER_PORT` | `8000` | Bind port |
| `MCP_OAUTH_DATA_PATH` | `oauth_data.json` | Path to OAuth persistence file |

CLI arguments `--host` and `--port` override the environment variables.

## OAuth Flow

The server implements the standard OAuth 2.0 authorization code flow with PKCE:

1. **Discovery**: Client fetches `/.well-known/oauth-authorization-server`
2. **Registration**: Client registers via `POST /register` (dynamic client registration)
3. **Authorization**: Client redirects to `/authorize`, server auto-approves
4. **Token Exchange**: Client exchanges auth code for tokens via `POST /token`
5. **API Access**: Client includes `Bearer <token>` in requests to `/mcp`
6. **Refresh**: Client refreshes expired tokens via `POST /token` with refresh token

Tokens expire after 1 hour and can be refreshed using the refresh token.

## Architecture

```
┌──────────────────┐     HTTPS      ┌──────────────────┐
│  Claude Mobile   │◄──────────────►│   Reverse Proxy  │
│  (MCP Client)    │    (OAuth)     │   (nginx/caddy)  │
└──────────────────┘                └────────┬─────────┘
                                             │ HTTP
                                    ┌────────▼─────────┐
                                    │  server.py        │
                                    │  (FastMCP +       │
                                    │   OAuth + HTTP)   │
                                    └────────┬─────────┘
                                             │
                                    ┌────────▼─────────┐
                                    │  Telegram API     │
                                    │  (via Telethon)   │
                                    └──────────────────┘
```
