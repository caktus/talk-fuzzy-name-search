"""Tests for the ON_KIND settings block in fuzzy_demo/settings.py (RECS-2026-08-14 B16-7).

The block's env-var contract after the B16-7 simplification:

  (a) ON_KIND unset        -> the top-level env.db() config stands (DATABASE_URL
                              from the environment/.env, else the dev default);
  (b) ON_KIND + DATABASE_URL -> DATABASE_URL is read as the connection string
                              (psycopg3 format), parsed with env.db_url_config;
  (c) ON_KIND, no DATABASE_URL -> DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT
                              fallback with the same defaults as before.

DEBUG is parsed exactly once (top-level env("DEBUG")); the ON_KIND block must
not re-parse it with different semantics.

Each test reloads the settings module with a controlled environment (the
worktree .env is suppressed so the tests are deterministic) and restores the
original environment + module on teardown.
"""

import importlib
import os
from contextlib import contextmanager
from unittest import mock

import environ
import pytest

from fuzzy_demo import settings as settings_module

# Every env var the settings module reads that these tests control.
_CONTROLLED = (
    "ON_KIND",
    "ON_KIND_SETUP",
    "DATABASE_URL",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "DEBUG",
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "APP_HOSTNAME",
)


@contextmanager
def _no_dotenv():
    """Skip the .env read on reload so the controlled env is the only source."""
    with mock.patch.object(environ.Env, "read_env", lambda *args, **kwargs: None):
        yield


@pytest.fixture
def settings_env():
    """Clear the controlled vars, yield, then restore env and reload the module."""
    saved = {key: os.environ.get(key) for key in _CONTROLLED}
    for key in _CONTROLLED:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(settings_module)


def _db() -> dict:
    return settings_module.DATABASES["default"]


class TestOnKindDatabaseConfig:
    def test_on_kind_unset_uses_database_url(self, settings_env):
        """(a) Without ON_KIND the top-level env.db() DATABASE_URL parse stands."""
        os.environ["DATABASE_URL"] = "psql://testuser@localhost:5432/testdb"
        with _no_dotenv():
            importlib.reload(settings_module)
        db = _db()
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "testdb"
        assert db["USER"] == "testuser"
        assert db["HOST"] == "localhost"
        assert db["PORT"] == 5432
        # B16-6: the ATOMIC_REQUESTS no-op line is gone.
        assert "ATOMIC_REQUESTS" not in db

    def test_on_kind_with_database_url_uses_connection_string(self, settings_env):
        """(b) ON_KIND + DATABASE_URL: the URL itself is the connection config
        (not a boolean flag — the old code ignored its contents)."""
        os.environ["ON_KIND"] = "1"
        os.environ["DATABASE_URL"] = "postgresql://kinduser:kindpass@cnpg-host:5433/kdb"
        with _no_dotenv():
            importlib.reload(settings_module)
        db = _db()
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "kdb"
        assert db["USER"] == "kinduser"
        assert db["PASSWORD"] == "kindpass"
        assert db["HOST"] == "cnpg-host"
        assert db["PORT"] == 5433

    def test_on_kind_without_database_url_uses_db_vars(self, settings_env):
        """(c) ON_KIND without DATABASE_URL: the DB_* vars from the helm
        configmap/secret are used, as the chart has always intended."""
        os.environ["ON_KIND"] = "1"
        os.environ["DB_HOST"] = "cnpg-rw.svc.cluster.local"
        os.environ["DB_NAME"] = "fuzzy_demo"
        os.environ["DB_USER"] = "fuzzy_demo"
        os.environ["DB_PASSWORD"] = "s3cret"
        os.environ["DB_PORT"] = "5432"
        with _no_dotenv():
            importlib.reload(settings_module)
        assert _db() == {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "fuzzy_demo",
            "USER": "fuzzy_demo",
            "PASSWORD": "s3cret",
            "HOST": "cnpg-rw.svc.cluster.local",
            "PORT": "5432",
        }

    def test_on_kind_without_database_url_keeps_legacy_defaults(self, settings_env):
        """(c-defaults) No DATABASE_URL and no DB_* vars: same defaults as before."""
        os.environ["ON_KIND"] = "1"
        with _no_dotenv():
            importlib.reload(settings_module)
        db = _db()
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "fuzzy_demo"
        assert db["USER"] == "fuzzy_demo"
        assert db["PASSWORD"] == ""
        assert db["HOST"] == "localhost"
        assert db["PORT"] == "5432"

    def test_debug_parsed_once(self, settings_env):
        """(c-DEBUG) the helm configmap's lowercase DEBUG='false' parses to False
        via the single top-level parse; the ON_KIND block does not re-parse it."""
        os.environ["ON_KIND"] = "1"
        os.environ["DEBUG"] = "false"
        with _no_dotenv():
            importlib.reload(settings_module)
        assert settings_module.DEBUG is False

    def test_debug_defaults_true_without_env(self, settings_env):
        """No DEBUG set: the top-level default (True) stands, ON_KIND or not."""
        os.environ["ON_KIND"] = "1"
        with _no_dotenv():
            importlib.reload(settings_module)
        assert settings_module.DEBUG is True
