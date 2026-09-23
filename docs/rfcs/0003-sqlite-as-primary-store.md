# RFC 0003：首版业务数据库由 seekdb 改为 SQLite，向量能力由 sqlite-vec 承载

| 项 | 值 |
| --- | --- |
| 提案名称 | `sqlite_as_primary_store` |
| 提出日期 | 2026-09-22 |
| 提出人 | 产品决策人 |
| 状态 | 草案 |
| 影响范围 | 公共契约 / 数据模型 / 跨模块行为 |
| 关联 PR | #（待填） |

## 摘要

首版业务数据库由 OceanBase seekdb Server 改为 **SQLite 单文件库**，驱动为标准库 `sqlite3`，方言为 SQLAlchemy 的 `sqlite+pysqlite`。SQLAlchemy 2 + Alembic 保留，Celery + Redis + Beat 保留，模块划分与 Interface 不变。

未来的向量检索能力由 **sqlite-vec 可加载扩展**承载，全文检索由 SQLite 内置 FTS5 承载，取代原先"未来可评估 seekdb 自带的向量与全文检索"的表述。**首版仍不开启语义检索**，产品范围不变；本轮只验证扩展可加载并记录能力现状。

数据库从独立 Server 变成随应用进程打开的本地文件，因此新增一条部署硬约束：**API、Worker、Beat 必须运行在同一主机、访问同一本地文件系统上的同一个库文件，且该文件不得置于 NFS / SMB 等网络文件系统。**

## 动机

seekdb 这条路当前走不通，且不是通过"红线项测失败"发现的，而是在 T01 开工前的可得性调研阶段就断了：

1. **实例不可得。** T01 的全部用例都以"在真实 seekdb 实例上验证"为前提（见 [T01 交接卡](../worklog/T01-seekdb-verification.md)第 1 节）。调研结论是当前拿不到可用的 seekdb 实例，`02-backend-proposal.md` 第 59 段"当前仅完成官方文档核对，未运行数据库实例或兼容性测试"因此无法推进到下一步。
2. **M0 被无限期挂起。** `00-workflow.md` 第 8 节规定 M0 = T01 + T02，且"里程碑 M0 未完成前，不要开工依赖它的任务"。T03、T04、T07 全部依赖 T01。数据库不可得等于整条实现链停在门前。
3. **原计划留了这条出口。** T01 决策 A2 已写明"任一红线失败走 RFC 重新评估数据库选型"。本 RFC 就是那次重新评估，只是触发原因从"红线失败"换成了"实例不可得"，结论方向一致。

不做的后果：T01 无法完成，M0 无法达成，整个后端实现无法开工。

选 SQLite 而不是退回 PostgreSQL 的理由见"替代方案"。简短版本：本产品是**单机自部署的开源个人工具**（`02-backend-proposal.md` 第 18 段"初期可同机部署"、`docs/product/04-open-source-product.md`），SQLite 让"零外部依赖、零镜像拉取、Windows 开发机直接跑"成为可能，这对自部署用户和中国大陆可达性（PRD Q06）是净收益。

## 现状

| 来源 | 当前结论 | 状态 |
| --- | --- | --- |
| `AGENTS.md`"技术基线" | 数据库 seekdb | 已确认 |
| `AGENTS.md`"测试" | 数据库验收使用真实 seekdb，**不允许用 SQLite 通过的结果代替** | 已确认 |
| `docs/development/02-backend-proposal.md` 第 3、10 段 | 数据库指定为 OceanBase seekdb，替代最初建议的 PostgreSQL | 已确认 |
| `docs/development/02-backend-proposal.md` 第 55 段 | seekdb 为业务事实来源；未来可评估 seekdb 自带的向量与全文检索，首版不增加语义检索范围 | 已确认（向量部分为"未来可评估"） |
| `docs/development/02-backend-proposal.md` 第 57 段 | 独立 Server 模式，MySQL 协议，优先验证 MySQL 方言与 PyMySQL 驱动 | 建议 |
| `docs/development/03-data-model.md` 第 1、7、8 节 | CHAR(36)、DATE、DATETIME(6)、JSON 等物理类型须实测；行锁与条件更新方案待实测 | 建议 |
| `docs/worklog/T01-seekdb-verification.md` | 47 条用例全部针对 seekdb / MySQL 协议 | 未开始 |
| `docs/development/06-delivery-plan.md` 第 15、37 行 | T01 = seekdb 兼容性验证；M0 要求"明确数据库不兼容项" | 已确认 |

