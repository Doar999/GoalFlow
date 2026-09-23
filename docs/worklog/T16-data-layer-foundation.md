# T16 数据层地基

| 项 | 值 |
| --- | --- |
| 工作包 | T16（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | （待填，数据负责人） |
| 状态 | 进行中：连接装配与 Alembic 脚手架均已完成并通过，分两个 PR 待评审 |
| 更新日期 | 2026-09-23 |
| 相关 PR | #（待填） |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容。

## 1. 目标

把 [T01](T01-sqlite-verification.md) 验证出来的连接参数与并发协议，从验证套件搬成生产代码，并搭好 Alembic 的迁移脚手架。做完这一件，T03、T04、T07 才能各自建自己的表。

达成标准：业务模块拿到一个**只需要选读还是写**的入口，不需要知道 pragma、不需要自己拼 `BEGIN`、不需要 `create_engine`；第一条迁移可以直接写，不用再搭环境。

**本工作包不建任何业务表。** `users` 属于 T03，`goals` 属于 T04，各自在自己的契约 PR 里建。

## 2. 范围

### 可修改路径

```text
backend/src/goalflow/db/
backend/tests/db/
backend/migrations/                    （Alembic 脚手架，单独提契约 PR）
backend/src/goalflow/core/config.py    （仅 Settings 的 docstring）
docs/worklog/T16-data-layer-foundation.md
docs/worklog/README.md                 （仅索引表）
docs/development/06-delivery-plan.md   （仅新增 T16 行与受影响工作包的依赖列）
```

两处越出本工作包、但属于修正既有错误而非新增内容，已显式列出而不是默默改掉：
`docs/worklog/README.md` 的索引里 T01 状态仍写着"阻塞，等 RFC 0003 接受"（RFC 已接受、回写已合并），
且 T02 的行从未加入；`docs/worklog/T01-sqlite-verification.md` 的状态与第 7 节同样停在 RFC 未接受时的措辞。
两者都只改状态描述，不动任何结论。

### 明确不可修改

```text
backend/migrations/versions/           （业务表迁移归各自工作包）
backend/src/goalflow/contracts/
openapi/
frontend/
.env.example
```

`.env.example` 列在这里是因为**环境变量属于公共契约**（[01-contracts 第 2 节](../engineering/01-contracts-and-ownership.md)）。本工作包因此不新增任何环境变量，见决策 B3。

### 不在本次范围内

- 任何业务表与业务迁移。
- ORM 基类与模型定义。首张表由 T03 带，届时一并定 `DeclarativeBase` 的位置。
- FastAPI 的请求级会话依赖。由 T03 决定形态，见第 5 节未决问题。
- Celery Worker 的连接池参数。属于 T07。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [RFC 0003](../rfcs/0003-sqlite-as-primary-store.md) | 连接参数、并发协议收敛、部署硬约束 | 已接受 |
| [T01 交接卡](T01-sqlite-verification.md) | 结论表 A2、C5、D2、D3、D4、D6 | 已验证 |
| [03-data-model.md](../development/03-data-model.md) 第 7 节 | 事务与并发协议 | 已确认 |
| [01-contracts 第 2、6 节](../engineering/01-contracts-and-ownership.md) | 公共契约清单、迁移规程 | 已确认 |
| `.github/CODEOWNERS` | `/backend/src/goalflow/db/`、`/backend/migrations/` 归 `@data-owner` | 已确认（owner 仍是占位符） |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| B1 | **读写事务分离**：`write()` 用 `BEGIN IMMEDIATE`，`read()` 用 `BEGIN DEFERRED`。两者共享同一个连接池 | 修正 T01 验证套件的做法，见下文 | 全部数据访问代码 | 否 |
| B2 | 会话 `expire_on_commit=False` | 避免事务结束后读属性触发一次事务外的隐式刷新 | 全部数据访问代码 | 否 |
| B3 | 不新增环境变量。`busy_timeout`、`synchronous` 等取值写成模块常量 | 环境变量属公共契约，加一个就要单独走契约 PR；这些值目前没有按环境调整的需求 | 部署 | 否，将来要可配置时是 |
| B4 | 启动即拒绝：空连接串、非 SQLite 连接串、内存库 | 三者都会让"多进程共享同一个库文件"这个前提静默失效 | 全项目 | 否 |
| B5 | 运行时 SQLite 最低版本断言放在 `create_database_engine()` | 沿用 T01 决策 A1，但从测试挪到启动路径——让不满足的环境启动就失败，而不是第一条 SQL 才失败 | 全项目 | 否 |
| B6 | Alembic 脚手架单独提契约 PR，不与 `db/` 混在一起 | `backend/migrations/` 是契约路径，AGENTS.md 红线 1 | 工程流程 | 否 |
| B7 | `alembic.ini` 的注释一律写英文。中文说明放 `backend/migrations/README` | Alembic 用 configparser 按**平台默认编码**读 ini，中文 Windows 上不是 UTF-8，非 ASCII 会让每条 alembic 命令都 `UnicodeDecodeError`。这是实测踩到的，不是预防性规定 | 迁移工具链 | 否 |
| B8 | 迁移用顺序编号（`0001`、`0002`……），不用随机 rev id | 多分支同时新增迁移时，冲突直接表现为文件名撞车；随机 id 会各自挂在同一个 `down_revision` 上形成双 head，要到合并后才发现 | 工程流程 | 否 |

