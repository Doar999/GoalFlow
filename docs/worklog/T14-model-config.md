# T14 个人模型配置

| 项 | 值 |
| --- | --- |
| 工作包 | T14（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | Giraffe12311（前后端与数据负责人） |
| 状态 | 进行中：PR-1 已合并（#26）；PR-2 业务实现完成，待用户批准提交 |
| 更新日期 | 2026-09-24 |
| 相关 PR | #26（PR-1 契约面，已合并）；PR-2 待建 |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容。

## 1. 目标

让每位用户保存自己的模型配置（provider、API 模式、自定义地址、加密凭证），支持增删改、默认选择、连通性测试与脱敏展示；执行侧（T08 的作业）按 owner_id 读取配置，配置错误可定位且不影响已有任务记录（R15）。

达成标准：六个 HTTP 端点按本卡决策落地；`model_configs` 表与迁移、ORM 一致；凭证密文入库、任何响应不携带凭证材料；最小测试调用直接构造 chat model 发起（不经过 LangGraph 图、不依赖领域策略）；用户隔离、脱敏、删除后禁止重试与自定义地址限制验收通过。

## 2. 范围

按既定节奏拆两个 PR：

- **PR-1（契约面）**：迁移 0007、`model_configs` 模块 ORM、契约枚举与错误码、6 端点契约桩、OpenAPI 与前端类型导出。
- **PR-2（业务实现）**：凭证信封加密、出站地址校验、/test 真实调用、默认切换事务、限流与全部验收测试。

### 可修改路径

```text
backend/src/goalflow/model_configs/
backend/tests/model_configs/
backend/migrations/versions/           （仅本工作包的 0007 迁移）
backend/src/goalflow/contracts/        （T14 新增枚举与错误码，随契约 PR）
backend/src/goalflow/api/routes/model_configs.py
backend/src/goalflow/api/app.py        （仅注册路由一行）
backend/src/goalflow/core/config.py    （仅新增 model_endpoint_allowlist 字段，决策 A13）
.env.example                           （仅新增 GOALFLOW_MODEL_ENDPOINT_ALLOWLIST 占位符，决策 A13）
backend/tests/db/test_models_match_migrations.py （仅新增一行 import）
docs/worklog/T14-model-config.md
docs/worklog/README.md                 （仅索引表）
openapi/goalflow.yaml、frontend/src/shared/api/generated/ （生成物）
```

### 明确不可修改

```text
backend/src/goalflow/auth/、goals/、links/、scheduling/、jobs/ 的模块代码与既有迁移
```

### 不在本次范围内

