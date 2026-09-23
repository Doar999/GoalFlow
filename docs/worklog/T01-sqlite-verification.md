# T01 SQLite 数据库行为验证

| 项 | 值 |
| --- | --- |
| 工作包 | T01（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | （待填，数据负责人） |
| 状态 | 已完成：39 条用例通过，[RFC 0003](../rfcs/0003-sqlite-as-primary-store.md) 已接受，设计文档回写已合并 |
| 更新日期 | 2026-09-23 |
| 相关 PR | #（待填） |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容。

## 1. 目标

验证 SQLite 在 [RFC 0003](../rfcs/0003-sqlite-as-primary-store.md) 规定的连接参数下，能否承载 [核心数据模型](../development/03-data-model.md) 所依赖的数据库行为。产出三样东西：一套可重复运行的验证套件、一张逐项结论表、以及每个不支持项所采用的替代实现。

达成标准：结论表无空项；红线项全部通过；所有"不支持/部分支持"项都已写明替代实现并回写到对应设计文档。M0 门以此为前提。

**本工作包不实现业务表，也不产出生产 DDL。** 它只回答"这个数据库能不能承载已设计的协议"。

验证的对象是**生产要用的那套配置**，不是 SQLite 的默认行为。这个区别在这里格外要紧：`foreign_keys`、`busy_timeout` 是连接级的、默认关闭或为 0，漏设不会报错，保护就静默消失。所以套件的连接装配（`backend/tests/db_compat/conftest.py`）本身就是被验证的一部分。

## 2. 范围

### 可修改路径

```text
backend/tests/db_compat/
backend/pyproject.toml                 （仅数据库驱动相关依赖增删）
backend/uv.lock                        （随依赖变化重新生成）
docs/development/03-data-model.md      （仅第 1、7、8 节的"需实测"结论回写）
docs/development/02-backend-proposal.md（仅第 3、10、55、57—59 段的验证结论回写）
docs/worklog/T01-sqlite-verification.md
.github/workflows/ci.yml               （仅数据库相关作业调整）
backend/src/goalflow/core/config.py    （仅 Settings 的 docstring，不改任何行为）
```

RFC 0003 接受后还需回写一批仅含措辞的文件，清单见该 RFC 的"接受后的回写清单"。那批改动属于 RFC 的回写义务，不属于本工作包的验证范围。

### 明确不可修改

```text
backend/migrations/
backend/src/               （例外：core/config.py 的 docstring，见上）
openapi/
frontend/
```

业务表结构与迁移属于 T02 之后的契约 PR，本次不动。生产连接装配属于后续创建 `backend/src/goalflow/db/` 的工作包——本次把连接参数固定在验证套件里，届时整体搬过去。

### 不在本次范围内

- 生产 DDL、索引调优、分区策略。
- 语义检索。**首版不开启**，产品范围不变；本次只验证 sqlite-vec 扩展可加载并记录能力现状（G3）。
- 性能阈值设定，只记录基线数值（见决策 A4）。
- 对象存储、Redis、Celery 的验证，那些属于 T07。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [RFC 0003](../rfcs/0003-sqlite-as-primary-store.md) | 数据库选型、连接参数、并发协议收敛、部署硬约束 | 已接受 |
| `docs/development/03-data-model.md` | 第 1 节物理类型约定、第 7 节事务与并发协议、第 8 节数据库行为验证 | 建议（字段与类型未验证） |
| `docs/development/02-backend-proposal.md` | 第 57—59 段数据库接入与待验证项 | 待随 RFC 0003 改写 |
| `docs/product/PRD.md` | Q02 重复操作不重复生效、Q03 生成可恢复、Q06 大陆环境验证 | 已确认 |
| `docs/engineering/00-workflow.md` | 第 8 节 M0 门 | 已确认 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| A1 | 不锁定具体 SQLite 发行版，也不引入 `pysqlite3-binary`；改为在套件里断言运行时 SQLite ≥ 3.35.0。门槛由用到的特性倒推：部分唯一索引 3.8.0、`VACUUM INTO` 3.27.0、`ALTER TABLE DROP COLUMN` 3.35.0 | 本工作包自定 | 全项目 | 否 |
| A2 | 红线为 C1、D1、D8、F2，**新增 D9**（多进程并发写不产生不可恢复的 `SQLITE_BUSY`）。任一失败走 RFC 重新评估数据库选型 | RFC 0003 提案 | 全项目 | 失败时是 |
| A3 | 验证套件长期保留，并随 `backend/tests` 在每次 PR 跑。**不再是 CI 可选作业**——SQLite 不需要外部服务，跳过它没有理由 | 本工作包自定（改变了原 T01 决策 A3） | 工程流程 | 否 |
| A4 | 记录性能基线但不设通过阈值 | 沿用原 T01 决策 A4 | 仅本工作包 | 否 |
| A5 | 条件唯一一律用部分唯一索引，`active_marker` 冗余列方案作废 | RFC 0003 | 跨模块 | 否，但需回写 |
| A6 | 库文件不得置于 NFS / SMB 等网络文件系统，写进部署文档作为硬约束而非建议 | RFC 0003 | 部署 | 否 |
| A7 | 写事务一律 `BEGIN IMMEDIATE`，不用隐式 `BEGIN DEFERRED`；pysqlite 的隐式事务管理必须关掉（`isolation_level=None`），改由应用自己发 BEGIN | 本工作包自定，依据 D3 实测 | 全部数据访问代码 | 否 |

