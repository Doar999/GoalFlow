# T08 模型接入与领域策略

| 项 | 值 |
| --- | --- |
| 工作包 | T08（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | 待指定（Agent 负责人） |
| 状态 | 进行中：T14 作业 Interface 已提 PR #29；T08 对话与模型调用契约单独建 PR |
| 更新日期 | 2026-09-25 |
| 相关 PR | #27（交接卡）、T14 #26/#28（已合并）、#29（作业 Interface，待评审）、#30（契约，待评审） |

## 1. 目标

交付供业务模块调用的 Agent 步骤能力：从用户描述提取有来源的目标档案候选，内部选择学习、健身或通用领域策略，由规则层判定澄清就绪状态；通过 LangGraph 编排短生命周期步骤，通过 LangChain 调用用户选择的 OpenAI 或 Anthropic 配置，记录提示版本、用量和可解释错误。输出是经过结构与领域硬约束校验的候选，不自行启用计划。

完成时须分别用固定模型样例验证确定性规则、用两个 provider 的真实调用验证接入，并证明重复投递、版本过期、取消及 Worker 恢复不会重复提交业务结果。T09 消费本包的候选能力，负责路线、计划到启用的完整闭环。

## 2. 范围

本包按可独立评审的短 PR 交付：先收敛契约，再实现规则与固定样例，再接模型及作业，最后做真实模型评估。公共契约变更必须单独提 PR，不能与业务实现混合。

### 可修改路径（业务实现 PR）

```text
backend/src/goalflow/agent/
backend/tests/agent/
docs/worklog/T08-model-and-domain-strategy.md
docs/worklog/README.md                 （仅索引）
```

T14 配置读取 Interface 由独立 PR #29 负责。澄清消息的持久化与 HTTP 接线、T04/T07 的作业接缝由本轮独立契约 PR 界定；业务实现 PR 不直接修改其他模块内部文件。

### 公共契约 PR 可修改路径（本轮确认）

```text
backend/migrations/versions/           （conversations/messages/model_calls）
backend/src/goalflow/conversations/    （仅 ORM）
backend/src/goalflow/agent/models.py   （仅 model_calls ORM）
backend/src/goalflow/contracts/enums.py（仅消息角色枚举）
backend/src/goalflow/api/routes/conversations.py（仅 HTTP 契约桩）
backend/src/goalflow/api/app.py        （注册契约桩）
backend/tests/db/test_models_match_migrations.py（登记 ORM）
backend/tests/conversations/           （契约与迁移验收）
openapi/goalflow.yaml                  （仅由 FastAPI 导出）
frontend/src/shared/api/generated/     （仅由生成命令更新）
docs/rfcs/                             （本次跨模块契约提案）
```

迁移序号按 2026-09-25 最新 `main`（head=0007）拟定为 0008（对话与消息）、0009（模型调用），数据负责人在评审时确认；不修改已合并迁移。契约 PR 只建表、ORM 和 HTTP 桩，不混入保存消息、创建作业或模型计量业务实现。

### 明确不可修改

```text
backend/src/goalflow/model_configs/    （T14 所有）
backend/src/goalflow/goals/            （T04/T09 所有）
backend/src/goalflow/scheduling/       （T05 所有）
backend/src/goalflow/links/            （T06 所有）
backend/src/goalflow/jobs/             （T07 所有）
frontend/                               （生成类型仅随独立契约 PR 更新）
docs/product/、docs/development/、docs/engineering/
```

### 不在本包范围内

