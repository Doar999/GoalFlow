# 领域策略包与约束校验实现基线 v0.1

状态：实施设计建议；承接已确认产品决策 [三类领域专业规则与责任边界](../product/11-domain-rules.md)。产品规则不可在本层放宽；下述结构、字段与校验点为实施建议，实现前按 [决策规程](../engineering/00-workflow.md) 确认。

原则：硬约束必须表达为可执行谓词，由服务端在模型输出后判定；策略包与提示模板同属一个版本号，进入每个生成结果的输入快照。

## 1. 策略包结构

```python
class ConstraintSpec(BaseModel):
    id: str                      # 稳定标识，用于错误码与测试引用
    scope: Literal["route", "plan", "task_batch", "agenda", "verification"]
    severity: Literal["reject", "warn"]
    message: str                 # 面向用户的冲突说明

class DomainPolicy(BaseModel):
    domain: Literal["learning", "fitness", "general"]
    policy_version: str
    prompt_template_version: str
    clarification_fields: list[ClarificationField]
    task_types: list[str]
    hard_constraints: list[ConstraintSpec]
    soft_preferences: list[str]   # 只进提示，不参与判定
    verification_defaults: VerificationDefaults
    adjustment_bounds: AdjustmentBounds
    blocked_task_types: dict[str, list[str]]   # 限制标识 -> 被阻断的任务类型
```

谓词实现放在代码里并以 `id` 与 `ConstraintSpec` 关联，配置只声明启用哪些约束及其参数；不把判定逻辑写进 JSON 表达式语言，避免产生一个无法测试的迷你解释器。

`soft_preferences` 与 `hard_constraints` 必须在类型层面分离，防止软偏好被误当成拒绝理由。

## 2. 校验点

同一份策略包在四个阶段被读取，各自只校验属于自己 `scope` 的约束：

| 阶段 | 校验内容 | 现有衔接 |
| --- | --- | --- |
| 路线生成 | 阶段划分、证据类型、安全边界 | [路线引擎](11-route-engine.md) 第 32 行已要求领域策略校验 |
| 计划生成 | 里程碑验收方式、诊断阶段位置、依赖无环、任务时长上限 | [计划引擎](12-plan-engine.md) |
| 任务批次 | 复习任务的引用完整性、复习间隔、判分标准齐备 | [计划引擎](12-plan-engine.md) 滚动细化 |
| 每日安排 | 恢复日间隔、周训练量增幅、同肌群连续日 | [排期引擎](13-scheduling-engine.md) |
| 验证 | 掌握阈值、允许的材料类型与依据 | [辅助与验证实现基线](15-assistance-and-verification-design.md) |

拒绝路径统一：模型输出违反 `severity=reject` 的约束时，附带 `constraint_id` 与 `message` 有限次重试；重试耗尽后返回约束冲突交用户取舍，不降低标准也不无限重试。这沿用 [Agent 工作流](04-agent-workflows.md) 第 72 节的修复次数限制。

## 3. 阻断集合

健康限制的处理与能力门控同构，但不可混用：能力缺失影响"能否验证"，健康限制影响"能否生成"。

1. 澄清阶段把用户自述的限制归一化为限制标识，只保留与执行安全直接相关的项。
2. `blocked_task_types` 把限制标识映射到被禁止的任务类型，形成本次生成的阻断集合。
3. 阻断集合进入生成输入快照，并在模型返回后做一次独立过滤：命中即拒绝整个候选，不做"删掉违规任务后继续使用"的局部修补。
4. 风险信号（疼痛、孕期、术后、心脏或血压问题等自述）触发范围收窄标记，进入档案与计划的展示项，不作为目标阻断。

归一化不得推断未声明的医学结论；用户没说的不写入限制标识。

## 4. 排期侧的领域约束

恢复日与训练量约束落在排期层，必须与已确认的排期规则一致：

- 恢复日是**允许为空的安排**，不是缺失安排。排期器不得因为当天存在剩余容量而追加训练任务，这与"不能仅因空闲时间增加自动追加任务"同源。
- 周训练量增幅按上一自然周的有效实际投入或已排投入计算，取哪一个属于策略参数，需在实现前定死一种，不允许两处不同算法。
- 领域约束不能越过共享时间预算：与其他目标争用容量时仍按已确认的分层优先级处理，领域策略不获得额外配额。

## 5. 版本化与迁移

`policy_version` 写入每个生成物的输入快照，复用数据模型已有的 `generation_policy_version` 与 `scheduling_policy_version` 字段，不新增机制。

- 策略包升级不追溯已生成内容；已有计划保留其生成时的版本，直到产生新版本。
- 主领域迁移只改变后续生成使用的策略；计划已启用时按 `confirmation_required` 或 `profile_revision_required` 进入变更提案，由 [反馈调整引擎](14-feedback-adjustment-engine.md) 第 5 节判级。
- 次要特征只写入档案事实字段并进入提示上下文，不参与 `hard_constraints` 选择，因此不产生策略组合。

## 6. 提示模板绑定

每个领域的对话、路线、计划、任务细化、复盘各有独立提示模板，模板版本与 `policy_version` 一同记录到调用日志。软偏好由模板注入，硬约束不依赖模板生效——模板改写不能削弱判定。

## 7. 测试重点

- 三套主领域各自的硬约束单元测试，按 `constraint_id` 逐条覆盖，包括边界值（恰好 90 分钟、恰好 10% 增幅）。
- 基线未知的学习目标，计划必然在首个里程碑前包含诊断任务。
- 复习任务缺少被复习单元引用时被拒绝。
- 健康限制命中时整个候选被拒绝，而不是删除违规任务后放行。
- 用户未声明的限制不会被推断出来写进阻断集合。
- 恢复日为空清单时排期器不填入训练任务，即使当天预算充足。
- 软偏好被违反时不产生拒绝，也不进入验收条件。
- 策略包升级后，已启用计划的阶段与里程碑不被追溯改写。
- 计划启用后触发领域迁移，产生待确认提案而非自动重排；进行中与已完成任务不被改写。
- 同一目标、同一策略版本，在两种不同模型配置下生成的计划骨架与硬约束判定结果一致。
