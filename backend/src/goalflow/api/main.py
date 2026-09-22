"""ASGI 进程入口：`uvicorn goalflow.api.main:app`。"""

from fastapi import FastAPI

from goalflow.api.app import create_app
from goalflow.core.config import get_settings
from goalflow.core.logging import configure_logging

configure_logging(get_settings().log_level)

app: FastAPI = create_app()
