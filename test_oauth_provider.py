"""Tests for the OAuth provider implementation."""

import json
import os
import tempfile
import time

import pytest
import pytest_asyncio

from mcp.shared.auth import OAuthClientInformationFull
from oauth_provider import TelegramMCPAuthProvider, TelegramMCPTokenVerifier
from mcp.server.auth.provider import AuthorizationParams


@pytest.fixture
def storage_path(tmp_path):
    return str(tmp_path / "oauth_test.json")


@pytest.fixture
def provider(storage_path):
    return TelegramMCPAuthProvider(storage_path=storage_path)


@pytest.fixture
def verifier(provider):
    return TelegramMCPTokenVerifier(provider)


def make_client_info(client_id="test-client"):
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="test-secret",
        redirect_uris=["https://example.com/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
    )


@pytest.mark.asyncio
async def test_register_and_get_client(provider):
    client_info = make_client_info()
    await provider.register_client(client_info)

    loaded = await provider.get_client("test-client")
    assert loaded is not None
    assert loaded.client_id == "test-client"


@pytest.mark.asyncio
async def test_get_unknown_client_returns_none(provider):
    result = await provider.get_client("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_authorization_flow(provider):
    client_info = make_client_info()
    await provider.register_client(client_info)

    params = AuthorizationParams(
        state="test-state",
        scopes=["telegram"],
        code_challenge="test-challenge",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )

    redirect_uri = await provider.authorize(client_info, params)
    assert "code=" in redirect_uri
    assert "state=test-state" in redirect_uri

    # Extract code from redirect URI
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(redirect_uri)
    code = parse_qs(parsed.query)["code"][0]

    # Load the code
    auth_code = await provider.load_authorization_code(client_info, code)
    assert auth_code is not None
    assert auth_code.client_id == "test-client"
    assert auth_code.scopes == ["telegram"]


@pytest.mark.asyncio
async def test_token_exchange(provider, verifier):
    client_info = make_client_info()
    await provider.register_client(client_info)

    params = AuthorizationParams(
        state="s",
        scopes=["telegram"],
        code_challenge="c",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )
    redirect_uri = await provider.authorize(client_info, params)

    from urllib.parse import urlparse, parse_qs

    code = parse_qs(urlparse(redirect_uri).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)

    # Exchange for tokens
    token = await provider.exchange_authorization_code(client_info, auth_code)
    assert token.access_token
    assert token.refresh_token
    assert token.token_type.lower() == "bearer"
    assert token.expires_in == 3600

    # Verify the access token
    access = await verifier.verify_token(token.access_token)
    assert access is not None
    assert access.client_id == "test-client"

    # Auth code should be consumed (single use)
    auth_code2 = await provider.load_authorization_code(client_info, code)
    assert auth_code2 is None


@pytest.mark.asyncio
async def test_refresh_token_flow(provider, verifier):
    client_info = make_client_info()
    await provider.register_client(client_info)

    params = AuthorizationParams(
        state="s",
        scopes=["telegram"],
        code_challenge="c",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )
    redirect_uri = await provider.authorize(client_info, params)
    from urllib.parse import urlparse, parse_qs

    code = parse_qs(urlparse(redirect_uri).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Load refresh token
    rt = await provider.load_refresh_token(client_info, token.refresh_token)
    assert rt is not None

    # Exchange refresh token for new tokens
    new_token = await provider.exchange_refresh_token(client_info, rt, ["telegram"])
    assert new_token.access_token != token.access_token
    assert new_token.refresh_token != token.refresh_token

    # Old refresh token should be consumed
    old_rt = await provider.load_refresh_token(client_info, token.refresh_token)
    assert old_rt is None

    # New access token should work
    access = await verifier.verify_token(new_token.access_token)
    assert access is not None


@pytest.mark.asyncio
async def test_token_expiration(provider, verifier):
    client_info = make_client_info()
    await provider.register_client(client_info)

    params = AuthorizationParams(
        state="s",
        scopes=["telegram"],
        code_challenge="c",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )
    redirect_uri = await provider.authorize(client_info, params)
    from urllib.parse import urlparse, parse_qs

    code = parse_qs(urlparse(redirect_uri).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Manually expire the token
    provider._data["access_tokens"][token.access_token]["created_at"] = (
        time.time() - 7200
    )
    provider._save()

    access = await verifier.verify_token(token.access_token)
    assert access is None


@pytest.mark.asyncio
async def test_revoke_access_token(provider, verifier):
    client_info = make_client_info()
    await provider.register_client(client_info)

    params = AuthorizationParams(
        state="s",
        scopes=["telegram"],
        code_challenge="c",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )
    redirect_uri = await provider.authorize(client_info, params)
    from urllib.parse import urlparse, parse_qs

    code = parse_qs(urlparse(redirect_uri).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    access = await provider.load_access_token(token.access_token)
    assert access is not None

    await provider.revoke_token(access)

    access2 = await verifier.verify_token(token.access_token)
    assert access2 is None


@pytest.mark.asyncio
async def test_persistence(storage_path):
    """Test that data persists across provider instances."""
    provider1 = TelegramMCPAuthProvider(storage_path=storage_path)
    client_info = make_client_info()
    await provider1.register_client(client_info)

    # Create new instance pointing to same file
    provider2 = TelegramMCPAuthProvider(storage_path=storage_path)
    loaded = await provider2.get_client("test-client")
    assert loaded is not None
    assert loaded.client_id == "test-client"


@pytest.mark.asyncio
async def test_wrong_client_cannot_load_code(provider):
    client1 = make_client_info("client-1")
    client2 = make_client_info("client-2")
    await provider.register_client(client1)
    await provider.register_client(client2)

    params = AuthorizationParams(
        state="s",
        scopes=["telegram"],
        code_challenge="c",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )
    redirect_uri = await provider.authorize(client1, params)
    from urllib.parse import urlparse, parse_qs

    code = parse_qs(urlparse(redirect_uri).query)["code"][0]

    # client2 should not be able to load client1's auth code
    auth_code = await provider.load_authorization_code(client2, code)
    assert auth_code is None
