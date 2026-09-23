# T07 作业执行与恢复

| 项 | 值 |
| --- | --- |
| 工作包 | T07（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | （待填，后台负责人） |
| 状态 | 进行中：PR-1（幂等存储）已完成实现，`check.sh`、`test.sh` 全绿，待提 PR |
| 更新日期 | 2026-09-23 |
| 相关 PR | #（待填） |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容。

## 1. 目标

交付两样被后续模块共用的基础能力：

1. **通用幂等存储**：写接口携带的 `Idempotency-Key` 真正生效——相同 key 相同内容返回原结果，相同 key 不同内容返回 `IDEMPOTENCY_KEY_CONFLICT`。T04 的第一个版本化写接口依赖它，因此最先交付。
2. **持久化作业**：作业与 outbox 同事务写入、分发重试、租约领取与续租、有限重试、取消、恢复扫描和持久化事件（含 SSE 补读）。做完之后，T08、T09、T11、T14 只需注册一个作业处理函数，就能得到"重复投递不重复提交、旧 Worker 晚返回提交不了、重启可恢复"的保证。

达成标准：06 的 T07 验收项（重复投递 / 旧 Worker 晚返回不重复提交）、[04-agent-workflows.md](../development/04-agent-workflows.md) 第 8 节中与作业相关的四条、PRD Q03（刷新、断网、Worker 重启后能查到最终状态，无虚假成功）全部有对应测试，并在与生产同构的 SQLite 配置上通过。

**本工作包不实现任何真实的作业种类。** 路线生成、计划生成、今日安排、材料验证等处理函数属于 T08、T09、T11；T07 只在测试里注册假的作业种类来验证协议。也不接入 LangGraph 或模型。

## 2. 范围

按决策 E1 拆为三个 PR，依次合并。每个 PR 内契约提交在前、实现提交在后。

### 可修改路径

契约面：

```text
backend/migrations/versions/0002_T07_create_idempotency_requests.py   （PR-1）
backend/migrations/versions/0003_T07_create_job_tables.py             （PR-2：jobs、job_events、outbox_events）
backend/src/goalflow/contracts/enums.py          （PR-2：新增 JobStatus、JobEventType）
backend/src/goalflow/api/dependencies.py         （PR-1：仅 require_idempotency_key 的 docstring，见 E26）
backend/src/goalflow/api/routes/jobs.py          （PR-3：inspect_job、watch_job、cancel_job）
backend/src/goalflow/api/app.py                  （PR-3：仅挂载 jobs 路由）
openapi/goalflow.yaml、frontend/src/shared/api/generated/  （PR-3：只经 api-generate.sh 生成）
docs/development/03-data-model.md                （仅第 6 节 jobs / outbox_events / idempotency_requests 三行与迁移对齐，见 E10、E11）
docs/development/04-agent-workflows.md           （仅第 1 节作业状态行与第 7 节事件清单，见 E9、E19）
```

业务实现：

```text
backend/src/goalflow/idempotency/                （新模块，PR-1）
backend/src/goalflow/jobs/                       （新模块，PR-2、PR-3）
backend/tests/idempotency/
backend/tests/jobs/
backend/tests/db/test_models_match_migrations.py （仅补 import 行）
backend/pyproject.toml、backend/uv.lock          （仅当实现证明需要新依赖时；celery、redis 已在依赖中）
docs/worklog/T07-job-execution.md
docs/worklog/README.md                           （仅索引表）
```

### 明确不可修改

```text
backend/src/goalflow/db/engine.py、db/session.py （T16 已交付，需要改时回到数据负责人）
backend/src/goalflow/auth/                       （T03 已交付）
backend/src/goalflow/core/config.py              （不新增环境变量，见 E23）
frontend/                                        （生成物以外）
scripts/、.github/
```

### 不在本次范围内

