# 路线生成与比较引擎 v0.1

状态：首版实现基线已确认。承接已确认目标档案，不负责生成完整执行计划。

## 1. 设计选择

采用“约束快照 + 整组候选生成 + 确定性校验”的混合方案。模型一次看到同一输入并生成整组候选，便于保持比较口径；Python 规则负责硬约束、预算、重复候选、版本和推荐资格。

不为每条路线分别启动独立模型作业。独立生成难以保证目标、估算单位和比较维度一致，还会增加调用成本。首版允许一次有限的结构或差异修复，不无限调用模型争取凑满数量。

## 2. 深模块接口

```python
generate_route_set(snapshot: RouteGenerationSnapshot) -> RouteSetCandidate
validate_route_set(
    snapshot: RouteGenerationSnapshot,
    candidate: RouteSetCandidate,
) -> ValidatedRouteSet | RouteConflicts
revise_route(
    route: RouteSnapshot,
    adjustment: RouteAdjustment,
) -> RouteVariantCandidate
```

`RouteGenerationSnapshot` 包含已确认 profile_id、领域策略版本、共享时间预算快照、活动目标占用、已确认关联、可用 Agent 能力和生成策略版本。模型只接收规划所需的最少摘要，不接收其他目标的私人对话全文。

## 3. 生成流程

1. 事务中记录 profile_id、planning revision、时间预算 revision 和请求去重键，创建路线生成作业。
2. 事务外构建约束快照，计算当前可用分钟、固定依赖和不可更改条件。
3. 模型一次输出一至三条结构化路线、共同假设、差异键及可选推荐解释。
4. Pydantic 校验结构和枚举；领域策略校验阶段、证据和安全边界。
5. 规则层重新计算每阶段和每周投入、共享预算缺口、硬期限以及与现有目标的冲突。
6. 差异校验检查候选的核心方法、投入曲线、阶段顺序或风险处理是否至少一项不同。
7. 只有结构或差异可以修复时允许一次模型修复；业务约束冲突直接返回用户可调整条件。
8. 提交前重新检查输入 revision；有效时原子保存 route_set 和 routes，否则作业标记 stale。

模型不能决定路线是否可行，也不能通过改变成功标准或忽略其他目标占用来让预算通过。

## 4. 结构化路线

`RouteCandidate` 建议包含：

- `title`、`approach_summary` 和 `difference_keys`。
- `duration_range`，避免无依据的精确日期承诺。
- `phase_outline`，每阶段包含成果、预计周数及每周分钟数。
- `verification_cadence` 和 `evidence_types`。
- `tradeoffs`、`risks` 和 `assumptions`。
- `required_resources` 和 `required_capabilities`。
- `profile_change_proposals`，若路线必须改变目标条件则与普通路线分离。

服务端派生 `total_estimated_minutes`、`peak_weekly_minutes`、`available_weekly_minutes`、`budget_gap_minutes`、`conflicts` 和 `recommendation_eligible`。派生值不能由模型直接覆盖。

## 5. 差异和推荐校验

每条路线声明的 `difference_keys` 只能来自受控枚举，例如 `method`、`pace`、`phase_order`、`feedback_frequency`、`resource_use`、`risk_control` 和 `uncertainty_handling`。任意两条路线必须至少存在一个会改变阶段结构、投入或验证方式的有效差异。

推荐先执行硬过滤：预算不满足、依赖不可用、安全限制冲突或要求修改成功标准的路线不能成为默认推荐。剩余路线按用户明确偏好、预算余量、期限匹配和风险暴露形成可解释排序。排序相近或偏好不足时不产生唯一推荐；内部排序结果不作为伪精确用户分数展示。

## 6. 微调与重新生成边界

以下变化可以创建 `based_on_route_id` 指向原路线的新变体：

- 在可行范围内调整每周投入分配。
- 调整阶段节奏但保持核心方法和成功标准。
- 增减反馈或验证频率。
- 替换等价且当前可用的资源。

改变核心方法、目标结果、成功标准、硬期限、关键限制或目标关联时，创建新路线集合。新集合不会覆盖旧集合；planning_session 只指向当前有效集合和已选择路线。

## 7. 数据模型调整建议

- `route_sets` 增加 planning_revision、availability_revision、generation_policy_version、status、invalidated_reason。
- `routes` 增加 based_on_route_id、title、difference_keys_json、duration_range_json、phase_outline_json、risks_json、resources_json、derived_metrics_json、status。
- 路线选择保存在 planning_session，并记录 selected_at；选择动作不创建正式任务。

路线和派生校验结果作为快照保存，便于解释当时为何判定可行。激活计划时仍要使用最新时间预算重新检查，不能依赖旧快照直接启用。

## 8. API 与作业

- `POST /api/goals/{id}/route-generations`：从已确认 profile_id 创建生成作业。
- `GET /api/goals/{id}/route-sets/current`：返回当前集合、差异、派生指标和 stale 状态。
- `POST /api/goals/{id}/route-variants`：提交 route_id 和结构化微调或自然语言要求。
- `POST /api/goals/{id}/route-selection`：记录用户选择和当前 revision。
- `POST /api/goals/{id}/route-rejections`：记录“都不合适”的原因并决定微调还是生成新集合。

作业进度只需要 queued、generating、validating、repairing、completed、failed、stale。SSE 断线后通过作业和当前路线集合恢复。

## 9. 测试重点

- 固定输入下，预算计算与推荐资格不受模型措辞影响。
- 两条语义相同但标题不同的路线不能同时通过差异校验。
- 模型减少成功标准后生成的候选被分离为约束调整建议。
- 用户微调产生新 route 记录并保留 based_on_route_id。
- profile 或 availability revision 变化使晚返回结果失效。
- 预算不足、只有一条可行和全部不可行均返回不同的结构化结果。
- OpenAI 与 Anthropic 适配器返回相同内部 schema，领域与预算校验共用同一实现。
