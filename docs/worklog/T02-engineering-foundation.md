# T02 工程与契约基础

| 项 | 值 |
| --- | --- |
| 工作包 | T02（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | Doar（集成与交付） |
| 状态 | 进行中 |
| 更新日期 | 2026-09-22 |
| 相关 PR | #（待填）；前置 [RFC 0002](../rfcs/0002-contract-pr-gate.md) #3 |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容。

## 1. 目标

建出 `backend/` 与 `frontend/` 两个可构建、可检查、可测试的工程骨架，并让 `openapi/goalflow.yaml` 真正由 FastAPI 导出、前端类型真正由它生成、漂移真正能被 `scripts/check.sh` 拦下。同时把统一错误结构、幂等与 `expected_revision` 三条全局约束落成 `contracts/` 下的单点定义。

达成标准：`bash scripts/check.sh` 与 `bash scripts/test.sh` 全绿且非全跳过；`bash scripts/api-generate.sh` 可重复执行且产物稳定；故意改动路由而不重新生成时 `check.sh` 必须失败。M0 门以本工作包与 T01 共同为前提。

**本工作包不实现任何业务功能，也不建数据库。** 它只回答"这套工程约定能不能真的跑起来"。

## 2. 范围

### 可修改路径

```text
backend/
frontend/
openapi/
.env.example
docs/worklog/T02-engineering-foundation.md
```

### 明确不可修改

```text
backend/migrations/           见决策 A5
docs/product/
docs/development/
docs/engineering/
.github/workflows/            见决策 A6，由 RFC 0002 单独处理
scripts/                      见未决问题
```

### 不在本次范围内

- **数据库**：引擎、会话、基类、迁移一律不建。T01 未出结论前建它们等于把假设写进代码，见 [契约与边界所有权](../engineering/01-contracts-and-ownership.md) 第 6 节。
- **业务端点**：注册登录、目标、计划、排期的接口属于 T03 及之后，本次不抢先定义 schema。
- **Celery / Redis 接入**：属于 T07。依赖会写进 `pyproject.toml` 以固定版本，但不写启动代码。
- **LangGraph 图与模型接入**：属于 T08 / T14。同上，只锁版本不写代码。
- **shadcn/ui 组件落地**：本次只建 `shared/ui/` 目录与设计令牌位置，不批量引入组件源码。
- **幂等与版本冲突的存储与执行**：本次只交付契约形状（模型、依赖、错误码），真正的去重表与版本比对属于 T03 / T07。见决策 A4。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [06-delivery-plan.md](../development/06-delivery-plan.md) 第 2 节 T02 行 | 目录、锁文件、构建检查、错误结构、请求去重与 revision 契约、OpenAPI 生成前端类型并检查漂移 | 已确认 |
| [06-delivery-plan.md](../development/06-delivery-plan.md) 第 6 节 | LangChain / LangGraph 的锁定版本属于 T02 前置 | 已确认（属 T02 范围） |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 4 节 | 统一错误结构 `code`/`message`/`request_id`/`retryable`/`details` | 已确认 |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 5 节 | 身份、幂等、`expected_revision` 三条全局约束 | 已确认 |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 6 节 | T01 出结论前不写依赖特定数据库特性的迁移 | 已确认 |
| [03-code-and-test-standards.md](../engineering/03-code-and-test-standards.md) 第 1 节 | 前后端目录结构 | 已确认 |
| [03-code-and-test-standards.md](../engineering/03-code-and-test-standards.md) 第 2、3 节 | Python 与 TypeScript 规范 | 已确认 |
| [05-module-contracts.md](../development/05-module-contracts.md) 共同规则 | 错误码清单 `REVISION_CONFLICT`、`BUDGET_CONFLICT`、`DEPENDENCY_CYCLE`、`CONFIRMATION_REQUIRED`、`INPUT_STALE`、`MODEL_UNAVAILABLE` | 建议契约 → 第 4 节已把错误结构定为已确认，错误码沿用 |
| [09-frontend-architecture.md](../development/09-frontend-architecture.md) | 配套选型与目录约定 | 推荐（技术栈本身已确认） |
| [AGENTS.md](../../AGENTS.md) 技术基线 | 包管理后端 `uv`、前端 `pnpm`，锁文件必须提交 | 已确认 |

`09-frontend-architecture.md` 整体标为"推荐实施方案，配套库尚待代码初始化验证"。本工作包**就是**那次验证，因此按其推荐直接落地，落地结果回写该文档的状态由后续 PR 处理（见未决问题）。

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要升级为 RFC |
| --- | --- | --- | --- | --- |
| A1 | Python 目标 `>=3.11`，CI 与 `.python-version` 固定 3.11；Node 20，pnpm 9 | AGENTS.md 技术基线与现有 CI 矩阵 | 全项目 | 否 |
| A2 | LangChain 1.x / LangGraph 1.x 系列，`pyproject.toml` 写主版本约束，精确版本由 `uv.lock` 固定；本次只锁版本不写任何 Agent 代码 | 交付计划第 6 节要求在 T02 收敛 | T08、T14 | 否 |
| A3 | T02 只提供 `GET /api/health` 一个端点，用于让 OpenAPI 导出与漂移检查有真实对象 | 需要至少一个路由才能跑通生成链路 | 公共契约（新增一个端点） | 否 |
| A4 | 幂等与 `expected_revision` 本次只交付**契约形状**：`contracts/` 下的请求模型、错误码与 FastAPI 依赖，以及它们的单元测试。去重存储与版本比对的执行属 T03 / T07 | 无数据库时无法实现执行语义，强行实现会写死错误假设 | 跨模块可见（后续模块直接复用这套形状） | 否 |
| A5 | 不建 `backend/src/goalflow/db/` 与 `backend/migrations/` | T01 未出结论；01-contracts 第 6 节 | 后端数据层 | 否 |
| A6 | 本工作包的实现 PR 需待 [RFC 0002](../rfcs/0002-contract-pr-gate.md) 合并后才能通过 CI；在此之前分支可推送但不合并 | 现有 `pr-hygiene` 与契约漂移检查互相矛盾 | 工程流程 | 已走 RFC 0002 |
| A7 | `openapi/goalflow.yaml` 由 `python -m goalflow.tools.export_openapi` 以 UTF-8 + LF 写入 stdout 的字节流产出，不经文本层换行转换 | Windows 上 Python 文本 stdout 会把 `\n` 转成 `\r\n`，导致 `check.sh` 的 `diff` 永远失败 | 工程流程 | 否 |