- 真实作业种类及其处理函数（T08、T09、T11）。
- `model_calls` 表与模型用量统计（T08）。它在 03 第 6 节与作业表并列，但只有 T08 会写它。
- `audit_events` 表：归属仍未决，见第 8 节。
- LangGraph 图、LangChain 模型调用、逐 token 流式事件（T08）。
- Docker Compose、Nginx 的 SSE 代理配置（`proxy_buffering off`）、Worker 与 Beat 的部署编排（T13）。
- 前端作业进度展示（T10）。
- 已结束作业与 `job_events` 的清理策略：见第 8 节。
- 按用户或按作业的模型调用预算（预算金额按 06 第 6 节继续暂缓）。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| `docs/product/PRD.md` | Q01 数据隔离（修改作业 ID 不能访问他人内容）；Q02；Q03 生成可恢复 | 已确认 |
| [06-delivery-plan.md](../development/06-delivery-plan.md) 第 2 节 T07 行 | outbox、领取租约、有限重试、取消和持久化事件；重复投递 / 旧 Worker 晚返回不重复提交 | 已确认（任务拆解） |
| 06 第 4 节 | 等待用户不占 Worker、重启可恢复、恢复后重新检查版本、原子确认和取消竞争，由数据库业务状态与 jobs 表保证，不依赖 checkpointer | 已确认 |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 5 节 | 写操作携带 `Idempotency-Key`，相同 key 不同内容返回冲突；异步操作返回 202 与 `job_id`，重复异步请求返回原作业、不得创建第二个；`/api/auth/*` 为唯一例外 | 已确认 |
| [03-data-model.md](../development/03-data-model.md) 第 6 节 | jobs、job_events、outbox_events、idempotency_requests 的字段与约束 | 建议（字段与状态为设计建议），由本卡 E4—E11 收敛 |
| 03 第 7 节 | 短写事务 + 条件 UPDATE + 影响行数判定；租约式领取（`SKIP LOCKED` 不可用）；旧租约提交影响 0 行；取消与成功提交条件更新竞争 | 已确认（T01 实测） |
| [02-backend-proposal.md](../development/02-backend-proposal.md) 第 67 段 | Redis 只做队列传递，不作为唯一业务记录；投递成功但标记失败可能重复发送，由作业 ID、状态与唯一约束防重复提交；区分业务提交幂等与供应商请求幂等 | 已确认（总体方案） |
| [04-agent-workflows.md](../development/04-agent-workflows.md) 第 1 节 | 作业状态 queued → running → succeeded / failed / cancelled / stale，可重试错误经 retry_wait 回 queued | 建议，由 E9 收敛 |
| 04 第 7 节 | SSE 持久化事件 queued、started、stage_completed、awaiting_confirmation、completed、failed；job_id + sequence 补读；框架事件不透传；连接终止不取消作业；查询与订阅校验 owner_id | 建议，由 E19、E20 收敛 |
| 04 第 6 节 | chat model 的 `max_retries` 必须显式设置，与作业重试共用同一调用预算 | 建议；T07 给出作业侧上限（E13），模型侧由 T08 落实 |
| [05-module-contracts.md](../development/05-module-contracts.md) | inspect_job、watch_job（Last-Event-ID）、cancel_job（expected_revision，不能撤销已成功发布的结果） | 建议契约，由 PR-3 落地为 OpenAPI |
| [T02 交接卡](T02-engineering-foundation.md) 决策 A4 | 幂等与版本只交付了契约形状，存储与执行属 T03 / T07 | 已确认 |
| [T03 交接卡](T03-auth-session.md) 第 8 节 | 幂等存储归 T07，须在 T04 第一个版本化写接口之前落地 | **已确认**（仓库负责人 2026-09-23） |
| [T16 交接卡](T16-data-layer-foundation.md) | `Database.write()` / `read()` 是唯一的事务入口；写事务里不发网络请求 | 已确认 |

## 4. 决策与假设

本节全部由仓库负责人于 2026-09-23 确认。标为"契约"的项落在契约提交里，并回写 03 / 04。E1 偏离 AGENTS.md 红线 1，与 T03 C1 同理，本次确认**只适用于 T07 的三个 PR**。