## 5. 验证用例清单

每条用例都是可重复运行的自动化测试，文件在 `backend/tests/db_compat/`。

### A 连接与工具链

- [x] **A1** SQLAlchemy 2 的 `sqlite+pysqlite` 方言建连、查询、关闭正常；运行时 SQLite 版本满足最低要求
- [x] **A2** 区分库级与连接级 pragma：WAL 写进库文件，`foreign_keys` / `busy_timeout` 不写进文件
- [x] **A3** 连接池每新建一条连接都重设连接级 pragma
- [x] **A4** `inspect()` 反射表、列、索引、唯一约束、外键、主键，结果与建表语句一致

### B 类型与物理存储

- [x] **B1** TEXT 存 36 字符 UUID 并建唯一索引，等值查询正常
- [x] **B2** 业务日期用 TEXT `YYYY-MM-DD`，可直接比较与排序，无时区偏移
- [x] **B3** 事件时间以 ISO 8601 UTC 带微秒存取，精度不丢
- [x] **B4** JSON 存 TEXT，写入前 `json()` 校验，读取可 `json_extract` 查询；非法 JSON 被拒
- [x] **B5** 长文本可存下文档 10 规定的 20 万字符抽取文本
- [x] **B6** 类型亲和性**不拦截**写错的类型——这条固定的是一个"没有保护"的事实

### C 约束与唯一性

- [x] **C1（红线）** 复合唯一约束生效：`(goal_id, version_no)`、`(owner_id, kind, dedupe_key)` 等
- [x] **C2** 唯一冲突异常能区分是哪个键冲突的
- [x] **C3** 唯一索引中 NULL 互不相等，多行 NULL 都允许
- [x] **C4** 条件唯一用部分唯一索引直接表达
- [x] **C5** 外键生效，但**只在开了 `PRAGMA foreign_keys` 的连接上**生效
- [x] **C6** CHECK 约束生效

### D 事务与并发

- [x] **D1（红线）** 事务提交与回滚，回滚后无残留
- [x] **D2** 实测隔离行为：WAL 下读事务看到事务开始时的快照
- [x] **D3** `BEGIN IMMEDIATE` 与 `BEGIN DEFERRED` 的区别：后者升级写锁失败不可等待、不可重试
- [x] **D4** 条件更新的影响行数语义：匹配即计数，新值等于旧值也算
- [x] **D5** 约束冲突与锁等待超时是两类不同异常，可分别识别
- [x] **D6** `busy_timeout` 生效且可配置
- [x] **D7** `SKIP LOCKED` 不可用，租约式抢占替代
- [x] **D8（红线）** 并发预算更新：两连接同时扣同一日容量，只有一个成功（对应 Q02）
- [x] **D9（红线，新增）** 多进程并发写不产生无法通过重试恢复的 `SQLITE_BUSY`

### E DDL 与迁移

- [x] **E1** DDL 在事务里，回滚后不留残表
- [x] **E2** Alembic batch 模式加列，表重建后数据与列都不丢，唯一索引跟着活下来
- [x] **E3** batch 模式改列约束、删列，数据完整，downgrade 路径可用
- [x] **E4** 记录加列与加索引在有数据的表上的耗时。只记录，不设阈值

