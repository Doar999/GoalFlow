# T05 时间预算与多目标排期

| 项 | 值 |
| --- | --- |
| 工作包 | T05（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | Giraffe12311 |
| 状态 | 已完成（契约 PR #19、业务实现 PR #20 均已合并） |
| 更新日期 | 2026-09-24 |
| 相关 PR | #19（契约面）、#20（业务实现），均已合并，CI 三 job 全绿 |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容，不要追加"第二次会话……"这类流水账。历史在 Git 里。

## 1. 目标

交付确定性多目标排期引擎：以纯函数 `calculate_agenda(snapshot) -> SchedulingResult` 为核心，覆盖周/日容量计算、候选过滤、分层排序、装箱拆分、原因码与冲突输出，以及配套的持久化与并发协议（planning revision 重查、agenda revision 唯一化）。

达成标准：

- 排期是纯 Python 确定性模块：不调用模型、计算函数不读写数据库；相同快照重复计算得到相同结果。
- 多目标不重复消耗同一份容量；必须项超容量时全部进入冲突输出，不按隐藏分数静默删除。
- 总额/剩余额度两种口径不混用；预算为零、并发变更有测试覆盖。
- 用户锁定（must_do_today / locked）与 focus/rank 偏好按 [13 号文档](../development/13-scheduling-engine.md) 生效。

## 2. 范围

### 可修改路径

```text
backend/src/goalflow/scheduling/
backend/tests/scheduling/
docs/worklog/T05-scheduling.md
docs/worklog/README.md                 （仅索引表）
```

### 明确不可修改

```text
openapi/
backend/migrations/
backend/src/goalflow/contracts/
backend/src/goalflow/db/
backend/src/goalflow/goals/            （T04 已交付；仅 A3 接缝替换按约定修改函数体）
frontend/
```

本工作包需要新增排期族迁移、contracts 原因码枚举与 OpenAPI 排期端点，均按契约流程**单独提契约 PR**（见决策 A4—A6），不在业务实现 PR 里顺手做。

### 不在本次范围内

- 模型解释冲突、生成变更建议（T08 Agent 图）；本模块只输出结构化冲突与 `change_proposals`。
- 目标关联与依赖环检测（T06）。`task_dependencies` 表由 T06 建迁移；本模块快照中的依赖结果字段预留，T06 合并前视为全部满足。
- 档案、路线、计划版本等 goals/plans 族（T04 已交付）。
- 前端页面（T10/T11 消费排期结果）。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [PRD](../product/PRD.md) | R06、R07、R08、Q04、Q08 | 已确认 |
| [PRD](../product/PRD.md) 决策表 | D03（滚动计划与开始日期）、D05（多目标优先级和冲突）、D06（自动调整边界） | 已确认 |
| [核心数据模型](../development/03-data-model.md) 第 4 节 | 排期族 8 张表、两种额度口径、排期约束 | 已确认 |
| [多目标排期与冲突引擎](../development/13-scheduling-engine.md) | 全文：模块接口、容量、过滤、分层排序、装箱拆分、冲突原因码、持久化与 API | 已确认（首版实现基线） |
| [模块契约与开发衔接](../development/05-module-contracts.md) | read_today、ensure_agenda、update_goal_priorities、constrain_today_task、update_availability、override_today 接口行，第 49 行排期模块约定 | 建议（共同规则与默认值表已确认） |
| [T04 交接卡](T04-goal-plan-versions.md) | A9（策略参数版本化落 contracts 常量）、A11（resume 预算接缝 `check_shared_weekly_budget` 签名） | 已完成 |
| [目标关联与依赖解除实现基线](../development/18-goal-link-design.md) | 第 4、5 节：投入变化阈值与可移动日期范围推导 | 已确认 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| A1 | 排期策略参数（分层排序阈值、minimum_session_minutes 等）落 `contracts/` 版本化常量，`scheduling_policy_version` 写入 `agenda_revisions`；暂不建策略参数表 | [T04 决策 A9](T04-goal-plan-versions.md) 同一约定；13 号第 4 节"数值阈值由版本化 scheduling policy 管理" | contracts、agenda_revisions | 否 |
| A2 | 依赖结果输入字段在 `SchedulingSnapshot` 中预留，T06 合并 `task_dependencies` 前所有任务依赖视为满足；实现方式沿用 T04 A12 的"占位 + 显式标记 + 测试保护"模式，不伪装成已检查 | [13 号](../development/13-scheduling-engine.md)第 3 节；T04 A12 先例 | scheduling 模块、T06 衔接 | 否 |
| A3 | T04 预留的 resume 预算接缝 `check_shared_weekly_budget(user_id, week, goal_demand) -> {ok, over_by_minutes, conflicts}` 由本工作包交付真实现，替换 `not_available` 占位；resume 检查是提示层，排期计算冲突原因码是权威层 | [T04 决策 A11](T04-goal-plan-versions.md) | goals/lifecycle resume 流程（仅替换接缝函数体，经 T04 负责人确认后合并） | 否 |
| A4 | 排期族 8 张表迁移为 0005（0004 已被 T04 占用），单独契约 PR | AGENTS.md 红线 1；T16 决策 B8 编号约定 | migrations | 否 |
| A5 | reason codes（`MANDATORY_CAPACITY_CONFLICT` 等 6 个）、constraint_kind、override_kind 等枚举进 `contracts/` 单点定义，随契约 PR 提交 | AGENTS.md 契约表 | contracts、API | 否 |
| A6 | OpenAPI 补全 13 号第 8 节 5 个端点（scheduling-preferences、task-constraints、agendas/{date}、generation、availability 两端点），单独契约 PR；[05-module-contracts](../development/05-module-contracts.md) 已预留"各模块实施前补全 OpenAPI" | AGENTS.md 红线 1 | openapi/goalflow.yaml、前端生成类型 | 否 |
| A7 | `focus_status` 枚举定为 `focused` / `normal`（`GoalFocusStatus`）。03 第 4 节只给了列名未给取值；focus 只提升弹性任务排序权重（13 号第 9 节），两个取值够表达 | 文档空白，本次按最小取值集实现 | contracts、goal_scheduling_preferences、API | 否（填补文档空白） |
| A8 | 单日约束的清除保留 `cleared` 行、不删行（`TaskDayConstraintStatus`）。03 第 4 节有 status 列但未给取值；保留历史支持 Q08 使用历史可解释 | 文档空白；沿用 goal_links"不设软失效"的对照面：约束行轻量、查询按 (owner_id, task_id, local_date) 唯一取 active | contracts、task_day_constraints、API | 否 |
| A9 | `capacity_summary` 的额度口径枚举 `CapacityBasis = total / remaining`，与 `DailyOverrideKind` 同值不同义（一个是存储口径、一个是计算口径）；`change_proposals` 序列化进 `agenda_revisions.conflict_json` 的 `change_proposals` 键，不加新列 | 13 号第 1 节 capacity_summary"额度来源"未定形；03 第 4 节 agenda_revisions 只有三个 JSON 列 | contracts、agenda_revisions、API | 否 |
| A10 | `POST /agendas/{date}/generation` 建模为异步作业（202 + JobResponse），依赖既有 jobs 去重（05-module-contracts ensure_agenda"去重生成作业"）；`ChangeProposalView.proposal_type` 首版用说明性字符串，取值随业务实现固定后再收敛为枚举 | 05-module-contracts 接口行 | API、jobs | 否 |

