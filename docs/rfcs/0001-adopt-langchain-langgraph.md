# RFC 0001：首版 Agent 编排与模型接入改用 LangChain + LangGraph

| 项 | 值 |
| --- | --- |
| 提案名称 | `adopt_langchain_langgraph` |
| 提出日期 | 2026-09-22 |
| 提出人 | 产品决策人 |
| 状态 | 已接受 |
| 影响范围 | 公共契约 / 数据模型 / 跨模块行为 |
| 关联 PR | #（待填） |

## 摘要

首版 Agent 不再自研工作流与模型适配层：编排改用 LangGraph 的 `StateGraph`，模型接入改用 LangChain 的统一 chat model 抽象（`init_chat_model` + `langchain-openai` / `langchain-anthropic`），取代原先直接调用 OpenAI / Anthropic 官方 Python SDK 的三条自研适配路径。

图按**短生命周期**使用：一次 Celery 作业内跑完即结束，不启用 checkpointer。等待用户、版本竞争、预算校验与计划变更判级仍由 seekdb 业务表和现有业务模块承担，不下放给框架。

## 动机

原决策（首版自研工作流 + 官方 SDK 薄封装）的代价集中在三处，都是本产品不产生差异化价值的地方：

1. **模型适配层要自己长期维护。** openai_responses、openai_chat_completions、anthropic_messages 三条路径的消息/内容块映射、结束原因、流事件、用量字段各不相同，供应商改一次接口就要改一次自研代码，并且要自己分别验收。
2. **步骤编排、分支与重试要自己写一遍。** 澄清、路线生成、计划生成、反馈分析是典型的多步骤有状态流程，自研意味着状态流转、节点重试、超时、流式事件桥接全部是自有代码，测试成本落在本产品身上。
3. **生态外部性。** 结构化输出、流式事件、用量统计、可观测接口在 LangChain / LangGraph 里是既有能力，自研版本只能做到"够用"，且新加入的开发者与 AI 工具缺少可参照的公开约定。

不做的后果：T08、T14 的实现量明显偏大，且长期由本仓库承担供应商接口漂移的维护责任。

## 现状

| 来源 | 当前结论 | 状态 |
| --- | --- | --- |
| `AGENTS.md`"技术基线" | Agent 为自研工作流，官方 SDK 加薄封装；**不引入 LangChain / LangGraph** | 已确认 |
| `docs/development/02-backend-proposal.md` 第 29、31 段 | 首版显式 Python 工作流 + 持久化状态，不引入 Agent 编排框架 | 已确认 |
| `docs/development/04-agent-workflows.md` 第 5 段 | 编排选型已确认：首版自研 | 已确认 |
| `docs/development/07-model-provider-design.md` 第 9 段 | 内部划分 openai_responses / openai_chat_completions / anthropic_messages 三个适配路径 | 建议 |
| `docs/development/07-model-provider-design.md` 第 29 段 | `model_configs` 增加 `sdk_type` 与 `api_mode` | 建议 |
| `docs/product/PRD.md` D08 | 首版兼容 OpenAI/Anthropic 官方 Python SDK，原生双路径 | 已确认 |
| `docs/development/06-delivery-plan.md` 第 51 段 | LangChain/LangGraph 不属于本轮实现依赖 | 已确认 |

## 提案

### 编排

每个 Agent 步骤（目标澄清、路线生成、计划生成与滚动细化、反馈分析与回顾、材料验证）实现为一张 LangGraph `StateGraph`。图的 state 是 Pydantic / TypedDict 定义的**步骤输入快照 + 中间产物**，不是业务事实源；业务事实始终从 seekdb 读取。

图**不启用 checkpointer**，也不使用跨请求的 `interrupt` / `Command(resume)`。理由见"替代方案"。一次 Worker 执行内 `invoke()` / `astream_events()` 跑完，产出结构化候选后交回业务模块落库。

等待用户的节点仍是业务状态：建计划会话的 `clarifying → profile_review → route_review → plan_review → activated` 存在 seekdb，作业状态存 `jobs`。用户下一次确认是一次**新的作业**，重新读取当前业务版本重新构图执行——这正是原设计"等待用户不占 Worker、重启可恢复、恢复后重新检查版本"的要求，与 LangGraph 的 checkpoint 恢复相比语义更严格，因为它强制重新校验业务版本而不是复用旧快照。

