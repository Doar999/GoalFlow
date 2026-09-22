# 辅助能力、材料解析与验证实现基线 v0.1

状态：实施设计建议；承接已确认产品决策 [Agent 辅助能力与证据验证范围](../product/10-agent-assistance-and-verification.md)（PRD D09、D10）。产品规则不可在本层放宽；下述接口、默认值与算法为实施建议，实现前按 [决策规程](../engineering/00-workflow.md) 确认。

原则：确定性路径优先，模型只产生候选；材料与链接内容全程视为不可信数据；能力缺失是可预期状态，不是系统故障。

## 1. 模块边界

```python
register_artifact(command: RegisterArtifactCommand) -> ArtifactRef
extract_material(artifact_id: UUID) -> ExtractionResult
fetch_link(url: str, policy: FetchPolicy) -> LinkSnapshot
run_verification(job: VerificationJob) -> VerificationResult
run_assistance(command: AssistanceCommand) -> AssistanceArtifact
```

材料模块负责登记、解析和可读性判定；抓取模块负责受限出站；验证模块负责按冻结策略逐条判定并由代码聚合；辅助模块负责生成 Agent 产出。四者都不直接修改任务、计划或每日安排。

## 2. 上传与存储

两阶段提交：`POST /api/artifacts/uploads` 申请（校验声明类型、大小、账号配额）→ 直传私有对象存储或经后端流式转存 → 确认登记后写入 `artifacts`。未确认的上传按过期清理，不产生可引用材料。

- **真实类型嗅探（magic bytes）与扩展名双校验**，一律不信任客户端 `Content-Type`。二者不一致即拒绝。
- 存储 key 随机生成，不使用原文件名构造路径；原文件名仅作展示字段并转义输出。
- 下载必须校验 `owner_id` 与任务归属，使用短有效期签名 URL，响应固定 `Content-Disposition: attachment`，不内联渲染。
- 拒绝 `.svg`、`.html` 及任何可在浏览器执行脚本的类型。首版不做病毒扫描，需在部署文档显式声明。
- 配额检查在申请阶段和确认阶段各做一次，避免并发绕过；总容量按账号统计。

`artifacts` 建议补充字段：`mime`、`byte_size`、`sha256`、`origin`（`user_upload` / `agent_output` / `link_snapshot`）、`extraction_status`、`extraction_reason`、`model_ref`。**表结构变更走契约 PR，同步更新 [核心数据模型](03-data-model.md)**，不在本模块内私自扩展。

## 3. 解析流水线

在独立 Worker 中执行，带超时与内存上限，不在请求线程解析：

1. 文本类直接读取并按字符上限截断。
2. PDF 先抽取文本层（确定性库）。每页有效字符低于阈值（建议 100）视为无文本层。
3. 无文本层的 PDF 与图片进入图片路径，由能力门控决定能否送模型。
4. 不解析 PDF 内嵌脚本、附件流与外部引用；不跟随文档内链接。

上限在此层统一执行：字节数、PDF 页数、抽取字符数。超限时截断并记录 `extraction_reason=size_exceeded`，不静默丢弃。

## 4. 能力门控

能力位建议：`vision`、`structured_output`、`web_search`。来源为用户模型配置的连接测试结果与手动标记，按配置持久化（见 [用户模型接入设计](07-model-provider-design.md)）。

三处使用同一份能力视图：

| 时点 | 行为 |
| --- | --- |
| 任务生成 | 能力不可用时不生成依赖该能力的验证策略，改用可执行的替代标准 |
| 任务开始前 | 展示本任务需要的提交形式与当前能力限制 |
| 验证执行 | 门控实际调用；缺失能力直接产出 `insufficient_evidence` |
| 默认配置切换 | 重算能力视图；**不改写已冻结的 `verification_policy`**，受影响的未开始任务在"任务开始前"提示中显示当前模型不满足其提交形式 |

能力缺失的结果是 `insufficient_evidence` 加对应原因码，**不是 `error`，不进入自动重试**。

用户换用能力更少的模型（例如从支持读图换到不支持）时，已冻结的验证策略保持不变，只是如实降级：验证执行时门控命中，产出 `insufficient_evidence` + `vision_unavailable`。不追溯改写策略，是因为"完成要求在任务开始前可见、不在提交后变动"是已确认的产品规则（见 [今日执行页](../product/01-today-page.md) 的反馈细则）；换模型不该成为事后提高或降低标准的入口。用户的出路是换回有该能力的配置，或按第 6 节改为提交文字说明转入 `self_report`。

## 5. 链接抓取安全