- T14 的凭证加密、配置增删改、出站地址校验与 `/test` 最小调用；T08 经 T14 提供的 Interface 读取和调用配置，不复制凭证逻辑。
- T09 的路线集合、计划草稿、任务批次持久化与计划原子启用；T08 只交付候选生成与校验能力。
- T11 的材料登记、执行记录与独立验证结果持久化，T12 的调整判级与回顾流程；T08 可以提供辅助产出候选，不替这些模块写状态。
- 主动联网检索、LangChain memory/retriever/vectorstore、`create_agent` 预制循环、LangGraph checkpointer、模型可调用的数据库写入工具，以及货币预算。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [CONTEXT.md](../../CONTEXT.md) | Goal Domain、Domain Strategy、Goal Profile、Planning Assumption、Assistance Artifact 等术语 | 术语事实源 |
| [PRD.md](../product/PRD.md) | R02、R03、R14、R15；D01、D08、D09、D13 | 产品规则已确认；Q01、Q03、Q07 位于「建议补充的质量要求」，不能误写成已确认产品决策 |
| [07-goal-clarification.md](../product/07-goal-clarification.md) | 来源区分、少量追问、规则层停止澄清、用户不选择领域 | 已确认 |
| [11-domain-rules.md](../product/11-domain-rules.md) | 三个主领域的结构硬约束、软偏好与责任边界 | 已确认 |
| [04-agent-workflows.md](../development/04-agent-workflows.md) | LangGraph 短生命周期图、结构化候选、作业与恢复；具体图状态与默认值 | 编排选型已确认；其余为实施设计建议 |
| [10-clarification-engine.md](../development/10-clarification-engine.md) | 澄清处理流程、来源与 readiness、重复问题控制 | 首版实现基线已确认；`evaluate_clarification` 的公开签名标为建议 |
| [07-model-provider-design.md](../development/07-model-provider-design.md) | LangChain 统一 chat model、双 provider、调用与能力语义 | 双 provider 已确认；provider 与能力契约章节仍标为建议，T14 已落地的配置字段以 PR #26 契约为准 |
| [16-domain-policy-design.md](../development/16-domain-policy-design.md) | 策略包结构、校验点、阻断集合与提示版本绑定 | 实施设计建议；实现前需按决策规程确认 |
| [15-assistance-and-verification-design.md](../development/15-assistance-and-verification-design.md) | 辅助能力与材料/验证模块边界 | 实施设计建议；新增 API 与 `artifacts` 字段另走契约 PR |
| [05-module-contracts.md](../development/05-module-contracts.md) | `run_step(step_kind, input_snapshot) → validated_candidate` 等模块边界 | 建议契约；实际 HTTP 形状以已导出的 OpenAPI 为准 |
| [06-delivery-plan.md](../development/06-delivery-plan.md) | T08 交付、T14 前置、T09 后续依赖 | 任务拆解建议；本卡确定 T08 实施边界 |
| [T07-job-execution.md](T07-job-execution.md) | 作业处理函数、提交协议 E13/E14/E18、HTTP 202 验收转交 T08 | 已完成、相关决策已确认 |
| [T14-model-config.md](T14-model-config.md) | 模型配置契约与 T08 分工；PR #26/#28 的迁移、枚举和六个端点 | 契约与业务实现已合并；作业侧 Interface 在 #29 待评审 |
| `backend/pyproject.toml`、`backend/uv.lock` | LangChain、LangGraph 依赖和锁定版本 | T02 已落地的工程事实；实现前仍需核对所用 API 的官方文档 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要升级为 RFC |
| --- | --- | --- | --- | --- |
| A1 | T08 的公开业务出口保持为「输入快照 → 已校验候选或可解释错误」；图节点不直接修改目标、计划、排期或作业表。具体函数签名在首个实现 PR 前定稿 | 05 号建议契约、04 号短生命周期图约束 | Agent 模块内部及 T09 消费接缝 | 若改变跨模块接口，先走 RFC/契约确认 |
| A2 | 首个可执行切片先用固定模型样例验证来源、readiness、三领域硬约束与版本过期，再接真实模型；固定样例结果明确标记，不能计作真实模型质量验收 | 01 号契约规范第 9 节、06 号交付计划 | T08 的 PR 顺序与验收 | 否 |
| A3 | T14 已固定 `model_provider`、`api_mode` 和配置 revision；T08 仅绑定配置 ID/revision 与非敏感快照。作业侧 Interface 在独立 #29 中按 owner 复查并构造模型；T08 不直接读配置内部表或凭证 | T14 交接卡 A3/A4/A5/A15/A16；用户 2026-09-25 确认方案 | T08/T14 接缝 | 否，按独立 #29 实施 |
| A4 | `model_calls` 在数据模型中有建议字段，但尚无迁移；用量缺失须保持「未知」，不能记作 0。迁移 0009 为本轮契约提案，具体记录时点与失败调用在业务 PR 落地 | 03 号数据模型、Q07 建议质量要求、RFC 0004 | 用量与数据库契约 | 是，随契约 PR 评审 |
| A5 | T08 仅确认「两个 provider 分别验收」；具体模型 ID、OpenAI API 模式覆盖范围、原生结构化输出能力门槛和真实评估样例仍需在实现前收敛。T14 配置契约允许的 API 模式不等于 T08 已完成对应真实调用验证 | PRD D08、07 号建议能力契约、T14 PR #26 | 真实模型验收 | 跨模块能力契约需负责人确认 |
| A6 | T07 首个真实业务作业的 HTTP 验收由 T08 承接：新提交返回 202 与 `job_id`，重复请求返回原作业引用；图内模型调用在写事务外，提交回调内重新检查输入版本和租约 | T07 E13/E14 与第 5 节未完成验收 | 澄清作业接线 | 否，沿用已确认协议 |
| A7 | 领域策略结构和校验点采用 16 号实施建议前，先逐项确认跨模块可见规则；已确认的 11 号产品硬约束不允许通过提示词或模型判断来代替服务端校验 | 11 号已确认产品规则、16 号建议实施设计 | T08 策略与 T09/T11 使用方 | 影响公共行为的取舍需走决策规程 |
| A8（提案） | 创建目标时创建一条初始澄清对话并保存初始用户描述；对话表允许未来一个目标有多条对话，消息按对话内序号排序，用户消息以 Idempotency-Key 防重复。创建目标的原子写入属于后续业务 PR | 10 号第 3/6 节、03 号建议数据模型；用户同意先做独立契约 PR，具体形状在 RFC 0004 评审 | T04/T08 数据接缝 | 是，随独立契约 PR 评审 |
| A9（提案） | HTTP 增加 `GET /api/goals/{id}/conversations` 供刷新后恢复对话，`POST /api/conversations/{id}/messages` 带文本与 `expected_revision` 返回 202 消息及作业引用；附件引用等待 T11 契约。契约 PR 只提供桩 | 10 号第 6 节、05 号 submit_message 与 T07 202 协议；具体形状在 RFC 0004 评审 | 前后端公共契约 | 是，随独立契约 PR 评审 |
| A10（提案） | `model_calls` 记录 owner、job、provider、model、prompt_version、状态、可空 token 用量和耗时；未知用量保持 NULL。03 号建议的 `estimated_cost` 保留可空列，定价与币种尚无契约时不填 | 03 号建议数据模型、Q07 建议质量要求；具体形状在 RFC 0004 评审 | T08 计量契约 | 是，随独立契约 PR 评审 |

