# T10 前端核心页面

| 项 | 值 |
| --- | --- |
| 工作包 | T10（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | Codex（前端实现） |
| 状态 | 进行中：会话、今日入口、目标创建和详情的首个切片在草稿 PR #25 评审 |
| 更新日期 | 2026-09-24 |
| 相关 PR | #25（首个前端切片，草稿） |

## 1. 目标

交付可从浏览器使用的核心页面：注册登录、受保护的应用入口、目标创建与查看、时间设置、路线比较及计划预览。页面消费已生成的 HTTP 类型和真实服务端状态；尚未接线的模型生成操作必须如实标识，不能伪装成成功。

本工作包分成可独立验收的短 PR。首个切片实现注册登录、会话保护、退出、今日入口空态及目标创建/详情，让用户能通过真实 API 从零进入应用。目标列表等待独立的公共契约补齐。

## 2. 范围

### 可修改路径

```text
frontend/src/app/
frontend/src/features/auth/
frontend/src/features/goals/
frontend/src/features/today/
frontend/src/features/planning/
frontend/src/features/model-settings/
frontend/src/shared/api/（不含 generated/）
frontend/src/shared/ui/
frontend/src/shared/lib/
frontend/src/shared/test/
frontend/index.html
frontend/package.json
frontend/pnpm-lock.yaml
frontend/tsconfig.json
frontend/vite.config.ts
docs/worklog/T10-frontend-core.md
docs/worklog/README.md（仅索引）
```

### 明确不可修改

```text
openapi/
frontend/src/shared/api/generated/
backend/
scripts/
docs/product/
docs/development/
docs/engineering/
```

公共契约缺失时记录问题，交由契约负责人单独处理；前端不手写生成类型。

### 不在本次范围内

- T08/T09 的澄清、路线与计划生成业务，T14 的模型凭证服务端能力，T11 的执行记录与验证，T12 的调整与回顾。
- 前端自行推断目标领域、时间预算、计划生效或作业成功状态。
- 浏览器通知和站外提醒。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [PRD.md](../product/PRD.md) | R01 注册登录、R02–R08 核心旅程；今日为首页 | 已确认 |
| [07-goal-clarification.md](../product/07-goal-clarification.md) | 创建目标只要求自然语言描述，不让用户选领域 | 已确认 |
| [08-auth-design.md](../development/08-auth-design.md) | Cookie 会话、注册开关、账号流程及错误语义 | 已确认 |
| [09-frontend-architecture.md](../development/09-frontend-architecture.md) | React + TypeScript + Vite + shadcn/ui；页面路由、状态与目录方案 | 技术栈已确认；配套库与路由/目录为推荐，由本卡 A1–A3 确定首个切片 |
| [05-module-contracts.md](../development/05-module-contracts.md) | 模块操作和共同规则 | 建议契约；以已导出的 OpenAPI 和实际路由为具体接口依据 |
| [06-delivery-plan.md](../development/06-delivery-plan.md) | T10 范围、T02 输入及 T03/T09 联调依赖 | 任务拆解建议；本卡界定实施范围 |
| `openapi/goalflow.yaml` | 已导出的 HTTP 结构，前端仅消费生成类型 | 已落地契约 |

## 4. 决策与假设

| 编号 | 决策 / 假设 | 依据 | 影响范围 | 是否需要升级为 RFC |
| --- | --- | --- | --- | --- |
| A1 | 首个切片采用 React Router 的 SPA 页面路由；身份始终从 `/api/auth/session` 查询，路由守卫不替代后端鉴权 | 09 号文档推荐；只影响 T10 前端内部实现 | 前端 | 否 |
| A2 | 服务端状态沿用已安装的 TanStack Query；用户切换或 401 时清除私有缓存，登录前不请求私有资源 | 09 号文档推荐、03 号规范的测试重点 | 前端 | 否 |
| A3 | 首个切片只对真实已接线端点提供操作；未接线的生成步骤展示明确的开发状态，不使用假数据冒充真实结果 | T04 交接卡指明四个生成端点仍是契约桩 | 前端 | 否 |
| A4 | 交付按独立能力拆 PR：会话与可进入的目标页优先，随后时间设置与计划预览；每个 PR 控制评审规模 | 02-ai-collaboration 第 3 节 | T10 工作包内部 | 否 |
| A5 | 已有契约仅支持按 ID 读取目标；首个切片不使用 localStorage 维护目标清单，创建成功后直接进入该目标详情 | OpenAPI `/api/goals` 只有 POST，缺少 GET | 公共契约待补，前端先避免虚假列表 | 是，交由契约负责人单独处理 |
| A6 | 创建页只收一段自然描述；提交时取其第一行作为 `title`（最多 200 字符），全文作为 `initial_description` | 产品 07 第 5 节只有一个主要输入，现有 POST 契约要求 `title` | 前端表单映射 | 否 |
| A7 | 首个切片的表单与导航使用原生语义元素；shadcn/ui 的可复用组件源码随需要弹窗、选择器等组件的后续切片接入 | 已确认技术基线包含 shadcn/ui；首个切片尚无这类组件需求 | T10 内部实施顺序 | 否 |
| A8 | 今日页当前只展示排期数值概况；不把 `task_id` 显示成任务标题 | 现有 AgendaItemView 只含 task_id、task_spec_id、分钟数与原因码 | T11 联调接口待补 | 是，交由契约负责人单独处理 |

