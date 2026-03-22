"""
Multi-account manager for Telegram MCP.

Manages multiple TelegramClient instances and tracks the currently active account.
Backward compatible: if TELEGRAM_ACCOUNTS is not set, falls back to single-account mode
using the legacy environment variables.
"""

import os
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger("telegram_mcp.accounts")

_clients: dict[str, TelegramClient] = {}
_current_account: Optional[str] = None


def register_client(name: str, client: TelegramClient) -> None:
    """Register a named TelegramClient."""
    _clients[name] = client


def get_current_client() -> TelegramClient:
    """Return the currently active TelegramClient."""
    if _current_account is None or _current_account not in _clients:
        raise RuntimeError(
            "No active Telegram account. "
            "Configure TELEGRAM_ACCOUNTS or legacy TELEGRAM_SESSION_STRING."
        )
    return _clients[_current_account]


def set_current_account(name: str) -> None:
    """Set the active account by name."""
    global _current_account
    if name not in _clients:
        raise ValueError(
            f"Unknown account '{name}'. Available: {', '.join(_clients.keys())}"
        )
    _current_account = name


def get_current_account_name() -> Optional[str]:
    """Return the name of the currently active account."""
    return _current_account


def list_accounts() -> list[str]:
    """Return all registered account names."""
    return list(_clients.keys())


def get_client(name: str) -> TelegramClient:
    """Return a specific client by account name."""
    if name not in _clients:
        raise ValueError(
            f"Unknown account '{name}'. Available: {', '.join(_clients.keys())}"
        )
    return _clients[name]


def _make_client(
    api_id: int,
    api_hash: str,
    session_string: Optional[str],
    session_name: Optional[str],
) -> TelegramClient:
    """Create a TelegramClient from credentials."""
    if session_string:
        return TelegramClient(StringSession(session_string), api_id, api_hash)
    elif session_name:
        return TelegramClient(session_name, api_id, api_hash)
    else:
        raise ValueError("Either session_string or session_name must be provided")


def load_accounts_from_env() -> dict[str, TelegramClient]:
    """
    Load Telegram accounts from environment variables.

    Multi-account mode (shared API credentials):
        TELEGRAM_API_ID=12345
        TELEGRAM_API_HASH=abc123
        TELEGRAM_ACCOUNTS=personal,work
        TELEGRAM_PERSONAL_SESSION_STRING=...
        TELEGRAM_WORK_SESSION_STRING=...

    Legacy single-account mode (no TELEGRAM_ACCOUNTS):
        TELEGRAM_API_ID=12345
        TELEGRAM_API_HASH=abc123
        TELEGRAM_SESSION_STRING=...
    """
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]

    accounts_str = os.getenv("TELEGRAM_ACCOUNTS")

    if not accounts_str:
        # Legacy single-account mode
        session_string = os.getenv("TELEGRAM_SESSION_STRING")
        session_name = os.getenv("TELEGRAM_SESSION_NAME")
        client = _make_client(api_id, api_hash, session_string, session_name)
        return {"default": client}

    accounts = {}
    for name in accounts_str.split(","):
        name = name.strip()
        if not name:
            continue
        prefix = f"TELEGRAM_{name.upper()}_"
        session_string = os.getenv(f"{prefix}SESSION_STRING")
        session_name = os.getenv(f"{prefix}SESSION_NAME")
        if not session_string and not session_name:
            raise ValueError(
                f"Account '{name}' requires {prefix}SESSION_STRING "
                f"or {prefix}SESSION_NAME"
            )
        accounts[name] = _make_client(api_id, api_hash, session_string, session_name)

    if not accounts:
        raise ValueError("TELEGRAM_ACCOUNTS is set but contains no valid account names")

    return accounts


def reset() -> None:
    """Reset all state. Used for testing."""
    global _current_account
    _clients.clear()
    _current_account = None
