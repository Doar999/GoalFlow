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
scripts/check.sh                只改 mypy 调用参数，见决策 A9
docs/worklog/T02-engineering-foundation.md
```

### 明确不可修改

```text
backend/migrations/           见决策 A5
docs/product/
docs/development/
docs/engineering/
.github/workflows/            见决策 A6，由 RFC 0002 单独处理
scripts/install.sh
scripts/test.sh
scripts/api-generate.sh
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
| A1 | Python 目标 `>=3.11`，CI 与 `.python-version` 固定 3.11；Node 24，pnpm 9 | AGENTS.md 技术基线；Node 版本见 A11 | 全项目 | 否 |
| A2 | LangChain 1.x / LangGraph 1.x 系列，`pyproject.toml` 写主版本约束，精确版本由 `uv.lock` 固定；本次只锁版本不写任何 Agent 代码 | 交付计划第 6 节要求在 T02 收敛 | T08、T14 | 否 |
| A3 | T02 只提供 `GET /api/health` 一个端点，用于让 OpenAPI 导出与漂移检查有真实对象 | 需要至少一个路由才能跑通生成链路 | 公共契约（新增一个端点） | 否 |
| A4 | 幂等与 `expected_revision` 本次只交付**契约形状**：`contracts/` 下的请求模型、错误码与 FastAPI 依赖，以及它们的单元测试。去重存储与版本比对的执行属 T03 / T07 | 无数据库时无法实现执行语义，强行实现会写死错误假设 | 跨模块可见（后续模块直接复用这套形状） | 否 |
| A5 | 不建 `backend/src/goalflow/db/` 与 `backend/migrations/` | T01 未出结论；01-contracts 第 6 节 | 后端数据层 | 否 |
| A6 | 本工作包的实现 PR 需待 [RFC 0002](../rfcs/0002-contract-pr-gate.md) 合并后才能通过 CI；在此之前分支可推送但不合并 | 现有 `pr-hygiene` 与契约漂移检查互相矛盾 | 工程流程 | 已走 RFC 0002 |
| A7 | `openapi/goalflow.yaml` 由 `python -m goalflow.tools.export_openapi` 以 UTF-8 + LF 写入 stdout 的字节流产出，不经文本层换行转换 | Windows 上 Python 文本 stdout 会把 `\n` 转成 `\r\n`，导致 `check.sh` 的 `diff` 永远失败 | 工程流程 | 否 |
| A8 | `ErrorCode` 在 05-module-contracts 的六个错误码之外补 `VALIDATION_FAILED`、`UNAUTHENTICATED`、`FORBIDDEN`、`NOT_FOUND`、`INTERNAL_ERROR`、`IDEMPOTENCY_KEY_CONFLICT` 六项 | 前五项是任何 HTTP 接口都会产生的情形，第六项直接对应 01-contracts 第 5 节"相同 key 不同内容返回冲突" | 公共契约 | 否 |
| A9 | `scripts/check.sh` 的 mypy 调用补 `--config-file backend/pyproject.toml` | mypy 只在 cwd 找配置，而脚本的 cwd 是仓库根，`strict = true` 原本被静默忽略、类型检查空转。已用临时违例实测确认 | 工程流程 | 否 |
| A10 | ruff 关闭 `RUF001`/`RUF002`/`RUF003` | 这三条把中文全角标点判为"疑似打错的西文标点"，而 AGENTS.md 第 139 行要求中文直接用中文字符 | 后端代码风格 | 否 |
| A11 | CI 的 Node 由 20 升到 24，`scripts/install.sh` 与 `CONTRIBUTING.md` 的前置说明同步 | vitest 5 的 engines 为 `^22.12.0 \|\| ^24.0.0 \|\| >=26.0.0`、vite 8 为 `^20.19.0 \|\| >=22.12.0`，Node 20 上前端测试根本起不来；且 Node 20 已于 2026 年 4 月 EOL。24 为当前 Active LTS，与已合并的 PR #1（action 运行时升 Node 24）一致 | 全项目工具链 | 否，产品决策人已确认 |
| A12 | 前端 API 客户端的 `baseUrl` 取 `window.location.origin` 而非 `"/"`；MSW 的 `server.listen()` 放测试 setup 模块顶层而非 `beforeAll` | 前者：fetch 在 jsdom/undici 下要求绝对 URL，相对值抛 `ERR_INVALID_URL`。后者：openapi-fetch 在 `createClient` 时就捕获 `globalThis.fetch` 的引用，`beforeAll` 打补丁太晚，请求会穿透到真实网络 | 前端 shared/api 与测试基建 | 否 |

