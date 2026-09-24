# T04 目标与计划版本

| 项 | 值 |
| --- | --- |
| 工作包 | T04（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | （待填） |
| 状态 | 契约 PR #16 已合并（CI 三项全绿）；PR-2 业务实现待启动 |
| 更新日期 | 2026-09-24 |
| 相关 PR | #16（契约面，带 `contract-change` 标签，已合并） |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容，不要追加"第二次会话……"这类流水账。历史在 Git 里。

## 1. 目标

交付"目标 → 来源化档案草稿 → 确认档案 → 路线 → 阶段/里程碑/任务批次 → 计划草稿"整条结构的持久化、版本不变量与生命周期命令。

达成标准：

- 战略版本不可变：已确认档案与计划版本的阶段、里程碑、核心方法没有写路径，修订只能创建新版本。
- 草稿不启用：draft 计划的任务为 proposed，不进入正式待办；启用走原子事务。
- 六个生命周期命令（激活、暂停、恢复、结束、撤销结束、以此为起点新建）按 [目标生命周期与结束实现基线](../development/17-goal-lifecycle-design.md) 的状态机工作。
- 模型生成作业未接入（T08/T09）时，上述结构能用固定样例数据完整驱动并测试。

## 2. 范围

### 可修改路径

```text
backend/src/goalflow/goals/
backend/tests/goals/
docs/worklog/T04-goal-plan-versions.md
docs/worklog/README.md                 （仅索引表）
```

### 明确不可修改

```text
openapi/
backend/migrations/
backend/src/goalflow/contracts/
backend/src/goalflow/db/
frontend/
```

本工作包需要新增 goals/plan 族迁移与 OpenAPI 端点，均按契约流程**单独提 PR**（见决策 A4、A5），不在业务实现 PR 里顺手做。

### 不在本次范围内