A3 与 A4 都触及公共契约。按 [00-workflow.md](../engineering/00-workflow.md) 第 3 节，它们影响公共契约本应走 RFC；本次判断为不需要，理由是：A3 新增的是一个无业务语义的健康检查端点，A4 是**把已确认的第 5 节全局约束翻译成代码形状**而非做出新选择。如果评审认为 A4 的具体字段命名构成新决策，应退回走 RFC。

## 5. 验收场景

- [ ] `bash scripts/install.sh` 在干净环境可完成后端与前端依赖安装
- [ ] `bash scripts/check.sh` 通过，且后端、前端、契约漂移三项均**不再显示跳过**
- [ ] `bash scripts/test.sh` 通过，后端与前端均有真实用例被执行
- [ ] `bash scripts/test.sh e2e` 通过（`backend/tests/e2e/` 有真实用例，不是空目录）
- [ ] `bash scripts/api-generate.sh` 连续执行两次，产物逐字节一致
- [ ] 契约漂移真的会被拦下：改动路由或 Pydantic 模型后不重新生成，`check.sh` 失败
- [ ] 请求未知路径返回统一错误结构，含 `code`、`message`、`request_id`、`retryable`、`details`
- [ ] 未捕获异常返回统一错误结构，响应体与日志中不出现堆栈
- [ ] 每个响应带 `request_id`，且与错误体中的值一致
- [ ] `contracts/` 包不 import 任何业务模块（有测试断言）
- [ ] 错误码为单点定义，重复定义或拼写漂移会被测试发现
- [ ] 前端：错误映射把统一错误体转成可展示结果，未知 `code` 有兜底
- [ ] 前端：`tsc --noEmit` 在严格模式下通过，无新增 `any` / `!` / `@ts-ignore`
- [ ] 锁文件漂移：`uv lock --check` 与 `pnpm install --frozen-lockfile` 均通过

## 6. 进展

- 已完成：分支 `feat/T02-engineering-foundation` 建立；本交接卡建立；依赖版本已从 PyPI 查得（见第 7 节）。
- 进行中：后端工程骨架。
- 未开始：前端工程骨架、OpenAPI 导出与类型生成、`.env.example` 同步。

## 7. 验证结果

依赖版本查询（2026-09-22，PyPI 最新稳定版，用于确定 `pyproject.toml` 的约束下界）：

```text
fastapi 0.141.1     uvicorn 0.53.0        pydantic 2.13.5      pydantic-settings 2.15.0
sqlalchemy 2.0.54   alembic 1.20.0        celery 5.6.3         redis 8.1.0
pymysql 1.2.3       langchain 1.4.2       langchain-core 1.6.4 langchain-openai 1.6.3
langchain-anthropic 1.7.2                 langgraph 1.2.12
ruff 0.16.8         mypy 2.3.1            pytest 9.1.1         pytest-asyncio 1.4.0
httpx 0.28.1        pyyaml 6.0.3
```

`scripts/check.sh` 与 `scripts/test.sh` 的完整输出待工程骨架就绪后补。**在此之前不要把本节当作已验证。**

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| RFC 0002 未合并前，本工作包的 PR 无法通过 `pr-hygiene` | 阻塞 T02 合并，不阻塞实现 | 集成负责人（RFC 0002 #3） |
| `scripts/test.sh` 的 `e2e` 分支在 `backend/tests/e2e/` 不存在时会因 pytest 退出码失败 | 本次通过补真实 e2e 用例规避，未改脚本 | 集成负责人 |
| `09-frontend-architecture.md` 状态为"推荐"，本次落地后应改为"已确认" | 下一个 AI 会话读到"推荐"会再犹豫一次 | 前端负责人；`docs/development/` 不在本工作包可改路径内 |
| seekdb 驱动与连接串格式仍未定，`.env.example` 的 `GOALFLOW_DATABASE_URL` 保持空占位 | T03 之前必须由 T01 给出 | 数据负责人（T01） |
| CI 的 `test` 作业尚未接入真实 seekdb | 数据库验收目前无处执行 | T01 结论后由集成负责人补 |

## 9. 给接手者

- **A7 那条不是小事。** Windows 上 `python -m ... > file` 会产出 CRLF，而仓库 `.gitattributes` 强制 LF，结果是 `check.sh` 的契约漂移 `diff` 在 Windows 开发机上永远失败、在 CI 上却通过。导出必须写 `sys.stdout.buffer`。
- **不要顺手建 `db/` 或写第一个迁移。** 看起来只是"先把引擎配好"，但连接串格式、方言参数、`CLIENT_FOUND_ROWS` 的取值全都依赖 T01 的实测结论（尤其是 D4）。先建等于先猜。
- **不要为了让 OpenAPI"看起来完整"去补业务端点。** T03 之后每个工作包自己定义自己的 schema，提前定义的那份一定会被推翻，而它已经进了公共契约。
- **`contracts/` 不许 import 业务模块**，这条有测试守着。加共享模型时注意方向：contracts 被依赖，不依赖别人。