### F 备份与恢复

- [x] **F1** `VACUUM INTO` 在线生成一致快照；备份期间仍有写入时不会拿到半笔事务
- [x] **F2（红线）** 全量恢复演练：删掉原库连同 WAL/SHM，恢复后数据一致、应用能连上、能继续写

### G 环境与基线

- [x] **G1** 开发机直接跑，不需要数据库服务进程或容器
- [x] **G2** 记录性能基线。只记录，不设阈值
- [x] **G3** sqlite-vec 扩展可加载、`vec0` 虚拟表可建、近邻查询可跑；向量表与业务表同文件

## 6. 结论表

环境：Windows-10-10.0.19045-SP0，CPython 3.11.15，SQLite 3.50.4，sqlite-vec v0.1.9。

| 用例 | 结果 | 实测值 / 说明 | 采用的替代实现 | 需回写的文档 |
| --- | --- | --- | --- | --- |
| A1 | 支持 | 方言 `sqlite`；运行时 SQLite 3.50.4 ≥ 3.35.0 | 无 | 02 第 57 段 |
| A2 | 支持（有条件） | WAL 持久化到库文件；裸连接上 `foreign_keys` 读回 0 | 连接级 pragma 每条连接重设，由 `connect` 事件承担 | 02 第 57 段 |
| A3 | 支持 | 连续三次新建连接，pragma 均生效 | 无 | — |
| A4 | 支持 | 列、唯一约束、索引、外键、主键全部反射到位 | 无（Alembic autogenerate 可用） | 01-contracts 第 3 节 |
| B1 | 支持 | TEXT 存 36 字符 UUID，唯一冲突正常抛出 | 不做 BINARY(16)；原 MySQL 索引长度限制项整体消失 | 03 第 1 节 |
| B2 | 支持 | `'2026-09-23' < '2026-09-30' < '2026-10-01'` 排序与范围查询正确 | 不用 INT(YYYYMMDD) | 03 第 1 节 |
| B3 | 支持 | `2026-09-23T10:20:30.123456+00:00` 原样往返；相差 1 微秒可区分先后 | 无 | 03 第 1 节 |
| B4 | 支持 | `json_extract` 取标量与数组元素正常；`json('{not json')` 抛 OperationalError | 存 TEXT，写入前 `json()` 校验；不用 JSONB 语法 | 03 第 1 节 |
| B5 | 支持 | 20 万字符往返一致 | 无 | 03 第 1 节 |
| B6 | **不支持（预期内）** | TEXT 列写入整数 `20260923` → `typeof` 为 `text`；INTEGER 列写入 `'not-a-number'` → `typeof` 为 `text`，**均无异常** | 类型正确性交给 SQLAlchemy 类型层 + Pydantic 入口校验；是否启用 STRICT 表见第 9 节 | 03 第 1 节 |
| C1 | **支持（红线通过）** | `(goal_id, version_no)`、`(owner_id, kind, dedupe_key)` 均拦截重复；换一个 version_no 放行 | 无 | 03 第 8 节 |
| C2 | 支持 | 报错文本直接列出冲突列名，两个不同键的消息可区分 | 直接"插入，冲突则按已存在处理"，不需要先查后插 | 03 第 8 节 |
| C3 | 支持 | 同一唯一键上两行 NULL 均可插入 | dedupe_key 为空的作业不会互相挤掉 | 03 第 8 节 |
| C4 | **支持** | 部分唯一索引 `ON goal_profiles(goal_id) WHERE status='active'` 生效；非 active 行不受约束 | **`active_marker` 冗余列方案作废** | 03 第 2、3 节 |
| C5 | 支持（有条件） | 开 pragma 的连接拦截悬空引用；不开的连接静默写入，`PRAGMA foreign_key_check` 事后可查 | pragma 必须每条连接设；归属与无环校验仍在服务端 | 03 第 1 节、02 第 57 段 |
| C6 | 支持 | `CHECK constraint failed` 正常抛出 | 无（Pydantic 校验仍保留） | 03 第 1 节 |
| D1 | **支持（红线通过）** | 回滚后插入与更新均无残留 | 无 | 03 第 7 节 |
| D2 | 支持 | 写方提交后，已在事务中的读方仍读到旧值；结束事务后读到新值 | 事务协议的"事务中重新检查 revision"成立 | 03 第 7 节 |
| D3 | 部分支持 | DEFERRED 事务升级写锁抛 OperationalError；改 IMMEDIATE 后重试即成功 | **写事务一律 `BEGIN IMMEDIATE`**（决策 A7） | 03 第 7 节 |
| D4 | 支持 | 新值等于旧值时影响行数为 1；版本不匹配为 0 | 无。**MySQL 的 `CLIENT_FOUND_ROWS` 陷阱不存在**，不需要在连接参数里固定语义 | 03 第 7 节 |
| D5 | 支持 | 约束冲突为 `IntegrityError`，锁等待为 `OperationalError`，两者无继承关系 | 只有 OperationalError 进重试路径 | 03 第 7 节 |
| D6 | 支持 | 配置 400ms 时实测等待 ≥ 0.35s 后放弃 | 按实测值设置作业超时 | 03 第 7 节 |
| D7 | **不支持（预期内）** | `FOR UPDATE` / `SKIP LOCKED` 均为语法错误 | 租约式抢占：`UPDATE ... WHERE status='queued'`，靠影响行数判断；旧 Worker 凭过期 lease_token 提交影响 0 行 | 03 第 7 节 |
| D8 | **支持（红线通过）** | 两线程同时扣 60 分钟，影响行数分别为 1 和 0，最终 `remaining=0, revision=2` | 无 | 03 第 7 节 |
| D9 | **支持（红线通过）** | 4 个子进程 × 25 次写事务，全部提交成功，计数器终值 100，无 `UNRECOVERABLE_BUSY` | 带退避的有限重试，重试上限 10 次 | 02 第 59 段 |
| E1 | 支持 | `BEGIN` → `CREATE TABLE` → `ROLLBACK` 后表不存在 | 仍保留"一个迁移只做一件 DDL、每步可重入" | 01-contracts 第 3 节 |
| E2 | 支持 | `recreate="always"` 重建后 3 行数据与 5 列齐全，唯一索引存活 | **Alembic 必须启用 batch 模式** | 01-contracts 第 3 节 |
| E3 | 支持 | 可空 → 非空改完数据完整；加列后可删回 | 无 | 01-contracts 第 3 节 |
| E4 | 记录 | 5000 行表上加列 0.18ms，建索引 1.59ms | 无 | — |
| F1 | 支持 | 备份 `integrity_check` 为 ok；目标文件已存在时报错不覆盖；未提交事务不进快照 | 备份用 `VACUUM INTO`，**不直接拷库文件** | 02 第 59 段 |
| F2 | **支持（红线通过）** | 删库连同 WAL/SHM 后从备份恢复，数据逐行一致，SQLAlchemy 能连上并继续写入 | 无 | 02 第 59 段 |
| G1 | 支持 | Windows 10 上直接对文件跑通，无容器、无服务进程 | 无 | 02 第 18 段 |
| G2 | 记录 | 1000 行批量插入 2.23ms；带索引计数查询 0.07ms；单条条件更新 0.07ms | 无 | — |
| G3 | 支持 | sqlite-vec v0.1.9 加载成功；`vec0(embedding float[4])` 建表、写入、近邻查询正常；向量表与业务表同文件 | 无。**首版仍不开启语义检索** | 02 第 55 段 |