- 仅 `http`/`https`。DNS 解析后逐个候选 IP 校验，拒绝回环、私有网段、链路本地、CGNAT 及云元数据地址（`169.254.169.254`、`::1`、`fc00::/7` 等），IPv4 与 IPv6 同等处理。
- **连接时 pin 已校验通过的 IP**，防止 DNS rebinding；最多 3 跳重定向，每跳重新执行全部校验。
- 超时 10 秒，响应体上限 2 MB（流式读取并在超限处中断），仅接受 `text/html`、`text/plain`、`application/pdf`。
- 不执行 JavaScript，不加载子资源；正文抽取使用确定性库。
- 保存 `LinkSnapshot`：抽取文本、`fetched_at`、`http_status`、`final_url`、`content_hash`，`origin=link_snapshot`。验证结论引用快照而非实时重取。
- 出站开关为实例级环境变量，默认开启；关闭时直接返回 `outbound_disabled`，不发起任何连接。抓取建议走可配置的独立出口，便于自部署者限制。

## 6. 验证策略、依据与聚合

`verification_policy` 版本化并随 `task_spec` 冻结，至少包含：`criteria[]`、`basis_type`、`thresholds`、`required_capabilities`、`allowed_material_kinds`。任务创建后策略不可变，更换策略等于新的 task_spec。

`verification_basis`（术语见 `CONTEXT.md` 的"验证依据"）建议枚举：`deterministic_scoring`、`criteria_checklist`、`metric_comparison`、`rubric_model`、`self_report`。

模型只输出**逐条候选结果与证据引用位置**，聚合由代码完成：

| 逐条结果分布 | 聚合结果 |
| --- | --- |
| 全部 met | `passed` |
| 存在 unmet | `not_met` |
| 存在 unknown 且无 unmet | `insufficient_evidence` |
| 材料或调用失败 | `error` |

**服务端不接受模型给出的总体结论**。学习类系统出题走 `deterministic_scoring`，完全不调用模型；健身测量走 `metric_comparison`。

原因码建议枚举（面向用户展示，需前后端共用）：`link_unreachable`、`link_requires_auth`、`content_empty`、`format_unsupported`、`size_exceeded`、`vision_unavailable`、`model_call_failed`、`model_timeout`、`content_irrelevant`、`outbound_disabled`、`criteria_uncovered`。

自评路径写入 `verification_results` 且 `verification_basis=self_report`，可用于依赖解锁，但在界面、回顾与估算校准中必须可区分，不与系统判分等权使用。

`error` 最多自动重试两次并指数退避；耗尽后保持 `error`，允许用户改为提交文字说明转入 `self_report`。只有 `not_met` 触发领域补救动作，与 [执行反馈、验证与调整引擎](14-feedback-adjustment-engine.md) 第 6 节的判级衔接。

## 7. 辅助产出

辅助能力走独立作业，产出辅助产出（`CONTEXT.md` 的 assistance artifact，落库为 `origin=agent_output` 的 artifact），记录模型、提示版本与生成时间，不写入 checkin。

服务端强校验：验证材料引用中**不得出现同一任务下 `origin=agent_output` 的 artifact**。这是防止 Agent 自评自过的硬约束，必须在 `record_checkin` 的材料引用校验里执行，而不是仅靠界面不展示。

练习生成的答案键与判分标准存入该任务的 `verification_policy`，提交前不得通过任何接口返回给客户端；判分在服务端确定性执行。

## 8. API 衔接

- `POST /api/artifacts/uploads`：申请上传，返回上传地址与配额校验结果。
- `POST /api/artifacts`：确认登记，返回可引用的 artifact。
- `POST /api/artifacts/links`：提交链接并创建抓取作业。
- `GET /api/artifacts/{id}/content`：鉴权下载或读取抽取文本。
- `POST /api/tasks/{id}/assistance`：请求辅助产出，返回 job_id。
- `GET /api/tasks/{id}/verification-results`：在既有接口上补充逐条结果、依据与原因码。
- `POST /api/verification-results/{id}/retry`：手动重试。
- `GET /api/capabilities`：当前默认模型配置的能力视图。

新增接口在实现前补入 OpenAPI，并按 [契约先行与边界所有权](../engineering/01-contracts-and-ownership.md) 由契约负责人单点维护。

## 9. 测试重点

- SSRF：`127.0.0.1`、私有网段、`169.254.169.254`、解析到私网的域名、重定向至私网，全部拒绝且不产生出站连接。
- 伪装扩展名（内容为 HTML 的 `.png`）被拒；`.svg` 被拒。
- 第二个用户下载他人 artifact、读取他人验证结果与抓取快照，全部被拒。
- 无 `vision` 能力提交图片得到 `insufficient_evidence` 且不触发重试。
- 任务生成后把默认配置换成无 `vision` 的模型，该任务的 `verification_policy` 不变，任务开始前的提示显示当前模型不满足提交形式。
- 响应体超过 2 MB 的链接被安全中断，不发生内存耗尽。
- `agent_output` 无法作为同一任务的验证材料，接口层直接拒绝。
- 同一任务上 `self_report` 与 `deterministic_scoring` 的结果在查询中可区分。
- 扫描件 PDF 正确落入图片路径，有文本层 PDF 不调用视觉能力。
- 答案键在提交前不通过任何接口泄露。
- 模型超时两次后结果为 `error`，已保存的 checkin 与投入时间不受影响。