- 模型驱动的澄清、路线生成、计划生成作业（T08 Agent、T09 闭环）。本工作包实现这些深模块的**校验与持久化**，候选生成接口留桩：快照进、候选出，由调用方（测试/固定样例）提供。
- 每日排期计算与预算冲突细则（T05）。暂停/恢复只做状态转换、递增 `user_planning_state` revision、返回受影响实体列表；排期重算由 revision 递增触发，归 T05。
- 跨目标关联与依赖环检测（T06）。依赖图校验接口预留，`task_dependencies` 表由 T06 建迁移。
- 前端页面。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [PRD](../product/PRD.md) | R02、R04、R05、R10、D12 | 已确认 |
| [目标形态、生命周期与结束规则](../product/12-goal-lifecycle.md) | 全文：形态推导、状态机、暂停语义、终态与撤销窗口 | 已确认 |
| [核心数据模型](../development/03-data-model.md) 第 1、2、3 节 | goals 及计划/路线族表结构 | 已确认（goals 行已按 D12 与生命周期基线回写；第 7 节事务协议、T01 实测结论此前已确认） |
| [路线生成与比较引擎](../development/11-route-engine.md) | 深模块接口、结构化路线、微调边界 | 已确认（第 7 节数据模型调整已回写 03） |
| [计划生成、滚动细化与启用引擎](../development/12-plan-engine.md) | 模块边界、生成/启用流程、启用事务 | 已确认 |
| [目标生命周期与结束实现基线](../development/17-goal-lifecycle-design.md) | 六个命令、状态机判定、goals 字段 | 已确认（2026-09-23 按决策规程确认为实现基线） |
| [模块契约与开发衔接](../development/05-module-contracts.md) | 共同规则、create_goal 到 activate_plan 的接口行、默认值表 | 建议（默认值表已确认） |
| [T16 交接卡](T16-data-layer-foundation.md) | 读写事务分离（B1）、expunge（B10）、迁移编号（B8） | 已完成 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| A1 | T04 只做确定性核心：表结构、不变量、显式命令与校验。澄清/路线/计划的模型生成是 T08 的图，T09 负责闭环集成；T04 的生成类深模块按"校验与持久化就位、候选由调用方注入"实现 | [05-module-contracts](../development/05-module-contracts.md) 分工第 3 条"后端先用固定模型输出验证事务和状态，再接真实模型" | goals/plans 模块接口形状 | 否 |
| A2 | [17 号文档](../development/17-goal-lifecycle-design.md)已于 2026-09-23 按决策规程确认为实现基线；确认清单（原第 8 节）逐项结论已回写 17 号与 03 号 | 用户（仓库负责人）确认 | goals 表、生命周期命令、OpenAPI | 字段变更走契约 PR，不需 RFC |
| A3 | 暂停/恢复的实际排期与预算影响由 T05 计算、依赖影响由 T06 计算。暂停响应定形：`affected_dependent_tasks` 列表 + `dependency_check: "not_wired"` 显式标记，T06 合并前返回空列表；T04 保证状态转换正确并递增 planning revision | [17 号文档](../development/17-goal-lifecycle-design.md)第 3 节；模块边界 | 跨模块接口 | 否 |
| A4 | goals/goal_profiles/route_sets/routes/plan_versions 族迁移单独契约 PR；迁移顺序号从 0004 续起 | AGENTS.md 红线 1；[T16 决策 B8](T16-data-layer-foundation.md) | migrations | 否 |
| A5 | OpenAPI 补全（生命周期命令端点、profile-draft/confirm、route/plan 草稿读取端点）同样单独契约 PR；[05-module-contracts](../development/05-module-contracts.md) 第 43 行已预留"暂停恢复等在各模块实施前补全 OpenAPI" | AGENTS.md 红线 1 | openapi/goalflow.yaml、前端生成类型 | 否 |
| A6 | `goals.status`、`kind`、readiness、plan/route status 等枚举进 `contracts/` 单点定义，前端由生成类型消费；不散落在业务模块里 | AGENTS.md 契约表 | contracts、API | 否 |
| A7 | "一个目标最多一个当前执行版本/一个 active 档案"用部分唯一索引表达，不引入 active_marker 列 | [03-data-model](../development/03-data-model.md) 第 1 节 | 迁移 | 否 |
| A8 | 结束快照存 `goals.closure_criteria_snapshot_json`：单写点（close 时一次写入）、整读整取（undo 还原、逐条展示），保存用户对每条成功标准的确认选择而非标准内容本身（档案不可变、本就保留） | 2026-09-23 确认；03 号第 1 节 JSON 约定 | goals 表、close_goal/undo_closure | 否 |
| A9 | 版本化参数（24h 撤销窗口、review_period 默认值）落 contracts 命名常量，审计记录 `closure_policy_version`；暂不建策略参数表，T05 的排期策略版本沿用同一约定，将来需要按环境调整时再升级 | 2026-09-23 确认 | contracts、audit_events | 否 |
| A10 | 激活统一入口（方案 A，**2026-09-23 已生效**）：`activate_goal` 并入 `activate_plan` 同一短事务，单 Idempotency-Key，`expected_revision` 同携 goal revision 与 planning_revision，全成或全回滚；`activate_goal` 为模块内部函数，不暴露独立 HTTP 端点。已回写 [17 号文档](../development/17-goal-lifecycle-design.md)第 1 节与 [12 号文档](../development/12-plan-engine.md)第 7 节 | 用户确认；D12"点击开始执行"语义；分事务会产生"goal=active 而计划未启用"的可观察中间态并需两套幂等键 | 生命周期命令、幂等键设计、T09 闭环 | 否（填补文档空白，非改已确认决策） |
| A11 | resume 的共享预算冲突判定采用**接缝契约 + 占位**：注入纯函数 `check_shared_weekly_budget(user_id, week, goal_demand) -> {ok, over_by_minutes, conflicts}`（不落库、不调模型，符合 [05-module-contracts](../development/05-module-contracts.md) 内部计算 Interface 风格）；T05 未合并前接缝返回 `not_available`，resume 照常完成状态转换并递增 revision。两层判定分工固定：**resume 检查是提示层，排期计算冲突原因码是权威层** | 用户确认；[17 号文档](../development/17-goal-lifecycle-design.md)第 3 节 | resume 流程、T05 接口签名 | 否 |
| A12 | pause 的依赖影响列表采用**接口占位 + 测试保护**：OpenAPI 现在即定义 `affected_dependent_tasks`（并入 A5 契约 PR）；T04 提供 `compute_pause_impact(goal_id) -> list[AffectedDependency]` 接缝，stub 返回空列表并携带显式标记 `dependency_check: not_wired`（枚举进 `contracts/`）；测试断言"空列表 + 标记存在"，把"不能伪装成已检查"变成被保护的行为。T06 建 `task_dependencies` 后回填真实现，被依赖任务进入计算态 `blocked` | 用户确认；[17 号文档](../development/17-goal-lifecycle-design.md)第 3 节 | pause 响应契约、goals 模块接口 | 否 |

