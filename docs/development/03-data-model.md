# 核心数据模型 v0.1

状态：依据已确认产品方向制定的逻辑设计，供实现与评审使用；字段和状态为设计建议，不代表数据库兼容性已验证。数据库为 seekdb Server；正式 DDL 在目标版本兼容性验证后生成。

## 1. 统一约定

- 应用生成 UUID，候选存储类型 CHAR(36)；分钟数用整数，业务日期用 DATE，事件时间以 UTC 保存并通过用户 IANA 时区显示。DATETIME(6) 和 JSON 等物理类型须实测。
- 用户私有表包含 owner_id；子对象必须与父对象同属一个用户。使用显式归属条件查询，后台执行也必须检查；仅知道记录 ID 不构成访问权限。
- 可变聚合使用 revision 整数进行并发控制；更新携带 expected_revision，过期返回冲突，不直接覆盖。
- 状态建议使用短字符串和后端枚举；唯一约束负责持久化去重。外键、CHECK 和迁移支持需实测，但归属、无环、状态转换等业务校验始终在服务端执行。
- 以下表省略常见 created_at、updated_at；不可变版本只有 created_at。JSON 用于领域扩展与快照，核心关联、日期、状态保持独立字段。

## 2. 账号与目标

| 表 | 关键字段 | 约束或用途 |
| --- | --- | --- |
| users | id, account_identifier, role, timezone, status | identifier 规范化后唯一；凭证另表保存 |
| password_credentials | user_id, password_hash, hash_scheme, changed_at | 不存明文或可逆密码；推荐 Argon2id |
| sessions | id, user_id, token_hash, last_seen_at, expires_at, revoked_at | 原始令牌不落库；请求验证有效性与撤销状态 |
| password_reset_tokens | id, user_id, token_hash, issued_by, expires_at, consumed_at | 一次性、短时有效；首版由管理员或本地命令签发 |
| goals | id, owner_id, title, domain, domain_confidence, kind, status, isolation_mode, active_profile_id, current_plan_version_id, revision | domain 是 Agent 的内部策略路由，不是用户必填标签；kind 为 achievement/maintenance 且由档案时间边界推导（已确认）；一个目标最多一个当前执行版本。生命周期字段建议见 [目标生命周期与结束实现基线](17-goal-lifecycle-design.md) 第 6 节 |
| goal_profile_drafts | id, owner_id, goal_id, revision, content_json, source_map_json, gaps_json, assumptions_json, contradictions_json, readiness | 每个目标一个当前草稿；保存澄清进度，readiness 为 needs_input/review_ready/blocked |
| goal_profiles | id, owner_id, goal_id, version_no, result_definition, success_criteria_json, baseline_json, constraints_json, facts_json, confirmed_at | (goal_id, version_no) 唯一；用户事实、假设、建议分别标注来源与确认状态 |
| goal_links | id, owner_id, goal_a_id, goal_b_id, status, confirmed_at | 规范化目标对唯一；不允许自关联或跨用户关联；status 为 proposed/active/removed，不设软失效状态 |
| planning_sessions | id, owner_id, goal_id, state, profile_draft_id, confirmed_profile_id, selected_route_id, draft_plan_id, current_job_id, revision | 保存澄清到启用的交互进度，等待用户不占用 Worker |
| route_sets | id, owner_id, goal_id, profile_id, planning_revision, availability_revision, generation_policy_version, input_snapshot_json, input_hash, status, invalidated_reason | 一组可比较路线；绑定目标档案、策略及预算快照 |
| routes | id, owner_id, route_set_id, based_on_route_id, status, title, approach, difference_keys_json, duration_range_json, phase_outline_json, weekly_minutes, tradeoffs_json, risks_json, assumptions_json, resources_json, derived_metrics_json | 修改生成新变体或新集合，不覆盖已被计划引用的路线；派生指标由服务端计算 |

goal_links 表示用户允许协作，不自动产生任务依赖；实际依赖由任务依赖边表达。切换为独立推进前先处理活动依赖，不能只改标签而遗留跨目标依赖。

领域推断不足时使用 general 并继续收集会影响计划的事实。领域变化写入审计并由新档案/计划版本承接；不得要求用户为了修正计划而理解内部分类。历史计划通过 strategy_version 保留当时采用的规则。

goal_profile_drafts 是可修改的交互状态，goal_profiles 是用户确认后的不可变业务版本。确认时在同一事务中重新检查阻断项、创建 goal_profiles、更新 goals.active_profile_id 和 planning_sessions.confirmed_profile_id。模型晚返回时必须匹配草稿 revision，不能覆盖用户刚完成的直接编辑。

## 3. 计划、任务与版本