## 提案

### 数据库与驱动

业务事实来源是一个 SQLite 库文件，路径由配置项给出，默认落在部署目录下的数据卷里。API、Worker、Beat 三类进程各自用标准库 `sqlite3` 打开同一个文件；不再有数据库容器，`infra/` 下不再需要数据库镜像与初始化脚本。

每个连接建立时必须应用同一组 pragma，缺一条就会出现静默的行为差异：

| pragma | 值 | 为什么 |
| --- | --- | --- |
| `journal_mode` | `WAL` | 读不阻塞写、写不阻塞读；单 writer 仍然串行 |
| `foreign_keys` | `ON` | SQLite 默认**关闭**外键，且是连接级设置，漏设就等于没有外键 |
| `busy_timeout` | 显式毫秒值 | 默认立即返回 `SQLITE_BUSY`；写竞争必须有等待窗口 |
| `synchronous` | `NORMAL`（WAL 下） | 与 WAL 搭配的常规取值；具体值由 T01 按恢复要求确认 |

写事务一律以 `BEGIN IMMEDIATE` 开始，不用隐式 `BEGIN DEFERRED`。原因是 deferred 事务先取读锁、写第一行时才升级为写锁，两个事务同时升级会直接拿到不可重试的 `SQLITE_BUSY`（升级冲突不受 `busy_timeout` 保护）。`BEGIN IMMEDIATE` 把竞争提前到事务开头，变成可等待、可重试的情形。

### 并发协议

`03-data-model.md` 第 7 节原本写的是"按相同顺序获取 `user_planning_state`，**或者**使用实测支持的条件更新方案"。SQLite 没有 `SELECT ... FOR UPDATE`（已实测为语法错误），因此二选一收敛为后者：

**所有影响排期的写路径统一采用"短写事务 + 带 revision 条件的 UPDATE + 影响行数判定"**，不依赖行锁。`user_planning_state` 从"排队用的锁对象"变成"乐观并发的版本载体"：写事务里先按 `WHERE owner_id=? AND revision=?` 递增它，影响行数为 0 即判定 stale 并返回冲突。

这条协议本身不是新发明——`jobs` 表的 `lease_token` / `lease_until` 租约抢占（原 D7 替代实现）、`expected_revision` 校验都已经是这个形状。本 RFC 做的是把它从"备选方案"提为"唯一方案"，并删掉 `FOR UPDATE` / `SKIP LOCKED` 这两个分支。

### 唯一性与条件唯一

原 C4 设计的 `active_marker` 列（active 行写固定值、非 active 行写自身 id）**不再需要**：SQLite 支持部分唯一索引，可以直接写

```sql
CREATE UNIQUE INDEX ux_goal_one_active_profile
    ON goal_profiles (goal_id) WHERE status = 'active';
```

这比 `active_marker` 少一个需要应用层维护的冗余列，也少一类"marker 写错导致约束失效"的静默故障。数据模型里所有"一个目标最多一个当前执行版本"类不变量都改用部分唯一索引表达。

### 向量与全文检索

| 能力 | 承载方式 | 首版是否启用 |
| --- | --- | --- |
| 向量检索 | sqlite-vec 可加载扩展（`vec0` 虚拟表） | 否 |
| 全文检索 | SQLite 内置 FTS5 | 否 |