### 流程与结构

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| E1 | PR 切分 | 拆三个 PR，依次合并：**PR-1 幂等存储**（迁移 0002 + `idempotency/`）；**PR-2 作业核心**（迁移 0003 + 提交、领取、续租、完成、取消、重试、恢复扫描、outbox 分发、Celery 装配）；**PR-3 作业接口**（inspect / watch / cancel 路由与 OpenAPI 生成）。每个 PR 打 `contract-change` 标签，契约提交在前 | 整包估计远超 02-ai-collaboration 第 3 节的 400 行上限。PR-1 单独先合，T04 就不用等作业核心。每个 PR 仍把契约与实现放在一起，理由同 T03 C1：迁移不配 ORM 模型过不了 `test_models_match_migrations`，路由不配实现只能是桩。**这偏离 AGENTS.md 红线 1**，T03 C1 写明"只适用于 T03"，因此这里要重新确认 | 工程流程 | 已确认 |
| E2 | 模块位置 | 幂等存储放新包 `goalflow/idempotency/`，作业放 `goalflow/jobs/`。两者都按业务模块处理（01 第 3 节：名单外的新包默认是业务模块） | 幂等是所有写接口共用的，放进 `jobs/` 会让 T04 的同步写接口去依赖作业模块。其他模块只 import 包入口暴露的 Interface，不 import 内部文件（01 第 8 节） | 全部后续写接口 | 已确认 |
| E3 | 本包不建的表 | 不建 `model_calls`（归 T08），不建 `audit_events`（归属未决） | 03 第 6 节把它们与作业表列在一起，但 T07 不会写它们；先建表等于替别的模块定下 schema，和 T03 C15 不建 `audit_events` 的理由相同 | 数据模型 | 已确认 |

### 幂等存储（PR-1）

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| E4 | 唯一范围（契约） | 唯一约束建在 `(owner_id, request_key)` 上，`operation` 作为普通列保存。同一个 key 被用在另一个操作上，同样返回 `IDEMPOTENCY_KEY_CONFLICT` | 03 只列了字段，没写约束。如果按 `(owner_id, operation, request_key)` 建唯一约束，客户端把同一个 key 误用在两个操作上时会被当作两次独立请求执行，而这种误用本应被发现 | 契约、前端生成 key 的方式 | 已确认 |
| E5 | 请求摘要 | `request_hash` 为 SHA-256，输入是规范化 JSON：`{operation, 路径参数, 校验后的请求体 model_dump(mode="json")}`，按键排序，不含空白 | 对校验后的模型取摘要，而不是对原始字节：客户端重试时空白或键顺序不同，不应被判成"不同内容" | 全部写接口 | 已确认 |
| E6 | 与业务写入的关系 | 去重记录与业务结果写在**同一个写事务**里。Interface 形如 `run_idempotent(session, request, execute)`：先查记录，摘要一致就重放，不一致就报冲突，不存在就执行 `execute` 并插入记录。业务失败时整个事务回滚、不留记录，同一个 key 可以重试。路由用一个依赖把 key、操作名和摘要打包成 `IdempotentRequest`，传给模块 Interface | `write()` 是 `BEGIN IMMEDIATE`，同一个 key 的并发请求在库级写锁上串行：第二个请求拿到锁时，第一个已经提交，它直接看到记录并重放。因此**不需要"处理中"状态**，也不存在两个请求同时执行的窗口。代价：业务写入必须在一个写事务内完成；需要跨事务的操作（中间要调模型）应改为提交作业，由作业去重兜底 | 全部写接口的实现方式 | 已确认 |
| E7 | 重放内容（契约） | 保存 `result_ref`（JSON，如 `{"type": "goal", "id": "..."}` 或 `{"type": "job", "id": "..."}`）和 `response_status`。重放时按引用重新读取资源的**当前状态**，并沿用原来的状态码 | 另一种做法是保存完整响应快照，重放时逐字节一致。放弃它的原因：快照会在库里多存一份用户私人内容；响应模型一变，旧快照就过时了。代价：重放拿到的可能是更新后的 revision，但对"网络断了再试一次"的客户端来说，拿到当前状态反而更有用。03 的字段需要补 `response_status`、`created_at` | 契约、数据模型 | 已确认 |
| E8 | 保留期 | 记录保留 7 天，由 Beat 每日清理；过期之后，同一个 key 按新请求处理 | 03 没有规定。前端每个意图生成一次 UUID，过期后撞 key 的概率可以忽略。7 天足以覆盖"隔天回来重试"。个人工具写入量小，存储不是问题 | 运维 | 已确认 |

