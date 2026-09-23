# 契约先行与边界所有权

多人并行开发的失败模式不是写得慢，而是各自写得都对、合起来不兼容。本文定义哪些东西是共享的、改它们要走什么流程，以及每个人能改哪些路径。

## 1. 为什么契约必须先行

`05-module-contracts.md` 已经把模块操作映射到 HTTP 入口，但那是**建议契约**，不是已实现接口。在任何两个人（或两个 AI 会话）并行实现依赖同一接口的两端之前，该接口必须先落到可机器校验的形式：OpenAPI schema 与共享类型定义。

判断标准很简单：**如果另一个人的代码会因为你改了某个名字或字段而编译失败或行为变化，那它就是公共契约。**

## 2. 公共契约清单

| 契约 | 事实源路径 | 变更方式 |
| --- | --- | --- |
| HTTP API 形状 | `openapi/goalflow.yaml` | 改 FastAPI 路由与 Pydantic 模型 → `bash scripts/api-generate.sh` 导出 |
| 前端 API 类型 | `frontend/src/shared/api/generated/` | 生成物，只能由上一条重新生成 |
| 数据库结构 | `backend/migrations/versions/` | 新增 Alembic 迁移，**禁止修改已合并的迁移** |
| 共享枚举 | `backend/src/goalflow/contracts/enums.py` | 单点定义 |
| 错误码 | `backend/src/goalflow/contracts/errors.py` | 单点定义 |
| 领域术语 | `CONTEXT.md` | 全员评审 |
| 环境变量 | `.env.example` | 新增变量必须同时补默认值说明 |

`backend/src/goalflow/contracts/` 是一个**只被依赖、不依赖业务模块**的包，不允许 import 任何业务模块。

## 3. 契约变更流程

1. **提出**：在 Issue 或 RFC 中写明要改什么、为什么、影响哪些模块与页面。破坏性变更必须显式标注。
2. **批准**：由集成负责人批准。影响产品行为的，还需产品决策人确认。
3. **单独 PR**：契约面的变更**独立成 PR**，不与业务模块混在同一个 PR 里。
4. **广播**：合并后通知团队，受影响的人 rebase 自己的分支并重新生成前端类型。
5. **消费**：其他 PR 基于新契约继续。

分开的目的是**可区分性**：任何人 rebase 后一眼能看出"契约变了"还是"别人的业务逻辑变了"。混在一起时，这个区分就没了。

### 契约面与业务模块

| 面 | 路径 | 含义 |
| --- | --- | --- |
| 契约面 | `openapi/`、`backend/migrations/`、`backend/src/goalflow/contracts/`、`backend/src/goalflow/api/` | 对外可见的接口形状、错误码、枚举与数据库结构 |
| 业务模块 | `backend/src/goalflow/{auth,goals,planning,scheduling,agent,jobs}/`、`frontend/src/features/` | 业务规则与功能实现 |
| 中性 | `backend/src/goalflow/{__init__.py,core,db,tools}/`、`frontend/src/{app,shared}/`、`backend/tests/`、`scripts/`、`docs/` | 两面都不算 |

`frontend/src/shared/api/generated/` 是生成物，随契约一起变更，不单独判定。

`backend/src/goalflow/api/` 属于**契约面**，因为 `openapi/goalflow.yaml` 就是从它导出的生成物。把生成物和它的源分在对立两侧，会让任何改接口的 PR 同时撞上本节与契约漂移检查，两者必有一个失败。这一归属依赖第 8 节的约束——api 层只做权限校验与调用模块 Interface，业务编排在模块内部。

上表三份名单**之外**的 `backend/src/goalflow/` 子包与 `frontend/src/` 目录一律按业务模块处理。新增包时如果它属于契约面或中性，**必须显式补进名单**：默认放行会让门禁在无人察觉的情况下失效。

### `contract-change` 标签

确有必要同时改两面时（典型场景：新建工程骨架、一次性重构模块边界），给 PR 打 `contract-change` 标签即放行，CI 会在 Step Summary 中列出本次的契约面改动清单，使其在评审页面上一眼可见。

标签只影响 **CI 是否拦截**，不影响谁有权批准——[00-workflow.md](00-workflow.md) 第 6 节"公共契约必须由契约负责人以外的至少 1 人批准"照常适用。

判定实现在 `.github/workflows/ci.yml` 的 `pr-hygiene` 作业；这套划分的理由与被否决的替代方案见 [RFC 0002](../rfcs/0002-contract-pr-gate.md)。

## 4. 错误契约（首版固定形状）

所有 API 错误返回同一结构，**不允许各模块自定义错误体**：

```json
{
  "code": "REVISION_CONFLICT",
  "message": "计划版本已过期，请刷新后重试",
  "request_id": "req_0000000000",
  "retryable": false,
  "details": {}
}
```

`code` 取自 `contracts/errors.py` 的单点枚举。`05-module-contracts.md` 已列出的错误码包括 `REVISION_CONFLICT`、`BUDGET_CONFLICT`、`DEPENDENCY_CYCLE`、`CONFIRMATION_REQUIRED`、`INPUT_STALE`、`MODEL_UNAVAILABLE`。新增错误码属于契约变更。