结论表填完后，把 `03-data-model.md` 第 1、7、8 节与 `02-backend-proposal.md` 第 3、10、55、57—59 段中标注"需实测""不预设"的表述替换为实际结论，状态由"建议"改为"已确认"。

## 7. 进展

- 已完成：全部 39 条用例编写并通过；依赖调整（移除 pymysql，dev 组加入 sqlite-vec）；结论表填写完毕；设计文档回写已随 RFC 0003 合并。
- 进行中：无。
- 未开始：无。生产连接装配由 [T16](T16-data-layer-foundation.md) 承接——本工作包只把连接参数固定在验证套件里。

## 8. 验证结果

全部结果来自在本工作包分支上真实执行的命令。

环境：Windows-10-10.0.19045-SP0，CPython 3.11.15，SQLite 3.50.4，sqlite-vec v0.1.9。

```text
$ uv run --project backend pytest backend/tests/db_compat
.......................................                                  [100%]
39 passed in 3.29s

$ bash scripts/check.sh
==> 结果
检查通过

$ bash scripts/test.sh
==> 后端测试（all）
91 passed, 1 warning in 11.12s
==> 前端测试（all）
 Test Files  3 passed (3)
      Tests  16 passed (16)
==> 结果
测试通过
```

基线数值通过 pytest 的 `record_property` 记录，用 `--junit-xml` 导出后读取：

