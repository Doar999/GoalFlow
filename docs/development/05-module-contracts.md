# 模块契约与开发衔接 v0.1

状态：建议契约，供多人协作对齐；不是已实现接口。字段 schema 将由 Pydantic/OpenAPI 落地，前端基于同一契约维护 TypeScript 类型。

## 共同规则

请求身份从服务端会话获得；路径中的对象 ID 全部检查归属。写操作使用 Idempotency-Key；修改现有状态同时提交 expected_revision。相同 key 不同内容返回冲突，重新评估后的新意图使用新 key。

同步成功返回实体及 revision；异步成功返回 HTTP 202 和 job_id；重复异步请求返回原作业引用。错误统一包含 code、message、request_id、retryable 和必要的 details，不暴露其他用户数据。常见错误包括 REVISION_CONFLICT、BUDGET_CONFLICT、DEPENDENCY_CYCLE、CONFIRMATION_REQUIRED、INPUT_STALE、MODEL_UNAVAILABLE。

## 模块 Interface 与 HTTP 映射建议

| 模块操作 | HTTP 入口 | 主要输入 | 输出/业务保证 |
| --- | --- | --- | --- |
| create_goal | POST /api/goals | 初始目标描述 | draft 目标与 planning_session；领域策略由服务端推断，不接收用户分类作为业务事实 |
| submit_message | POST /api/conversations/{id}/messages | 文本、允许的附件引用、revision | 已持久化用户消息及回答 job_id |
| read_profile_draft | GET /api/goals/{id}/profile-draft | 无 | 草稿字段、来源、缺口、假设、矛盾及 readiness |
| update_profile_draft | PATCH /api/goals/{id}/profile-draft | 用户编辑、expected_revision | 新草稿 revision；基于旧档案的预览标记 stale |
| confirm_profile | POST /api/goals/{id}/profile-confirmations | 草稿档案、expected_revision | 不可变已确认档案版本 |
| generate_routes | POST /api/goals/{id}/route-generations | profile_id、expected_revision | 路线生成作业 |
| read_current_routes | GET /api/goals/{id}/route-sets/current | 无 | 当前路线集合、统一比较字段、派生冲突和 stale 状态 |
| revise_route | POST /api/goals/{id}/route-variants | route_id、微调要求、expected_revision | 校验后的路线变体或需重新生成集合的结果 |
| select_route | POST /api/goals/{id}/route-selection | route_id、微调要求、expected_revision | 选择结果；需重新生成时返回作业引用 |
| generate_plan | POST /api/goals/{id}/plan-generations | 最终 route_id、start_date、expected_revision | 草稿生成作业，不启用 |
| read_plan_draft | GET /api/goals/{id}/plan-drafts/current | 无 | 阶段、里程碑、七日任务批次、冲突和 stale 状态 |
| request_plan_change | POST /api/goals/{id}/plan-change-requests | 结构化或自然语言修改、expected_revision | 局部更新、重排或新计划草稿作业 |
| activate_plan | POST /api/goals/{id}/activation | draft_plan_id、expected_revision、planning_revision | 当前计划与今日页引用；预算冲突不部分启用 |
| read_today | GET /api/agendas/{date} | 本地日期 | 当前安排或缺失状态；读取不直接创建作业 |
| ensure_agenda | POST /api/agendas/{date}/generation | planning_revision | 已有有效安排或去重生成作业 |
| update_goal_priorities | PUT /api/goals/scheduling-preferences | focus 状态、目标顺序、expected_revision | 新 planning revision；触发受影响安排重算 |
| constrain_today_task | PUT /api/agendas/{date}/task-constraints/{task_id} | must_do_today/locked/clear、expected_revision | 保存约束或返回容量冲突 |
| update_availability | PUT /api/availability | 周额度、生效日期、expected_revision | 保存额度；已有安排需要协调时明确标记 |
| override_today | PUT /api/availability/dates/{date} | total/remaining、分钟数、expected_revision | 保存用户声明并触发协调；不足时返回可见冲突 |
| record_checkin | POST /api/tasks/{id}/checkins | outcome、耗时增量、材料引用、expected_revision | 有效记录、任务状态及可选验证作业 |
| correct_checkin | POST /api/checkins/{id}/corrections | 更正内容、expected_revision | 新记录和重建后的有效执行投影 |
| resolve_blocker | POST /api/tasks/{id}/blockers/{blocker_id}/resolve | 解决说明、expected_revision | 关闭阻碍并触发安排重算 |
| accept_change | POST /api/change-proposals/{id}/accept | expected_revision | 校验后原子应用，或返回 stale |
| reject_change | POST /api/change-proposals/{id}/reject | 原因可选、expected_revision | rejected，当前计划不变 |
| inspect_job | GET /api/jobs/{id} | 无 | 作业与最终产物引用 |
| watch_job | GET /api/jobs/{id}/events | Last-Event-ID | 当前用户的持久化事件流 |
| cancel_job | POST /api/jobs/{id}/cancellation | expected_revision | 取消请求状态；不能撤销已成功发布的结果 |

