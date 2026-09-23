# 首版后端架构提案

状态：用户已确认总体方案，数据库为 SQLite 单文件库（见 [RFC 0003](../rfcs/0003-sqlite-as-primary-store.md)）；其余组件按本方案推进。数据库行为已由 [T01](../worklog/T01-sqlite-verification.md) 实测验证。资料核对日期：2026-09-23。

## 技术组合

| 责任 | 推荐 |
| --- | --- |
| HTTP 接口与请求校验 | FastAPI + Pydantic |
| 业务数据 | SQLite 单文件库，WAL 模式，驱动为标准库 `sqlite3` |
| 数据访问与迁移 | SQLAlchemy 2 + Alembic |
| 后台执行 | Celery Worker + Redis 消息队列 |
| 定期扫描 | 单实例 Celery Beat，提交到期作业 |
| 前端进度更新 | HTTP 提交请求，SSE 接收事件，查询接口恢复最终状态 |
| 附件 | 私有对象存储，数据库保存归属和元数据，供应商待定 |
| 初期部署 | Linux + Docker Compose + Nginx，同域提供网页与 /api |

采用模块化单体：一个 Python 后端代码库，API、Worker、Beat 为不同进程，共用业务模块和数据库。数据库是随进程打开的本地文件，因此这三类进程**必须同主机、同本地文件系统、同一个库文件**，且该文件不得置于 NFS / SMB 等网络文件系统——SQLite 的锁依赖 POSIX 文件锁语义，网络文件系统上不可靠。这也意味着 Worker 无法横向扩展到第二台主机；当前部署形态本就是单机，但要扩容就得先换数据库，不是换部署方式。单机存在故障停机风险，恢复演练见 T01 的 F2。Windows 开发者不需要为数据库起容器（T01 G1 已验证）。

## 模块及 Interface

- 账号模块：注册、认证、会话与资源归属；其他模块接收服务端确定的用户身份，不能信任请求中自报的用户 ID。
- 目标与计划模块：创建目标、选择路线、保存草稿、启用与修订计划；封装版本和状态转换，只有该模块执行正式计划变更。
- 排期模块：根据任务、依赖、领域约束和时间预算计算每日安排或冲突结果；计算本身不写库，提交时校验输入版本，避免覆盖较新的记录。
- 执行记录模块：记录完成、部分完成、困难和材料，分别保存执行与验证状态，提供历史查询。
- Agent 模块：澄清、路线生成、任务细化、反馈分析，产出结构化建议，不能直接任意修改数据库。
- 后台作业模块：持久化作业状态、分发、重试、取消请求与运行事件；内部实现对调用者隐藏队列细节。

按 codebase-design 技能，复杂规则集中在上述模块的少量 Interface 后；HTTP 路由与后台 Worker 调用同一套规则。Agent 步骤的调用顺序由 LangGraph 承担，业务规则不进入图节点内部。

编排与模型接入已确认：Agent 步骤实现为 LangGraph `StateGraph`，模型通过 LangChain 统一 chat model 抽象接入，provider 范围为 OpenAI 与 Anthropic（PRD D08），供应商与模型由每位用户自行配置。**图为短生命周期**——一次 Celery 作业内跑完即结束，不启用 checkpointer，也不使用跨请求的 `interrupt` / `Command(resume)`。流程状态与等待用户的节点由数据库持久化，用户下一次确认是一次新作业，重新读取当前业务版本重新构图执行；这比 checkpoint 恢复更严格，因为它强制重新校验业务版本而不是复用旧快照。Celery 负责后台执行，Pydantic 与业务模块负责输出和变更校验。不建设通用 Agent 平台，也不采用框架的预制 agent 循环与记忆/检索组件。选型理由与被排除的替代方案见 [RFC 0001](../rfcs/0001-adopt-langchain-langgraph.md)。

## Agent 与确定性规则

