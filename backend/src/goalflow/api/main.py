"""ASGI 进程入口：`uvicorn goalflow.api.main:app`。"""

from fastapi import FastAPI

from goalflow.api.app import create_app
from goalflow.api.dependencies import get_auth_service
from goalflow.core.config import get_settings
from goalflow.core.logging import configure_logging

configure_logging(get_settings().log_level)

# 启动即装配：缺连接串、缺会话密钥、SQLite 版本过低都在这里失败，而不是等到第一个请求。
get_auth_service()

app: FastAPI = create_app()