A3 与 A4 都触及公共契约。按 [00-workflow.md](../engineering/00-workflow.md) 第 3 节，它们影响公共契约本应走 RFC；本次判断为不需要，理由是：A3 新增的是一个无业务语义的健康检查端点，A4 是**把已确认的第 5 节全局约束翻译成代码形状**而非做出新选择。如果评审认为 A4 的具体字段命名构成新决策，应退回走 RFC。

## 5. 验收场景

- [x] `bash scripts/install.sh` 在干净环境可完成后端与前端依赖安装
- [x] `bash scripts/check.sh` 通过，且后端、前端、契约漂移三项均**不再显示跳过**
- [x] `bash scripts/test.sh` 通过，后端与前端均有真实用例被执行
- [x] `bash scripts/test.sh e2e` 通过（`backend/tests/e2e/` 有 5 条真实用例，不是空目录）
- [x] `bash scripts/api-generate.sh` 连续执行两次，产物逐字节一致
- [x] 契约漂移真的会被拦下：改动路由不重新生成时 `check.sh` 退出码 1 并打印 diff
- [x] 请求未知路径返回统一错误结构，含 `code`、`message`、`request_id`、`retryable`、`details`
- [x] 未捕获异常返回统一错误结构，响应体中不出现堆栈与内部消息
- [x] 每个响应带 `request_id`，且与错误体中的值一致、两次请求不重复
- [x] `contracts/` 包不 import 任何业务模块（`test_package_isolation.py` 用 AST 断言）
- [x] 错误码为单点定义，且每个码都有 HTTP 状态映射（`test_error_contract.py`）
- [x] 前端：错误映射把统一错误体转成可展示结果，未知 `code` 有兜底
- [x] 前端：`tsc --noEmit` 在严格模式下通过，无新增 `any` / `!` / `@ts-ignore`
- [x] 锁文件漂移：`uv lock --check` 与 `pnpm install --frozen-lockfile` 均通过

未覆盖的一条，说明如下：校验错误不回显原始输入（`test_error_handling.py` 的
`test_validation_error_reports_fields_without_echoing_input`）虽已通过，但它只证明了
FastAPI 默认错误里的 `input` 字段被剥掉，**没有覆盖嵌套模型与列表下标**的情形。
T03 加入第一个真实写接口时应补一条带嵌套请求体的用例。

## 6. 进展

- 已完成：后端 uv 工程与 `contracts/`、`core/`、`api/`、`tools/`；前端 pnpm 工程与
  `app/`、`shared/api/`、`shared/test/`；`openapi/goalflow.yaml` 导出与前端类型生成；
  `.env.example` 与 `core/config.py` 对齐；CI 的 Node 升到 24。第 5 节验收场景全部勾选。
- 进行中：无。
- 未开始：无。本工作包的交付内容已齐，等待评审。

## 7. 验证结果

以下为真实执行输出，环境 Windows 10 + Git Bash，后端 Python 3.11（uv 管理），
前端 Node 22.13.0 + pnpm 10.6.2。

```text
$ bash scripts/check.sh
==> 后端检查
    $ uv lock --project backend --check        Resolved 93 packages
    $ ruff format --check backend              27 files already formatted
    $ ruff check backend                       All checks passed!
    $ mypy --config-file backend/pyproject.toml backend/src
                                               Success: no issues found in 18 source files
==> 前端检查
    $ pnpm --dir frontend install --frozen-lockfile --ignore-scripts   Done
    $ pnpm --dir frontend run lint             eslint . （无输出即通过）
    $ pnpm --dir frontend exec tsc --noEmit    （无输出即通过）
    $ pnpm --dir frontend exec prettier --check src
                                               All matched files use Prettier code style!
==> 契约漂移检查
    契约一致
==> 凭证粗筛
    未发现疑似凭证
==> 结果
检查通过
```

**没有任何跳过项**——这是本工作包与之前状态的关键差别。

```text
$ bash scripts/test.sh
==> 后端测试（all）      52 passed, 1 warning in 1.62s
==> 前端测试（all）      Test Files 3 passed (3)   Tests 16 passed (16)
==> 结果                 测试通过

$ bash scripts/test.sh e2e
==> 后端测试（e2e）      5 passed, 1 warning in 0.42s
==> 结果                 测试通过
```