## 5. 验收场景

- [ ] 自然描述生成带来源的档案候选；模型推断与未知不被标为用户确认事实，用户已有共享时间预算不被重复索取。
- [ ] 模型声称 `review_ready` 但关键结果或安全信息缺失时，规则层重新判为 `needs_input` 或 `blocked`；非阻塞缺口不能造成无限追问。
- [ ] 领域路由由内部完成，低置信度时使用通用策略并追问事实；不向用户提出「选择领域」问题。
- [ ] 学习、健身、通用三套已确认结构硬约束用固定候选逐条验证；违反硬约束的候选整体拒绝，软偏好不当作拒绝理由。
- [ ] 模型输出的字段路径、实体 ID、目标归属和来源引用受服务端允许集合约束；非法结构或越权引用不落库，失败可解释。
- [ ] 两个相同的澄清提交只生成一个有效修订；输入 revision 在模型调用期间变化时旧结果标为 stale，不能覆盖新档案。
- [ ] 首个 T08 异步 HTTP 入口对首次提交和重复提交均返回 202、同一 `job_id`；刷新/SSE 断线后能查询最终状态，取消或旧 Worker 晚返回不会重复发布结果。
- [ ] OpenAI 与 Anthropic 分别覆盖成功、结构化结果、错误和用量路径；无凭证/禁用配置、超时、限流、非法结构等失败不泄露凭证或私人内容。
- [ ] 模型调用次数与 T07 最多三次作业尝试共同受限；`max_retries` 显式设置，未知用量不伪造为零，提示与策略版本可追溯。
- [ ] LangGraph 图在一次作业内结束，不依赖 checkpointer 或跨请求的框架状态；等待用户期间不占用 Worker。
- [ ] `bash scripts/check.sh` 与 `bash scripts/test.sh` 全部通过；数据库相关用例使用真实库文件、WAL 和生产连接 pragma。真实模型评估单独记录实际 provider、模型与结果，不以固定样例代替。

## 6. 进展

- 已完成：T08 交接卡 #27 已合并；T02/T07 与 T14 #26/#28 已合并；T14 作业侧修补已提 PR #29。
- 进行中：T08 公共契约在 PR #30 待评审，包含两条迁移、ORM、HTTP 桩、RFC 0004、导出的 OpenAPI 与前端类型；本地门禁通过。
- 未开始：规则与固定样例、模型和作业接线、双 provider 真实评估。

## 7. 验证结果

2026-09-24，Windows，使用 Git Bash 执行统一脚本；本次仅变更文档，未新增数据库用例。

