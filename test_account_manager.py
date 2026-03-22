"""Tests for the multi-account manager."""

import os
from unittest.mock import MagicMock, patch

import pytest

import account_manager


@pytest.fixture(autouse=True)
def reset_state():
    """Reset account manager state before each test."""
    account_manager.reset()
    yield
    account_manager.reset()


def make_mock_client(name="mock"):
    """Create a mock TelegramClient."""
    client = MagicMock()
    client._name = name
    return client


class TestRegisterAndGet:
    def test_register_and_get_client(self):
        c = make_mock_client()
        account_manager.register_client("test", c)
        account_manager.set_current_account("test")
        assert account_manager.get_current_client() is c

    def test_get_client_by_name(self):
        c1 = make_mock_client("c1")
        c2 = make_mock_client("c2")
        account_manager.register_client("one", c1)
        account_manager.register_client("two", c2)
        assert account_manager.get_client("one") is c1
        assert account_manager.get_client("two") is c2

    def test_get_unknown_client_raises(self):
        with pytest.raises(ValueError, match="Unknown account 'nope'"):
            account_manager.get_client("nope")

    def test_list_accounts(self):
        account_manager.register_client("a", make_mock_client())
        account_manager.register_client("b", make_mock_client())
        assert account_manager.list_accounts() == ["a", "b"]

    def test_list_accounts_empty(self):
        assert account_manager.list_accounts() == []


class TestCurrentAccount:
    def test_set_and_get_current(self):
        account_manager.register_client("x", make_mock_client())
        account_manager.set_current_account("x")
        assert account_manager.get_current_account_name() == "x"

    def test_switch_account(self):
        account_manager.register_client("a", make_mock_client())
        account_manager.register_client("b", make_mock_client())
        account_manager.set_current_account("a")
        assert account_manager.get_current_account_name() == "a"
        account_manager.set_current_account("b")
        assert account_manager.get_current_account_name() == "b"

    def test_set_unknown_account_raises(self):
        account_manager.register_client("x", make_mock_client())
        with pytest.raises(ValueError, match="Unknown account 'y'"):
            account_manager.set_current_account("y")

    def test_get_current_client_no_account_set(self):
        with pytest.raises(RuntimeError, match="No active Telegram account"):
            account_manager.get_current_client()

    def test_get_current_account_name_initially_none(self):
        assert account_manager.get_current_account_name() is None


class TestClientProxy:
    """Test the _ClientProxy pattern directly (without importing main.py)."""

    def _make_proxy_class(self):
        """Recreate the proxy class to avoid importing main.py and its heavy deps."""

        class _ClientProxy:
            def __getattr__(self, name):
                return getattr(account_manager.get_current_client(), name)

            def __call__(self, *args, **kwargs):
                return account_manager.get_current_client()(*args, **kwargs)

        return _ClientProxy

    def test_proxy_delegates_getattr(self):
        mock_client = make_mock_client()
        mock_client.some_method = MagicMock(return_value="result")
        account_manager.register_client("test", mock_client)
        account_manager.set_current_account("test")

        proxy = self._make_proxy_class()()
        assert proxy.some_method() == "result"
        mock_client.some_method.assert_called_once()

    def test_proxy_delegates_call(self):
        mock_client = make_mock_client()
        mock_client.return_value = "called"
        account_manager.register_client("test", mock_client)
        account_manager.set_current_account("test")

        proxy = self._make_proxy_class()()
        result = proxy("arg1", key="val")
        mock_client.assert_called_once_with("arg1", key="val")

    def test_proxy_follows_account_switch(self):
        c1 = make_mock_client("c1")
        c2 = make_mock_client("c2")
        c1.get_me = MagicMock(return_value="user1")
        c2.get_me = MagicMock(return_value="user2")
        account_manager.register_client("a", c1)
        account_manager.register_client("b", c2)

        proxy = self._make_proxy_class()()

        account_manager.set_current_account("a")
        assert proxy.get_me() == "user1"

        account_manager.set_current_account("b")
        assert proxy.get_me() == "user2"


class TestLoadAccountsFromEnv:
    @patch("account_manager._make_client")
    def test_legacy_single_account_session_string(self, mock_make):
        mock_make.return_value = make_mock_client()
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abc123",
            "TELEGRAM_SESSION_STRING": "test_session_string",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TELEGRAM_ACCOUNTS", None)
            os.environ.pop("TELEGRAM_SESSION_NAME", None)
            accounts = account_manager.load_accounts_from_env()

        assert list(accounts.keys()) == ["default"]
        mock_make.assert_called_once_with(
            12345, "abc123", "test_session_string", None
        )

    @patch("account_manager._make_client")
    def test_legacy_single_account_session_name(self, mock_make):
        mock_make.return_value = make_mock_client()
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abc123",
            "TELEGRAM_SESSION_NAME": "my_session",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TELEGRAM_ACCOUNTS", None)
            os.environ.pop("TELEGRAM_SESSION_STRING", None)
            accounts = account_manager.load_accounts_from_env()

        assert list(accounts.keys()) == ["default"]
        mock_make.assert_called_once_with(12345, "abc123", None, "my_session")

    @patch("account_manager._make_client")
    def test_multi_account(self, mock_make):
        mock_make.side_effect = [make_mock_client("c1"), make_mock_client("c2")]
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abc123",
            "TELEGRAM_ACCOUNTS": "personal,work",
            "TELEGRAM_PERSONAL_SESSION_STRING": "session1",
            "TELEGRAM_WORK_SESSION_STRING": "session2",
        }
        with patch.dict(os.environ, env, clear=False):
            accounts = account_manager.load_accounts_from_env()

        assert list(accounts.keys()) == ["personal", "work"]
        assert mock_make.call_count == 2

    def test_multi_account_missing_session_raises(self):
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abc123",
            "TELEGRAM_ACCOUNTS": "personal,work",
            # Neither TELEGRAM_WORK_SESSION_STRING nor TELEGRAM_WORK_SESSION_NAME set
            # personal also has no session — but work is checked first alphabetically
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TELEGRAM_PERSONAL_SESSION_STRING", None)
            os.environ.pop("TELEGRAM_PERSONAL_SESSION_NAME", None)
            os.environ.pop("TELEGRAM_WORK_SESSION_STRING", None)
            os.environ.pop("TELEGRAM_WORK_SESSION_NAME", None)
            with pytest.raises(ValueError, match="Account 'personal' requires"):
                account_manager.load_accounts_from_env()

    def test_empty_accounts_string_raises(self):
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abc123",
            "TELEGRAM_ACCOUNTS": ",,,",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="no valid account names"):
                account_manager.load_accounts_from_env()

    @patch("account_manager._make_client")
    def test_first_account_is_default(self, mock_make):
        """The first account in TELEGRAM_ACCOUNTS should be the default."""
        mock_make.side_effect = [make_mock_client("c1"), make_mock_client("c2")]
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abc123",
            "TELEGRAM_ACCOUNTS": "work,personal",
            "TELEGRAM_WORK_SESSION_STRING": "s1",
            "TELEGRAM_PERSONAL_SESSION_STRING": "s2",
        }
        with patch.dict(os.environ, env, clear=False):
            accounts = account_manager.load_accounts_from_env()

        # First key should be "work"
        assert list(accounts.keys())[0] == "work"


class TestReset:
    def test_reset_clears_state(self):
        account_manager.register_client("x", make_mock_client())
        account_manager.set_current_account("x")
        account_manager.reset()
        assert account_manager.list_accounts() == []
        assert account_manager.get_current_account_name() is None