### 作业（PR-2）

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| E9 | 状态集（契约） | 采用 04 第 1 节：`queued`、`running`、`retry_wait`、`succeeded`、`failed`、`cancelled`、`stale`，写入 `contracts/enums.py` 的 `JobStatus`，建表时加 CHECK。终态为后四个，终态不可再变 | 04 为建议，这里原样采纳，不另起名字。回写 04 为已确认 | 契约、前端 | 已确认 |
| E10 | 作业去重（契约） | `dedupe_key` 非空，由提交方按输入计算（例如"日期 + planning_revision"）。唯一约束改为**部分唯一索引**：`(owner_id, kind, dedupe_key) WHERE status IN ('queued','running','retry_wait','succeeded')`。提交时如果已有命中的作业，就返回它（202 + 原 `job_id`） | 03 写的是全表唯一，那样一个 failed 作业会永久占住它的去重键，用户在同一输入上无法重试。把 failed、cancelled、stale 排除在外之后，"相同输入只对应一个**有效**作业"（04 第 8 节）仍然成立。部分唯一索引已由 T01 验证可用。回写 03 | 契约、数据模型、全部提交方 | 已确认 |
| E11 | jobs 补充字段（契约） | 在 03 的字段之外补 `revision`（cancel_job 的 expected_revision 需要它）、`next_attempt_at`（retry_wait 的到期时间）、`created_at`、`updated_at`、`finished_at`、`error_message`（面向用户的说明，不含凭证与他人数据） | 03 缺 `revision`，而 05 的 cancel_job 要求 expected_revision；T03 C18 同样是"现在就加 revision，免得以后重建表"。回写 03 | 数据模型 | 已确认 |
| E12 | 租约与续租 | 租约 60 秒。Worker 在后台线程里每 20 秒续租一次，每次续租是一个单独的短写事务，条件为 `lease_token` 相同且 `status = 'running'`。续租影响 0 行时置位"租约已丢失"，处理函数在下一个检查点中止，并且不再尝试提交 | 一次模型调用就可能超过 60 秒，只在阶段边界续租不够，因此放在后台线程里。60 秒决定了"Worker 被杀"之后恢复的最长延迟 | Worker 行为、恢复延迟 | 已确认 |
| E13 | 重试上限 | 每个作业最多 3 次尝试，`attempts` 在领取时加 1。两次重试之间的退避为 30 秒、120 秒。只有处理函数抛出"可重试"错误（模型超时、`MODEL_UNAVAILABLE`、重试后仍 `SQLITE_BUSY`）才进入 `retry_wait`，其余直接 `failed` 并保留 `error_code`。租约过期算一次失败的尝试 | 04 第 6 节要求作业重试与模型 `max_retries` 共用同一预算，否则次数相乘放大。T07 先定作业侧上限，T08 设模型侧的 `max_retries` 时必须以它为准。已知限制：失败的尝试可能已经产生模型费用（02 第 67 段） | T08 的模型配置 | 已确认 |
| E14 | 提交协议 | 处理函数在事务外完成计算后，返回一个 `commit(session)` 回调。作业模块开写事务：先执行 `UPDATE jobs SET status='succeeded' ... WHERE id=? AND lease_token=? AND status='running' AND cancel_requested_at IS NULL`，影响 1 行才调用 `commit(session)` 写业务结果，并在同一事务里写 `completed` 事件；影响 0 行则回滚，业务结果不落库。`commit` 内发现输入版本已变时抛 `InputStale`，作业转为 `stale`，同样不写业务结果 | 这就是 03 第 7 节"旧租约提交影响 0 行"和"取消与成功提交条件更新竞争"的落地形状：业务结果和作业状态在同一个事务里，要么都生效，要么都不生效。业务模块只在自己的 `commit` 里写自己的表，不越过模块边界 | T08、T09、T11 的处理函数写法 | 已确认 |
| E15 | 取消 | `queued`、`retry_wait` 直接转为 `cancelled`。`running` 只设 `cancel_requested_at`，由 Worker 在检查点和提交时观察到后转为 `cancelled`。已处于终态的作业，取消请求返回当前状态，不报错。`expected_revision` 不符时返回 `REVISION_CONFLICT` | 05 要求"不能撤销已成功发布的结果"。对终态作业做取消是幂等的无操作，不当成错误——客户端在"已经成功了"的时间点点击取消很常见 | 契约行为、前端 | 已确认 |
| E16 | 分发 | outbox 行与作业同事务写入。API 提交后立即尽力投递一次，成功后把 outbox 标记为已发送；失败的由 Beat 每 10 秒扫描补投，每行按指数退避。Celery 消息只带 `job_id`。**使用 Celery 默认的提前确认**，不开 `acks_late` | 只靠 Beat 扫描会让交互式作业多等最多 10 秒。不开 `acks_late` 的原因：Worker 崩溃后的恢复统一由数据库租约过期加恢复扫描负责，只留一条恢复路径；开了 `acks_late` 会多出一条由 broker 重投的路径，并且要和 Redis 的 `visibility_timeout` 配合，两条路径叠加更难推理 | 部署、恢复延迟 | 已确认 |
| E17 | 恢复扫描 | Beat 每 30 秒执行一次：`running` 且租约已过期的 → 次数未用尽转 `retry_wait`，否则转 `failed`；`retry_wait` 已到期的 → 转回 `queued` 并写新的 outbox 行；`queued` 超过 5 分钟且没有待发 outbox 的 → 补写 outbox（覆盖 Redis 重启导致消息丢失）。每一步都是条件更新 | 条件更新让扫描天然幂等：误开两个 Beat，或者两次扫描重叠，都不会重复推进状态 | 部署 | 已确认 |
| E18 | 作业种类 | `kind` 为字符串列，**不加 CHECK，不进共享枚举**。各业务模块在自己的包里注册处理函数，API 响应里的 `kind` 为普通字符串 | 如果进枚举或加 CHECK，每新增一个作业种类都要改契约和迁移。前端不需要按 kind 分支，它知道自己提交的是什么。未注册的 kind 在提交时直接报错，不会写入库 | T08、T09、T11 | 已确认 |