### 模型接入

用户配置解析后由服务端构造 chat model 实例：

```python
model = init_chat_model(
    model=config.model_id,
    model_provider=config.model_provider,   # "openai" | "anthropic"
    api_key=decrypted_key,
    base_url=config.base_url,               # 通过出站校验后才允许传入
    timeout=...,
    max_retries=...,                        # 显式设置，不用默认值 6
)
```

- 结构化输出统一用 `model.with_structured_output(Schema, include_raw=True)`：`parsed` 给业务校验，`raw` 取 `usage_metadata` 与 `response_metadata`。
- 流式输出用 `astream_events()`，事件映射到现有 SSE 契约（`queued` / `started` / `stage_completed` / `awaiting_confirmation` / `completed` / `failed`），SSE 契约本身不变。
- OpenAI 的 Responses / Chat Completions 选择由 `langchain-openai` 的 `use_responses_api` 参数承担，不再是两条自研代码路径。

### 明确不引入的部分

采用框架不等于采用框架的全部预制件。以下**不用**：

- `create_agent` 等预制 agent 循环、LangChain 的 memory / retriever / vectorstore 组件。上下文仍按"系统规则 → 已确认档案 → 领域策略 → 当前计划 → 相关执行记录 → 会话摘要"由业务代码显式装配。
- 把 `apply_change`、排期计算或数据库写入包装成模型可调用的 tool。计划变更只能走计划模块的 `apply_change`。
- LangSmith 默认追踪。`LANGSMITH_TRACING` 必须默认关闭，示例配置不得开启——用户目标内容属于私人数据，不得默认外发到第三方服务。

## 技术细节

### `model_configs` 字段变化

| 原字段 | 新字段 | 说明 |
| --- | --- | --- |
| `sdk_type`（openai / anthropic） | `model_provider`（openai / anthropic） | 取值直接作为 `init_chat_model` 的 `model_provider` |
| `api_mode`（responses / chat_completions / messages） | `api_mode`（仅 `model_provider=openai` 时有效：responses / chat_completions） | 映射 `use_responses_api`；anthropic 下必须为空 |
| `endpoint` | `base_url` | 与 LangChain 参数同名，减少拼接歧义 |

组合校验：`model_provider=anthropic` 且 `api_mode` 非空 → 拒绝。`api_mode` 变化仍视为配置版本变化。其余字段（`credential_ciphertext`、`encryption_key_version`、`revision`、`enabled`、`capabilities_json`、`last_test_at`）不变。

尚无迁移文件，属于设计期字段重命名，**不是破坏性数据迁移**。

### 重试预算

LangChain chat model 的 `max_retries` 默认 **6**。与 Celery 任务重试叠加会产生乘法放大（最坏 6 × Celery 次数次真实计费调用）。因此：单次作业的模型调用总次数由统一调用预算约束，`max_retries` 必须显式设置，不允许使用默认值。这条替代原设计中"SDK 内置重试与 Celery 重试使用统一调用预算"的同名约束，结论不变。

### 三条全局约束

- **`expected_revision`**：不受影响。图不持有业务版本的写权限，提交仍在图外的短事务里做版本校验。
- **幂等**：不受影响。业务结果提交仍由 `jobs` 表与唯一约束保证只提交一次；框架重试只影响模型请求次数，不影响业务提交次数。
- **数据隔离**：新增一条约束——chat model 实例按 `owner_id` + 配置 revision 构造，禁止跨用户复用实例或通过全局环境变量传递密钥。

### 出站访问控制

`base_url` 交给 LangChain 之前，仍由本产品的出站校验执行：限制协议与端口、校验 DNS 解析与重定向、阻断云元数据与未授权内网、本地端点走实例管理员允许列表。框架不提供这层保护，不得因为改用框架而省略。

### 依赖

`langchain`、`langchain-core`、`langchain-openai`、`langchain-anthropic`、`langgraph`。具体版本在 T02 锁定并写入锁文件。LangGraph 的 `StateGraph`、`add_node`、`add_edge`、`add_conditional_edges` 与 `compile()` 属于 1.x 稳定接口。

## 替代方案

