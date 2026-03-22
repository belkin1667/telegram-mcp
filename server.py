"""
HTTP server entry point for Telegram MCP with OAuth protection.

Runs the same Telegram MCP tools over StreamableHTTP transport with OAuth 2.0
authentication, suitable for remote hosting and use with Claude mobile app.

Usage:
    python server.py [--host HOST] [--port PORT] [allowed_root ...]

Required environment variables (in addition to Telegram credentials):
    MCP_SERVER_URL       - Public URL of this server (e.g. https://mcp.example.com)

Optional environment variables:
    MCP_SERVER_HOST      - Bind host (default: 0.0.0.0)
    MCP_SERVER_PORT      - Bind port (default: 8000)
    MCP_OAUTH_DATA_PATH  - Path to OAuth data file (default: oauth_data.json)
"""

import argparse
import asyncio
import os
import sys
import sqlite3

import nest_asyncio
from dotenv import load_dotenv
from pydantic import AnyHttpUrl

load_dotenv()

# Validate server-specific config before heavy imports
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "").rstrip("/")
if not MCP_SERVER_URL:
    print(
        "Error: MCP_SERVER_URL environment variable is required.\n"
        "Set it to the public URL of this server, e.g. https://mcp.example.com",
        file=sys.stderr,
    )
    sys.exit(1)

MCP_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8000"))
MCP_OAUTH_DATA_PATH = os.getenv("MCP_OAUTH_DATA_PATH", "oauth_data.json")

# Import main module — this registers all MCP tools on the `mcp` instance
from main import mcp, _configure_allowed_roots_from_cli, _start_all_clients  # noqa: E402

from mcp.server.auth.settings import (  # noqa: E402
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from oauth_provider import (  # noqa: E402
    TelegramMCPAuthProvider,
    TelegramMCPTokenVerifier,
)


def configure_oauth() -> None:
    """Configure the FastMCP instance with OAuth settings for HTTP hosting."""
    auth_provider = TelegramMCPAuthProvider(storage_path=MCP_OAUTH_DATA_PATH)
    token_verifier = TelegramMCPTokenVerifier(auth_provider)

    # Configure auth settings on the FastMCP instance
    mcp.settings.auth = AuthSettings(
        issuer_url=AnyHttpUrl(MCP_SERVER_URL),
        resource_server_url=AnyHttpUrl(MCP_SERVER_URL),
        service_documentation_url=AnyHttpUrl(
            "https://github.com/chigwell/telegram-mcp"
        ),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["telegram"],
            default_scopes=["telegram"],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["telegram"],
    )

    # Set auth provider and verifier on the FastMCP instance
    mcp._auth_server_provider = auth_provider
    mcp._token_verifier = token_verifier

    # Configure HTTP transport settings
    mcp.settings.host = MCP_SERVER_HOST
    mcp.settings.port = MCP_SERVER_PORT
    mcp.settings.stateless_http = False


async def _main_http() -> None:
    """Start all Telegram clients and run MCP over HTTP with OAuth."""
    try:
        await _start_all_clients()

        print(
            f"All clients started. Running MCP HTTP server on "
            f"{MCP_SERVER_HOST}:{MCP_SERVER_PORT}...",
            file=sys.stderr,
        )
        print(f"OAuth server URL: {MCP_SERVER_URL}", file=sys.stderr)
        print(
            f"MCP endpoint: {MCP_SERVER_URL}/mcp",
            file=sys.stderr,
        )

        await mcp.run_streamable_http_async()
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        if isinstance(e, sqlite3.OperationalError) and "database is locked" in str(e):
            print(
                "Database lock detected. Please ensure no other instances are running.",
                file=sys.stderr,
            )
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Telegram MCP server over HTTP with OAuth"
    )
    parser.add_argument(
        "allowed_roots",
        nargs="*",
        help="Allowed file-system roots for file-path tools",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"Bind host (default: {MCP_SERVER_HOST}, or MCP_SERVER_HOST env)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Bind port (default: {MCP_SERVER_PORT}, or MCP_SERVER_PORT env)",
    )
    args = parser.parse_args()

    # CLI args override env vars
    global MCP_SERVER_HOST, MCP_SERVER_PORT
    if args.host:
        MCP_SERVER_HOST = args.host
    if args.port:
        MCP_SERVER_PORT = args.port

    _configure_allowed_roots_from_cli(args.allowed_roots)
    configure_oauth()

    nest_asyncio.apply()
    asyncio.run(_main_http())


if __name__ == "__main__":
    main()