### 实现中补充的决策

E25、E26 在单个工作包内部，按决策规程由实现者自定，随 PR-1 评审。

| 编号 | 决策 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| E25 | E7 的 `result_ref` 落成两列 `result_type`（≤32）、`result_id`（≤36），不存 JSON | 03 第 1 节："核心关联、日期、状态保持独立字段"，JSON 只用于领域扩展与快照。结果引用是核心关联。另一个原因：SQLite 里声明类型为 `JSON` 的列是 NUMERIC 亲和性，要么另写一个 JSON 列类型放进 T16 的 `db/types.py`，要么存成裸 TEXT，两者都不如两列清楚 | 数据模型（已回写 03） | 否 |
| E26 | E6 的"路由用依赖打包 `IdempotentRequest`"改为：路由调用 `IdempotentRequest.build(owner_id=..., operation=..., key=..., body=..., path_params=...)`。key 仍由既有的 `require_idempotency_key` 依赖取得 | 摘要要基于**校验后的**请求模型（E5），而 FastAPI 依赖拿不到路由参数里已校验的请求体；硬做成依赖就得重复解析请求体。结果是 `api/` 在 PR-1 只改了一处 docstring，OpenAPI 不变 | 后续全部写路由的写法，样板见 `tests/idempotency/test_idempotency_http.py` | 否 |