| 表 | 关键字段 | 约束或用途 |
| --- | --- | --- |
| plan_versions | id, owner_id, goal_id, version_no, profile_id, route_id, based_on_version_id, status, start_date, horizon_end, detailed_through_date, strategy_version, reason | (goal_id, version_no) 唯一；战略结构不可变；status: draft/active/superseded/discarded |
| plan_phases | id, owner_id, plan_version_id, phase_key, rank, title, outcome, exit_criteria_json, duration_estimate_json | (plan_version_id, phase_key) 唯一；保存完整阶段结构 |
| plan_milestones | id, owner_id, plan_version_id, phase_id, milestone_key, rank, title, success_criteria_json, target_window_json | (plan_version_id, milestone_key) 唯一；表达可检查的阶段结果 |
| task_batches | id, owner_id, plan_version_id, window_start, window_end, input_progress_revision, generation_policy_version, status, reason | 同一计划、窗口和进度输入只产生一个有效批次；正常滚动不创建新计划版本 |
| tasks | id, owner_id, goal_id, execution_status, remaining_minutes_estimate, progress_revision, revision | 稳定的任务身份及当前执行投影；重排和计划修订不复制完成记录 |
| task_specs | id, owner_id, task_id, spec_no, title, instructions, completion_criteria_json, executor, expected_minutes, minimum_minutes, maximum_minutes, estimate_confidence, earliest_date, latest_date, can_split, minimum_session_minutes, verification_policy | (task_id, spec_no) 唯一；不可变内容版本；minimum <= expected <= maximum；latest_date 非空，空值等于默认允许无限延期 |
| plan_task_memberships | id, owner_id, plan_version_id, task_batch_id, phase_id, milestone_id, task_id, task_spec_id | (plan_version_id, task_id) 唯一；任务必须属于阶段，里程碑引用可选 |
| task_dependencies | id, owner_id, plan_version_id, predecessor_task_id, successor_task_id, required_outcome, goal_link_id | 一个版本内边唯一；required_outcome 区分执行完成和验证通过 |
| change_proposals | id, owner_id, goal_id, base_versions_json, input_revision, proposed_patch_json, impact_json, change_class, status, reason, accepted_at, applied_at | status: pending/applied/rejected/stale；接受和应用在同一事务完成 |

计划版本固定阶段、里程碑和核心依赖；正常滚动展开只追加 task_batch。任务内容更新创建 task_spec；每日顺序变化只创建每日安排版本。阶段、里程碑、核心方法或既有核心依赖变化才创建新计划版本。

未启用计划中的新任务为 proposed，不出现在正式待办；启用后成为 pending。进行中或已完成任务不得因新计划被改写。替换未开始任务需显式取消旧任务并记录原因，不能让历史记录失去所属任务。修订中未变化的任务复用原身份和规格。

跨目标依赖仅在已确认关联下建立。校验拟启用版本与其他目标当前版本形成的完整依赖图，不能只检查单个目标。依赖边由后继任务所属计划管理，前驱任务在被引用期间禁止物理删除。

## 4. 时间预算与每日安排

| 表 | 关键字段 | 约束或用途 |
| --- | --- | --- |
| user_planning_state | owner_id, revision | 每用户一个协调入口；所有影响排期的写操作都更新它 |
| availability_versions | id, owner_id, effective_from, weekly_minutes_json, version_no | 周一至周日七个额度；按生效日期选择版本 |
| daily_overrides | id, owner_id, local_date, override_kind, minutes, measured_at, revision | (owner_id, local_date) 唯一；kind: total/remaining，防止把剩余时间误当全天时间 |
| goal_scheduling_preferences | id, owner_id, goal_id, focus_status, rank, revision | (owner_id, goal_id) 唯一；默认同等优先，focus/rank 只参与弹性任务取舍 |
| task_day_constraints | id, owner_id, task_id, local_date, constraint_kind, status, revision | (owner_id, task_id, local_date) 唯一；kind: must_do_today/locked |
| daily_agendas | id, owner_id, local_date, timezone_snapshot, current_revision_id, revision | (owner_id, local_date) 唯一；稳定的每日页面身份 |
| agenda_revisions | id, owner_id, agenda_id, version_no, input_planning_revision, scheduling_policy_version, capacity_snapshot_json, deferred_json, conflict_json, status, reason | (agenda_id, version_no) 唯一；status: ready/conflicted；保留旧安排 |
| agenda_items | id, owner_id, agenda_revision_id, task_id, task_spec_id, rank, allocated_minutes, reason_codes_json | (agenda_revision_id, task_id) 唯一；首版没有起止时刻 |

首版可拆分任务跨日期分配，但一天一个任务只对应一个安排项。每日分配不改变任务总完成标准；周期性重复练习按日期生成不同任务实例，避免一次打卡完成所有重复任务。

实际投入来自反馈记录；缺失时保留 unknown，并以明确标记的估算辅助排期。全天额度模式下，剩余容量为 max(0, 全天额度 - 今日已投入)；剩余额度模式下，从用户声明时点起扣除后续投入。不能把同一段投入扣两次。

排期约束：进行中任务剩余投入 + 未开始任务分配量 <= 当前剩余容量。若固定事项或进行中任务已超过额度，保存真实冲突，不缩写历史耗时或强制中断任务。后台模型等待时间不直接计入用户投入。计划首周预算仅为预览，启用时重新评估。