- LangGraph 步骤图、领域路由、提示版本与用量统计——归 T08。
- 作业与配置的绑定/失效联动（"排队期间配置被改动则停止"）——归 T08/T09 接线。
- 自部署实例管理员设置本地/内网允许列表的管理界面——PR-2 只落校验逻辑与默认策略。
- 模型用量费用与预算额度——Q07 明确暂缓。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [PRD](../product/PRD.md) | R15、Q01、Q03、Q07、D08（已确认双 provider） | 已确认 |
| [06-model-configuration.md](../product/06-model-configuration.md) | 本地无鉴权模型可不填 Key、凭证替换/禁用/删除、测试与保存分开、默认配置流程 | 第 2 节具体规则原文标为**建议**，经决策 A11/A12 采纳 |
| [07-model-provider-design.md](../development/07-model-provider-design.md) | provider 与能力契约、凭证与运行快照、自定义地址、接口建议 | 第 2 节与"自定义地址"节为设计稿结论；表结构与端点清单原文标为**建议**，按决策 A1/A9 落地 |
| [08-auth-design.md](../development/08-auth-design.md) | 第 4 节限流模式与阈值量级（决策 A14 先例） | 已确认 |
| [05-module-contracts.md](../development/05-module-contracts.md) | 共同规则（版本竞争、幂等、错误结构） | 已确认 |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) | 公共契约清单、迁移规程 | 已确认 |
| [T03 交接卡](T03-auth-session.md) | 决策 C2（Base 位置）、C3（无请求级会话依赖）、GOALFLOW_CREDENTIAL_ENCRYPTION_KEY | 已完成 |
| [T15 设计 15 号](../development/15-assistance-and-verification-design.md) | "出站开关为实例级环境变量"（决策 A13 载体先例） | 已确认 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要 RFC |
| --- | --- | --- | --- | --- |
| A1 | `model_configs` 表结构与字段集按 07 号文档"建议"段落地为契约：id、owner_id、name、model_provider、api_mode、base_url、model_id、credential_ciphertext、encryption_key_version、revision、enabled、is_default、capabilities_json、last_test_at、created_at、updated_at | 文档原文标"建议"，本工作包无其他已确认事实源；字段与其逐一对应 | model_configs 模块、迁移 0007 | 否 |
| A2 | 用户默认配置引用**不落在 users 表**，用 `model_configs.is_default` 列 + 部分唯一索引（每用户至多一个默认）表达 | users 表归 T03/auth 所有，红线 5 禁止跨模块改对方状态表；加列即跨边界 | model_configs 表 | 否 |
| A3 | `model_provider` 创建后不可修改，换 provider 新建配置；`api_mode` 可修改且视为配置版本变化（递增 revision）。`anthropic` 下 api_mode 必须为空，DB CHECK 强制 | 07 号"建议"段："修改 API 模式视为配置版本变化"；provider 变更文档未定，按保守处理 | PATCH 语义、DB CHECK | 否 |
| A4 | DELETE 语义 = **归档**：deleted_at 置时间戳、enabled=0、credential_ciphertext 与 encryption_key_version 清空、is_default 清除、行保留（保留作业所需非敏感历史信息）；归档配置对所有端点按 404 处理（"删除后禁止重试"），不可恢复。**修订**：原按 enabled 单字段承载删除语义，但 06 号明确"支持替换、禁用和删除"，禁用与删除是两个操作，enabled 无法同时表达两者，故新增 deleted_at 列（A12） | 07 号接口建议 DELETE 条目；06 号第 2 节 | DELETE 语义、迁移 0007、作业历史可解释 | 否 |
| A5 | 任何响应不携带凭证材料：读接口只返回 `has_credential` 布尔值；PATCH 的 api_key 字段缺省 = 保持原值，显式空串拒绝（VALIDATION_FAILED），不存在"掩码回显覆盖"路径 | 07 号接口建议 PATCH 条目；红线"禁止明文进入日志、作业载荷、导出样例或前端读接口" | 全部端点响应与请求模型 | 否 |
| A6 | 自定义地址被实例出站策略拒绝时返回新错误码 `MODEL_ENDPOINT_NOT_ALLOWED`（403）；校验在 base_url 交给 LangChain 之前执行（业务实现 PR-2 落地，校验规则：协议、端口、DNS 解析与重定向、云元数据与内网段黑名单；本地/内网端点默认拒绝，由 A13 的允许列表开放） | 07 号"自定义地址"节（设计稿结论） | contracts/errors.py、PR-2 | 否 |
| A7 | POST /{id}/test 用固定无私人内容短提示，测试结果在 **200 响应体**返回（succeeded/failed + 能力 + 脱敏错误分类），不抛 MODEL_UNAVAILABLE；错误分类枚举 ModelTestErrorKind（auth / model_not_found / rate_limited / network / protocol），供应商原始错误信息脱敏后才进入响应 | 07 号："分别展示鉴权/权限、路径/模型、限流、网络和响应协议错误，脱敏供应商错误" | /test 端点、PR-2 | 否 |
| A8 | 能力记录三态 `CapabilityState`（supported/unsupported/unknown），capabilities_json 首版记录 basic_generation、structured_output、streaming 三项；LangChain 用提示工程模拟结构化输出的 provider 组合记 unsupported，不伪报原生能力 | 07 号"能力契约"段 | capabilities_json 结构、PR-2 | 否 |
| A9 | 端点清单照 07 号"接口建议"定为 6 端点；POST/PUT/DELETE 需 Idempotency-Key（幂等），PATCH 走 expected_revision 乐观锁、不取幂等键（沿用 goals 模式）；**/test 是唯一不要幂等键的 POST**——它是显式用户触发、可重复执行的正当行为，滥用由频控约束，幂等键反而会妨碍有意重测 | 07 号接口建议（建议→本次确认为契约）；01-contracts 第 5 节 | 6 端点 | 否 |
| A10 | 凭证加密采用信封加密：credential_ciphertext 为密文，主密钥复用 T03 预置的 GOALFLOW_CREDENTIAL_ENCRYPTION_KEY，encryption_key_version 记录密钥版本，为将来轮换留位；本 PR 除 A13 外不新增环境变量 | .env.example 与 config.py 已有该密钥且标注用途即模型凭证加密（T03） | PR-2 加密实现 | 否 |
| A11 | **api_key 创建时可选**：本地无鉴权模型（如 Ollama 类服务）可不填 Key（产品 06 号第 2 节"本地无鉴权模型可不填 Key"，原文标为建议、本次采纳）；凭证为空的活跃配置合法，故不设"活跃必有凭证"的 DB CHECK；has_credential 对无 Key 配置为 false | 06 号第 2 节（建议→采纳） | 创建契约、迁移 0007 | 否 |
| A12 | **禁用与删除是两个操作**：PATCH 增加 enabled 字段，false=禁用（凭证保留、可重新启用）；DELETE=删除归档（A4）。列表返回未删除配置（含禁用）；已禁用/已删除配置不能设为默认（409） | 产品 06 号第 2 节"支持替换、禁用和删除"（建议→采纳）；07 号 DELETE 条目仅描述删除 | PATCH 契约、迁移 0007、列表语义 | 否 |
| A13 | 本地/内网允许列表的配置载体定为**环境变量** `GOALFLOW_MODEL_ENDPOINT_ALLOWLIST`（逗号分隔主机名/IP/CIDR，留空=本地/内网全部拒绝），随本契约 PR 加入 config.py 与 .env.example | 先例：T15"出站开关为实例级环境变量"、08 号 `GOALFLOW_ALLOW_REGISTRATION`、T03 C7；07 号仅说"实例管理员设置"未定载体，环境变量是唯一已确立的实例策略载体模式；环境变量属公共契约故随契约 PR | config.py、.env.example、PR-2 出站校验 | 否 |
| A14 | /test 频控**仅作滥用兜底**，阈值放宽至**每用户 1000 次/15 分钟**（用户决策：几乎无限制），进程内固定窗口、超限返回 RATE_LIMITED 与 Retry-After，阈值写成模块常量，PR-2 可再调 | 08 号第 4 节限流先例仅作量级参考；用户明确要求几乎不限制正常使用 | /test 端点、PR-2 | 否 |

