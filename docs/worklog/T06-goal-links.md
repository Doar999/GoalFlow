# T06 目标关联与任务依赖

| 项 | 值 |
| --- | --- |
| 工作包 | T06（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | Giraffe12311 |
| 状态 | 进行中（业务实现 PR-2 已完成，待提交评审） |
| 更新日期 | 2026-09-24 |
| 相关 PR | #22（契约面，已合并）；PR-2 业务实现待提 |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容，不要追加"第二次会话……"这类流水账。历史在 Git 里。

## 1. 目标

交付目标关联与任务依赖的后端能力：跨目标关联的提出与确认（含跨目标无环校验）、任务依赖边（`task_dependencies`）的建立与唯一性、解除关联的提案-原子应用流程（未完成依赖先进影响分析，不静默清理），并回填 T04/T05 预留的两处依赖接缝（pause 影响计算、排期快照依赖结果字段）。

达成标准：

- 只带两个 goal_id、不带具体任务关系的关联请求被拒；确认时校验同用户、不自关联、目标对不重复、加入新边后完整依赖图无环。
- 解除存在未完成依赖的关联返回变更提案（含 `criteria_unsatisfiable` 标记）而非直接生效；接受后在同一事务内原子执行，不存在"已解除关联但依赖边仍在"的中间状态。
- pause 与排期的依赖校验从占位（`not_wired` / 恒 satisfied）切换为查 `task_dependencies` 的真实现。

## 2. 范围

### 可修改路径

```text
backend/src/goalflow/links/
backend/tests/links/
backend/src/goalflow/goals/lifecycle.py （仅 compute_pause_impact 接缝函数体，回填 A12 遗留）
backend/src/goalflow/scheduling/       （仅快照依赖结果字段的装配，回填 T05 A2 遗留）
docs/worklog/T06-goal-links.md
docs/worklog/README.md                 （仅索引表）
```

### 明确不可修改

```text
openapi/
backend/migrations/
backend/src/goalflow/contracts/
frontend/
```

本工作包需要新增迁移 0006（goal_links、task_dependencies、change_proposals）、contracts 枚举与 OpenAPI 关联端点，均按契约流程**单独提契约 PR**，不在业务实现 PR 里顺手做。

### 不在本次范围内

