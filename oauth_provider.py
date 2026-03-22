"""
OAuth 2.0 Authorization Server Provider for Telegram MCP.

Implements the MCP SDK's OAuthAuthorizationServerProvider protocol to enable
OAuth-protected remote MCP server hosting, suitable for Claude mobile app.
"""

import json
import secrets
import time
import logging
from pathlib import Path
from typing import Optional

from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    AccessToken,
    TokenVerifier,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


logger = logging.getLogger("telegram_mcp.oauth")


class TelegramMCPAuthProvider(OAuthAuthorizationServerProvider):
    """
    Simple OAuth provider backed by a JSON file for persistence.

    Supports dynamic client registration, authorization code flow with PKCE,
    and token refresh — the minimum needed for Claude mobile app integration.
    """

    def __init__(self, storage_path: str = "oauth_data.json"):
        self.storage_path = Path(storage_path)
        self._data = self._load()

    def _load(self) -> dict:
        if self.storage_path.exists():
            try:
                return json.loads(self.storage_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "clients": {},
            "auth_codes": {},
            "access_tokens": {},
            "refresh_tokens": {},
        }

    def _save(self) -> None:
        self.storage_path.write_text(json.dumps(self._data, indent=2))

    # --- Client Registration ---

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        client_data = self._data["clients"].get(client_id)
        if client_data is None:
            return None
        return OAuthClientInformationFull(**client_data)

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        self._data["clients"][client_info.client_id] = json.loads(
            client_info.model_dump_json()
        )
        self._save()
        logger.info(f"Registered OAuth client: {client_info.client_id}")

    # --- Authorization ---

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """
        Generate an authorization code and return a redirect URI.

        For a headless server, we auto-approve since the server operator
        controls access via the server itself. The OAuth layer protects
        the transport; Telegram auth is separate.
        """
        code = secrets.token_urlsafe(32)

        self._data["auth_codes"][code] = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "scopes": params.scopes or [],
            "created_at": time.time(),
            "redirect_uri_provided_explicitly": (
                params.redirect_uri_provided_explicitly
            ),
        }
        self._save()

        logger.info(f"Authorization code issued for client {client.client_id}")

        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    # --- Token Exchange ---

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> Optional[AuthorizationCode]:
        code_data = self._data["auth_codes"].get(authorization_code)
        if code_data is None:
            return None
        if code_data["client_id"] != client.client_id:
            return None
        # Expire after 10 minutes
        if time.time() - code_data["created_at"] > 600:
            del self._data["auth_codes"][authorization_code]
            self._save()
            return None

        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            redirect_uri=code_data["redirect_uri"],
            redirect_uri_provided_explicitly=code_data[
                "redirect_uri_provided_explicitly"
            ],
            code_challenge=code_data["code_challenge"],
            scopes=code_data["scopes"],
            expires_at=code_data["created_at"] + 600,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Remove used code
        self._data["auth_codes"].pop(authorization_code.code, None)

        access_token = secrets.token_urlsafe(48)
        refresh_token = secrets.token_urlsafe(48)
        expires_in = 3600  # 1 hour

        self._data["access_tokens"][access_token] = {
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "created_at": time.time(),
            "expires_in": expires_in,
        }
        self._data["refresh_tokens"][refresh_token] = {
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "created_at": time.time(),
        }
        self._save()

        logger.info(f"Tokens issued for client {client.client_id}")

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes)
            if authorization_code.scopes
            else None,
        )

    # --- Refresh Tokens ---

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> Optional[RefreshToken]:
        token_data = self._data["refresh_tokens"].get(refresh_token)
        if token_data is None:
            return None
        if token_data["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=token_data["scopes"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Revoke old refresh token
        self._data["refresh_tokens"].pop(refresh_token.token, None)

        new_access_token = secrets.token_urlsafe(48)
        new_refresh_token = secrets.token_urlsafe(48)
        expires_in = 3600

        effective_scopes = scopes if scopes else refresh_token.scopes

        self._data["access_tokens"][new_access_token] = {
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "created_at": time.time(),
            "expires_in": expires_in,
        }
        self._data["refresh_tokens"][new_refresh_token] = {
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "created_at": time.time(),
        }
        self._save()

        return OAuthToken(
            access_token=new_access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=new_refresh_token,
            scope=" ".join(effective_scopes) if effective_scopes else None,
        )

    # --- Access Token Verification ---

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        token_data = self._data["access_tokens"].get(token)
        if token_data is None:
            return None
        # Check expiration
        if time.time() - token_data["created_at"] > token_data["expires_in"]:
            del self._data["access_tokens"][token]
            self._save()
            return None
        return AccessToken(
            token=token,
            client_id=token_data["client_id"],
            scopes=token_data["scopes"],
        )

    # --- Revocation ---

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        if isinstance(token, AccessToken):
            self._data["access_tokens"].pop(token.token, None)
        elif isinstance(token, RefreshToken):
            self._data["refresh_tokens"].pop(token.token, None)
        self._save()
        logger.info(f"Token revoked for client {token.client_id}")


class TelegramMCPTokenVerifier(TokenVerifier):
    """Verifies bearer tokens against the auth provider's stored tokens."""

    def __init__(self, provider: TelegramMCPAuthProvider):
        self._provider = provider

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        return await self._provider.load_access_token(token)
