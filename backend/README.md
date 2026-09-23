# GoalFlow 后端

模块化单体：API、Celery Worker 与 Beat 是不同进程，共用本代码库与同一个数据库。

工程约定、命令与红线见仓库根目录的 [AGENTS.md](../AGENTS.md)，目录结构见
[代码与测试规范](../docs/engineering/03-code-and-test-standards.md) 第 1 节。

## 当前进度

本目录由工作包 T02 建立，**尚未实现任何业务功能**。当前只有：

- `src/goalflow/contracts/` 共享错误码、统一错误结构、幂等与 `expected_revision` 的契约形状
- `src/goalflow/core/` 配置、日志、请求上下文
- `src/goalflow/api/` FastAPI 应用、全局异常处理、`GET /api/health`
- `src/goalflow/tools/` OpenAPI 导出

`db/`、`migrations/` 与业务模块尚未创建。数据库行为已由 T01 验证（结论见
[T01 交接卡](../docs/worklog/T01-sqlite-verification.md)，用例在 `tests/db_compat/`）；
生产连接装配属于创建 `src/goalflow/db/` 的工作包，届时把 `tests/db_compat/conftest.py`
里的连接参数整体搬过去。另见 [T02 交接卡](../docs/worklog/T02-engineering-foundation.md) 决策 A5。

## 命令

一律使用仓库根目录的统一脚本，不要在本目录另起等价命令：

```bash
bash scripts/install.sh      # uv sync
bash scripts/check.sh        # ruff、mypy、锁文件与契约漂移
bash scripts/test.sh         # pytest
bash scripts/api-generate.sh # 导出 OpenAPI 并生成前端类型
```

本地起服务：

```bash
uv run --project backend uvicorn goalflow.api.main:app --reload --port 8000
```