契约漂移检查的**反向验证**（把路由的 `summary` 改掉但不重新生成）：

```text
$ bash scripts/check.sh
==> 契约漂移检查
--- openapi/goalflow.yaml
+++ /tmp/tmp.KrDt5XS7qu
@@ -87,6 +87,6 @@
检查未通过        （退出码 1）
```

`bash scripts/api-generate.sh` 连续执行两次，`openapi/goalflow.yaml` 与
`frontend/src/shared/api/generated/schema.d.ts` 的 sha256 均一致。

mypy 配置生效性的**反向验证**（临时放一个未标注函数）：不带 `--config-file` 时
`Success: no issues found`，带上后报 `error: Function is missing a type annotation
[no-untyped-def]`——决策 A9 的依据。

数据库相关验证：**本工作包不涉及数据库**，未建 `db/` 与 `migrations/`（决策 A5）。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| `scripts/test.sh` 的 `e2e` 分支在 `backend/tests/e2e/` 不存在时会因 pytest 退出码失败 | 本次通过补真实 e2e 用例规避，未改脚本 | 集成负责人 |
| `09-frontend-architecture.md` 状态为"推荐"，本次落地已验证其配套选型可用，应改为"已确认" | 下一个 AI 会话读到"推荐"会再犹豫一次 | 前端负责人；`docs/development/` 不在本工作包可改路径内 |
| 数据库驱动与连接串格式仍未定，`.env.example` 的 `GOALFLOW_DATABASE_URL` 保持空占位 | T03 之前必须给出 | 数据负责人（T01） |
| CI 的 `test` 作业尚未接入真实数据库 | 数据库验收目前无处执行 | T01 结论后由集成负责人补 |
| `backend/README.md` 与 `core/config.py` 的注释提到"等 T01 的 seekdb 结论"。若 [RFC 0003](../rfcs/0003-sqlite-as-primary-store.md)（数据库改 SQLite）被接受，这两处连同 `.env.example` 的数据库注释需要回写 | 措辞过期，不影响行为 | 数据负责人；本工作包按"只做 T02"的指示未预先改动 |
| 未引入 React Router、React Hook Form、Zod、shadcn/ui | `09-frontend-architecture.md` 推荐了它们，但本次没有真实使用场景，装了等于锁一个未经验证的版本 | 前端负责人在 T10 首次使用时锁定版本 |

RFC 0002 已随 PR #3 合并，原先记在这里的 `pr-hygiene` 阻塞（决策 A6）已解除。

## 9. 给接手者

- **A7 那条不是小事。** Windows 上 `python -m ... > file` 会产出 CRLF，而仓库 `.gitattributes` 强制 LF，结果是 `check.sh` 的契约漂移 `diff` 在 Windows 开发机上永远失败、在 CI 上却通过。导出必须写 `sys.stdout.buffer`。
- **不要顺手建 `db/` 或写第一个迁移。** 看起来只是"先把引擎配好"，但连接串格式、方言参数、条件更新的影响行数语义全都依赖 T01 的实测结论。先建等于先猜。
- **不要为了让 OpenAPI"看起来完整"去补业务端点。** T03 之后每个工作包自己定义自己的 schema，提前定义的那份一定会被推翻，而它已经进了公共契约。
- **`contracts/` 不许 import 业务模块**，这条有测试守着。加共享模型时注意方向：contracts 被依赖，不依赖别人。
- **前端两处反直觉的写法有测试依据，别"简化"掉**（决策 A12）：`client.ts` 的
  `baseUrl` 必须是绝对 URL，改回 `"/"` 会让所有前端测试挂在 `ERR_INVALID_URL`；
  `shared/test/setup.ts` 的 `server.listen()` 必须在模块顶层，挪进 `beforeAll`
  会让 MSW 补丁晚于 openapi-fetch 抓取 `globalThis.fetch`，请求直接穿透到真实网络。
- **`errors.ts` 的 `FALLBACK_MESSAGE_BY_CODE` 是故意写成 `Record<ApiErrorCode, string>` 的。**
  后端新增错误码、重新生成类型之后，这里漏一个就编译失败。不要为了省事改成
  `Partial<Record<...>>` 或加默认分支——那会让契约变更悄悄溜过前端。