## 5. 验收场景

### 版本不变量（R05、R10）

- [ ] 已确认档案（goal_profiles）与计划版本的阶段/里程碑没有更新路径；修订创建新 version 并经 `based_on_version_id`/新 `version_no` 关联，未变化的任务复用原 task_id
- [ ] draft 计划的任务为 proposed，不出现在正式待办；重复启用点击只产生一个有效活动版本，重复请求返回原 activation 结果
- [ ] 启用事务任一步失败整体回滚，不留半套任务/安排/outbox
- [ ] 进行中或已完成任务不被新计划版本改写；替换未开始任务需显式取消并记录原因

### 档案草稿与确认（R02）

- [ ] confirm_profile 在同一事务中重查阻断项、创建不可变 goal_profiles、更新 `goals.active_profile_id` 与 `planning_sessions.confirmed_profile_id`
- [ ] 模型晚返回时草稿 revision 不匹配，结果被拒，不覆盖用户刚完成的直接编辑

### 生命周期命令（D12）

- [ ] 状态机：`draft→active`；`active↔paused`；`completed` 仅对 `kind=achievement` 开放；`paused→completed` 拒绝；终态无出口（撤销窗口内除外）
- [ ] `kind=maintenance` 请求 completed 返回业务错误，不静默改写
- [ ] 任务全部完成且验证全部通过时 `goals.status` 仍为 active，只产生可完成提示
- [ ] pause 不触碰任何 tasks 行执行状态；含 in_progress 任务的目标暂停成功
- [ ] close_goal 保存成功标准逐条快照；undo_closure 窗口内恢复、窗口外返回过期；重复提交同一结束命令只生效一次
- [ ] derive_goal 生成的新目标含档案草稿与 `source_goal_id`，不含源目标的任务与执行记录；源目标保持终态

### 通用红线

- [ ] 数据隔离：第二个用户访问 goals/plans/routes/profiles 一律被拒
- [ ] 版本竞争：过期 `expected_revision` 返回 `REVISION_CONFLICT`；新值等于旧值的幂等重提不算冲突
- [ ] 全部数据库测试在与生产同构的 SQLite 配置（同一组 pragma、WAL、真实库文件）下运行，不允许 `:memory:`

## 6. 进展

- 设计基线已确认：原第 8 节 D12 同步清单 7 项与三项跨模块未决问题已于 2026-09-23 全部决策（§4 A8–A12），结论已回写 [03 号数据模型](../development/03-data-model.md)、[17 号实现基线](../development/17-goal-lifecycle-design.md)与 [12 号计划引擎](../development/12-plan-engine.md)。
- 契约面已完成（本 PR，未提交）：迁移 0004（13 张表）、`goals/models.py` ORM 映射、contracts 枚举与 `GOAL_STATE_CONFLICT` 错误码、`contracts/policies.py` 策略常量（A9）、`api/routes/goals.py` 全部 19 个端点的契约形状（处理函数为桩）、OpenAPI 与前端类型再生成、前端 `GOAL_STATE_CONFLICT` 兜底文案、`tests/goals/` 契约保护测试 5 条。
- 业务实现未开始：`goals/` 下只有 models，Interface 与生命周期命令逻辑随业务实现 PR 交付。
- 开放项不变：pause 依赖回填（T06）、resume 预算接缝真实现（T05）。

## 7. 验证结果

2026-09-23 在 Windows、Python 3.11.16、SQLite 3.50.4 上执行。数据库用例全部由迁移建出真实库文件，经 `create_database_engine()` 连接（WAL、`foreign_keys=ON`、`busy_timeout=5000`），不使用内存库。