- 自动调整的 change_class 判级（`record_only` / `auto_schedule` / `auto_plan_local` 归 T12，见 [14 号](../development/14-feedback-adjustment-engine.md) 第 55 行）；本包只产出 `confirmation_required` 与 `profile_revision_required` 两类提案。
- 成果复用（一份产出挂两个目标）首版不支持（PRD D04）。
- Agent 自动建议关联（T08）；本包只交付用户/命令驱动的建立与解除。
- `fitness_dependency_outcome` 领域策略谓词的注册与执行（16 号 §2 属计划生成校验链路，T08/T09 接线）；本包只保证 `required_outcome` 取值不受 DB 层限制。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [PRD](../product/PRD.md) | R07、Q01、决策 D04 | 已确认 |
| [目标关联、依赖与解除规则](../product/13-goal-links.md) | 全文（默认独立推进、首版只支持任务先后依赖、解除规则、投入阈值） | 已确认 |
| [核心数据模型](../development/03-data-model.md) 第 2、3 节 | goal_links、task_dependencies、change_proposals 三行及第 57 行跨目标依赖约束 | 已确认 |
| [目标关联与依赖解除实现基线](../development/18-goal-link-design.md) | 全文：命令边界、建立/解除流程、API 衔接、测试重点 | 实施设计建议（经本卡决策 A1—A9 采纳后作为首版实现基线） |
| [领域策略包与约束校验](../development/16-domain-policy-design.md) 第 2 节 | `fitness_dependency_outcome` 是应用层 `scope=plan` 谓词，非 DB CHECK | 建议（本包只引用结论） |
| [T04 交接卡](T04-goal-plan-versions.md) | A12：pause 依赖影响接缝（`compute_pause_impact` 恒空 + `not_wired`） | 已完成，待本包回填 |
| [T05 交接卡](T05-scheduling.md) | A2：排期快照依赖结果字段占位恒 satisfied；第 8 节开放项"pause 依赖回填" | 已完成，待本包回填 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| A1 | 迁移 0006 建 goal_links、task_dependencies、change_proposals 三张表（0004 迁移头已声明三表归 T06），单独契约 PR | AGENTS.md 红线 1；T16 决策 B8 编号约定 | migrations | 否 |
| A2 | goal_links 规范化目标对唯一 = CHECK `goal_a_id < goal_b_id`（强制规范化方向，propose 时业务层排序）+ 部分唯一索引 `uq_goal_links_active_pair` WHERE `status IN ('proposed','active')`；removed 行保留归档，解除后允许对同一目标对重新建立关联 | 03 第 2 节只说"规范化目标对唯一"，未定 removed 行是否占位；产品说"已满足的依赖边随关联归档"（13 号第 4 节），归档即保留行 | goal_links | 否（填补文档空白） |
| A3 | `required_outcome` 取值 `execution_completed` / `verification_passed`；`fitness_dependency_outcome`（健身领域禁止 verification_passed）是应用层策略谓词，不加 DB CHECK——库层无法跨表判定 goal 的 domain | 03 第 3 节；16 号第 2 节 | contracts、task_dependencies | 否 |
| A4 | task_dependencies 边唯一 = UniqueConstraint(plan_version_id, predecessor_task_id, successor_task_id)；另加 CHECK `predecessor_task_id <> successor_task_id`（自依赖即平凡环，DB 层直接禁止）。goal_link_id 可空：同目标版本内的依赖边不挂 link；跨目标边必须挂已确认关联，由业务层校验（DB 无法跨表判定两任务的所属目标） | 03 第 3 节"一个版本内边唯一"、第 57 行"跨目标依赖仅在已确认关联下建立" | task_dependencies | 否 |
| A5 | `change_class` CHECK 限 `confirmation_required` / `profile_revision_required`（D04/18 号第 3 节已确认的两个值）；14 号第 55 行另有三个取值归 T12，届时随 T12 契约 PR 扩展 CHECK | 18 号第 3 节；14 号第 55 行 | contracts、change_proposals | 否 |
| A6 | change_proposals 的 `input_revision` 对应 `user_planning_state.revision`；stale 判定（提案基础版本已变）在业务实现中比较该值，不新增列 | 03 第 3 节；18 号第 3 节"提案过期返回 stale" | change_proposals、PR-2 | 否 |
| A7 | API 契约面按 18 号第 6 节 5 个端点 + 补 `GET /api/change-proposals/{id}`（用户确认/拒绝前必须能读到影响分析；18 号未列读取端点，本卡补最小读取）。命令端点沿用 Idempotency-Key 惯例。**PR-2 契约修正**：`ConfirmGoalLinkRequest` 必须携带任务依赖边——03 第 57 行规定"跨目标依赖仅在已确认关联下建立"，依赖边只能在确认事务内落库，propose 阶段的空壳关联承载不了它们；契约 PR #22 的空体设计在实现时被推翻，随 PR-2 评审确认 | 18 号第 6 节 + 03 第 57 行 | openapi、routes | 否 |
| A8 | pause 依赖回填与排期依赖字段回填属业务实现 PR-2：`compute_pause_impact` 查 task_dependencies 真实现（`DependencyCheckStatus.WIRED`，签名加 session 参数）；排期快照依赖结果字段按生效边装配（同计划内边无 link、跨目标边须 active 关联），`verification_passed` 边在验证记录（T11）落地前一律视为未满足——不把"未检查"伪装成"已检查"；打开"依赖未满足进 deferred"集成测试（T05 A2 遗留） | T04 A12；T05 A2 与第 8 节开放项 | goals/lifecycle、scheduling | 否 |
| A9 | 模块名 `links`（backend/src/goalflow/links/），路由文件 routes/goal_links.py；ORM 的 GoalLink/TaskDependency/ChangeProposal 与迁移 CHECK 文本逐字一致，由 models-match 测试比对 | 沿用 T04/T05 的模块-迁移配对模式 | links、migrations、tests/db | 否 |

## 5. 验收场景

逐条对应 18 号第 7 节测试重点与交付计划的专项要求。

### 建立关联

- [x] 只带两个 goal_id、不带任务关系的关联请求被拒（18 号第 7 节；路由 Pydantic min_length=1 + service 兜底）
- [x] 确认时同一事务校验：两目标同属一个用户、不自关联、规范化目标对未重复（含未失效的既有关联）
- [x] 跨目标依赖形成环时确认失败（`DEPENDENCY_CYCLE`），且校验覆盖其他目标的当前版本（03 第 57 行；propose 阶段同判环，fail fast）
- [x] 解除关联后同一目标对可重新建立；removed 行保留归档（A2，部分唯一索引只覆盖 proposed/active）

### 解除关联

- [x] 解除含未满足依赖的关联返回提案而非直接生效；提案含每条边、后继任务及执行状态、`criteria_unsatisfiable` 标记、受影响的里程碑与期限（18 号第 3 节）
- [x] 接受解除提案后，关联状态与依赖边在同一事务内一起变更；中途失败时两者都不变
- [x] 拒绝解除提案后关联与依赖保持原样，且相同输入不自动重新生成提案（提案仅由显式端点创建）
- [x] 解除不改写进行中与已完成的后继任务（accept 路径仅处理 pending 且 criteria_unsatisfiable 的任务）
- [x] 提案过期（input_revision 落后）返回 stale，展示新差异，不沿用旧确认（A6；stale 标记在独立事务先落库再抛 INPUT_STALE）

### 回填接缝

- [x] pause 响应的 `affected_dependent_tasks` 来自 task_dependencies 真实查询，`dependency_check` 为 `wired`（T04 A12 回填）
- [x] 排期快照中依赖未满足的后继任务进 deferred，不再恒 satisfied（T05 A2 回填，含集成测试）

### 通用红线

- [x] 数据隔离：第二个用户关联他人目标、读取他人解除提案、操作他人提案一律被拒（Q01）
- [x] 全部数据库测试在与生产同构的 SQLite 配置（同一组 pragma、WAL、真实库文件）下运行，不允许 `:memory:`