### 事件与接口（PR-2、PR-3）

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| E19 | 事件类型（契约） | 在 04 的六种之外补 `cancelled`、`stale`、`retrying`，写入 `JobEventType`。事件与对应的状态变化写在同一个事务里；`sequence` 在单个作业内从 1 起连续递增。`stage_completed` 由处理函数通过作业上下文发布，payload 只有 `{"stage": "<名称>"}` | 04 的清单缺少取消和过期两种终态，SSE 订阅方就无法得知流在什么时候结束。事件与状态同事务写入，才不会出现"状态已成功、事件没写上"。回写 04 | 契约、前端 | 已确认 |
| E20 | SSE 实现 | 服务端每 500 毫秒轮询一次 `job_events`（SQLite 没有 LISTEN/NOTIFY），`id` 字段为 `sequence`，按 `Last-Event-ID` 补读。每 15 秒发一次注释行作为心跳。发出终态事件后关闭流；单条连接最长 5 分钟，之后由客户端重连。连接断开不影响作业 | WAL 下读不阻塞写，轮询代价很小。5 分钟上限是为了不让一条连接无限占用 API 进程，EventSource 会自动重连并带上 `Last-Event-ID` | 契约、前端、T13 的 Nginx 配置 | 已确认 |
| E21 | 越权 | 查询、订阅、取消他人的作业，一律返回 `NOT_FOUND`，与作业不存在无法区分。所有作业查询都带 `owner_id` 条件 | 返回 `FORBIDDEN` 会泄露"这个 ID 存在"（Q01） | 契约行为 | 已确认 |

### 实现约束

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| E22 | 时钟 | 全部时间用 UTC 应用时钟，可以注入；测试靠推进时钟，不 sleep | 沿用 T03。API、Worker、Beat 按 RFC 0003 运行在同一台主机上，应用时钟一致，不需要用数据库时间 | 测试 | 已确认 |
| E23 | 配置 | 不新增环境变量。租约、续租间隔、重试次数、退避、扫描间隔都写成模块常量。Redis 地址沿用已有的 `GOALFLOW_REDIS_URL` | 沿用 T16 B3：环境变量属于公共契约，目前没有按环境调整的需求 | 部署 | 已确认 |
| E24 | 测试与 Redis | 协议测试直接调用 `run_job(job_id)`，用多线程或多进程模拟并发 Worker 和进程被杀，不依赖 Redis。Celery 装配用 kombu 的 `memory://` 传输做一条集成用例。CI 不加 Redis 服务，真实 Redis 的冒烟测试留给 T13 的 Compose 环境 | 所有正确性保证都在数据库一侧，Redis 只负责传递消息，用真实 Redis 测不出额外的协议问题。给 CI 加服务需要改 `.github/`，那是集成负责人的路径。代价：Redis 连接配置和序列化问题要到 T13 才能暴露，已记入第 8 节 | CI、T13 | 已确认 |

## 5. 验收场景

每条对应一个测试。数据库相关的一律使用真实库文件、WAL 模式和 T16 的连接装配。

幂等（PR-1）：

测试文件在 `backend/tests/idempotency/`。"业务执行了几次"一律看测试专用表 `probe_effects` 的行数，而不是看 `run_idempotent` 的返回值。

- [x] 相同 key、相同内容重复提交，业务只执行一次，第二次返回原结果和原状态码 —— `test_idempotency_service.py::test_same_key_and_content_executes_once_and_replays`
- [x] 相同 key、不同内容（包括换一个操作、换路径参数）返回 `IDEMPOTENCY_KEY_CONFLICT`，错误体符合统一结构且 details 为空 —— `test_same_key_with_different_content_conflicts`、`test_same_key_on_another_operation_conflicts`、`test_idempotency_http.py::test_reused_key_with_different_content_returns_the_error_contract`
- [x] 相同 key 并发提交 8 次，业务只执行一次，其余全部重放同一结果 —— `test_concurrent_submissions_with_one_key_execute_once`
- [x] 业务失败导致回滚后，不留去重记录，同一个 key 重试可以成功 —— `test_failed_business_write_leaves_no_record_and_the_key_can_be_retried`
- [x] 请求体只有空白、键顺序不同或省略了默认值时，判为相同内容 —— `test_idempotency_http.py::test_retry_with_reformatted_body_replays_the_original_result`、`test_request_hash_ignores_key_order_and_omitted_defaults`
- [x] 用户 B 使用与用户 A 相同的 key，互不影响 —— `test_keys_are_scoped_per_owner`、`test_idempotency_http.py::test_another_user_with_the_same_key_gets_their_own_result`
- [x] 保留期内重放、到期即按新请求执行（不依赖清理有没有跑过）；清理只删过期记录 —— `test_record_deduplicates_until_the_retention_period_ends`、`test_purge_removes_only_expired_records`
- [x] 只记录 2xx；缺少 key 的写请求在任何写入之前被拒 —— `test_only_successful_responses_are_recorded`、`test_idempotency_http.py::test_missing_key_is_rejected_before_any_write`
- [x] 迁移 0002 升级 → 降级 → 再升级结果一致；ORM 元数据与迁移后的反射结果一致（含 CHECK） —— `tests/db/test_models_match_migrations.py`（已补 import）

