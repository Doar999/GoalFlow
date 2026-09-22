# 多目标排期与冲突引擎 v0.1

状态：首版实现基线已确认。排期是纯 Python 确定性模块，不调用模型；模型可以解释冲突或生成变更建议，但不能决定最终任务集合。

## 1. 模块接口

```python
calculate_agenda(snapshot: SchedulingSnapshot) -> SchedulingResult
```

`SchedulingSnapshot` 包含用户本地日期和时区、有效日/周容量、planning revision、活动目标、目标调度偏好、任务规格与执行状态、依赖结果、用户锁定、近期分配记录及相关领域约束。它是不可变输入，计算函数不读写数据库。

`SchedulingResult` 包含：

- `agenda_items`：任务、分配分钟数、顺序和原因码。
- `deferred_tasks`：未进入当天的任务及原因。
- `conflicts`：无法同时满足的硬约束和分钟缺口。
- `change_proposals`：需要用户确认的期限、投入或目标取舍建议。
- `capacity_summary`：额度来源、已投入、已保留和剩余分钟。

## 2. 容量计算

先根据 availability version 和 daily override 计算当天有效容量。全天额度使用 `max(0, total - actual_spent)`；剩余额度从用户声明时点起使用，不能重复扣除已投入时间。

周容量统计活动计划的预计需求、已安排分钟、有效实际投入和剩余日期容量。缺失实际耗时保持 unknown，并用明确标记的预计值辅助计算，不能当作零分钟。

## 3. 候选过滤

任务进入候选集前必须满足：状态允许安排、earliest_date 已到、依赖结果满足、任务及目标未暂停、必要能力可用、目标关系合法。超过 latest_date 的任务进入冲突或变更建议，不能作为普通弹性任务静默移动。

跨目标依赖只读取已确认 goal_link 和 task_dependency。独立目标之间不共享完成结果，排期器只能看到统一时间竞争。

## 4. 层级排序

第一步形成必须项集合：

- `in_progress`。
- 用户设置的 `must_do_today` 或 `locked`；must_do_today 固定日期，locked 还固定当前分配分钟数。
- `latest_date == local_date`。
- 领域规则判定为保持安全/连续性所必需，或会阻塞近期硬期限任务的前置任务。

必须项总量超过容量时返回 `MANDATORY_CAPACITY_CONFLICT`，保留全部冲突项供用户选择，不按隐藏分数删除。

剩余弹性任务使用稳定的分层排序键：

1. 期限/里程碑紧迫区间。
2. 本周实际或已安排投入相对计划需求的缺口区间。
3. 用户设置的 focus 状态和 goal rank。
4. 连续性要求及未获安排天数区间。
5. 是否能适配剩余容量、依赖解锁价值和目标切换成本。

排序键、所选层级及原因码写入结果。数值阈值由版本化 scheduling policy 管理；不向用户展示没有业务意义的总分。投入变化阈值与可移动日期范围的推导规则见 [目标关联与依赖解除实现基线](18-goal-link-design.md) 第 4、5 节。

## 5. 装箱与拆分

排期只分配分钟和顺序，不生成起止时刻。不可拆分任务需要 `expected_minutes <= remaining_capacity` 才能加入；可拆分任务需要剩余容量至少达到 minimum_session_minutes，分配量不超过任务剩余预计分钟。

同一天同一任务最多一个 agenda item。部分分配不会改变任务完成标准，执行反馈记录实际进展。无法容纳任何任务时允许剩余容量为空闲，不用低价值任务填满。

## 6. 冲突和变更分类

- `MANDATORY_CAPACITY_CONFLICT`：必须项超过今日容量。
- `WEEKLY_CAPACITY_CONFLICT`：活动计划周需求超过剩余周容量。
- `DEADLINE_RISK`：按当前容量无法在 latest_date 前完成。
- `DEPENDENCY_BLOCKED`：前置结果未满足。
- `MINIMUM_SESSION_UNFIT`：剩余容量不足最小单次时长。
- `CROSS_GOAL_IMPACT`：建议会影响关联目标。

在允许日期范围内移动未开始任务、调整顺序和合法拆分可以自动执行并记录原因。里程碑延期、增加总投入、降低某目标投入、移动用户锁定/进行中任务或影响关联目标时生成 pending change proposal。

## 7. 持久化与并发

应用服务使用 SchedulingSnapshot 调用纯函数后，在短事务中重新检查 user_planning_state.revision。有效时创建新的 agenda_revision 和 agenda_items，保存 capacity、deferred、conflict 和 reason 快照；输入已变化则重算。

目标优先级、用户锁定、任务状态、可用时间、计划启用和执行反馈都会递增 planning revision。页面与 Beat 同时计算相同日期时，通过输入 revision 和唯一约束只保留一个当前有效结果。

## 8. API 衔接

- `PUT /api/goals/scheduling-preferences`：一次提交活动目标 focus 状态和排序，使用 expected_revision。
- `PUT /api/agendas/{date}/task-constraints/{task_id}`：设置 must_do_today、locked 或清除约束。
- `GET /api/agendas/{date}`：返回安排、容量、原因、延期项和冲突。
- `POST /api/agendas/{date}/generation`：保证该日期存在基于最新 planning revision 的安排。
- `PUT /api/availability/dates/{date}`：修改当天总额或剩余额度，并触发协调。

## 9. 测试重点

- 三个独立目标不能分别重复使用同一日容量。
- 必须项超过容量时全部出现在冲突中，不按目标顺序静默删除。
- 用户 focus 只能改变弹性任务顺序，不能越过硬期限或进行中任务。
- 低优先级目标连续未安排后在同等弹性条件下获得更高顺序，但不挤掉必须项。
- 可拆分任务低于 minimum_session_minutes 时不生成无意义片段。
- 今日容量减少不改写已完成耗时，也不覆盖进行中任务。
- 相同快照重复计算得到相同结果；并发提交只有一个当前 revision。