## 6. 进展

- 已完成（契约面，PR #22 已合并）：迁移 0006（goal_links、task_dependencies、change_proposals）；`links/models.py` ORM 三表；contracts 新增 GoalLinkStatus / DependencyOutcome / ChangeProposalStatus / ChangeClass 四枚举；`api/routes/goal_links.py` 6 端点契约形状并注册进 app；OpenAPI 与前端类型重导出。
- 已完成（业务实现 PR-2，待提交）：`links/service.py`——propose_goal_link（规范化目标对、边必须跨目标对、propose 阶段判环）、confirm_goal_link（确认事务内创建依赖边 + 全图无环校验 + 递增 planning revision）、propose_unlink（影响分析：未满足边/后继任务状态/受影响里程碑/已满足边归档清单）、accept（同事务原子：置 removed → 删边 → 处理 criteria_unsatisfiable → applied → 递增 revision；stale 预检在独立事务先落标记再抛 INPUT_STALE）、reject、get_change_proposal；路由 6 端点全部接线；`ConfirmGoalLinkRequest` 契约修正为携带依赖边（决策 A7 修正，随本 PR 评审确认）。
- 已完成（回填）：`goals/lifecycle.py` `compute_pause_impact` 查 task_dependencies 真实现（签名加 session 参数，WIRED 恒成立）；`scheduling/service.py` `build_snapshot` 按 task_dependencies 生效边装配 `dependency_satisfied`（T05 A2 遗留清偿）。
- 测试：`tests/links/` 28 条（service 21、API 7），含无环跨目标校验、原子应用、stale、隔离（Q01）、pause/排期回填集成；合计 376 passed。

## 7. 验证结果

2026-09-24 在 Windows、Python 3.11、SQLite 上执行。数据库用例全部由迁移建出真实库文件，经 `create_database_engine()` 连接（WAL、`foreign_keys=ON`、`busy_timeout=5000`），不使用内存库。

```text
$ uv run --project backend python -m pytest backend/tests -q --tb=short
376 passed, 1 warning（348 基线 + 本 PR 新增 28）
唯一 warning 为 starlette 对 anyio 别名的既有弃用提示

$ uv run --project backend ruff format --check backend / ruff check backend
通过

$ uv run --project backend mypy --config-file backend/pyproject.toml backend/src
Success: no issues found in 59 source files

$ uv lock --project backend --check
通过（无锁文件漂移）

$ 契约漂移：重新导出 OpenAPI 与 openapi/goalflow.yaml diff
一致（ConfirmGoalLinkRequest 契约修正已同步重导出）

$ npx -y pnpm@9.15.4 --dir frontend run lint / exec tsc --noEmit / exec prettier --check src
全部通过（check.sh 前端段因环境 corepack 冲突失败，以上为等价命令真实输出）
```

环境备注：全量 pytest 在沙箱内会因 Windows 文件锁干扰中断，需绕过沙箱执行；此外本包开发中发现**外层写事务内嵌套写事务必然死锁到 busy_timeout 超时**（SQLite 库级写锁），测试支撑的取数与落库必须分事务（见接手者第 7 条）。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| 关联列表读取端点（如 GET /api/goals/{id}/goal-links）未在 18 号第 6 节定义 | 前端展示既有关联时缺接口；T10 前需补契约 | T10 负责人 / 产品 |
| change_class 其余三个取值（record_only / auto_schedule / auto_plan_local）的 CHECK 扩展 | T12 需随其契约 PR 扩展 CHECK 文本 | T12 负责人 |

## 9. 给接手者

1. **迁移手写、ORM 跟随**：CHECK 文本与 goalflow.links.models 的构造逐字一致，models-match 测试逐字比对；新增模块记得在 test_models_match_migrations.py 补 import。
2. **removed 行不删**：解除关联置 status=removed 并归档依赖边；部分唯一索引只覆盖 proposed/active，这是"解除后可重建"的依据（A2）。
3. **无环校验必须跨目标**：把拟启用版本与其他目标的当前版本合成一张图再判环，不能只查单个 plan_version（03 第 57 行）。
4. **解除是提案加原子应用**：不要先改 goal_links.status 再清理依赖边——两步之间失败的中间状态是产品规则明令禁止的（18 号原则）。
5. **测试文件命名带模块前缀**（`test_links_*.py`）：tests 目录无 `__init__.py`，通用文件名会与其他模块 pytest 收集冲突（T04/T05 都踩过）。
6. **fitness 领域的 required_outcome 限制在应用层**：不要试图用 DB CHECK 表达——它依赖跨表的 goal.domain（A3）。
7. **外层写事务里绝不能再开写事务**：SQLite 库级写锁 + `BEGIN IMMEDIATE`，嵌套必死锁到 busy_timeout 超时。测试支撑里"先读批次/阶段、再落任务"必须拆成两个事务；业务代码同理由 db.write() 的短事务协议保证。
8. **stale 标记先落库再抛错**：接受提案时若发现 input_revision 落后，在独立事务里置 stale 并提交，然后才抛 INPUT_STALE——在抛异常的同一事务里做标记会随回滚消失。