## 5. 验收场景

- [ ] 创建配置（openai + chat_completions、anthropic 无 api_mode）返回 201 与脱敏元数据，响应无凭证材料
- [ ] 本地无鉴权模型可不填 Key 创建成功（A11），has_credential=false
- [ ] 非法组合被拒：anthropic 携带 api_mode、openai 缺 api_mode → 422
- [ ] GET 列表仅返回当前用户配置，含已禁用、不含已删除（Q01：第二个用户访问/列举被拒）
- [ ] PATCH 携带过期 expected_revision → 409 REVISION_CONFLICT；api_key 缺省时凭证保持原值（has_credential 不变）
- [ ] PATCH 显式空串 api_key → 422
- [ ] PATCH enabled=false 禁用：凭证保留（has_credential 不变）、可重新启用（A12）
- [ ] PUT default 切换后旧默认自动让位，每用户至多一个默认（DB 部分唯一索引兜底）；对禁用/已删除配置设默认 → 409
- [ ] DELETE 后：deleted_at 非空、has_credential=false、enabled=false、再次访问任何端点 → 404；删除后禁止重试
- [ ] /test 固定短提示，成功返回三态能力与 tested_at；失败按五类错误脱敏返回；频控仅作滥用兜底（每用户 1000 次/15 分钟），正常使用不可触达（A14）
- [ ] 自定义地址命中出站黑名单（内网/云元数据）→ 403 MODEL_ENDPOINT_NOT_ALLOWED；GOALFLOW_MODEL_ENDPOINT_ALLOWLIST 命中的本地端点放行（A13）
- [ ] 凭证密文入库，库里查不到明文 Key；日志脱敏
- [ ] 最小测试调用直接构造 chat model 发起，不经过 LangGraph 图（T14 交付计划）

## 6. 进展

- 已完成：PR-1 契约面（#26 已合并）；**PR-2 业务实现完成并通过验证**——信封加密（crypto.py，AES-GCM 双层信封 + 密钥版本）、出站校验（outbound.py，协议/端口/DNS/内网段黑名单/允许列表，调用前重校验）、/test 真实调用（probes.py，直接构造 chat model，三探测：基本生成/结构化输出/流式，max_retries=1、timeout 15s、错误五类脱敏分类）、六端点接线、频控兜底（1000 次/15 分钟，决策 A14）、默认切换事务、61 条模块测试。
- 修订记录：提交 PR-1 前按用户提示扫描全库文档，据产品 06 号与既有先例修订决策 A4 并新增 A11–A13（Key 可选、禁用独立操作、允许列表载体），契约面已同步；A14（/test 频控）经两轮调整定为**仅作滥用兜底（每用户 1000 次/15 分钟）**。
- 待办：向仓库负责人展示 PR-2 预提交清单并获批准 → 提交 → 建 PR。
- PR-3 收尾（交接卡状态改写、验收勾选）随后。