以下改动做过变异检查：把被保护的代码临时改坏，对应测试确实变红，恢复后变绿。

| 临时改动 | 变红的测试 |
| --- | --- |
| 不查已有记录，每次都执行业务 | 8 条，含并发、重放、冲突、过期 |
| 过期判断恒为"未过期" | `test_record_deduplicates_until_the_retention_period_ends` |
| 删除过期记录后不 flush | 同上（撞唯一约束，见第 9 节第 6 条） |

另做过一条"去掉 operation 比对"的变异，没有任何测试变红——operation 已经算进摘要，那段比对是死代码，已删除。

作业（PR-2）：

- [ ] 提交在同一事务内写入作业、outbox 和 `queued` 事件；事务回滚时三者都不存在
- [ ] 相同 `dedupe_key` 并发提交 8 次，只产生一个作业；failed、cancelled、stale 之后再提交会产生新作业
- [ ] outbox 投递失败后由扫描补投；同一作业投递两次，只有一次领取成功
- [ ] 两个 Worker 同时领取同一作业，只有一个成功
- [ ] 旧 Worker 租约过期、新尝试开始之后，旧 Worker 晚返回，其提交影响 0 行，最终结果由新尝试决定，业务结果只有一份
- [ ] Worker 进程在运行中被杀：租约过期后被扫描回收并重试，最终只发布一个结果
- [ ] 可重试错误进入 `retry_wait`，按退避时间重试；3 次用尽转 `failed` 并保留 `error_code`；不可重试错误直接 `failed`
- [ ] 取消与成功提交并发竞争，两者只有一个生效；取消生效后不发布业务结果
- [ ] 提交时输入版本已变：作业转 `stale`，不写业务结果
- [ ] 恢复扫描并发执行两次，状态推进不重复
- [ ] 同一作业的事件 `sequence` 连续且唯一；每个状态变化都有对应事件
- [ ] 迁移 0002、0003 升级 → 降级 → 再升级结果一致；ORM 元数据与迁移后的反射结果一致（含部分唯一索引与 CHECK）

接口（PR-3）：

- [ ] 异步提交返回 202 与 `job_id`；重复提交返回原 `job_id`
- [ ] SSE 按 `Last-Event-ID` 补读漏掉的事件；终态后流关闭；断开连接后作业照常完成，事后查询得到最终状态
- [ ] 已成功的作业再取消，返回当前状态且结果保留；`expected_revision` 过期返回 `REVISION_CONFLICT`
- [ ] 数据隔离：用户 B 查询、订阅、取消用户 A 的作业，均返回 `NOT_FOUND`，响应不含 A 的任何数据
- [ ] 错误详情与日志中不出现 Cookie、令牌或模型凭证

## 6. 进展

- 已完成：交接卡第 1—5 节；第 4 节 E1—E24 已确认。PR-1 幂等存储：迁移 0002、`goalflow/idempotency/`（`run_idempotent`、`IdempotentRequest`、`purge_expired`）、`tests/idempotency/` 15 条用例，03 第 6 节 idempotency_requests 行已回写。
- 进行中：PR-1 待提交与评审。
- 未开始：PR-2 作业核心（含 Beat 每日调用 `purge_expired`）、PR-3 作业接口。