**A. 维持自研工作流（现状）。** 差异化价值为零，供应商接口漂移的维护责任长期由本仓库承担，T08/T14 实现量最大。已排除。

**B. 只用 LangGraph 编排，模型层保留官方 SDK 自研适配。** 改动面最小，PRD D08 完全不动。排除原因：三条自研适配路径正是维护成本的主要来源，保留它等于保留了本 RFC 想解决的问题，只换掉了成本较低的那一半。

**C. 用 LangGraph 的 checkpointer 承载"等待用户"。** 看起来更"框架原生"，但被排除，原因有三：
- seekdb 走 MySQL 协议，官方 checkpointer 只有 Postgres / SQLite / Redis，必须自己实现 `BaseCheckpointSaver`——刚砍掉的自研组件又长回来，而且是个未经验证、承载业务恢复语义的自研组件，T01 / T07 验收范围随之扩大。
- Redis checkpointer 虽然现成，但与已确认的"Redis 不作为唯一业务记录"直接冲突：等待用户可能跨天，长期状态不能只存在 Redis 里。
- 恢复语义更弱。checkpoint 恢复会沿用旧快照，而本产品明确要求"恢复后重新检查版本"。短生命周期图 + seekdb 业务状态天然满足这一点。

**D. 引入 LangChain 的 `create_agent` 预制循环。** 排除：本产品的步骤边界、判级规则和确认点都是确定性的，让模型自主决定调用顺序会削弱"模型不能自行放宽成功标准"的约束，且难以稳定测试。

**什么都不做**：等同于方案 A。

## 影响与迁移

| 受影响对象 | 影响 |
| --- | --- |
| T08 模型接入与领域策略 | 验收项从"两条 SDK 原生路径分别验收"改为"两个 LangChain provider 分别验收"；适配代码量下降，领域策略与结构化校验不变 |
| T14 个人模型配置 | 配置字段改为 `model_provider` / `api_mode` / `base_url`；测试调用改为构造 chat model 后发最小请求 |
| T09 规划到启用闭环 | 图的组织方式改变，启用事务与版本校验不变 |
| T02 工程与契约基础 | 新增五个 Python 依赖并锁定版本；`agent/` 目录下 `providers/` 改为 `graphs/` + `models/` |
| T07 作业执行与恢复 | 不变。outbox、租约、有限重试、持久化事件全部保留 |
| T01 seekdb 验证 | 不变。不新增 checkpoint 表 |
| 前端 | 模型配置页字段名随契约更新；SSE 事件契约不变 |
| 已有数据 | 无。尚未生成任何迁移 |

## 未决问题

- LangChain 与 LangGraph 的具体锁定版本，随 T02 确定。
- 各 provider 的能力探测（原生结构化输出、流式）在 LangChain 抽象下的实际表现，需真实调用验证；当前未执行任何真实调用。
- 自定义兼容服务（DeepSeek、Ollama 等）经 `langchain-openai` 的 `base_url` 接入的实际兼容性，需实测，不能凭"OpenAI 兼容"字样断定通过。
- 刻意排除在本 RFC 范围外：是否引入 LangSmith 或其他可观测平台（当前结论是默认关闭，不做选型）；是否为 Agent 提供工具调用能力（首版非必需）。

## 接受后的回写清单

- [x] `AGENTS.md` 技术基线
- [x] `docs/development/00-development-planning.md` 输入约束
- [x] `docs/development/02-backend-proposal.md` 模块与 Agent 章节
- [x] `docs/development/04-agent-workflows.md` 编排选型与模型调用章节
- [x] `docs/development/06-delivery-plan.md` 技术基线、T08、T14、验收条款
- [x] `docs/development/07-model-provider-design.md` 模型层与字段
- [x] `docs/development/11-route-engine.md` 适配器验收项
- [x] `docs/engineering/00-workflow.md` 角色表
- [x] `docs/engineering/03-code-and-test-standards.md` 目录结构
- [x] `docs/product/PRD.md` D08
- [x] `docs/product/06-model-configuration.md` 用户体验与字段
- [ ] `CONTEXT.md`：无需改动，未引入新领域术语
- [ ] 契约变更 PR：`model_configs` 字段改名需随 T14 的契约 PR 提交
- [ ] 通知 T08、T09、T14 负责人