`AGENTS.md` 里"不使用 LangChain memory / retriever / vectorstore"继续有效——即使将来启用语义检索，向量表也由本仓库直接操作，不引入框架的 vectorstore 抽象。上下文组织方式不变：按目标档案、当前计划、近期记录与会话摘要组织（`02-backend-proposal.md` 第 55 段）。

### 部署形态

| 项 | 原（seekdb） | 新（SQLite） |
| --- | --- | --- |
| 数据库进程 | 独立容器，API/Worker 通过网络连接 | 无；库文件由应用进程直接打开 |
| 进程分布 | 可分主机（受 seekdb 可达性限制） | **必须同主机同本地文件系统** |
| 备份 | mysqldump 兼容或专有工具（待确认） | `VACUUM INTO` 生成一致快照，或 `sqlite3 .backup` |
| Windows 开发 | 必须容器跑数据库 | 直接跑，无外部依赖 |
| 中国大陆可达性（Q06） | 依赖镜像拉取与文档可达 | 无镜像；sqlite-vec 轮子可从常用国内镜像取得 |

**库文件不得置于 NFS / SMB 等网络文件系统**：SQLite 的锁依赖 POSIX 文件锁语义，网络文件系统上不可靠，会导致静默的数据损坏。这一条写进部署文档，属于硬约束而非建议。

## 技术细节

### 已在本机实测的行为

以下结果来自一次针对本 RFC 的探测运行，环境为 **Windows 10、CPython 3.14.5、SQLite 3.50.4**，脚本在仓库外的临时目录执行，不作为 T01 的交付物。**T01 必须在后端目标 Python（3.11+）与锁定的 SQLite 版本上用可重复运行的测试重测并记录版本号。**

| 关注点 | 原 T01 用例 | 实测结果 |
| --- | --- | --- |
| 复合唯一约束生效 | C1（红线） | 生效。报错为 `UNIQUE constraint failed: goal_profiles.goal_id, goal_profiles.version_no` |
| 唯一冲突能否区分具体键 | C2 | 能。`sqlite3.IntegrityError` 的消息直接列出冲突的列名，不需要先查后插 |
| 唯一索引中的多行 NULL | C3 | 均允许（NULL 互不相等） |
| 条件唯一 | C4 | **部分唯一索引可用**，`active_marker` 变通方案可以删除 |
| CHECK 约束 | C6 | 生效。报错为 `CHECK constraint failed: ...` |
| 条件更新影响行数语义 | D4 | `WHERE` 匹配即计 1，**即使新值等于旧值**；无匹配为 0。不存在 MySQL `CLIENT_FOUND_ROWS` 那种需要在连接参数里固定语义的陷阱 |
| 行锁 | D3 | `FOR UPDATE` 为语法错误，不支持 |
| `SKIP LOCKED` | D7 | 语法错误，不支持；维持租约式抢占 |
| DDL 是否事务性 | E1 | 是。`BEGIN` → `CREATE TABLE` → `ROLLBACK` 后表不存在 |
| 并发预算更新 | D8（红线） | 两线程 `BEGIN IMMEDIATE` 同时扣同一日 60 分钟容量，影响行数分别为 1 和 0，最终值 `remaining=0, revision=2`。**只有一个成功**，符合 PRD Q02 |
| JSON | B4 | `json()` / `json_extract()` 可用（JSON1 内置）；物理存储为 TEXT |
| 微秒时间戳 | B3 | ISO 8601 带微秒字符串原样往返，无精度丢失 |
| 业务日期比较 | B2 | `'2026-09-22' < '2026-09-23'` 为真；字符串日期可直接比较排序 |
| 类型亲和性 | B1 | TEXT 亲和列写入整数会被转成 text；类型靠亲和性而非严格校验，应用层必须保证写入类型一致 |
| sqlite-vec 可得性 | G3 | `sqlite_vec-0.1.9-py3-none-win_amd64.whl` 可从国内 PyPI 镜像取得（`pip install --dry-run` 实测）。**扩展实际加载与 `vec0` 查询未验证**，留给 T01 |