```text
test_g1_runs_against_a_plain_file_on_this_platform
    platform = Windows-10-10.0.19045-SP0
    python = 3.11.15
    sqlite = 3.50.4
test_g2_performance_baseline
    insert_1000_rows_ms = 2.23
    indexed_count_query_ms = 0.07
    single_conditional_update_ms = 0.07
test_e4_ddl_timing_baseline
    add_column_on_5000_rows_ms = 0.18
    create_index_on_5000_rows_ms = 1.59
test_g3_extension_loads_on_this_platform
    sqlite_vec_version = v0.1.9
```

> 填写规则：只记录真实执行过的命令与真实输出摘要。**没跑过的不要写。**

## 9. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| 是否启用 STRICT 表 | B6 证明类型校验缺位，STRICT 能把一部分校验还给数据库。但它要求 SQLite ≥ 3.37（现有断言是 3.35），且**未验证 SQLAlchemy 能否声明 STRICT、Alembic batch 重建时会不会把它丢掉**。要定就得先补这个验证，不能靠推断 | 数据负责人 |
| 单条连接的写吞吐上限是否够用 | D9 证明多进程写是安全的，但只测了 4 进程 × 25 次。真实负载下 API + Worker + Beat + outbox 分发器的并发写规模还没有数 | 数据负责人与后台负责人，在 T07 前定 |
| 库文件的部署路径与备份周期 | A6 只定了"不能放网络文件系统"，没定放哪、多久备一次、备份留几份 | 集成与交付负责人 |
| [T02 交接卡](T02-engineering-foundation.md)第 9 节列的"等 RFC 0003 接受后回写 `backend/README.md`、`core/config.py`、`.env.example`"一条，**已由本工作包完成** | 该条已失效，留着会让下一个人重做一遍 | T02 负责人删除即可，本工作包按"不改别人工作包的交接卡"未代改 |

## 10. 给接手者

四件事最容易被跳过，但跳过就白做一轮：

1. **连接级 pragma 漏设不会报错。** `foreign_keys` 默认关闭、`busy_timeout` 默认为 0，都是连接级设置，不写进库文件。少设一条，外键就只是一句注释，锁等待就变成立即失败——两种情况都没有任何异常提示。C5 的用例特意同时测了"开"和"不开"两个方向，就是为了把这个陷阱固定成已知事实。生产连接装配搬到 `src/goalflow/db/` 时，这组 pragma 必须整体搬过去。

2. **写事务必须 `BEGIN IMMEDIATE`，而且要先关掉 pysqlite 的隐式事务管理。** pysqlite 默认在 DML 前隐式 BEGIN、在 DDL 前隐式 COMMIT，结果是事务性 DDL 拿不到、`BEGIN IMMEDIATE` 也轮不到你发。必须 `isolation_level=None` 加自己发 BEGIN。用 DEFERRED 的代价见 D3：升级写锁失败**不受 `busy_timeout` 保护、也不能靠等待解决**，而这正是并发下最难复现的一类故障。

3. **Alembic batch 模式的表重建是丢数据的地方。** 改列类型、改约束都会走"建新表 → 拷数据 → 删旧表 → 改名"。漏一列就静默丢一列的数据，测试如果只断言"表还在"是看不出来的。E2 的用例断言了全部行、全部列和唯一索引三项，契约 PR 的 review 要按同样的标准看。

4. **B6 和 D7 记录的是"没有的东西"，别把它们当成待办去补。** B6 说的是数据库不校验类型，D7 说的是没有 `SKIP LOCKED`——两条都已经有明确的替代实现（应用层校验、租约式抢占），不是缺口。真正的缺口在第 9 节。

另外：结论表里的"部分支持"和"不支持（预期内）"都必须保留这个措辞，不要为了表格好看合并进"支持"。替代实现一旦被采用，就是后续所有模块的既定前提，含糊描述会让 T05、T07 建立在错误假设上。
