# 计划生成、滚动细化与启用引擎 v0.1

状态：首版实现基线已确认。负责把选定路线转换成可检查的计划草稿和近期任务，并安全启用；每日安排由独立排期模块计算。

## 1. 模块边界

计划模块维护不可变的战略结构，任务批次模块负责按窗口补充近期任务，排期模块负责把任务放到具体日期：

```text
PlanVersion       阶段、里程碑、核心依赖和总体假设
TaskBatch         某个连续日期窗口内生成的详细任务
Task / TaskSpec   稳定任务身份及不可变内容版本
DailyAgenda       某一天实际准备执行的任务排序和分配分钟数
```

建议提供以下深模块接口：

```python
generate_plan_draft(snapshot: PlanGenerationSnapshot) -> PlanCandidate
validate_plan(candidate: PlanCandidate, snapshot: PlanGenerationSnapshot) -> ValidatedPlan
extend_task_window(snapshot: TaskWindowSnapshot) -> TaskBatchCandidate
activate_plan(command: ActivatePlanCommand) -> ActivationResult
```

上层 API 不直接创建 tasks、dependencies 或 agenda_items，统一经过这些业务入口。

## 2. 首次生成流程

1. 记录 profile_id、route_id、开始日期、planning revision、availability revision 和去重键，创建作业。
2. 事务外构建目标、路线、领域策略、共享预算、关联目标和能力快照。
3. 模型一次生成阶段、里程碑、核心依赖及首个七日任务批次。
4. Pydantic 校验结构、ID 引用、日期和枚举。
5. 领域规则检查阶段逻辑、里程碑证据、任务完成标准及安全限制。
6. 计划规则检查依赖无环、首周预算、成功标准覆盖、时间范围、执行者和能力可用性。
   同时校验开始日可用性：开始日的剩余容量必须能容纳该日安排的至少一项任务（不可拆分任务取 `expected_minutes`，可拆分任务取 `minimum_session_minutes`）。一项都容纳不下时，草稿照常保存，并在结果中带上建议前移到的下一个可容纳日期与原因码，由用户在预览中确认；不在服务端静默改写 `start_date`。
7. 仅对结构错误允许一次有限修复；真实预算或期限冲突直接返回结构化冲突。
8. 提交前重新检查输入 revision，有效时原子保存 draft 计划版本、阶段、里程碑和 proposed 任务批次。

模型调用不占用数据库事务。计划草稿生成失败时保留选中路线和用户修改要求。

## 3. 计划结构和任务批次

`PlanVersion` 的阶段、里程碑和核心约束不可变。新计划修订使用 based_on_version_id，并对复用的任务保留相同 task_id。

`TaskBatch` 包含 plan_version_id、window_start、window_end、input_progress_revision、generation_policy_version、status 和生成原因。一个计划窗口只能有一个当前有效批次；过期或失败批次保留记录但不进入排期。

正常滚动展开只追加新的 task batch。以下情况需要新 plan version：阶段或里程碑变化、既有核心依赖变化、路线方法变化、成功标准或硬期限变化。既有任务内容调整创建 task_spec；任务日期排序变化创建 agenda revision。

**维持型目标的计划没有终点。** `horizon_end` 对 `kind=maintenance` 可空，滚动展开按 [目标生命周期与结束实现基线](17-goal-lifecycle-design.md) 的 `review_period` 持续进行，没有"计划跑完"这个状态——这与维持型永不进入 `completed` 一致。其里程碑退化为周期性回顾点：每个回顾周期一个，验收方式就是该周期的成功标准，不表达一次性成果。滚动的唯一终止条件是目标离开 `active`（暂停或 `stopped`）。达成型目标的 `horizon_end` 仍然非空，展开不得越过它。

## 4. 时间估算和任务校验

`TaskSpec` 保存 expected/minimum/maximum minutes、confidence、can_split、minimum_session_minutes、earliest_date 和 latest_date。要求：

- minimum <= expected <= maximum。
- 不可拆分任务的 expected 不能超过其所有允许日期的最大可用额度，否则返回冲突。
- 可拆分任务的 minimum_session_minutes 不能大于其所有允许日期中的最大可用额度，否则该任务无法被分配任何有效片段，返回冲突。判定口径与上一条一致，取允许日期范围内的最大值；不要写成"每一个允许日期都必须满足"——允许日期额度可以为零（见 [多目标协调](../product/02-multi-goal-coordination.md) 的时间额度规则），逐日全称判定会让校验永远失败。
- 用户任务、Agent 任务和协作任务分别计算用户投入；后台模型等待时间不计入用户时间。
- 任务必须引用阶段；承担里程碑证据的任务还需引用 milestone。

