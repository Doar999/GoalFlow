# 目标生命周期与结束实现基线 v0.1

状态：实施设计建议；承接已确认产品决策 [目标形态、生命周期与结束规则](../product/12-goal-lifecycle.md)（PRD D12）。下述命令、字段与判定为实施建议，实现前按 [决策规程](../engineering/00-workflow.md) 确认；`goals` 字段变更走契约 PR 并同步 [核心数据模型](03-data-model.md)。

原则：状态转换是显式命令，不是任何统计量的副作用；终态单向；暂停只改变调度参与度，不改写执行事实。

## 1. 命令边界

```python
activate_goal(command: ActivateGoalCommand) -> GoalState
pause_goal(command: PauseGoalCommand) -> GoalState
resume_goal(command: ResumeGoalCommand) -> GoalState
close_goal(command: CloseGoalCommand) -> GoalClosure      # completed | stopped
undo_closure(command: UndoClosureCommand) -> GoalState
derive_goal(command: DeriveGoalCommand) -> Goal           # 以此为起点新建
```

全部携带 `expected_revision` 与 Idempotency-Key。转换在短事务内完成：校验归属 → 校验转换合法 → 写状态与审计 → 递增 `user_planning_state` revision 触发重算。

**没有任何路径可以由任务完成率、验证结果或回顾结论自动写入 `completed`。** 验证结果只能触发"可以标记完成"的提示。

## 2. 状态机判定

| 当前 | 允许目标状态 | 附加条件 |
| --- | --- | --- |
| `draft` | `active` | 启用时事务校验共享预算与计划草稿版本 |
| `active` | `paused`、`completed`、`stopped` | `completed` 仅对 `kind=achievement` 开放 |
| `paused` | `active`、`stopped` | `paused → completed` 不允许，须先恢复 |
| `completed`、`stopped` | 无 | 撤销窗口内经 `undo_closure` 回到结束前状态 |

`kind=maintenance` 的目标请求 `completed` 时返回业务错误，不做静默改写。

## 3. 暂停与恢复

`pause_goal`：

1. 写入 `paused_at`、`pause_reason`；不触碰任何 `tasks` 行的执行状态。
2. 排期器按已有前提排除该目标（[排期引擎](13-scheduling-engine.md) 第 29 行已要求"任务及目标未暂停"）。
3. 停止该目标的滚动任务批次生成，已有批次保留但不进入排期。
4. 周投入需求从共享预算需求中移除；释放不触发其他目标的自动追加。
5. 计算受影响的跨目标依赖并随响应返回，供界面在操作前预览与操作后展示。

`resume_goal`：

1. 重新读取共享周预算；总需求超额时返回冲突供用户取舍，不压缩本目标投入。
2. `detailed_through_date` 早于今天时创建新的任务批次，不复用过期批次。
3. 不生成补齐历史日期的安排；按"多日未登录返回"的既有路径先确认现状。

暂停期间不推进依赖解锁，也不产生新的验证作业。

## 4. 结束、撤销与派生

`close_goal` 写入 `closed_at`、`closure_kind`（`completed` / `stopped`）、`closure_note` 及成功标准的逐条快照——快照是必需的，否则撤销与回顾无法还原"当时哪几条未达成"。

`undo_closure` 的窗口默认 24 小时（版本化参数）：

- 窗口内恢复结束前状态并写审计；窗口外返回已过期，提示改用派生。
- 撤销不重放期间被释放的预算占用，按恢复路径重新校验。
- 撤销是补偿操作而非状态回退，审计中保留两条记录。

`derive_goal` 复制源目标最新档案内容为新目标的档案草稿，写 `source_goal_id`，不复制计划版本、任务与执行记录。源目标保持终态。

## 5. 维持型回顾

维持型目标按 `review_period`（默认 `weekly`）产生周期回顾结果：`on_track` / `off_track` / `interrupted`。

- 回顾由 Beat 按用户时区触发，与每日安排使用同源去重键。
- 回顾结果写入 `goal_review_results`（见 [核心数据模型](03-data-model.md) 第 5 节）并携带 `policy_version`，不写入 `goals.status`。该表与日级的 `daily_reviews` 是两回事，不复用。
- `off_track` / `interrupted` 进入调整评估，可提出降低频率或调整内容的建议；超出计划包络时按待确认变更处理。
- 维持型目标的计划没有终点：`plan_versions.horizon_end` 可空，滚动展开按 `review_period` 持续进行，里程碑退化为周期性回顾点。展开的唯一终止条件是目标离开 `active`。见 [计划引擎](12-plan-engine.md) 第 3 节。

## 6. `goals` 字段建议

在现有 `kind`、`status` 之外建议补充：`paused_at`、`pause_reason`、`closed_at`、`closure_kind`、`closure_note`、`review_period`、`source_goal_id`。

`kind` 由档案的时间边界推导后写入 `goals`，档案新版本改变时间边界时同步更新并写审计；不允许 `goals.kind` 与 `goal_profiles` 的时间边界长期不一致。

## 7. 测试重点

- 任务全部完成且全部验证通过时，`goals.status` 仍为 `active`，只产生可完成提示。
- `kind=maintenance` 请求 `completed` 被拒绝。
- 维持型目标的计划在 `horizon_end` 为空的情况下持续滚动展开，不产生"计划已执行完"状态；暂停后展开停止，恢复后重新开始。
- 周期回顾结果落在 `goal_review_results` 且不改写 `goals.status`；同一周期重复触发只产生一条记录。
- 暂停一个含 `in_progress` 任务的目标成功，且该任务状态与已投入分钟数不变；恢复后仍为 `in_progress`。
- 暂停后被依赖方的任务进入计算态 `blocked`，且响应中返回受影响任务列表。
- 暂停释放的预算不触发其他目标自动追加任务。
- 暂停两周后恢复，不生成覆盖历史日期的安排，且产生新的任务批次。
- 恢复时共享预算已被占满，返回冲突而不是缩减本目标投入。
- 结束时保存成功标准逐条快照；撤销后快照可还原。
- 撤销窗口外的 `undo_closure` 返回过期，且不改变状态。
- `derive_goal` 生成的新目标不含源目标的任务与执行记录，且 `source_goal_id` 正确。
- 终态目标不存在任何转回 `active` 的路径（撤销之外）。
- 重复提交同一结束命令只生效一次。