最新确认：项目开源，模型由每位用户配置。下述集中接入指统一调用实现，不是所有用户共用一个密钥或被限定一个供应商。个人配置与协议适配详见 [模型接入设计](07-model-provider-design.md)，替代此前单一供应商待选的假设。

模型负责理解、估计、生成和解释；后端校验结果结构、目标归属、时间总额、依赖无环和变更权限。模型给出时长不代表估计必然准确，实际反馈用于修正。

领域策略提供学习、健身和通用目标的专属澄清字段及规则；三类的覆盖深度、硬约束与责任边界已确认，见 [三类领域专业规则与责任边界](../product/11-domain-rules.md) 与 [领域策略包与约束校验实现基线](16-domain-policy-design.md)。首版通过统一的模型调用 Interface 集中处理超时、流式输出、结构化输出、用量及错误，其下由 LangChain 的 chat model 抽象承担供应商差异（见 [模型接入设计](07-model-provider-design.md)）；OpenAI 与 Anthropic 两个 provider 分别验收，不以一个 provider 通过代替另一个，也不提前建设完整插件平台。

小调整经规则检查后可执行并记录原因；大调整存为待确认建议。确认时再次检查基础版本与共享时间预算。模型输出非法、依赖冲突或计划已变更时返回可解释的失败或重新生成请求。

## 关键执行流程

生成计划：验证身份和目标状态 → 数据库事务保存作业及待分发记录 → Worker 调模型 → 校验结构与业务条件 → 保存计划草稿和作业结果 → 页面展示总览与首周安排 → 用户开始执行 → 事务校验版本与预算并启用。

每日安排：按用户时区，由定期扫描发现需要准备的日期；首次打开页面时也可请求补齐缺失安排。使用用户、日期及输入版本去重，不因反复打开页面重生成。多个目标更新时串行或乐观并发校验同一用户预算。

聊天与生成任务通过持久化作业运行，断开浏览器不等于取消。SSE 事件带序号，重连补读已保存的阶段事件；逐 token 内容可仅为临时展示，最终回答必须持久化。查询接口是状态恢复兜底。

## 数据与可靠性

实体划分为账号与目标、计划与任务版本、时间预算与每日安排、执行与材料、后台作业与审计五组。**表名、字段与约束的事实源是 [核心数据模型](03-data-model.md)**，本文不再并列一份表清单——两份清单一定会发散。

SQLite 库文件为业务事实来源。关联、日期、状态和归属使用关系字段；领域扩展字段与模型输出快照存为 TEXT，写入前用 `json()` 校验并带结构版本，查询用 `json_extract`，不使用 PostgreSQL 专属 JSONB 类型或语法。历史计划版本和执行记录不随重排覆盖。首版可按目标档案、当前计划、近期记录与会话摘要组织上下文；未来要做语义检索时由 sqlite-vec 扩展（向量，T01 G3 已验证可加载）与内置 FTS5（全文）承载，与业务表同文件、同备份，**无需现在增加语义检索范围**。

SQLAlchemy 2 + Alembic 保留，方言为 `sqlite+pysqlite`，没有第三方驱动。连接装配有三条不能省的设置，漏掉任何一条都不会报错，保护却已经消失：

- **每条连接**重设 `foreign_keys=ON`、`busy_timeout`、`synchronous`。它们是连接级设置，不写进库文件；`journal_mode=WAL` 是库级的，写进文件后持久生效。
- 关掉 pysqlite 的隐式事务管理（`isolation_level=None`），否则拿不到事务性 DDL，也无法自己控制事务开始方式。
- 写事务一律 `BEGIN IMMEDIATE`。`BEGIN DEFERRED` 在写第一行时才升级写锁，升级冲突**不受 `busy_timeout` 保护、也无法靠等待解决**。

迁移必须启用 Alembic batch 模式（`render_as_batch=True`）：SQLite 的 `ALTER TABLE` 只支持有限操作，改列类型或约束都要重建表。重建路径最危险的失败模式是漏列静默丢数据，契约 PR 的 review 要逐列核对。