预算计算和日期分配由纯 Python 模块完成，不调用模型。模型给出的估算是候选输入，规则层可以标记异常并要求修复，但不能在没有依据时静默改写。

## 5. 滚动展开

按用户本地日期检查活动计划的 detailed_through_date。当该日期早于 `today + 3 days` 时，以最后有效批次之后的连续七日作为新窗口创建去重作业。

展开输入包括当前里程碑、有效执行记录、验证结果、未完成任务、最新时间预算和计划 revision。已经完成或进行中的任务只作为上下文，不能被新批次替换。

`TaskWindowSnapshot` 还必须包含**本计划此前所有批次已产生的学习单元及其已安排的复习日期**。复习间隔是跨批次的硬约束（默认 1、3、7、14 天，见 [三类领域专业规则](../product/11-domain-rules.md) 第 3 节），而单个窗口只有七天——不把已有单元和复习历史带进快照，第二个批次就无法判定 14 天间隔，约束会静默失效。历史按 `(unit_key, 首次学习日期, 已安排复习日期列表)` 组织，只读，不随新批次改写。新批次与现有未完成任务一起由排期模块检查预算；若容量不足，保存冲突，不堆积到每日安排。

同一窗口使用 `(owner_id, plan_version_id, window_start, input_progress_revision)` 作为业务去重依据。Worker 晚返回时重新检查计划和进度 revision，过期结果不得发布。

## 6. 草稿修改

简单字段修改走显式命令并创建新 task_spec。自然语言修改先由模型输出 `PlanChangeCandidate`，包含目标实体、补丁、理由和预期影响；服务端重新计算影响并分类：

- `task_spec_change`：任务内容或估算。
- `schedule_change`：日期范围或排序。
- `plan_revision`：阶段、里程碑或核心依赖。
- `profile_or_route_revision`：目标条件或路线方法变化。

草稿阶段可以自动应用用户明确要求的合法变更，但仍保留版本和审计；活动计划沿用小调整自动、大调整确认的产品规则。

## 7. 启用事务

`activate_plan` 使用 Idempotency-Key 和 expected revisions，在同一短事务内：

1. 获取 user_planning_state，并重新读取 goal、profile、route、draft plan 和时间预算。
2. 验证草稿仍有效、开始日期可用、依赖无环、首批任务完整及跨目标约束成立。
3. 将计划状态切换为 active，并处理同目标旧活动版本。
4. 将首批 proposed 任务切换为 pending，递增 planning revision。
5. 写入审计和 outbox，触发当前日期 agenda 重算。

任何一步失败整体回滚。重复请求返回已有 activation 结果，不能重复创建任务、依赖或安排项。

## 8. API 衔接

- `POST /api/goals/{id}/plan-generations`：提交 route_id、start_date 和 revisions，返回生成作业。
- `GET /api/goals/{id}/plan-drafts/current`：读取计划结构、首周批次、冲突和 stale 状态。
- `PATCH /api/goals/{id}/plan-drafts/current/tasks/{task_id}`：编辑草稿任务并提交 expected_revision。
- `POST /api/goals/{id}/plan-change-requests`：提交自然语言或结构化修改。
- `POST /api/goals/{id}/activation`：幂等启用当前草稿。
- 内部 `ensure_task_window`：由 Beat 或读路径触发去重的滚动展开作业，不作为普通客户端任意调用入口。

## 9. 测试重点

- 首次生成只创建七日详细批次，阶段和里程碑覆盖完整路线。
- 开始日剩余容量大于零但容纳不下任何一项任务时，草稿仍保存，返回建议前移日期而不是静默改写 `start_date`。
- 同一窗口由 Beat 和页面同时触发时只有一个有效批次。
- 预算、依赖和成功标准检查不受模型自报的“可行”结论影响。
- 生成期间时间预算或计划 revision 变化使结果 stale。
- 正常展开不创建 plan version，阶段变化必须创建新版本。
- 已完成或进行中任务不会被滚动批次覆盖。
- 重复激活、激活中断和 outbox 重投都只产生一个有效活动版本。
