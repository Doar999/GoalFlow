# 执行反馈、验证与调整引擎 v0.1

状态：首版实现基线已确认。写入执行事实不依赖模型；验证和复杂调整通过可恢复作业异步完成。

## 1. 模块边界

```python
record_checkin(command: RecordCheckinCommand) -> CheckinResult
evaluate_adjustment(snapshot: AdjustmentSnapshot) -> AdjustmentDecision
apply_automatic_adjustment(decision: AdjustmentDecision) -> AppliedChange
propose_major_change(decision: AdjustmentDecision) -> ChangeProposal
```

执行记录模块负责追加事实和更新任务当前投影；验证模块负责判断材料是否达到预先定义的标准；调整判级模块依据计划包络决定自动应用或等待确认。模型只能产生验证候选或变更候选，不能直接改变任务、计划和每日安排。

## 2. 写入反馈

`record_checkin` 使用 Idempotency-Key 和 task revision，在短事务中：

1. 校验任务归属、当前 task_spec、允许的状态转换和材料引用。
2. 追加 checkin，不覆盖旧记录。
3. 更新 tasks 上的当前执行投影、剩余分钟估算和 progress revision。
4. 创建必要的 verification job 和 outbox 事件。
5. 递增 user_planning_state revision，触发日程重算和调整评估。

接口立即返回已保存的 checkin、执行状态及可选 verification_job_id。模型或 Worker 失败不能回滚已成功写入的用户事实。

更正使用 supersedes_id 追加记录。投影按有效更正链重建；同一 client_request_key 只产生一个有效反馈。

## 3. 阻碍与今日延期

`blocked` 不作为覆盖执行状态的持久枚举，而由未满足依赖、开放 task_blocker 和能力不可用共同计算。用户报告阻碍时创建或更新 task_blocker；解决后关闭阻碍并触发排期。

“今天做不了”写入 outcome=`deferred_today`，关联 local_date 和原因；任务保持 pending 或 partial。排期器只在原 earliest/latest 窗口内自动移动，超过窗口进入重大变更建议。

## 4. 验证流水线

verification job 读取固定 task_spec、checkin、允许的材料和 policy_version。先检查材料可读性，再运行确定性或模型评估，输出 `passed`、`not_met`、`insufficient_evidence` 或 `error`，并保存 basis 和理由。

上传材料和链接内容都是不可信数据，不能改变系统指令、权限或工具范围。验证只依据任务开始前可见的 completion criteria 和 verification policy。结果提交前重新检查 checkin 未被更正、task_spec 未过期且 Worker 租约有效。

验证结果会再次触发 adjustment evaluation。需要 verification passed 的依赖只有在有效结果达标后解锁。

## 5. 调整判级

`AdjustmentSnapshot` 包含触发记录、当前 plan/profile/route、任务状态、里程碑、共享预算、日期范围、用户锁定、关联目标和领域策略版本。规则层计算：

- 是否改变目标或成功标准。
- 是否改变阶段、里程碑或硬期限。
- 是否增加已批准周投入或挤占其他目标。
- 是否移动进行中、已完成或锁定任务。
- 是否仍在任务日期范围和同一里程碑内。
- 是否属于领域策略允许的补救/替代动作。

输出 `record_only`、`auto_schedule`、`auto_plan_local`、`confirmation_required` 或 `profile_revision_required`。模型给出的 change_class 只作候选，服务端必须重新判定。

## 6. 自动应用和建议

A1 调整通过排期接口创建 agenda revision。A2 调整对未开始任务创建新 task_spec、新任务或显式 cancellation 记录，并写 audit；总预计投入必须仍在批准包络内。进行中/已完成任务永不由 A2 替换。

A3/A4 写入 change_proposals，包含 base versions、结构化 patch、受影响目标/任务、投入差异、期限差异、原因和不确定项。用户接受时重新检查所有版本并原子应用；过期返回 stale。

相同触发、相同基础版本和相同规范化提案使用稳定去重键。被拒提案不能由同一输入自动重新创建。

## 7. 连续问题和估算校准

同一任务最近两条有效 checkin 均为 partial、deferred_today 或 blocked 时，产生 `REPEATED_FRICTION`，优先请求拆分、说明阻碍或求助，不生成重复任务。

估算校准只使用同一用户、同一领域和可比较任务类型的有效实际耗时。建议至少三条样本后计算稳健比例，并限制单次校准幅度；具体算法属于版本化 estimation policy。校准仍需满足计划包络，不能借此静默增加周投入。

## 8. API 衔接

- `POST /api/tasks/{id}/checkins`：完成、部分完成、今日延期、遇阻和材料提交。
- `POST /api/checkins/{id}/corrections`：追加更正记录。
- `POST /api/tasks/{id}/blockers/{blocker_id}/resolve`：解决持久阻碍。
- `GET /api/tasks/{id}/verification-results`：读取独立验证状态和依据。
- `POST /api/change-proposals/{id}/accept|reject`：处理重大调整。
- `PUT /api/reviews/{date}`：可选每日回顾，重复提交更新当前版本但保留审计。

## 9. 测试重点

- 模型服务超时时，checkin 仍已保存且可以稍后验证。
- 重复提交、网络重试和更正链得到唯一有效执行投影。
- completed/not_met 并存，依赖分别按 execution completed 或 verification passed 判断。
- deferred_today 不取消任务，且不能越过 latest_date 自动移动。
- A2 调整超过周投入包络时升级为 confirmation_required。
- 旧 verification Worker 不能提交到已更正的 checkin。
- 被拒提案不会由相同输入再次自动创建。