### 物理类型约定的变化

SQLite 只有 NULL / INTEGER / REAL / TEXT / BLOB 五种存储类，声明类型只影响亲和性。因此 `03-data-model.md` 第 1 节的物理类型约定改为：

| 逻辑类型 | 存储 | 说明 |
| --- | --- | --- |
| UUID | `TEXT`（36 字符规范形式） | 不做 BINARY(16) 优化；SQLite 没有 MySQL 那种索引字节上限，原 B6 用例整体消失 |
| 分钟数 | `INTEGER` | 不变 |
| 业务日期 | `TEXT`，`YYYY-MM-DD` | 可直接比较与排序；不用 INT(YYYYMMDD) |
| 事件时间 | `TEXT`，ISO 8601 UTC 带微秒 | 显示时按用户 IANA 时区转换，不变 |
| JSON | `TEXT`，写入前 `json()` 校验 | 不使用 PostgreSQL 专属 JSONB 类型或语法，这条原样保留 |
| 长文本 | `TEXT` | 上限由 `SQLITE_MAX_LENGTH` 决定（默认 10^9 字节），远超 20 万字符抽取文本的需求；T01 实测确认 |

由于类型靠亲和性而非严格校验，**"字段类型正确"这件事从数据库责任变成应用层责任**。SQLAlchemy 的类型层负责往返转换，Pydantic 负责入口校验，T01 的验证套件必须包含一条"写错类型不会被数据库拦住"的用例，把这个前提固定成已知事实而不是意外。

### 迁移工作流的变化

SQLite 的 `ALTER TABLE` 只支持有限操作（加列、改名、3.35+ 支持删列），改列类型、加约束都要重建表。因此 Alembic 必须启用 **batch 模式**（`render_as_batch=True`，迁移里用 `op.batch_alter_table()`），由 Alembic 生成"建新表 → 拷数据 → 删旧表 → 改名"的序列。

这是一条工程规范变化，不只是配置项：**每个迁移只做一件 DDL、每步可重入**这条原 E1 替代实现要求继续保留（虽然 SQLite 的 DDL 是事务性的，batch 模式的表重建仍然值得逐步做），并且**契约 PR 的 review 必须检查 batch 模式下的数据拷贝是否覆盖全部列**——漏列会静默丢数据。

### 对三条全局约束的影响

- **`expected_revision`**：正向收益。D4 的影响行数语义（匹配即计 1）让"幂等重提时新值等于旧值"不再被误判为版本冲突，原 T01 交接卡第 10 节列为"最隐蔽的一条"的风险消失。
- **幂等**：不变。`idempotency_requests` 与各表的 `client_request_key` 唯一约束照原设计工作，复合唯一约束已实测生效。
- **数据隔离**：不变。`owner_id` 显式归属条件查询与服务端校验不依赖数据库能力。

### 新引入的风险

原 T01 的红线是数据正确性四项（C1、D1、D8、F2），这四项在 SQLite 上风险显著下降（C1、D8 已实测通过）。但 SQLite 引入两类新风险，T01 必须正面验证：

1. **多进程并发写吞吐与 `SQLITE_BUSY`。** 写在库级别串行。API 请求、Celery Worker、Beat 扫描、outbox 分发器同时写时，必须验证在目标并发下不出现无法通过重试恢复的 busy 失败，并记录实测的写吞吐基线。这项应当成为新的红线。
2. **横向扩展被排除。** Worker 无法扩到第二台主机。当前部署形态本来就是单机（`02-backend-proposal.md` 第 18 段），所以不是新损失，但它把"以后加机器"从"换部署方式"变成了"换数据库"。这一条必须在部署文档里说清楚，而不是让后来的人以为可以平滑扩容。

## 替代方案

### 退回 PostgreSQL