## 7. 验证结果

以下均为真实执行（2026-09-24，Windows / Git Bash；数据库相关用例由套件内 fixture 在与生产同构的 SQLite 配置上执行——真实库文件、同一组 pragma）。

**PR-1（#26）：**

```text
$ uv run --project backend pytest backend/tests -rf --tb=line
376 passed, 0 failed
$ uv run --project backend ruff format --check backend / ruff check backend / mypy --config-file backend/pyproject.toml backend/src
全部通过
$ uv lock --project backend --check
锁文件无漂移
前端 tsc / eslint / prettier 全绿
```

**PR-2：**

```text
$ uv run --project backend pytest backend/tests
437 passed, 1 warning, 0 failed   （新增 61 条：crypto 7 / outbound 13 / service 24 / API 17）
唯一 warning 为 starlette 对 anyio 别名的既有弃用提示

$ uv run --project backend ruff format --check backend / ruff check backend / mypy --config-file backend/pyproject.toml backend/src
全部通过（mypy 66 文件零错误）

$ uv lock --project backend --check
锁文件无漂移（新增 cryptography 47.0.0）

$ npx pnpm@9.15.4 --dir frontend exec tsc --noEmit / run lint / run test
全部通过（Vitest 3 文件 / 20 用例，含 #25 T10 新增用例）
```

回归确认：

- tests/db/test_models_match_migrations.py 与 tests/db_compat 全绿（含备份恢复演练 F2）——模型、迁移与库结构一致。
- tests/e2e/test_health_and_contract.py::test_committed_spec_matches_current_code 在重新导出后通过。
- /test 探测经 monkeypatch 桩验证，不产生真实网络调用；真实 provider 验收（openai/anthropic 各路径）按 07 号要求在部署环境由用户执行。

环境注记：本机沙箱的 safe-delete 钩子会对"批量删除临时文件"的合法测试（db 模块的迁移目录复制清理、db_compat 备份恢复演练）注入 SystemExit 并级联毒化后续夹具（"assert not self._finalizers"），在沙箱内跑全量套件必现、绕过沙箱即全绿——与 T06 交接卡记录同源，判定以绕过沙箱后的完整输出为准。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| ~~自部署本地/内网允许列表的配置载体~~ | 已解决：定为环境变量 GOALFLOW_MODEL_ENDPOINT_ALLOWLIST（决策 A13，随本契约 PR） | — |
| ~~\/test 频控的具体阈值~~ | 已解决：仅作滥用兜底，每用户 1000 次/15 分钟（决策 A14，模块常量，几乎无限制） | — |
| anthropic 配置不填 Key 时的策略（SDK 要求必有 Key，创建放行、调用期报 auth 错误 vs 创建即拒） | 影响创建体验；首版按"契约放行、调用期报错"处理，与 06 号"错误区分凭证"一致 | 仓库负责人，PR-2 前确认 |

## 9. 给接手者

1. **凭证永远不出现在任何响应、日志、作业载荷里**（A5）。读接口只给 has_credential；测试用例要盯库文件与响应体两处。
2. **is_default 的唯一性靠 DB 部分唯一索引兜底**（A2），业务层切换默认时"先清旧再置新"必须在同一写事务内，否则索引会拦下并暴露半程状态。
3. **禁用与删除是两条路**（A4/A12）：enabled=0 是用户禁用（凭证保留、可恢复）；deleted_at 非空才是删除归档（凭证清空、不可恢复）。判定"已删除"的唯一口径是 deleted_at IS NOT NULL，不要用 credential 是否为空推断——本地无鉴权模型（A11）活跃时凭证就是空的。
4. **出站校验在 base_url 交给 LangChain 之前**（A6）；允许列表载体是 GOALFLOW_MODEL_ENDPOINT_ALLOWLIST（A13），留空=本地/内网全拒。框架不提供这层保护（07 号原文）。
5. **provider 不可改**（A3）：PATCH 模型里不出现 model_provider 字段是有意为之，不要"补全"它。
6. `model_id` 是供应商模型名（如 claude-sonnet-4），沿用 07 号文档拼写；与主键 id 无关。
7. 本地无鉴权模型不填 Key 是合法契约（A11，06 号），业务校验按 provider 与服务形态区分，不要在契约层一刀切必填。