## 5. 验收场景

逐条对应 13 号第 9 节测试重点与交付计划的专项要求。

### 容量与额度

- [ ] 三个独立目标不能分别重复使用同一日容量
- [ ] 全天额度容量 = `max(0, total - actual_spent)`；剩余额度从声明时点起扣，同一段投入不扣两次
- [ ] 周容量中缺失实际耗时按 unknown 处理并以明确标记的预计值辅助，不当零分钟
- [ ] 预算为零时：不产生负容量、不静默丢弃任务，冲突/延期输出如实反映
- [ ] 当天 override 不改写后续默认额度（R08）

### 分层排序与锁定

- [ ] 必须项超过容量时全部出现在冲突中（`MANDATORY_CAPACITY_CONFLICT`），不按目标顺序静默删除
- [ ] focus/rank 只改变弹性任务顺序，不越过硬期限或进行中任务
- [ ] 低优先级目标连续未安排后在同等弹性条件下获得更高顺序，但不挤掉必须项
- [ ] `latest_date == local_date` 与 must_do_today/locked 进入必须项集合；locked 固定分配分钟数

### 装箱与拆分

- [ ] 不可拆分任务需 `expected_minutes <= remaining_capacity`；可拆分任务低于 `minimum_session_minutes` 不生成无意义片段
- [ ] 同一天同一任务最多一个 agenda item
- [ ] 今日容量减少不改写已完成耗时，也不覆盖进行中任务

### 持久化与并发

- [ ] 相同快照重复计算得到相同结果；并发提交经 planning revision 重查 + 唯一约束只保留一个当前 revision
- [ ] 排期期间输入变化（revision 过期）触发重算而非覆盖
- [ ] 旧 agenda revision 保留可查（Q08 使用历史可解释）

### 通用红线

- [ ] 数据隔离：第二个用户访问 agendas/availability/preferences 一律被拒
- [ ] 全部数据库测试在与生产同构的 SQLite 配置（同一组 pragma、WAL、真实库文件）下运行，不允许 `:memory:`

## 6. 进展