`02-backend-proposal.md` 第 3 段记录了 PostgreSQL 是最初建议、被 seekdb 取代。它在技术上是最稳的选择：行锁、`SKIP LOCKED`、部分唯一索引、JSONB、事务 DDL、pgvector 全都有，`SKIP LOCKED` 还能让作业抢占比租约方案更简单。

没选它的理由是部署形态：本产品是单机自部署的开源个人工具，PostgreSQL 会给每个自部署用户增加一个必须拉取镜像、必须配置、必须备份的外部依赖，Windows 开发机也要跑容器。PRD Q06 已经把中国大陆的镜像拉取可达性列为待验证风险。SQLite 把这部分成本降到零。

**如果将来产品形态转向多用户托管服务，应当重新评估并走新的 RFC 换回 PostgreSQL。** 届时的迁移成本主要落在部分唯一索引以外的地方：本 RFC 选择的"条件更新 + 租约"并发协议在 PostgreSQL 上同样成立，不需要重写；物理类型约定需要重新收紧。

### 继续等 seekdb 可用

不做任何决策，等实例可得。代价是 M0 无限期挂起，T03/T04/T07 全部无法开工，而"什么时候可得"没有时间表。已排除。

### 用 SQLite 做开发库、seekdb 做生产库

双数据库路线。已排除，理由是 `AGENTS.md` 现行规则本身就指出了问题所在——"不允许用 SQLite 通过的结果代替"真实数据库验收。两套数据库意味着并发协议、类型约定、迁移写法都要同时满足两边的交集，测试要跑两遍，且生产上出现的问题在开发库上复现不出来。这比选定一个数据库的成本高得多。

### 向量用独立向量库（Chroma / Qdrant 等）

首版不开启语义检索，现在引入独立向量服务等于为一个未启用的功能增加一个外部依赖，与选 SQLite 的动机直接冲突。sqlite-vec 与业务库同文件、同备份、同事务边界，是与当前部署形态一致的选择。等真要做语义检索时如果 sqlite-vec 不够用，再走新 RFC。

## 影响与迁移

### 已有数据

**无。** 尚未生成任何迁移，`backend/migrations/` 不存在，没有任何数据库实例存在过（`02-backend-proposal.md` 第 59 段"未运行数据库实例"）。这是做这个决策成本最低的时刻。

### 受影响的工作包

| 工作包 | 影响 |
| --- | --- |
| T01 | **重写**。目标从"验证 seekdb 能否承载协议"改为"验证 SQLite 配置与并发协议"，用例清单按本 RFC 调整：删除 MySQL 协议相关项（A1 驱动、B6 索引长度、D4 语义固定），新增 SQLite 特有项（pragma 生效与持久性、batch 模式迁移、多进程写竞争、`VACUUM INTO` 备份、sqlite-vec 加载）。交接卡文件重命名 |
| T02 | 配置项与连接装配受影响（`backend/src/goalflow/core/config.py` 的数据库 URL 相关部分）。工程骨架本身不变 |
| T03、T04、T07 | 依赖不变（仍依赖 T01、T02），但 T01 结论表的内容变了，实现时读到的既定前提是"条件更新 + 租约"而不是"行锁或条件更新二选一" |
| T05 | 预算冲突实现按条件更新协议写，不再有行锁分支 |
| 前端 | 无影响。HTTP 契约不变，不需要重新生成类型 |

### 需要同步的文档

见"接受后的回写清单"。其中 `AGENTS.md`"测试"一节的规则要反向重写：原文是"数据库验收使用真实 seekdb，不允许用 SQLite 通过的结果代替"，新规则为**数据库验收必须使用与生产同构的 SQLite 配置（同一组 pragma、WAL、锁定的 SQLite 与 sqlite-vec 版本、真实文件而非 `:memory:`），不允许用默认配置的内存库通过的结果代替**。规则的用意没变——不能用一个更宽松的环境冒充验收环境。

## 未决问题

合并前必须解决：