`details` 不得包含其他用户的任何数据，也不得包含模型凭证或其片段。

## 5. 并发与幂等（跨模块统一约定）

这三条是全局约束，任何模块都要遵守，不能各自发明：

- **身份**：请求身份只从服务端会话获得，路径里的对象 ID 一律校验归属。不接受客户端传入的 user_id。
- **幂等**：写操作携带 `Idempotency-Key`。相同 key 不同内容返回冲突；重新评估后的新意图使用新 key。
  **唯一例外是 `/api/auth/*` 账号接口**：注册时用户尚不存在，按 owner 划分的去重记录用不上；重复注册由账号标识的唯一约束兜底，退出与撤销本身幂等（[T03 决策 C12](../worklog/T03-auth-session.md)）。
- **版本**：修改既有状态必须同时提交 `expected_revision`，服务端比对失败返回 `REVISION_CONFLICT`；成功响应必须返回新 revision。

异步操作返回 HTTP 202 与 `job_id`；重复的异步请求返回原作业引用，**不得创建第二个作业**。

## 6. 数据库迁移规程

数据库为 SQLite，行为已由 [T01](../worklog/T01-sqlite-verification.md) 实测确认。**迁移必须启用 Alembic batch 模式**（`render_as_batch=True`，迁移里用 `op.batch_alter_table()`）：SQLite 的 `ALTER TABLE` 只支持有限操作，改列类型或约束都要重建表。

- 每个迁移单一目的，文件名含工作包编号：`0007_T05_add_task_lock.py`。
- **已合并的迁移不可修改**，修正靠新增迁移。
- 迁移必须可回滚，或在文件头注释里写明为何不可回滚以及人工回滚步骤。
- 破坏性变更（删列、改类型、加非空约束）拆成多步：加新列并双写 → 回填 → 切读 → 删旧列。
- 同时有多个分支新增迁移时，由数据负责人统一决定顺序，不要各自猜测 `down_revision`。
- 迁移验收必须在与生产同构的 SQLite 配置上跑（同一组 pragma、WAL、真实库文件），**不能用默认配置或内存库的结果代替**。
- **batch 模式的表重建是丢数据的地方**：它会建新表、拷数据、删旧表、改名。review 必须逐列核对拷贝是否完整，并确认唯一索引跟着重建活了下来——漏一列就静默丢一列的数据，只断言"表还在"看不出来。

## 7. 路径所有权

`.github/CODEOWNERS` 是权威定义，下表是它的说明：

| 路径 | 所有者帽子 | 他人可否直接改 |
| --- | --- | --- |
| `openapi/`、`backend/src/goalflow/contracts/`、`backend/migrations/` | 集成 / 数据 | 否，走契约流程 |
| `scripts/`、`Makefile`、`.github/`、`.pre-commit-config.yaml` | 集成 | 否 |
| `backend/src/goalflow/scheduling/` | 业务规则 | 否 |
| `backend/src/goalflow/agent/` | Agent | 否 |
| `frontend/src/shared/` | 前端 | 否 |
| `frontend/src/features/<feature>/` | 对应功能负责人 | 否 |
| `docs/product/` | 产品决策人 | 否 |
| `docs/development/` | 对应模块负责人 | 否 |
| `docs/worklog/T<NN>-*.md` | 该工作包负责人 | 否 |
| 其余业务模块与测试 | 任务负责人 | 是，需说明 |

**给 AI 的约束**：会话开始时把当前工作包的"可修改路径"明确告诉 AI，提交前用 `git status` 检查是否越界。AI 顺手修改范围外文件是多人协作中最常见的冲突来源，且往往到评审时才被发现。

## 8. 模块边界

跨模块只能通过 `05-module-contracts.md` 定义的 Interface 交互：

- 排期模块暴露 `calculate_agenda(snapshot) → arrangement_or_conflicts`。**纯计算：不写数据库、不调用模型。**
- 计划模块暴露 `apply_change(actor, proposal, expected_versions) → applied_or_conflict`。所有计划变更——HTTP 路由、后台作业、模型工具——都只能走这一条路径。
- Agent 模块暴露 `run_step(step_kind, input_snapshot) → validated_candidate`。返回结构化候选或可解释错误，**不返回任意 SQL，也不自行提交计划**。

**禁止**：绕过 Interface 直接读写其他模块的状态表；在业务模块里 import 另一模块的内部实现文件；为图快在排期计算里插一次数据库写入。这三件事都会让并行开发时的行为无法推理。

## 9. 前后端并行

前端不必等后端实现完成，但必须等**契约**完成：

1. 契约 PR 合并，`openapi/goalflow.yaml` 就位。
2. 前端执行 `bash scripts/api-generate.sh` 生成类型，按类型开发，用 MSW 按 schema 造样例数据。
3. 后端用固定模型输出先验证事务与状态机，再接真实模型。
4. 联调阶段替换 MSW 为真实服务。

样例数据必须**明确标记为样例**，不得在任何交付说明里把基于样例的界面演示称作功能已打通。