当前表覆盖核心执行链。注册登录、附件上传、目标关联、暂停恢复和历史检索在各模块实施前补全 OpenAPI；不得因本表未展开而视为已实现或从首版删除。

账号接口及验收见 [账号设计](08-auth-design.md)。T03 按已确认的自由注册和服务端会话 Cookie 实施，不引入 localStorage 长期 JWT。

## 内部计算 Interface

排期模块 expose calculate_agenda(snapshot) → arrangement_or_conflicts。快照含 planning_revision、日期/时区、当前计划版本、任务规格与执行状态、预算、目标调度偏好、用户锁定和约束。该计算不修改数据库，也不调用模型；缺少任务内容交回 Agent 模块补齐。具体原因码和冲突见开发文档 13。

计划模块 expose apply_change(actor, proposal, expected_versions) → applied_or_conflict。封装权限、变更判级、确认、锁定、版本写入与审计。HTTP 路由、后台执行和模型工具均只能通过这条业务路径提交变化。

Agent 模块 expose run_step(step_kind, input_snapshot) → validated_candidate。内部由对应的 LangGraph 图执行，图在本次调用内跑完即结束，不持有跨调用状态。返回结构化候选或可解释错误，不返回任意 SQL，也不自行提交计划。调用方不感知图的节点划分，换图不改本契约。

## 分工与合并顺序

1. 先完成共享枚举、错误契约、数据库迁移基础及鉴权上下文，由指定负责人维护。
2. 账号/数据基础、计划与排期、Agent/作业、前端体验可分别领取任务；人数不足时合并角色。未指定实际人员，不在此自动创建开发任务或启动代理。
3. 前端先用契约样例开发今日页与计划预览；后端先用固定模型输出验证事务和状态，再接真实模型。
4. 公共 schema 或错误码变更同时更新前端类型及契约测试；跨模块不得直接改对方状态表以绕过 Interface。
5. 首条集成验收链：注册登录 → 配置模型 → 创建目标 → 档案确认 → 路线选择 → 草稿检查 → 启用 → 今日任务 → 反馈。之后增加多目标冲突、修订与恢复场景。

## 默认值状态

下列默认值均已确认，实现时直接采用，不需要另行选择，也不需要 RFC：

| 项 | 结论 | 出处 |
| --- | --- | --- |
| 可移动日期范围 | `earliest_date`/`latest_date` 由计划生成时推导，`latest_date` 非空 | [目标关联、依赖与解除规则](../product/13-goal-links.md) 第 5 节；推导顺序见 [18-goal-link-design.md](18-goal-link-design.md) 第 4 节 |
| "明显增加投入"阈值 | 相对最近一次批准的周投入基线，累计增量同时超过 10% 与 30 分钟；另有使共享周预算超额的调整一律确认 | [13-goal-links.md](../product/13-goal-links.md) 第 6 节；参数见 [18-goal-link-design.md](18-goal-link-design.md) 第 5 节 |
| 提醒时机 | 首版不做主动通知，只做状态呈现；不请求浏览器通知权限、不做 Web Push 与邮件提醒 | PRD D11；[今日执行页](../product/01-today-page.md)"状态呈现与提醒范围" |
| 路线数量、开始日期、滚动细化、目标优先级 | 已确认 | 产品文档 02、03、08 |

仍需在实现前收敛的只剩 OpenAI / Anthropic 具体 API 子集与模型能力门槛，见 [开发任务与验收](06-delivery-plan.md) 第 6 节。
