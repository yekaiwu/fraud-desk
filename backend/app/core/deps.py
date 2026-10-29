"""FastAPI dependencies: shared application objects live on ``app.state`` (set by ``main.py``)."""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.core.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_runner(request: Request):  # noqa: ANN201 - app.pipeline.JobRunner (avoids an import cycle)
    return request.app.state.runner