数据库行为已由 T01 逐项实测，结论与替代实现见 [T01 交接卡](../worklog/T01-sqlite-verification.md)。要点：复合唯一约束、CHECK、部分唯一索引、事务性 DDL、`VACUUM INTO` 在线备份与恢复演练均可用；条件更新的影响行数按**匹配**计数，不存在 MySQL `CLIENT_FOUND_ROWS` 那类需要固定语义的陷阱；`FOR UPDATE` 与 `SKIP LOCKED` 不支持，作业抢占改用租约式条件更新；类型靠亲和性而非校验，字段类型正确由 SQLAlchemy 类型层与 Pydantic 负责。多进程并发写在库级别串行，靠带退避的有限重试恢复，已验证不产生不可恢复的 `SQLITE_BUSY`。

Redis 负责队列传递，不作为唯一业务记录。采用事务内作业记录与 outbox，分发器重试投递；投递成功但标记失败可能重复发送，因此 Worker 必须基于作业 ID、状态锁定和唯一约束防止重复提交业务结果。定期检查失联作业，有限重试，保留错误状态。无法保证外部模型调用只收费一次，需区分业务提交幂等与供应商请求幂等。

模型调用设置超时、重试上限、并发和用量限额；预算数额按用户要求暂缓。LangChain chat model 的 `max_retries` 默认为 6，与 Celery 重试叠加会产生乘法放大，必须显式设置并纳入单次作业的统一调用预算。调用日志保存模型、提示版本、用量与错误，避免默认记录密钥或完整私人内容；**LangSmith 追踪默认关闭**，示例配置不得开启——用户目标内容属于私人数据，不默认外发到第三方服务。

账号采用自由注册、账号标识加密码和服务端会话，Cookie 设置 HttpOnly、Secure 及适当 SameSite，写请求校验来源/CSRF。注册和登录分别限流；会话、附件、作业事件和 Agent 工具都检查用户归属。

## 实施顺序与验收

1. 账号、目标、时间预算、任务及记录：不用模型也能保存和查看业务数据；验证越权与数据隔离。
2. 接入澄清、路线比较、草稿和启用：验证草稿不生效、重复点击不重复创建、过期版本不能覆盖。
3. 加入多目标排期、反馈调整及日志：验证预算不足、依赖循环、隔离目标、重大变更待确认。
4. 补齐后台恢复、流式进度、附件与试用观测：验证断线、作业重复投递、模型超时与生成失败。

多人开发按上述模块分工，公共数据库迁移与 Interface 由指定负责人协调；单仓库组织 frontend/、backend/、docs/、infra/。合并前验证接口契约与核心闭环。

## 待定

模型供应商、对象存储和托管环境需验证中国大陆实际可用性；框架选型不保证网络可用性。注册防滥用、调度触发时间、数据保留期及资源规格尚未确定。本提案不执行采购、部署或代码初始化。

## 官方参考

- [FastAPI 特性与 OpenAPI](https://fastapi.tiangolo.com/features/)
- [FastAPI 后台任务适用范围](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [SQLAlchemy](https://docs.sqlalchemy.org/en/20/)
- [Alembic](https://alembic.sqlalchemy.org/en/latest/)
- [Celery 任务与幂等](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
- [Celery Redis 队列](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [LangGraph 图 API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [LangChain chat model 与 `init_chat_model`](https://docs.langchain.com/oss/python/langchain/models)
- [SQLite 事务与 BEGIN 模式](https://www.sqlite.org/lang_transaction.html)
- [SQLite WAL 模式](https://www.sqlite.org/wal.html)
- [SQLAlchemy pysqlite 方言与事务处理注意事项](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#pysqlite-serializable)
- [Alembic batch 模式（SQLite 表重建）](https://alembic.sqlalchemy.org/en/latest/batch.html)
- [sqlite-vec](https://github.com/asg017/sqlite-vec)