```text
$ bash scripts/check.sh  （通过 Git Bash 执行）
后端：128 files already formatted；Ruff All checks passed!；mypy 62 source files 无问题
前端：锁文件一致；eslint、tsc --noEmit、Prettier 均通过
契约一致；未发现疑似凭证；检查通过

$ bash scripts/test.sh  （通过 Git Bash 执行）
后端：376 passed, 1 warning in 41.54s
前端：Test Files 3 passed (3), Tests 20 passed (20)
测试通过
```

系统默认的 `bash` 指向未安装发行版的 WSL，因此改用本机 Git Bash 执行同一组脚本。后端测试沿用仓库现有 fixture；本次没有新增或宣称完成 T08 的数据库行为验收。唯一 warning 为 Starlette 对 anyio 别名的既有弃用提示。

2026-09-25，T08 契约 PR，Windows / Git Bash：

```text
$ bash scripts/api-generate.sh
已写入 openapi/goalflow.yaml；已写入 frontend/src/shared/api/generated/schema.d.ts

$ bash scripts/check.sh
后端：144 files already formatted；Ruff All checks passed!；mypy 71 source files 无问题
前端：锁文件一致；eslint、tsc --noEmit、Prettier 均通过
契约一致；未发现疑似凭证；检查通过

$ bash scripts/test.sh
后端：438 passed, 1 warning in 52.42s
前端：Test Files 3 passed (3), Tests 20 passed (20)
测试通过
```

新增迁移的 ORM 漂移与升级/回滚由 `test_models_match_migrations.py` 验证；消息序号唯一性用生产同构的真实 SQLite 文件、WAL 与 foreign_keys=ON 验证。唯一 warning 仍是 Starlette/anyio 既有弃用提示。对话路由尚是契约桩，不能算澄清业务完成。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| T14 #29 的作业调用 Interface 待评审 | 避免跨模块直接读取凭证、重复实现出站校验或模型构造 | T14 与 Agent 负责人 |
| RFC 0004 的对话/消息与 model_calls 契约、迁移 0008/0009 序号待评审确认 | 契约合并前不能开始依赖其形状的业务接线 | 契约、数据、T04/T07/T08 负责人 |
| 澄清消息、对话和 `model_calls` 的迁移与 HTTP 契约尚未落地；T04 四个生成类端点仍是契约桩 | T08 的首个真实作业与 T09 接线需要稳定契约 | 契约/数据负责人、T08/T09 负责人 |
| OpenAI/Anthropic 的真实评估模型、API 模式覆盖范围、能力门槛与所需测试凭证 | 无法据固定样例宣称双 provider 真实验收完成 | Agent 负责人、产品决策人 |
| 16 号领域策略包的结构、校验点及「周训练量增幅」基线算法仍属实施建议 | 不能把推荐结构或算法直接实现为已确认规则 | Agent、业务规则与产品负责人 |
| D09 的辅助产出候选与 T11 的材料登记/验证之间的 Interface，及 15 号建议的能力位扩展 | 避免辅助产出写入 checkin 或成为同一任务的验证材料 | T08/T11 与契约负责人 |
| `audit_events` 的表归属仍未定（T07 第 8 节） | 计划与模型相关审计不能自行建表 | 数据与集成负责人 |

## 9. 给接手者

1. 从最新 `main` 起步。T14 #26/#28 已交付配置契约与业务接口；作业侧模型解析在独立 #29 交付后由 T08 消费。T14 的 `/test` 只做最小连接测试，不经过 T08 的图。
2. T04 已有档案、路线与计划确定性核心；四个生成端点（`route-generations`、`route-variants`、`plan-generations`、`plan-change-requests`）仍为契约桩。接线前与 T09 明确谁拥有 HTTP 提交、候选校验及最终持久化。
3. T07 提供作业租约、去重、取消、恢复和条件提交。模型调用不进入写事务；处理函数提交时用 T07 的 `commit(session)` 协议，在同一事务内重新校验输入 revision。不要自建第二套作业状态或把 Celery ID 当业务身份。
4. 模型输出、对话摘要和上传材料都不是业务事实源。档案字段来源、领域硬约束、预算和版本必须由服务端重新校验；图仅编排单次步骤，不承担跨请求记忆。
5. 真实 provider 评估须分别记录；配置模式在 OpenAPI 中存在，只说明可保存，不证明供应商调用或结构化输出已通过。不得把失败或未知用量写成 0，也不得在日志、事件或测试样例中写入凭证。
