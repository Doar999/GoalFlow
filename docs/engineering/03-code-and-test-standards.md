# 代码与测试规范

## 1. 目录结构

### 后端

```text
backend/
  pyproject.toml
  src/goalflow/
    contracts/        共享枚举、错误码、通用响应模型（不依赖业务模块）
    core/             配置、日志、请求上下文、依赖注入
    db/               引擎、会话、基类
    auth/             注册、登录、会话
    goals/            目标、档案、路线
    planning/         计划、阶段、里程碑、任务批次、apply_change
    scheduling/       时间预算与排期计算（纯函数）
    agent/            Agent 步骤、领域策略、模型接入
      graphs/         LangGraph StateGraph 定义（短生命周期，无 checkpointer）
      models/         LangChain chat model 构造与统一调用 Interface
    jobs/             outbox、Celery 任务、作业事件
    api/              FastAPI 路由与依赖
  migrations/
  tests/
    <module>/         模块行为测试
    e2e/              跨组件验收场景
```

### 前端

沿用 [09-frontend-architecture.md](../development/09-frontend-architecture.md) 已确认的约定：

```text
frontend/src/
  app/                启动、路由、providers、全局错误页
  features/
    auth/ today/ goals/ planning/ review/ model-settings/
  shared/
    api/              生成类型、fetch client、错误映射
    ui/               shadcn/ui 与项目公共组件
    lib/              日期、格式化等无业务归属工具
    test/
```

业务规则跟随 feature。**不要**创建按技术类型划分的全局 `components/` `hooks/` `services/`。feature 之间不直接操作对方的缓存，共享能力通过公开 hook 或 `shared/api` 完成。

## 2. Python 规范

- 目标 Python 3.11+，Ruff 负责格式化与 lint，行宽 120。
- 公共函数、Pydantic 模型、Interface 必须有类型注解；类型检查在 CI 中阻塞。
- 命名用 `CONTEXT.md` 的英文术语：`goal_profile`、`task_batch`、`checkin`、`verification_result`、`plan_envelope`。不要出现 `user_plan`、`daily_log` 这类同义自造词。执行记录固定拼作 `checkin`（表 `checkins`、字段 `checkin_id`、接口 `/api/tasks/{id}/checkins`），不写 `check_in`。
- 业务异常继承统一基类并携带错误码，由全局异常处理器转成 [统一错误结构](01-contracts-and-ownership.md)。**不要在路由里手写错误 JSON。**
- 排期计算保持纯函数：输入快照、输出安排或冲突，不访问数据库、不调用模型。这让它可以被穷举测试。
- 时间一律存 UTC 并携带用户 IANA 时区；对外接口用明确的本地日期字符串，禁止隐式转换。
- 时长用整数分钟，不用浮点。

## 3. TypeScript 规范

- 严格模式。`any`、非空断言 `!`、`@ts-ignore` 需要在同行注释里写明理由，CI 会统计其数量。
- API 类型只能来自 `shared/api/generated/`，**禁止手写重复的响应类型**。
- 所有请求走统一的 `openapi-fetch` 客户端（同域 Cookie、CSRF 头、request id、错误映射），组件里不出现裸 `fetch`。
- TanStack Query 的 key 至少包含资源类型、用户可见 ID、日期或版本。mutation 成功后用服务器返回的 revision 精确失效相关查询。
- **计划启用、时间预算、目标关联、重大变更不做乐观更新**；简单反馈可显示"提交中"，服务器确认后才成为正式记录。
- SSE 走独立的 `JobEventClient`，保存最后事件序号，断线用 `Last-Event-ID` 补读，再调作业查询接口确认最终结果。事件只用于刷新状态，**不能仅凭事件流把计划标记成功**。

## 4. 测试规范

### 原则

测试必须保护**外部可观察行为**或某个具体回归。复杂度本身不是加测试的理由，覆盖率也不是。

- 模块行为测试放 `backend/tests/<module>/`，通过公开接口断言。
- 跨组件验收场景放 `backend/tests/e2e/`，它们应当在内部重写后依然有效。
- 修复缺陷时，如果该缺陷可能复发，补一个回归测试：先复现失败，再修，把原先错误的那个外部可观察行为固定成断言。

### 不要做的事

- 不要冻结实现细节：import 关系、模块归属、私有调用顺序、调用次数。除非它表达的是一个外部预算或幂等保证——例如"重复点击只产生一次业务结果"，这个要测。
- 不要穷举 mock 一个抽象层刻意归一化掉的内部错误。只有当内部错误产生**不同的可观察行为**时才单独覆盖。
- 不要为提高覆盖率给显而易见的脚本补测试。
- 不要把实现里的常量抄进断言。测试要独立表达期望。

### 必须覆盖的真实规则

以下来自 `06-delivery-plan.md` 的验收要求，是首版测试的重点：

| 领域 | 场景 |
| --- | --- |
| 事务与恢复 | 数据库回滚；Worker 重启后恢复；旧 Worker 晚返回不重复提交 |
| 版本竞争 | `expected_revision` 过期返回 `REVISION_CONFLICT`；失效版本不启用 |
| 幂等 | 重复启用计划不产生第二份结果；重复异步请求返回原作业 |
| 时间预算 | 总额与剩余额度不混用；预算为零；双目标争用额度不双重成功 |
| 依赖 | 反向依赖成环被拒；解除关联前处理未完成依赖 |
| 数据隔离 | 第二个用户访问第一个用户的任务、附件、作业事件全部被拒 |
| 模型异常 | `MODEL_UNAVAILABLE` 路径；结构化输出校验失败；取消与恢复竞争 |
| 凭证安全 | 模型 API Key 在响应、日志、错误详情中均脱敏 |

### 数据库

数据库相关验收使用**真实 seekdb**。SQLite 通过的结果不能作为验收依据——事务语义、并发行为、约束实现都可能不同，而这些正是要测的部分。

### 前端

Vitest + Testing Library，必要时 MSW。覆盖关键交互与契约，不镜像实现细节。重点场景：

- 未登录不渲染私有数据；401 后清理当前用户缓存，不短暂显示上一位用户内容。
- 切换目标或日期后 query key 不串数据；登出清空全部用户缓存与 SSE 连接。
- 重复点击只产生一次业务结果，冲突与过期版本有可理解的提示。
- SSE 重连后不重复追加最终消息；刷新页面可从 HTTP 状态恢复。

## 5. 日志与可观测

- 结构化日志，每条带 `request_id`；异步作业带 `job_id`。
- **禁止记录**：模型 API Key、口令、会话令牌，及其片段或哈希前缀。
- 模型调用记录用量与耗时，用于 T13 的费用观测；不记录提示词中的用户隐私字段。
- 错误日志包含足够定位的上下文，但不得包含其他用户的数据。