```text
$ bash scripts/check.sh
==> 后端检查 … ruff format / ruff check / mypy 全部通过
    ==> 契约漂移检查
        契约一致
    ==> 凭证粗筛
        未发现疑似凭证
（pnpm 步骤因 corepack 12.3.4 与项目锁定的 9.15.4 冲突中断，属环境问题，按下法补跑）

$ npx -y pnpm@9.15.4 --dir frontend run lint / exec tsc --noEmit / exec prettier --check src
    exit=0（三项全部通过）

$ uv run --project backend pytest backend/tests -q
    267 passed（原 262 条 + 本工作包新增 5 条），唯一 warning 为既有 anyio 弃用提示
```

新增的 5 条测试：未登录访问目标接口 401、桩端点返回契约化错误体、goals.status CHECK 拒绝脏值、
maintenance 目标写 `closure_kind='completed'` 被库级 CHECK 拒绝（`completed_requires_achievement`）、
目标族 13 张表齐备。ORM 与迁移一致性由既有 `tests/db/test_models_match_migrations.py` 守护
（goals.models 已加入 import，含 CHECK 逐字比对与升级→降级→升级往返）。

## 8. 未决问题

### D12 → 数据模型同步清单：已全部确认并回写（2026-09-23）

原清单 7 项的结论已回写 [17 号实现基线](../development/17-goal-lifecycle-design.md)与 [03 号数据模型](../development/03-data-model.md)：

1. goals 补 8 字段（7 项 + `closure_criteria_snapshot_json`）——已入 03 第 2 节。
2. `goals.status` CHECK `draft/active/paused/completed/stopped`，转换合法性由服务层状态机判定。
3. 快照落点：`closure_criteria_snapshot_json` JSON 列（决策 A8）。
4. `goals.kind` 档案确认事务内同步更新并写审计。
5. 版本化参数落 contracts 常量 + `closure_policy_version` 入审计（决策 A9）。
6. `blocked` 计算态不建列，tasks 零新增字段。
7. 派生关系 = `source_goal_id`（带索引）+ 审计，不建独立表。

三项跨模块未决问题的决策：激活归并（A10，已回写设计文档）、resume 预算接缝（A11）、pause 依赖占位（A12）。

### 仍开放的事项

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| pause 依赖影响列表的真实现 | `task_dependencies` 表归 T06 建；合并前 `dependency_check: not_wired`、列表为空（A12），T06 合并后回填 `compute_pause_impact` 并打开"暂停后被依赖任务进入计算态 blocked"测试 | T06 负责人 |
| resume 预算接缝的真实现 | `check_shared_weekly_budget` 签名已约定（A11）；T05 交付后替换 `not_available` 占位，并打开"恢复时共享预算已占满返回冲突"测试 | T05 负责人 |

## 9. 给接手者

1. **不要用内存库跑数据库测试。** `foreign_keys` 默认关闭、`busy_timeout` 默认为 0，漏设不报错；用宽松环境跑出来的"通过"什么都不说明。测试基建在 `backend/tests/db_compat/` 与 `backend/tests/db/`，直接复用。
2. **读写事务规则在 [T16 交接卡](T16-data-layer-foundation.md)**：`read()` 是 DEFERRED、退出时 `expunge_all()`；写事务一律 `BEGIN IMMEDIATE` 且**不调模型、不发 HTTP**——模型调用在事务外，事务里只重新校验版本再落库。
3. **迁移手写、编号续 0004+**；ORM 与迁移的一致性由 `tests/db/test_models_match_migrations.py` 比对，新建模型后该测试必须能跑。alembic.ini 注释只写英文（T16 决策 B7，中文 ini 在 Windows 上会 UnicodeDecodeError）。
4. **枚举进 `contracts/`**，不要在 goals 模块里自造字符串常量；前端消费生成类型。改 contracts/OpenAPI/迁移是契约变更，单独 PR。
5. **"最多一个 active"用部分唯一索引**（`WHERE status = 'active'`），不建冗余标记列；`blocked` 是计算态，不要给 tasks 加列。
6. **验证结果只能触发"可以标记完成"的提示**，任何统计量都不得写 `goals.status = 'completed'`——这是 D12 的红线，也是 17 号文档测试重点的第一条。