## 7. 验证结果

PR-1，于 2026-09-23 在 Windows（Git Bash）本地执行。数据库用例全部使用真实迁移建出的库文件、WAL 与 `create_database_engine()` 的连接装配，与生产同构。

```text
$ bash scripts/check.sh
==> 后端检查
All checks passed!
Success: no issues found in 34 source files
==> 前端检查
==> 契约漂移检查
==> 凭证粗筛
==> 结果
检查通过

$ bash scripts/test.sh
==> 后端测试（all）
206 passed, 1 warning in 17.87s
==> 前端测试（all）
 Test Files  3 passed (3)
      Tests  16 passed (16)
==> 结果
测试通过

$ uv run --project backend pytest backend/tests/idempotency backend/tests/db/test_models_match_migrations.py -o addopts=""
============================= 18 passed in 2.44s ==============================
```

那 1 条 warning 是 starlette 测试客户端对 anyio 别名的弃用提示，与本次改动无关。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| `audit_events` 表归谁建、字段是否要兼顾账号事件（沿自 T03 第 8 节） | 08 要求账号操作写审计；T09、T12 的计划变更也要写 | 数据负责人与集成负责人 |
| 已结束作业、`job_events` 与已发送 outbox 行的保留和清理 | 不清理时库会随使用时长持续增长；清理需要考虑用户查看历史的需求 | 集成负责人 |
| 真实 Redis 的冒烟测试放在哪里（E24） | Redis 连接和序列化问题要到 T13 才能暴露 | 集成负责人 |
| Nginx 代理 SSE 需要关闭缓冲并调大读超时（E20） | 不配置时事件会被缓冲，前端看不到进度 | T13 |
| T08 设置模型 `max_retries` 时必须与 E13 共用同一预算 | 否则重试次数相乘，模型费用放大 | Agent 负责人（T08） |

## 9. 给接手者

1. **作业状态与业务结果必须在同一个写事务里提交**（E14）。先提交业务结果、再更新作业状态，或者反过来，都会在两步之间留下"结果已写、作业还是 running"的窗口，旧 Worker 和取消请求都会从这个窗口钻进来。
2. **写事务里不要调模型，也不要投递 Celery 消息。** 投递放在事务提交之后，失败了由 outbox 补投——这正是 outbox 存在的原因。
3. **不要用 Celery task ID 当作业身份**（03 第 6 节）。作业身份是 `jobs.id`，Celery 消息只是提醒 Worker 去领取。
4. **幂等不需要"处理中"状态**（E6）。它依赖 `BEGIN IMMEDIATE` 的库级写锁；如果以后有人把业务写入拆成多个事务，这条保证就没了，应改为提交作业。
5. **写接口接入幂等的样板是 `tests/idempotency/test_idempotency_http.py` 的探针路由**：`CurrentUserDep` 取身份，`require_idempotency_key` 取 key，`IdempotentRequest.build(...)` 基于校验后的请求体算摘要，`database.write()` 里调 `run_idempotent`，首次执行和重放都按 `outcome.result.id` 读当前状态再返回。
6. **`run_idempotent` 里删除过期记录后必须先 flush。** SQLAlchemy 的工作单元对同一张表默认先 INSERT 后 DELETE，不 flush 就会撞 `(owner_id, request_key)` 唯一约束。变异检查确认过这一条。
7. **测试文件名在整个 `backend/tests/` 下必须唯一。** 测试目录没有 `__init__.py`，`tests/idempotency/test_service.py` 会和 `tests/auth/test_service.py` 撞名，单独跑子目录能过、跑全量才报 import file mismatch。
8. 其余沿用 [T03 交接卡](T03-auth-session.md) 第 9 节：ORM 模型继承 `db.base.Base`，时间列用 `db.types.UtcDateTime`，迁移手写，并在 `test_models_match_migrations.py` 补 import；取当前用户一律用 `CurrentUserDep`。