### B1 为什么要改 T01 的做法

T01 的 `conftest.py` 在 `begin` 事件里**无条件**发 `BEGIN IMMEDIATE`。测试里这样没问题，但直接搬进生产会让**每一个只读事务也去抢写锁**——读与读、读与写全部串行，WAL"读不阻塞写"的收益归零。

症状不会是报错，而是并发下吞吐塌掉，且日志里什么都看不出来。所以 `db/` 里事务开始模式改成通过 `execution_option` 传递，默认 IMMEDIATE，`read()` 显式降为 DEFERRED。

`backend/tests/db/test_session.py::test_read_does_not_take_the_write_lock` 专门盯这条。已验证它有牙：把 `read()` 换回 IMMEDIATE 后，该场景会等满 `busy_timeout` 再抛 `database is locked`。

## 5. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| 是否启用 STRICT 表（从 T01 第 9 节继承，仍未决） | 能把一部分类型校验还给数据库。但要求 SQLite ≥ 3.37（现断言 3.35），且**未验证** SQLAlchemy 能否声明 STRICT、Alembic batch 重建会不会把它丢掉 | 数据负责人 |
| FastAPI 的请求级会话依赖怎么给 | 现在只有上下文管理器。路由里是每请求一个会话，还是按读写分两个依赖，取决于 T03 的路由形态 | 后端负责人，在 T03 里定 |
| Celery Worker 的连接池参数 | Worker 进程数 × 池大小决定并发写的排队深度，直接关系到 T01 D9 那条红线在真实负载下是否还成立 | 后台负责人，在 T07 里定 |
| `busy_timeout` 是否要按环境可调 | 现在是常量 5000ms。要可调就得加环境变量，那是契约变更 | 数据负责人与集成负责人 |
| `DeclarativeBase` 放哪 | 放 `db/` 会让 `db/` 依赖业务模型；放各业务模块会有多个 Base。第一张表落地时必须定 | 数据负责人与后端负责人，在 T03 前定 |

### 一条与工具输出相反的实测结论

`alembic upgrade` 会打印 `Will assume non-transactional DDL`——那是 Alembic 对 SQLite 的默认判断。但本项目关掉了 pysqlite 的隐式事务管理，**失败的迁移实际会整体回滚**，不留半套表。

这条靠读日志判断不了（日志说的正好相反），所以写成了用例：`test_failing_migration_leaves_no_half_applied_schema` 造一条"先建表再抛异常"的迁移，断言异常确实发生在迁移体内、且表没留下。

即便如此，"一个迁移只做一件 DDL、每步可重入"这条规矩仍然保留——batch 模式的表重建步骤多，逐步可重入在排查失败迁移时依然值钱。

## 6. 给接手者

四件事，前两件搬错了不会报错：

1. **不要把 `read()` 改成 IMMEDIATE。** 见决策 B1。这是本工作包唯一一处刻意偏离 T01 验证套件的地方，偏离的理由写在那里。
2. **连接级 pragma 漏设无声。** `foreign_keys` 默认关闭、`busy_timeout` 默认为 0，都是连接级的、不写进库文件。`install_connection_hooks()` 负责每条新连接重设一次；如果将来有人绕过 `create_database_engine()` 自己建 Engine，这层保护就没了，而且不会有任何提示。
3. **写事务里不要调模型、不要发 HTTP。** 写锁是库级的，一个慢事务会把所有写路径堵住。模型调用一律在事务外，事务里只重新校验版本再落库——这是 `03-data-model.md` 第 7 节的要求，不是优化建议。
4. **`db/` 不要 import 业务模块。** 它被所有业务模块依赖，反向依赖会立刻变成循环。`contracts/` 已经有同样的约束，理由相同。