- 已完成：开工检查；分支；交接卡（决策 A1—A10）。
- 已完成（契约面，PR #19 已合并）：contracts 排期枚举；迁移 0005 排期族 8 张表 + `scheduling/models.py` ORM；`api/routes/scheduling.py` 6 端点契约形状；OpenAPI 与前端类型重导出。
- 已完成并合并（业务实现，PR #20，2026-09-24）：
  - `scheduling/engine.py`：`calculate_agenda` 纯函数——容量两口径、候选过滤、必须项集合与分层排序（紧迫/周缺口/饥饿/focus/rank/适配性）、装箱拆分、6 类冲突输出与 deadline_extension 变更建议；排序键全序 tiebreaker 保证确定性。
  - `scheduling/service.py`：快照组装（只读事务）、`persist_agenda` 短写事务落库与 current_revision 切换、preferences/availability/override/constrain 四个命令（各带 revision 重查）、`get_agenda`、`ensure_agenda`（作业提交 + outbox 投递）、`resolve_shared_budget`（A11 接缝真实现）。
  - `scheduling/jobs.py`：`agenda_generation` 作业处理函数（事务外计算、commit 内重查 revision），已登记 `celery_app.HANDLER_MODULES`。
  - 路由接线：6 端点全部由桩改为调用模块 Interface；`api/dependencies.py` 新增 `JobPublisherDep`（测试可覆盖为 no-op）。
  - lifecycle 接缝替换：`resolve_shared_budget(db, user_id)` 签名扩展并委托排期模块（A11 兑现）。
  - 测试：`tests/scheduling/` 39 条（引擎 19、服务 15、API 5），合计 348 passed。
- 开放项：pause 依赖回填（T06）、生成类端点接线（T08/T09）。

## 7. 验证结果

2026-09-24 在 Windows、Python 3.11、SQLite 上执行。数据库用例全部由迁移建出真实库文件，经 `create_database_engine()` 连接（WAL、`foreign_keys=ON`、`busy_timeout=5000`），不使用内存库。

```text
$ uv run --project backend pytest backend/tests
348 passed, 1 warning（#19 合并后 309 + 本 PR 新增 39）
唯一 warning 为 starlette 对 anyio 别名的既有弃用提示

$ ruff format --check backend / ruff check backend
通过

$ uv run --project backend mypy --config-file backend/pyproject.toml backend/src
Success: no issues found in 55 source files

$ npx -y pnpm@9.15.4 --dir frontend run lint / exec tsc --noEmit / exec prettier --check src
全部通过（check.sh 前端段因环境 corepack 冲突失败，以上为等价命令真实输出）
```

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| T06 未建 `task_dependencies` 表 | 依赖结果字段按 A2 占位（恒 satisfied），T06 合并后回填并打开"依赖未满足进 deferred"的集成测试 | T06 负责人 |
| 今日已投入（spent_minutes）恒为 0 | 执行记录归 T11；总额口径下会高估当日剩余容量，T11 接入后修正 | T11 负责人 |
| 周需求 v1 口径（见 service.py docstring） | pending 任务 expected 之和（限最晚日期 ≤ 本周日）+ in_progress 剩余；T11 落地实际投入后可改按"计划包络"口径 | T11 负责人 |

已解决：工作区 ci.yml 未提交改动被另一会话撤销（未决问题自然消除）。

T04 已全部完成（#16、#18 已合并）：goals/ 禁触解除，A3 接缝替换 `check_shared_weekly_budget` 已在本 PR 兑现（`resolve_shared_budget(db, user_id)` 签名扩展并委托排期模块）。

## 9. 给接手者

1. **排期计算函数必须保持纯函数**：不读写数据库、不调用模型。数据库装配与 revision 重查在应用服务层的短写事务里完成（`BEGIN IMMEDIATE`），计算在事务外。写事务规则见 [T16 交接卡](T16-data-layer-foundation.md)。
2. **不要用内存库跑数据库测试**。测试基建在 `backend/tests/db_compat/` 与 `backend/tests/db/`，直接复用；conftest 由迁移建库。
3. **枚举进 `contracts/`**，不在 scheduling 模块自造字符串常量；改 contracts/OpenAPI/迁移是契约变更，单独 PR。
4. **`blocked` 是计算态**，不要给 tasks 加列；暂停目标的任务是否进入候选集由候选过滤（13 号第 3 节）处理。
5. **总额/剩余额度是 `daily_overrides.override_kind` 的两种口径**，不是可互换参数；混用是最容易写错的地方（03 号第 4 节）。
6. **Snapshot 一律不可变输入**：先在事务外组装快照（含 planning_revision），再进短事务重查 revision——过期就放弃重算，不要在事务内做计算。
7. **测试文件命名带模块前缀**（`test_scheduling_*.py`）：tests 目录无 `__init__.py`，`test_service.py`/`test_api.py` 这类通用名会与 auth/db 的同名文件 pytest 收集冲突（T04 也踩过同一坑）。
8. **generation 是异步作业**（A10）：处理函数经模块级 `get_database()` 取库（Worker 单例）；service 层逻辑直接测 `run_agenda_generation`（接受显式 Database），不要经 Celery 测。
