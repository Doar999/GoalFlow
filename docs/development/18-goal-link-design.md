# 目标关联与依赖解除实现基线 v0.1

状态：实施设计建议；承接已确认产品决策 [目标关联、依赖与解除规则](../product/13-goal-links.md)（PRD D04）。字段与接口变更走契约 PR 并同步 [核心数据模型](03-data-model.md)。

原则：不存在"关联已解除但依赖边仍在"的中间状态；解除是提案加原子应用，不是先改标签再清理。

## 1. 命令边界

```python
propose_goal_link(command: ProposeLinkCommand) -> LinkProposal
confirm_goal_link(command: ConfirmLinkCommand) -> GoalLink
propose_unlink(command: ProposeUnlinkCommand) -> ChangeProposal
apply_unlink(proposal_id: UUID) -> AppliedChange
```

`goal_links.status` 建议取值：`proposed` / `active` / `removed`。没有 `inactive`——软失效会产生产品规则禁止的中间状态。

## 2. 建立关联

- `propose_goal_link` 必须携带具体关系，即候选的前驱任务与后继任务，不接受只有两个 goal_id 的"相关"声明。
- 确认时在同一事务内校验：两目标同属一个用户、不自关联、规范化目标对未重复、加入新依赖边后完整依赖图无环。
- 无环校验必须跨目标进行，覆盖拟启用版本与其他目标的当前版本（[核心数据模型](03-data-model.md) 第 53 行已要求）。
- 首版只创建 `task_dependencies` 边，不建立任何跨目标的产出引用。

## 3. 解除关联

`propose_unlink` 计算并返回：

1. 该 `goal_link_id` 下**未满足**的依赖边清单。
2. 每条边的后继任务及其当前执行状态。
3. 后继任务的完成标准是否引用了前驱产出。引用了即标记 `criteria_unsatisfiable`——这是提案里最重要的一项，缺了它用户看不出任务会变成不可执行。
4. 受影响的里程碑与期限。
5. 已满足的依赖边单独列出，标记为随关联归档、无需处理。

`change_class` 为 `confirmation_required`；同时改变成功标准时升级为 `profile_revision_required`。

`apply_unlink` 在单个事务内：置 `goal_links.status=removed` → 删除该 link 下的依赖边 → 对 `criteria_unsatisfiable` 的未开始任务创建新 `task_spec` 或显式取消并记录原因 → 写审计 → 递增 `user_planning_state.revision`。

进行中与已完成的后继任务不被改写；它们只进入影响说明，由用户决定后续处理。

提案过期（基础版本已变）返回 stale 并展示新差异，不沿用旧确认。

## 4. 日期范围推导

`task_specs.latest_date` **建议设为非空约束**。这是产品规则"不自行假定任务可以延期"的唯一可执行形式：允许空值等于默认允许无限延期。

计划生成时按优先级推导：

| 字段 | 推导顺序 |
| --- | --- |
| `earliest_date` | 依赖满足的最早日期 → 所属阶段开始日 → 计划开始日 |
| `latest_date` | 硬期限 → 所属里程碑目标窗口结束日 → 所属阶段结束日 |

推导结果参与计划草稿的服务端校验；任一任务推导不出 `latest_date` 时，生成失败并返回结构缺口，不写入空值。

## 5. 投入阈值参数

归入版本化 scheduling policy，与既有阈值同处一地（[排期引擎](13-scheduling-engine.md) 第 52 节）：

```text
approved_weekly_baseline_minutes   # 用户最近一次批准的周投入，自动调整不更新它
effort_increase_ratio_threshold    # 默认 0.10
effort_increase_absolute_minutes   # 默认 30
```

判定：`累计增量 > baseline * ratio` **且** `累计增量 > absolute` → `confirmation_required`。累计增量相对 `approved_weekly_baseline_minutes` 计算，不相对上一次自动调整后的值——否则小步累积可以永久绕过阈值。

独立于以上判定：任何使共享周预算总需求超出可用额度的调整一律 `confirmation_required`，复用既有的 `WEEKLY_CAPACITY_CONFLICT` 判定路径。

## 6. API 衔接

- `POST /api/goals/{id}/link-proposals`：提出关联，携带具体任务关系。
- `POST /api/goal-links/{id}/confirm`：确认建立。
- `POST /api/goal-links/{id}/unlink-proposals`：生成解除提案及影响分析。
- 解除的接受与拒绝复用 `POST /api/change-proposals/{id}/accept|reject`，不新增终态接口。

## 7. 测试重点

- 只带两个 goal_id、不带任务关系的关联请求被拒绝。
- 跨目标依赖形成环时确认失败，且校验覆盖其他目标的当前版本。
- 解除含未满足依赖的关联返回提案而非直接生效，提案中包含每条边、后继任务与 `criteria_unsatisfiable` 标记。
- 接受解除提案后，关联状态与依赖边在同一事务内一起变更；中途失败时两者都不变。
- 拒绝解除提案后关联与依赖保持原样，且相同输入不自动重新生成提案。
- 解除不改写进行中与已完成的后继任务。
- 计划生成时任一任务推导不出 `latest_date` 即失败，数据库中不存在 `latest_date` 为空的 `task_specs` 行。
- 周投入 60 分钟的目标累计增加 7 分钟不触发确认；增加 35 分钟触发。
- 周投入 1200 分钟的目标单次增加 31 分钟不触发确认；累计达到 150 分钟触发。
- 多次自动小幅增加后，累计口径仍相对用户批准基线计算，不被自动调整推移。
- 使共享周预算超额的调整无论幅度都触发确认。
- 第二个用户尝试关联他人目标或读取他人解除提案，全部被拒。