## 5. 验收场景

- [x] 未登录打开 `/today` 或 `/goals/new` 不显示私有数据，转到登录后能返回原站内路径。
- [x] 注册开放时能注册并进入应用；注册关闭时页面不给出可提交的注册表单，仍提供登录入口。
- [x] 登录失败显示服务端可读错误；成功后进入受保护页面。
- [x] 退出后清理私有查询缓存；私有接口返回 401 时立即撤下旧账号页面内容。
- [x] 用户从自然描述创建目标，不需要选择领域；创建后进入服务器返回的目标详情，刷新时按 ID 重新读取。
- [x] 今日页在没有排期结果时展示真实空态，不把草稿任务当正式待办。
- [ ] 时间额度、路线和计划页面在各自切片中按当前契约验收；生成类端点未接线时不给出虚假成功。
- [x] `bash scripts/check.sh` 和 `bash scripts/test.sh` 均通过。

## 6. 进展

- 已完成：核对 T02–T07、T16 状态及可用的 OpenAPI；首个切片实现注册、登录、会话守卫、退出、今日排期概况、目标创建与详情；前端新增路由依赖及锁文件；自动测试覆盖守卫、注册开关、真实 API 交互形状、退出缓存清理和私有请求 401。使用本地迁移数据库与实际 API 完成浏览器验收。
- 进行中：首个切片草稿 PR #25 评审。
- 未开始：目标列表契约、时间设置、路线比较、计划预览及 T09 联调；shadcn/ui 可复用组件接入。

## 7. 验证结果

2026-09-24，Windows，Git Bash。数据库相关的浏览器联调用迁移 0001–0006 建出的真实 SQLite 文件，经后端生产连接装配访问；文件位于仓库忽略的 `data/`，未提交。

```text
$ bash scripts/check.sh
后端：124 files already formatted；All checks passed!；mypy 59 source files 无问题
前端：锁文件一致；eslint、tsc --noEmit、Prettier 均通过
契约一致；未发现疑似凭证；检查通过

$ bash scripts/test.sh
后端：376 passed, 1 warning in 42.08s
前端：Test Files 3 passed (3), Tests 20 passed (20)
测试通过

$ bash scripts/test.sh frontend
Test Files 3 passed (3), Tests 20 passed (20)
测试通过
```

手工浏览器验收：在本地 Vite + FastAPI 服务上注册测试账号，打开今日空态，提交自然语言目标，再读取目标草稿详情；分别查看桌面与 390px 手机宽度。测试数据和截图未进入提交。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| 四个目标生成类端点仍为契约桩 | 无法验收从目标描述到计划启用的完整旅程 | T08/T09 负责人 |
| 模型配置接口尚未交付 | 设置页不能保存或测试模型凭证 | T14 负责人 |
| 执行记录接口尚未交付 | 今日页暂不能提交完成、部分完成或验证材料 | T11 负责人 |
| 缺少 `GET /api/goals` 目标列表契约 | 无法从应用内浏览全部既有目标；创建后只能进入详情 | 公共契约负责人 |
| 今日安排项不含任务标题与内容 | 无法在今日页展示具体行动，当前只显示排期项数与容量 | T11 与公共契约负责人 |

## 9. 给接手者

从最新 `main` 和本卡列出的边界继续。共享 HTTP 类型只由 `scripts/api-generate.sh` 生成；不要编辑 `generated/`。目标生成接口存在于 OpenAPI 不代表已实现，T04 路由目前会返回内部错误。首个切片应连接真实注册、会话和目标端点，不应使用静态假目标作为演示成功。目标列表必须等待独立契约 PR。
