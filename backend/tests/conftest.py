"""Shared fixtures. Tests are offline: any non-loopback socket connect fails the test."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.core.models import STAGE_ORDER  # noqa: F401  (imported to fail early on a broken core)
from app.core.testing import FIXED_TODAY, make_ctx

__all__ = ["FIXED_TODAY", "make_ctx"]

_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0] if isinstance(address, tuple) else address
            if host not in _LOOPBACK:
                raise AssertionError(f"network access is not allowed in tests: {host!r}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never see the developer's environment or ``.env`` (which holds a real, paid key):
    ``Settings`` ignores the default env file and every GLDB_*/ANTHROPIC_* variable is removed.
    A test that wants an env file passes ``Settings(_env_file=path)`` explicitly."""
    import os

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key in list(os.environ):
        if key.startswith(("GLDB_", "ANTHROPIC_")):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="fake",
    )


@pytest.fixture
def db(settings: Settings) -> Database:
    database = Database(settings.database_path, data_dir=settings.data_dir)
    database.init_schema()
    return database


@pytest.fixture
def mem_db() -> Database:
    database = Database(":memory:")
    database.init_schema()
    return database


@pytest.fixture
def ctx():  # noqa: ANN201
    """An empty loan CaseContext with the Fake LLM, in-memory DB and ``FIXED_TODAY``."""
    return make_ctx()


@pytest.fixture
def fixed_today():  # noqa: ANN201
    return FIXED_TODAY