## 5. 执行、材料与对话

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| checkins | id, owner_id, task_id, task_spec_id, local_date, outcome, actual_minutes_delta, remaining_minutes_after, difficulty_code, note, supersedes_id, client_request_key | 追加式反馈；outcome 含 completed/partial/deferred_today/blocked；更正追加记录，统计取有效链末端 |
| task_blockers | id, owner_id, task_id, source_checkin_id, blocker_kind, description, status, resolved_at | 持久阻碍单独管理；开放阻碍参与 blocked 计算 |
| verification_results | id, owner_id, task_id, task_spec_id, checkin_id, status, basis_json, policy_version, evaluator, reason | status: pending/passed/not_met/insufficient/error；记录实际检查依据 |
| artifacts | id, owner_id, task_id, kind, storage_key_or_url, source_json, status | 私有附件、用户产出或 Agent 辅助产物；下载检查权限 |
| checkin_artifacts | owner_id, checkin_id, artifact_id | 明确哪份反馈引用哪个材料 |
| conversations | id, owner_id, goal_id, task_id, revision | 可选关联目标或任务，保留明确范围 |
| messages | id, owner_id, conversation_id, role, content, job_id, client_request_key | 确定顺序及防重复提交 |
| context_summaries | id, owner_id, conversation_id, through_message_id, summary, source_revision | 可重建摘要，不作为目标事实的唯一来源 |
| daily_reviews | id, owner_id, local_date, version_no, day_rating, estimate_feedback, constraint_note | 可跳过的日级回顾；(owner_id, local_date, version_no) 唯一 |

用户完成练习但得分不足时，可以 execution_status=completed，同时验证结果 not_met。只要求练习完成的后续任务可以继续；要求掌握度达标的依赖继续阻塞。用户可更正误操作；更正若影响已启动的后继任务，需要提示和调整，不能删除后继历史。

## 6. 后台作业与审计

| 表 | 关键字段 | 约束或用途 |
| --- | --- | --- |
| jobs | id, owner_id, kind, dedupe_key, input_refs_json, input_revision, status, attempts, lease_token, lease_until, cancel_requested_at, result_refs_json, error_code | (owner_id, kind, dedupe_key) 唯一；不把 Celery ID 当业务唯一身份 |
| job_events | id, owner_id, job_id, sequence, event_type, payload_json | (job_id, sequence) 唯一；SSE 补读持久化阶段事件 |
| outbox_events | id, owner_id, job_id, event_type, status, next_attempt_at, attempts | 与业务作业同事务写入；重复投递由 jobs 去重 |
| model_calls | id, owner_id, job_id, provider, model, prompt_version, input_tokens, output_tokens, estimated_cost, status, latency_ms | 用量统计；缺失用量不记作零；不记录密钥 |
| idempotency_requests | id, owner_id, operation, request_key, request_hash, result_ref | 同 key 不同请求体返回冲突；相同请求返回原结果 |
| audit_events | id, owner_id, actor_kind, action, entity_type, entity_id, before_revision, after_revision, reason, request_id | 追踪自动调整、用户确认、关联变更及管理操作 |

索引优先覆盖 owner_id + status、owner_id + local_date、goal_id + version_no、conversation_id + 顺序、job_id + sequence，以及待分发状态 + next_attempt_at。外部输入长度、索引长度及唯一键设计在迁移验证中检查。

## 7. 事务与并发协议

计划启用、任务反馈、时间额度修改及排期提交均在短事务中按相同顺序获取 user_planning_state，再获取目标/任务记录；或者使用实测支持的条件更新方案。所有影响排期的写路径必须遵循同一协议，否则不能保证预算一致性。

模型调用在事务外进行。事务中重新检查 planning revision、目标版本、权限和依赖，过期建议返回 stale 并重算。启用事务同时切换当前计划、更新任务及每日安排、递增 revision 并写审计/outbox；任一步失败整体回滚。

Worker 领取作业生成 lease_token，心跳续租。提交结果必须匹配仍有效的租约及运行状态；旧 Worker 即使晚返回也不能提交。过期租约由恢复扫描处理，重试生成新令牌。取消与成功提交采用条件更新竞争，不能在取消已生效后发布结果。

## 8. 实现前验证清单

seekdb 目标版本需验证：SQLAlchemy 连接/反射、Alembic 建表升级、唯一约束、JSON、日期时间、条件更新行数语义、行锁与隔离、事务回滚、死锁错误识别、备份恢复。不得假定 PostgreSQL JSONB、部分唯一索引、事务 DDL 或 SKIP LOCKED 可用。当前文档不提供未经验证的生产 DDL。

完整用例清单、判定标准、每项的替代实现及红线定义见 [T01 交接卡](../worklog/T01-seekdb-verification.md)。红线为复合唯一约束、事务回滚、并发预算更新与恢复演练，任一失败走 RFC 重新评估选型；其余项按替代实现推进并回写本文。验证套件长期保留并在升级 seekdb 或更换驱动时重跑。