- **SQLite 版本怎么锁定。** 标准库 `sqlite3` 用的是 Python 发行版自带的 SQLite，同一份 `uv.lock` 在不同机器上可能对应不同 SQLite 版本，而部分唯一索引（3.8.0+）、`VACUUM INTO`（3.27+）、`DROP COLUMN`（3.35+）、严格表（3.37+）各有版本门槛。需要定：是在应用启动时断言最低版本，还是改用 `pysqlite3-binary` 自带 SQLite。这直接决定 T01 的 A 类用例怎么写。
- **新红线的取舍。** 本 RFC 建议把"多进程并发写不产生不可恢复的 `SQLITE_BUSY`"提为红线。需要数据负责人确认，以及确认原四条红线（C1、D1、D8、F2）是否保留。

刻意排除在本 RFC 范围外：

- **是否启用严格表（`STRICT`）。** 它能把类型校验从应用层还给数据库，但要求 SQLite 3.37+，和上面的版本锁定问题绑在一起。属于 T01 可以在交接卡"决策与假设"里自行决定的实现细节，不需要 RFC。
- **具体的 `busy_timeout`、`synchronous`、WAL checkpoint 参数取值。** 按 T01 实测基线定，记入交接卡。
- **首版是否开启语义检索。** 本 RFC 明确不改产品范围。要开启属于改变产品行为，需产品决策人确认并走独立 RFC。
- **对象存储、Redis、Celery 的验证。** 仍属 T07。

需要自己的 RFC 的后续决策：

- 产品形态转向多用户托管时换回 PostgreSQL。
- 启用语义检索或全文检索作为产品能力。

## 接受后的回写清单

RFC 合并后必须完成，否则视为未完成：

- [ ] `AGENTS.md`：技术基线的数据库改为 SQLite；"测试"一节的 seekdb 验收规则按上文反向重写；仓库结构里 `infra/seekdb/` 相关表述调整
- [ ] `docs/development/02-backend-proposal.md`：第 3、10 段数据库选型；第 55 段事实来源与向量/全文检索承载方式；第 57—59 段整段改写为 SQLite 的接入与待验证项
- [ ] `docs/development/03-data-model.md`：第 1 节物理类型约定；第 7 节并发协议收敛为条件更新；第 8 节验证清单；条件唯一改用部分唯一索引
- [ ] `docs/development/06-delivery-plan.md`：T01 条目与 M0 门的表述
- [ ] `docs/development/00-development-planning.md`、`docs/development/04-agent-workflows.md`：seekdb 提及处
- [ ] `docs/engineering/00-workflow.md`、`01-contracts-and-ownership.md`、`03-code-and-test-standards.md`、`04-definition-of-done.md`：seekdb 提及处，以及 Alembic batch 模式带来的契约 PR review 要求
- [ ] `docs/worklog/T01-seekdb-verification.md` → 重命名并按本 RFC 重写用例清单；`docs/worklog/README.md`、`TEMPLATE.md`、`T02-engineering-foundation.md` 的引用同步
- [ ] `backend/README.md`、`.env.example`、`scripts/test.sh`、`.github/workflows/ci.yml`、`.github/ISSUE_TEMPLATE/1-bug-report.yml`、`.github/pull_request_template.md`、`CONTRIBUTING.md` 的 seekdb 提及处
- [ ] `docs/rfcs/README.md`："首批可能需要 RFC 的事项"里"seekdb 不兼容项的替代实现方案"一条，按本 RFC 结论改写或删除；索引状态由"草案"改为"已接受"
- [ ] 不涉及 `CONTEXT.md`：seekdb 不是领域术语，领域词表无变化
- [ ] 不涉及公共契约 PR：OpenAPI、共享枚举与错误码不变；`backend/migrations/` 尚不存在，无迁移需要改写
- [ ] 通知 T03、T04、T05、T07 负责人：并发协议已收敛为"条件更新 + 租约"，行锁分支取消
